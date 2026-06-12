#!/usr/bin/env python3
"""spot_check.py: randomized metadata + file-integrity audit of a Calibre library.

Samples N random books and, for each, checks the things pattern-based sweeps
miss: metadata field quality (title corruption, junk author entries, mojibake,
missing or stub descriptions) and the actual file contents (EPUB archive
integrity, spine completeness, text volume; PDF header/page count; DJVU page
count). The point of random sampling is honesty: every record has equal odds
of inspection, so the result estimates whole-library quality instead of
confirming what curation already looked at.

Mechanical checks only flag; the human (or LLM) judgment pass happens over the
emitted review bundle, which carries title/author/tag/series plus a blurb
excerpt per sampled book. Validator-owned checks (tag-in-spec, identifier
hygiene, coverage) are deliberately not duplicated here.

Read-only against metadata.db (mode=ro). Stdlib only; shells out to exiftool
(PDF) and djvused (DJVU) when present, and skips those checks when not.

Usage:
  python3 spot_check.py [--db PATH] [--n 600] [--seed N]
                        [--report PATH.tsv] [--bundle PATH.txt]

Exit code: number of books with hard failures (broken archive, empty spine,
missing file), capped at 99.
"""

import argparse
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

FMT_EXT = {
    "EPUB": ".epub",
    "PDF": ".pdf",
    "DJVU": ".djvu",
    "MOBI": ".mobi",
    "AZW3": ".azw3",
}

# Sequences that appear when UTF-8 is decoded as latin-1/cp1252 somewhere upstream.
_MOJIBAKE = re.compile(r"[�]|â€|Ã[©¨¤¶¼£±]|Â[«»°·]")
_AUTHOR_JUNK = re.compile(
    r"\b(press|publishing|publications?|books|classics|editors?|edition|"
    r"library|gmbh|llc|inc)\b",
    re.IGNORECASE,
)
# Words glued case-inside-out, e.g. "SPuter" for "Computer": two-plus leading
# capitals welded onto a lowercase tail, or capitals erupting mid-word. Real
# tech intercaps (SQLite, QBasic) are allowlisted; this flag is advisory.
_CASE_GARBLE = re.compile(r"\b[A-Z]{2,}[a-z]{2,}\w*|[a-z][A-Z]{2,}[a-z]")
_CASE_OK = {"SQLite", "QBasic", "OAuth", "JScript", "DRMed", "POSIXly"}

MIN_COMMENT = 120  # chars; below this a description is a stub
MIN_EPUB_TEXT = 30_000  # bytes of spine text; below this a "book" is suspect
MIN_PDF_PAGES = 8


def lint_title(title: str) -> list[str]:
    flags = []
    if title != title.strip() or "  " in title:
        flags.append("TITLE_WHITESPACE")
    if _MOJIBAKE.search(title):
        flags.append("TITLE_MOJIBAKE")
    garbled = [
        m.group(0) for m in _CASE_GARBLE.finditer(title) if m.group(0) not in _CASE_OK
    ]
    if garbled:
        flags.append(f"TITLE_CASE_GARBLE:{garbled[0]}")
    return flags


def lint_authors(authors: list[str]) -> list[str]:
    flags = []
    for a in authors:
        if _AUTHOR_JUNK.search(a):
            flags.append(f"AUTHOR_JUNK:{a}")
        if _MOJIBAKE.search(a):
            flags.append(f"AUTHOR_MOJIBAKE:{a}")
    if len(authors) > 4:
        flags.append(f"AUTHOR_CROWD:{len(authors)}")
    if not authors:
        flags.append("AUTHOR_MISSING")
    return flags


def lint_comment(comment: str | None) -> list[str]:
    text = re.sub(r"<[^>]+>", "", comment or "").strip()
    if not text:
        return ["COMMENT_MISSING"]
    flags = []
    if len(text) < MIN_COMMENT:
        flags.append(f"COMMENT_STUB:{len(text)}")
    if _MOJIBAKE.search(text):
        flags.append("COMMENT_MOJIBAKE")
    return flags


def check_epub(path: Path) -> list[str]:
    """Archive integrity, container/OPF sanity, spine completeness, text volume."""
    flags: list[str] = []
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as e:
        return [f"EPUB_BADZIP:{e.__class__.__name__}"]
    with z:
        bad = z.testzip()
        if bad is not None:
            flags.append(f"EPUB_CRC:{bad}")
        names = set(z.namelist())
        if "META-INF/container.xml" not in names:
            return flags + ["EPUB_NO_CONTAINER"]
        try:
            root = ET.fromstring(z.read("META-INF/container.xml"))
            rootfile = root.find(
                ".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile"
            )
            opf_path = rootfile.get("full-path") if rootfile is not None else None
            if not opf_path:
                return flags + ["EPUB_OPF_UNREADABLE:NoRootfile"]
            opf = ET.fromstring(z.read(opf_path))
        except Exception as e:
            return flags + [f"EPUB_OPF_UNREADABLE:{e.__class__.__name__}"]
        ns = {"o": "http://www.idpf.org/2007/opf"}
        base = os.path.dirname(opf_path)
        manifest = {
            i.get("id"): os.path.normpath(os.path.join(base, i.get("href", "")))
            for i in opf.findall(".//o:manifest/o:item", ns)
        }
        spine = [i.get("idref") for i in opf.findall(".//o:spine/o:itemref", ns)]
        if not spine:
            flags.append("EPUB_EMPTY_SPINE")
        missing = [s for s in spine if manifest.get(s) not in names]
        if missing:
            flags.append(f"EPUB_SPINE_MISSING:{len(missing)}/{len(spine)}")
        text = sum(
            z.getinfo(manifest[s]).file_size for s in spine if manifest.get(s) in names
        )
        if spine and text < MIN_EPUB_TEXT:
            flags.append(f"EPUB_THIN_TEXT:{text}B")
    return flags


