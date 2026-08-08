#!/usr/bin/env python3
"""
audit_isbns.py: check each book's stored ISBN against the ISBN it prints itself.

Every other audit here asks whether the catalogue is internally consistent.
This one asks something no tool in the Calibre ecosystem asks: does the ISBN
recorded against a book actually identify THAT book? Calibre downloads metadata
but never re-examines what it stored, so a wrong ISBN is invisible forever. It
matters because an ISBN is what other systems key on: hand a catalogue to a
library service and the ISBN, not the title, decides which book you get.

The failure is real and it is quiet. A sweep of a 6,786-ISBN library that was
already validator-clean found 51 identifiers pointing at something else, most
often a SIBLING of the right book: the same publisher's next title, so the
number looks plausible and the checksum passes. Programming Clojure carried
tmux 2's ISBN; Spelunky carried Super Mario Bros. 3's; A Book on C carried
9782147483649, which is the 2147483649 integer-overflow constant wearing an
ISBN's clothes.

For books no bibliographic database has heard of (small-press RPGs, indie
ebooks, print-on-demand reprints) the publisher's own copyright page is the
best authority there is, and it is sitting on disk. This script reads it.

CIRCULARITY, the one trap that would make this whole tool a lie: it reads
BODY TEXT only, never a file's embedded metadata. reconcile_file_metadata.py
exists to write the database's values INTO those metadata blocks, so comparing
against them would be comparing the database with itself and would confirm
every error it was built to find. Rendered page text is the publisher's.

The hard part is NOT finding printed ISBNs; it is NOT crying wolf. Three benign
things look like a mismatch to a crude check and are classified apart here:

  * bibliographies   A book that cites other books prints their ISBNs. The Art
                     of UNIX Programming prints 49. Above --max-printed
                     distinct ISBNs a file is read as a citing work and its
                     numbers are not evidence about itself.
  * bundles/series   A boxed set or omnibus legitimately prints each component's
                     ISBN, and a series volume may print its siblings'. Several
                     printed ISBNs, none of them the stored one, is reported
                     AMBIGUOUS: a human picks, the script does not guess.
  * format variants  Print and ebook editions of one book get different ISBNs
                     that differ only in the last digits. Sharing a publisher
                     prefix makes a disagreement a VARIANT (probably another
                     binding) rather than a MISMATCH (a different publisher
                     block, which is where a genuinely wrong ISBN sits).

A MISMATCH is NOT a claim that the ISBN is wrong. On a real library the
commonest cause by far is that the FILE is a different edition than the
catalogue records, which is worth knowing but is a different problem from a
wrong book; an ISBN alone cannot tell the two apart, and saying otherwise would
be crying exactly the wolf this tool is careful about. Deciding needs a
bibliographic lookup, or a human.

THE PRINTED ISBN CAN ITSELF BE WRONG, which is the limit of this tool's whole
premise and the reason it only ever reports. Two real cases, both caught by a
VARIANT verdict and both resolved in favour of the DATABASE:

  * publisher misprint  The TSR Forgotten Realms Campaign Setting boxed set
                        prints 1-56076-605-0, which belongs to The Jungles of
                        Chult. A documented typo, in the book, forever.
  * copied front matter A small press reusing its last book's copyright page.
                        Night Witches (Bully Pulpit, 2014) prints Durance's
                        ISBN because the template was not updated.

Both are same-publisher cases, so both land as VARIANT. That is exactly why
VARIANT exists: the shape "same publisher block, different number" covers
innocent format variants AND publisher mistakes, and no rule separates them
without a human.

Nothing is written, ever, and there is deliberately no --apply. Across the
sweep that motivated this tool, single-source verdicts were wrong often enough
to matter: an auto-fixer would have "corrected" Curse of Strahd, Cold Mountain,
Kitchen and The Master and Margarita, all of which were right. Findings are for
a human to judge.

Text comes from EPUB spine documents (stdlib zipfile), PDF via pdftotext, and
DJVU via djvutxt; the external binaries are optional and their absence is
reported, not fatal. MOBI/AZW3 are skipped: extracting their text needs more
machinery than a check like this earns.

Run from the library directory:
    python3 audit_isbns.py                        # every book with an ISBN
    python3 audit_isbns.py --tag NonFic.Tech      # one branch of the taxonomy
    python3 audit_isbns.py --id 1969,3189         # specific books
    python3 audit_isbns.py --format tsv > out.tsv # machine-readable

Exit codes:
    0 = no disagreement found
    1 = at least one MISMATCH/VARIANT/AMBIGUOUS finding, or an unreadable file
    2 = setup error (missing DB, bad arguments)
"""

