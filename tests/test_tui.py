"""Tests for the TUI, ported from the Lattice TUI audit (2026-07-01): a mode
exception is paged instead of propagated, a curses init failure degrades to
the text fallback instead of reading as Quit, Esc in a prompt chain cancels
back to the menu, output prompts expand ~, the export format is validated,
and the no-curses fallback menu is generated from the same sections the
curses menu renders."""

import os
import tempfile
import unittest

from cquarry import tui
from cquarry.tui import (
    _LETTER_KEYS,
    _MAIN_ALIASES,
    _MAIN_FALLBACK_DISPLAY,
    _MAIN_FALLBACK_MAP,
    _MAIN_FALLBACK_MAX,
    _MAIN_SECTIONS,
    _build_fallback,
)


class RunWithCaptureTests(unittest.TestCase):
    """A mode error or Ctrl-C is paged (with whatever output was captured),
    never propagated as a raw traceback that loses the results."""

    def setUp(self):
        self.pages = []
        self._pager, self._pause = tui._tui_scroll_text, tui._pause
        self._use = tui._USE_CURSES
        tui._tui_scroll_text = lambda title, text: self.pages.append((title, text))
        tui._pause = lambda: None
        tui._USE_CURSES = True  # route output to the (monkeypatched) pager

    def tearDown(self):
        tui._tui_scroll_text, tui._pause = self._pager, self._pause
        tui._USE_CURSES = self._use

    def test_mode_exception_is_paged_not_propagated(self):
        def boom():
            print("partial output")
            raise RuntimeError("mode blew up")

        tui._run_with_capture("T", boom)
        self.assertEqual(len(self.pages), 1)
        text = self.pages[0][1]
        self.assertIn("[Error]", text)
        self.assertIn("RuntimeError: mode blew up", text)
        self.assertIn("partial output", text)

    def test_keyboard_interrupt_pages_cancel_notice(self):
        def cancelled():
            print("listed 10 books")
            raise KeyboardInterrupt

        tui._run_with_capture("T", cancelled)
        text = self.pages[0][1]
        self.assertIn("[Cancelled]", text)
        self.assertIn("listed 10 books", text)

    def test_normal_output_still_paged(self):
        tui._run_with_capture("T", lambda: print("hello"))
        self.assertIn("hello", self.pages[0][1])

    def test_footer_shown_on_success(self):
        tui._run_with_capture("T", lambda: print("done"), footer="Report written")
        self.assertIn("Report written", self.pages[0][1])

    def test_footer_suppressed_on_error_and_cancel(self):
        # The footer claims an output file exists; a mode that died or was
        # cancelled never finished writing one.
        def boom():
            raise RuntimeError("no file was written")

        def cancelled():
            raise KeyboardInterrupt

        tui._run_with_capture("T", boom, footer="Report written to /x")
        tui._run_with_capture("T", cancelled, footer="Report written to /x")
        for _title, text in self.pages:
            self.assertNotIn("Report written", text)


@unittest.skipUnless(tui.HAVE_CURSES, "curses not available")
class CursesInitFailureTests(unittest.TestCase):
    """A curses init failure must degrade to the text fallback, not read as
    the user choosing Quit (silent exit 0 on capability-poor terminals)."""

    def test_tui_select_returns_fallback_sentinel_on_curses_error(self):
        orig_wrapper, orig_use = tui.curses.wrapper, tui._USE_CURSES

        def boom(_fn):
            raise tui.curses.error("setupterm failed")

        tui.curses.wrapper = boom
        tui._USE_CURSES = True
        try:
            result = tui._tui_select("T", [("", ["One"])])
            self.assertEqual(result, "fallback")
            self.assertFalse(tui._USE_CURSES)
        finally:
            tui.curses.wrapper = orig_wrapper
            tui._USE_CURSES = orig_use

    def test_init_tui_colors_nonfatal_without_color_support(self):
        orig = tui.curses.start_color

        def boom():
            raise tui.curses.error("no colors")

        tui.curses.start_color = boom
        try:
            tui._init_tui_colors()  # must not raise
        finally:
            tui.curses.start_color = orig

    def test_curs_set_failure_is_swallowed(self):
        orig = tui.curses.curs_set

        def boom(_v):
            raise tui.curses.error("no cursor caps")

        tui.curses.curs_set = boom
        try:
            tui._curs_set(0)  # must not raise
        finally:
            tui.curses.curs_set = orig


