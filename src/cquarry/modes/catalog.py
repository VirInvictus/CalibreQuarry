from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Optional

from cquarry.db import CalibreDB
from cquarry.helpers import (
    author_sort_key,
    calibre_rating_to_stars,
    format_stars,
    normalize_author_display,
)


def write_catalog(db: CalibreDB, output: str, *,
                  wing: Optional[str] = None, primary_only: bool = False,
                  show_tags: bool = False, show_id: bool = False,
                  quiet: bool = False) -> None:
    """Write a formatted text catalog, optionally filtered to a virtual library."""
    books = db.get_all_books()

    if wing:
        try:
            valid_ids = db.resolve_vl(wing)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return
        books = [b for b in books if b['id'] in valid_ids]
        if not quiet:
            print(f"Wing '{wing}': {len(books)} books")

    if not books:
        if not quiet:
            print("No books found.")
        return

    books.sort(key=lambda b: (author_sort_key(b['author_sort'], primary_only), b['title_sort'] or ''))

    with open(output, 'w', encoding='utf-8') as f:
        header = f"Calibre Library Export \u2014 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        if wing:
            header += f" [{wing}]"
        f.write(header + "\n")
        f.write("=" * len(header) + "\n\n")

        current_author_key = None
        book_count = 0

        for book in books:
            author_display = normalize_author_display(book['authors'], primary_only)
            key = author_sort_key(book['author_sort'], primary_only)

            if key != current_author_key:
                if current_author_key is not None:
                    f.write("\n")
                f.write(f"[{author_display}]\n")
                f.write("-" * (len(author_display) + 2) + "\n")
                current_author_key = key

            title = book['title'] or 'Unknown Title'

            if show_tags:
                tag_list = [t.strip() for t in (book['tags'] or '').split(',') if t.strip()]
                meta_str = f" [{', '.join(tag_list)}]" if tag_list else ""
            else:
                rating = calibre_rating_to_stars(book['rating'])
                meta_str = format_stars(rating)

            series_str = ""
            if book['series']:
                idx = book['series_index']
                if idx is not None and idx == int(idx):
                    series_str = f" ({book['series']} #{int(idx)})"
                elif idx is not None:
                    series_str = f" ({book['series']} #{idx})"
                else:
                    series_str = f" ({book['series']})"

            formats = book['formats'] or ''
            fmt_str = f" [{formats}]" if formats else ""

            id_str = f"[{book['id']}] " if show_id else ""
            f.write(f"  * {id_str}{title}{series_str}{fmt_str}{meta_str}\n")
            book_count += 1

        f.write(f"\n{'=' * 40}\n")
        f.write(f"Total: {book_count} books\n")

    if not quiet:
        print(f"Catalog written: {output} ({book_count} books)")


def write_all_wings(db: CalibreDB, outdir: str, *, primary_only: bool = False,
                    show_tags: bool = False, show_id: bool = False,
                    quiet: bool = False) -> None:
    """Generate a catalog file for each virtual library wing."""
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.", file=sys.stderr)
        return

    os.makedirs(outdir, exist_ok=True)

    for name in sorted(vls.keys()):
        safe_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_Library.txt")
        if not quiet:
            print(f"\u2192 {name}")
        write_catalog(db, output, wing=name, primary_only=primary_only,
                      show_tags=show_tags, show_id=show_id, quiet=True)

    if not quiet:
        print(f"\nAll wings written to: {outdir}")
