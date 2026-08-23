import io
import os
import subprocess
import sys
import traceback
from contextlib import contextmanager
from typing import Any

from cquarry.config import get_db_path, set_db_path
from cquarry.db import CalibreDB

from cquarry_cli import VERSION
from cquarry_cli.modes.analytics import (
    show_author_stats,
    show_pace_stats,
    show_tag_tree,
    show_wing_overlap,
)
from cquarry_cli.modes.audit import run_audit
from cquarry_cli.modes.catalog import write_all_wings, write_catalog
from cquarry_cli.modes.display import show_recent, show_series, show_wings
from cquarry_cli.modes.export import run_export, run_search_export
from cquarry_cli.modes.stats import show_stats
from cquarry_cli.modes.tags import show_tag_dump

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
    if _SCREEN is not None:
        return  # a live curses session owns the terminal state
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
    """Set up the curses color pairs used by the TUI menus and pager.
    Non-fatal: a terminal without color support gets a monochrome TUI
    instead of a dead one."""
    try:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
        curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
        curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
        curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
        curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
        curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)
    except curses.error:
        pass


def _curs_set(visibility: int) -> None:
    """curs_set raises on terminals without cursor-visibility support; the
    cursor is cosmetic, so never let it kill a widget."""
    try:
        curses.curs_set(visibility)
    except curses.error:
        pass