def _positions(sections):
    """Every (section, item) pair, split into numbered entries and letter-keyed
    ones, mirroring how _build_fallback walks the sections."""
    numbered = []
    lettered = {}
    for si, (_hdr, items) in enumerate(sections):
        for ii, label in enumerate(items):
            clean = " ".join(label.split())
            if clean in _LETTER_KEYS:
                lettered[clean] = (si, ii)
            else:
                numbered.append(((si, ii), clean))
    return numbered, lettered


class FallbackMenuTests(unittest.TestCase):
    """The typed-input fallback is generated from the curses sections; these
    pin that every entry is reachable and numbers line up with labels (the
    hand-maintained map drifted once in Lattice and dispatched wrong modes)."""

    def test_every_entry_has_a_number_matching_its_label(self):
        numbered, _ = _positions(_MAIN_SECTIONS)
        rows = [row for _hdr, rows in _MAIN_FALLBACK_DISPLAY for row in rows]
        for n, (pos, clean) in enumerate(numbered, 1):
            self.assertEqual(_MAIN_FALLBACK_MAP[str(n)], pos)
            self.assertIn(f"{n}) {clean}", rows)
        self.assertEqual(_MAIN_FALLBACK_MAX, len(numbered))

    def test_letter_keys(self):
        _, lettered = _positions(_MAIN_SECTIONS)
        self.assertEqual(_MAIN_FALLBACK_MAP["s"], lettered["Change database path"])
        # "q" maps to the Quit row itself; the dispatch treats it as Quit.
        self.assertEqual(_MAIN_FALLBACK_MAP["q"], lettered["Quit"])
        self.assertEqual(_MAIN_FALLBACK_MAP["q"], tui._SEL_QUIT)

    def test_aliases_target_real_entries(self):
        valid = {
            (si, ii)
            for si, (_h, items) in enumerate(_MAIN_SECTIONS)
            for ii in range(len(items))
        }
        for alias, target in _MAIN_ALIASES.items():
            if target is not None:
                self.assertIn(target, valid, f"alias {alias!r}")

    def test_export_number_dispatches_export_not_settings(self):
        # The Lattice 2026-06-10 bug shape: a fallback number labelled one
        # mode dispatching another. Pin the Export label's number.
        numbered, _ = _positions(_MAIN_SECTIONS)
        for n, (pos, clean) in enumerate(numbered, 1):
            if clean.startswith("Export"):
                self.assertEqual(_MAIN_FALLBACK_MAP[str(n)], pos)
                si, ii = pos
                self.assertTrue(_MAIN_SECTIONS[si][1][ii].startswith("Export"))
                return
        self.fail("no Export entry in _MAIN_SECTIONS")

    def test_display_rows_match_map_size(self):
        rows = sum(len(rows) for _hdr, rows in _MAIN_FALLBACK_DISPLAY)
        numbers = sum(1 for k in _MAIN_FALLBACK_MAP if k.isdigit())
        letters = sum(
            1 for k in _MAIN_FALLBACK_MAP if len(k) == 1 and k.isalpha() and k in "qs"
        )
        self.assertEqual(rows, numbers + letters)

    def test_build_fallback_is_pure(self):
        sections = [("A", ["One", "Two"]), ("", ["Quit"])]
        display, mapping, n = _build_fallback(sections, {"x": (0, 1)})
        self.assertEqual(n, 2)
        self.assertEqual(mapping["1"], (0, 0))
        self.assertEqual(mapping["2"], (0, 1))
        self.assertEqual(mapping["x"], (0, 1))
        self.assertEqual(mapping["q"], (1, 0))  # letter keys map to their own row
        self.assertEqual(display, [("A", ["1) One", "2) Two"]), ("", ["q) Quit"])])

    def test_aliases_point_at_their_labels(self):
        # Aliases are hand-written (section, item) tuples; pin each one to the
        # label it claims so inserting a mode can't silently retarget them.
        expected = {
            "catalog": "Build catalog",
            "all": "Generate all wing",
            "stats": "Library statistics",
            "audit": "Audit",
            "search": "Search query export",
            "author": "Author statistics",
            "pace": "Reading pace",
            "tags": "Tag tree",
            "overlap": "Wing overlap",
            "recent": "Recently added",
            "series": "Series list",
            "wings": "List wings",
            "tag-dump": "Tag dump",
            "tagdump": "Tag dump",
            "export": "Export",
            "settings": "Change database path",
            "config": "Change database path",
        }
        targeting = {a: t for a, t in _MAIN_ALIASES.items() if t is not None}
        self.assertEqual(set(targeting), set(expected))
        for alias, prefix in expected.items():
            si, ii = targeting[alias]
            self.assertTrue(
                _MAIN_SECTIONS[si][1][ii].startswith(prefix),
                f"alias {alias!r} points at {_MAIN_SECTIONS[si][1][ii]!r}",
            )

    def test_named_selection_constants_point_at_their_labels(self):
        si, ii = tui._SEL_CHANGE_DB
        self.assertEqual(_MAIN_SECTIONS[si][1][ii], "Change database path")
        si, ii = tui._SEL_QUIT
        self.assertEqual(_MAIN_SECTIONS[si][1][ii], "Quit")


