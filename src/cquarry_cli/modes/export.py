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
    "pages",
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
        "authors": b["authors"],
        "author_sort": b["author_sort"],
        "tags": b["tags"],
        "series": b["series"],
        "series_index": b["series_index"],
        "formats": b["formats"],
        "pages": b.get("pages"),
        "rating": calibre_rating_to_stars(b["rating"]),
        "publisher": b["publisher"],
        "languages": b["languages"],
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
                "authors": ", ".join(b["authors"]),
                "author_sort": b["author_sort"] or "",
                "tags": ", ".join(b["tags"]),
                "series": b["series"] or "",
                "series_index": b["series_index"]
                if b["series_index"] is not None
                else "",
                "formats": ", ".join(b["formats"]),
                "pages": b.get("pages") if b.get("pages") is not None else "",
                "rating": stars if stars is not None else "",
                "publisher": b["publisher"] or "",
                "languages": ", ".join(b["languages"]),
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
            if b.get("pages"):
                line.append(f"{b['pages']}p")
            if b["tags"]:
                line.append(f"[{', '.join(b['tags'])}]")
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

    if fmt not in ("json", "csv", "ai"):
        print(f"Unknown format: {fmt}. Use 'json', 'csv', or 'ai'.", file=sys.stderr)
        return

    with _open_out(output) as (stream, out_path):
        _serialize(books, stream, fmt, custom_data, show_custom)

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
    plugin_data: str | None = None,
    quiet: bool = False,
) -> None:
    """Evaluate a search query and write matching books.

    Writes to ``output`` if given, otherwise to stdout. With ``fmt`` (json/csv/
    ai) the matches are serialized in that structured format; otherwise a plain
    text listing is produced. An empty query matches the whole library. With
    ``plugin_data`` (a ``books_plugin_data`` name such as ``goodreads_id`` or
    ``wordcount``), each plain-text line gains a ``<name: value>`` segment.
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
    plugin_map: dict[int, str] = {}
    if plugin_data:
        plugin_map = {
            row["book"]: row["val"]
            for row in db.get_plugin_data(name=plugin_data)
            if row.get("val")
        }

    if fmt is not None and fmt not in ("json", "csv", "ai"):
        print(f"Unknown format: {fmt}. Use 'json', 'csv', or 'ai'.", file=sys.stderr)
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
                plugin_str = ""
                if plugin_data and b["id"] in plugin_map:
                    plugin_str = f" <{plugin_data}: {plugin_map[b['id']]}>"
                stream.write(f"  * {title} - {author}{custom_str}{plugin_str}\n")

    if not quiet:
        if out_path:
            print(f"Exported {len(books)} matches to: {out_path}")
        else:
            print(f"\n{len(books)} matches.", file=sys.stderr)


def run_annotations_export(
    db: CalibreDB,
    book_id: int | None,
    output: str | None = None,
    *,
    quiet: bool = False,
) -> int:
    """Dump e-reader highlights/bookmarks/notes as a JSON payload.

    Reads the ``annotations`` table through cquarry. With ``book_id`` only
    that book's annotations are written; otherwise every annotated book is
    included. Returns a process exit code.
    """
    annotations = db.get_annotations(book_id)
    if not annotations:
        scope = f"book {book_id}" if book_id is not None else "the library"
        print(f"No annotations found for {scope}.", file=sys.stderr)
        return 0

    # Group by book so consumers get one object per annotated title.
    by_book: dict[int, dict] = {}
    titles = {b["id"]: b for b in db.get_all_books()}
    for row in annotations:
        entry = by_book.setdefault(
            row["book"],
            {
                "book_id": row["book"],
                "title": (titles.get(row["book"], {}) or {}).get("title", ""),
                "annotations": [],
            },
        )
        entry["annotations"].append(row)

    payload = list(by_book.values())
    with _open_out(output) as (stream, out_path):
        json.dump(payload, stream, indent=2, default=str)
        stream.write("\n")

    total = sum(len(e["annotations"]) for e in payload)
    if not quiet:
        if out_path:
            print(
                f"Exported {total} annotations across {len(payload)} books to: {out_path}"
            )
        else:
            print(
                f"{total} annotations across {len(payload)} books.",
                file=sys.stderr,
            )
    return 0