def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    """addstr that silently ignores curses out-of-bounds errors."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


# =====================================
# Session screen (one per interactive session)
# =====================================

# One persistent curses screen per interactive session. interactive_menu
# opens it once and every widget draws into it, so multi-prompt flows no
# longer flash to the shell between widgets (each widget used to be its own
# curses.wrapper init/teardown). None when no session owns a screen; widgets
# invoked directly then fall back to a one-shot wrapper session.
_SCREEN = None


def _with_screen(fn):
    """Run a widget body against the session's persistent screen, or in a
    one-shot curses.wrapper session when no session owns one. Colors are
    initialized here (or in _open_screen), not per widget."""
    if _SCREEN is not None:
        return fn(_SCREEN)

    def _boot(stdscr):
        _init_tui_colors()
        return fn(stdscr)

    return curses.wrapper(_boot)


def _open_screen():
    """Start the session screen (initscr + the modes curses.wrapper would
    set). Returns the screen, or None when curses can't start on this
    terminal; the caller degrades the whole session to the text menu."""
    try:
        stdscr = curses.initscr()
        curses.noecho()
        curses.cbreak()
        stdscr.keypad(True)
        _init_tui_colors()
        return stdscr
    except curses.error:
        # initscr may have partially engaged the terminal; put it back.
        try:
            if not curses.isendwin():
                curses.endwin()
        except curses.error:
            pass
        return None


def _close_screen() -> None:
    """End the session screen. Idempotent and guarded, so it is safe after a
    mid-session degrade already ended the screen."""
    global _SCREEN
    _SCREEN = None
    if not HAVE_CURSES:
        return
    try:
        if not curses.isendwin():
            try:
                curses.echo()
                curses.nocbreak()
            except curses.error:
                pass
            curses.endwin()
    except curses.error:
        pass


def _degrade_to_text() -> None:
    """A curses failure (dumb terminal, capability lost mid-session): suspend
    the screen and flip the whole session to the text fallback. endwin puts
    the terminal back in normal mode and nothing refreshes it afterwards, so
    plain print/input work from here on."""
    global _USE_CURSES
    _USE_CURSES = False
    _close_screen()


# =====================================
# Curses menu selector
# =====================================


def _tui_select(
    title: str,
    sections: list,
    hints: str = "\u2191\u2193 Navigate  \u23ce Select  q Quit",
) -> tuple | None:
    """Full-screen arrow-key menu. Returns the chosen (section, item) or None."""
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
        sel_row = 3  # offset of the selected item from the box top
        idx0 = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                box_h += 1
            if hdr:
                box_h += 1
            if idx0 <= cur < idx0 + len(items):
                sel_row = box_h + (cur - idx0)
            idx0 += len(items)
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)
        if y + sel_row >= h - 1:
            # Terminal shorter than the menu: shift the box up so the selected
            # row stays visible (rows scrolled off the top just don't draw).
            y = (h - 2) - sel_row

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(
            stdscr,
            y,
            bx + 1,
            f" {title:^{INNER - 2}} ",
            curses.color_pair(_CP_TITLE) | curses.A_BOLD,
        )
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
                _safe_addstr(
                    stdscr,
                    y,
                    bx + 1,
                    content,
                    curses.color_pair(_CP_HEADER) | curses.A_BOLD,
                )
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
            for label in items:
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

    def _run(stdscr) -> tuple | None:
        _curs_set(0)
        cur = 0
        while True:
            _draw(stdscr, cur)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                cur = (cur - 1) % len(flat)
            elif key in (curses.KEY_DOWN, ord("j")):
                cur = (cur + 1) % len(flat)
            elif key in (curses.KEY_ENTER, 10, 13):
                return flat[cur]
            elif key in (ord("q"), ord("Q"), 27):
                return None
            elif key == curses.KEY_RESIZE:
                pass

    try:
        return _with_screen(_run)
    except curses.error:
        # A real curses failure (dumb terminal, TERM=vt100), not a user Quit:
        # degrade the whole session to the text fallback and hand the menu
        # loop a sentinel it re-enters on, instead of silently exiting 0.
        _degrade_to_text()
        return "fallback"


# =====================================
# Curses text input prompt
# =====================================


def _tui_prompt_str(label: str, default: str | None) -> str | None:
    """Boxed single-line prompt. Enter accepts (bare Enter = the default);
    Esc cancels and returns None; Ctrl-U clears the field."""
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str | None:
        _curs_set(1)
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
            _safe_addstr(
                stdscr,
                y,
                bx + 1,
                padded_lbl[:INNER],
                curses.color_pair(_CP_HEADER) | curses.A_BOLD,
            )
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1
            display = "".join(buf)
            max_input = INNER - 4
            visible = (
                "\u2026" + display[-(max_input - 1) :]
                if len(display) > max_input
                else display
            )
            input_text = f" > {visible}" + " " * max(0, INNER - len(visible) - 3)
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(
                stdscr, y, bx + 1, input_text[:INNER], curses.color_pair(_CP_ITEM)
            )
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            input_y = y
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2
            hints = "\u23ce Accept  Esc Cancel  Ctrl-U Clear"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(
                stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM
            )

            cursor_x = bx + 4 + min(len(display), max_input)
            try:
                stdscr.move(input_y, min(cursor_x, bx + BOX_W - 2))
            except curses.error:
                pass
            stdscr.refresh()

            # get_wch, not getch: getch yields one byte at a time, so a typed
            # 'ë' arrived as two out-of-range bytes and was dropped. A library
            # of translated fiction is full of names that cannot be typed that
            # way. get_wch hands back a str for a character and an int for a
            # key code, so both shapes are handled below.
            # A curses.error here is deliberately NOT caught: the screen is
            # blocking, so a failure is a real one, and the outer handler
            # degrades the session to the text prompt rather than spinning.
            key = stdscr.get_wch()
            # Every curses KEY_* constant is above 255, so a small int is a
            # plain character and folds into the str path.
            if isinstance(key, int) and key < 256:
                key = chr(key)
            if key in (curses.KEY_ENTER, "\n", "\r"):
                result = "".join(buf).strip()
                return result if result else (default or "")
            elif key == "\x1b":
                return None  # Esc cancels; it must never launch with defaults
            elif key in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                if buf:
                    buf.pop()
            elif key == "\x15":  # Ctrl-U: clear the field (pre-filled defaults)
                buf.clear()
            elif key == curses.KEY_RESIZE:
                pass
            elif isinstance(key, str) and key.isprintable():
                buf.append(key)

    try:
        return _with_screen(_run)
    except KeyboardInterrupt:
        return None  # Ctrl-C at a prompt cancels, exactly like Esc
    except curses.error:
        # _USE_CURSES is now False, so this re-asks via the text prompt.
        _degrade_to_text()
        return _prompt_str(label, default)


# =====================================
# Curses pause screen
# =====================================


def _tui_pause() -> None:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> None:
        _curs_set(0)

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
        _safe_addstr(
            stdscr,
            y,
            bx + 1,
            padded[:INNER],
            curses.color_pair(_CP_TITLE) | curses.A_BOLD,
        )
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        stdscr.refresh()

        while True:
            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13, ord("q"), ord("Q"), 27):
                return

    try:
        _with_screen(_run)
    except KeyboardInterrupt:
        pass
    except curses.error:
        # _USE_CURSES is now False, so this re-runs as the text pause.
        _degrade_to_text()
        _pause()


# =====================================
# Curses scrollable text pager
# =====================================


def _tui_scroll_text(title: str, text: str) -> None:
    lines = text.replace("\x00", "").expandtabs(4).splitlines()
    # Computed once, not per keypress: the content never changes while paging.
    max_line_len = max((len(ln) for ln in lines), default=0)

    def _run(stdscr):
        _curs_set(0)
        top = 0
        left = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            fa = curses.color_pair(_CP_FRAME)

            # Width follows the longest line (up to the terminal width) so wide
            # reports are not chopped at the menu box width.
            content_w = min(w, max(_TUI_BOX_W, max_line_len + 4))
            bx = max(0, (w - content_w) // 2)
            max_lines = max(1, h - 3)
            last_top = max(0, len(lines) - max_lines)
            top = min(top, last_top)  # keep the view valid across resizes
            visible_w = content_w - 4
            max_left = max(0, max_line_len - visible_w)
            left = min(left, max_left)

            _safe_addstr(
                stdscr, 0, bx, "\u2554" + "\u2550" * (content_w - 2) + "\u2557", fa
            )
            _safe_addstr(
                stdscr,
                0,
                bx + 2,
                f" {title} ",
                curses.color_pair(_CP_TITLE) | curses.A_BOLD,
            )
            _safe_addstr(
                stdscr, h - 2, bx, "\u255a" + "\u2550" * (content_w - 2) + "\u255d", fa
            )

            hints = (
                "\u2191\u2193 Scroll  \u2190\u2192 Pan  PgUp/Dn"
                "  g/G Top/Bottom  q/Esc Close"
            )
            _safe_addstr(
                stdscr,
                h - 1,
                max(0, (w - len(hints)) // 2),
                hints,
                curses.color_pair(_CP_HINT) | curses.A_DIM,
            )

            for i in range(max_lines):
                _safe_addstr(stdscr, i + 1, bx, "\u2551", fa)
                if top + i < len(lines):
                    ln = lines[top + i]
                    seg = ln[left : left + visible_w]
                    # Ellipsis markers show that a line continues off-screen.
                    if len(ln) - left > visible_w and seg:
                        seg = seg[:-1] + "\u2026"
                    if left and seg:
                        seg = "\u2026" + seg[1:]
                    _safe_addstr(
                        stdscr, i + 1, bx + 2, seg, curses.color_pair(_CP_ITEM)
                    )
                _safe_addstr(stdscr, i + 1, bx + content_w - 1, "\u2551", fa)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord("k")):
                top = max(0, top - 1)
            elif key in (curses.KEY_DOWN, ord("j")):
                top = min(last_top, top + 1)
            elif key in (curses.KEY_LEFT, ord("h")):
                left = max(0, left - 8)
            elif key in (curses.KEY_RIGHT, ord("l")):
                left = min(max_left, left + 8)
            elif key in (curses.KEY_PPAGE,):
                top = max(0, top - max_lines)
            elif key in (curses.KEY_NPAGE,):
                top = min(last_top, top + max_lines)
            elif key in (curses.KEY_HOME, ord("g")):
                top = 0
                left = 0
            elif key in (curses.KEY_END, ord("G")):
                top = last_top
            elif key in (ord("q"), ord("Q"), 27, curses.KEY_ENTER, 10, 13):
                return
            elif key == curses.KEY_RESIZE:
                pass

    try:
        _with_screen(_run)
    except KeyboardInterrupt:
        pass  # Ctrl-C just closes the pager
    except curses.error:
        # Degrade, then still show the results as plain text: a dead pager
        # must not eat a completed mode's output.
        _degrade_to_text()
        print(text)
        _pause()


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


def _run_with_capture(title: str, func, *args, footer: str = "", **kwargs) -> None:
    """Run a function, capture its stdout/stderr, and display in the pager.
    A mode error is paged under an [Error] heading (with whatever output was
    captured), never propagated as a raw traceback that loses the results."""
    note = ""
    with _capture_output() as (out, err):
        try:
            func(*args, **kwargs)
        except KeyboardInterrupt:
            note = "[Cancelled]"
        except Exception:
            note = "[Error]\n" + traceback.format_exc().rstrip()

    text = ""
    if note:
        text += note + "\n"
    out_text = out.getvalue().strip()
    if out_text:
        text += out_text + "\n"

    err_text = err.getvalue().strip()
    if err_text:
        text += "\n[Errors/Warnings]:\n" + err_text + "\n"

    if footer and not note:
        # The "Report written to ..." footer must not assert a file exists
        # when the mode died or was cancelled before finishing.
        text += "\n" + footer + "\n"

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
    """Plain-text boxed menu, used when curses is unavailable."""
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
    """Wait for the user to acknowledge before the menu redraws."""
    if _USE_CURSES:
        _tui_pause()
        return
    try:
        input("\n  Press Enter to continue...")
    except EOFError, KeyboardInterrupt:
        pass


def _fallback_input(prompt: str, mapping: dict) -> Any:
    # KeyboardInterrupt propagates on purpose: Ctrl-C at the menu must exit
    # 130 like the curses menu does, not read as a clean Quit.
    try:
        ch = input(prompt).strip().lower()
    except EOFError:
        print()
        return None  # input exhausted: treat as Quit
    return mapping.get(ch, "invalid")


class _Cancelled(Exception):
    """Raised when the user cancels a prompt (Esc in the TUI, Ctrl-C/EOF at a
    text prompt); the active prompt chain unwinds back to the menu instead of
    launching a mode with defaults."""


def _prompt_str(label: str, default: str | None) -> str | None:
    """One prompt. Returns the entered value (the default on bare Enter), or
    None when the user cancelled."""
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    display = default if default else ""
    try:
        raw = input(f"  {label} [{display}]: ").strip()
    except EOFError, KeyboardInterrupt:
        print()
        return None
    return raw or (default or "")


def _ask(label: str, default: str | None) -> str:
    """_prompt_str that raises _Cancelled instead of returning None, so a
    multi-prompt handler aborts as one unit."""
    val = _prompt_str(label, default)
    if val is None:
        raise _Cancelled
    return val


def _ask_yn(label: str, default: str = "N") -> bool:
    return _ask(label, default).lower().startswith("y")


def _prompt_out(label: str, default: str) -> str:
    """Output-path prompt: expands ~ (no shell is there to do it) but is not
    made absolute, so relative paths keep their current meaning."""
    return os.path.expanduser(_ask(label, default) or default)


def _out_note(path: str | None) -> str:
    """Results-pager footer saying where a report landed, so 'where did my
    report go' answers itself."""
    return f"Report written to {os.path.abspath(path)}" if path else ""