class _FakeDB:
    """Stands in for CalibreDB so menu tests never open a real database."""

    def __init__(self, path):
        self.path = path

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _MenuHarness(unittest.TestCase):
    """Drives interactive_menu() hermetically: the DB is faked, the pager and
    pause never touch the terminal, and selections/prompts come from
    iterators."""

    def setUp(self):
        self.pages = []
        self._orig = (
            tui._USE_CURSES,
            tui._select_main,
            tui._prompt_str,
            tui._tui_scroll_text,
            tui._pause,
            tui.get_db_path,
            tui.CalibreDB,
        )
        tui._USE_CURSES = False
        tui._tui_scroll_text = lambda title, text: self.pages.append((title, text))
        tui._pause = lambda: None
        tui.CalibreDB = _FakeDB

    def tearDown(self):
        (
            tui._USE_CURSES,
            tui._select_main,
            tui._prompt_str,
            tui._tui_scroll_text,
            tui._pause,
            tui.get_db_path,
            tui.CalibreDB,
        ) = self._orig

    def _wire(self, db_file, selections, prompts=None):
        tui.get_db_path = lambda: db_file
        sel_iter = iter(selections)
        tui._select_main = lambda: next(sel_iter)
        if prompts is not None:
            p_iter = iter(prompts)
            tui._prompt_str = lambda label, default: next(p_iter, None)
        else:
            tui._prompt_str = lambda label, default: default or ""

    def _run(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = tui.interactive_menu()
        return rc, buf.getvalue()


class EscCancelTests(_MenuHarness):
    """T2: Esc in a prompt chain cancels back to the menu; it must never
    launch a mode with defaults."""

    def test_cancelled_prompt_aborts_without_running_mode(self):
        calls = []
        orig = tui.write_catalog
        tui.write_catalog = lambda *a, **k: calls.append(a)
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as db:
                # Select "Build catalog", cancel at the first prompt, then Quit.
                self._wire(db.name, [(0, 0), None], prompts=[None])
                rc, _ = self._run()
        finally:
            tui.write_catalog = orig
        self.assertEqual(rc, 0)
        self.assertEqual(calls, [])

    def test_ask_raises_cancelled_on_none(self):
        orig = tui._prompt_str
        tui._prompt_str = lambda label, default: None
        try:
            with self.assertRaises(tui._Cancelled):
                tui._ask("Anything", "x")
        finally:
            tui._prompt_str = orig

    def test_cancelled_first_run_exits_nonzero_without_persisting(self):
        # No database was ever resolved, so the session exits 1 (the CLI's
        # no-database signal); unattended runs must not read as success.
        saved = []
        orig = (tui.get_db_path, tui.set_db_path, tui._prompt_str)
        tui.get_db_path = lambda: None
        tui.set_db_path = lambda p: saved.append(p)
        tui._prompt_str = lambda label, default: None  # cancel immediately
        try:
            rc = tui.interactive_menu()
        finally:
            tui.get_db_path, tui.set_db_path, tui._prompt_str = orig
        self.assertEqual(rc, 1)
        self.assertEqual(saved, [])

    def test_cancelled_change_db_keeps_saved_path(self):
        saved = []
        orig = tui.set_db_path
        tui.set_db_path = lambda p: saved.append(p)
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as db:
                self._wire(db.name, [tui._SEL_CHANGE_DB, None], prompts=[None])
                rc, _ = self._run()
        finally:
            tui.set_db_path = orig
        self.assertEqual(rc, 0)
        self.assertEqual(saved, [])


class ExportFormatValidationTests(_MenuHarness):
    """T5d analog: the export format prompt re-asks until json/csv/ai."""

    def test_gibberish_reprompts_until_valid(self):
        calls = []
        orig = tui.run_export
        tui.run_export = lambda db, output, fmt: calls.append((output, fmt))
        try:
            with tempfile.NamedTemporaryFile(suffix=".db") as db:
                self._wire(
                    db.name,
                    [(3, 0), None],
                    prompts=["xml", "csv", "out.csv"],
                )
                self._run()
        finally:
            tui.run_export = orig
        self.assertEqual(calls, [("out.csv", "csv")])


class PromptHelperTests(unittest.TestCase):
    """T4/T6a: output prompts expand ~ (but stay relative otherwise); a bad
    integer re-prompts with a note instead of silently using the default."""

    def setUp(self):
        self._orig = tui._prompt_str

    def tearDown(self):
        tui._prompt_str = self._orig

    def test_prompt_out_expands_tilde(self):
        tui._prompt_str = lambda label, default: "~/reports/x.txt"
        self.assertEqual(
            tui._prompt_out("Output file", "d.txt"),
            os.path.expanduser("~/reports/x.txt"),
        )

    def test_prompt_out_keeps_relative_paths_relative(self):
        tui._prompt_str = lambda label, default: "x.txt"
        self.assertEqual(tui._prompt_out("Output file", "d.txt"), "x.txt")

    def test_prompt_int_reprompts_on_garbage(self):
        labels = []
        vals = iter(["abc", "7"])

        def prompt(label, default):
            labels.append(label)
            return next(vals)

        tui._prompt_str = prompt
        self.assertEqual(tui._prompt_int("How many", 20), 7)
        self.assertIn("not a number", labels[1])

    def test_prompt_path_returns_none_on_cancel(self):
        tui._prompt_str = lambda label, default: None
        self.assertIsNone(tui._prompt_path("Path", "."))

    def test_out_note_is_absolute(self):
        note = tui._out_note("x.txt")
        self.assertIn(os.path.abspath("x.txt"), note)
        self.assertEqual(tui._out_note(None), "")


class _KeyScreen:
    """A stand-in curses screen that replays a scripted key sequence.

    get_wch returns a str for a character and an int for a key code, which is
    exactly the distinction _tui_prompt_str has to handle.
    """

    def __init__(self, keys):
        self._keys = list(keys)

    def get_wch(self):
        if not self._keys:
            raise AssertionError("prompt asked for more input than the test scripted")
        return self._keys.pop(0)

    def erase(self):
        pass

    def getmaxyx(self):
        return 24, 80

    def addstr(self, *a, **kw):
        pass

    def move(self, *a):
        pass

    def refresh(self):
        pass


class PromptInputTests(unittest.TestCase):
    """The curses prompt reads whole characters, not bytes: a Calibre library
    is full of titles and authors that cannot be typed in ASCII."""

    def setUp(self):
        self._screen = tui._SCREEN
        # color_pair needs a real initscr'd terminal; the drawing is not what
        # these tests are about, so neutralize it.
        self._color_pair = tui.curses.color_pair
        tui.curses.color_pair = lambda _n: 0

    def tearDown(self):
        tui._SCREEN = self._screen
        tui.curses.color_pair = self._color_pair

    def _prompt(self, keys, default=""):
        tui._SCREEN = _KeyScreen(keys)
        return tui._tui_prompt_str("Query", default)

    def test_accepts_non_ascii_characters(self):
        # getch handed back one byte at a time, so 'ë' arrived as two
        # out-of-range values and was dropped silently.
        self.assertEqual(self._prompt(["B", "r", "o", "n", "t", "ë", "\n"]), "Brontë")

    def test_accepts_ascii_and_int_key_codes_alike(self):
        self.assertEqual(self._prompt(["a", ord("b"), "\n"]), "ab")

    def test_backspace_and_ctrl_u_still_edit(self):
        self.assertEqual(self._prompt(["a", "b", "\x7f", "c", "\n"]), "ac")
        self.assertEqual(self._prompt(["a", "b", "\x15", "z", "\n"]), "z")

    def test_escape_still_cancels(self):
        self.assertIsNone(self._prompt(["a", "\x1b"], default="d.txt"))

    def test_bare_enter_still_takes_the_default(self):
        self.assertEqual(self._prompt(["\n"], default="catalog.txt"), "catalog.txt")

    def test_control_characters_are_not_inserted(self):
        # a stray unhandled control char must not land in the buffer
        self.assertEqual(self._prompt(["a", "\x07", "b", "\n"]), "ab")


class PersistentScreenTests(unittest.TestCase):
    """T7: one curses screen per session. Widgets run against the session's
    screen when one is active; a mid-session curses failure degrades the
    whole session to the text fallback in one place."""

    def test_with_screen_uses_session_screen(self):
        marker = object()
        orig = tui._SCREEN
        tui._SCREEN = marker
        try:
            self.assertIs(tui._with_screen(lambda scr: scr), marker)
        finally:
            tui._SCREEN = orig

    @unittest.skipUnless(tui.HAVE_CURSES, "curses not available")
    def test_with_screen_falls_back_to_wrapper_without_session(self):
        calls = []
        orig = tui.curses.wrapper

        def fake_wrapper(fn):
            calls.append(fn)
            return "wrapped"

        tui.curses.wrapper = fake_wrapper
        try:
            self.assertIsNone(tui._SCREEN)
            self.assertEqual(tui._with_screen(lambda scr: scr), "wrapped")
        finally:
            tui.curses.wrapper = orig
        self.assertEqual(len(calls), 1)

    def test_degrade_to_text_clears_all_session_state(self):
        orig = (tui._SCREEN, tui._USE_CURSES)
        tui._SCREEN = object()
        tui._USE_CURSES = True
        try:
            tui._degrade_to_text()
            self.assertIsNone(tui._SCREEN)
            self.assertFalse(tui._USE_CURSES)
        finally:
            tui._SCREEN, tui._USE_CURSES = orig

    def test_reset_terminal_is_a_noop_while_session_screen_lives(self):
        # stty sane mid-session would undo cbreak/noecho under curses' feet.
        ran = []
        orig_screen = tui._SCREEN
        orig_run = tui.subprocess.run
        tui._SCREEN = object()
        tui.subprocess.run = lambda *a, **k: ran.append(a)
        try:
            tui._reset_terminal()
        finally:
            tui._SCREEN = orig_screen
            tui.subprocess.run = orig_run
        self.assertEqual(ran, [])


class MenuInterruptTests(_MenuHarness):
    """T7: Ctrl-C at the menu exits the session cleanly (130), instead of
    propagating a traceback with the terminal half-restored. Applies to the
    text-fallback menu too: _fallback_input lets KeyboardInterrupt through
    instead of swallowing it into a clean Quit."""

    def test_ctrl_c_at_menu_exits_130(self):
        def boom():
            raise KeyboardInterrupt

        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            tui.get_db_path = lambda: db.name
            tui._select_main = boom
            rc, _ = self._run()
        self.assertEqual(rc, 130)

    def test_fallback_input_propagates_ctrl_c(self):
        import builtins

        orig = builtins.input

        def fake(prompt=""):
            raise KeyboardInterrupt

        builtins.input = fake
        try:
            with self.assertRaises(KeyboardInterrupt):
                tui._fallback_input("Select: ", {})
        finally:
            builtins.input = orig

    def test_fallback_input_eof_is_quiet_quit(self):
        import builtins

        orig = builtins.input

        def fake(prompt=""):
            raise EOFError

        builtins.input = fake
        try:
            self.assertIsNone(tui._fallback_input("Select: ", {}))
        finally:
            builtins.input = orig


class FallbackSentinelLoopTests(unittest.TestCase):
    def test_menu_loop_reenters_on_fallback_sentinel(self):
        # The sentinel must loop back into the menu (which now renders the
        # text fallback), not fall through as a Quit or a selection.
        selections = iter(["fallback", None])
        orig = (tui._USE_CURSES, tui._select_main, tui.get_db_path)
        with tempfile.NamedTemporaryFile(suffix=".db") as db:
            tui._USE_CURSES = False
            tui._select_main = lambda: next(selections)
            tui.get_db_path = lambda: db.name
            try:
                rc = tui.interactive_menu()
            finally:
                tui._USE_CURSES, tui._select_main, tui.get_db_path = orig
        self.assertEqual(rc, 0)  # sentinel looped, second pass quit cleanly


if __name__ == "__main__":
    unittest.main()
