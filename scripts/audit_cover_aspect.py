#!/usr/bin/env python3
"""audit_cover_aspect: flag covers whose width/height ratio is unusual.

Book covers are overwhelmingly portrait, near 2:3 (a w/h ratio around
0.66). This audit sizes every catalogued cover through cquarry's
header-only image readers (no image library, no full decode) and
reports two advisory bands:

    cover_aspect_narrow  w/h below --low (default 0.55): a spine scan,
                         a badly cropped strip, or a rotated image
    cover_aspect_wide    w/h above --high (default 0.80): landscape or
                         square art

False-positive note, on purpose: legitimate landscape and square art
exists (art books, comics omnibuses, board-game boxes, manga wide
editions). Both classes are advisory; the audit surfaces the
distribution, the operator judges. Covers the DB records but the disk
lacks, and covers whose file is unreadable, are other audits' classes
(find_missing_cover_files) and are skipped here, not double-reported.

Read-only: metadata.db opens mode=ro through cquarry, cover files are
read header-only, nothing is written. Exit codes:
    0 = no covers outside the bands
    1 = findings (advisory)
    2 = setup error

Usage:
    python3 audit_cover_aspect.py [library_dir_or_metadata.db]
        [--search EXPR | --ids ID[,ID...]] [--low 0.55] [--high 0.80]
        [--quiet] [--format {text,json}]
"""

import argparse
import json
import sys
from pathlib import Path

LOW_BAND = 0.55
HIGH_BAND = 0.80


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Flag covers with unusual width/height ratios."
    )
    parser.add_argument(
        "path", nargs="?", help="library directory or metadata.db (default: .)"
    )
    parser.add_argument(
        "--search",
        default=None,
        metavar="EXPR",
        help="check only the books matching a Calibre search expression",
    )
    parser.add_argument(
        "--ids", default=None, metavar="ID[,ID...]", help="check only these ids"
    )
    parser.add_argument(
        "--low",
        type=float,
        default=LOW_BAND,
        help=f"narrow-band threshold (default: {LOW_BAND})",
    )
    parser.add_argument(
        "--high",
        type=float,
        default=HIGH_BAND,
        help=f"wide-band threshold (default: {HIGH_BAND})",
    )
    parser.add_argument("--quiet", action="store_true", help="print only the book ids")
    parser.add_argument(
        "--format", choices=("text", "json"), default="text", dest="fmt"
    )
    args = parser.parse_args()

    id_filter: set[int] | None = None
    if args.ids:
        id_filter = set()
        for part in args.ids.split(","):
            part = part.strip()
            if part:
                try:
                    id_filter.add(int(part))
                except ValueError:
                    print(f"ERROR: invalid id {part!r}.", file=sys.stderr)
                    return 2

    p = Path(args.path) if args.path else Path.cwd()
    db_path = p / "metadata.db" if p.is_dir() else p
    if not db_path.exists():
        print("ERROR: no metadata.db found.", file=sys.stderr)
        return 2

    from cquarry.db import CalibreDB
    from cquarry.helpers import get_image_size

    findings: list[dict] = []
    checked = 0
    skipped = 0
    with CalibreDB(str(db_path)) as db:
        if args.search:
            try:
                id_filter = set(db.search(args.search))
            except Exception as e:
                print(
                    f"ERROR: could not parse the search expression: {e}",
                    file=sys.stderr,
                )
                return 2
            if not id_filter:
                print("No books matched the filter; nothing to check.")
                return 0
        for b in db.get_all_books():
            bid = b["id"]
            if id_filter is not None and bid not in id_filter:
                continue
            if not b["has_cover"]:
                continue
            cover = db.get_cover_path(bid)
            if not cover:
                skipped += 1
                continue
            size = get_image_size(cover)
            if not size or not size[1]:
                skipped += 1
                continue
            width, height = size
            checked += 1
            ratio = width / height
            if ratio < args.low:
                cls = "cover_aspect_narrow"
            elif ratio > args.high:
                cls = "cover_aspect_wide"
            else:
                continue
            findings.append(
                {
                    "book": bid,
                    "class": cls,
                    "width": width,
                    "height": height,
                    "ratio": round(ratio, 3),
                    "title": b["title"] or "",
                }
            )

    if args.fmt == "json":
        print(
            json.dumps(
                {"findings": findings, "checked": checked, "skipped": skipped}, indent=2
            )
        )
        return 1 if findings else 0

    if not findings:
        print(
            f"All {checked} catalogued cover(s) sit inside the "
            f"{args.low}-{args.high} ratio bands ({skipped} skipped: "
            "missing or unreadable files)."
        )
        return 0

    print(
        f"{len(findings)} cover(s) outside the ratio bands "
        f"({checked} checked, {skipped} skipped):\n"
    )
    for f in findings:
        if args.quiet:
            print(f["book"])
            continue
        print(
            f"  #{f['book']} {f['title']}  {f['width']}x{f['height']}  "
            f"ratio {f['ratio']}  [{f['class']}]"
        )
    if not args.quiet:
        print(
            "\nAdvisory: legitimate landscape and square art exists; "
            "narrow hits are usually spine scans or bad crops."
        )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
