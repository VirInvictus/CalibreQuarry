"""Library-level information modes.

``show_info`` renders the library dossier -- identity, virtual libraries
with their expressions, saved searches, @Name user categories, grouped
search terms, news feeds, conversion overrides, sync queues, and the
tag-browser state -- everything cquarry exposes about the library as a
whole. ``show_columns`` lists the custom-column schema.
"""

from cquarry.db import CalibreDB
from cquarry.helpers import C_DIM, C_HEADER, color

_LIST_PREVIEW = 6


def _resolve_counts(db: CalibreDB, vls: dict[str, str]) -> dict[str, str]:
    out = {}
    for name in vls:
        try:
            out[name] = f"{len(db.resolve_vl(name))} books"
        except ValueError:
            out[name] = "unresolvable expression"
    return out


def show_info(db: CalibreDB, *, quiet: bool = False) -> None:
    """Print the library-wide dossier."""
    if not quiet:
        print(color("=== Library Information ===", C_HEADER))
        print()

    # Identity
    print(color("Identity:", C_HEADER))
    print(f"  database: {db.db_path}")
    uuid = db.get_library_uuid()
    print(f"  uuid: {uuid or '?'}")
    print(f"  books: {db.count_books()}")

    # Virtual libraries (wings) with their defining expressions
    vls = db.get_virtual_libraries()
    print(color(f"\nVirtual libraries ({len(vls)}):", C_HEADER))
    counts = _resolve_counts(db, vls)
    for name in sorted(vls):
        print(f"  {name} [{counts[name]}]")
        print(color(f"    {vls[name]}", C_DIM))

    # Saved searches
    saved = db.get_saved_searches()
    print(color(f"\nSaved searches ({len(saved)}):", C_HEADER))
    for name in sorted(saved):
        print(f"  {name}")
        print(color(f"    {saved[name]}", C_DIM))

    # @Name user categories (searchable via @Name:query)
    user_cats = db.get_user_categories()
    print(color(f"\nUser categories ({len(user_cats)}):", C_HEADER))
    for name in sorted(user_cats):
        members = user_cats[name] or []
        names = [str(m[0] if isinstance(m, (list, tuple)) else m) for m in members]
        preview = ", ".join(names[:_LIST_PREVIEW])
        more = f" … +{len(names) - _LIST_PREVIEW}" if len(names) > _LIST_PREVIEW else ""
        print(f"  @{name}: {len(names)} members — {preview}{more}")

    # Grouped search terms (GroupName:query expansion)
    grouped = db.get_grouped_search_terms()
    print(color(f"\nGrouped search terms ({len(grouped)}):", C_HEADER))
    for name in sorted(grouped):
        print(f"  {name}: {', '.join(grouped[name])}")

    # News feeds
    feeds = db.get_feeds()
    print(color(f"\nNews feeds ({len(feeds)}):", C_HEADER))
    for feed in feeds:
        script = feed.get("script") or ""
        script_note = f" — {script}" if script else ""
        print(f"  {feed.get('title') or feed.get('id')}{script_note}")
    if not feeds:
        print(color("  (none registered)", C_DIM))

    # Conversion overrides
    overrides = db.get_conversion_profiles()
    print(color(f"\nConversion overrides ({len(overrides)}):", C_HEADER))
    for row in overrides[:_LIST_PREVIEW]:
        print(f"  book {row['book']} — {row['format']}")
    if len(overrides) > _LIST_PREVIEW:
        print(color(f"  … +{len(overrides) - _LIST_PREVIEW} more", C_DIM))
    if not overrides:
        print(color("  (none)", C_DIM))

    # Sync queues Calibre will consume on its next startup
    dirtied = db.get_dirtied_books()
    annot_dirtied = db.get_annotations_dirtied_books()
    print(color("\nSync queues:", C_HEADER))
    print(f"  metadata_dirtied (OPF resync): {len(dirtied)} book(s)")
    if dirtied:
        preview = ", ".join(str(b) for b in dirtied[:20])
        more = " …" if len(dirtied) > 20 else ""
        print(color(f"    ids: {preview}{more}", C_DIM))
    print(f"  annotations_dirtied: {len(annot_dirtied)} book(s)")
    if annot_dirtied:
        preview = ", ".join(str(b) for b in annot_dirtied[:20])
        more = " …" if len(annot_dirtied) > 20 else ""
        print(color(f"    ids: {preview}{more}", C_DIM))

    # Tag-browser layout mirrors
    state = db.get_tag_browser_state()
    hidden = state.get("hidden") or []
    order = state.get("order") or []
    print(color("\nTag browser:", C_HEADER))
    print(f"  ordered categories: {len(order)}, hidden: {len(hidden)}")
    if hidden:
        print(color(f"    hidden: {', '.join(str(h) for h in hidden)}", C_DIM))


def show_columns(db: CalibreDB, *, quiet: bool = False) -> None:
    """List the custom-column schema with editability and enum values."""
    cols = db.get_custom_columns()
    if not cols:
        print("No custom columns defined in this library.")
        return

    if not quiet:
        print(color(f"=== Custom Columns ({len(cols)}) ===", C_HEADER))
        print()

    width = max(len(name) for name in cols)
    for name in sorted(cols, key=str.lower):
        meta = cols[name]
        flags = []
        if meta.get("is_multiple"):
            flags.append("multi")
        flags.append("editable" if meta.get("editable", True) else "read-only")
        if meta.get("normalized"):
            flags.append("normalized")
        display = meta.get("display") or {}
        enum_values = display.get("enum_values") or []
        if isinstance(enum_values, dict):
            enum_str = ", ".join(str(k) for k in enum_values)
        else:
            enum_str = ", ".join(str(v) for v in enum_values)
        enum_note = f"  enum: {enum_str}" if enum_str else ""
        composite = display.get("composite_template")
        comp_note = f"  template: {composite}" if composite else ""
        print(
            f"  {name:<{width}}  #{meta['label']:<12} {meta['datatype']:<10}"
            f" [{', '.join(flags)}]{enum_note}{comp_note}"
        )

    if not quiet:
        print()
        print(
            f"  {len(cols)} columns; search them as #label — "
            "write with --set-column ID #label value"
        )