import argparse
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import unicodedata
import zipfile

# A number only counts when the book introduces it as an ISBN. Bare 10-13 digit
# runs are everywhere in printed matter (dates, part numbers, phone numbers).
ISBN_LABELLED_RE = re.compile(
    r"ISBN(?:[-\s]?1[03])?[^0-9]{0,24}((?:97[89][-\s]?)?(?:\d[-\s]?){9}[\dXx])",
    re.IGNORECASE,
)
TAG_RE = re.compile(r"<[^>]+>")
XHTML_SUFFIXES = (".xhtml", ".html", ".htm")

# Front matter holds the copyright page; back matter holds RPG credits pages.
EPUB_HEAD_DOCS = 8
EPUB_TAIL_DOCS = 3
PDF_HEAD_PAGES = 14
PDF_TAIL_PAGES = 3
DJVU_PAGES = 12

MAX_TEXT_BYTES = 4 * 1024 * 1024
SUBPROCESS_TIMEOUT = 90

# Above this many distinct printed ISBNs, treat the file as citing other works.
DEFAULT_MAX_PRINTED = 6

# Shared leading digits that imply one publisher: EAN prefix + group + the
# first two registrant digits. A registrant is 2-7 digits and INVERSELY sized
# with the publisher (Penguin is 0-14, a one-book press is 0-9883909), so no
# fixed length is right for everyone; six is chosen to catch the big houses,
# whose short registrants otherwise made two HarperCollins ISBNs look like
# rival publishers. Deliberately coarse: it only downgrades a finding's
# severity, never suppresses one.
REGISTRANT_PREFIX_LEN = 6

VERDICT_ORDER = [
    "MISMATCH",
    "AMBIGUOUS",
    "VARIANT",
    "CONFIRMED",
    "NO_ISBN_PRINTED",
    "UNREADABLE",
    "SKIPPED",
]

FINDINGS = ("MISMATCH", "AMBIGUOUS", "VARIANT")


# --------------------------------------------------------------------------- #
# ISBN arithmetic
# --------------------------------------------------------------------------- #
def normalise(raw: str) -> str:
    return re.sub(r"[^0-9Xx]", "", raw or "").upper()


def checksum_ok(raw: str) -> bool:
    s = normalise(raw)
    if len(s) == 13 and s.isdigit():
        total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(s[:12]))
        return (10 - total % 10) % 10 == int(s[12])
    if len(s) == 10 and s[:9].isdigit() and (s[9].isdigit() or s[9] == "X"):
        total = sum((10 - i) * int(d) for i, d in enumerate(s[:9]))
        total += 10 if s[9] == "X" else int(s[9])
        return total % 11 == 0
    return False


def to_isbn13(raw: str) -> str:
    """Fold ISBN-10 to ISBN-13 so stored and printed forms compare like for like."""
    s = normalise(raw)
    if len(s) != 10:
        return s
    core = "978" + s[:9]
    total = sum((1 if i % 2 == 0 else 3) * int(d) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def same_registrant(a: str, b: str) -> bool:
    """Do two ISBN-13s come from the same publisher's number block?"""
    if len(a) != 13 or len(b) != 13:
        return False
    return a[:REGISTRANT_PREFIX_LEN] == b[:REGISTRANT_PREFIX_LEN]


def printed_isbns(text: str) -> list[str]:
    found = {
        to_isbn13(m) for m in ISBN_LABELLED_RE.findall(text or "") if checksum_ok(m)
    }
    return sorted(found)


# --------------------------------------------------------------------------- #
# text extraction (body text only -- never embedded metadata)
# --------------------------------------------------------------------------- #
def epub_text(path: str) -> str | None:
    try:
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(XHTML_SUFFIXES)]
            if not names:
                return None
            names.sort()
            chosen = names[:EPUB_HEAD_DOCS] + names[-EPUB_TAIL_DOCS:]
            out: list[str] = []
            size = 0
            for name in dict.fromkeys(chosen):
                try:
                    raw = z.read(name)
                except KeyError, RuntimeError, zipfile.BadZipFile:
                    continue
                chunk = TAG_RE.sub(" ", raw.decode("utf-8", "replace"))
                out.append(chunk)
                size += len(chunk)
                if size > MAX_TEXT_BYTES:
                    break
            return "\n".join(out) if out else None
    except zipfile.BadZipFile, OSError:
        return None


