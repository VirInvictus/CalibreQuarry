import os
import sqlite3
import sys

from cquarry.config import get_db_path, set_db_path
from cquarry.db import CalibreDB
from vir_tui import (
    CancelledError,
    ask,
    ask_yn,
    confirm,
    interactive_session,
    out_note,
    prompt_float,
    prompt_int,
    prompt_out,
    prompt_path,
    reset_terminal,
    run_with_capture,
    text_mode,
    tui_select,
)

from cquarry_cli import writeops
from cquarry_cli.modes.analytics import (
    show_author_stats,
    show_genre_breakdown,
    show_pace_stats,
    show_reading_stats,
    show_tag_tree,
    show_wing_overlap,
)
from cquarry_cli.modes.audit import run_audit, show_health
from cquarry_cli.modes.catalog import (
    run_all_saved_searches,
    write_all_wings,
    write_catalog,
)
from cquarry_cli.modes.fts import run_fts_search, run_fts_status
from cquarry_cli.modes.detail import show_book
from cquarry_cli.modes.display import (
    show_entities,
    show_reading_progress,
    show_recent,
    show_series,
    show_wings,
)
from cquarry_cli.modes.export import (
    run_annotations_export,
    run_export,
    run_search_export,
)
from cquarry_cli.modes.info import show_columns, show_info
from cquarry_cli.modes.librarything import run_librarything_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump


def _notify(msg: str) -> None:
    print(msg)
    ask("Press Enter to continue...", "")


def _safe_entities(db, kind: str) -> None:
    """The Entity Browser's kinds come from the same menu as the CLI, but
    a stored kind can still be unknown: say so instead of paging a
    traceback."""
    try:
        show_entities(db, kind)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)


def _resolve_db_input(raw_path: str) -> str | None:
    path = os.path.abspath(raw_path)
    if os.path.exists(path):
        if os.path.isdir(path):
            file_path = os.path.join(path, "metadata.db")
            if os.path.exists(file_path):
                return file_path
        else:
            if path.endswith("metadata.db"):
                return path
    return None


def _db_opens(db_path: str) -> bool:
    """Probe-open the database. False for a corrupt or foreign sqlite file:
    CalibreDB's constructor runs a real statement (`SELECT 1 FROM books`)
    and re-raises what it cannot satisfy, so this is a true open test. The
    menu loop and Change Database both consult it -- constructing
    CalibreDB outside every exception boundary used to end the whole
    session in a raw traceback (the 2026-09-08 sweep's P0)."""
    try:
        with CalibreDB(db_path):
            return True
    except sqlite3.Error:
        return False


def _menu_sections() -> list:
    """The main menu's sections. Settings must stay the LAST section
    with Change Database first and Quit second: the s/q letter aliases
    and _SEL_CHANGE_DB/_SEL_QUIT pin those coordinates."""
    return [
        (
            "",
            [
                "Catalog (TXT)",
                "Catalog Wings (TXT/Dir)",
                "Statistics",
                "Audit Database",
                "Search & Export",
                "Content Search (FTS)",
                "Saved Search Catalogs",
                "Library Health",
            ],
        ),
        (
            "Analytics",
            [
                "Author Stats",
                "Reading Pace",
                "Tag Tree",
                "Genre Breakdown",
                "Wing Overlap",
                "Reading Analytics",
                "FTS Index Status",
            ],
        ),
        (
            "Display",
            [
                "Recently Added",
                "Series List",
                "Virtual Libraries",
                "Tag Dump",
                "Book Detail",
                "Reading Progress",
                "Custom Columns",
                "Library Info",
                "Entity Browser",
            ],
        ),
        (
            "Export",
            [
                "Export Database (JSON/CSV/AI)",
                "Annotations (JSON)",
                "LibraryThing (CSV)",
            ],
        ),
        (
            "Write (Calibre closed)",
            [
                "Edit Book",
                "Remove Book",
            ],
        ),
        (
            "Settings",
            [
                "Change Database",
                "Quit",
            ],
        ),
    ]


def _restricted(db: CalibreDB) -> CalibreDB:
    """The Phase 19 scope prompt, shared by the read entries that gained
    restriction in the CLI: blank keeps the whole library; an expression
    resolves once through the CLI's RestrictedView. A parse failure
    notifies and stays unrestricted (the CLI exits 1; the menu session
    has nowhere to exit to). The notice goes through _notify, not a bare
    print: every caller resets the terminal right after this returns, and
    the reset erased the note before it could be read, so a typo'd scope
    ran the mode UNRESTRICTED silently."""
    expr = ask("Restrict to a search expression (blank = whole library)", "")
    if not expr.strip():
        return db
    from cquarry.search import ParseException

    from cquarry_cli.restrict import RestrictedView

    try:
        return RestrictedView(db, set(db.search(expr)))
    except (ParseException, ValueError) as e:
        _notify(f"Could not parse the expression ({e}); using the whole library.")
        return db


