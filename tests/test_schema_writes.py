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

from _fixtures import SCHEMA as _SCHEMA

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
        self.assertIn("schema-write plan: add saved search 'Fic'", out)
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

    def test_add_column_dry_run_plans_and_sets_nothing(self):
        code, out, _ = self.run_cli(
            "--add-custom-column",
            "lcc",
            "LCC",
            "text",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        self.assertIn("create custom column #lcc ('LCC', text)", out)
        self.assertIn("update_all_last_mod_dates_on_start", out)
        con = sqlite3.connect(self.db_path)
        n = con.execute("SELECT COUNT(*) FROM custom_columns").fetchone()[0]
        con.close()
        self.assertEqual(n, 0)

    def test_add_column_apply_creates_it(self):
        code, out, _ = self.run_cli(
            "--add-custom-column",
            "hardcover",
            "Hardcover ID",
            "text",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        self.assertIn("applied: create custom column #hardcover", out)
        con = sqlite3.connect(self.db_path)
        row = con.execute(
            "SELECT label, name, datatype FROM custom_columns WHERE label = 'hardcover'"
        ).fetchone()
        con.close()
        self.assertEqual(row, ("hardcover", "Hardcover ID", "text"))

    def test_add_column_refuses_bad_label_and_datatype(self):
        code, _, err = self.run_cli(
            "--add-custom-column", "Bad Label", "X", "text", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("lowercase letters", err)
        code, _, err = self.run_cli(
            "--add-custom-column", "ok", "X", "prose", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("not a supported datatype", err)

    def test_add_column_duplicate_label_fails_at_apply(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO custom_columns (label, name, datatype) "
            "VALUES ('taken', 'Taken', 'text')"
        )
        con.commit()
        con.close()
        code, _, err = self.run_cli(
            "--add-custom-column",
            "taken",
            "Again",
            "text",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

    def test_is_multiple_is_an_add_modifier(self):
        code, _, err = self.run_cli(
            "--column-is-multiple",
            "--saved-search-delete",
            "Recent",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("modifier of --add-custom-column", err)

    def test_remove_column_dry_run_names_the_purge_semantics(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO custom_columns (label, name, datatype) "
            "VALUES ('old', 'Old', 'text')"
        )
        con.commit()
        con.close()
        code, out, _ = self.run_cli(
            "--remove-custom-column", "old", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("mark_for_delete", out)
        self.assertIn("Column today: #old", out)

    def test_remove_column_apply_flags_it(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO custom_columns (label, name, datatype) "
            "VALUES ('old', 'Old', 'text')"
        )
        con.commit()
        con.close()
        code, out, _ = self.run_cli(
            "--remove-custom-column",
            "old",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        con = sqlite3.connect(self.db_path)
        flagged = con.execute(
            "SELECT mark_for_delete FROM custom_columns WHERE label = 'old'"
        ).fetchone()[0]
        con.close()
        self.assertEqual(flagged, 1)

    def test_remove_unknown_column_refused(self):
        code, _, err = self.run_cli(
            "--remove-custom-column", "nope", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("no custom column named", err)

    def test_forbidden_labels_refused_at_both_doors(self):
        for flag, argv in (
            ("--add-custom-column", ["reading_status", "Status", "text"]),
            ("--remove-custom-column", ["date_read"]),
        ):
            code, _, err = self.run_cli(flag, *argv, "--db", self.db_path)
            self.assertEqual(code, 2, flag)
            self.assertIn("NON-NEGOTIABLES", err)

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


class TestSchemaWriteArms(_SchemaWriteCase):
    """The arms the first pass missed: empty-string refusals, the
    batch-verb combination guard (the silent-drop class), a successful
    is_multiple create, and the lock arm."""

    def test_empty_name_or_expression_refused(self):
        for argv in (
            ("--saved-search-add", "", "tags:Fic"),
            ("--saved-search-add", "Fic", "  "),
            ("--saved-search-delete", " "),
            ("--saved-search-rename", "Recent", ""),
        ):
            code, _, err = self.run_cli(*argv, "--db", self.db_path)
            self.assertEqual(code, 2, argv)
            self.assertIn("empty", err)

    def test_batch_verb_company_refused_not_silently_dropped(self):
        # The silent-drop class: a schema verb beside a --batch-* flag used
        # to run the schema write, exit 0, and quietly ignore the batch
        # flag, so the user believed the tag landed.
        code, _, err = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--batch-add-tag",
            "Curated",
            "--ids",
            "1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("refuse company", err)
        self.assertEqual(self.stored(), _SEED_SEARCHES)

    def test_clear_rating_bool_dest_company_refused(self):
        code, _, err = self.run_cli(
            "--saved-search-add",
            "Fic",
            "tags:Fic",
            "--batch-clear-tags",
            "--ids",
            "1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("refuse company", err)

    def test_add_column_with_is_multiple_creates_it(self):
        code, out, _ = self.run_cli(
            "--add-custom-column",
            "audiences",
            "Audiences",
            "text",
            "--column-is-multiple",
            "--apply",
            "--backup-dir",
            self.backups(),
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0, out)
        con = sqlite3.connect(self.db_path)
        row = con.execute(
            "SELECT is_multiple FROM custom_columns WHERE label = 'audiences'"
        ).fetchone()
        con.close()
        self.assertEqual(row, (1,))

    def test_lock_error_maps_to_exit_one(self):
        import sqlite3 as s3

        with mock.patch(
            "cquarry.write.WritableCalibreDB.saved_search_add",
            side_effect=s3.OperationalError("database is locked"),
        ):
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
        self.assertIn("write lock", err)


if __name__ == "__main__":
    unittest.main()