def _prompt_int(label: str, default: int) -> int:
    prompt = label
    while True:
        s = _ask(prompt, str(default)).strip()
        try:
            return int(s)
        except ValueError:
            prompt = f"{label} (not a number, try again)"


def _prompt_path(label: str, default: str = ".") -> str | None:
    """Prompt for a filesystem path, expanding ~ and making absolute. None
    when cancelled or left blank with no default (a blank answer is never
    silently absolutized into the CWD)."""
    raw = _prompt_str(label, default)
    if raw is None or not raw.strip():
        return None
    return os.path.abspath(os.path.expanduser(raw))


# =====================================
# Menu definitions
# =====================================

_MAIN_SECTIONS = [
    (
        "OUTPUT",
        [
            "Build catalog (full or by wing)",
            "Generate all wing catalogs",
            "Library statistics",
            "Audit (issues report)",
            "Search query export",
        ],
    ),
    (
        "ANALYTICS",
        [
            "Author statistics",
            "Reading pace stats",
            "Tag tree visualization",
            "Wing overlap analysis",
        ],
    ),
    (
        "LISTS",
        [
            "Recently added",
            "Series list (with gap detection)",
            "List wings",
            "Tag dump (flat list with counts)",
        ],
    ),
    (
        "EXPORT",
        [
            "Export (JSON/CSV/AI)",
        ],
    ),
    (
        "SETTINGS",
        [
            "Change database path",
        ],
    ),
    ("", ["Quit"]),
]

