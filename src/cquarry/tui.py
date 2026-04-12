from __future__ import annotations

import io
import os
import subprocess
import sys
from contextlib import contextmanager
from typing import Any, Dict, Optional

from cquarry.config import VERSION, get_db_path, set_db_path
from cquarry.db import CalibreDB
from cquarry.helpers import find_db
from cquarry.modes.catalog import write_catalog, write_all_wings
from cquarry.modes.stats import show_stats
from cquarry.modes.audit import run_audit
from cquarry.modes.display import show_recent, show_series, show_wings
from cquarry.modes.export import run_export

try:
    import curses
    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()


# =====================================
# Terminal utilities
# =====================================

def _reset_terminal() -> None:
    if not sys.stdin.isatty():
        return
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
    except Exception:
        pass


# =====================================
# Curses primitives
# =====================================

_CP_FRAME = 1
_CP_TITLE = 2
_CP_HEADER = 3
_CP_ITEM = 4
_CP_SELECTED = 5
_CP_HINT = 6

_TUI_BOX_W = 46
_TUI_INNER = _TUI_BOX_W - 2


def _init_tui_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


# =====================================
# Curses menu selector
# =====================================

def _tui_select(title: str, sections: list,
                hints: str = "\u2191\u2193 Navigate  \u23ce Select  q Quit") -> Optional[tuple]:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    flat: list[tuple[int, int]] = []
    for si, (_, items) in enumerate(sections):
        for ii in range(len(items)):
            flat.append((si, ii))

    def _draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        box_h = 3
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, f" {title:^{INNER - 2}} ",
                     curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1
        _safe_addstr(stdscr, y, bx, "\u2560" + "\u2550" * INNER + "\u2563", fa)
        y += 1

        idx = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
                y += 1
            if hdr:
                content = f"  {hdr}" + " " * (INNER - len(hdr) - 2)
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, content,
                             curses.color_pair(_CP_HEADER) | curses.A_BOLD)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
            for ii, label in enumerate(items):
                is_sel = idx == cur
                if is_sel:
                    text = f" \u25ba {label}"
                    attr = curses.color_pair(_CP_SELECTED) | curses.A_BOLD
                else:
                    text = f"   {label}"
                    attr = curses.color_pair(_CP_ITEM)
                padded = text + " " * max(0, INNER - len(text))
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, padded[:INNER], attr)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
                idx += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        y += 2
        hx = max(0, (w - len(hints)) // 2)
        _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)
        stdscr.refresh()

    def _run(stdscr) -> Optional[tuple]:
        _init_tui_colors()
        curses.curs_set(0)
        cur = 0
        while True:
            _draw(stdscr, cur)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                cur = (cur - 1) % len(flat)
            elif key in (curses.KEY_DOWN, ord('j')):
                cur = (cur + 1) % len(flat)
            elif key in (curses.KEY_ENTER, 10, 13):
                return flat[cur]
            elif key in (ord('q'), ord('Q'), 27):
                return None
            elif key == curses.KEY_RESIZE:
                pass

    try:
        return curses.wrapper(_run)
    except curses.error:
        return None


# =====================================
# Curses text input prompt
# =====================================

def _tui_prompt_str(label: str, default: Optional[str]) -> str:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str:
        _init_tui_colors()
        curses.curs_set(1)
        buf = list(default or "")
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            bx = max(0, (w - BOX_W) // 2)
            fa = curses.color_pair(_CP_FRAME)
            y = max(0, (h - 8) // 2)

            _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
            y += 1
            lbl = f"  {label}"
            padded_lbl = lbl + " " * max(0, INNER - len(lbl))
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, padded_lbl[:INNER],
                         curses.color_pair(_CP_HEADER) | curses.A_BOLD)
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1
            display = "".join(buf)
            max_input = INNER - 4
            visible = "\u2026" + display[-(max_input - 1):] if len(display) > max_input else display
            input_text = f" > {visible}" + " " * max(0, INNER - len(visible) - 3)
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, input_text[:INNER], curses.color_pair(_CP_ITEM))
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            input_y = y
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2
            hints = "\u23ce Confirm  Esc Default"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)

            cursor_x = bx + 4 + min(len(display), max_input)
            try:
                stdscr.move(input_y, min(cursor_x, bx + BOX_W - 2))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                result = "".join(buf).strip()
                return result if result else (default or "")
            elif key == 27:
                return default or ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf:
                    buf.pop()
            elif key == curses.KEY_RESIZE:
                pass
            elif 32 <= key <= 126:
                buf.append(chr(key))

    try:
        return curses.wrapper(_run)
    except curses.error:
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(130)
        return raw or (default or "")


