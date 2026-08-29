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
from cquarry_cli.modes.detail import show_book
from cquarry_cli.modes.display import (
    show_entities,
    show_reading_progress,
    show_recent,
    show_series,
    show_wings,
)
from cquarry_cli.modes.export import (
    run_annotations_export,
    run_export,
    run_search_export,
)
from cquarry_cli.modes.info import show_columns, show_info
from cquarry_cli.modes.librarything import run_librarything_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump
from cquarry_cli.tui import interactive_menu
from cquarry_cli.writeops import dispatch_write


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
    group.add_argument(
        "--book",
        dest="book",
        type=int,
        default=None,
        metavar="BOOK_ID",
        help="Show the full record for one book: identifiers, format files, "
        "cover, comments, custom columns, annotations, reading progress",
    )
    group.add_argument(
        "--entities",
        dest="entities",
        choices=["authors", "series", "publishers", "tags", "languages", "ratings"],
        default=None,
        metavar="KIND",
        help="List an entity class with book counts (authors/series/publishers "
        "include sort and link columns)",
    )
    group.add_argument(
        "--reading-progress",
        dest="reading_progress",
        action="store_true",
        help="Show per-device reading positions with progress bars, newest first",
    )
    group.add_argument(
        "--columns",
        dest="columns",
        action="store_true",
        help="List custom columns: type, editability, enum values",
    )
    group.add_argument(
        "--info",
        dest="info",
        action="store_true",
        help="Library dossier: identity, wings + expressions, saved searches, "
        "@Name user categories, grouped search terms, feeds, sync queues",
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

    # --- Write verbs (opt-in; all funnel through writeops/cquarry.write) ---
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
        "--set-pubdate",
        dest="set_pubdate",
        nargs=2,
        metavar=("BOOK_ID", "DATE"),
        default=None,
        help="Set the publication date (YYYY-MM-DD or a full ISO datetime)",
    )
    w.add_argument(
        "--clear-pubdate",
        dest="clear_pubdate",
        metavar="BOOK_ID",
        default=None,
        help="Clear the publication date",
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
        "--add-tag",
        dest="add_tag",
        nargs=2,
        metavar=("BOOK_ID", "TAG"),
        action="append",
        default=None,
        help="Attach a tag (repeat the flag for several)",
    )
    w.add_argument(
        "--remove-tag",
        dest="remove_tag",
        nargs=2,
        metavar=("BOOK_ID", "TAG"),
        action="append",
        default=None,
        help="Detach a tag (repeat the flag for several)",
    )
    w.add_argument(
        "--set-identifier",
        dest="set_identifier",
        nargs=3,
        metavar=("BOOK_ID", "TYPE", "VALUE"),
        default=None,
        help="Upsert an identifier (isbn, goodreads, ...); empty VALUE deletes it",
    )
    w.add_argument(
        "--clear-identifier",
        dest="clear_identifier",
        nargs=2,
        metavar=("BOOK_ID", "TYPE"),
        default=None,
        help="Delete one identifier type",
    )
    w.add_argument(
        "--set-series",
        dest="set_series",
        nargs=2,
        metavar=("BOOK_ID", "NAME"),
        default=None,
        help='Assign the series (index 1.0 unless --series-index; "" clears)',
    )
    w.add_argument(
        "--series-index",
        dest="series_index",
        type=float,
        default=None,
        metavar="NUM",
        help="With --set-series: the book's number in the series",
    )
    w.add_argument(
        "--clear-series",
        dest="clear_series",
        metavar="BOOK_ID",
        default=None,
        help="Remove the book from its series",
    )
    w.add_argument(
        "--set-publisher",
        dest="set_publisher",
        nargs=2,
        metavar=("BOOK_ID", "NAME"),
        default=None,
        help="Replace the publisher",
    )
    w.add_argument(
        "--clear-publisher",
        dest="clear_publisher",
        metavar="BOOK_ID",
        default=None,
        help="Remove the publisher",
    )
    w.add_argument(
        "--set-languages",
        dest="set_languages",
        nargs=2,
        metavar=("BOOK_ID", "LANGS"),
        default=None,
        help='Replace languages ("en, fr" — English names or ISO codes)',
    )
    w.add_argument(
        "--clear-languages",
        dest="clear_languages",
        metavar="BOOK_ID",
        default=None,
        help="Remove all languages from the book",
    )
    w.add_argument(
        "--add-format",
        dest="add_format",
        nargs=4,
        metavar=("BOOK_ID", "FORMAT", "NAME", "SIZE"),
        default=None,
        help="Register a format row (metadata only — the file must already "
        "sit in the book's folder as NAME.format)",
    )
    w.add_argument(
        "--remove-format",
        dest="remove_format",
        nargs=2,
        metavar=("BOOK_ID", "FORMAT"),
        default=None,
        help="Drop a format row (leaves the file on disk untouched)",
    )
    w.add_argument(
        "--set-cover",
        dest="set_cover",
        nargs=2,
        metavar=("BOOK_ID", "YES/NO"),
        default=None,
        help="Toggle the catalogued has_cover flag",
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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        if args.series_index is not None and not args.set_series:
            print(
                "ERROR: --series-index is only valid together with --set-series.",
                file=sys.stderr,
            )
            return 2
        db_path = find_db(args.db)

        # Write verbs (opt-in) are dispatched before any read mode opens the
        # database read-only; writeops owns WritableCalibreDB and the
        # error-to-exit-code mapping (validation -> 2, lock/write -> 1).
        handled = dispatch_write(args, db_path)
        if handled is not None:
            return handled

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

            if args.book is not None:
                ok = show_book(db, args.book, quiet=args.quiet)
                return 0 if ok else 1

            if args.entities:
                show_entities(db, args.entities, quiet=args.quiet)
                return 0

            if args.reading_progress:
                show_reading_progress(db, quiet=args.quiet)
                return 0

            if args.columns:
                show_columns(db, quiet=args.quiet)
                return 0

            if args.info:
                show_info(db, quiet=args.quiet)
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
