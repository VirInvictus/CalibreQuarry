"""The --fts content search and the index-staleness report (Phase 19 A.1).

Content search reads Calibre's ``full-text-search.db`` sidecar: the
plain ``books_text`` table only, no FTS5 machinery (the index tables
use a custom tokenizer and are unqueryable outside Calibre). The search
itself goes through cquarry's ``search_book_text`` (case- and
accent-folded, restrict-aware) and coverage comes through cquarry's
``get_text_extractions``; the sidecar's ``dirtied_formats`` queue is
the one table cquarry 1.20 does not expose, so it is read directly with
a read-only sqlite connection, confined to this module (recorded A.1
route decision; promoting it to cquarry remains a future option).
"""

import json
import os
import sqlite3
import sys

from cquarry.db import CalibreDB
from cquarry.helpers import C_DIM, C_HEADER, C_WARN, color, db_uri_ro

from cquarry_cli.output import open_output

# Formats Calibre's text extractor can index. Conservative by design: a
# format outside this set is never reported as "never indexed" (the
# false-positive note for the coverage audit).
TEXT_INDEX_FORMATS = {
    "EPUB",
    "PDF",
    "MOBI",
    "AZW3",
    "AZW",
    "DOCX",
    "RTF",
    "TXT",
    "TXTZ",
    "FB2",
    "HTMLZ",
    "LIT",
    "PDB",
    "MD",
    "MARKDOWN",
}


def fts_sidecar_path(db_path: str) -> str:
    return os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "full-text-search.db"
    )


def fts_staleness(db: CalibreDB) -> dict:
    """Coverage classes over the sidecar, per (book, format) pair.

    ``never_indexed``: a text-capable format in the data table with no
    ``books_text`` row. ``indexed_empty``: a row with zero extracted text
    and no error (a genuinely textless document). ``extraction_errors``:
    rows with a non-empty ``err_msg``. ``stale_queued``: pairs in the
    sidecar's ``dirtied_formats`` table, i.e. queued for re-index after
    the file changed (the "stale hash" class). All four are restricted
    by the active view when --restrict is in play.
    """
    wanted: dict[int, set[str]] = {}
    for b in db.get_all_books():
        fmts = {f.upper() for f in (b["formats"] or []) if f}
        text_fmts = fmts & TEXT_INDEX_FORMATS
        if text_fmts:
            wanted[b["id"]] = text_fmts

    indexed: set[tuple[int, str]] = set()
    empty: list[tuple[int, str]] = []
    errors: dict[int, dict[str, str]] = {}
    queued: set[tuple[int, str]] = set()
    for row in db.get_text_extractions():
        fmt = (row["format"] or "").upper()
        pair = (row["book"], fmt)
        indexed.add(pair)
        msg = (row.get("err_msg") or "").strip()
        if msg:
            errors.setdefault(row["book"], {})[fmt] = msg
        elif not row["text_size"]:
            empty.append(pair)
    sidecar = fts_sidecar_path(db.db_path)
    if os.path.exists(sidecar):
        con = sqlite3.connect(db_uri_ro(sidecar), uri=True)
        try:
            try:
                queued = {
                    (book, (fmt or "").upper())
                    for book, fmt in con.execute(
                        "SELECT book, format FROM dirtied_formats"
                    )
                }
            except sqlite3.OperationalError:
                queued = set()
        finally:
            con.close()

    never = sorted(
        (bid, fmt)
        for bid, fmts in wanted.items()
        for fmt in fmts
        if (bid, fmt) not in indexed
    )
    empty = sorted(p for p in empty if p[0] in wanted and p[1] in wanted[p[0]])
    errors = {bid: fmts for bid, fmts in errors.items() if bid in wanted}
    stale = sorted(p for p in queued if p[0] in wanted and p[1] in wanted[p[0]])
    return {
        "sidecar_present": os.path.exists(sidecar),
        "never_indexed": never,
        "indexed_empty": empty,
        "extraction_errors": errors,
        "stale_queued": stale,
    }


def _label(pair: tuple[int, str], titles: dict[int, str]) -> str:
    bid, fmt = pair
    return f"  #{bid} {titles.get(bid, '?')} [{fmt}]"


