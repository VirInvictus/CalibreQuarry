#!/usr/bin/env python3
"""
export_librarything.py: emit this library as LibraryThing import CSVs.

LibraryThing's importer takes a FIXED template: eleven columns, named exactly
as in their sample file, single quotes and all, and nothing may be added,
removed, or reordered. This writes that template, in UTF-8, with LF endings,
straight from metadata.db (opened mode=ro, never written).

Two things about their importer shape everything here:

  * An ISBN is a lookup key, not data. Given one, LT fetches that edition from
    its own sources and OVERWRITES the bibliographic cells (title, author,
    publication info, page count). It still imports the cells that are yours
    rather than the edition's: tags, rating, review, date read, call number.
    A row with a blank ISBN is taken verbatim instead, which is why the
    non-ISBN books are the ones where page count and publisher actually land.
  * Read/unread is a property of the IMPORT BATCH, not of a row. There is no
    "read" column. So Read books go in their own file, and you assign that
    batch to a collection at import time.

Large imports are the known failure mode: LT's queue sticks, and people report
four-figure imports taking hours. Output is therefore split into batches, so a
stalled upload is one identifiable chunk to retry rather than a mystery about
which of seven thousand books landed.

Data hygiene the importer cares about, all handled here:

  * ISBNs are emitted as ISBN-13. 247 of this library's ISBN-10s begin with a
    zero, and any spreadsheet round-trip silently eats that leading zero,
    turning a valid ISBN into a nine-digit number. Folding to 13 removes the
    hazard entirely and LT resolves ISBN-13 natively.
  * TAGS is comma-separated, so a comma INSIDE a tag would split it in two.
    Translator credits are the only field here that can contain one (a single
    record holds two names in one value); each name becomes its own tag.
  * PAGE COUNT is skipped when the stored value is <= 0. Thirteen books carry
    junk from the Count Pages plugin, one as low as -2. Verified against
    pdfinfo, the positive values are exact for PDFs.
  * REVIEW is left empty on purpose. The curated descriptions are not sent:
    a LibraryThing review is public, and these were written for this catalogue.

Usage:
    python3 export_librarything.py                    # to ~/Downloads
    python3 export_librarything.py --outdir /tmp/lt
    python3 export_librarything.py --batch-size 1000  # fewer, larger uploads
    python3 export_librarything.py --no-call-number   # omit LCC codes

Exit codes:
    0 = files written and self-checked
    1 = a self-check failed (nothing is trustworthy; do not upload)
    2 = setup error (no metadata.db, bad arguments)
"""

import csv
import os
import re
import sys

from cquarry.db import CalibreDB
from cquarry.helpers import to_isbn13

# Byte-exact from LibraryThing's own sample (librarything.com/LibraryThingSample.csv,
# refetched 2026-08-09). Two things are load-bearing and easy to get wrong:
#
#   * the single quotes are LITERAL content, part of each column's name;
#   * a name containing a space or comma is ADDITIONALLY wrapped in double
#     quotes, i.e. real CSV quoting on top of the literal single quotes.
#
# The second is not cosmetic. 'AUTHOR (last, first)' contains a comma, so
# without the double quotes the header parses as TWELVE fields instead of
# eleven, every column after AUTHOR shifts one to the right, and LibraryThing
# reads publisher as the ISBN. The symptom is an import screen cheerfully
# reporting that every book in the file lacks an ISBN.
HEADER = (
    "'TITLE',\"'AUTHOR (last, first)'\",'DATE','ISBN',\"'PUBLICATION INFO'\","
    "'TAGS','RATING','REVIEW',\"'DATE READ'\",\"'PAGE COUNT'\",\"'CALL NUMBER'\""
)
COLUMNS = 11

DEFAULT_BATCH = 500
READ_STATUS = "Read"
SENTINEL_PUBDATE = "0101"


