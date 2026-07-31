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
(PDF) and djvused (DJVU) when present, and skips those checks when not. The
advisory COMMENT_TRUNCATED check reads /usr/share/dict/words on the same terms:
used when the system has it, silently skipped when it does not.

Usage:
  python3 spot_check.py [--db PATH] [--n 600] [--seed N]
                        [--report PATH.tsv] [--bundle PATH.txt]

Exit code: number of books with hard failures (broken archive, empty spine,
missing file), capped at 99.
"""

import argparse
import html
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
from urllib.parse import unquote

FMT_EXT = {
    "EPUB": ".epub",
    "PDF": ".pdf",
    "DJVU": ".djvu",
    "MOBI": ".mobi",
    "AZW3": ".azw3",
}

# Sequences that appear when UTF-8 is decoded as latin-1/cp1252 somewhere upstream.
# "Ã" or "Â" followed by anything in the latin-1 supplement punctuation/accent band
# is never valid text: a real Portuguese "Ã" is followed by an ASCII vowel, never by
# U+0080-U+00BF. Enumerating individual accents missed the commonest lead byte of
# all, "Ã¢" (the double-encoded form of a curly apostrophe).
_MOJIBAKE = re.compile("[\ufffd]|\u00e2\u20ac|[\u00c3\u00c2][\u0080-\u00bf]")
_AUTHOR_JUNK = re.compile(
    r"\b(press|publishing|publications?|books|classics|editors?|edition|"
    r"library|gmbh|llc|inc)\b",
    re.IGNORECASE,
)
# Words glued case-inside-out, e.g. "SPuter" for "Computer": two-plus leading
# capitals welded onto a lowercase tail, or capitals erupting mid-word. Real
# tech intercaps (SQLite, QBasic) are allowlisted; this flag is advisory.
_CASE_GARBLE = re.compile(r"\b[A-Z]{2,}[a-z]{2,}\w*|[a-z][A-Z]{2,}[a-z]")
_CASE_OK = {
    "SQLite",
    "QBasic",
    "OAuth",
    "JScript",
    "DRMed",
    "POSIXly",
    "OCaml",
    "NCurses",
}

MIN_COMMENT = 120  # chars; below this a description is a stub
MIN_EPUB_TEXT = 30_000  # bytes of spine text; below this a "book" is suspect
MIN_PDF_PAGES = 8
MIN_TRUNC_COMMENT = 120  # below this the truncation heuristic has too little to go on

# Optional wordlist, used only by the advisory COMMENT_TRUNCATED check. Same
# contract as exiftool/djvused above: use it when the system has it, skip the
# check when it does not. Not a Python dependency.
_WORDLISTS = ("/usr/share/dict/words", "/usr/share/dict/american-english")
# Sentence-final punctuation, plus the characters a legitimately list-shaped or
# link-shaped blurb can end on.
_TERMINAL = ".!?\"'’”)]…:;*-–—•>/"
_URLISH = re.compile(r"https?://|www\.|\.[a-z]{2,4}(\.[a-z]{2})?$", re.IGNORECASE)
_TOC_TAIL = re.compile(r"\n\s*\d+\s+[^\n]{0,60}$")


def _load_wordlist() -> set[str] | None:
    for candidate in _WORDLISTS:
        path = Path(candidate)
        if path.is_file():
            with path.open(encoding="utf-8", errors="ignore") as fh:
                return {line.strip().lower() for line in fh if line.strip()}
    return None


_WORDS = _load_wordlist()


def plain_text(markup: str | None) -> str:
    """Strip tags AND decode entities, so lints see what a reader sees."""
    text = re.sub(
        r"<(script|style)[^>]*>.*?</\1>",
        " ",
        markup or "",
        flags=re.DOTALL | re.IGNORECASE,
    )
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def _is_known_word(word: str) -> bool:
    base = word.lower().rstrip("'’")
    if not _WORDS:
        return True
    return (
        base in _WORDS
        or base.rstrip("s") in _WORDS
        or (base + "e") in _WORDS
        or base.replace("-", "") in _WORDS
        or ("-" in base and all(p in _WORDS for p in base.split("-") if p))
    )


def looks_truncated(text: str) -> bool:
    """Advisory: a description that stops mid-word, e.g. 'her manipul'.

    Sources truncate blurbs at a length cap, and the result passes every other
    check because it is long and well-formed; only the final word gives it away.
    Requires a wordlist. Proper nouns, URLs, contents lists, and non-English
    tails are excluded because they legitimately end without punctuation.
    """
    if _WORDS is None or len(text) < MIN_TRUNC_COMMENT or text[-1] in _TERMINAL:
        return False
    if _URLISH.search(text[-40:]) or _TOC_TAIL.search(text):
        return False
    # Must be prose. A single long token (an id, a hash, a run of filler) has no
    # word boundaries and its "final word" is the whole string.
    if len(text.split()) < 15:
        return False
    match = re.search(r"(?<=\s)([A-Za-z][A-Za-z'’-]*)\s*$", text)
    if not match:
        return False
    word = match.group(1)
    if word[0].isupper() or any(ord(c) > 127 for c in word):
        return False
    return len(word) < 3 or not _is_known_word(word)


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
    text = plain_text(comment)
    if not text:
        return ["COMMENT_MISSING"]
    flags = []
    if len(text) < MIN_COMMENT:
        flags.append(f"COMMENT_STUB:{len(text)}")
    if _MOJIBAKE.search(text):
        flags.append("COMMENT_MOJIBAKE")
    if looks_truncated(text):
        flags.append("COMMENT_TRUNCATED")
    return flags


def _by_local_name(root, name: str) -> list:
    """Find elements by local name, ignoring which namespace the package declares.

    An EPUB 2/3 package is in the OPF namespace, but legacy OEB 1.0 packages
    (OverDrive-era conversions) use http://openebook.org/namespaces/oeb-package/1.0/.
    Matching the OPF namespace alone finds nothing in those files, so a complete
    book reads as EPUB_EMPTY_SPINE, a HARD failure.
    """
    return [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == name]


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
        base = os.path.dirname(opf_path)
        # OPF hrefs are URL-encoded per spec, so a filename with a space arrives as
        # "%20" and would never match the zip namelist: that reads as a missing
        # spine item, which is a HARD failure. Decode before resolving.
        manifest = {
            i.get("id"): os.path.normpath(
                os.path.join(base, unquote(i.get("href", "").split("#", 1)[0]))
            )
            for i in _by_local_name(opf, "item")
        }
        spine = [i.get("idref") for i in _by_local_name(opf, "itemref")]
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

            blurb = plain_text(comment).replace("\n", " ")[:220]
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
