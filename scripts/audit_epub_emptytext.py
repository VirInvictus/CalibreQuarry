#!/usr/bin/env python3
"""
audit_epub_emptytext.py: read the actual text of every EPUB and flag files that
contain little or no body text. The motivating failure mode is the "Bookmate
stub": an export whose archive holds only cover/promo images plus a tiny HTML
placeholder, with the OPF spine pointing at that placeholder and the real book
nowhere in the file. A reader shows the title and then nothing.

Crucially, such a file passes every other audit. epubcheck validates it (the one
referenced document is well-formed), Bindery "repairs" the stub markup to zero
fatals, the language audit sees no foreign text because there is no text at all,
and the metadata looks perfect. Only reading the spine and counting rendered
characters catches it. This is the check that would have stopped Barchester
Towers (a Wordsworth cover image, zero words) from being imported.

Companion to audit_epub_content.py (wrong-language content) and
audit_epub_pagenumbers.py (baked page numbers). All three decompress and scan
the whole library, open metadata.db strictly mode=ro, and change nothing.

Run from the library directory:
    python3 audit_epub_emptytext.py              # audit the whole library (DB-driven)
    python3 audit_epub_emptytext.py ~/Downloads  # vet loose .epub files before import

Tuning:
    --min-chars N   at or below this many rendered chars a book is EMPTY
                    (a real defect; default 2000). No genuine prose book has
                    fewer; a stub has zero.
    --thin-chars N  below this a book is THIN and listed for review but does not
                    by itself fail the run (default 20000). Legitimately short
                    works (a single short story, a poetry chapbook, an RPG zine
                    exported to EPUB) can land here, so THIN is advisory.

Exit codes:
    0 = clean (no EMPTY books; THIN books, if any, are advisory)
    1 = at least one EMPTY book found, or a scan error
    2 = setup error (missing DB / library, or no .epub files in directory)

Method (stdlib only):
  - resolve the spine via container.xml -> OPF (shared approach with the
    pagenumbers audit)
  - for each spine document, drop <script>/<style> contents, strip tags, decode
    entities, collapse whitespace, and sum the visible characters
  - report the total, the spine length, how many images the archive carries, and
    whether Bookmate markers are present (bookmate.css, calibre_bookmarks.txt) as
    a provenance hint, not a verdict: a Bookmate ORIGIN is fine, an empty book is
    not (most Bookmate exports carry their full text)

A flagged book is re-sourced and replaced, not edited; this is a detector.
"""

import argparse
import os
import re
import sqlite3
import sys
import zipfile
from html import unescape
from pathlib import Path
from xml.etree import ElementTree as ET


def resolve_library_root() -> Path | None:
    """The library root is wherever metadata.db sits: next to this script (the
    copy living inside the library) or the current working directory (running
    the repo copy from inside a library), in that order."""
    for d in (Path(__file__).resolve().parent, Path.cwd()):
        if (d / "metadata.db").is_file():
            return d
    return None


# ANSI colours; suppress when stdout isn't a TTY (matches the sibling audits).
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = "{http://www.idpf.org/2007/opf}"

DEFAULT_MIN_CHARS = 2000  # at or below this: EMPTY (real defect)
DEFAULT_THIN_CHARS = 20000  # below this: THIN (advisory review)

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
BOOKMATE_MARKERS = ("bookmate.css", "calibre_bookmarks.txt")

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _spine(z: zipfile.ZipFile) -> list[str]:
    """Resolved, in-order list of spine document paths that exist in the archive
    (shared resolution with audit_epub_pagenumbers.py)."""
    names = set(z.namelist())
    container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//c:rootfile", CONTAINER_NS)
    opf_path = rootfile.get("full-path") if rootfile is not None else None
    if not opf_path:
        raise ValueError("container.xml has no rootfile")
    opf = ET.fromstring(z.read(opf_path))
    base = os.path.dirname(opf_path)
    manifest: dict[str, str] = {}
    for it in opf.iter(OPF_NS + "item"):
        item_id, href = it.get("id"), it.get("href")
        if item_id and href:
            manifest[item_id] = href

    def full(href: str) -> str:
        return os.path.normpath(f"{base}/{href}" if base else href).replace("\\", "/")

    spine = []
    for itemref in opf.iter(OPF_NS + "itemref"):
        idref = itemref.get("idref")
        href = manifest.get(idref) if idref else None
        if href and full(href) in names:
            spine.append(full(href))
    return spine


def _visible_chars(html: str) -> int:
    """Rendered character count: drop script/style, strip tags, decode entities,
    collapse whitespace."""
    html = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", html)
    text = unescape(text)
    return len(_WS_RE.sub(" ", text).strip())


def scan(path: Path) -> dict:
    """Count visible text across the spine and gather triage signals."""
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        spine = _spine(z)
        chars = 0
        for doc in spine:
            try:
                raw = z.read(doc)
            except KeyError:
                continue
            try:
                html = raw.decode("utf-8", "replace")
            except Exception:
                html = raw.decode("latin-1", "replace")
            chars += _visible_chars(html)
        images = sum(1 for n in names if n.lower().endswith(IMAGE_EXTS))
        bookmate = any(any(m in n.lower() for m in BOOKMATE_MARKERS) for n in names)
    return {
        "chars": chars,
        "spine_len": len(spine),
        "images": images,
        "bookmate": bookmate,
    }


