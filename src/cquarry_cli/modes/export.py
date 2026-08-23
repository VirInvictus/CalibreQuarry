import csv
import json
import os
import sys
from contextlib import contextmanager

from cquarry.db import CalibreDB
from cquarry.helpers import calibre_rating_to_stars

_CSV_FIELDS = [
    "id",
    "title",
    "authors",
    "author_sort",
    "tags",
    "series",
    "series_index",
    "formats",
    "rating",
    "publisher",
    "languages",
    "added",
    "has_cover",
]


def _load_custom(db: CalibreDB, show_custom: str | None) -> dict | None:
    """Load a custom column, or {} if none requested. None signals an error."""
    if not show_custom:
        return {}
    try:
        return db.load_custom_column(show_custom)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return None


@contextmanager
def _open_out(output: str | None):
    """Yield (stream, path). A falsy output streams to stdout (path is None)."""
    if output:
        out_path = os.path.abspath(output)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        f = open(out_path, "w", newline="", encoding="utf-8")
        try:
            yield f, out_path
        finally:
            f.close()
    else:
        yield sys.stdout, None


def _book_to_dict(b, custom_data, show_custom) -> dict:
    d = {
        "id": b["id"],
        "title": b["title"],
        "authors": [a.strip() for a in b["authors"].split(",")] if b["authors"] else [],
        "author_sort": b["author_sort"],
        "tags": [t.strip() for t in b["tags"].split(",")] if b["tags"] else [],
        "series": b["series"],
        "series_index": b["series_index"],
        "formats": [f.strip() for f in b["formats"].split(",")] if b["formats"] else [],
        "rating": calibre_rating_to_stars(b["rating"]),
        "publisher": b["publisher"],
        "languages": [lang.strip() for lang in b["languages"].split(",")]
        if b["languages"]
        else [],
        "added": (b["timestamp"] or "")[:10],
        "has_cover": bool(b["has_cover"]),
    }
    if show_custom:
        d[show_custom] = custom_data.get(b["id"])
    return d


def _serialize(books, stream, fmt, custom_data, show_custom) -> bool:
    """Write books to a stream as json/csv/ai. Returns False for unknown fmt."""
    if fmt == "json":
        json.dump(
            [_book_to_dict(b, custom_data, show_custom) for b in books],
            stream,
            indent=2,
            ensure_ascii=False,
        )
        stream.write("\n")
    elif fmt == "csv":
        fieldnames = list(_CSV_FIELDS)
        if show_custom:
            fieldnames.append(show_custom)
        w = csv.DictWriter(stream, fieldnames=fieldnames)
        w.writeheader()
        for b in books:
            stars = calibre_rating_to_stars(b["rating"])
            row = {
                "id": b["id"],
                "title": b["title"],
                "authors": b["authors"] or "",
                "author_sort": b["author_sort"] or "",
                "tags": b["tags"] or "",
                "series": b["series"] or "",
                "series_index": b["series_index"]
                if b["series_index"] is not None
                else "",
                "formats": b["formats"] or "",
                "rating": stars if stars is not None else "",
                "publisher": b["publisher"] or "",
                "languages": b["languages"] or "",
                "added": (b["timestamp"] or "")[:10],
                "has_cover": b["has_cover"],
            }
            if show_custom:
                row[show_custom] = custom_data.get(b["id"], "")
            w.writerow(row)
    elif fmt == "ai":
        for b in books:
            line = []
            if b["title"]:
                line.append(b["title"])
            if b["author_sort"]:
                line.append(f"by {b['author_sort']}")
            if b["series"]:
                idx = f" #{b['series_index']}" if b["series_index"] is not None else ""
                line.append(f"({b['series']}{idx})")
            if b["tags"]:
                line.append(f"[{b['tags']}]")
            stars = calibre_rating_to_stars(b["rating"])
            if stars is not None:
                line.append(f"{stars}/5")
            if show_custom:
                val = custom_data.get(b["id"])
                if val:
                    line.append(f"<{show_custom}: {val}>")
            stream.write(" ".join(line) + "\n")
    else:
        return False
    return True


def run_export(
    db: CalibreDB,
    output: str,
    fmt: str = "json",
    *,
    show_custom: str | None = None,
    quiet: bool = False,
) -> None:
    """Export full library to JSON, CSV, or AI-readable format."""
    books = db.get_all_books()
    custom_data = _load_custom(db, show_custom)
    if custom_data is None:
        return

    with _open_out(output) as (stream, out_path):
        if not _serialize(books, stream, fmt, custom_data, show_custom):
            print(
                f"Unknown format: {fmt}. Use 'json', 'csv', or 'ai'.", file=sys.stderr
            )
            return

    if not quiet:
        dest = out_path or "stdout"
        print(
            f"Exported {len(books)} books to: {dest}",
            file=sys.stdout if out_path else sys.stderr,
        )


def run_search_export(
    db: CalibreDB,
    query: str,
    output: str | None = None,
    *,
    fmt: str | None = None,
    show_custom: str | None = None,
    quiet: bool = False,
) -> None:
    """Evaluate a search query and write matching books.

    Writes to ``output`` if given, otherwise to stdout. With ``fmt`` (json/csv/
    ai) the matches are serialized in that structured format; otherwise a plain
    text listing is produced. An empty query matches the whole library.
    """
    try:
        matching_ids = db.search(query)
    except Exception as e:
        print(f"Error parsing search query: {e}", file=sys.stderr)
        return

    if not matching_ids:
        print(
            f"No books matched the query: '{query}'. Nothing written.",
            file=sys.stderr,
        )
        return

    books = [b for b in db.get_all_books() if b["id"] in matching_ids]
    custom_data = _load_custom(db, show_custom)
    if custom_data is None:
        return

    with _open_out(output) as (stream, out_path):
        if fmt in ("json", "csv", "ai"):
            _serialize(books, stream, fmt, custom_data, show_custom)
        else:
            stream.write(f"Search Query: {query}\n")
            stream.write(f"Matches: {len(books)}\n")
            stream.write("=" * 40 + "\n\n")
            for b in books:
                author = b["author_sort"] or "Unknown"
                title = b["title"] or "Untitled"
                custom_str = ""
                if show_custom:
                    val = custom_data.get(b["id"])
                    if val:
                        custom_str = f" <{show_custom}: {val}>"
                stream.write(f"  * {title} - {author}{custom_str}\n")

    if not quiet:
        if out_path:
            print(f"Exported {len(books)} matches to: {out_path}")
        else:
            print(f"\n{len(books)} matches.", file=sys.stderr)
