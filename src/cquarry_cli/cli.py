import argparse
import sys

from cquarry.db import CalibreDB
from cquarry.helpers import find_db

from cquarry_cli import VERSION
from cquarry_cli.modes.analytics import (
    show_author_stats,
    show_pace_stats,
    show_tag_tree,
    show_wing_overlap,
)
from cquarry_cli.modes.audit import run_audit
from cquarry_cli.modes.catalog import write_all_wings, write_catalog
from cquarry_cli.modes.display import show_recent, show_series, show_wings
from cquarry_cli.modes.export import (
    run_annotations_export,
    run_export,
    run_search_export,
)
from cquarry_cli.modes.librarything import run_librarything_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump
from cquarry_cli.tui import interactive_menu


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cquarry",
        description="Calibre library toolkit: catalog, stats, audit, export",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    group = p.add_mutually_exclusive_group()
    group.add_argument("--catalog", action="store_true", help="Build a text catalog")
    group.add_argument(
        "--all-wings",
        dest="all_wings",
        action="store_true",
        help="Generate catalogs for all virtual libraries",
    )
    group.add_argument("--stats", action="store_true", help="Show library statistics")
    group.add_argument(
        "--analytics",
        choices=["author", "pace", "tags", "overlap"],
        default=None,
        help="Extended analytics and visualizations",
    )
    group.add_argument(
        "--audit",
        action="store_true",
        help="Report issues (untagged, unrated, series gaps)",
    )
    group.add_argument(
        "--recent",
        type=int,
        nargs="?",
        const=20,
        default=None,
        help="Show N most recently added books (default: 20)",
    )
    group.add_argument(
        "--series",
        action="store_true",
        help="List all series with completeness and gap detection",
    )
    group.add_argument(
        "--export",
        action="store_true",
        help="Export library to JSON, CSV, or AI format",
    )
    group.add_argument(
        "--search",
        default=None,
        metavar="QUERY",
        help="Show/export books matching a Calibre search expression "
        "(prints to stdout unless --output is given; empty query = whole "
        "library). Supports custom grouped-search terms (GroupName:query) "
        "and annotations: full-text over e-reader highlights",
    )
    group.add_argument(
        "--wings", action="store_true", help="List all virtual library wings"
    )
    group.add_argument(
        "--tags", action="store_true", help="Dump every tag with its book count"
    )

    p.add_argument(
        "--exportlt",
        action="store_true",
        help="Export to LibraryThing CSV format (can be used alone or with --search)",
    )

    p.add_argument(
        "--export-annotations",
        dest="export_annotations",
        action="store_true",
        help="Dump e-reader highlights/bookmarks/notes as JSON "
        "(optionally scoped with --id)",
    )

    p.add_argument(
        "--id",
        dest="book_id",
        type=int,
        default=None,
        metavar="BOOK_ID",
        help="Scope --export-annotations to a single Calibre book id",
    )

    p.add_argument(
        "--plugin-data",
        dest="plugin_data",
        default=None,
        metavar="NAME",
        help="With --catalog or --search: append a books_plugin_data value "
        "(e.g. goodreads_id, wordcount) to each book line",
    )

    p.add_argument(
        "--db",
        default=None,
        help="Path to Calibre metadata.db (auto-detected if omitted)",
    )
    p.add_argument(
        "--wing", default=None, help="Filter to a specific virtual library wing"
    )
    p.add_argument("--output", default=None, help="Output file path")
    p.add_argument(
        "--outdir",
        default=None,
        help="Output directory for --all-wings (default: current dir)",
    )
    p.add_argument(
        "--format",
        choices=["json", "csv", "ai"],
        default=None,
        help="Output format. --export defaults to json; --search defaults to a "
        "plain-text listing unless a format is given here",
    )
    p.add_argument(
        "--primary-only",
        dest="primary_only",
        action="store_true",
        help="Use only the first author (useful for TTRPG collections)",
    )
    p.add_argument(
        "--show-tags",
        dest="show_tags",
        action="store_true",
        help="Show tags instead of ratings in catalog output",
    )
    p.add_argument(
        "--show-id",
        dest="show_id",
        action="store_true",
        help="Prefix each book with its Calibre ID for scripting",
    )
    p.add_argument(
        "--show-custom",
        dest="show_custom",
        default=None,
        metavar="COL_NAME",
        help="Load and display a specific custom column",
    )
    p.add_argument(
        "--show-author-details",
        dest="show_author_details",
        action="store_true",
        help="With --catalog/--all-wings/--export/--search: append each "
        "author's true sort key and link URL (from cquarry's entity "
        "secondary columns) to the output",
    )

    p.add_argument("--quiet", action="store_true", help="Minimize output")

    # --- Write verbs (opt-in; each goes through cquarry.write) ---
    w = p.add_argument_group("write verbs (Calibre must be closed)")
    w.add_argument(
        "--set-title",
        dest="set_title",
        nargs=2,
        metavar=("BOOK_ID", "TITLE"),
        default=None,
        help="Rename a book",
    )
    w.add_argument(
        "--set-authors",
        dest="set_authors",
        nargs=2,
        metavar=("BOOK_ID", "NAMES"),
        default=None,
        help='Replace authors ("Name One; Name Two"; ; = separator)',
    )
    w.add_argument(
        "--set-rating",
        dest="set_rating",
        nargs=2,
        metavar=("BOOK_ID", "STARS"),
        default=None,
        help="Set rating (0-5, halves allowed)",
    )
    w.add_argument(
        "--set-comments",
        dest="set_comments",
        nargs=2,
        metavar=("BOOK_ID", "HTML"),
        default=None,
        help="Set the comments/description HTML",
    )
    w.add_argument(
        "--clear-comments",
        dest="clear_comments",
        metavar="BOOK_ID",
        default=None,
        help="Clear the comments/description",
    )
    w.add_argument(
        "--set-column",
        dest="set_column",
        nargs=3,
        metavar=("BOOK_ID", "LABEL", "VALUE"),
        default=None,
        help="Write a custom-column value (#label; enumerations are "
        "validated against the column's configured values)",
    )
    w.add_argument(
        "--clear-column",
        dest="clear_column",
        nargs=2,
        metavar=("BOOK_ID", "LABEL"),
        default=None,
        help="Clear a custom-column value",
    )
    w.add_argument(
        "--remove-book",
        dest="remove_book",
        metavar="BOOK_ID",
        default=None,
        help="Permanently remove a book (dry run unless --confirm-remove)",
    )
    w.add_argument(
        "--confirm-remove",
        dest="confirm_remove",
        action="store_true",
        help="With --remove-book: actually delete instead of dry-running",
    )
    w.add_argument(
        "--format-stats",
        dest="format_stats",
        action="store_true",
        help="Show per-format book counts and total bytes",
    )

    return p


