"""Single-book dossier (--book ID).

Composes cquarry's read APIs into one view: the hydrated row, identifiers,
per-format files (path + catalogued size), cover, pages, comments (HTML
stripped for the terminal), custom columns, e-reader annotations, reading
positions, plugin data, and conversion overrides.
"""

import json
import os
import sys
import textwrap

from cquarry.db import CalibreDB
from cquarry.helpers import (
    C_DIM,
    C_HEADER,
    C_WARN,
    calibre_rating_to_stars,
    color,
    format_stars,
    normalize_author_display,
    strip_html,
)

_ANNOT_PREVIEW = 8
_TEXT_WIDTH = 78


def _human_size(n) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GiB"


def _fmt_custom(value) -> str:
    if isinstance(value, (list, tuple, set)):
        return ", ".join(str(v) for v in value)
    return str(value)


def _show_identifiers(b) -> None:
    idents = b.get("identifiers") or {}
    if not idents:
        return
    print(color("Identifiers:", C_HEADER))
    for id_type, val in sorted(idents.items()):
        print(f"  {id_type}: {val}")


def _show_formats(db, b) -> None:
    fmts = db.get_formats(b["id"])
    if not fmts:
        return
    print(color("Formats:", C_HEADER))
    for fmt in sorted(fmts):
        info = fmts[fmt]
        size = _human_size(info.get("size_bytes"))
        note = (
            color("  [file missing on disk]", C_WARN)
            if not os.path.exists(info["path"])
            else ""
        )
        print(f"  {fmt:<6} {info.get('name')}.{fmt.lower()} ({size}){note}")
        print(color(f"         {info['path']}", C_DIM))


def _show_comments(db, b) -> None:
    try:
        raw = db.field(b["id"], "comments")
    except Exception:
        return
    text = strip_html(raw or "")
    if not text.strip():
        return
    print(color("Comments:", C_HEADER))
    for para in text.splitlines() or [text]:
        para = para.strip()
        if para:
            print(
                textwrap.fill(
                    para,
                    width=_TEXT_WIDTH,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )


def _show_custom(db, b) -> None:
    cols = db.get_custom_columns()
    if not cols:
        return
    rows = []
    for name, meta in cols.items():
        try:
            value = db.field(b["id"], "#" + meta["label"])
        except Exception:
            continue
        if value is None or value == "" or value == []:
            continue
        rows.append((meta["label"], name, value))
    if not rows:
        return
    print(color("Custom columns:", C_HEADER))
    for label, name, value in sorted(rows):
        print(f"  {name} (#{label}): {_fmt_custom(value)}")


def _show_annotations(db, b) -> None:
    annots = db.get_annotations(b["id"])
    if not annots:
        return
    print(color(f"Annotations ({len(annots)}):", C_HEADER))
    for row in annots[:_ANNOT_PREVIEW]:
        ts = (str(row.get("timestamp") or ""))[:10] or "?"
        kind = row.get("annot_type") or "note"
        data = row.get("annot_data")
        if isinstance(data, dict):
            snippet = data.get("text") or data.get("note") or json.dumps(data)
        else:
            snippet = str(data or "")
        snippet = " ".join(str(snippet).split())[:100]
        suffix = f": {snippet}" if snippet else ""
        print(f"  [{ts}] {kind}{suffix}")
    if len(annots) > _ANNOT_PREVIEW:
        print(color(f"  … {len(annots) - _ANNOT_PREVIEW} more", C_DIM))


def _show_progress(db, b) -> None:
    positions = db.get_last_read_positions(b["id"])
    if not positions:
        return
    print(color("Reading progress:", C_HEADER))
    for row in positions:
        frac = row.get("pos_frac")
        pct = f"{frac * 100:.0f}%" if isinstance(frac, (int, float)) else "?"
        epoch = row.get("epoch")
        when = ""
        if epoch:
            from datetime import datetime

            when = f" at {datetime.fromtimestamp(epoch):%Y-%m-%d}"
        who = row.get("device") or row.get("user") or "?"
        print(f"  {who} ({row.get('format') or '?'}) — {pct}{when}")


def _show_extras(db, b) -> None:
    plugin_rows = db.get_plugin_data(book_id=b["id"])
    if plugin_rows:
        print(color("Plugin data:", C_HEADER))
        for row in plugin_rows:
            val = " ".join(str(row.get("val") or "").split())[:80]
            print(f"  {row['name']}: {val}")

    overrides = db.get_conversion_profiles(b["id"])
    if overrides:
        print(color("Conversion overrides:", C_HEADER))
        for row in overrides:
            print(
                f"  {row['format']} — manual override "
                f"({row.get('data_size', 0)} bytes of recipe data)"
            )


def show_book(db: CalibreDB, book_id: int, *, quiet: bool = False) -> bool:
    """Print the full record for one book. Returns False for unknown ids."""
    b = db.get_book(book_id)
    if b is None:
        print(f"ERROR: no book with id {book_id} in this library.", file=sys.stderr)
        return False

    authors = normalize_author_display(b["authors"]) if b["authors"] else "(no author)"
    series = ""
    if b["series"]:
        idx = b["series_index"]
        idx_str = f" #{idx:g}" if idx is not None else ""
        series = f" [{b['series']}{idx_str}]"

    if not quiet:
        print(color(f"=== Book {b['id']} ===", C_HEADER))
        print()
    print(f"{b['title']}{series}")
    print(color(f"by {authors}", C_DIM))

    print()
    facts = []
    stars = calibre_rating_to_stars(b["rating"])
    if stars is not None:
        facts.append(f"rating {format_stars(stars)}")
    if b["publisher"]:
        facts.append(f"publisher {b['publisher']}")
    if b["languages"]:
        facts.append(f"languages {', '.join(b['languages'])}")
    if b.get("pages") is not None:
        facts.append(f"{b['pages']} pages")
    if b.get("size") is not None:
        facts.append(_human_size(b["size"]))
    facts.append(f"added {(b['timestamp'] or '?')[:10]}")
    facts.append(f"modified {(b['last_modified'] or '?')[:10]}")
    if b["has_cover"]:
        cover = db.get_cover_path(b["id"])
        facts.append("cover on disk" if cover else "cover catalogued (file missing)")
    else:
        facts.append("no cover")
    for line in textwrap.wrap(", ".join(facts), width=_TEXT_WIDTH):
        print(f"  {line}")

    if b["tags"]:
        print()
        print(f"  Tags: {', '.join(b['tags'])}")

    print()
    _show_identifiers(b)
    _show_formats(db, b)
    _show_custom(db, b)
    _show_comments(db, b)
    _show_annotations(db, b)
    _show_progress(db, b)
    _show_extras(db, b)

    if not quiet:
        print()
        print(color("Provenance:", C_HEADER))
        print(f"  uuid: {b.get('uuid') or '?'}")
        print(f"  library: {db.db_path}")
    return True