def print_staleness(db: CalibreDB, report: dict, titles: dict[int, str]) -> None:
    never, empty, stale = (
        report["never_indexed"],
        report["indexed_empty"],
        report["stale_queued"],
    )
    if not report["sidecar_present"]:
        print(
            color(
                "No full-text-search.db sidecar found: Calibre's text index "
                "has never been built in this library.",
                C_WARN,
            )
        )
    if never:
        print(color(f"Never indexed: {len(never)}", C_WARN))
        for pair in never[:15]:
            print(_label(pair, titles))
        if len(never) > 15:
            print(f"  ... and {len(never) - 15} more")
    if empty:
        print(color(f"Indexed empty (no extractable text): {len(empty)}", C_WARN))
        for pair in empty[:15]:
            print(_label(pair, titles))
        if len(empty) > 15:
            print(f"  ... and {len(empty) - 15} more")
    if stale:
        print(
            color(
                f"Stale, queued for re-index (dirtied_formats): {len(stale)}",
                C_WARN,
            )
        )
        for pair in stale[:15]:
            print(_label(pair, titles))
        if len(stale) > 15:
            print(f"  ... and {len(stale) - 15} more")
    if report["extraction_errors"]:
        total = sum(len(fmts) for fmts in report["extraction_errors"].values())
        print(color(f"Extraction errors: {total}", C_WARN))
        for bid, fmts in list(report["extraction_errors"].items())[:10]:
            print(f"  #{bid} {titles.get(bid, '?')} [{', '.join(sorted(fmts))}]")
    if never or empty or stale or report["extraction_errors"]:
        return
    if report["sidecar_present"]:
        print(color("Index coverage looks complete for text-capable formats.", C_DIM))


def run_fts_search(
    db: CalibreDB,
    query: str,
    output: str | None = None,
    *,
    fmt: str | None = None,
    quiet: bool = False,
) -> int:
    """Content search over the sidecar's extracted text. Returns exit code."""
    if not query.strip():
        print("ERROR: --fts needs a non-empty query.", file=sys.stderr)
        return 2
    try:
        matches = db.search_book_text(query)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    books = {b["id"]: b for b in db.get_all_books() if b["id"] in matches}
    rows = []
    for bid in sorted(books):
        b = books[bid]
        rows.append(
            {
                "id": bid,
                "title": b["title"] or "",
                "authors": b["authors"] or [],
                "formats": sorted(matches[bid]),
            }
        )

    if fmt == "json":
        with open_output(output, db.db_path) as (stream, out_path):
            json.dump(rows, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
        if not quiet:
            print(f"Exported {len(rows)} content matches to: {out_path or 'stdout'}")
        return 0

    with open_output(output, db.db_path) as (stream, out_path):
        stream.write(f"FTS Content Search: {query!r} ({len(rows)} books)\n")
        for row in rows:
            author = row["authors"][0] if row["authors"] else "?"
            stream.write(
                f"  #{row['id']} {row['title']} \u2014 {author} "
                f"[{','.join(row['formats'])}]\n"
            )

    if not quiet:
        print(f"{len(rows)} book(s) with matching content.")
        titles = {b["id"]: b["title"] or "" for b in db.get_all_books()}
        print()
        print_staleness(db, fts_staleness(db), titles)
        if out_path:
            print(f"\nFull report: {color(out_path, C_HEADER)}")
    return 0


def run_fts_status(db: CalibreDB, *, quiet: bool = False) -> None:
    """The index-staleness report on its own (the --fts tail, standalone)."""
    titles = {b["id"]: b["title"] or "" for b in db.get_all_books()}
    if not quiet:
        print(color("=== FTS Index Status ===", C_HEADER))
        print()
    print_staleness(db, fts_staleness(db), titles)
    if not quiet:
        print()
        print(
            "Text-capable formats audited: "
            + ", ".join(sorted(TEXT_INDEX_FORMATS)).lower()
        )
        print(
            color(
                "Advisory: the extractor set is conservative; formats outside "
                "it are never reported as never indexed.",
                C_DIM,
            )
        )
