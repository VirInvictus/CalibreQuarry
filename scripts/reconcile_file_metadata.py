#!/usr/bin/env python3
"""
reconcile_file_metadata.py: compare a Calibre library's curated database
metadata against the metadata actually embedded in each book file, and
optionally push the database values into the files so the two agree.

The database is the source of truth. Calibre's `metadata.db` is where you curate
titles, authors, series, tags, publishers, dates, identifiers, and blurbs; the
copy embedded inside the EPUB/MOBI/AZW3/PDF/DJVU file is what travels with the
book when it leaves the library (a different reader, a phone, a backup). Those
two drift apart whenever you edit metadata in Calibre without re-exporting the
file. This script finds that drift and, with --apply, closes it. It never reads
file metadata back into the database; the flow is always database -> file.

What it does:
  * Reads the database read-only (handles a locked DB by copying it, like the
    cquarry package).
  * Reads each file's embedded metadata with `ebook-meta` (EPUB/MOBI/AZW3/PDF)
    or `djvused` (DJVU).
  * Diffs a per-format set of fields (see FORMAT_FIELDS) and reports, per book,
    which fields differ. Fields a format cannot reliably carry are not compared,
    to keep the noise down (a PDF is not faulted for lacking your tag tree).

With --apply (only the drifted books are touched):
  * EPUB/MOBI/AZW3: `calibredb embed_metadata`, which writes the full record
    (and the cover) straight from the database.
  * PDF: `exiftool` writes title/author/publisher/date to the Info dict and
    XMP. calibredb is skipped for PDF because it silently leaves some PDFs
    unchanged; exiftool wrote every PDF tested.
  * DJVU: `djvused` sets the title and author (all DJVU's flat metadata holds);
    Calibre cannot embed DJVU.

Usage:
    python3 reconcile_file_metadata.py                 # dry-run report, ./metadata.db
    python3 reconcile_file_metadata.py ~/Calibre       # a library directory
    python3 reconcile_file_metadata.py --sample 50     # random 50 books (quick look)
    python3 reconcile_file_metadata.py --id 6688,6690  # specific books
    python3 reconcile_file_metadata.py --format epub   # only EPUB files
    python3 reconcile_file_metadata.py --apply          # embed DB metadata into drifted files
    python3 reconcile_file_metadata.py --apply --force  # skip the "Calibre is running" guard

Reading every file spawns a subprocess per file, so a full-library dry run is
slow (tens of minutes for thousands of books). Scope with --sample / --id /
--format for a quick look; run unscoped once as a one-off reconcile.

Exit codes:
    0 = no drift found (or --apply completed with no failures)
    1 = drift found (dry run), or one or more apply/embed operations failed
    2 = setup error (metadata.db or a required external tool not found)

Stdlib only; shells out to `calibredb`, `ebook-meta`, `djvused`, and (for PDF
writes) `exiftool`. All but exiftool ship with Calibre / djvulibre; `--apply`
checks for what it needs and exits 2 if a tool is missing.
"""

import argparse
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
CYAN = "\033[36m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

# Fields compared per format: only those the format reliably stores and
# `ebook-meta`/`djvused` reliably reports, so we do not flag a format for
# lacking something it was never going to hold.
ALL_FIELDS = (
    "title",
    "authors",
    "series",
    "publisher",
    "pubdate",
    "languages",
    "tags",
    "identifiers",
    "comments",
)
FORMAT_FIELDS: dict[str, tuple[str, ...]] = {
    "EPUB": ALL_FIELDS,
    "AZW3": ALL_FIELDS,
    "MOBI": (
        "title",
        "authors",
        "publisher",
        "pubdate",
        "languages",
        "tags",
        "identifiers",
        "comments",
    ),
    # PDF carries title/author in its Info dict and publisher/date in XMP;
    # calibredb embed_metadata writes all four and ebook-meta reads them back.
    # Tags/series/comments/identifiers do not reliably round-trip, so they are
    # not compared for PDF.
    "PDF": ("title", "authors", "publisher", "pubdate"),
    "DJVU": ("title", "authors"),
}
# Each format's writer on --apply. EPUB/MOBI/AZW3 go through calibredb (it
# embeds the full record and the cover). PDF goes through exiftool instead:
# calibredb writes PDF metadata only ~80% of the time (some PDFs silently keep
# their old title/author), whereas exiftool writes the Info dict + XMP on every
# PDF tested. DJVU goes through djvused, which Calibre cannot drive.
CALIBREDB_FORMATS = {"EPUB", "AZW3", "MOBI"}
EXIFTOOL_FORMATS = {"PDF"}
DJVUSED_FORMATS = {"DJVU"}