# Items that get a letter key instead of a number in the fallback menu.
# Matched on the cleaned label so the mapping follows the sections; the
# letter always maps to the item's own (section, item) tuple.
_LETTER_KEYS = {
    "Quit": "q",
    "Change database path": "s",
}

# Word aliases the typed fallback accepts alongside the generated numbers.
_MAIN_ALIASES: dict[str, tuple | None] = {
    "catalog": (0, 0),
    "all": (0, 1),
    "stats": (0, 2),
    "audit": (0, 3),
    "search": (0, 4),
    "author": (1, 0),
    "pace": (1, 1),
    "tags": (1, 2),
    "overlap": (1, 3),
    "recent": (2, 0),
    "series": (2, 1),
    "wings": (2, 2),
    "tag-dump": (2, 3),
    "tagdump": (2, 3),
    "export": (3, 0),
    "settings": (4, 0),
    "config": (4, 0),
    "quit": None,
    "exit": None,
}


def _build_fallback(sections: list, extra_aliases: dict[str, tuple | None]):
    """Derive the no-curses fallback menu rows and input map from the same
    sections the curses menu renders, so the two can never drift apart (the
    numbered map used to be maintained by hand, which is exactly the pattern
    that went stale in Lattice)."""
    mapping: dict[str, tuple | None] = dict(extra_aliases)
    display: list[tuple[str, list[str]]] = []
    n = 0
    for si, (hdr, items) in enumerate(sections):
        rows = []
        for ii, label in enumerate(items):
            clean = " ".join(label.split())
            letter = _LETTER_KEYS.get(clean)
            if letter is not None:
                rows.append(f"{letter}) {clean}")
                mapping[letter] = (si, ii)
            else:
                n += 1
                rows.append(f"{n}) {clean}")
                mapping[str(n)] = (si, ii)
        display.append((hdr, rows))
    return display, mapping, n


