import sys
from collections import Counter, defaultdict
from datetime import date
from statistics import mean, median

from cquarry.analytics import (
    addition_timeline,
    author_stats,
    genre_distribution,
    vl_overlap,
)
from cquarry.db import CalibreDB
from cquarry.helpers import (
    C_DIM,
    C_HEADER,
    color,
    normalize_author_display,
    tags_to_tree,
)


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
        rating_str = (
            f"avg rating: {s['avg_rating']:.1f}" if s["rated_count"] else "unrated"
        )
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


def show_genre_breakdown(db: CalibreDB, *, depth: int = 1, quiet: bool = False) -> None:
    """Show the first `depth` levels of the tag hierarchy as library shares.

    Rendered over cquarry.analytics' genre_distribution (which already
    carries every node of the hierarchy, tree-ordered): level 1 is the
    genre roots, deeper levels indent under their parents with the last
    path segment as the label. Every level is a share of the whole
    library, not of its parent, so children need not sum to their parent.
    """
    dist = genre_distribution(db)

    if not quiet:
        print(f"=== Genre Breakdown ({db.count_books()} books) ===\n")

    if not dist:
        print("No books in this library.")
        return

    rows = [
        (name.count(".") + 1, name.rpartition(".")[2], share)
        for name, share in dist.items()
        if name.count(".") + 1 <= depth
    ]
    if not rows:
        print(f"Nothing to show at depth {depth}.")
        return
    width = max(len(label) for level, label, share in rows)
    max_share = rows[0][2]  # tree order: biggest root first
    for level, label, share in rows:
        bar_len = int(share * 40 / max_share) if max_share else 0
        bar = "\u2588" * bar_len
        print(f"{'  ' * level}{label:<{width}}  {share * 100:5.1f}%  {bar}")
    print()
    print("  Multi-genre books count once per genre, so shares can sum over 100%.")


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


def _find_column(db: CalibreDB, label: str):
    for meta in db.get_custom_columns().values():
        if meta.get("label") == label:
            return meta
    return None


def _parse_date(value) -> date | None:
    if value is None:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def show_reading_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Reading analytics over #reading_status and #date_read.

    READ-ONLY by charter: the library NON-NEGOTIABLES ban writing these
    columns, and nothing here writes anything. The status funnel follows
    the column's configured enum order (so it renders the funnel the
    way Calibre's editor defines it); days-to-read measures each
    finished book's added-to-finished span (books.timestamp is the
    date the book entered the library, the honest denominator this
    report has). Under --restrict every section scopes to the view.
    """
    books = db.get_all_books()
    total = len(books)
    if not quiet:
        print(f"=== Reading Analytics ({total} books) ===\n")

    status_meta = _find_column(db, "reading_status")
    date_meta = _find_column(db, "date_read")
    if status_meta is None and date_meta is None:
        print(
            "No #reading_status or #date_read column in this library; "
            "nothing to report."
        )
        return

    # --- the status funnel ---
    if status_meta is not None:
        try:
            values = db.load_custom_column("#" + status_meta["label"])
        except ValueError:
            values = {}
        enum_order = [
            str(v) for v in (status_meta.get("display") or {}).get("enum_values") or []
        ]
        counts: Counter = Counter()
        for b in books:
            raw = values.get(b["id"])
            text = str(raw).strip() if raw is not None else ""
            counts[text or "(no status)"] += 1
        known = [v for v in enum_order if counts.get(v)]
        unknown = [v for v in counts if v not in enum_order and v != "(no status)"]
        order = (
            known
            + sorted(unknown)
            + (["(no status)"] if counts.get("(no status)") else [])
        )
        if not quiet:
            print(color("Reading status funnel:", C_HEADER))
        for label in order:
            count = counts[label]
            pct = f"{count * 100 / total:5.1f}%" if total else "  N/A"
            bar = "\u2588" * (count * 40 // total) if total else ""
            print(f"  {label:<20} {count:5d}  {pct}  {bar}")
        if not quiet:
            print()

    # --- the finish dates ---
    if date_meta is not None:
        try:
            finished_raw = db.load_custom_column("#" + date_meta["label"])
        except ValueError:
            finished_raw = {}
        finished = []
        for b in books:
            parsed = _parse_date(finished_raw.get(b["id"]))
            if parsed is not None:
                finished.append((parsed, b))
        finished.sort(key=lambda pair: pair[0], reverse=True)
        if not quiet:
            print(
                color(
                    f"Recently finished ({len(finished)} with #date_read):",
                    C_HEADER,
                )
            )
        for parsed, b in finished[:15]:
            author = normalize_author_display(b["authors"], primary_only=True)
            print(f"  {parsed.isoformat()}  {b['title']} \u2014 {author}")
        if len(finished) > 15 and not quiet:
            print(f"  ... and {len(finished) - 15} more")
        if not finished and not quiet:
            print("  (no books carry a #date_read value)")
        if not quiet:
            print()

        spans = []
        for parsed, b in finished:
            added = _parse_date(b["timestamp"])
            if added is not None:
                spans.append((parsed - added).days)
        if spans and not quiet:
            print(color("Days from added to finished:", C_HEADER))
            print(
                f"  median {median(spans):.0f}   mean {mean(spans):.1f}   "
                f"min {min(spans)}   max {max(spans)}   ({len(spans)} books)"
            )
            negative = sum(1 for s in spans if s < 0)
            if negative:
                print(
                    color(
                        f"  {negative} book(s) finished before their added "
                        "date (pre-library reads or a stale timestamp).",
                        C_DIM,
                    )
                )
            print(
                color(
                    "  The span uses the date each book entered the "
                    "library, not the date reading started.",
                    C_DIM,
                )
            )
