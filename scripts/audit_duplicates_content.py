#!/usr/bin/env python3
"""audit_duplicates_content: find books whose TEXT duplicates another book's.

Title+author grouping (and screen_duplicate.py) only see metadata; this
audit fingerprints what is inside the files, so a re-download filed under
the wrong title, an edition swap, or an omnibus that contains a standalone
book shows up even though every metadata field differs.

Method: a 64-bit simhash over the 3-word shingles of each book's spine
text (one text per book id: the largest text-bearing format) finds
near-duplicate candidates by Hamming distance; a bottom-32 sketch (the
32 shingle hashes with the smallest values) finds containment
candidates, because a subset document shares its whole sketch with the
superset (an omnibus's simhash is NOT close to the standalone's, so
the Hamming gate alone would never see it). Candidates are then
classified exactly, by shingle-set containment:
    near_duplicate     same text under different metadata (re-download)
    omnibus_overlap    the larger book contains the smaller's text
                       (anthologies, "complete works" vs the standalone)

Candidate recall is deliberately wide (>= 2 of 32 shared sketch
elements); the exact containment test decides. Advisory tool, not a
guarantee.

False-positive note, on purpose: legitimate public-domain reissues
(different title/author rows, genuinely the same text) are reported by
charter; that IS the finding for this library's curation. Short documents
(under MIN_SHINGLES shingles, i.e. front-matter-only files) are excluded,
because a simhash over a handful of shingles is noise. Only EPUB/TXT/MD
texts are read (PDF via pdftotext when installed); formats without a text
extractor here are skipped silently rather than guessed at.

Read-only: metadata.db opens mode=ro through cquarry, files are read
in place, nothing is written. Runtime note: fingerprinting is ~1s per
book (full spine texts, per-shingle hashing); a whole-library run is
a hours-scale pass -- scope with --search/--ids for interactive use.
Exit codes:
    0 = no clusters (or none after filtering)
    1 = clusters found (report printed; --quiet lists id pairs only)
    2 = setup error

Usage:
    python3 audit_duplicates_content.py [library_dir_or_metadata.db]
        [--search EXPR | --ids ID[,ID...]] [--threshold N] [--quiet]
        [--format {text,json}]
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

MIN_SHINGLES = 200
DEFAULT_THRESHOLD = 6
_OMNIBUS_CONTAINMENT = 0.80
_NEAR_JACCARD = 0.90

_TAG_RE = re.compile(r"<[^>]+>")
_WORD_RE = re.compile(r"[a-z0-9']+")
_READABLE = {"EPUB", "KEPUB", "PDF", "TXT", "TEXT", "MD", "MARKDOWN"}


def _h64(data: bytes) -> int:
    return int.from_bytes(hashlib.md5(data).digest()[:8], "big")


def simhash(text: str) -> tuple[int, dict[str, int]]:
    """64-bit simhash over 3-word shingles, plus the per-shingle hashes.

    Returns (0, {}) when the document is too short to fingerprint.
    """
    words = _WORD_RE.findall(text.lower())
    shingles = {" ".join(words[i : i + 3]) for i in range(max(0, len(words) - 2))}
    if len(shingles) < MIN_SHINGLES:
        return 0, {}
    v = [0] * 64
    hashes = {sh: _h64(sh.encode("utf-8")) for sh in shingles}
    for sh, h in hashes.items():
        for bit in range(64):
            if h >> bit & 1:
                v[bit] += 1
            else:
                v[bit] -= 1
    fingerprint = 0
    for bit, weight in enumerate(v):
        if weight > 0:
            fingerprint |= 1 << bit
    return fingerprint, hashes


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


_SKETCH_K = 32
_SKETCH_MIN_SHARED = 2


def bottom_sketch(shingle_hashes: dict[str, int]) -> frozenset[int]:
    """The _SKETCH_K smallest shingle hashes: a subset document shares
    its whole sketch with any superset."""
    return frozenset(sorted(shingle_hashes.values())[:_SKETCH_K])


def containment_candidates(sketches: dict[int, frozenset[int]]) -> set[tuple[int, int]]:
    """Pairs sharing at least _SKETCH_MIN_SHARED sketch elements."""
    buckets: dict[int, list[int]] = {}
    for bid in sorted(sketches):
        for h in sketches[bid]:
            buckets.setdefault(h, []).append(bid)
    counts: dict[tuple[int, int], int] = {}
    for members in buckets.values():
        if len(members) < 2 or len(members) > 50:
            continue  # a sketch element shared by dozens of books is noise
        members = sorted(members)
        for i, a in enumerate(members):
            for b in members[i + 1 :]:
                counts[(a, b)] = counts.get((a, b), 0) + 1
    return {p for p, n in counts.items() if n >= _SKETCH_MIN_SHARED}


def epub_text(path: Path) -> str:
    """Spine-ordered text from an EPUB; all html entries as a fallback."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        docs: list[str] = []
        try:
            container = z.read("META-INF/container.xml").decode("utf-8", "replace")
            opf_match = re.search(r'full-path="([^"]+)"', container)
            if opf_match:
                opf_name = opf_match.group(1)
                opf = z.read(opf_name).decode("utf-8", "replace")
                manifest: dict[str, str] = {}
                for item_tag in re.findall(r"<item\b[^>]*>", opf):
                    item_id = re.search(r'id="([^"]+)"', item_tag)
                    href = re.search(r'href="([^"]+)"', item_tag)
                    if item_id and href:
                        manifest[item_id.group(1)] = href.group(1)
                base = opf_name.rpartition("/")[0]
                for idref in re.findall(r'<itemref\b[^>]*?idref="([^"]+)"', opf):
                    href = manifest.get(idref)
                    if not href:
                        continue
                    full = f"{base}/{href}" if base else href
                    if full in names:
                        docs.append(full)
        except KeyError, zipfile.BadZipFile:
            docs = []
        if not docs:
            docs = sorted(
                n for n in names if n.lower().endswith((".xhtml", ".html", ".htm"))
            )
        parts = []
        for name in docs:
            try:
                raw = z.read(name).decode("utf-8", "replace")
            except KeyError:
                continue
            parts.append(_TAG_RE.sub(" ", raw))
    return " ".join(parts)