_MAIN_FALLBACK_DISPLAY, _MAIN_FALLBACK_MAP, _MAIN_FALLBACK_MAX = _build_fallback(
    _MAIN_SECTIONS, _MAIN_ALIASES
)

# Named (section, item) results for the non-mode rows, so the dispatch below
# reads without cross-referencing _MAIN_SECTIONS indices.
_SEL_CHANGE_DB = (4, 0)
_SEL_QUIT = (5, 0)


def _select_main() -> tuple | None:
    if _USE_CURSES:
        return _tui_select(f"CalibreQuarry v{VERSION}", _MAIN_SECTIONS)
    _box_menu(f"CalibreQuarry v{VERSION}", _MAIN_FALLBACK_DISPLAY)
    return _fallback_input(
        f"  Select [1-{_MAIN_FALLBACK_MAX}/s/q]: ", _MAIN_FALLBACK_MAP
    )


# =====================================
# Interactive menu loop
# =====================================


def _notify(msg: str) -> None:
    """A notice the user must see before the next menu redraw."""
    if _USE_CURSES:
        _tui_scroll_text("Notice", msg)
    else:
        print(f"  {msg}")


def _resolve_db_input(raw: str) -> str | None:
    """Resolve a user-entered database path (a directory means its
    metadata.db). Returns the absolute path when it exists, else None;
    persisting is the caller's call."""
    path = os.path.join(raw, "metadata.db") if os.path.isdir(raw) else raw
    return os.path.abspath(path) if os.path.exists(path) else None


def _resolve_db_for_tui() -> str | None:
    """Resolve the database path, using TUI prompts for first-run config.
    Returns None when the user cancels the prompt (nothing is persisted)."""
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
        if raw_path is None:
            return None  # cancelled: leave with nothing persisted
        resolved = _resolve_db_input(raw_path)
        if resolved is not None:
            set_db_path(resolved)
            return resolved
        _notify(f"Not found: {raw_path}")


def interactive_menu() -> int:
    """Run one interactive session. Owns the persistent curses screen: it is
    opened once here, every widget draws into it, and it is torn down once on
    the way out, so multi-prompt flows never flash back to the shell. When
    curses can't start (or isn't available), the whole session runs the text
    menu."""
    global _SCREEN, _USE_CURSES
    stdscr = _open_screen() if _USE_CURSES else None
    if _USE_CURSES and stdscr is None:
        _USE_CURSES = False  # curses can't start: the session runs the text menu
    _SCREEN = stdscr
    try:
        return _menu_session()
    except KeyboardInterrupt:
        if _SCREEN is None:
            print()  # no curses screen to restore; just tidy the prompt line
        return 130
    finally:
        if stdscr is not None:
            _close_screen()


