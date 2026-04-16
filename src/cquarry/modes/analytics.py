from __future__ import annotations

import sys
from collections import Counter, defaultdict

from cquarry.db import CalibreDB
from cquarry.helpers import calibre_rating_to_stars, normalize_author_display


def show_author_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display per-author breakdowns."""
    books = db.get_all_books()
    
    author_data = defaultdict(lambda: {
        'count': 0,
        'ratings': [],
        'formats': set(),
        'series': set()
    })

    for b in books:
        if b['authors']:
            author = normalize_author_display(b['authors'], primary_only=True)
            ad = author_data[author]
            ad['count'] += 1
            stars = calibre_rating_to_stars(b['rating'])
            if stars is not None:
                ad['ratings'].append(stars)
            if b['formats']:
                ad['formats'].update(f.strip() for f in b['formats'].split(','))
            if b['series']:
                ad['series'].add(b['series'])

    if not quiet:
        print(f"=== Author Statistics ({len(author_data)} authors) ===\n")
    
    # Sort by book count descending, then name
    sorted_authors = sorted(author_data.items(), key=lambda x: (-x[1]['count'], x[0]))

    for author, ad in sorted_authors:
        avg_rating = sum(ad['ratings']) / len(ad['ratings']) if ad['ratings'] else 0.0
        rating_str = f"avg rating: {avg_rating:.1f}" if ad['ratings'] else "unrated"
        formats_str = ", ".join(sorted(ad['formats']))
        series_count = len(ad['series'])
        series_str = f"{series_count} series" if series_count else "no series"
        
        print(f"[{author}]")
        print(f"  Books:   {ad['count']}")
        print(f"  Ratings: {rating_str} ({len(ad['ratings'])} rated)")
        print(f"  Formats: {formats_str}")
        print(f"  Series:  {series_str}")
        print()


def show_pace_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Show books added per month/year trend."""
    books = db.get_all_books()
    
    pace = defaultdict(int)
    for b in books:
        if b['timestamp']:
            ym = b['timestamp'][:7]  # YYYY-MM
            pace[ym] += 1
            
    if not quiet:
        print(f"=== Reading Pace Statistics ===\n")
        
    if not pace:
        print("No timestamp data available.")
        return

    max_count = max(pace.values())
    for ym in sorted(pace.keys()):
        count = pace[ym]
        bar_len = (count * 40) // max_count if max_count else 0
        bar = "\u2588" * bar_len
        print(f"  {ym}: {count:4d}  {bar}")


def show_tag_tree(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display the full hierarchical tag taxonomy as a tree."""
    tags = db.get_all_tags()
    
    if not quiet:
        print(f"=== Tag Taxonomy Tree ===\n")
        
    tree = {}
    for tag in tags:
        parts = tag.split('.')
        current = tree
        for part in parts:
            if part not in current:
                current[part] = {}
            current = current[part]
            
    def _print_tree(node, indent=0):
        for key in sorted(node.keys()):
            print("  " * indent + f"\u2514\u2500 {key}")
            _print_tree(node[key], indent + 1)
            
    _print_tree(tree, indent=1)


def show_wing_overlap(db: CalibreDB, *, quiet: bool = False) -> None:
    """Show which books appear in multiple virtual libraries."""
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.", file=sys.stderr)
        return
        
    book_wings = defaultdict(list)
    for name in vls.keys():
        try:
            ids = db.resolve_vl(name)
            for bid in ids:
                book_wings[bid].append(name)
        except Exception:
            pass  # ignore unparseable
            
    overlap_counts = Counter()
    for bid, wings in book_wings.items():
        if len(wings) > 1:
            overlap_counts[tuple(sorted(wings))] += 1
            
    if not quiet:
        print(f"=== Wing Overlap Analysis ===\n")
        
    if not overlap_counts:
        print("No overlaps found between virtual libraries.")
        return
        
    for wings, count in overlap_counts.most_common():
        wings_str = " + ".join(wings)
        print(f"  {count:4d} books in: {wings_str}")
