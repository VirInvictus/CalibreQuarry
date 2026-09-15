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

from cquarry.db import CalibreDB

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

    def test_saved_config_wins_over_cwd_strays(self):
        # The sweep's P1: the TUI consulted a CWD-relative metadata.db
        # before the saved config and silently rebound it. The saved path
        # now wins and nothing is rewritten.
        with (
            mock.patch.object(tui, "get_db_path", return_value=self.good),
            mock.patch.object(tui, "set_db_path") as set_path,
            mock.patch.object(tui, "_resolve_db_input") as resolve_input,
        ):
            self.assertEqual(tui._resolve_db_for_tui(), self.good)
        set_path.assert_not_called()  # no silent rebind
        resolve_input.assert_not_called()  # no first-run prompt

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


class TestMenuSections(unittest.TestCase):
    """The menu structure: the Phase 19 read surfaces have entries, and
    the Settings coordinates the s/q aliases pin are unchanged."""

    def test_new_read_surfaces_are_on_the_menu(self):
        sections = tui._menu_sections()
        first = dict(sections)[""]
        analytics = dict(sections)["Analytics"]
        self.assertIn("Content Search (FTS)", first)
        self.assertIn("Saved Search Catalogs", first)
        self.assertIn("Library Health", first)
        self.assertIn("Reading Analytics", analytics)
        self.assertIn("FTS Index Status", analytics)

    def test_settings_coordinates_are_stable(self):
        # s/q are letter aliases onto (5, 0)/(5, 1): inserting a section
        # or reordering Settings breaks them silently.
        sections = tui._menu_sections()
        self.assertEqual(sections[-1][0], "Settings")
        self.assertEqual(sections[-1][1], ["Change Database", "Quit"])
        self.assertEqual(tui._SEL_CHANGE_DB, (5, 0))
        self.assertEqual(tui._SEL_QUIT, (5, 1))


class TestRestrictedPrompt(unittest.TestCase):
    """The shared scope prompt behind the new entries: blank keeps the
    whole library, an expression wraps the session db in the CLI's
    RestrictedView, a parse failure notifies and stays unrestricted."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_tuir_")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
                last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT,
                uuid TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT,
                author INT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT,
                tag INT);
            CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT,
                series INT);
            CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT);
            CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT,
                rating INT);
            CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT,
                publisher INT);
            CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
            CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT,
                lang_code INT);
            CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
                name TEXT, uncompressed_size INT);
            CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
            CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
            """
        )
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            "(1,'Dune','Dune','H','2024-01-01','2020-01-01',0,'2024-01-01',"
            "1.0,'p1','u1'), (2,'Emma','Emma','A','2024-01-01','2020-01-01',0,"
            "'2024-01-01',1.0,'p2','u2')"
        )
        con.commit()
        con.close()
        self.db = CalibreDB(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_blank_keeps_the_whole_library(self):
        with mock.patch.object(tui, "ask", return_value=""):
            self.assertIs(tui._restricted(self.db), self.db)

    def test_an_expression_scopes_the_view(self):
        with mock.patch.object(tui, "ask", return_value="id:1"):
            view = tui._restricted(self.db)
        self.assertIsNot(view, self.db)
        self.assertEqual(view.restrict_ids, frozenset({1}))
        self.assertIs(view.origin, self.db)

    def test_a_parse_failure_notifies_through_the_blocking_notice(self):
        # The note used to be a bare print, which the caller's following
        # reset_terminal() erased before it could be read: a typo'd scope
        # ran the mode UNRESTRICTED silently. _notify blocks on Enter, so
        # the notice survives the reset.
        with (
            mock.patch.object(tui, "ask", return_value="((nope"),
            mock.patch.object(tui, "_notify") as notify_mock,
        ):
            view = tui._restricted(self.db)
        self.assertIs(view, self.db)
        self.assertTrue(notify_mock.called)
