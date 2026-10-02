"""Tests for the library-schema writes (--saved-search add/delete/rename).

The verbs ride cquarry 1.24's typed preference writer against a temp
database; the closed-Calibre pgrep guard is stubbed so the suite never
depends on whether the real Calibre happens to be running. The dry run
reads through CalibreDB and must never open the write handle.
"""

import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from cquarry_cli.cli import main

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, author_sort TEXT,
    timestamp TEXT, pubdate TEXT, has_cover INT, last_modified TEXT,
    series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
-- Calibre's real preferences table carries UNIQUE(key): INSERT OR REPLACE
-- upserts on it. Without the constraint the writers insert second rows and
-- every fetchone() read keeps seeing the first, stale one.
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT NOT NULL,
    val TEXT NOT NULL, UNIQUE (key));
"""

_SEED_SEARCHES = '{"SciFi Picks": "tags:Fic.SciFi", "Recent": "date:>7d"}'


class _SchemaWriteCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cquarry_schema_")
        self.addCleanup(self._rm)
        self.db_path = os.path.join(self._tmp, "metadata.db")
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        con.execute("INSERT INTO books (id,title) VALUES (1, 'One')")
        con.execute(
            "INSERT INTO preferences (key, val) VALUES ('saved_searches', ?)",
            (_SEED_SEARCHES,),
        )
        con.commit()
        con.close()
        pg = mock.patch("cquarry_cli.schemawrite._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def _rm(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def stored(self):
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute(
                "SELECT val FROM preferences WHERE key = 'saved_searches'"
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def backups(self):
        return self._tmp + "-backups"


class TestSavedSearchWrites(_SchemaWriteCase):
    def test_dry_run_shows_the_plan_and_the_current_set(self):
        code, out, _ = self.run_cli(
            "--saved-search-add", "Fic", "tags:Fic", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("saved-search plan: add saved search 'Fic'", out)
        self.assertIn("SciFi Picks", out)
        self.assertIn("Dry run", out)
        self.assertEqual(self.stored(), _SEED_SEARCHES)  # untouched

    def test_add_apply_writes_and_readd_is_already_so(self):
        code, out, _ = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("applied: add saved search 'Fic'", out)
        self.assertIn("Fic", self.stored())
        # The add is an upsert; an identical re-add is honest already-so.
        code, out, _ = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("already-so", out)

    def test_delete_and_rename_round_trip(self):
        code, out, _ = self.run_cli(
            "--saved-search-rename",
            "Recent",
            "Last Week",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("applied: rename saved search 'Recent' -> 'Last Week'", out)
        stored = self.stored()
        self.assertIn("Last Week", stored)
        self.assertNotIn('"Recent"', stored)
        code, out, _ = self.run_cli(
            "--saved-search-delete",
            "Last Week",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertNotIn("Last Week", self.stored())

    def test_delete_unknown_is_an_honest_already_so(self):
        code, out, _ = self.run_cli(
            "--saved-search-delete",
            "Nope",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("already-so", out)

    def test_rename_onto_existing_name_refuses(self):
        # Upstream's saved_searches add silently overwrites; the cquarry
        # rename refuses, and the refusal is exit 1 (validation class).
        code, _, err = self.run_cli(
            "--saved-search-rename",
            "Recent",
            "SciFi Picks",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_rename_unknown_old_refuses(self):
        code, _, err = self.run_cli(
            "--saved-search-rename",
            "Nope",
            "Whatever",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 1)
        self.assertIn("No saved search named", err)

    def test_combination_with_a_book_verb_refused(self):
        code, _, err = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--set-title",
            "1",
            "X",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("refuse company", err)

    def test_combination_with_a_set_source_refused(self):
        code, _, err = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--ids",
            "1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("refuse company", err)

    def test_two_saved_search_verbs_refused(self):
        code, _, err = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--saved-search-delete",
            "Recent",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("one saved-search write per invocation", err)

    def test_apply_demands_backup_dir(self):
        code, _, err = self.run_cli(
            "--saved-search-add", "Fic", "tags:Fic", "--apply", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)
        self.assertEqual(self.stored(), _SEED_SEARCHES)  # nothing written

    def test_apply_takes_a_timestamped_backup(self):
        code, out, _ = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        import glob

        self.assertTrue(glob.glob(os.path.join(self.backups(), "metadata-*.db")))

    def test_live_calibre_refuses_apply(self):
        with mock.patch("cquarry_cli.schemawrite._calibre_running", return_value=True):
            code, _, err = self.run_cli(
                "--saved-search-add",
                "Fic",
                "tags:Fic",
                "--apply",
                "--backup-dir",
                self.backups(),
                "--db",
                self.db_path,
            )
        self.assertEqual(code, 1)
        self.assertIn("Calibre is running", err)

    def test_restrict_refuses_the_combination(self):
        code, _, err = self.run_cli(
            "--restrict",
            "tags:1",
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("--restrict", err)


if __name__ == "__main__":
    unittest.main()
