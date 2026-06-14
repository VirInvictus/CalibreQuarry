#!/usr/bin/env python3
"""
audit_epub_pagenumbers.py: read the actual text of every EPUB and flag files
where print page numbers (and running headers) were captured as body *text*
instead of as proper EPUB pagination. In a well-made EPUB a page number is an
invisible marker (<span epub:type="pagebreak">, a page-list nav entry) that the
reader maps to a page; in these broken conversions it is a literal <p>16</p> in
the flow, so when the text reflows it lands in the middle of a sentence
("where the hay cart 16 was taking him"). Metadata cannot catch this.

Companion to audit_epub_content.py (which flags wrong-language content). Both
decompress and scan the whole library and take a few minutes; both open
metadata.db strictly mode=ro and change nothing.

Run from the library directory:
    python3 audit_epub_pagenumbers.py              # audit the whole library (DB-driven)
    python3 audit_epub_pagenumbers.py ~/Downloads  # vet loose .epub files before import

Exit codes:
    0 = clean (no baked-in page numbers found)
    1 = baked-in page numbers found
    2 = setup error (missing DB / library, or no .epub files in directory)

Method (stdlib only):
  - resolve the spine via container.xml -> OPF; skip the nav document and any
    doc that is mostly <li> (the page-list / TOC, where numbered links are
    legitimate, not body text)
  - collect standalone numeric blocks: a <p>/<div> whose entire text is a bare
    1-4 digit number or a roman numeral, sitting next to a prose paragraph
  - the hard part is precision: bare numbers in body text are usually NOT a
    defect. Two discriminators, learned by hand-classifying a library's worth of
    candidates, separate the real thing from the false positives:
      * SECTION / CHAPTER numbers open the next block, so the text after them
        starts with a capital / new scene. A real baked PAGE number is followed
        by a lowercase continuation, OR a word split across it (the previous
        block ends in a hyphen), OR it abuts a running header. We require one of
        those "baked" signals; a number followed by a capital is left alone.
      * ENDNOTE / footnote numbers and chronology years cluster in one region
        (the back matter, a timeline). We require the flagged numbers to span a
        large fraction of the book, which a true running pagination does.
  - running headers/footers (and piracy watermarks) are detected as short blocks
    repeated many times across the book; a number abutting one is a strong tell.

A flagged book is re-sourced and replaced (remove, add a clean copy, re-tag,
reconcile), not edited in place; this is a detector, not a fixer.
"""

import argparse
import os
import re
import sqlite3
import sys
import zipfile
from collections import Counter
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


# ANSI colours; suppress when stdout isn't a TTY (matches audit_epub_content.py)
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = "{http://www.idpf.org/2007/opf}"

INT_RE = re.compile(r"\d{1,4}$")
ROMAN_RE = re.compile(r"[ivxlcdm]{2,7}$", re.I)
# Block-level elements we track to reconstruct reading order.
BLOCK_TAGS = {
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "td",
    "th",
    "blockquote",
    "section",
    "caption",
    "figcaption",
}

# Tuning, validated by hand against a 3,897-EPUB library (every flagged book and
# the full n>=2 tail inspected). At these values the detector flagged 21 books,
# all true positives; the false-positive tail (experimental footnote-poems,
# scraped web-serial vote counts, placeholder section labels) all fell under
# MIN_BAKED_HITS or MIN_SPAN. Lowering MIN_BAKED_HITS to catch the few remaining
# 3-4 hit books (a localized stray-number patch) starts admitting those.
PROSE_MIN = 120  # a neighbour this long counts as a prose paragraph
RUNHEAD_MIN_REPEAT = 8  # a short block repeated this often is a running header
RUNHEAD_MAX_LEN = 60  # running headers are short
MIN_BAKED_HITS = 5  # below this, a handful of hits is too often coincidence
MIN_SPAN = 0.10  # flagged numbers must cover this fraction of the book (drops
# localized clusters: footnote-poems, scraped comment sections)
MIN_RUN = 1  # ascending run is informative but not gated; the baked test already
# requires genuine sentence interruption, so a short run is not disqualifying


def roman_value(s: str) -> int | None:
    vals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    s = s.lower()
    for i, c in enumerate(s):
        if c not in vals:
            return None
        v = vals[c]
        total += -v if (i + 1 < len(s) and vals[s[i + 1]] > v) else v
    return total or None


def number_value(text: str) -> int | None:
    """A bare page-number-ish value (1-9999 arabic, or a roman numeral), else None."""
    if INT_RE.fullmatch(text):
        return int(text)
    if ROMAN_RE.fullmatch(text):
        return roman_value(text)
    return None


