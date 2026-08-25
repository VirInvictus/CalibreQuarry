#!/usr/bin/env python3
"""audit_conversion_overrides: list books with manual Calibre conversion recipes.

Calibre stores per-book conversion overrides in the `conversion_options` table
as pickled recipe blobs. This audit reads them through cquarry's extractor,
which deliberately never unpickles: a blob's presence and size are enough to
know a book has drifted from default conversion, which is the question this
tool answers.

Read-only. Exit codes:
    0 = no overrides (or none after filtering)
    1 = overrides found (ids listed; pipe into your repair workflow)
    2 = setup error

Usage:
    python3 audit_conversion_overrides.py [library_dir_or_metadata.db] [--quiet]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

RESET = "\033[0m"
YELLOW = "\033[1;33m"


def resolve_db(path: str | None) -> Path | None:
    p = Path(path) if path else Path.cwd()
    if p.is_dir():
        candidate = p / "metadata.db"
        return candidate if candidate.exists() else None
    return p if p.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="List books carrying manual conversion overrides."
    )
    parser.add_argument(
        "path", nargs="?", help="library directory or metadata.db (default: .)"
    )
    parser.add_argument("--quiet", action="store_true", help="print only the book ids")
    args = parser.parse_args()

    db_path = resolve_db(args.path)
    if db_path is None:
        print("ERROR: no metadata.db found.", file=sys.stderr)
        return 2

    from cquarry.db import CalibreDB

    try:
        db = CalibreDB(str(db_path))
    except Exception as e:
        print(f"ERROR: cannot open {db_path}: {e}", file=sys.stderr)
        return 2

    try:
        profiles = db.get_conversion_profiles()
        titles = {b["id"]: b["title"] for b in db.get_all_books()}
    finally:
        db.close()

    if not profiles:
        if not args.quiet:
            print("No manual conversion overrides found.")
        return 0

    rc = 1
    if args.quiet:
        for row in profiles:
            print(row["book"])
        return rc

    print(f"{YELLOW}Manual conversion overrides ({len(profiles)} books){RESET}\n")
    for row in profiles:
        title = titles.get(row["book"], "?")
        print(
            f"  #{row['book']} [{row['format']}] {title}"
            f" \u2014 recipe blob {row['data_size']} bytes"
        )
    print(
        "\nThe recipe blobs are Calibre pickles; open the book's conversion"
        " dialog in Calibre to inspect or clear them."
    )
    return rc


if __name__ == "__main__":
    sys.exit(main())