# --- normalisation ---------------------------------------------------------


def norm_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def norm_set(values) -> frozenset[str]:
    return frozenset(norm_text(v).lower() for v in values if norm_text(v))


def norm_comment(html: str | None) -> str:
    """Compare blurbs by their visible text, ignoring HTML/whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip().lower()


def norm_date(value: str | None) -> str:
    """Reduce a date/timestamp to YYYY-MM-DD; '' for the sentinel/empty."""
    s = norm_text(value)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", s)
    if not m:
        return ""
    d = m.group(1)
    return "" if d.startswith("0101-01-01") else d


def parse_series(value: str | None) -> tuple[str, str]:
    """'Card Mage #1' -> ('card mage', '1'); index normalised to drop '.0'."""
    s = norm_text(value)
    if not s:
        return "", ""
    m = re.match(r"^(.*?)(?:\s+#\s*([\d.]+))?$", s)
    if not m:
        return s.lower(), ""
    name = (m.group(1) or "").strip().lower()
    idx = (m.group(2) or "").strip()
    if idx.endswith(".0"):
        idx = idx[:-2]
    return name, idx


def parse_identifiers(value: str | None) -> dict[str, str]:
    """'isbn:9780..., amazon:B0..' -> {'isbn': '9780...', 'amazon': 'b0..'}."""
    out: dict[str, str] = {}
    for part in re.split(r"[,\s]+", norm_text(value)):
        if ":" in part:
            k, _, v = part.partition(":")
            if k and v:
                out[k.strip().lower()] = v.strip().lower()
    return out


# --- database side ---------------------------------------------------------


def connect_ro(db_path: Path) -> tuple[sqlite3.Connection, str | None]:
    """Open read-only; if Calibre holds the lock, read a temp copy (returning
    the temp dir for cleanup). Mirrors scripts/validate_metadata.py."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM books LIMIT 1")
        return con, None
    except sqlite3.OperationalError:
        con.close()
    tmpdir = tempfile.mkdtemp(prefix="cquarry-reconcile-")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, Path(tmpdir) / ("metadata.db" + suffix))
    con = sqlite3.connect(f"file:{Path(tmpdir) / 'metadata.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con, tmpdir


def db_record(cur, book_id: int, library_root: Path) -> dict:
    """Curated metadata for one book, plus its on-disk format files."""
    b = cur.execute(
        "SELECT title, pubdate, series_index, path FROM books WHERE id=?", (book_id,)
    ).fetchone()
    authors = [
        r["name"]
        for r in cur.execute(
            "SELECT a.name FROM books_authors_link al JOIN authors a ON a.id=al.author "
            "WHERE al.book=? ORDER BY al.id",
            (book_id,),
        )
    ]
    tags = [
        r["name"]
        for r in cur.execute(
            "SELECT t.name FROM books_tags_link bl JOIN tags t ON t.id=bl.tag WHERE bl.book=?",
            (book_id,),
        )
    ]
    langs = [
        r["lang_code"]
        for r in cur.execute(
            "SELECT l.lang_code FROM books_languages_link bl "
            "JOIN languages l ON l.id=bl.lang_code WHERE bl.book=? ORDER BY bl.item_order",
            (book_id,),
        )
    ]
    pub = cur.execute(
        "SELECT p.name FROM books_publishers_link pl JOIN publishers p ON p.id=pl.publisher "
        "WHERE pl.book=?",
        (book_id,),
    ).fetchone()
    ser = cur.execute(
        "SELECT s.name FROM books_series_link bl JOIN series s ON s.id=bl.series WHERE bl.book=?",
        (book_id,),
    ).fetchone()
    idents = {
        r["type"].lower(): r["val"].strip().lower()
        for r in cur.execute(
            "SELECT type, val FROM identifiers WHERE book=?", (book_id,)
        )
    }
    com = cur.execute("SELECT text FROM comments WHERE book=?", (book_id,)).fetchone()
    series_name = ser["name"] if ser else ""
    idx = b["series_index"]
    series_idx = ""
    if series_name and idx is not None:
        series_idx = str(int(idx)) if float(idx).is_integer() else str(idx)
    formats = {}
    for r in cur.execute("SELECT format, name FROM data WHERE book=?", (book_id,)):
        formats[r["format"].upper()] = (
            library_root / b["path"] / f"{r['name']}.{r['format'].lower()}"
        )
    return {
        "id": book_id,
        "title": b["title"],
        "authors": authors,
        "series": series_name,
        "series_index": series_idx,
        "publisher": pub["name"] if pub else "",
        "pubdate": b["pubdate"],
        "languages": langs,
        "tags": tags,
        "identifiers": idents,
        "comments": com["text"] if com else "",
        "formats": formats,
    }


# --- file side -------------------------------------------------------------

_EBOOK_META_LINE = re.compile(r"^([A-Za-z][A-Za-z()/ ]*?)\s*:\s?(.*)$")


def read_ebook_meta(path: Path) -> dict[str, str] | None:
    """Parse `ebook-meta <file>` into a flat field dict. None on failure."""
    try:
        out = subprocess.run(
            ["ebook-meta", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    fields: dict[str, str] = {}
    current: str | None = None
    for line in out.stdout.splitlines():
        m = _EBOOK_META_LINE.match(line)
        if m and not line.startswith(" "):
            key = str(m.group(1)).strip().lower()
            current = key
            fields[key] = str(m.group(2)).strip()
        elif current == "comments":  # comments wrap across lines
            fields["comments"] += " " + line.strip()
    return fields


def read_djvu_meta(path: Path) -> dict[str, str] | None:
    """Read DJVU metadata via `djvused -e 'print-meta'` (key "value" lines)."""
    try:
        out = subprocess.run(
            ["djvused", str(path), "-e", "print-meta"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        m = re.match(r'^(\w+)\s+"?(.*?)"?\s*$', line.strip())
        if m:
            fields[m.group(1).lower()] = m.group(2)
    return fields


def file_metadata(path: Path, fmt: str) -> dict[str, str] | None:
    if fmt in DJVUSED_FORMATS:
        return read_djvu_meta(path)
    return read_ebook_meta(path)


# --- diff ------------------------------------------------------------------


def diff_fields(db: dict, fm: dict[str, str], fmt: str) -> list[str]:
    """Return the names of fields that differ for this format."""
    compare = FORMAT_FIELDS.get(fmt, ())
    drift: list[str] = []
    # ebook-meta keys: title, author(s), series, publisher, published, languages,
    # tags, identifiers, comments. djvused keys: title, author.
    for field in compare:
        if field == "title":
            if norm_text(db["title"]).lower() != norm_text(fm.get("title")).lower():
                drift.append("title")
        elif field == "authors":
            file_auth = fm.get("author(s)") or fm.get("author") or ""
            file_auth = re.sub(r"\s*\[[^\]]*\]", "", file_auth)  # drop "[sort]"
            fset = norm_set(re.split(r"\s*[&;]\s*|,\s*", file_auth))
            if norm_set(db["authors"]) != fset:
                drift.append("authors")
        elif field == "series":
            db_s = (norm_text(db["series"]).lower(), db["series_index"])
            fm_s = parse_series(fm.get("series"))
            if db_s != fm_s:
                drift.append("series")
        elif field == "publisher":
            if (
                norm_text(db["publisher"]).lower()
                != norm_text(fm.get("publisher")).lower()
            ):
                drift.append("publisher")
        elif field == "pubdate":
            if norm_date(db["pubdate"]) != norm_date(fm.get("published")):
                drift.append("pubdate")
        elif field == "languages":
            if norm_set(db["languages"]) != norm_set(
                re.split(r"[,\s]+", fm.get("languages", ""))
            ):
                drift.append("languages")
        elif field == "tags":
            if norm_set(db["tags"]) != norm_set(re.split(r",\s*", fm.get("tags", ""))):
                drift.append("tags")
        elif field == "identifiers":
            if db["identifiers"] != parse_identifiers(fm.get("identifiers")):
                drift.append("identifiers")
        elif field == "comments":
            if norm_comment(db["comments"]) != norm_comment(fm.get("comments")):
                drift.append("comments")
    return drift


# --- apply -----------------------------------------------------------------


def calibre_running() -> bool:
    try:
        return (
            subprocess.run(["pgrep", "-x", "calibre"], capture_output=True).returncode
            == 0
        )
    except OSError:
        return False


def embed_calibredb(ids: list[int], library_root: Path) -> bool:
    """`calibredb embed_metadata` for the given ids, in chunks. True on success."""
    ok = True
    for i in range(0, len(ids), 100):
        chunk = [str(x) for x in ids[i : i + 100]]
        cmd = [
            "calibredb",
            "embed_metadata",
            "--library-path",
            str(library_root),
            *chunk,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(
                f"  {RED}embed_metadata failed{RESET}: {res.stderr.strip()[:200]}",
                file=sys.stderr,
            )
            ok = False
    return ok


def embed_djvu(db: dict, path: Path) -> bool:
    """Set DJVU title/author via djvused (the only fields DJVU metadata holds)."""

    def esc(v: str) -> str:
        return norm_text(v).replace("\\", "\\\\").replace('"', '\\"')

    lines = [f'Title\t"{esc(db["title"])}"']
    if db["authors"]:
        lines.append(f'Author\t"{esc(" & ".join(db["authors"]))}"')
    with tempfile.NamedTemporaryFile("w", suffix=".meta", delete=False) as tf:
        tf.write("\n".join(lines) + "\n")
        meta_file = tf.name
    try:
        res = subprocess.run(
            ["djvused", str(path), "-e", f'set-meta "{meta_file}"', "-s"],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print(
                f"  {RED}djvused failed{RESET} on {path.name}: {res.stderr.strip()[:160]}",
                file=sys.stderr,
            )
            return False
        return True
    finally:
        os.unlink(meta_file)


def embed_pdf(db: dict, path: Path) -> bool:
    """Write PDF metadata with exiftool (Info dict + XMP). More reliable than
    calibredb, which silently leaves some PDFs unchanged. Authors are joined
    with ' & ' to match Calibre's display so the diff round-trips; the single
    genre tag is written as a keyword for completeness (not compared)."""
    authors = " & ".join(db["authors"])
    args = [
        "exiftool",
        "-overwrite_original",
        "-q",
        f"-Title={norm_text(db['title'])}",
        f"-XMP-dc:Title={norm_text(db['title'])}",
        f"-Author={authors}",
        f"-XMP-dc:Creator={authors}",
    ]
    if db["publisher"]:
        args.append(f"-XMP-dc:Publisher={norm_text(db['publisher'])}")
    date = norm_date(db["pubdate"])
    if date:
        args.append(f"-XMP-dc:Date={date}")
    if db["tags"]:
        args.append(f"-Keywords={'; '.join(db['tags'])}")
        args.append(f"-XMP-dc:Subject={'; '.join(db['tags'])}")
    args.append(str(path))
    res = subprocess.run(args, capture_output=True, text=True)
    if res.returncode != 0:
        print(
            f"  {RED}exiftool failed{RESET} on {path.name}: {res.stderr.strip()[:160]}",
            file=sys.stderr,
        )
        return False
    return True


# --- driver ----------------------------------------------------------------


def resolve_db_path(arg: str | None) -> Path | None:
    candidates: list[Path] = []
    if arg:
        p = Path(arg).expanduser()
        candidates.append(p if p.suffix == ".db" else p / "metadata.db")
    else:
        candidates.append(Path.cwd() / "metadata.db")
    for c in candidates:
        if c.exists():
            return c
    return None


def select_ids(cur, args) -> list[int]:
    if args.id:
        wanted = {int(x) for x in re.split(r"[,\s]+", args.id) if x.strip()}
        return [
            r[0]
            for r in cur.execute("SELECT id FROM books ORDER BY id")
            if r[0] in wanted
        ]
    ids = [r[0] for r in cur.execute("SELECT id FROM books ORDER BY id")]
    if args.sample and args.sample < len(ids):
        random.seed(args.seed)
        ids = sorted(random.sample(ids, args.sample))
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile Calibre database metadata with the metadata embedded in book files.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="library directory or metadata.db (default: ./metadata.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="embed DB metadata into drifted files (default: dry run)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="with --apply, proceed even if Calibre is running",
    )
    parser.add_argument(
        "--format", help="restrict to one format (epub, pdf, mobi, azw3, djvu)"
    )
    parser.add_argument("--id", help="comma-separated book ids to check")
    parser.add_argument(
        "--sample", type=int, help="check a random N books (quick look)"
    )
    parser.add_argument(
        "--seed", type=int, default=20260607, help="random seed for --sample"
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only drift, truncate long field lists",
    )
    args = parser.parse_args()

    for tool in ("ebook-meta", "djvused"):
        if shutil.which(tool) is None:
            print(f"ERROR: required tool '{tool}' not found on PATH.", file=sys.stderr)
            return 2
    if args.apply:
        for tool in ("calibredb", "exiftool"):
            if shutil.which(tool) is None:
                print(f"ERROR: --apply needs '{tool}' on PATH.", file=sys.stderr)
                return 2

    db_path = resolve_db_path(args.path)
    if db_path is None:
        print(
            f"ERROR: no metadata.db found at {args.path or 'the current directory'}.",
            file=sys.stderr,
        )
        return 2
    library_root = db_path.parent
    fmt_filter = args.format.upper() if args.format else None

    if args.apply and not args.force and calibre_running():
        print(
            f"{RED}REFUSING --apply{RESET}: Calibre is running. Close it, or pass --force.",
            file=sys.stderr,
        )
        return 2

    con, tmpdir = connect_ro(db_path)
    try:
        cur = con.cursor()
        ids = select_ids(cur, args)
        records = [db_record(cur, i, library_root) for i in ids]
    finally:
        con.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    drifted_calibredb: list[int] = []
    drifted_pdf: list[tuple[dict, Path]] = []
    drifted_djvu: list[tuple[dict, Path]] = []
    n_checked = n_insync = n_missing = 0
    drift_rows: list[tuple[int, str, str, list[str]]] = []

    for rec in records:
        for fmt, fpath in rec["formats"].items():
            if fmt_filter and fmt != fmt_filter:
                continue
            n_checked += 1
            if not fpath.exists():
                n_missing += 1
                continue
            fm = file_metadata(fpath, fmt)
            if fm is None:
                n_missing += 1
                continue
            drift = diff_fields(rec, fm, fmt)
            if not drift:
                n_insync += 1
                continue
            drift_rows.append((rec["id"], rec["title"], fmt, drift))
            if fmt in CALIBREDB_FORMATS:
                drifted_calibredb.append(rec["id"])
            elif fmt in EXIFTOOL_FORMATS:
                drifted_pdf.append((rec, fpath))
            elif fmt in DJVUSED_FORMATS:
                drifted_djvu.append((rec, fpath))

    # report
    if not args.quiet:
        scope = f"{len(records)} book(s)"
        if args.sample:
            scope += f" (random sample, seed {args.seed})"
        print(f"{BOLD}Reconciling{RESET} {db_path}  [{scope}]")
    n_drift = len(drift_rows)
    if n_drift:
        print(f"\n{BOLD}DRIFT{RESET} ({n_drift} file(s))")
        shown = drift_rows if not args.quiet else drift_rows[:40]
        for bid, title, fmt, drift in shown:
            print(f"  {CYAN}#{bid}{RESET} [{fmt}] {title[:48]}")
            print(f"      differs: {', '.join(drift)}")
        if args.quiet and n_drift > len(shown):
            print(f"  ... and {n_drift - len(shown)} more")
    print(
        f"\nchecked {n_checked} file(s): {GREEN}{n_insync} in sync{RESET}, "
        f"{YELLOW}{n_drift} drifted{RESET}, {n_missing} unreadable/missing."
    )

    if not args.apply:
        if n_drift:
            print(
                f"\nRun again with {BOLD}--apply{RESET} to embed the database metadata into the drifted files."
            )
            return 1
        return 0

    # apply (only drifted)
    print(f"\n{BOLD}Applying{RESET} (database -> file)...")
    ok = True
    unique_ids = sorted(set(drifted_calibredb))
    if unique_ids:
        print(f"  embedding {len(unique_ids)} book(s) via calibredb...")
        ok = embed_calibredb(unique_ids, library_root) and ok
    if drifted_pdf:
        print(f"  embedding {len(drifted_pdf)} PDF(s) via exiftool...")
        for rec, fpath in drifted_pdf:
            ok = embed_pdf(rec, fpath) and ok
    for rec, fpath in drifted_djvu:
        print(f"  djvused #{rec['id']} {fpath.name}")
        ok = embed_djvu(rec, fpath) and ok
    if ok:
        print(
            f"\n{GREEN}DONE{RESET}: embedded {len(unique_ids)} via calibredb, "
            f"{len(drifted_pdf)} PDF via exiftool, {len(drifted_djvu)} DJVU via djvused."
        )
        return 0
    print(f"\n{RED}COMPLETED WITH ERRORS{RESET}: some operations failed (see above).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