def _select_main() -> tuple | str | None:
    letter_keys = {"Change Database": ("s", "self"), "Quit": ("q", None)}
    aliases = {"s": (5, 0), "q": None, "quit": None}
    return tui_select(
        "CalibreQuarry", _menu_sections(), aliases=aliases, letter_keys=letter_keys
    )


_SEL_CHANGE_DB = (5, 0)
_SEL_QUIT = (5, 1)


def _resolve_db_for_tui() -> str | None:
    # The saved config wins, always: the sweep found the TUI consulting a
    # hard-coded default list that STARTS with a CWD-relative metadata.db
    # and never called get_db_path(), so launching from any directory
    # with a stray metadata.db overwrote the shared config and the next
    # CLI run read the wrong library.
    saved = get_db_path()
    if saved and os.path.exists(saved):
        return saved
    DEFAULT_DB_PATHS = [
        "~/Calibre Library/metadata.db",
        "~/Documents/Calibre Library/metadata.db",
    ]
    DEFAULT_DB_PATHS = [os.path.expanduser(p) for p in DEFAULT_DB_PATHS]
    for p in DEFAULT_DB_PATHS:
        if os.path.exists(p):
            path = os.path.abspath(p)
            set_db_path(path)
            return path
    while True:
        try:
            raw_path = prompt_path(
                "First run: path to Calibre metadata.db (or its library folder)"
            )
        except CancelledError:
            return None
        resolved = _resolve_db_input(raw_path)
        if resolved is not None:
            set_db_path(resolved)
            return resolved
        _notify(f"Not a Calibre database: {raw_path}")


def interactive_menu() -> int:
    try:
        with interactive_session():
            return _menu_session()
    except KeyboardInterrupt:
        return 130


