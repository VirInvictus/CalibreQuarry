"""The TUI must survive a malformed database (the 2026-09-08 sweep's P0:
CalibreDB was constructed outside every exception boundary, so a corrupt
or foreign sqlite file at the chosen path ended the session in a raw
traceback, including via Change Database). These tests drive the real
degrade wiring with the interactive layer patched out: a garbage file,
a valid sqlite file that is not a Calibre library, and a healthy fixture
all get handled without a traceback.
"""

import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from cquarry_cli import tui

_GARBAGE = b"this is definitely not a sqlite database\n" * 8


def _build_minimal_library(db_path: str) -> None:
    # Just enough Calibre for the open probe (SELECT 1 FROM books).
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
    con.execute("INSERT INTO books (title) VALUES ('Dune')")
    con.commit()
    con.close()


class TestDbOpens(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cquarry_tui_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_garbage_file_does_not_open(self):
        path = os.path.join(self.temp_dir, "metadata.db")
        with open(path, "wb") as f:
            f.write(_GARBAGE)
        self.assertFalse(tui._db_opens(path))

    def test_foreign_sqlite_does_not_open(self):
        # A valid sqlite file that is not a Calibre library fails the same
        # probe (no books table) and must degrade the same way.
        path = os.path.join(self.temp_dir, "other.db")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        self.assertFalse(tui._db_opens(path))

    def test_minimal_calibre_fixture_opens(self):
        path = os.path.join(self.temp_dir, "metadata.db")
        _build_minimal_library(path)
        self.assertTrue(tui._db_opens(path))


class TestMenuDegrade(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cquarry_tui_")
        self.good = os.path.join(self.temp_dir, "library", "metadata.db")
        os.makedirs(os.path.dirname(self.good))
        _build_minimal_library(self.good)
        self.bad = os.path.join(self.temp_dir, "junk", "metadata.db")
        os.makedirs(os.path.dirname(self.bad))
        with open(self.bad, "wb") as f:
            f.write(_GARBAGE)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_malformed_start_db_degrades_to_a_notice_and_reprompt(self):
        # The sweep's P0 scenario: the resolved path is garbage. The menu
        # says so in prose and re-prompts; quitting there exits cleanly.
        with (
            mock.patch.object(tui, "_resolve_db_for_tui", side_effect=[self.bad, None]),
            mock.patch.object(tui, "get_db_path", return_value=None),
            mock.patch.object(tui, "_notify") as notify,
        ):
            rc = tui._menu_session()
        self.assertEqual(rc, 1)
        (message,), _ = notify.call_args
        self.assertIn("not a readable Calibre database", message)

    def test_change_database_refuses_a_malformed_file_and_keeps_the_old(self):
        with (
            mock.patch.object(tui, "_resolve_db_for_tui", side_effect=[self.good]),
            mock.patch.object(tui, "get_db_path", return_value=None),
            mock.patch.object(tui, "_db_opens", side_effect=[True, False, True]),
            mock.patch.object(
                tui, "_select_main", side_effect=[tui._SEL_CHANGE_DB, tui._SEL_QUIT]
            ),
            mock.patch.object(tui, "prompt_path", return_value=self.bad),
            mock.patch.object(tui, "reset_terminal"),
            mock.patch.object(tui, "_notify") as notify,
            mock.patch.object(tui, "set_db_path") as set_path,
        ):
            rc = tui._menu_session()
        self.assertEqual(rc, 0)
        set_path.assert_not_called()  # the old database stays configured
        messages = " | ".join(str(c.args[0]) for c in notify.call_args_list)
        self.assertIn("database unchanged", messages)


if __name__ == "__main__":
    unittest.main()
