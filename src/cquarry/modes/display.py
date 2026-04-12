from __future__ import annotations

import sys

from cquarry.db import CalibreDB
from cquarry.helpers import (
    calibre_rating_to_stars,
    detect_series_gaps,
    format_stars,
    normalize_author_display,
)


def show_recent(db: CalibreDB, count: int = 20, *, quiet: bool = False) -> None:
    """Show most recently added books."""
    books = db.get_all_books()
    by_date = sorted(books, key=lambda b: b['timestamp'] or '', reverse=True)

    if not quiet:
        print(f"=== {count} Most Recently Added ===\n")
    for b in by_date[:count]:
        date = (b['timestamp'] or '')[:10]
        author = normalize_author_display(b['authors'], primary_only=True)
        rating = calibre_rating_to_stars(b['rating'])
        rating_str = format_stars(rating)
        tags = b['tags'] or ''
        tag_str = f" ({tags.split(',')[0].strip()})" if tags else ""
        series_str = ""
        if b['series']:
            idx = b['series_index']
            if idx is not None and idx == int(idx):
                series_str = f" [{b['series']} #{int(idx)}]"
            elif idx is not None:
                series_str = f" [{b['series']} #{idx}]"
            else:
                series_str = f" [{b['series']}]"

        print(f"  [{date}] {author} \u2014 {b['title']}{series_str}{tag_str}{rating_str}")


def show_series(db: CalibreDB, *, quiet: bool = False) -> None:
    """List all series with gap detection."""
    all_series = db.get_all_series()

    if not quiet:
        print(f"=== Series ({len(all_series)} total) ===\n")

    for s in sorted(all_series, key=lambda x: x['name'].lower()):
        gaps = detect_series_gaps(s['indices'], s['max_index'])
        gap_str = f"  \u26a0 missing: {', '.join(str(g) for g in gaps)}" if gaps else ""
        count = s['book_count']
        raw_max = s['max_index']
        if raw_max is not None and raw_max == int(raw_max):
            max_idx = int(raw_max)
        else:
            max_idx = raw_max

        if gaps:
            status = "incomplete"
        elif raw_max is not None and count == int(raw_max):
            status = "complete"
        else:
            status = ""

        status_str = f" ({status})" if status else ""
        print(f"  {s['name']}: {count} of {max_idx}{status_str}{gap_str}")


def show_wings(db: CalibreDB) -> None:
    """List all virtual library wings with book counts."""
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.")
        return

    print(f"=== Virtual Libraries ({len(vls)} wings) ===\n")
    for name in sorted(vls.keys()):
        try:
            ids = db.resolve_vl(name)
            print(f"  {name}: {len(ids)} books")
        except ValueError as e:
            print(f"  {name}: (error resolving: {e})")

    print(f"\n  Total library: {db.count_books()} books")