def pdf_text(path: Path) -> str | None:
    """Text via poppler's pdftotext; None when it is not installed."""
    try:
        out = subprocess.run(
            ["pdftotext", "-q", str(path), "-"],
            capture_output=True,
            timeout=120,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    return out.stdout.decode("utf-8", "replace")


def book_text(path: Path, fmt: str) -> str | None:
    if fmt in ("EPUB", "KEPUB"):
        try:
            return epub_text(path)
        except zipfile.BadZipFile, OSError:
            return None
    if fmt == "PDF":
        return pdf_text(path)
    if fmt in ("TXT", "TEXT", "MD", "MARKDOWN"):
        try:
            return path.read_text("utf-8", errors="replace")
        except OSError:
            return None
    return None


def collect_books(db_path: Path, id_filter: set[int] | None = None):
    """{id: (title, author_sort, file path, fmt)} for text-capable books.

    Best format: the largest uncompressed size among the formats this tool
    can extract text from, one text per book id.
    """
    from cquarry.db import CalibreDB

    out = {}
    with CalibreDB(str(db_path)) as db:
        if id_filter is not None:
            wanted = id_filter & db.all_ids()
        else:
            wanted = None
        for b in db.get_all_books():
            if wanted is not None and b["id"] not in wanted:
                continue
            formats = db.get_formats(b["id"]) or {}
            best = None
            for fmt, meta in formats.items():
                if fmt.upper() not in _READABLE:
                    continue
                size = meta.get("size_bytes") or 0
                if best is None or size > best[0]:
                    best = (size, fmt.upper(), meta.get("name"), b["path"])
            if best is None:
                continue
            _, fmt, name, rel = best
            if not name or not rel:
                continue
            fpath = db_path.parent / rel / f"{name}.{fmt.lower()}"
            out[b["id"]] = (b["title"] or "", b["author_sort"] or "", fpath, fmt)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find books whose content duplicates another book's."
    )
    parser.add_argument(
        "path", nargs="?", help="library directory or metadata.db (default: .)"
    )
    parser.add_argument(
        "--search",
        default=None,
        metavar="EXPR",
        help="fingerprint only the books matching a Calibre search expression",
    )
    parser.add_argument(
        "--ids", default=None, metavar="ID[,ID...]", help="fingerprint only these ids"
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=DEFAULT_THRESHOLD,
        help=f"max simhash Hamming distance for a candidate pair "
        f"(default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument("--quiet", action="store_true", help="print only the id pairs")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="fmt"
    )
    args = parser.parse_args()

    id_filter: set[int] | None = None
    if args.ids:
        id_filter = set()
        for part in args.ids.split(","):
            part = part.strip()
            if part:
                try:
                    id_filter.add(int(part))
                except ValueError:
                    print(f"ERROR: invalid id {part!r}.", file=sys.stderr)
                    return 2

    p = Path(args.path) if args.path else Path.cwd()
    db_path = p / "metadata.db" if p.is_dir() else p
    if not db_path.exists():
        print("ERROR: no metadata.db found.", file=sys.stderr)
        return 2

    if args.search:
        from cquarry.db import CalibreDB

        with CalibreDB(str(db_path)) as db:
            try:
                id_filter = set(db.search(args.search))
            except Exception as e:
                print(
                    f"ERROR: could not parse the search expression: {e}",
                    file=sys.stderr,
                )
                return 2
            if not id_filter:
                print("No books matched the filter; nothing to fingerprint.")
                return 0

    books = collect_books(db_path, id_filter)
    fingerprints: dict[int, tuple[int, dict[str, int]]] = {}
    skipped = 0
    for bid, (_title, _author, fpath, fmt) in sorted(books.items()):
        if not fpath.exists():
            skipped += 1
            continue
        text = book_text(fpath, fmt)
        if text is None:
            skipped += 1
            continue
        fingerprint, shingles = simhash(text)
        if not fingerprint:
            skipped += 1
            continue
        fingerprints[bid] = (fingerprint, shingles)

    ids = sorted(fingerprints)
    sketches = {bid: bottom_sketch(fingerprints[bid][1]) for bid in ids}
    candidates = containment_candidates(sketches)
    for i, a in enumerate(ids):
        ha, _ = fingerprints[a]
        for b in ids[i + 1 :]:
            if hamming(ha, fingerprints[b][0]) <= args.threshold:
                candidates.add((a, b))
    clusters: list[dict] = []
    for a, b in sorted(candidates):
        ha, sa = fingerprints[a]
        hb, sb = fingerprints[b]
        distance = hamming(ha, hb)
        inter = len(sa.keys() & sb.keys())
        union = len(sa.keys() | sb.keys())
        jaccard = inter / union if union else 0.0
        containment = inter / min(len(sa), len(sb)) if sa and sb else 0.0
        if jaccard >= _NEAR_JACCARD:
            cls = "near_duplicate"
        elif containment >= _OMNIBUS_CONTAINMENT:
            cls = "omnibus_overlap"
        else:
            continue
        clusters.append(
            {
                "a": a,
                "b": b,
                "class": cls,
                "hamming": distance,
                "jaccard": round(jaccard, 3),
                "containment": round(containment, 3),
            }
        )

    if args.fmt == "json":
        print(
            json.dumps(
                {
                    "clusters": clusters,
                    "scanned": len(fingerprints),
                    "skipped": skipped,
                },
                indent=2,
            )
        )
        return 1 if clusters else 0

    if not clusters:
        print(
            f"No content duplicates among {len(fingerprints)} fingerprinted "
            f"books ({skipped} skipped: missing files, unreadable or too "
            "short to fingerprint)."
        )
        return 0

    titles = {bid: books[bid][0] for bid in books}
    print(
        f"{len(clusters)} content-duplicate cluster(s) among "
        f"{len(fingerprints)} fingerprinted books ({skipped} skipped):\n"
    )
    for c in clusters:
        a, b = c["a"], c["b"]
        if args.quiet:
            print(f"{a},{b}")
            continue
        print(
            f"  #{a} {titles.get(a, '?')}  <->  #{b} {titles.get(b, '?')}  "
            f"[{c['class']} d={c['hamming']} j={c['jaccard']} "
            f"contain={c['containment']}]"
        )
        if c["class"] == "omnibus_overlap":
            print(
                "    containment: the larger book carries the smaller's "
                "text (omnibus/anthology vs standalone, or a partial "
                "re-download)."
            )
        else:
            print(
                "    same text under different metadata (a re-download "
                "or a public-domain reissue; for this library the "
                "reissue IS the finding)."
            )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
