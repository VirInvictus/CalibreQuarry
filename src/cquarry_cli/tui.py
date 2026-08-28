import os

from cquarry.config import get_db_path, set_db_path
from cquarry.db import CalibreDB
from vir_tui import (
    CancelledError,
    ask,
    ask_yn,
    close_screen,
    open_screen,
    prompt_int,
    prompt_out,
    reset_terminal,
    run_with_capture,
    tui_select,
)

from cquarry_cli import writeops
from cquarry_cli.modes.analytics import (
    show_author_stats,
    show_pace_stats,
    show_tag_tree,
    show_wing_overlap,
)
from cquarry_cli.modes.audit import run_audit
from cquarry_cli.modes.catalog import write_all_wings, write_catalog
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

_SCREEN = None
_USE_CURSES = True


def _notify(msg: str) -> None:
    print(msg)
    ask("Press Enter to continue...", "")


def _prompt_path(prompt: str, default: str = "") -> str | None:
    while True:
        try:
            ans = ask(prompt, default)
        except CancelledError:
            return None
        if not ans:
            continue
        return ans


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


def _out_note(path: str) -> str:
    return f"Output written to {os.path.abspath(path)}"


def _select_main() -> tuple | str | None:
    sections = [
        (
            "",
            [
                "Catalog (TXT)",
                "Catalog Wings (TXT/Dir)",
                "Statistics",
                "Audit Database",
                "Search & Export",
            ],
        ),
        (
            "Analytics",
            [
                "Author Stats",
                "Reading Pace",
                "Tag Tree",
                "Wing Overlap",
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
    letter_keys = {"Change Database": ("s", "self"), "Quit": ("q", None)}
    aliases = {"s": (5, 0), "q": None, "quit": None}
    return tui_select(
        "CalibreQuarry", sections, aliases=aliases, letter_keys=letter_keys
    )


_SEL_CHANGE_DB = (5, 0)
_SEL_QUIT = (5, 1)


def _resolve_db_for_tui() -> str | None:
    DEFAULT_DB_PATHS = [
        "metadata.db",
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
        raw_path = _prompt_path("First run: path to Calibre metadata.db")
        if raw_path is None:
            return None
        resolved = _resolve_db_input(raw_path)
        if resolved is not None:
            set_db_path(resolved)
            return resolved
        _notify(f"Not found: {raw_path}")


def interactive_menu() -> int:
    global _SCREEN, _USE_CURSES
    stdscr = open_screen() if _USE_CURSES else None
    if _USE_CURSES and stdscr is None:
        _USE_CURSES = False
    _SCREEN = stdscr
    try:
        return _menu_session()
    except KeyboardInterrupt:
        if _SCREEN is None:
            print()
        return 130
    finally:
        if stdscr is not None:
            close_screen()


def _ask_rating() -> float | None:
    """Prompt until a valid 0-5 rating is entered. None on cancel."""
    while True:
        try:
            raw = ask("Rating 0-5 (halves ok, blank cancels)", "")
        except CancelledError:
            return None
        if not raw.strip():
            return None
        try:
            stars = float(raw)
        except ValueError:
            _notify(f"Not a number: {raw!r}")
            continue
        if not 0 <= stars <= 5:
            _notify("Rating must be within 0-5.")
            continue
        return stars


def _edit_book_session(db_path: str) -> None:
    """Nested menu that applies one cquarry write verb to one book id."""
    bid = prompt_int("Book ID to edit", 1)
    ops = [
        "Set Title",
        "Set Authors",
        "Set Rating",
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
            stars = _ask_rating()
            if stars is not None:
                run_with_capture(
                    "Set Rating",
                    lambda s=stars: writeops.op_set_rating(db_path, bid, s),
                )
        elif idx == 3:
            tag = ask("Tag to add", "")
            if tag:
                run_with_capture(
                    "Add Tag",
                    lambda t=tag: writeops.op_add_tag(db_path, bid, [t]),
                )
        elif idx == 4:
            tag = ask("Tag to remove", "")
            if tag:
                run_with_capture(
                    "Remove Tag",
                    lambda t=tag: writeops.op_remove_tag(db_path, bid, [t]),
                )
        elif idx == 5:
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
        elif idx == 6:
            run_with_capture(
                "Clear Series", lambda: writeops.op_clear_series(db_path, bid)
            )
        elif idx == 7:
            name = ask("Publisher (blank cancels)", "")
            if name:
                run_with_capture(
                    "Set Publisher",
                    lambda n=name: writeops.op_set_publisher(db_path, bid, n),
                )
        elif idx == 8:
            run_with_capture(
                "Clear Publisher", lambda: writeops.op_clear_publisher(db_path, bid)
            )
        elif idx == 9:
            langs = ask("Languages (comma-separated, blank cancels)", "")
            if langs:
                run_with_capture(
                    "Set Languages",
                    lambda lg=langs: writeops.op_set_languages(db_path, bid, lg),
                )
        elif idx == 10:
            run_with_capture(
                "Clear Languages", lambda: writeops.op_clear_languages(db_path, bid)
            )
        elif idx == 11:
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
        elif idx == 12:
            id_type = ask("Identifier type to delete", "isbn")
            if id_type:
                run_with_capture(
                    "Clear Identifier",
                    lambda t=id_type: writeops.op_clear_identifier(db_path, bid, t),
                )
        elif idx == 13:
            text = ask("Comments (HTML, blank cancels)", "")
            if text:
                run_with_capture(
                    "Set Comments",
                    lambda t=text: writeops.op_set_comments(db_path, bid, t),
                )
        elif idx == 14:
            run_with_capture(
                "Clear Comments", lambda: writeops.op_clear_comments(db_path, bid)
            )
        elif idx == 15:
            label = ask("Column label (without #)", "")
            if not label:
                continue
            value = ask("Value", "")
            run_with_capture(
                "Set Custom Column",
                lambda lb=label, v=value: writeops.op_set_column(db_path, bid, lb, v),
            )
        elif idx == 16:
            label = ask("Column label to clear (without #)", "")
            if label:
                run_with_capture(
                    "Clear Custom Column",
                    lambda lb=label: writeops.op_clear_column(db_path, bid, lb),
                )
        elif idx == 17:
            has = ask_yn("Catalogued as having a cover? (y/N)")
            run_with_capture(
                "Set Cover Flag",
                lambda h=has: writeops.op_set_cover(db_path, bid, h),
            )
        elif idx == 18:
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
        elif idx == 19:
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
    if ask_yn(f"Permanently delete book {bid}? This cannot be undone!"):
        if ask_yn("Really delete? (y/N)"):
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
        reset_terminal()
        result = _select_main()
        if result == "fallback":
            continue
        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue
        if result is None or result == _SEL_QUIT:
            return 0
        if result == _SEL_CHANGE_DB:
            new_path = _prompt_path(f"Change database (current: {db_path})", db_path)
            if new_path is None:
                continue
            resolved = _resolve_db_input(new_path)
            if resolved is not None:
                set_db_path(resolved)
            else:
                _notify(f"Not found: {new_path} (database unchanged)")
            continue
        try:
            with CalibreDB(db_path) as db:
                if result == (0, 0):
                    wing = ask("Wing name (blank for all)", "") or None
                    primary = ask_yn("Primary author only? (y/N)")
                    tags = ask_yn("Show tags instead of ratings? (y/N)")
                    ids = ask_yn("Show book IDs? (y/N)")
                    output = prompt_out("Output file", "catalog.txt")
                    reset_terminal()
                    run_with_capture(
                        "Catalog",
                        lambda o=output, w=wing, p=primary, t=tags, i=ids: (
                            write_catalog(
                                db, o, wing=w, primary_only=p, show_tags=t, show_id=i
                            )
                        ),
                        footer=_out_note(output),
                    )
                elif result == (0, 1):
                    outdir = prompt_out("Output directory", "catalogs")
                    primary = ask_yn("Primary author only? (y/N)")
                    tags = ask_yn("Show tags instead of ratings? (y/N)")
                    ids = ask_yn("Show book IDs? (y/N)")
                    reset_terminal()
                    run_with_capture(
                        "Generate Wings",
                        lambda o=outdir, p=primary, t=tags, i=ids: write_all_wings(
                            db, o, primary_only=p, show_tags=t, show_id=i
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
                        footer=_out_note(output),
                    )
                elif result == (0, 4):
                    query = ask("Search query (Calibre format)", "")
                    if query:
                        output = prompt_out("Output file", "search_results.txt")
                        reset_terminal()
                        run_with_capture(
                            "Search Results",
                            lambda q=query, o=output: run_search_export(db, q, o),
                            footer=_out_note(output),
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
                            lambda k=kind: show_entities(db, k),
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
                        footer=_out_note(output),
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
                        footer=_out_note(output),
                    )
                elif result == (3, 2):
                    outdir = prompt_out("Output directory", "librarything")
                    reset_terminal()
                    run_with_capture(
                        "LibraryThing Export",
                        lambda o=outdir: run_librarything_export(db, o),
                        footer=f"CSV written to {os.path.abspath(outdir)}",
                    )
                elif result == (4, 0):
                    _edit_book_session(db_path)
                elif result == (4, 1):
                    _remove_book_session(db_path)
        except CancelledError:
            continue
