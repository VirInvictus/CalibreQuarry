import sys
from collections import Counter, defaultdict

from cquarry.analytics import addition_timeline, author_stats, vl_overlap
from cquarry.db import CalibreDB
from cquarry.helpers import normalize_author_display, tags_to_tree


def show_author_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display per-author breakdowns, rendered over cquarry.analytics."""
    stats = author_stats(db)

    if not quiet:
        print(f"=== Author Statistics ({len(stats)} authors) ===\n")

    # The series count stays frontend-side: it is a rendering detail the
    # shared module does not carry (per the mine-site waivers). One pass.
    series_by_author: dict[str, set[str]] = defaultdict(set)
    for b in db.get_all_books():
        if b["authors"] and b["series"]:
            author = normalize_author_display(b["authors"], primary_only=True)
            series_by_author[author].add(b["series"])

    for s in stats:
        series = series_by_author.get(s["author"], set())
        rating_str = f"avg rating: {s['avg_rating']:.1f}" if s["rated_count"] else "unrated"
        formats_str = ", ".join(s["formats"])
        series_str = f"{len(series)} series" if series else "no series"

        print(f"[{s['author']}]")
        print(f"  Books:   {s['book_count']}")
        print(f"  Ratings: {rating_str} ({s['rated_count']} rated)")
        print(f"  Formats: {formats_str}")
        print(f"  Series:  {series_str}")
        print()


def show_pace_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Show books added per month/year trend, rendered over cquarry.analytics."""
    pace = addition_timeline(db)

    if not quiet:
        print("=== Reading Pace Statistics ===\n")

    if not pace:
        print("No timestamp data available.")
        return

    max_count = max(pace.values())
    for ym, count in pace.items():  # already chronological
        bar_len = (count * 40) // max_count if max_count else 0
        bar = "\u2588" * bar_len
        print(f"  {ym}: {count:4d}  {bar}")


def show_tag_tree(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display the full hierarchical tag taxonomy as a tree."""
    tags = db.get_all_tags()

    if not quiet:
        print("=== Tag Taxonomy Tree ===\n")

    # cquarry's shared builder: one taxonomy parser for the whole ecosystem.
    tree = tags_to_tree(tags)

    def _print_tree(node, indent=0):
        for key in sorted(node.keys()):
            print("  " * indent + "\u2514\u2500 " + key)
            _print_tree(node[key], indent + 1)

    _print_tree(tree, indent=1)


def show_wing_overlap(db: CalibreDB, *, quiet: bool = False) -> None:
    """Show which books appear in multiple virtual libraries.

    The derivation lives in cquarry.analytics now; this renders it. Unparseable
    wings are skipped exactly as before (probed via resolve_vl first).
    """
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.", file=sys.stderr)
        return

    usable = []
    for name in sorted(vls):
        try:
            db.resolve_vl(name)
            usable.append(name)
        except Exception:
            pass  # ignore unparseable, exactly as before

    overlap_counts = Counter(
        {wings: len(ids) for wings, ids in vl_overlap(db, usable).items()}
    )

    if not quiet:
        print("=== Wing Overlap Analysis ===\n")

    if not overlap_counts:
        print("No overlaps found between virtual libraries.")
        return

    for wings, count in overlap_counts.most_common():
        wings_str = " + ".join(wings)
        print(f"  {count:4d} books in: {wings_str}")