def tag_list(taxonomy: str | None, translators: str | None) -> str:
    """One taxonomy tag plus a translator: tag per named translator.

    LT splits TAGS on commas, so a name pair stored in a single custom-column
    value ("Salma Khadra Jayyusi, Trevor Le Gassick") has to be split here or
    it arrives as one real tag and one orphaned surname.
    """
    tags = [t for t in (taxonomy or "").split("|") if t.strip()]
    for value in (translators or "").split("|"):
        for name in value.split(","):
            name = name.strip()
            if name:
                tags.append(f"translator:{name}")
    return ", ".join(tags)


def build_rows(
    db: CalibreDB, want_call_number: bool, matching_ids: set[int] | None = None
) -> tuple[list, list]:
    cols = db.get_custom_columns()

    def _col_sql(name: str, is_mult: bool) -> str:
        c = cols.get(name)
        if not c:
            return "NULL"
        cid = c["id"]
        if is_mult:
            return f"(SELECT GROUP_CONCAT(v.value, '|') FROM books_custom_column_{cid}_link l JOIN custom_column_{cid} v ON v.id=l.value WHERE l.book=b.id)"
        if c["datatype"] in ("text", "enumeration", "series"):
            return f"(SELECT v.value FROM books_custom_column_{cid}_link l JOIN custom_column_{cid} v ON v.id=l.value WHERE l.book=b.id)"
        return f"(SELECT value FROM custom_column_{cid} WHERE book=b.id)"

    trans_sql = _col_sql("#translators", True)
    status_sql = _col_sql("#reading_status", False)  # Fallback to #status if not found
    if status_sql == "NULL":
        status_sql = _col_sql("#status", False)
    date_sql = _col_sql("#date_read", False)

    query = f"""
            SELECT b.id, b.title, b.author_sort, b.pubdate,
                   (SELECT val FROM identifiers WHERE book=b.id AND type='isbn'),
                   (SELECT p.name FROM books_publishers_link pl
                     JOIN publishers p ON p.id=pl.publisher WHERE pl.book=b.id),
                   (SELECT GROUP_CONCAT(t.name, '|') FROM books_tags_link tl
                     JOIN tags t ON t.id=tl.tag WHERE tl.book=b.id),
                   (SELECT r.rating FROM books_ratings_link rl
                     JOIN ratings r ON r.id=rl.rating WHERE rl.book=b.id),
                   {trans_sql},
                   {status_sql},
                   {date_sql},
                   (SELECT val FROM identifiers WHERE book=b.id AND type='lcc'),
                   (SELECT pages FROM books_pages_link WHERE book=b.id)
              FROM books b ORDER BY b.author_sort, b.title
    """
    records = db.conn.execute(query).fetchall()

    read, main = [], []
    for (
        bid,
        title,
        author_sort,
        pubdate,
        isbn,
        publisher,
        tags,
        rating,
        translators,
        status,
        date_read,
        lcc,
        pages,
    ) in records:
        year = ""
        if pubdate and not pubdate.startswith(SENTINEL_PUBDATE):
            year = pubdate[:4].lstrip("0")
        row = [
            title or "",
            author_sort or "",
            year,
            # cquarry 1.8's to_isbn13 returns None for garbage; the CSV
            # column stays "" exactly as before.
            to_isbn13(isbn) or "",
            publisher or "",
            tag_list(tags, translators),
            str(rating // 2) if rating else "",
            "",  # REVIEW: deliberately empty
            date_read[:10] if date_read else "",
            str(pages) if pages and pages > 0 else "",
            (lcc or "") if want_call_number else "",
        ]
        if matching_ids is None or bid in matching_ids:
            (read if status == READ_STATUS else main).append(row)
    return read, main


def write_batches(rows: list, outdir: str, stem: str, size: int) -> list[str]:
    written = []
    total = max(1, -(-len(rows) // size))
    for index in range(total):
        chunk = rows[index * size : (index + 1) * size]
        if not chunk and index:
            break
        name = f"{stem}.csv" if total == 1 else f"{stem}_{index + 1:02d}.csv"
        path = os.path.join(outdir, name)
        with open(path, "w", newline="", encoding="utf-8") as handle:
            handle.write(HEADER + "\n")
            csv.writer(handle, lineterminator="\n").writerows(chunk)
        written.append(path)
    return written


def self_check(paths: list[str], expected_rows: int) -> list[str]:
    """Refuse to hand over files that would fail at LT's end."""
    problems, seen = [], 0
    for path in paths:
        with open(path, "rb") as binary:
            raw = binary.read()
        if raw.startswith(b"\xef\xbb\xbf"):
            problems.append(f"{os.path.basename(path)}: UTF-8 BOM present")
        if b"\r\n" in raw:
            problems.append(f"{os.path.basename(path)}: CRLF line endings")
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"{os.path.basename(path)}: not valid UTF-8 ({exc})")
        with open(path, encoding="utf-8", newline="") as handle:
            header = handle.readline().rstrip("\n")
            if header != HEADER:
                problems.append(f"{os.path.basename(path)}: header not byte-exact")
            # The header must PARSE to eleven fields, not merely look right.
            # A comma lives inside 'AUTHOR (last, first)', so an unquoted
            # header splits into twelve, shifting every later column right and
            # making LT read the publisher as the ISBN.
            parsed = next(csv.reader([header]), [])
            if len(parsed) != COLUMNS:
                problems.append(
                    f"{os.path.basename(path)}: header parses to {len(parsed)} "
                    f"fields, not {COLUMNS} (column alignment would be wrong)"
                )
            elif parsed[3] != "'ISBN'":
                problems.append(
                    f"{os.path.basename(path)}: column 4 is {parsed[3]!r}, not 'ISBN'"
                )
            rows = list(csv.reader(handle))
        seen += len(rows)
        for number, row in enumerate(rows, start=2):
            if len(row) != COLUMNS:
                problems.append(
                    f"{os.path.basename(path)}:{number}: {len(row)} columns"
                )
                break
            isbn = row[3]
            if isbn and not (len(isbn) == 13 and isbn.isdigit()):
                problems.append(f"{os.path.basename(path)}:{number}: bad ISBN {isbn!r}")
                break
            if not row[0].strip():
                problems.append(f"{os.path.basename(path)}:{number}: empty title")
                break
    if seen != expected_rows:
        problems.append(f"row count {seen} != expected {expected_rows}")
    return problems


def run_librarything_export(
    db: CalibreDB,
    outdir: str,
    matching_ids: set[int] | None = None,
    batch_size: int = DEFAULT_BATCH,
    want_call_number: bool = True,
    quiet: bool = False,
) -> int:
    os.makedirs(outdir, exist_ok=True)

    for stale in os.listdir(outdir):
        if re.match(r"librarything[_ ](read|main).*\.csv$", stale, re.IGNORECASE):
            os.remove(os.path.join(outdir, stale))

    read, main_rows = build_rows(db, want_call_number, matching_ids)
    if not read and not main_rows:
        print("No books to export.", file=sys.stderr)
        return 0

    read_paths = write_batches(read, outdir, "librarything_read", batch_size)
    main_paths = write_batches(main_rows, outdir, "librarything_main", batch_size)

    problems = self_check(read_paths + main_paths, len(read) + len(main_rows))
    if problems:
        print("SELF-CHECK FAILED, do not upload:", file=sys.stderr)
        for problem in problems:
            print("  " + problem, file=sys.stderr)
        return 1

    if not quiet:
        with_isbn = sum(1 for r in read + main_rows if r[3])
        with_call = sum(1 for r in read + main_rows if r[10])
        with_pages = sum(1 for r in read + main_rows if r[9])
        print(f"{len(read) + len(main_rows)} books written to {outdir}")
        print(f"  {len(read):5} read  -> {len(read_paths)} file(s)")
        print(f"  {len(main_rows):5} rest  -> {len(main_paths)} file(s)")
        print(
            f"  {with_isbn} with ISBN, {with_pages} with page count, "
            f"{with_call} with call number"
        )
        print("\nUpload order (Universal Import > CSV at librarything.com/import):")
        for path in read_paths:
            print(
                f"  {os.path.basename(path)}   <- assign these to a 'Read' collection"
            )
        for path in main_paths:
            print(f"  {os.path.basename(path)}")
        print(
            "\nWait for each batch to finish before starting the next; LT's import "
            "queue is single-file and known to stall when overlapped."
        )
    return 0
