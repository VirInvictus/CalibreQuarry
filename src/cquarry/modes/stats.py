from __future__ import annotations

from collections import Counter

from cquarry.db import CalibreDB
from cquarry.helpers import calibre_rating_to_stars, normalize_author_display


def show_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display library statistics."""
    books = db.get_all_books()
    total = len(books)

    if not quiet:
        print(f"=== Library Statistics ({total} books) ===\n")

    # Format breakdown
    format_counts: Counter = Counter()
    for b in books:
        if b['formats']:
            for fmt in b['formats'].split(','):
                format_counts[fmt.strip()] += 1
    print("Formats:")
    for fmt, count in format_counts.most_common():
        bar = "\u2588" * (count * 40 // total) if total else ""
        print(f"  {fmt:6s} {count:5d}  {bar}")

    # Rating distribution
    print("\nRatings:")
    rated = 0
    rating_counts: Counter = Counter()
    for b in books:
        stars = calibre_rating_to_stars(b['rating'])
        if stars is not None:
            rating_counts[stars] += 1
            rated += 1
    for stars in sorted(rating_counts.keys()):
        count = rating_counts[stars]
        max_count = max(rating_counts.values())
        bar = "\u2588" * (count * 40 // max_count) if max_count else ""
        star_str = "\u2605" * int(stars)
        print(f"  {star_str:5s} ({stars:.1f})  {count:5d}  {bar}")
    unrated = total - rated
    pct = f"{unrated * 100 / total:.1f}%" if total else "N/A"
    print(f"  Unrated:       {unrated:5d}  ({pct})")

    # Tag taxonomy
    tags = db.get_all_tags()
    top_level: Counter = Counter()
    for t in tags:
        prefix = t.split('.')[0] if '.' in t else t
        top_level[prefix] += 1
    print(f"\nTag taxonomy ({len(tags)} tags):")
    for prefix, count in top_level.most_common():
        print(f"  {prefix}: {count} tags")

    # Series
    all_series = db.get_all_series()
    print(f"\nSeries: {len(all_series)} series tracked")
    print("Largest:")
    for s in sorted(all_series, key=lambda x: x['book_count'], reverse=True)[:10]:
        print(f"  {s['name']}: {s['book_count']} books")

    # Publisher
    pub_counts: Counter = Counter()
    for b in books:
        if b['publisher']:
            pub_counts[b['publisher']] += 1
    print(f"\nPublishers: {len(pub_counts)} distinct")
    print("Most represented:")
    for pub, count in pub_counts.most_common(10):
        print(f"  {pub}: {count}")

    # Language
    lang_counts: Counter = Counter()
    for b in books:
        if b['languages']:
            for lang in b['languages'].split(','):
                lang_counts[lang.strip()] += 1
    if len(lang_counts) > 1:
        print("\nLanguages:")
        for lang, count in lang_counts.most_common():
            print(f"  {lang}: {count}")

    # Cover status
    no_cover = sum(1 for b in books if not b['has_cover'])
    if no_cover:
        print(f"\nMissing covers: {no_cover}")

    # Recently added
    if not quiet:
        print("\nMost recently added:")
        by_date = sorted(books, key=lambda b: b['timestamp'] or '', reverse=True)
        for b in by_date[:5]:
            date = (b['timestamp'] or '')[:10]
            author = normalize_author_display(b['authors'], primary_only=True)
            print(f"  [{date}] {author} \u2014 {b['title']}")