def classify(r: dict, min_chars: int, thin_chars: int) -> str:
    if r["chars"] <= min_chars:
        return "EMPTY"
    if r["chars"] < thin_chars:
        return "THIN"
    return "OK"


def _detail(r: dict) -> str:
    bits = [f"{r['chars']} chars", f"spine {r['spine_len']}", f"{r['images']} images"]
    if r["bookmate"]:
        bits.append("bookmate")
    return ", ".join(bits)


def audit_library(min_chars: int, thin_chars: int) -> int:
    library_root = resolve_library_root()
    if library_root is None:
        print(
            "ERROR: no metadata.db next to this script or in the current "
            "directory. Run from the library directory."
        )
        return 2
    db_path = library_root / "metadata.db"

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    booktag: dict[int, str] = {}
    for bid, tname in cur.execute(
        "SELECT bt.book, t.name FROM books_tags_link bt JOIN tags t ON t.id = bt.tag"
    ):
        booktag.setdefault(bid, tname)
    rows = cur.execute(
        "SELECT b.id, b.title, b.path, d.name FROM data d "
        "JOIN books b ON b.id = d.book WHERE d.format = 'EPUB' ORDER BY b.id"
    ).fetchall()
    con.close()

    empty: list[tuple] = []
    thin: list[tuple] = []
    errors: list[tuple] = []
    scanned = 0
    for book_id, title, path, name in rows:
        full = library_root / path / f"{name}.epub"
        try:
            r = scan(full)
        except Exception as e:
            errors.append((book_id, title, f"{type(e).__name__}: {e}"))
            continue
        scanned += 1
        verdict = classify(r, min_chars, thin_chars)
        if verdict == "EMPTY":
            empty.append((book_id, title, booktag.get(book_id, "?"), r))
        elif verdict == "THIN":
            thin.append((book_id, title, booktag.get(book_id, "?"), r))

    print(f"Scanned {scanned} EPUBs in {library_root}\n")

    if empty:
        print(f"{RED}{BOLD}EMPTY / NO TEXT ({len(empty)}){RESET}")
        for book_id, title, tag, r in sorted(empty, key=lambda x: x[3]["chars"]):
            print(f"  {RED}#{book_id}{RESET} [{tag}] {title}\n    {_detail(r)}")
        print()

    if thin:
        print(
            f"{YELLOW}{BOLD}THIN (review; may be legitimately short) ({len(thin)}){RESET}"
        )
        for book_id, title, tag, r in sorted(thin, key=lambda x: x[3]["chars"]):
            print(f"  {YELLOW}#{book_id}{RESET} [{tag}] {title}\n    {_detail(r)}")
        print()

    if errors:
        print(f"{YELLOW}{BOLD}SCAN ERRORS ({len(errors)}){RESET}")
        for book_id, title, msg in errors:
            print(f"  #{book_id} {title}\n    {msg}")
        print()

    if not empty and not errors:
        suffix = f" ({len(thin)} thin, advisory)" if thin else ""
        print(f"{GREEN}{BOLD}CLEAN{RESET}: every EPUB has body text{suffix}.")
        return 0
    print(
        f"{RED}{BOLD}FOUND{RESET}: {len(empty)} empty file(s) need re-sourcing"
        f"{f', {len(errors)} scan error(s)' if errors else ''}."
    )
    return 1


def audit_directory(directory: Path, min_chars: int, thin_chars: int) -> int:
    """Vet loose .epub files (recursively) before they enter the library."""
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory.")
        return 2
    epubs = sorted(directory.rglob("*.epub"))
    if not epubs:
        print(f"No .epub files found under {directory}")
        return 2

    print(f"Auditing {len(epubs)} EPUB(s) in {directory}\n")
    empty = thin = errors = 0
    for path in epubs:
        try:
            r = scan(path)
        except Exception as e:
            print(f"  {YELLOW}ERROR {RESET} {path.name}\n      {type(e).__name__}: {e}")
            errors += 1
            continue
        verdict = classify(r, min_chars, thin_chars)
        if verdict == "EMPTY":
            empty += 1
            print(f"  {RED}EMPTY {RESET} {path.name}\n      {_detail(r)}")
        elif verdict == "THIN":
            thin += 1
            print(f"  {YELLOW}THIN  {RESET} {path.name}\n      {_detail(r)}")
        else:
            print(f"  {GREEN}OK    {RESET} {path.name}")
    print()

    if empty == 0 and errors == 0:
        suffix = f" ({thin} thin, advisory)" if thin else ""
        print(f"{GREEN}{BOLD}CLEAN{RESET}: every EPUB has body text{suffix}.")
        return 0
    print(
        f"{RED}{BOLD}FOUND{RESET}: {empty} empty file(s) need re-sourcing, "
        f"{errors} scan error(s)."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit EPUBs for missing/empty body text (e.g. Bookmate stubs)."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="vet loose .epub files under this directory instead of auditing the library",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=DEFAULT_MIN_CHARS,
        help=f"EMPTY threshold (default {DEFAULT_MIN_CHARS})",
    )
    parser.add_argument(
        "--thin-chars",
        type=int,
        default=DEFAULT_THIN_CHARS,
        help=f"THIN advisory threshold (default {DEFAULT_THIN_CHARS})",
    )
    args = parser.parse_args()
    if args.directory:
        return audit_directory(
            Path(args.directory).expanduser(), args.min_chars, args.thin_chars
        )
    return audit_library(args.min_chars, args.thin_chars)


if __name__ == "__main__":
    sys.exit(main())