def check_pdf(path: Path) -> list[str]:
    flags = []
    with open(path, "rb") as f:
        if f.read(5) != b"%PDF-":
            flags.append("PDF_BAD_HEADER")
        f.seek(max(0, path.stat().st_size - 2048))
        if b"%%EOF" not in f.read():
            flags.append("PDF_NO_EOF")
    if shutil.which("exiftool"):
        r = subprocess.run(
            ["exiftool", "-m", "-s3", "-PageCount", str(path)],
            capture_output=True,
            text=True,
        )
        pages = r.stdout.strip()
        if not pages.isdigit():
            flags.append("PDF_UNREADABLE_PAGECOUNT")
        elif int(pages) < MIN_PDF_PAGES:
            flags.append(f"PDF_FEW_PAGES:{pages}")
    return flags


def check_djvu(path: Path) -> list[str]:
    if not shutil.which("djvused"):
        return []
    r = subprocess.run(
        ["djvused", str(path), "-e", "n"], capture_output=True, text=True
    )
    pages = r.stdout.strip()
    if not pages.isdigit() or int(pages) < 2:
        return [f"DJVU_SUSPECT_PAGES:{pages or 'unreadable'}"]
    return []


def check_file(library: Path, row) -> list[str]:
    fmt, rel, name = row
    ext = FMT_EXT.get(fmt)
    if ext is None:
        return []
    path = library / rel / f"{name}{ext}"
    if not path.is_file():
        return [f"FILE_MISSING:{fmt}"]
    if fmt == "EPUB":
        return check_epub(path)
    if fmt == "PDF":
        return check_pdf(path)
    if fmt == "DJVU":
        return check_djvu(path)
    return []


HARD = (
    "EPUB_BADZIP",
    "EPUB_CRC",
    "EPUB_NO_CONTAINER",
    "EPUB_OPF_UNREADABLE",
    "EPUB_EMPTY_SPINE",
    "EPUB_SPINE_MISSING",
    "FILE_MISSING",
    "PDF_BAD_HEADER",
)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Randomized metadata and file-integrity spot check."
    )
    ap.add_argument("--db", default="metadata.db")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--report", default="spot_check_report.tsv")
    ap.add_argument("--bundle", default="spot_check_bundle.txt")
    args = ap.parse_args()

    db = Path(args.db).expanduser()
    if db.is_dir():
        db = db / "metadata.db"
    if not db.is_file():
        print(f"ERROR: {db} not found", file=sys.stderr)
        return 99
    library = db.parent
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cur = con.cursor()

    all_ids = [r[0] for r in cur.execute("SELECT id FROM books")]
    n = min(args.n, len(all_ids))
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(all_ids, n))
    print(
        f"Sampling {n} of {len(all_ids)} books"
        + (f" (seed {args.seed})" if args.seed is not None else "")
    )

    hard_failures = 0
    flagged = 0
    with open(args.report, "w") as rep, open(args.bundle, "w") as bun:
        rep.write("id\tflags\ttitle\n")
        for bid in sample:
            title, path = cur.execute(
                "SELECT title, path FROM books WHERE id=?", (bid,)
            ).fetchone()
            authors = [
                r[0]
                for r in cur.execute(
                    """SELECT a.name FROM books_authors_link l JOIN authors a
                   ON a.id=l.author WHERE l.book=? ORDER BY l.id""",
                    (bid,),
                )
            ]
            tag = (
                cur.execute(
                    """SELECT GROUP_CONCAT(t.name, ', ') FROM books_tags_link l
                   JOIN tags t ON t.id=l.tag WHERE l.book=?""",
                    (bid,),
                ).fetchone()[0]
                or ""
            )
            series = cur.execute(
                """SELECT s.name || ' #' || CAST(b.series_index AS TEXT)
                   FROM books_series_link sl JOIN series s ON s.id=sl.series
                   JOIN books b ON b.id=sl.book WHERE sl.book=?""",
                (bid,),
            ).fetchone()
            comment = cur.execute(
                "SELECT text FROM comments WHERE book=?", (bid,)
            ).fetchone()
            comment = comment[0] if comment else None
            fmts = cur.execute(
                "SELECT format, ?, name FROM data WHERE book=?", (path, bid)
            ).fetchall()

            flags = lint_title(title) + lint_authors(authors) + lint_comment(comment)
            for row in fmts:
                flags += check_file(library, row)

            if any(f.startswith(HARD) for f in flags):
                hard_failures += 1
            if flags:
                flagged += 1
                rep.write(f"{bid}\t{';'.join(flags)}\t{title[:60]}\n")

            blurb = re.sub(r"<[^>]+>", "", comment or "").replace("\n", " ")[:220]
            ser = f" [{series[0]}]" if series else ""
            fl = f" !!{';'.join(flags)}" if flags else ""
            bun.write(
                f"{bid}|{tag}|{title[:48]}|{'; '.join(authors)[:40]}{ser}{fl}\n"
                f"   {blurb}\n"
            )

    print(f"flagged: {flagged}/{n} ({hard_failures} hard failures)")
    print(f"report: {args.report}\nbundle: {args.bundle}")
    return min(hard_failures, 99)


if __name__ == "__main__":
    sys.exit(main())