def _edit_book_session(db_path: str) -> None:
    """Nested menu that applies one cquarry write verb to one book id."""
    bid = prompt_int("Book ID to edit", 1)
    ops = [
        "Set Title",
        "Set Authors",
        "Set Rating",
        "Set Pubdate",
        "Clear Pubdate",
        "Add Tag",
        "Remove Tag",
        "Set Series",
        "Clear Series",
        "Set Publisher",
        "Clear Publisher",
        "Set Languages",
        "Clear Languages",
        "Set Identifier",
        "Clear Identifier",
        "Set Comments",
        "Clear Comments",
        "Set Custom Column",
        "Clear Custom Column",
        "Set Cover Flag",
        "Add Format (metadata)",
        "Remove Format",
    ]
    while True:
        reset_terminal()
        pick = tui_select(
            f"Edit book {bid}  (d = done)",
            [("", ops)],
            aliases={"d": None, "done": None},
        )
        if not isinstance(pick, tuple):
            return
        idx = pick[-1]

        if idx == 0:
            title = ask("New title", "")
            if title:
                run_with_capture(
                    "Set Title",
                    lambda t=title: writeops.op_set_title(db_path, bid, t),
                )
        elif idx == 1:
            names = ask("Authors ('; '-separated)", "")
            if names:
                parsed = [n.strip() for n in names.split(";") if n.strip()]
                run_with_capture(
                    "Set Authors",
                    lambda p=parsed: writeops.op_set_authors(db_path, bid, p),
                )
        elif idx == 2:
            try:
                stars = prompt_float("Rating 0-5 (halves ok; 0 clears)", 0.0, 0.0, 5.0)
            except CancelledError:
                continue
            run_with_capture(
                "Set Rating",
                lambda s=stars: writeops.op_set_rating(db_path, bid, s),
            )
        elif idx == 3:
            raw = ask("Pubdate (YYYY-MM-DD, blank cancels)", "")
            if raw:
                run_with_capture(
                    "Set Pubdate",
                    lambda d=raw: writeops.op_set_pubdate(db_path, bid, d),
                )
        elif idx == 4:
            run_with_capture(
                "Clear Pubdate", lambda: writeops.op_clear_pubdate(db_path, bid)
            )
        elif idx == 5:
            tag = ask("Tag to add", "")
            if tag:
                run_with_capture(
                    "Add Tag",
                    lambda t=tag: writeops.op_add_tag(db_path, bid, [t]),
                )
        elif idx == 6:
            tag = ask("Tag to remove", "")
            if tag:
                run_with_capture(
                    "Remove Tag",
                    lambda t=tag: writeops.op_remove_tag(db_path, bid, [t]),
                )
        elif idx == 7:
            name = ask("Series name", "")
            if not name:
                continue
            raw = ask("Series index", "1")
            try:
                index = float(raw) if raw.strip() else None
            except ValueError:
                _notify(f"Not a number: {raw!r}")
                continue
            run_with_capture(
                "Set Series",
                lambda n=name, i=index: writeops.op_set_series(db_path, bid, n, i),
            )
        elif idx == 8:
            run_with_capture(
                "Clear Series", lambda: writeops.op_clear_series(db_path, bid)
            )
        elif idx == 9:
            name = ask("Publisher (blank cancels)", "")
            if name:
                run_with_capture(
                    "Set Publisher",
                    lambda n=name: writeops.op_set_publisher(db_path, bid, n),
                )
        elif idx == 10:
            run_with_capture(
                "Clear Publisher", lambda: writeops.op_clear_publisher(db_path, bid)
            )
        elif idx == 11:
            langs = ask("Languages (comma-separated, blank cancels)", "")
            if langs:
                run_with_capture(
                    "Set Languages",
                    lambda lg=langs: writeops.op_set_languages(db_path, bid, lg),
                )
        elif idx == 12:
            run_with_capture(
                "Clear Languages", lambda: writeops.op_clear_languages(db_path, bid)
            )
        elif idx == 13:
            id_type = ask("Identifier type (isbn, goodreads, ...)", "isbn")
            if not id_type:
                continue
            value = ask("Value (blank deletes the identifier)", "")
            run_with_capture(
                "Set Identifier",
                lambda t=id_type, v=value: writeops.op_set_identifier(
                    db_path, bid, t, v
                ),
            )
        elif idx == 14:
            id_type = ask("Identifier type to delete", "isbn")
            if id_type:
                run_with_capture(
                    "Clear Identifier",
                    lambda t=id_type: writeops.op_clear_identifier(db_path, bid, t),
                )
        elif idx == 15:
            text = ask("Comments (HTML, blank cancels)", "")
            if text:
                run_with_capture(
                    "Set Comments",
                    lambda t=text: writeops.op_set_comments(db_path, bid, t),
                )
        elif idx == 16:
            run_with_capture(
                "Clear Comments", lambda: writeops.op_clear_comments(db_path, bid)
            )
        elif idx == 17:
            label = ask("Column label (without #)", "")
            if not label:
                continue
            value = ask("Value", "")
            run_with_capture(
                "Set Custom Column",
                lambda lb=label, v=value: writeops.op_set_column(db_path, bid, lb, v),
            )
        elif idx == 18:
            label = ask("Column label to clear (without #)", "")
            if label:
                run_with_capture(
                    "Clear Custom Column",
                    lambda lb=label: writeops.op_clear_column(db_path, bid, lb),
                )
        elif idx == 19:
            has = ask_yn("Catalogued as having a cover? (y/N)")
            run_with_capture(
                "Set Cover Flag",
                lambda h=has: writeops.op_set_cover(db_path, bid, h),
            )
        elif idx == 20:
            fmt = ask("Format (e.g. EPUB)", "")
            if not fmt:
                continue
            name = ask("Filename stem (data.name)", "")
            raw = ask("Uncompressed size in bytes", "0")
            try:
                size = int(raw)
            except ValueError:
                _notify(f"Not an integer: {raw!r}")
                continue
            run_with_capture(
                "Add Format",
                lambda f=fmt, n=name, s=size: writeops.op_add_format(
                    db_path, bid, f, n, s
                ),
            )
        elif idx == 21:
            fmt = ask("Format to remove", "")
            if fmt:
                run_with_capture(
                    "Remove Format",
                    lambda f=fmt: writeops.op_remove_format(db_path, bid, f),
                )


def _remove_book_session(db_path: str) -> None:
    """Dry-run first, then a double-confirmed delete."""
    bid = prompt_int("Book ID to remove", 1)
    reset_terminal()
    run_with_capture(
        "Dry Run",
        lambda b=bid: writeops.op_remove_book(db_path, b, confirm=False),
    )
    if confirm(f"Permanently delete book {bid}? This cannot be undone", danger=True):
        if confirm("Really delete"):
            run_with_capture(
                "Remove Book",
                lambda b=bid: writeops.op_remove_book(db_path, b, confirm=True),
            )


