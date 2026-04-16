from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from cquarry.config import VERSION
from cquarry.db import CalibreDB
from cquarry.helpers import find_db
from cquarry.modes.catalog import write_catalog, write_all_wings
from cquarry.modes.stats import show_stats
from cquarry.modes.analytics import show_author_stats, show_pace_stats, show_tag_tree, show_wing_overlap
from cquarry.modes.audit import run_audit
from cquarry.modes.display import show_recent, show_series, show_wings
from cquarry.modes.export import run_export, run_search_export
from cquarry.tui import interactive_menu, _reset_terminal


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cquarry",
        description="Calibre library toolkit: catalog, stats, audit, export"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    group = p.add_mutually_exclusive_group()
    group.add_argument("--catalog", action="store_true",
                       help="Build a text catalog")
    group.add_argument("--all-wings", dest="all_wings", action="store_true",
                       help="Generate catalogs for all virtual libraries")
    group.add_argument("--stats", action="store_true",
                       help="Show library statistics")
    group.add_argument("--analytics", choices=["author", "pace", "tags", "overlap"], default=None,
                       help="Extended analytics and visualizations")
    group.add_argument("--audit", action="store_true",
                       help="Report issues (untagged, unrated, series gaps)")
    group.add_argument("--recent", type=int, nargs='?', const=20, default=None,
                       help="Show N most recently added books (default: 20)")
    group.add_argument("--series", action="store_true",
                       help="List all series with completeness and gap detection")
    group.add_argument("--export", action="store_true",
                       help="Export library to JSON, CSV, or AI format")
    group.add_argument("--search", default=None, metavar="QUERY",
                       help="Export books matching a Calibre search expression")
    group.add_argument("--wings", action="store_true",
                       help="List all virtual library wings")

    p.add_argument("--db", default=None,
                   help="Path to Calibre metadata.db (auto-detected if omitted)")
    p.add_argument("--wing", default=None,
                   help="Filter to a specific virtual library wing")
    p.add_argument("--output", default=None, help="Output file path")
    p.add_argument("--outdir", default=None,
                   help="Output directory for --all-wings (default: current dir)")
    p.add_argument("--format", choices=["json", "csv", "ai"], default="json",
                   help="Export format (default: json)")
    p.add_argument("--primary-only", dest="primary_only", action="store_true",
                   help="Use only the first author (useful for TTRPG collections)")
    p.add_argument("--show-tags", dest="show_tags", action="store_true",
                   help="Show tags instead of ratings in catalog output")
    p.add_argument("--show-id", dest="show_id", action="store_true",
                   help="Prefix each book with its Calibre ID for scripting")
    p.add_argument("--quiet", action="store_true", help="Minimize output")

    return p


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        db_path = find_db(args.db)

        with CalibreDB(db_path) as db:
            if args.catalog:
                output = args.output or "catalog.txt"
                write_catalog(db, output, wing=args.wing,
                              primary_only=args.primary_only,
                              show_tags=args.show_tags, show_id=args.show_id,
                              quiet=args.quiet)
                return 0

            if args.all_wings:
                outdir = args.outdir or "catalogs"
                write_all_wings(db, outdir, primary_only=args.primary_only,
                                show_tags=args.show_tags, show_id=args.show_id,
                                quiet=args.quiet)
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
                run_export(db, output, fmt, quiet=args.quiet)
                return 0

            if args.search:
                output = args.output or "search_results.txt"
                run_search_export(db, args.search, output, quiet=args.quiet)
                return 0

            if args.wings:
                show_wings(db)
                return 0

            # If --wing was given without a mode, default to catalog
            if args.wing:
                output = args.output or "catalog.txt"
                write_catalog(db, output, wing=args.wing,
                              primary_only=args.primary_only,
                              show_tags=args.show_tags, show_id=args.show_id,
                              quiet=args.quiet)
                return 0

            parser.print_help()
            return 2

    except (FileNotFoundError, PermissionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130
