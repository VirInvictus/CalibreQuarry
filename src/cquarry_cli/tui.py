import os
import sys

from cquarry.config import get_db_path, set_db_path
from cquarry.db import CalibreDB
from cquarry_cli.modes.analytics import show_author_stats, show_pace_stats, show_tag_tree, show_wing_overlap
from cquarry_cli.modes.audit import run_audit
from cquarry_cli.modes.catalog import write_all_wings, write_catalog
from cquarry_cli.modes.display import show_recent, show_series, show_wings
from cquarry_cli.modes.export import run_export, run_search_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump

from vir_tui import tui_select, _reset_terminal, _open_screen, _close_screen
from vir_tui import ask, ask_yn, prompt_int, prompt_out, run_with_capture, _Cancelled
from vir_tui import info, warn, error, success

_SCREEN = None
_USE_CURSES = True

def _notify(msg: str) -> None:
    print(msg)
    ask("Press Enter to continue...", "")

def _prompt_path(prompt: str, default: str = "") -> str | None:
    while True:
        try:
            ans = ask(prompt, default)
        except _Cancelled:
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
            ],
        ),
        (
            "Export",
            [
                "Export Database (JSON/CSV/AI)",
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
    return tui_select("CalibreQuarry", sections)

_SEL_CHANGE_DB = (4, 0)
_SEL_QUIT = (4, 1)

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
    stdscr = _open_screen() if _USE_CURSES else None
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
            _close_screen()

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
        _reset_terminal()
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
                    _reset_terminal()
                    run_with_capture("Catalog", lambda o=output, w=wing, p=primary, t=tags, i=ids: write_catalog(db, o, wing=w, primary_only=p, show_tags=t, show_id=i), footer=_out_note(output))
                elif result == (0, 1):
                    outdir = prompt_out("Output directory", "catalogs")
                    primary = ask_yn("Primary author only? (y/N)")
                    tags = ask_yn("Show tags instead of ratings? (y/N)")
                    ids = ask_yn("Show book IDs? (y/N)")
                    _reset_terminal()
                    run_with_capture("Generate Wings", lambda o=outdir, p=primary, t=tags, i=ids: write_all_wings(db, o, primary_only=p, show_tags=t, show_id=i), footer=f"Wings written to {os.path.abspath(outdir)}")
                elif result == (0, 2):
                    _reset_terminal()
                    run_with_capture("Statistics", lambda: show_stats(db))
                elif result == (0, 3):
                    output = prompt_out("Output CSV", "audit.csv")
                    _reset_terminal()
                    run_with_capture("Audit", lambda o=output: run_audit(db, o), footer=_out_note(output))
                elif result == (0, 4):
                    query = ask("Search query (Calibre format)", "")
                    if query:
                        output = prompt_out("Output file", "search_results.txt")
                        _reset_terminal()
                        run_with_capture("Search Results", lambda q=query, o=output: run_search_export(db, q, o), footer=_out_note(output))
                elif result == (1, 0):
                    _reset_terminal()
                    run_with_capture("Author Stats", lambda: show_author_stats(db))
                elif result == (1, 1):
                    _reset_terminal()
                    run_with_capture("Reading Pace", lambda: show_pace_stats(db))
                elif result == (1, 2):
                    _reset_terminal()
                    run_with_capture("Tag Tree", lambda: show_tag_tree(db))
                elif result == (1, 3):
                    _reset_terminal()
                    run_with_capture("Wing Overlap", lambda: show_wing_overlap(db))
                elif result == (2, 0):
                    count = prompt_int("How many", 20)
                    _reset_terminal()
                    run_with_capture("Recently Added", lambda c=count: show_recent(db, c))
                elif result == (2, 1):
                    _reset_terminal()
                    run_with_capture("Series List", lambda: show_series(db))
                elif result == (2, 2):
                    _reset_terminal()
                    run_with_capture("Virtual Libraries", lambda: show_wings(db))
                elif result == (2, 3):
                    _reset_terminal()
                    run_with_capture("Tag Dump", lambda: show_tag_dump(db))
                elif result == (3, 0):
                    fmt = ask("Format (json/csv/ai)", "json").strip().lower()
                    while fmt not in ("json", "csv", "ai"):
                        fmt = ask("Format must be json, csv or ai", "json").strip().lower()
                    output = prompt_out("Output file", f"library.{fmt}")
                    _reset_terminal()
                    run_with_capture("Export", lambda o=output, f=fmt: run_export(db, o, f), footer=_out_note(output))
        except _Cancelled:
            continue
