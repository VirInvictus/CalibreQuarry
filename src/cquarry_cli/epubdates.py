"""EPUB OPF dc:date normalization, shared by scripts/reconcile_file_metadata.py
and the `run flush` verb (every path that embeds EPUB metadata via calibredb).

calibredb's EPUB2 writer (the path any version="2.0" package takes) rewrites
only the EARLIEST dc:date element in place and leaves every other one
standing, while calibre's EPUB2 reader reports the MINIMUM of the dates it
finds. A file that ever carried a second date (a fetch-era run-clock value,
the publisher original, the modification stamp) therefore kept all of them
forever: the embed "converged" only while the minimum happened to equal the
database date, and any leftover date older than it read back as permanent
pubdate drift (the #9177/#9635 live class; both files are EPUB2 packages
carrying three dc:date elements). Every embed therefore ends with this pass:
the OPF is forced to exactly one canonical dc:date, the database pubdate in
date-only form."""

import os
import re
import tempfile
import zipfile
from pathlib import Path

_DC_DATE_SPAN = re.compile(
    r"<dc:date\b[^>]*>.*?</dc:date\s*>|<dc:date\b[^>]*/>", re.DOTALL
)
_METADATA_BLOCK = re.compile(r"<metadata\b.*?</metadata\s*>", re.DOTALL | re.IGNORECASE)
_DC_NS_BINDING = 'xmlns:dc="http://purl.org/dc/elements/1.1/"'
# OPF 2.0 DTD order: title, creator*, subject*, description?, publisher?,
# contributor*, date*, ... identifier+. A freshly inserted date goes after the
# last of the elements that precede date in that order.
_DATE_PREDECESSORS = (
    "dc:title",
    "dc:creator",
    "dc:subject",
    "dc:description",
    "dc:publisher",
    "dc:contributor",
)

_DATE_ONLY = re.compile(r"(\d{4}-\d{2}-\d{2})")


def norm_date(value: str | None) -> str:
    """Reduce a date/timestamp to YYYY-MM-DD; '' for the sentinel/empty.
    The same reduction the file-metadata diff uses, so a normalization
    target always matches what the read-back compares."""
    s = re.sub(r"\s+", " ", (value or "")).strip()
    m = _DATE_ONLY.match(s)
    if not m:
        return ""
    d = m.group(1)
    return "" if d.startswith("0101-01-01") else d


def canonical_dc_dates(opf_text: str, target: str) -> str | None:
    """Rewrite the OPF so its metadata block carries exactly one dc:date with
    `target` (date-only), or none when target is '' (the unset/sentinel case,
    matching how the diff compares). Returns byte-identical text when the
    file is already canonical, or None when the shape cannot be safely
    rewritten: no metadata block, or dates the document carries without the
    standard dc prefix binding (a file this function cannot name elements in
    must not be half-edited; the read-back re-diff stays the arbiter).
    Surgery is confined to the metadata block — everything outside it,
    including EPUB3's dcterms:modified meta element, is untouched."""
    block_m = _METADATA_BLOCK.search(opf_text)
    if block_m is None:
        return None
    block = block_m.group(0)
    canonical = f"<dc:date>{target}</dc:date>"
    spans = list(_DC_DATE_SPAN.finditer(block))
    if not spans and not target:
        return opf_text
    if len(spans) == 1 and target and spans[0].group(0) == canonical:
        return opf_text
    if not spans:
        if _DC_NS_BINDING not in block:
            return None
        anchor = None
        for tag in _DATE_PREDECESSORS:
            for m in re.finditer(
                rf"<{tag}\b[^>]*/>|<{tag}\b[^>]*>.*?</{tag}\s*>", block, re.DOTALL
            ):
                if anchor is None or m.start() > anchor.start():
                    anchor = m
        if anchor is None:
            return None
        line_start = block.rfind("\n", 0, anchor.start()) + 1
        indent = block[line_start : anchor.start()]
        if indent.strip():  # anchor shares a line with something else
            indent = ""
        return (
            opf_text[: block_m.start()]
            + block[: anchor.end()]
            + "\n"
            + indent
            + canonical
            + block[anchor.end() :]
            + opf_text[block_m.end() :]
        )
    out: list[str] = []
    pos = 0
    for i, span in enumerate(spans):
        if i == 0 and target:
            out.append(block[pos : span.start()])
            out.append(canonical)
            pos = span.end()
            continue
        start = span.start()
        while start > pos and block[start - 1] in " \t":
            start -= 1
        if start > pos and block[start - 1] == "\n":
            start -= 1
            if start > pos and block[start - 1] == "\r":
                start -= 1
        out.append(block[pos:start])
        pos = span.end()
    out.append(block[pos:])
    return opf_text[: block_m.start()] + "".join(out) + opf_text[block_m.end() :]


def _opf_entry_name(zf: zipfile.ZipFile) -> str | None:
    """The OPF path from META-INF/container.xml's first rootfile entry."""
    try:
        container = zf.read("META-INF/container.xml").decode("utf-8-sig")
    except KeyError, ValueError:
        return None
    m = re.search(r'full-path\s*=\s*"([^"]+)"', container)
    return m.group(1) if m else None


def normalize_epub_dates(path: Path, target: str) -> bool:
    """Force one EPUB's OPF dc:date set to the canonical shape. True when the
    file ends canonical (including when it already was: no rewrite then);
    False on any failure — a file that cannot be opened, an OPF this cannot
    safely rewrite, a zip that cannot be rebuilt. The archive is rewritten
    whole through a temp file + os.replace, entry order and per-entry zip
    metadata preserved, so a crash never leaves a half-written EPUB behind."""
    try:
        with zipfile.ZipFile(path, "r", strict_timestamps=False) as zin:
            opf_name = _opf_entry_name(zin)
            if opf_name is None:
                return False
            infos = zin.infolist()
            payloads = {info: zin.read(info) for info in infos}
        opf_info = next((i for i in reversed(infos) if i.filename == opf_name), None)
        if opf_info is None:
            return False
        opf_text = payloads[opf_info].decode("utf-8-sig")
        new_text = canonical_dc_dates(opf_text, target)
        if new_text is None:
            return False
        if new_text == opf_text:
            return True
        payloads[opf_info] = new_text.encode("utf-8")
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with (
                os.fdopen(fd, "wb") as tf,
                zipfile.ZipFile(tf, "w", strict_timestamps=False) as zout,
            ):
                for info in infos:
                    zout.writestr(info, payloads[info])
            os.replace(tmp_name, path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return True
    except Exception:
        return False
