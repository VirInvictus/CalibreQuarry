import argparse
import sys

from cquarry.db import CalibreDB
from cquarry.helpers import find_db
from cquarry.integrity import find_untagged

from cquarry_cli import VERSION
from cquarry_cli.manifest import DEFAULT_AUDIENCE
from cquarry_cli.output import OutputRefusedError
from cquarry_cli.restrict import RestrictedView, restrict_refusal
from cquarry_cli.modes.analytics import (
    show_author_stats,
    show_genre_breakdown,
    show_pace_stats,
    show_reading_stats,
    show_tag_tree,
    show_wing_overlap,
)
from cquarry_cli.modes.audit import run_audit
from cquarry_cli.modes.catalog import (
    run_all_saved_searches,
    write_all_wings,
    write_catalog,
)
from cquarry_cli.modes.detail import show_book, show_book_json
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
from cquarry_cli.modes.fts import run_fts_search, run_fts_status
from cquarry_cli.modes.info import show_columns, show_info
from cquarry_cli.modes.librarything import run_librarything_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump
from cquarry_cli.tui import interactive_menu
from cquarry_cli.setwrite import dispatch_set_write
from cquarry_cli.writeops import dispatch_write


def _book_ids(value: str) -> list[int]:
    """Parse --book's id list: `42` or `42,43,44` (whitespace tolerated).

    An empty string is the bare `--book` form (argparse applies the type to
    the const too) and comes back as an empty list for the dispatch to
    resolve: with --untagged it selects the untagged set, without one it is
    a usage error.
    """
    ids: list[int] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            raise argparse.ArgumentTypeError(f"invalid book id {part!r}") from None
    return ids


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
    group.add_argument(
        "--all-saved-searches",
        dest="all_saved_searches",
        action="store_true",
        help="Generate a catalog per saved search (the --all-wings "
        "analog; files land in --outdir, one per search, scoped by "
        "--restrict when given)",
    )
    group.add_argument("--stats", action="store_true", help="Show library statistics")
    group.add_argument(
        "--analytics",
        choices=["author", "pace", "tags", "genres", "overlap", "reading"],
        default=None,
        help="Extended analytics and visualizations (reading: status "
        "funnel and finish dates from #reading_status/#date_read; "
        "read-only)",
    )
    group.add_argument(
        "--audit",
        action="store_true",
        help="Report issues (untagged, unrated, series gaps, conversion overrides)",
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
        "--fts",
        default=None,
        metavar="QUERY",
        help="Full-text content search over Calibre's full-text-search.db "
        "sidecar (what the books' text actually says, not metadata). "
        "Case- and accent-folded; composes with --restrict. Prints an "
        "index-staleness summary after the matches unless --quiet",
    )
    group.add_argument(
        "--fts-status",
        dest="fts_status",
        action="store_true",
        help="Report FTS index staleness only: never-indexed formats, "
        "indexed-empty documents, stale entries queued for re-index, and "
        "extraction errors",
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
        nargs="?",
        const="",
        default=None,
        type=_book_ids,
        metavar="BOOK_ID[,BOOK_ID...]",
        help="Show the full record for one book or a comma-separated list: "
        "identifiers, format files, cover, comments, custom columns, "
        "annotations, reading progress. With --untagged, give no ids to "
        "select every untagged book",
    )
    # --untagged stays OUTSIDE the exclusive group on purpose: it is a
    # modifier of --book (`--book --untagged`), not an independent mode.
    p.add_argument(
        "--untagged",
        dest="untagged",
        action="store_true",
        help="With --book: select every untagged book (the phase-3 entry "
        "state) instead of listing ids; use as `--book --untagged`",
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

    group.add_argument(
        "--exportlt",
        action="store_true",
        help="Export to LibraryThing CSV format (can be used alone or with --search)",
    )

    group.add_argument(
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
        "--restrict",
        default=None,
        metavar="SEARCH",
        help="Scope every read mode to books matching this search "
        "expression (or `vl:Name` for a virtual library): stats, audit, "
        "analytics, exports, catalogs, and the rest compute over the "
        "restricted set only. Refused with write verbs and --book/--id",
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
        "--genre-depth",
        dest="genre_depth",
        type=int,
        default=1,
        metavar="N",
        help="Levels of the tag hierarchy shown by --analytics genres "
        "(default: 1, top-level genres only)",
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
    group.add_argument(
        "--format-stats",
        dest="format_stats",
        action="store_true",
        help="Show per-format book counts and total bytes",
    )

    # --- Set-oriented writes: one target set, id-less --batch-* verbs ---
    s = p.add_argument_group(
        "set writes (dry-run by default; --apply requires --backup-dir "
        "and Calibre closed)"
    )
    src = s.add_mutually_exclusive_group()
    src.add_argument(
        "--ids",
        dest="set_ids",
        default=None,
        metavar="ID[,ID...]",
        help="Target set: explicit book ids (set mode)",
    )
    src.add_argument(
        "--from-search",
        dest="from_search",
        default=None,
        metavar="EXPR",
        help="Target set: books matching a Calibre search expression, "
        "resolved read-only before anything opens writable",
    )
    src.add_argument(
        "--from-untagged",
        dest="from_untagged",
        action="store_true",
        help="Target set: every untagged book (the phase-3 entry state)",
    )
    src.add_argument(
        "--from-manifest",
        dest="from_manifest",
        default=None,
        metavar="FILE",
        help="Target set: ids one per line or comma-separated in FILE; the "
        "only source that unlocks --batch-clear-rating",
    )
    s.add_argument(
        "--batch-add-tag",
        dest="batch_add_tag",
        action="append",
        metavar="TAG",
        default=None,
        help="Add a tag to every targeted book (repeatable)",
    )
    s.add_argument(
        "--batch-remove-tag",
        dest="batch_remove_tag",
        action="append",
        metavar="TAG",
        default=None,
        help="Remove a tag from every targeted book (repeatable)",
    )
    s.add_argument(
        "--batch-clear-tags",
        dest="batch_clear_tags",
        action="store_true",
        help="Detach every tag from every targeted book",
    )
    s.add_argument(
        "--batch-clear-rating",
        dest="batch_clear_rating",
        action="store_true",
        help="Clear the rating on every targeted book; ONLY legal with "
        "--from-manifest (the NON-NEGOTIABLES bulk-ratings ban)",
    )
    s.add_argument(
        "--batch-set-column",
        dest="batch_set_column",
        nargs=2,
        metavar=("LABEL", "VALUE"),
        default=None,
        help="Write a custom-column value on every targeted book "
        "(#reading_status/#status/#date_read are refused)",
    )
    s.add_argument(
        "--batch-clear-column",
        dest="batch_clear_column",
        metavar="LABEL",
        default=None,
        help="Clear a custom-column value on every targeted book",
    )
    s.add_argument(
        "--batch-add-column-value",
        dest="batch_add_column_value",
        action="append",
        nargs=2,
        metavar=("LABEL", "VALUE"),
        default=None,
        help="Append a value to a multi-valued custom column on every "
        "targeted book (repeatable; deduped per book)",
    )
    s.add_argument(
        "--batch-set-title",
        dest="batch_set_title",
        metavar="TITLE",
        default=None,
        help="Rename every targeted book",
    )
    s.add_argument(
        "--batch-set-authors",
        dest="batch_set_authors",
        metavar="NAMES",
        default=None,
        help="Replace authors on every targeted book ('Name One; Name Two')",
    )
    s.add_argument(
        "--batch-set-pubdate",
        dest="batch_set_pubdate",
        metavar="DATE",
        default=None,
        help="Set the publication date on every targeted book",
    )
    s.add_argument(
        "--batch-clear-pubdate",
        dest="batch_clear_pubdate",
        action="store_true",
        help="Clear the publication date on every targeted book",
    )
    s.add_argument(
        "--batch-set-publisher",
        dest="batch_set_publisher",
        metavar="NAME",
        default=None,
        help="Set the publisher on every targeted book",
    )
    s.add_argument(
        "--batch-clear-publisher",
        dest="batch_clear_publisher",
        action="store_true",
        help="Clear the publisher on every targeted book",
    )
    s.add_argument(
        "--batch-set-languages",
        dest="batch_set_languages",
        metavar="CODES",
        default=None,
        help="Replace the languages on every targeted book",
    )
    s.add_argument(
        "--batch-clear-languages",
        dest="batch_clear_languages",
        action="store_true",
        help="Clear the languages on every targeted book",
    )
    s.add_argument(
        "--batch-set-series",
        dest="batch_set_series",
        metavar="NAME",
        default=None,
        help="Put every targeted book in a series (--series-index optional)",
    )
    s.add_argument(
        "--batch-clear-series",
        dest="batch_clear_series",
        action="store_true",
        help="Remove every targeted book from its series",
    )
    s.add_argument(
        "--batch-set-identifier",
        dest="batch_set_identifier",
        nargs=2,
        metavar=("TYPE", "VALUE"),
        default=None,
        help="Set an identifier (isbn, goodreads, ...) on every targeted book",
    )
    s.add_argument(
        "--batch-clear-identifier",
        dest="batch_clear_identifier",
        metavar="TYPE",
        default=None,
        help="Clear an identifier type on every targeted book",
    )
    s.add_argument(
        "--batch-set-cover",
        dest="batch_set_cover",
        metavar="YES/NO",
        default=None,
        help="Set the catalogued has_cover flag on every targeted book",
    )
    s.add_argument(
        "--batch-remove-format",
        dest="batch_remove_format",
        metavar="FMT",
        default=None,
        help="Drop a format row from every targeted book (files untouched)",
    )
    s.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="Execute the planned set write (default is a dry run)",
    )
    s.add_argument(
        "--backup-dir",
        dest="backup_dir",
        metavar="DIR",
        default=None,
        help="REQUIRED with --apply: metadata.db is copied here first; must "
        "sit outside the library directory",
    )
    s.add_argument(
        "--commit-per-book",
        dest="commit_per_book",
        action="store_true",
        help="With --apply: one transaction per book instead of one for the "
        "whole pass (escape hatch for very large sets)",
    )

    # --- The acquisition run verbs (Phase 17) ---
    sub = p.add_subparsers(dest="subcommand")
    run_p = sub.add_parser(
        "run",
        help="The acquisition pathway: vet (phase1), import (phase2), curate (phase3)",
    )
    run_p.add_argument(
        "phase",
        choices=(
            "phase1",
            "sign",
            "phase2",
            "phase3",
            "convert",
            "polish",
            "cover",
            "export",
            "merge",
            "flush",
            "backfill",
        ),
        help="phase1: vet a downloads dir into a manifest; sign: seal the "
        "reviewed manifest for phase 2; phase2: import the signed "
        "manifest; phase3: curate + mechanical pass; the Phase 19 C "
        "verbs (dry-run by default, --apply executes): convert "
        "(ebook-convert), polish (ebook-polish), cover (set/remove "
        "cover), export (calibredb), merge (duplicate into keeper), "
        "flush (embed the OPF queue), backfill (metadata source)",
    )
    for flag, help_text in (
        ("--search", "target set: books matching a search expression"),
        ("--ids", "target set: explicit book ids (ID[,ID...])"),
    ):
        run_p.add_argument(flag, default=None, help=help_text)
    run_p.add_argument(
        "--apply",
        dest="apply",
        action="store_true",
        help="execute the plan (default is a dry run)",
    )
    run_p.add_argument(
        "--to",
        dest="to",
        default=None,
        metavar="FORMAT",
        help="convert: the output format",
    )
    run_p.add_argument(
        "--from-format",
        dest="from_format",
        default=None,
        metavar="FORMAT",
        help="convert: the source format (default: the largest other format)",
    )
    run_p.add_argument(
        "--polish-ops",
        dest="polish_ops",
        default=None,
        metavar="OP[,OP...]",
        help="polish: comma list of smarten,unused-css,compress-images,"
        "subset-fonts,jacket,kepubify",
    )
    run_p.add_argument(
        "--cover",
        dest="cover",
        default=None,
        metavar="FILE",
        help="cover: image file to place on every targeted book",
    )
    run_p.add_argument(
        "--remove-cover",
        dest="remove_cover",
        action="store_true",
        help="cover: remove the cover instead of setting one",
    )
    run_p.add_argument(
        "--dest",
        dest="dest",
        default=None,
        metavar="DIR",
        help="export: destination directory",
    )
    run_p.add_argument(
        "--template",
        dest="template",
        default=None,
        metavar="TPL",
        help="export: calibredb save template (default '{author_sort}/{title} {id}')",
    )
    run_p.add_argument(
        "--keeper",
        dest="keeper",
        default=None,
        metavar="ID",
        help="merge: the book that survives",
    )
    run_p.add_argument(
        "--duplicate",
        dest="duplicate",
        default=None,
        metavar="ID",
        help="merge: the book folded into the keeper (lands in the trash)",
    )
    run_p.add_argument(
        "--chunk",
        dest="chunk",
        type=int,
        default=None,
        metavar="N",
        help="flush: embed_metadata chunk size (default 50)",
    )
    run_p.add_argument(
        "--fields",
        dest="fields",
        default=None,
        metavar="F[,F...]",
        help="backfill: comma list of title,authors,publisher,isbn,comments",
    )
    # --db in subparser position too (SUPPRESS keeps the main parser's
    # value when the flag is only given before `run`).
    run_p.add_argument(
        "--db",
        default=argparse.SUPPRESS,
        help="Path to Calibre metadata.db (before or after `run`)",
    )
    run_p.add_argument(
        "dir",
        nargs="?",
        default=None,
        help="phase1: the downloads directory to vet",
    )
    run_p.add_argument(
        "--manifest",
        metavar="FILE",
        help="sign/phase2/phase3: the batch manifest",
    )
    run_p.add_argument(
        "--backup-dir",
        dest="backup_dir",
        metavar="DIR",
        default=None,
        help="phase2 (required): metadata.db copied here first; outside the library",
    )
    run_p.add_argument(
        "--audience",
        default=None,
        help=f"phase2: the cc9 #audience value (default: {DEFAULT_AUDIENCE})",
    )
    run_p.add_argument(
        "--answer-file",
        dest="answer_file",
        metavar="FILE",
        help="phase3: JSON answers {book_id: {tags, comments_html, fixes}} "
        "instead of TTY prompts",
    )
    run_p.add_argument(
        "--bindery-report",
        dest="bindery_report",
        metavar="FILE",
        help="phase1: also write bindery's raw phase-1 JSON here",
    )
    run_p.add_argument(
        "--stamp",
        action="store_true",
        help="phase1: drive stamp_pdf on PDFs whose filename parses "
        "(file-side write; originals backed up)",
    )
    run_p.add_argument(
        "--apply-lossy",
        dest="apply_lossy",
        action="store_true",
        help="phase1: apply bindery's gated lossy repairs (file-side write)",
    )
    run_p.add_argument(
        "--quarantine",
        action="store_true",
        help="phase1: MOVE true DRM hits into _quarantine/ (file-side "
        "write). Without it the verdict and decision are recorded and "
        "the file stays where it is",
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
        if getattr(args, "subcommand", None) == "run":
            from cquarry_cli.run import dispatch_run

            return dispatch_run(args)
        if (
            args.series_index is not None
            and not args.set_series
            and not args.batch_set_series
        ):
            print(
                "ERROR: --series-index is only valid together with --set-series "
                "or --batch-set-series.",
                file=sys.stderr,
            )
            return 2
        db_path = find_db(args.db)

        # --restrict is a read-surface modifier (Phase 19 A.2). It composes
        # with every read mode through the RestrictedView; the combinations
        # where scoping is meaningless or dangerous are refused here,
        # before anything opens.
        if args.restrict:
            refusal = restrict_refusal(args)
            if refusal:
                print(f"ERROR: --restrict {refusal}.", file=sys.stderr)
                return 2

        # Set writes dispatch FIRST so a single-book/set combination is
        # rejected before any single-book verb executes; it returns None
        # when no set-mode flag is present. Write verbs (opt-in) are
        # dispatched before any read mode opens the database read-only;
        # writeops owns WritableCalibreDB and the error-to-exit-code
        # mapping (validation -> 2, lock/write -> 1).
        handled = dispatch_set_write(args, db_path)
        if handled is not None:
            return handled

        handled = dispatch_write(args, db_path)
        if handled is not None:
            return handled

        with CalibreDB(db_path) as db:
            if args.restrict:
                # One resolve point for the whole read surface: the view
                # below scopes every mode's universe to these ids. A
                # parse failure exits 1, matching --search's.
                from cquarry.search import ParseException

                try:
                    restrict_ids = set(db.search(args.restrict))
                except (ParseException, ValueError) as e:
                    print(
                        f"ERROR: could not parse the --restrict expression: {e}",
                        file=sys.stderr,
                    )
                    return 1
                db = RestrictedView(db, restrict_ids)

            if args.format_stats:
                stats = db.get_format_stats()
                total = sum(s["bytes"] for s in stats.values())
                count_total = sum(s["count"] for s in stats.values())
                print(f"{'Format':<10}{'Books':>8}{'Bytes':>16}")
                for fmt, s in sorted(stats.items()):
                    print(f"{fmt:<10}{s['count']:>8}{s['bytes']:>16,}")
                print("-" * 34)
                print(f"{'TOTAL':<10}{count_total:>8}{total:>16,}")
                return 0

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

                # The self-check verdict ("do not upload") is the
                # contract: a failed check must fail the verb, not exit 0
                # behind the operator's back.
                return run_librarything_export(
                    db, outdir=outdir, matching_ids=matching_ids, quiet=args.quiet
                )

            if args.catalog:
                output = args.output or "catalog.txt"
                return write_catalog(
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

            if args.all_saved_searches:
                outdir = args.outdir or "saved_search_catalogs"
                run_all_saved_searches(
                    db,
                    outdir,
                    primary_only=args.primary_only,
                    show_tags=args.show_tags,
                    show_id=args.show_id,
                    show_custom=args.show_custom,
                    plugin_data=args.plugin_data,
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
            elif args.analytics == "genres":
                if args.genre_depth < 1:
                    print("--genre-depth must be at least 1", file=sys.stderr)
                    return 2
                show_genre_breakdown(db, depth=args.genre_depth, quiet=args.quiet)
                return 0
            elif args.analytics == "overlap":
                show_wing_overlap(db, quiet=args.quiet)
                return 0
            elif args.analytics == "reading":
                show_reading_stats(db, quiet=args.quiet)
                return 0

            if args.audit:
                output = args.output or "audit.csv"
                run_audit(db, output, quiet=args.quiet)
                return 0

            if args.recent is not None:
                if args.recent <= 0:
                    print(
                        f"ERROR: --recent needs a positive count, got {args.recent}.",
                        file=sys.stderr,
                    )
                    return 2
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
                # form (json/csv/ai); otherwise a plain-text listing. A
                # parse failure exits 1, matching --exportlt --search.
                return run_search_export(
                    db,
                    args.search,
                    args.output,
                    fmt=args.format,
                    show_custom=args.show_custom,
                    plugin_data=args.plugin_data,
                    author_details=args.show_author_details,
                    quiet=args.quiet,
                )

            if args.fts:
                return run_fts_search(
                    db, args.fts, args.output, fmt=args.format, quiet=args.quiet
                )

            if args.fts_status:
                run_fts_status(db, quiet=args.quiet)
                return 0

            if args.wings:
                show_wings(db)
                return 0

            if args.tags:
                show_tag_dump(db, quiet=args.quiet)
                return 0

            if args.untagged or args.book is not None:
                if args.untagged:
                    if args.book:
                        print(
                            "ERROR: --untagged selects the books itself; give "
                            "--book ids, or use `--book --untagged` with no ids.",
                            file=sys.stderr,
                        )
                        return 2
                    ids = find_untagged(db)
                    if not ids:
                        print("No untagged books: the library is fully tagged.")
                        return 0
                elif not args.book:
                    print(
                        "ERROR: --book needs at least one id, or --book --untagged.",
                        file=sys.stderr,
                    )
                    return 2
                else:
                    ids = args.book
                if args.format == "json":
                    # Phase 17: machine-readable dossiers (phase 3's input).
                    ok = show_book_json(db, ids, quiet=args.quiet)
                    return 0 if ok else 1
                if args.format in ("csv", "ai"):
                    print(
                        "ERROR: --book supports --format json only.",
                        file=sys.stderr,
                    )
                    return 2
                ok = True
                for i, book_id in enumerate(ids):
                    if i and not args.quiet:
                        print()
                    ok = show_book(db, book_id, quiet=args.quiet) and ok
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
                return write_catalog(
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

            parser.print_help()
            return 2

    except OutputRefusedError as e:
        # The read surface's output guard: a report aimed at the database
        # (or its sidecars, or, for directory exporters, the library root)
        # is an argument-level refusal, exit 2, before anything is written.
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except (FileNotFoundError, PermissionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