# =====================================
# Curses pause screen
# =====================================

def _tui_pause() -> None:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> None:
        _init_tui_colors()
        curses.curs_set(0)

        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        y = max(0, (h - 5) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1

        msg = "Press Enter to continue\u2026"
        padded = f" {msg:^{INNER - 2}} "
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, padded[:INNER],
                     curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        stdscr.refresh()

        while True:
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13, ord('q'), ord('Q'), 27):
                return

    try:
        curses.wrapper(_run)
    except curses.error:
        try:
            input("\n  Press Enter to continue...")
        except (EOFError, KeyboardInterrupt):
            pass


# =====================================
# Curses scrollable text pager
# =====================================

def _tui_scroll_text(title: str, text: str) -> None:
    lines = text.replace('\x00', '').expandtabs(4).splitlines()

    def _run(stdscr):
        _init_tui_colors()
        curses.curs_set(0)
        top = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            max_line_len = max((len(l) for l in lines), default=0)
            content_w = min(w, max(_TUI_BOX_W, max_line_len + 4))
            bx = max(0, (w - content_w) // 2)

            fa = curses.color_pair(_CP_FRAME)

            _safe_addstr(stdscr, 0, bx, "\u2554" + "\u2550" * (content_w - 2) + "\u2557", fa)
            _safe_addstr(stdscr, 0, bx + 2, f" {title} ",
                         curses.color_pair(_CP_TITLE) | curses.A_BOLD)
            _safe_addstr(stdscr, h - 2, bx, "\u255a" + "\u2550" * (content_w - 2) + "\u255d", fa)

            hints = "\u2191\u2193 Scroll  PgUp/Dn  q/Esc Close"
            _safe_addstr(stdscr, h - 1, max(0, (w - len(hints)) // 2), hints,
                         curses.color_pair(_CP_HINT) | curses.A_DIM)

            max_lines = max(1, h - 3)
            for i in range(max_lines):
                if top + i < len(lines):
                    line = lines[top + i][:content_w - 4]
                    _safe_addstr(stdscr, i + 1, bx, "\u2551", fa)
                    _safe_addstr(stdscr, i + 1, bx + 2, line, curses.color_pair(_CP_ITEM))
                    _safe_addstr(stdscr, i + 1, bx + content_w - 1, "\u2551", fa)
                else:
                    _safe_addstr(stdscr, i + 1, bx, "\u2551", fa)
                    _safe_addstr(stdscr, i + 1, bx + content_w - 1, "\u2551", fa)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                top = max(0, top - 1)
            elif key in (curses.KEY_DOWN, ord('j')):
                top = min(max(0, len(lines) - max_lines), top + 1)
            elif key in (curses.KEY_PPAGE,):
                top = max(0, top - max_lines)
            elif key in (curses.KEY_NPAGE,):
                top = min(max(0, len(lines) - max_lines), top + max_lines)
            elif key in (curses.KEY_HOME, ord('g')):
                top = 0
            elif key in (curses.KEY_END, ord('G')):
                top = max(0, len(lines) - max_lines)
            elif key in (ord('q'), ord('Q'), 27, curses.KEY_ENTER, 10, 13):
                return
            elif key == curses.KEY_RESIZE:
                pass

    try:
        curses.wrapper(_run)
    except curses.error:
        pass


# =====================================
# Output capture + pager integration
# =====================================

@contextmanager
def _capture_output():
    old_out, old_err = sys.stdout, sys.stderr
    out, err = io.StringIO(), io.StringIO()
    sys.stdout, sys.stderr = out, err
    try:
        yield out, err
    finally:
        sys.stdout, sys.stderr = old_out, old_err


def _run_with_capture(title: str, func, *args, **kwargs) -> None:
    """Run a function, capture its stdout/stderr, and display in the pager."""
    with _capture_output() as (out, err):
        func(*args, **kwargs)

    text = ""
    out_text = out.getvalue().strip()
    if out_text:
        text += out_text + "\n"

    err_text = err.getvalue().strip()
    if err_text:
        text += "\n[Errors/Warnings]:\n" + err_text + "\n"

    text = text.strip()
    if text:
        if _USE_CURSES:
            _tui_scroll_text(title, text)
        else:
            print(text)
            _pause()
    else:
        _pause()


# =====================================
# Fallback (non-curses) utilities
# =====================================

def _box_menu(title: str, sections: list, width: int = 44) -> None:
    iw = width - 4
    hbar = "\u2550" * (width - 2)
    lbar = "\u2500" * (width - 2)
    print(f"\n  \u2554{hbar}\u2557")
    print(f"  \u2551 {title:^{iw}} \u2551")
    print(f"  \u2560{hbar}\u2563")
    first = True
    for header, items in sections:
        if not first:
            print(f"  \u255f{lbar}\u2562")
        first = False
        if header:
            print(f"  \u2551  {header:<{iw - 1}} \u2551")
        for item in items:
            print(f"  \u2551    {item:<{iw - 3}} \u2551")
    print(f"  \u255a{hbar}\u255d")


def _pause() -> None:
    if _USE_CURSES:
        _tui_pause()
        return
    try:
        input("\n  Press Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        pass


def _fallback_input(prompt: str, mapping: dict) -> Any:
    try:
        ch = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return mapping.get(ch, "invalid")


def _prompt_str(label: str, default: Optional[str]) -> str:
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    display = default if default else ""
    try:
        raw = input(f"  {label} [{display}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)
    return raw or (default or "")


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def _prompt_path(label: str, default: str = ".") -> str:
    return os.path.abspath(os.path.expanduser(_prompt_str(label, default)))


# =====================================
# Menu definitions
# =====================================

_MAIN_SECTIONS = [
    ("OUTPUT", [
        "Build catalog (full or by wing)",
        "Generate all wing catalogs",
        "Library statistics",
        "Audit (issues report)",
    ]),
    ("LISTS", [
        "Recently added",
        "Series list (with gap detection)",
        "List wings",
    ]),
    ("EXPORT", [
        "Export (JSON/CSV)",
    ]),
    ("SETTINGS", [
        "Change database path",
    ]),
    ("", ["Quit"]),
]

_MAIN_FALLBACK_MAP = {
    "1": (0, 0), "catalog": (0, 0),
    "2": (0, 1), "all": (0, 1),
    "3": (0, 2), "stats": (0, 2),
    "4": (0, 3), "audit": (0, 3),
    "5": (1, 0), "recent": (1, 0),
    "6": (1, 1), "series": (1, 1),
    "7": (1, 2), "wings": (1, 2),
    "8": (2, 0), "export": (2, 0),
    "s": (3, 0), "settings": (3, 0), "config": (3, 0),
    "q": None, "quit": None, "exit": None,
}


def _select_main() -> Optional[tuple]:
    if _USE_CURSES:
        return _tui_select(f"CalibreQuarry v{VERSION}", _MAIN_SECTIONS)
    _box_menu(f"CalibreQuarry v{VERSION}", [
        ("OUTPUT", ["1) Build catalog", "2) Generate all wings", "3) Statistics", "4) Audit"]),
        ("LISTS", ["5) Recently added", "6) Series list", "7) List wings"]),
        ("EXPORT", ["8) Export (JSON/CSV)"]),
        ("SETTINGS", ["s) Change database path"]),
        ("", ["q) Quit"]),
    ])
    return _fallback_input("  Select [1-8/s/q]: ", _MAIN_FALLBACK_MAP)


# =====================================
# Interactive menu loop
# =====================================

def _resolve_db_for_tui() -> Optional[str]:
    """Resolve the database path, using TUI prompts for first-run config."""
    # Check saved config and default paths first
    saved = get_db_path()
    if saved and os.path.exists(saved):
        return saved

    from cquarry.config import DEFAULT_DB_PATHS
    for p in DEFAULT_DB_PATHS:
        if os.path.exists(p):
            path = os.path.abspath(p)
            set_db_path(path)
            return path

    # Nothing found — prompt via TUI
    while True:
        raw_path = _prompt_path("First run: path to Calibre metadata.db")
        resolved = os.path.expanduser(raw_path)
        if os.path.isdir(resolved):
            resolved = os.path.join(resolved, "metadata.db")
        if os.path.exists(resolved):
            resolved = os.path.abspath(resolved)
            set_db_path(resolved)
            return resolved
        # Show error and re-prompt
        if _USE_CURSES:
            _run_with_capture("Error", lambda: print(f"Not found: {raw_path}"))
        else:
            print(f"  Not found: {raw_path}")


def interactive_menu() -> int:
    db_path = _resolve_db_for_tui()
    if not db_path:
        return 1

    while True:
        # Re-check in case user changed it via settings
        db_path = get_db_path() or db_path
        if not os.path.exists(db_path):
            db_path = _resolve_db_for_tui()
            if not db_path:
                return 1
            continue

        _reset_terminal()
        result = _select_main()

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == (4, 0):  # Quit
            return 0

        if result == (3, 0):  # Change database path
            new_path = _prompt_path(f"Change database (current: {db_path})")
            resolved = os.path.expanduser(new_path)
            if os.path.isdir(resolved):
                resolved = os.path.join(resolved, "metadata.db")
            if os.path.exists(resolved):
                resolved = os.path.abspath(resolved)
                set_db_path(resolved)
            else:
                err = f"Not found: {new_path}"
                if _USE_CURSES:
                    _run_with_capture("Error", lambda: print(err))
                else:
                    print(f"  {err}")
            continue

        with CalibreDB(db_path) as db:
            if result == (0, 0):
                wing = _prompt_str("Wing name (blank for all)", "")
                wing = wing if wing else None
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                output = _prompt_str("Output file", "catalog.txt")
                _reset_terminal()
                _run_with_capture("Catalog", lambda: write_catalog(
                    db, output, wing=wing, primary_only=primary,
                    show_tags=tags, show_id=ids))

            elif result == (0, 1):
                outdir = _prompt_str("Output directory", "catalogs")
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                _reset_terminal()
                _run_with_capture("Generate Wings", lambda: write_all_wings(
                    db, outdir, primary_only=primary, show_tags=tags, show_id=ids))

            elif result == (0, 2):
                _reset_terminal()
                _run_with_capture("Statistics", lambda: show_stats(db))

            elif result == (0, 3):
                output = _prompt_str("Output CSV", "audit.csv")
                _reset_terminal()
                _run_with_capture("Audit", lambda: run_audit(db, output))

            elif result == (1, 0):
                count = _prompt_int("How many", 20)
                _reset_terminal()
                _run_with_capture("Recently Added", lambda: show_recent(db, count))

            elif result == (1, 1):
                _reset_terminal()
                _run_with_capture("Series List", lambda: show_series(db))

            elif result == (1, 2):
                _reset_terminal()
                _run_with_capture("Virtual Libraries", lambda: show_wings(db))

            elif result == (2, 0):
                fmt = _prompt_str("Format (json/csv)", "json")
                output = _prompt_str("Output file", f"library.{fmt}")
                _reset_terminal()
                _run_with_capture("Export", lambda: run_export(db, output, fmt))

    return 0