def _run(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=SUBPROCESS_TIMEOUT, check=False
        )
    except FileNotFoundError, subprocess.TimeoutExpired, OSError:
        return None
    if proc.returncode != 0 and not proc.stdout:
        return None
    return proc.stdout[:MAX_TEXT_BYTES].decode("utf-8", "replace")


def pdf_page_count(path: str) -> int | None:
    out = _run(["pdfinfo", path])
    if not out:
        return None
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1)) if m else None


def pdf_text(path: str) -> str | None:
    chunks = []
    head = _run(["pdftotext", "-f", "1", "-l", str(PDF_HEAD_PAGES), path, "-"])
    if head:
        chunks.append(head)
    total = pdf_page_count(path)
    if total and total > PDF_HEAD_PAGES:
        first = max(total - PDF_TAIL_PAGES + 1, 1)
        tail = _run(["pdftotext", "-f", str(first), "-l", str(total), path, "-"])
        if tail:
            chunks.append(tail)
    return "\n".join(chunks) if chunks else None


def djvu_text(path: str) -> str | None:
    return _run(["djvutxt", f"--page=1-{DJVU_PAGES}", path])


EXTRACTORS = {".epub": epub_text, ".pdf": pdf_text, ".djvu": djvu_text}


def book_text(book_dir: str) -> tuple[str | None, str | None]:
    """Body text of the first readable file, with the format that supplied it.

    The returned format distinguishes two failures that look alike in a report
    but mean opposite things: a book held only as MOBI/AZW3 was SKIPPED by
    design (no extractor exists here), while a supported format that yielded
    nothing is genuinely UNREADABLE and may be a damaged file.
    """
    try:
        entries = sorted(os.listdir(book_dir))
    except OSError:
        return None, None
    attempted: str | None = None
    for entry in entries:
        ext = os.path.splitext(entry)[1].lower()
        extractor = EXTRACTORS.get(ext)
        if not extractor:
            continue
        attempted = ext.lstrip(".")
        text = extractor(os.path.join(book_dir, entry))
        if text:
            return text, attempted
    return None, attempted


# --------------------------------------------------------------------------- #
# verdict
# --------------------------------------------------------------------------- #
def classify(stored: str, printed: list[str], max_printed: int) -> str:
    if not printed:
        return "NO_ISBN_PRINTED"
    if len(printed) > max_printed:
        # a citing work: its ISBNs describe other books, not this one
        return "CONFIRMED" if stored in printed else "NO_ISBN_PRINTED"
    if stored in printed:
        return "CONFIRMED"
    if len(printed) > 1:
        return "AMBIGUOUS"
    if same_registrant(stored, printed[0]):
        return "VARIANT"
    return "MISMATCH"