def _menu_session() -> int:
    # A session that ends without ever resolving a database exits 1 (the
    # CLI's no-database signal), whether the first-run prompt was cancelled
    # or its input ran out; nothing was configured and nothing ran.
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

        if result == "fallback":
            continue  # curses init failed; the next pass renders the text menu

        if result == "invalid":
            if not _USE_CURSES:
                print("  Invalid selection.")
            continue

        if result is None or result == _SEL_QUIT:
            return 0

        if result == _SEL_CHANGE_DB:
            new_path = _prompt_path(f"Change database (current: {db_path})", db_path)
            if new_path is None:
                continue  # cancelled: the saved path stays as it was
            resolved = _resolve_db_input(new_path)
            if resolved is not None:
                set_db_path(resolved)
            else:
                _notify(f"Not found: {new_path} (database unchanged)")
            continue

        try:
            with CalibreDB(db_path) as db:
                if result == (0, 0):
                    wing = _ask("Wing name (blank for all)", "") or None
                    primary = _ask_yn("Primary author only? (y/N)")
                    tags = _ask_yn("Show tags instead of ratings? (y/N)")
                    ids = _ask_yn("Show book IDs? (y/N)")
                    output = _prompt_out("Output file", "catalog.txt")
                    _reset_terminal()
                    _run_with_capture(
                        "Catalog",
                        lambda o=output, w=wing, p=primary, t=tags, i=ids: (
                            write_catalog(
                                db,
                                o,
                                wing=w,
                                primary_only=p,
                                show_tags=t,
                                show_id=i,
                            )
                        ),
                        footer=_out_note(output),
                    )

                elif result == (0, 1):
                    outdir = _prompt_out("Output directory", "catalogs")
                    primary = _ask_yn("Primary author only? (y/N)")
                    tags = _ask_yn("Show tags instead of ratings? (y/N)")
                    ids = _ask_yn("Show book IDs? (y/N)")
                    _reset_terminal()
                    _run_with_capture(
                        "Generate Wings",
                        lambda o=outdir, p=primary, t=tags, i=ids: write_all_wings(
                            db,
                            o,
                            primary_only=p,
                            show_tags=t,
                            show_id=i,
                        ),
                        footer=f"Wings written to {os.path.abspath(outdir)}",
                    )

                elif result == (0, 2):
                    _reset_terminal()
                    _run_with_capture("Statistics", lambda: show_stats(db))

                elif result == (0, 3):
                    output = _prompt_out("Output CSV", "audit.csv")
                    _reset_terminal()
                    _run_with_capture(
                        "Audit",
                        lambda o=output: run_audit(db, o),
                        footer=_out_note(output),
                    )

                elif result == (0, 4):
                    query = _ask("Search query (Calibre format)", "")
                    if query:
                        output = _prompt_out("Output file", "search_results.txt")
                        _reset_terminal()
                        _run_with_capture(
                            "Search Results",
                            lambda q=query, o=output: run_search_export(db, q, o),
                            footer=_out_note(output),
                        )

                elif result == (1, 0):
                    _reset_terminal()
                    _run_with_capture("Author Stats", lambda: show_author_stats(db))

                elif result == (1, 1):
                    _reset_terminal()
                    _run_with_capture("Reading Pace", lambda: show_pace_stats(db))

                elif result == (1, 2):
                    _reset_terminal()
                    _run_with_capture("Tag Tree", lambda: show_tag_tree(db))

                elif result == (1, 3):
                    _reset_terminal()
                    _run_with_capture("Wing Overlap", lambda: show_wing_overlap(db))

                elif result == (2, 0):
                    count = _prompt_int("How many", 20)
                    _reset_terminal()
                    _run_with_capture(
                        "Recently Added", lambda c=count: show_recent(db, c)
                    )

                elif result == (2, 1):
                    _reset_terminal()
                    _run_with_capture("Series List", lambda: show_series(db))

                elif result == (2, 2):
                    _reset_terminal()
                    _run_with_capture("Virtual Libraries", lambda: show_wings(db))

                elif result == (2, 3):
                    _reset_terminal()
                    _run_with_capture("Tag Dump", lambda: show_tag_dump(db))

                elif result == (3, 0):
                    fmt = _ask("Format (json/csv/ai)", "json").strip().lower()
                    while fmt not in ("json", "csv", "ai"):
                        fmt = (
                            _ask("Format must be json, csv or ai", "json")
                            .strip()
                            .lower()
                        )
                    output = _prompt_out("Output file", f"library.{fmt}")
                    _reset_terminal()
                    _run_with_capture(
                        "Export",
                        lambda o=output, f=fmt: run_export(db, o, f),
                        footer=_out_note(output),
                    )
        except _Cancelled:
            continue  # Esc in a prompt chain: back to the menu, nothing launched
