"""Declared-cover extraction for EPUB sources, feeding `run phase2`'s
import. Calibre's GUI add door catalogues a cover for every EPUB that
declares one; `add_book` accepts a cover (path or bytes) but the verb
never extracted one, so verb-door imports landed ``has_cover=0`` on the
same files (issue #34). One function, one contract:

``declared_epub_cover(path)`` returns the bytes of the file's declared
cover image, or None. The preference order is the issue's:

1. the EPUB2 declaration, ``<meta name="cover" content="id">`` resolved
   through the OPF manifest item that id names;
2. the EPUB3 declaration, the manifest item whose ``properties`` carry
   ``cover-image``;
3. the largest archive entry whose basename contains ``cover`` and whose
   extension is a raster image. The name gate is the point: one batch
   file declared a cover path absent from its archive while unrelated
   internal images existed, and "largest image in the archive" would
   have catalogued a distributor logo as the art.

Every candidate must resolve to real archive bytes AND sniff as JPEG or
PNG (cquarry.helpers.sniff_image_format, the same gate add_book applies
sniff-or-raise): a declared SVG or a declared path not in the archive
falls through to the next tier rather than failing the import. Any
content-level failure (not a zip, no container/rootfile, malformed OPF)
also reads as None: an uncovered import is exactly the pre-3.72.0
behavior, and the library linter's EVERY_BOOK_COVER check is the
surfacing mechanism for files that genuinely carry no cover.
"""

from __future__ import annotations

import posixpath
import zipfile
import xml.etree.ElementTree as ET
from urllib.parse import unquote

from cquarry.helpers import sniff_image_format

#: The raster extensions the named-cover fallback considers. An SVG named
#: cover.svg is a real shape in EPUB3 land and add_book refuses it
#: sniff-or-raise, so it is not a candidate here either.
_IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _opf_items(opf: ET.Element) -> list[dict[str, str]]:
    """The OPF manifest items as plain dicts (id/href/media-type/properties,
    absent attributes as empty strings). Local-name matched: the house
    pattern (stamp_epub._opf_identifier_candidates, epubdates) — producer
    EPUBs declare prefixes this code never binds."""
    items = []
    for el in opf.iter():
        if _local(el.tag) != "item":
            continue
        items.append(
            {
                "id": el.get("id") or "",
                "href": el.get("href") or "",
                "media-type": el.get("media-type") or "",
                "properties": el.get("properties") or "",
            }
        )
    return items


def _candidate_names(opf: ET.Element, items: list[dict[str, str]]) -> list[str]:
    """Cover hrefs in the issue's preference order (declared tiers only;
    the named-cover fallback scans archive names and needs no OPF)."""
    names: list[str] = []
    for el in opf.iter():
        # The EPUB2 declaration: <meta name="cover" content="item-id"/>. The
        # name/content attributes are never namespaced. Every cover meta is
        # a candidate (doc order): a double declaration is malformed but
        # honest about its own preference order.
        if _local(el.tag) == "meta" and (el.get("name") or "").strip() == "cover":
            declared = (el.get("content") or "").strip()
            for item in items:
                if item["id"] and item["id"] == declared and item["href"]:
                    names.append(item["href"])
    for item in items:
        # The EPUB3 declaration: properties="cover-image" (possibly among
        # other properties, space-separated).
        if "cover-image" in item["properties"].split() and item["href"]:
            names.append(item["href"])
    return names


def _resolve_name(href: str, opf_dir: str) -> str:
    """An OPF href as an archive entry name: fragment-stripped, percent-
    decoded, resolved against the OPF's directory inside the zip."""
    rel = unquote(href.split("#")[0].strip())
    if not rel:
        return ""
    joined = posixpath.normpath(posixpath.join(opf_dir, rel))
    return joined.lstrip("/")


def declared_epub_cover(path: str) -> bytes | None:
    """The EPUB's declared cover image bytes, or None.

    Never raises: every failure mode (unreadable zip, no rootfile,
    malformed OPF, declared path absent, non-JPEG/PNG bytes) returns None
    and the caller imports as it did before this module existed.
    """
    try:
        with zipfile.ZipFile(path) as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_name = None
            for el in container.iter():
                if _local(el.tag) == "rootfile" and el.get("full-path"):
                    opf_name = el.get("full-path")
                    break
            if opf_name is None:
                return None
            opf = ET.fromstring(z.read(opf_name))
            items = _opf_items(opf)
            opf_dir = posixpath.dirname(opf_name)
            # The declared tiers are preference-ordered, not size-ordered:
            # a resolving declaration wins over any named-cover image no
            # matter how small. Each candidate falls through on a missing
            # entry or non-JPEG/PNG bytes (the declared-SVG shape).
            for href in _candidate_names(opf, items):
                data = _entry_bytes(z, _resolve_name(href, opf_dir))
                if data is not None:
                    return data
            best: tuple[int, bytes] | None = None
            for name in z.namelist():
                if not _is_named_cover(name):
                    continue
                data = _entry_bytes(z, name)
                if data is None:
                    continue
                if best is None or len(data) > best[0]:
                    best = (len(data), data)
            return best[1] if best else None
    except Exception:
        # Content-level failure: not a zip, no container, malformed OPF.
        # An uncovered import is the pre-3.72.0 behavior; never a refusal.
        return None


def _entry_bytes(z: zipfile.ZipFile, name: str) -> bytes | None:
    """One archive entry's bytes when it exists and sniffs as JPEG/PNG,
    else None (a declared path absent from the archive, an SVG, junk, a
    damaged or encrypted member — the per-candidate catch keeps the
    later tiers alive instead of discarding the whole file)."""
    if not name:
        return None
    try:
        data = z.read(name)
    except KeyError, OSError, RuntimeError, zipfile.BadZipFile:
        return None
    return data if sniff_image_format(data) is not None else None


def _is_named_cover(name: str) -> bool:
    """The named-cover fallback gate: a raster image whose basename
    carries "cover" (case-insensitive). Archive-name tier, so it also
    finds covers the OPF never listed."""
    base = posixpath.basename(name).lower()
    return base.endswith(_IMAGE_EXTS) and "cover" in base