# --------------------------------------------------------------------------- #
# database (read-only) and scoping
# --------------------------------------------------------------------------- #
def find_db(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.environ.get("CALIBRE_LIBRARY_PATH")
    if env:
        return os.path.join(env, "metadata.db")
    return os.path.join(os.getcwd(), "metadata.db")


def tag_matches(tag: str, prefixes: list[str]) -> bool:
    """Anchored-hierarchical match, the rule cquarry's `tags:` search uses:
    'NonFic' matches 'NonFic' and anything under 'NonFic.', but never
    'NonFiction'."""
    return any(tag == p or tag.startswith(p + ".") for p in prefixes)


def load_targets(
    db_path: str, ids: list[int] | None, prefixes: list[str] | None
) -> list[dict]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT b.id, b.title, b.path, i.val,
                   (SELECT t.name FROM tags t
                      JOIN books_tags_link l ON l.tag = t.id
                     WHERE l.book = b.id LIMIT 1)
              FROM books b
              JOIN identifiers i ON i.book = b.id AND i.type = 'isbn'
             ORDER BY b.id
            """
        ).fetchall()
    finally:
        con.close()

    wanted = set(ids) if ids else None
    out = [
        {
            "id": r[0],
            "title": r[1],
            "path": r[2],
            "isbn": to_isbn13(r[3]),
            "tag": r[4] or "",
        }
        for r in rows
        if wanted is None or r[0] in wanted
    ]
    if prefixes:
        out = [t for t in out if tag_matches(t["tag"], prefixes)]
    return out


# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
def _plain(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def report_text(results: list[dict], quiet: bool) -> None:
    counts: dict[str, int] = {}
    for r in results:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1

    # --quiet drops the decoration (headers, the summary table), never the
    # findings: they are the whole point of running this.
    findings = [r for r in results if r["verdict"] in FINDINGS]
    if findings:
        if not quiet:
            print("Disagreements between the catalogue and the books themselves:\n")
        for r in sorted(findings, key=lambda r: VERDICT_ORDER.index(r["verdict"])):
            print(f"  [{r['verdict']}] #{r['id']} {_plain(r['title'])[:60]}")
            print(f"      stored:  {r['isbn']}")
            print(f"      printed: {', '.join(r['printed']) or '-'}  ({r['format']})")
        print()

    if not quiet:
        print("Summary")
        for verdict in VERDICT_ORDER:
            if counts.get(verdict):
                print(f"  {verdict:16} {counts[verdict]:5}")
        print(f"  {'TOTAL':16} {len(results):5}")


def report_tsv(results: list[dict]) -> None:
    print("id\tverdict\tstored\tprinted\tformat\ttitle")
    for r in results:
        print(
            f"{r['id']}\t{r['verdict']}\t{r['isbn']}\t{','.join(r['printed'])}\t"
            f"{r['format'] or ''}\t{_plain(r['title'])}"
        )


# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Check stored ISBNs against the ISBN each book prints itself.",
        epilog="Read-only by design: it reports disagreements and never writes.",
    )
    ap.add_argument("--db", help="path to metadata.db (default: ./metadata.db)")
    ap.add_argument(
        "--tag",
        metavar="PREFIX",
        help="comma-separated tag prefixes, anchored-hierarchical "
        "(e.g. 'NonFic' covers NonFic and every NonFic.* leaf)",
    )
    ap.add_argument("--id", help="comma-separated book ids to restrict to")
    ap.add_argument("--sample", type=int, metavar="N", help="random sample of N books")
    ap.add_argument(
        "--seed", type=int, default=None, help="seed for --sample (reproducible)"
    )
    ap.add_argument("--limit", type=int, help="stop after N books")
    ap.add_argument(
        "--max-printed",
        type=int,
        default=DEFAULT_MAX_PRINTED,
        help=f"above this many printed ISBNs a file is read as citing other "
        f"works (default {DEFAULT_MAX_PRINTED})",
    )
    ap.add_argument(
        "--format", choices=("text", "tsv", "json"), default="text", help="output form"
    )
    ap.add_argument("--quiet", action="store_true", help="suppress per-book lines")
    args = ap.parse_args()

    ids = None
    if args.id:
        try:
            ids = [int(x) for x in args.id.split(",") if x.strip()]
        except ValueError:
            print("setup error: --id takes comma-separated integers", file=sys.stderr)
            return 2

    prefixes = (
        [p.strip() for p in args.tag.split(",") if p.strip()] if args.tag else None
    )

    db_path = find_db(args.db)
    if not os.path.exists(db_path):
        print(f"setup error: no metadata.db at {db_path}", file=sys.stderr)
        return 2
    library_root = os.path.dirname(os.path.abspath(db_path))

    try:
        targets = load_targets(db_path, ids, prefixes)
    except sqlite3.Error as exc:
        print(f"setup error: {exc}", file=sys.stderr)
        return 2

    if args.sample and args.sample < len(targets):
        random.Random(args.seed).shuffle(targets)
        targets = targets[: args.sample]
        targets.sort(key=lambda t: t["id"])
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print("no books with an ISBN matched the selection", file=sys.stderr)
        return 2

    if args.format == "text" and not args.quiet:
        print(f"Reading {len(targets)} book(s) for a printed ISBN...\n")

    results = []
    for target in targets:
        text, fmt = book_text(os.path.join(library_root, target["path"]))
        if text is None:
            # no supported format present at all vs one that yielded nothing
            verdict = "SKIPPED" if fmt is None else "UNREADABLE"
            printed = []
        else:
            printed = printed_isbns(text)
            verdict = classify(target["isbn"], printed, args.max_printed)
        results.append(
            {**target, "verdict": verdict, "printed": printed, "format": fmt}
        )

    if args.format == "json":
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        print()
    elif args.format == "tsv":
        report_tsv(results)
    else:
        report_text(results, args.quiet)

    flagged = any(r["verdict"] in (*FINDINGS, "UNREADABLE") for r in results)
    return 1 if flagged else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
