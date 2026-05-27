from cquarry.db import CalibreDB
from cquarry.helpers import C_TITLE, color


def show_tag_dump(db: CalibreDB, *, quiet: bool = False) -> None:
    """Print every tag in the library with its book count, alphabetically."""
    rows = db.get_tag_counts()

    if not rows:
        print("No tags in this library.")
        return

    if not quiet:
        print(color(f"=== Library Tags ({len(rows)} total) ===", C_TITLE))
        print()

    width = max(len(name) for name, _ in rows)
    total_links = 0
    for name, count in rows:
        total_links += count
        print(f"  {name:<{width}}  {count}")

    if not quiet:
        print()
        print(f"  {len(rows)} tags, {total_links} tagged book-instances")