def _parse_book_id(raw) -> int | None:
    try:
        return int(raw)
    except TypeError, ValueError:
        print(f"ERROR: BOOK_ID must be an integer, got {raw!r}", file=sys.stderr)
        return None


def _run_write(db_path: str, action) -> int:
    """Execute ``action(wdb)`` inside a WritableCalibreDB.

    Every write verb funnels through here so trigger UDFs get registered,
    transactions stay atomic, and lock/validation errors map to clean exit
    codes instead of tracebacks.
    """
    import sqlite3 as _sqlite3

    from cquarry.write import WritableCalibreDB

    try:
        with WritableCalibreDB(db_path) as wdb:
            return action(wdb)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except _sqlite3.OperationalError as e:
        print(
            f"ERROR: could not acquire the database write lock ({e}). "
            "Is Calibre running? Close it first.",
            file=sys.stderr,
        )
        return 1


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        db_path = find_db(args.db)

        if args.set_title:
            book_id = _parse_book_id(args.set_title[0])
            if book_id is None:
                return 2
            new_title = args.set_title[1]

            def _do_set_title(wdb):
                wdb.update_title(book_id, new_title)
                if not args.quiet:
                    print(
                        f"Renamed book {book_id} to {new_title!r} "
                        "(queued for OPF regeneration on Calibre's next startup)."
                    )
                return 0

            return _run_write(db_path, _do_set_title)

        if args.set_authors:
            book_id = _parse_book_id(args.set_authors[0])
            if book_id is None:
                return 2
            names = [n.strip() for n in args.set_authors[1].split(";") if n.strip()]

            def _do_set_authors(wdb):
                wdb.set_authors(book_id, names)
                if not args.quiet:
                    print(
                        f"Set authors of book {book_id} to {' & '.join(names)} "
                        "(queued for OPF regeneration)."
                    )
                return 0

            return _run_write(db_path, _do_set_authors)

        if args.set_rating:
            book_id = _parse_book_id(args.set_rating[0])
            if book_id is None:
                return 2
            try:
                stars = float(args.set_rating[1])
            except ValueError:
                print(
                    f"ERROR: STARS must be a number 0-5, got {args.set_rating[1]!r}",
                    file=sys.stderr,
                )
                return 2
            if not 0 <= stars <= 5:
                print("ERROR: STARS must be within 0-5.", file=sys.stderr)
                return 2

            def _do_set_rating(wdb):
                wdb.set_rating(book_id, stars)
                if not args.quiet:
                    print(f"Rated book {book_id} at {stars:g} stars.")
                return 0

            return _run_write(db_path, _do_set_rating)

        if args.set_comments:
            book_id = _parse_book_id(args.set_comments[0])
            if book_id is None:
                return 2
            text = args.set_comments[1]

            def _do_set_comments(wdb):
                wdb.set_comments(book_id, text)
                if not args.quiet:
                    print(f"Comments updated on book {book_id}.")
                return 0

            return _run_write(db_path, _do_set_comments)

        if args.clear_comments:
            book_id = _parse_book_id(args.clear_comments)
            if book_id is None:
                return 2

            def _do_clear_comments(wdb):
                wdb.set_comments(book_id, None)
                if not args.quiet:
                    print(f"Comments cleared on book {book_id}.")
                return 0

            return _run_write(db_path, _do_clear_comments)

        if args.set_column:
            book_id = _parse_book_id(args.set_column[0])
            label = args.set_column[1]
            value = args.set_column[2]
            if book_id is None:
                return 2

            def _do_set_column(wdb):
                # set_custom_column refuses non-editable/composite columns
                # and validates enumerations itself.
                wdb.set_custom_column(book_id, label, value)
                if not args.quiet:
                    print(f"Set #{label.lstrip('#')} = {value!r} on book {book_id}.")
                return 0

            return _run_write(db_path, _do_set_column)

        if args.clear_column:
            book_id = _parse_book_id(args.clear_column[0])
            label = args.clear_column[1]
            if book_id is None:
                return 2

            def _do_clear_column(wdb):
                wdb.set_custom_column(book_id, label, None)
                if not args.quiet:
                    print(f"Cleared #{label.lstrip('#')} on book {book_id}.")
                return 0

            return _run_write(db_path, _do_clear_column)

        if args.remove_book is not None:
            book_id = _parse_book_id(args.remove_book)
            if book_id is None:
                return 2
            if not args.confirm_remove:

                def _describe(wdb):
                    row = wdb.conn.execute(
                        "SELECT title FROM books WHERE id = ?", (book_id,)
                    ).fetchone()
                    title = row["title"] if row else "<unknown>"
                    fmts = [
                        r[0]
                        for r in wdb.conn.execute(
                            "SELECT format FROM data WHERE book = ?", (book_id,)
                        )
                    ]
                    print(
                        f"DRY RUN — would permanently remove book {book_id} "
                        f"({title!r}, formats: {', '.join(fmts) or 'none'})."
                    )
                    print("Re-run with --confirm-remove to delete.")
                    return 0

                return _run_write(db_path, _describe)

            def _do_remove(wdb):
                wdb.remove_book(book_id)
                if not args.quiet:
                    print(f"Book {book_id} removed.")
                return 0

            return _run_write(db_path, _do_remove)

        if args.format_stats:
            with CalibreDB(db_path) as db:
                stats = db.get_format_stats()
                total = sum(s["bytes"] for s in stats.values())
                count_total = sum(s["count"] for s in stats.values())
                print(f"{'Format':<10}{'Books':>8}{'Bytes':>16}")
                for fmt, s in sorted(stats.items()):
                    print(f"{fmt:<10}{s['count']:>8}{s['bytes']:>16,}")
                print("-" * 34)
                print(f"{'TOTAL':<10}{count_total:>8}{total:>16,}")
            return 0

        with CalibreDB(db_path) as db:
            if args.export_annotations:
                return run_annotations_export(
                    db, args.book_id, args.output, quiet=args.quiet
                )

            if args.exportlt:
                outdir = args.outdir or args.output or "librarything_export"
                matching_ids = None
                if args.search is not None:
                    try:
                        matching_ids = set(db.search(args.search))
                    except Exception as e:
                        print(f"Error parsing search query: {e}", file=sys.stderr)
                        return 1
                    if not matching_ids:
                        print(
                            f"No books matched the query: '{args.search}'. Nothing written.",
                            file=sys.stderr,
                        )
                        return 0

                run_librarything_export(
                    db, outdir=outdir, matching_ids=matching_ids, quiet=args.quiet
                )
                return 0

            if args.catalog:
                output = args.output or "catalog.txt"
                write_catalog(
                    db,
                    output,
                    wing=args.wing,
                    primary_only=args.primary_only,
                    show_tags=args.show_tags,
                    show_id=args.show_id,
                    show_custom=args.show_custom,
                    plugin_data=args.plugin_data,
                    author_details=args.show_author_details,
                    quiet=args.quiet,
                )
                return 0

            if args.all_wings:
                outdir = args.outdir or "catalogs"
                write_all_wings(
                    db,
                    outdir,
                    primary_only=args.primary_only,
                    show_tags=args.show_tags,
                    show_id=args.show_id,
                    show_custom=args.show_custom,
                    author_details=args.show_author_details,
                    quiet=args.quiet,
                )
                return 0

            if args.stats:
                show_stats(db, quiet=args.quiet)
                return 0

            if args.analytics == "author":
                show_author_stats(db, quiet=args.quiet)
                return 0
            elif args.analytics == "pace":
                show_pace_stats(db, quiet=args.quiet)
                return 0
            elif args.analytics == "tags":
                show_tag_tree(db, quiet=args.quiet)
                return 0
            elif args.analytics == "overlap":
                show_wing_overlap(db, quiet=args.quiet)
                return 0

            if args.audit:
                output = args.output or "audit.csv"
                run_audit(db, output, quiet=args.quiet)
                return 0

            if args.recent is not None:
                show_recent(db, args.recent, quiet=args.quiet)
                return 0

            if args.series:
                show_series(db, quiet=args.quiet)
                return 0

            if args.export:
                fmt = args.format or "json"
                output = args.output or f"library.{fmt}"
                run_export(
                    db, output, fmt, show_custom=args.show_custom, quiet=args.quiet
                )
                return 0

            if args.search is not None:
                # No --output: stream to stdout. --format selects a structured
                # form (json/csv/ai); otherwise a plain-text listing.
                run_search_export(
                    db,
                    args.search,
                    args.output,
                    fmt=args.format,
                    show_custom=args.show_custom,
                    plugin_data=args.plugin_data,
                    author_details=args.show_author_details,
                    quiet=args.quiet,
                )
                return 0

            if args.wings:
                show_wings(db)
                return 0

            if args.tags:
                show_tag_dump(db, quiet=args.quiet)
                return 0

            # If --wing was given without a mode, default to catalog
            if args.wing:
                output = args.output or "catalog.txt"
                write_catalog(
                    db,
                    output,
                    wing=args.wing,
                    primary_only=args.primary_only,
                    show_tags=args.show_tags,
                    show_id=args.show_id,
                    show_custom=args.show_custom,
                    plugin_data=args.plugin_data,
                    author_details=args.show_author_details,
                    quiet=args.quiet,
                )
                return 0

            parser.print_help()
            return 2

    except (FileNotFoundError, PermissionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
