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

Review mode (--review) exists because the checks above can only judge shape.
Whether a title is the right title, whether an author field holds the person who
wrote the book, and whether a description describes THIS book are judgements no
pattern can make. It emits full title/author/comment blocks in numbered chunks,
takes back a verdict file, and keeps the answers in a ledger so reviewed books
drop out of later samples and the work accumulates across sessions instead of
being repeated. Recording refuses unless the verdict ids reconcile exactly with
the ids emitted, because a reviewer working through hundreds of records drops
some and a short list looks identical to a complete one.

Usage:
  python3 spot_check.py [--db PATH] [--n 600] [--seed N]
                        [--report PATH.tsv] [--bundle PATH.txt]

  # judgement pass
  python3 spot_check.py --n 40 --review --batch 20      # emit chunks + .ids
  python3 spot_check.py --record verdicts.tsv --against spot_review.001.ids
  python3 spot_check.py --worklist                      # the BAD punch list

Exit code: number of books with hard failures (broken archive, empty spine,
missing file), capped at 99. Recording returns 1 if the verdicts are malformed
or the ids do not reconcile, and writes nothing.
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


# --- review mode -----------------------------------------------------------
#
# The mechanical checks above can only flag shapes. Whether a title is the
# right title, whether an author field holds the person who actually wrote the
# book, and whether a description describes THIS book accurately are judgement
# calls, and nothing in this file can make them. Review mode exists to put
# those three questions in front of a reader (human or LLM) in a form that can
# be answered in bulk and recorded, so the judging accumulates across sessions
# instead of being redone.
#
# Verdicts land in a ledger, reviewed books drop out of future samples, and the
# random sampling that makes a partial pass meaningful is preserved.

VERDICT_FIELDS = ("title", "author", "comment")
LEDGER_HEADER = "id\ttitle\tauthor\tcomment\tnote\treviewed\n"


