#!/usr/bin/env python3
"""audit_truncation: cross-check Count Pages data against the real PDFs.

The Count Pages plugin records a page count per book in
``books_pages_link`` (cquarry's ``get_page_metadata``), and nothing
ever re-checks it. This audit reads the actual page count of every
PDF the plugin measured (poppler's ``pdfinfo``) and reports two
classes:

    page_count_mismatch  the real count disagrees with the plugin's by
                         more than --tolerance (default 20%): the file
                         was truncated, or replaced by a different
                         edition, after the scan
    stale_plugin_data    the row no longer describes the file: the
                         catalogued format_size drifted from the file's
                         actual size, the plugin itself flagged
                         needs_scan, or the file's mtime is newer than
                         the row's timestamp

False-positive note, on purpose: only PDF rows are cross-checked. The
plugin's EPUB "pages" are word-count ESTIMATES by design (its own
algorithms), so a disagreement there is the plugin working as built,
not a finding. A re-download legitimately changes both the real count
and the file: it still surfaces here, because a page-count change
without a re-scan is exactly the drift this tool exists to catch.

Read-only: metadata.db opens mode=ro through cquarry, pdfinfo reads
the files in place, nothing is written. Runtime: one pdfinfo call per
PDF row (thousands of rows = minutes); scope with --search/--ids for
interactive use. The EPUB truncated-tail half of this check belongs to
bindery's analyzers (recorded routing). Exit codes:
    0 = clean, 1 = findings, 2 = setup error

Usage:
    python3 audit_truncation.py [library_dir_or_metadata.db]
        [--search EXPR | --ids ID[,ID...]] [--tolerance 0.20] [--quiet]
        [--format {text,json}]
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TOLERANCE = 0.20


def real_pdf_pages(path: Path) -> int | None:
    try:
        out = subprocess.run(
            ["pdfinfo", str(path)],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
    except OSError, subprocess.SubprocessError:
        return None
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def stale_reasons(meta: dict, fpath: Path) -> list[str]:
    """Why the row no longer describes the file (no mtime lies told)."""
    reasons = []
    if meta.get("needs_scan"):
        reasons.append("needs_scan flagged by the plugin")
    actual_size = fpath.stat().st_size if fpath.exists() else None
    claimed_size = meta.get("format_size")
    if actual_size is not None and claimed_size and actual_size != claimed_size:
        reasons.append(f"format_size {claimed_size} != file {actual_size}")
    ts = _parse_ts(meta.get("timestamp") or "")
    if ts is not None and fpath.exists():
        mtime = datetime.fromtimestamp(fpath.stat().st_mtime, tz=timezone.utc)
        if mtime > ts:
            reasons.append("file modified after the scan")
    return reasons


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cross-check Count Pages data against the real PDFs."
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
        "--tolerance",
        type=float,
        default=TOLERANCE,
        help=f"max relative page-count disagreement (default: {TOLERANCE})",
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
        page_meta = db.get_page_metadata()
        titles = {
            b["id"]: (b["title"] or "", b["author_sort"] or "")
            for b in db.get_all_books()
        }
        # The page row carries no file path; resolve each PDF's path
        # through the data table while the connection is open.
        pdf_paths: dict[int, Path] = {}
        for bid, meta in page_meta.items():
            if id_filter is not None and bid not in id_filter:
                continue
            if (meta.get("format") or "").upper() != "PDF":
                continue
            try:
                pdf_paths[bid] = Path(db.get_formats(bid)["PDF"]["path"])
            except KeyError, TypeError:
                pass

    findings: list[dict] = []
    checked = 0
    skipped = 0
    for bid, meta in sorted(page_meta.items()):
        if bid not in pdf_paths:
            continue
        if id_filter is not None and bid not in id_filter:
            continue
        pages_claimed = meta.get("pages")
        if not pages_claimed:
            continue
        fpath = pdf_paths[bid]
        if not fpath.exists():
            skipped += 1
            continue
        checked += 1
        real = real_pdf_pages(fpath)
        if real is None:
            skipped += 1
            continue
        drift = abs(real - pages_claimed) / max(real, 1)
        if drift > args.tolerance:
            findings.append(
                {
                    "book": bid,
                    "class": "page_count_mismatch",
                    "detail": f"claimed {pages_claimed}, real {real} "
                    f"({drift * 100:.0f}% off)",
                }
            )
        for reason in stale_reasons(meta, fpath):
            findings.append(
                {
                    "book": bid,
                    "class": "stale_plugin_data",
                    "detail": reason,
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
            f"Count Pages data matches the real PDFs ({checked} checked, "
            f"{skipped} skipped: missing files or unreadable PDFs)."
        )
        return 0

    print(
        f"{len(findings)} finding(s) across {checked} PDF page rows "
        f"({skipped} skipped):\n"
    )
    last_book = None
    for f in findings:
        if f["book"] != last_book:
            title, author = titles.get(f["book"], ("?", ""))
            if not args.quiet:
                print(f"  #{f['book']} {title} \u2014 {author}")
            last_book = f["book"]
        if args.quiet:
            print(f["book"])
        else:
            print(f"    [{f['class']}] {f['detail']}")
    if not args.quiet:
        print(
            "\nA mismatch means the file changed after the Count Pages "
            "scan: re-run the plugin, or treat the book as truncated."
        )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