class _Blocks:
    """Minimal block extractor over html.parser, kept here so the module has no
    class-level import cost surprises; tracks the innermost block tag so each
    emitted (tag, text) pair is one rendered block in reading order."""

    def __init__(self):
        from html.parser import HTMLParser

        outer = self

        class _P(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.stack: list[str] = []
                self.buf: list[str] = []

            def handle_starttag(self, tag, attrs):
                del attrs
                if tag in BLOCK_TAGS:
                    outer._flush(self)
                    self.stack.append(tag)

            def handle_endtag(self, tag):
                if tag in BLOCK_TAGS:
                    outer._flush(self)
                    if self.stack:
                        self.stack.pop()

            def handle_data(self, data):
                self.buf.append(data)

        self.blocks: list[tuple[str, str]] = []
        self._parser = _P()

    def _flush(self, parser):
        text = "".join(parser.buf).strip()
        parser.buf.clear()
        if text:
            tag = parser.stack[-1] if parser.stack else "?"
            self.blocks.append((tag, text))

    def feed(self, html: str):
        self._parser.feed(html)
        self._parser.close()
        self._flush(self._parser)


def _spine_and_nav(z: zipfile.ZipFile) -> tuple[list[str], str | None]:
    names = set(z.namelist())
    container = ET.fromstring(z.read("META-INF/container.xml"))
    rootfile = container.find(".//c:rootfile", CONTAINER_NS)
    opf_path = rootfile.get("full-path") if rootfile is not None else None
    if not opf_path:
        raise ValueError("container.xml has no rootfile")
    opf = ET.fromstring(z.read(opf_path))
    base = os.path.dirname(opf_path)
    manifest: dict[str, str] = {}
    nav_href: str | None = None
    for it in opf.iter(OPF_NS + "item"):
        item_id, href = it.get("id"), it.get("href")
        if not item_id or not href:
            continue
        manifest[item_id] = href
        if "nav" in (it.get("properties") or ""):
            nav_href = href

    def full(href: str) -> str:
        return os.path.normpath(f"{base}/{href}" if base else href).replace("\\", "/")

    spine = []
    for itemref in opf.iter(OPF_NS + "itemref"):
        idref = itemref.get("idref")
        href = manifest.get(idref) if idref else None
        if href and full(href) in names:
            spine.append(full(href))
    return spine, (full(nav_href) if nav_href else None)


def _read_doc(z: zipfile.ZipFile, name: str) -> str:
    try:
        return z.read(name).decode("utf-8", "replace")
    except Exception:
        return z.read(name).decode("latin-1", "replace")


def scan(path: Path) -> dict:
    """Read a book's blocks in reading order and score baked-in page numbers."""
    with zipfile.ZipFile(path) as z:
        spine, nav = _spine_and_nav(z)
        blocks: list[tuple[str, str]] = []
        for doc in spine:
            if nav and doc == nav:
                continue
            parser = _Blocks()
            try:
                parser.feed(_read_doc(z, doc))
            except Exception:
                continue
            li = sum(1 for t, _ in parser.blocks if t == "li")
            # a doc that is mostly <li> is a TOC / page-list, not body text
            if parser.blocks and li / len(parser.blocks) > 0.5:
                continue
            blocks.extend(parser.blocks)

    text_len = sum(len(t) for _, t in blocks) or 1

    # Running headers / footers / watermarks: short blocks repeated many times.
    freq = Counter(
        t
        for t in (b[1] for b in blocks)
        if len(t) <= RUNHEAD_MAX_LEN and number_value(t) is None
    )
    runheads = {t for t, c in freq.items() if c >= RUNHEAD_MIN_REPEAT}

    hits: list[tuple[int, int]] = []  # (char offset, value)
    examples: list[dict] = []
    offset = 0
    for i, (tag, text) in enumerate(blocks):
        if tag in ("p", "div"):
            v = number_value(text)
            # 1500-2099 are almost always years (chronologies, dated chapters),
            # not page numbers; a real page count rarely reaches them.
            if v is not None and not (1500 <= v <= 2099):
                ptag, ptxt = blocks[i - 1] if i > 0 else ("", "")
                ntag, ntxt = blocks[i + 1] if i + 1 < len(blocks) else ("", "")
                prose_prev = ptag in ("p", "div") and len(ptxt) > PROSE_MIN
                prose_next = ntag in ("p", "div") and len(ntxt) > PROSE_MIN
                if prose_prev or prose_next:
                    prev_runhead = ptxt in runheads
                    next_runhead = ntxt in runheads
                    word_split = (
                        bool(ptxt) and ptxt[-1] == "-" and ptxt[-2:-1].isalpha()
                    )
                    lower_cont = bool(ntxt) and ntxt[0].islower()
                    # the previous body paragraph is unfinished (ends mid-word /
                    # mid-clause), so the number is wedged into a live sentence.
                    prev_unfinished = prose_prev and (
                        ptxt[-1].islower() or ptxt[-1] == ","
                    )
                    baked = (
                        word_split
                        or lower_cont
                        or (prev_unfinished and (prose_next or next_runhead))
                        or (prev_runhead and next_runhead)
                    )
                    if baked:
                        hits.append((offset, v))
                        if len(examples) < 8:
                            examples.append(
                                {"v": text, "prev": ptxt[-60:], "next": ntxt[:60]}
                            )
        offset += len(text)

    vals = [v for _, v in hits]
    if hits:
        span = (hits[-1][0] - hits[0][0]) / text_len
    else:
        span = 0.0
    run = best = 1 if vals else 0
    for i in range(1, len(vals)):
        if vals[i] - vals[i - 1] in (1, 2):
            best += 1
            run = max(run, best)
        else:
            best = 1
    return {
        "n_hits": len(hits),
        "span": span,
        "run": run,
        "watermark": any(
            re.search(r"download|boykma|\.com\b", h, re.I) for h in runheads
        ),
        "examples": examples,
    }


def is_defective(r: dict) -> bool:
    return (
        r["n_hits"] >= MIN_BAKED_HITS and r["span"] >= MIN_SPAN and r["run"] >= MIN_RUN
    )


def audit_library() -> int:
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

    found: list[tuple] = []
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
        if is_defective(r):
            found.append((book_id, title, booktag.get(book_id, "?"), r))

    print(f"Scanned {scanned} EPUBs in {library_root}\n")
    if found:
        print(f"{RED}{BOLD}BAKED-IN PAGE NUMBERS ({len(found)}){RESET}")
        for book_id, title, tag, r in sorted(found, key=lambda x: -x[3]["n_hits"]):
            mark = f"  {YELLOW}[watermark]{RESET}" if r["watermark"] else ""
            print(
                f"  {RED}#{book_id}{RESET} [{tag}] {title}{mark}\n"
                f"    {r['n_hits']} baked numbers, {r['span'] * 100:.0f}% of book, run {r['run']}"
            )
            for ex in r["examples"][:3]:
                print(f"      ...{ex['prev']}  {BOLD}{ex['v']}{RESET}  {ex['next']}...")
        print()

    if errors:
        print(f"{YELLOW}{BOLD}SCAN ERRORS ({len(errors)}){RESET}")
        for book_id, title, msg in errors:
            print(f"  #{book_id} {title}\n    {msg}")
        print()

    if not found:
        print(f"{GREEN}{BOLD}CLEAN{RESET}: no baked-in page numbers found.")
        return 0 if not errors else 1
    print(
        f"{RED}{BOLD}FOUND{RESET}: {len(found)} file(s) need review "
        f"(re-source and replace bad conversions)."
    )
    return 1


def audit_directory(directory: Path) -> int:
    """Vet loose .epub files (recursively) before they enter the library."""
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory.")
        return 2
    epubs = sorted(directory.rglob("*.epub"))
    if not epubs:
        print(f"No .epub files found under {directory}")
        return 2

    print(f"Auditing {len(epubs)} EPUB(s) in {directory}\n")
    problems = 0
    errors = 0
    for path in epubs:
        try:
            r = scan(path)
        except Exception as e:
            print(f"  {YELLOW}ERROR {RESET} {path.name}\n      {type(e).__name__}: {e}")
            errors += 1
            continue
        if is_defective(r):
            problems += 1
            mark = "  [watermark]" if r["watermark"] else ""
            print(f"  {RED}REVIEW{RESET} {path.name}{mark}")
            print(
                f"      {r['n_hits']} baked numbers, {r['span'] * 100:.0f}% of book, run {r['run']}"
            )
            for ex in r["examples"][:2]:
                print(f"      ...{ex['prev']}  {ex['v']}  {ex['next']}...")
        else:
            print(f"  {GREEN}OK    {RESET} {path.name}")
    print()

    if problems == 0 and errors == 0:
        print(
            f"{GREEN}{BOLD}CLEAN{RESET}: no baked-in page numbers in {len(epubs)} file(s)."
        )
        return 0
    print(
        f"{RED}{BOLD}FOUND{RESET}: {problems} file(s) need review, {errors} scan error(s)."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit EPUB content for print page numbers baked into the body text."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="vet loose .epub files under this directory instead of auditing the library",
    )
    args = parser.parse_args()
    if args.directory:
        return audit_directory(Path(args.directory).expanduser())
    return audit_library()


if __name__ == "__main__":
    sys.exit(main())