def wrap(text: str, width: int = 96, indent: str = "  ") -> str:
    out, line = [], ""
    for word in text.split():
        if len(line) + len(word) + 1 > width:
            out.append(indent + line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(indent + line)
    return "\n".join(out) if out else indent + "(empty)"


def load_ledger(path: Path) -> dict[int, dict[str, str]]:
    """Read the verdict ledger. Missing file is an empty ledger, not an error."""
    out: dict[int, dict[str, str]] = {}
    if not path.is_file():
        return out
    with open(path, encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if i == 0 and line.startswith("id\t"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 4 or not parts[0].strip().isdigit():
                continue
            out[int(parts[0])] = {
                "title": parts[1].strip().upper(),
                "author": parts[2].strip().upper(),
                "comment": parts[3].strip().upper(),
                "note": parts[4] if len(parts) > 4 else "",
                "reviewed": parts[5] if len(parts) > 5 else "",
            }
    return out


def parse_verdicts(path: Path) -> tuple[dict[int, dict[str, str]], list[str]]:
    """Read a verdict file: id, title, author, comment, [note]. Tab or comma."""
    out: dict[int, dict[str, str]] = {}
    errors: list[str] = []
    with open(path, encoding="utf-8") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            # maxsplit keeps a free-text note intact even when it contains the
            # delimiter ("wrong author, should be Jane Doe" is one note, not
            # a note plus a silently dropped tail).
            parts = line.split("\t", 4) if "\t" in line else line.split(",", 4)
            parts = [p.strip() for p in parts]
            if parts and parts[0].lower() == "id":
                continue
            if len(parts) < 4:
                errors.append(f"line {lineno}: need id + 3 verdicts, got {len(parts)}")
                continue
            if not parts[0].isdigit():
                errors.append(f"line {lineno}: {parts[0]!r} is not a book id")
                continue
            v = {}
            for field, cell in zip(VERDICT_FIELDS, parts[1:4]):
                c = cell.upper()
                if c not in ("OK", "BAD"):
                    errors.append(f"line {lineno}: {field} is {cell!r}, want OK or BAD")
                v[field] = c
            v["note"] = parts[4] if len(parts) > 4 else ""
            out[int(parts[0])] = v
    return out, errors


def record_verdicts(
    verdict_path: Path, ids_path: Path | None, ledger_path: Path, today: str
) -> int:
    """Validate a verdict file against the emitted id list, then append.

    The id check is the point. A reviewer working through hundreds of records
    silently drops some, and a short list looks exactly like a complete one;
    this has bitten repeatedly on agent output. Nothing is written unless the
    ids reconcile.
    """
    verdicts, errors = parse_verdicts(verdict_path)
    if errors:
        print(f"REFUSED: {len(errors)} malformed line(s):", file=sys.stderr)
        for e in errors[:10]:
            print(f"  {e}", file=sys.stderr)
        return 1

    if ids_path is not None:
        emitted = {int(x) for x in ids_path.read_text().split() if x.strip().isdigit()}
        invented = set(verdicts) - emitted
        missing = emitted - set(verdicts)
        if invented:
            print(
                f"REFUSED: {len(invented)} id(s) not in {ids_path.name}: "
                f"{sorted(invented)[:10]}",
                file=sys.stderr,
            )
            return 1
        if missing:
            print(
                f"REFUSED: {len(missing)} of {len(emitted)} emitted id(s) have no "
                f"verdict: {sorted(missing)[:10]}",
                file=sys.stderr,
            )
            return 1
        print(f"id check: {len(verdicts)} verdicts match {len(emitted)} emitted ids")

    ledger = load_ledger(ledger_path)
    new = {k: v for k, v in verdicts.items() if k not in ledger}
    updated = {k: v for k, v in verdicts.items() if k in ledger}
    ledger.update({k: {**v, "reviewed": today} for k, v in verdicts.items()})

    with open(ledger_path, "w", encoding="utf-8") as fh:
        fh.write(LEDGER_HEADER)
        for bid in sorted(ledger):
            r = ledger[bid]
            fh.write(
                f"{bid}\t{r['title']}\t{r['author']}\t{r['comment']}\t"
                f"{r.get('note', '')}\t{r.get('reviewed', '')}\n"
            )
    bad = sum(1 for r in ledger.values() if any(r[f] == "BAD" for f in VERDICT_FIELDS))
    print(f"recorded {len(new)} new, {len(updated)} updated -> {ledger_path}")
    print(f"ledger now holds {len(ledger)} reviewed book(s); {bad} with a BAD verdict")
    return 0


def format_review_block(row: dict[str, str]) -> str:
    """One book, laid out so all three judgements can be made from the block."""
    flags = f"\nFLAGS   : {row['flags']}" if row["flags"] else ""
    return (
        f"##### {row['id']}\n"
        f"TITLE   : {row['title']}\n"
        f"AUTHORS : {row['authors']}\n"
        f"CONTEXT : {row['tag']} | {row['publisher']} | {row['year']} | "
        f"{row['formats']} | {row['series']}{flags}\n"
        f"COMMENT :\n{wrap(row['comment'])}\n"
    )


def write_review_chunks(
    rows: list[dict[str, str]], prefix: Path, batch: int
) -> list[Path]:
    """Write numbered chunk files plus a matching .ids file for each.

    The .ids file is what --record validates against, so a chunk and its
    verdicts can never drift apart.
    """
    written: list[Path] = []
    batch = max(1, batch)
    for i in range(0, len(rows), batch):
        chunk = rows[i : i + batch]
        num = i // batch + 1
        path = prefix.with_name(f"{prefix.name}.{num:03d}.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(
                f"# {len(chunk)} book(s), chunk {num}. Judge TITLE, AUTHORS and "
                "COMMENT independently.\n"
                "# TITLE   OK unless corrupt, garbled, or the wrong book.\n"
                "# AUTHORS OK unless it holds a non-author (publisher, illustrator,\n"
                "#         translator, front-matter contributor) or omits the real one.\n"
                "# COMMENT OK unless it describes another book, is truncated, is in\n"
                "#         the wrong language, or breaks house style.\n\n"
            )
            fh.write("\n".join(format_review_block(r) for r in chunk))
        path.with_suffix(".ids").write_text(
            "\n".join(str(r["id"]) for r in chunk) + "\n"
        )
        written.append(path)
    return written


def emit_worklist(ledger_path: Path) -> int:
    ledger = load_ledger(ledger_path)
    rows = [
        (bid, f, r.get("note", ""))
        for bid, r in sorted(ledger.items())
        for f in VERDICT_FIELDS
        if r[f] == "BAD"
    ]
    if not rows:
        print(f"{len(ledger)} book(s) reviewed, no BAD verdicts.")
        return 0
    print(f"{len(rows)} defect(s) across {len({r[0] for r in rows})} book(s):\n")
    for bid, field, note in rows:
        print(f"  #{bid:<6} {field.upper():<8} {note}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Randomized metadata and file-integrity spot check."
    )
    ap.add_argument("--db", default="metadata.db")
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--report", default="spot_check_report.tsv")
    ap.add_argument("--bundle", default="spot_check_bundle.txt")
    ap.add_argument(
        "--review",
        action="store_true",
        help="emit full title/author/comment blocks for a judgement pass",
    )
    ap.add_argument(
        "--batch",
        type=int,
        default=100,
        help="books per review chunk file (default 100)",
    )
    ap.add_argument(
        "--review-prefix",
        default="spot_review",
        help="chunk files are <prefix>.001.txt with a matching .001.ids",
    )
    ap.add_argument("--ledger", default="spot_check_verdicts.tsv")
    ap.add_argument(
        "--include-reviewed",
        action="store_true",
        help="do not skip books already in the ledger",
    )
    ap.add_argument("--record", help="ingest a verdict file into the ledger")
    ap.add_argument("--against", help="the .ids file the verdicts answer")
    ap.add_argument(
        "--worklist", action="store_true", help="print BAD verdicts from the ledger"
    )
    args = ap.parse_args()

    ledger_path = Path(args.ledger).expanduser()
    if args.worklist:
        return emit_worklist(ledger_path)
    if args.record:
        if not args.against:
            # The id reconciliation is the load-bearing part of review mode;
            # recording without it would accept exactly the short-but-plausible
            # verdict lists it exists to refuse.
            print("ERROR: --record requires --against <ids file>", file=sys.stderr)
            return 2
        import datetime

        return record_verdicts(
            Path(args.record).expanduser(),
            Path(args.against).expanduser(),
            ledger_path,
            datetime.date.today().isoformat(),
        )

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
    pool = all_ids
    already = 0
    if args.review and not args.include_reviewed:
        reviewed = set(load_ledger(ledger_path))
        pool = [b for b in all_ids if b not in reviewed]
        already = len(all_ids) - len(pool)
        if not pool:
            print(f"every one of {len(all_ids)} books is already in the ledger.")
            return 0
    n = min(args.n, len(pool))
    rng = random.Random(args.seed)
    sample = sorted(rng.sample(pool, n))
    print(
        f"Sampling {n} of {len(pool)} books"
        + (f" ({already} already reviewed, skipped)" if already else "")
        + (f" (seed {args.seed})" if args.seed is not None else "")
    )

    hard_failures = 0
    flagged = 0
    review_rows: list[dict[str, str]] = []
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

            if args.review:
                pub = cur.execute(
                    """SELECT p.name FROM books_publishers_link l
                       JOIN publishers p ON p.id=l.publisher WHERE l.book=?""",
                    (bid,),
                ).fetchone()
                pubdate = cur.execute(
                    "SELECT pubdate FROM books WHERE id=?", (bid,)
                ).fetchone()[0]
                review_rows.append(
                    {
                        "id": bid,
                        "title": title,
                        "authors": "; ".join(authors) or "(none)",
                        "tag": tag or "(untagged)",
                        "publisher": pub[0] if pub else "(none)",
                        "year": (pubdate or "")[:4] or "----",
                        "formats": ", ".join(sorted({r[0] for r in fmts})) or "(none)",
                        "series": series[0] if series else "(standalone)",
                        "comment": plain_text(comment),
                        "flags": ";".join(flags),
                    }
                )

    if args.review:
        written = write_review_chunks(
            review_rows, Path(args.review_prefix).expanduser(), args.batch
        )
        print(f"\nreview: {len(review_rows)} book(s) in {len(written)} chunk(s)")
        for p in written:
            print(f"  {p}  (+ {p.with_suffix('.ids').name})")
        print(
            "\nJudge each block on three axes and write a verdict file with rows\n"
            "  <id>\\t<title OK|BAD>\\t<author OK|BAD>\\t<comment OK|BAD>\\t<note>\n"
            f"then: spot_check.py --record verdicts.tsv --against {written[0].with_suffix('.ids').name}"
        )

    print(f"flagged: {flagged}/{n} ({hard_failures} hard failures)")
    print(f"report: {args.report}\nbundle: {args.bundle}")
    return min(hard_failures, 99)


if __name__ == "__main__":
    sys.exit(main())