def _menu_session() -> int:
    db_path = _resolve_db_for_tui()
    if not db_path:
        return 1
    while True:
        db_path = get_db_path() or db_path
        if not os.path.exists(db_path):
            db_path = _resolve_db_for_tui()
            if not db_path:
                return 1
            continue
        if not _db_opens(db_path):
            _notify(
                f"Cannot open {db_path}: not a readable Calibre database. Pick another."
            )
            db_path = _resolve_db_for_tui()
            if not db_path:
                return 1
            continue
        reset_terminal()
        result = _select_main()
        if result == "fallback":
            continue
        if result == "invalid":
            if text_mode():
                print("  Invalid selection.")
            continue
        if result is None or result == _SEL_QUIT:
            return 0
        if result == _SEL_CHANGE_DB:
            try:
                new_path = prompt_path(f"Change database (current: {db_path})", db_path)
            except CancelledError:
                continue
            resolved = _resolve_db_input(new_path)
            if resolved is None:
                _notify(f"Not a Calibre database: {new_path} (database unchanged)")
            elif not _db_opens(resolved):
                _notify(
                    f"Cannot open {new_path}: not a readable Calibre "
                    "database (database unchanged)"
                )
            else:
                set_db_path(resolved)
            continue
        try:
            with CalibreDB(db_path) as db:
                if result == (0, 0):
                    wing = ask("Wing name (blank for all)", "") or None
                    primary = ask_yn("Primary author only? (y/N)")
                    tags = ask_yn("Show tags instead of ratings? (y/N)")
                    ids = ask_yn("Show book IDs? (y/N)")
                    md = ask_yn("Markdown format? (y/N)")
                    output = prompt_out(
                        "Output file", "catalog.md" if md else "catalog.txt"
                    )
                    reset_terminal()
                    run_with_capture(
                        "Catalog",
                        lambda o=output, w=wing, p=primary, t=tags, i=ids, m=md: (
                            write_catalog(
                                db,
                                o,
                                wing=w,
                                primary_only=p,
                                show_tags=t,
                                show_id=i,
                                fmt="md" if m else None,
                            )
                        ),
                        footer=out_note(output),
                    )
                elif result == (0, 1):
                    outdir = prompt_out("Output directory", "catalogs")
                    primary = ask_yn("Primary author only? (y/N)")
                    tags = ask_yn("Show tags instead of ratings? (y/N)")
                    ids = ask_yn("Show book IDs? (y/N)")
                    md = ask_yn("Markdown format? (y/N)")
                    reset_terminal()
                    run_with_capture(
                        "Generate Wings",
                        lambda o=outdir, p=primary, t=tags, i=ids, m=md: (
                            write_all_wings(
                                db,
                                o,
                                primary_only=p,
                                show_tags=t,
                                show_id=i,
                                fmt="md" if m else None,
                            )
                        ),
                        footer=f"Wings written to {os.path.abspath(outdir)}",
                    )
                elif result == (0, 2):
                    reset_terminal()
                    run_with_capture("Statistics", lambda: show_stats(db))
                elif result == (0, 3):
                    output = prompt_out("Output CSV", "audit.csv")
                    reset_terminal()
                    run_with_capture(
                        "Audit",
                        lambda o=output: run_audit(db, o),
                        footer=out_note(output),
                    )
                elif result == (0, 4):
                    query = ask("Search query (Calibre format)", "")
                    if query:
                        output = prompt_out("Output file", "search_results.txt")
                        reset_terminal()
                        run_with_capture(
                            "Search Results",
                            lambda q=query, o=output: run_search_export(db, q, o),
                            footer=out_note(output),
                        )
                elif result == (1, 0):
                    reset_terminal()
                    run_with_capture("Author Stats", lambda: show_author_stats(db))
                elif result == (1, 1):
                    reset_terminal()
                    run_with_capture("Reading Pace", lambda: show_pace_stats(db))
                elif result == (1, 2):
                    reset_terminal()
                    run_with_capture("Tag Tree", lambda: show_tag_tree(db))
                elif result == (1, 3):
                    depth = prompt_int("Levels of the tag hierarchy (1 = genres)", 1)
                    reset_terminal()
                    run_with_capture(
                        "Genre Breakdown",
                        lambda d=max(1, depth): show_genre_breakdown(db, depth=d),
                    )
                elif result == (1, 4):
                    reset_terminal()
                    run_with_capture("Wing Overlap", lambda: show_wing_overlap(db))
                elif result == (2, 0):
                    count = prompt_int("How many", 20)
                    reset_terminal()
                    run_with_capture(
                        "Recently Added", lambda c=count: show_recent(db, c)
                    )
                elif result == (2, 1):
                    reset_terminal()
                    run_with_capture("Series List", lambda: show_series(db))
                elif result == (2, 2):
                    reset_terminal()
                    run_with_capture("Virtual Libraries", lambda: show_wings(db))
                elif result == (2, 3):
                    reset_terminal()
                    run_with_capture("Tag Dump", lambda: show_tag_dump(db))
                elif result == (2, 4):
                    bid = prompt_int("Book ID", 1)
                    reset_terminal()
                    run_with_capture("Book Detail", lambda b=bid: show_book(db, b))
                elif result == (2, 5):
                    reset_terminal()
                    run_with_capture(
                        "Reading Progress", lambda: show_reading_progress(db)
                    )
                elif result == (2, 6):
                    reset_terminal()
                    run_with_capture("Custom Columns", lambda: show_columns(db))
                elif result == (2, 7):
                    reset_terminal()
                    run_with_capture("Library Info", lambda: show_info(db))
                elif result == (2, 8):
                    kind = (
                        ask(
                            "Kind (authors/series/publishers/tags/languages/ratings)",
                            "authors",
                        )
                        .strip()
                        .lower()
                    )
                    if kind:
                        reset_terminal()
                        run_with_capture(
                            f"Entities: {kind}",
                            lambda k=kind: _safe_entities(db, k),
                        )
                elif result == (3, 0):
                    fmt = ask("Format (json/csv/ai)", "json").strip().lower()
                    while fmt not in ("json", "csv", "ai"):
                        fmt = (
                            ask("Format must be json, csv or ai", "json")
                            .strip()
                            .lower()
                        )
                    output = prompt_out("Output file", f"library.{fmt}")
                    reset_terminal()
                    run_with_capture(
                        "Export",
                        lambda o=output, f=fmt: run_export(db, o, f),
                        footer=out_note(output),
                    )
                elif result == (3, 1):
                    bid = prompt_int("Book ID (0 for the whole library)", 0)
                    output = prompt_out("Output JSON", "annotations.json")
                    reset_terminal()
                    run_with_capture(
                        "Annotations",
                        lambda b=bid, o=output: run_annotations_export(
                            db, b if b else None, o
                        ),
                        footer=out_note(output),
                    )
                elif result == (3, 2):
                    outdir = prompt_out("Output directory", "librarything")
                    reset_terminal()
                    run_with_capture(
                        "LibraryThing Export",
                        lambda o=outdir: run_librarything_export(db, o),
                        footer=f"CSV written to {os.path.abspath(outdir)}",
                    )
                elif result == (0, 5):
                    query = ask("Content query (full-text over extracted text)", "")
                    if not query.strip():
                        _notify("Content search needs a non-empty query.")
                        continue
                    output = prompt_out("Output file", "fts_results.txt")
                    restricted = _restricted(db)
                    reset_terminal()
                    run_with_capture(
                        "FTS Content Search",
                        lambda q=query, o=output, v=restricted: run_fts_search(v, q, o),
                        footer=out_note(output),
                    )
                elif result == (0, 6):
                    outdir = prompt_out("Output directory", "saved_search_catalogs")
                    md = ask_yn("Markdown format? (y/N)")
                    restricted = _restricted(db)
                    reset_terminal()
                    run_with_capture(
                        "Saved Search Catalogs",
                        lambda o=outdir, m=md, v=restricted: run_all_saved_searches(
                            v, o, fmt="md" if m else None
                        ),
                        footer=(
                            f"Saved-search catalogs written to "
                            f"{os.path.abspath(outdir)}"
                        ),
                    )
                elif result == (0, 7):
                    restricted = _restricted(db)
                    reset_terminal()
                    run_with_capture(
                        "Library Health", lambda v=restricted: show_health(v)
                    )
                elif result == (1, 5):
                    restricted = _restricted(db)
                    reset_terminal()
                    run_with_capture(
                        "Reading Analytics", lambda v=restricted: show_reading_stats(v)
                    )
                elif result == (1, 6):
                    restricted = _restricted(db)
                    reset_terminal()
                    run_with_capture(
                        "FTS Index Status", lambda v=restricted: run_fts_status(v)
                    )
                elif result == (4, 0):
                    _edit_book_session(db_path)
                elif result == (4, 1):
                    _remove_book_session(db_path)
        except sqlite3.Error as e:
            # The database went away or turned unreadable mid-session (a
            # swap, a crash, a moved library): say so and re-resolve rather
            # than dying in a traceback.
            _notify(f"Database error: {e}. Pick another database.")
            db_path = _resolve_db_for_tui()
            if not db_path:
                return 1
        except CancelledError:
            continue
