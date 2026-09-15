"""Tests for the trash surface (3.45.0): the read-only ``--trash``
listing and ``run trash`` (the .caltrash lifecycle over cquarry's
verbs, dry-run by default)."""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from cquarry_cli.cli import main
from cquarry_cli.modes.trash import collect_trash_entries


class _TrashCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cquarry_trash_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.lib = os.path.join(self.tmp, "library")
        os.makedirs(self.lib)
        self.db_path = os.path.join(self.lib, "metadata.db")
        con = sqlite3.connect(self.db_path)
        con.execute("CREATE TABLE books (id INTEGER PRIMARY KEY)")
        con.commit()
        con.close()
        # One old book entry, one fresh format entry: expire's age rule
        # needs both.
        old = os.path.join(self.lib, ".caltrash", "b", "7")
        os.makedirs(old)
        with open(os.path.join(old, "Old Book.epub"), "wb") as f:
            f.write(b"x")
        fresh = os.path.join(self.lib, ".caltrash", "f", "9")
        os.makedirs(fresh)
        with open(os.path.join(fresh, "Book.pdf"), "wb") as f:
            f.write(b"y")
        now = time.time()
        os.utime(old, (now - 40 * 86400, now - 40 * 86400))
        os.utime(fresh, (now - 1 * 86400, now - 1 * 86400))

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestTrashListing(_TrashCase):
    def test_listing_reports_both_entries(self):
        code, out, _ = self.run_cli("--trash", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("[book] book 7", out)
        self.assertIn("[format] book 9", out)
        self.assertIn("40.0 days old", out)
        self.assertIn("Old Book.epub", out)

    def test_listing_writes_nothing(self):
        def stable(entries):
            return [(e["category"], e["book_id"], tuple(e["files"])) for e in entries]

        before = stable(collect_trash_entries(self.lib))
        code, _, _ = self.run_cli("--trash", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertEqual(stable(collect_trash_entries(self.lib)), before)

    def test_a_library_without_trash_reports_none(self):
        shutil.rmtree(os.path.join(self.lib, ".caltrash"))
        code, out, _ = self.run_cli("--trash", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("No trash", out)


class TestRunTrash(_TrashCase):
    def setUp(self):
        super().setUp()
        # The apply half opens metadata.db writable, so the house
        # closed-Calibre guard applies; pin it like every other verb test.
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def test_dry_run_deletes_nothing(self):
        code, out, _ = self.run_cli("run", "trash", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("2 entries", out)
        self.assertTrue(os.path.exists(os.path.join(self.lib, ".caltrash", "b", "7")))
        self.assertTrue(os.path.exists(os.path.join(self.lib, ".caltrash", "f", "9")))

    def test_dry_run_names_what_would_go(self):
        code, out, _ = self.run_cli(
            "run", "trash", "--expire", "14", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("1 would be deleted", out)
        self.assertIn("(would delete)", out)
        self.assertTrue(os.path.exists(os.path.join(self.lib, ".caltrash", "b", "7")))

    def test_empty_apply_deletes_everything(self):
        code, out, _ = self.run_cli(
            "run", "trash", "--empty", "--apply", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("Removed 2 trash entry(ies).", out)
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".caltrash", "b", "7")))
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".caltrash", "f", "9")))
        # Upstream recreates the empty layout.
        self.assertTrue(os.path.isdir(os.path.join(self.lib, ".caltrash", "b")))

    def test_expire_apply_respects_the_age_rule(self):
        code, out, _ = self.run_cli(
            "run", "trash", "--expire", "14", "--apply", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("Removed 1 trash entry(ies).", out)
        self.assertFalse(os.path.exists(os.path.join(self.lib, ".caltrash", "b", "7")))
        self.assertTrue(os.path.exists(os.path.join(self.lib, ".caltrash", "f", "9")))

    def test_empty_and_expire_are_exclusive(self):
        code, _, err = self.run_cli(
            "run", "trash", "--empty", "--expire", "3", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_bad_days_value_is_a_usage_error(self):
        code, _, err = self.run_cli(
            "run", "trash", "--expire", "soon", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("number of days", err)

    def test_json_shape_carries_plan_and_results(self):
        code, out, _ = self.run_cli(
            "run",
            "trash",
            "--empty",
            "--apply",
            "--format",
            "json",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["plan"]["mode"], "empty")
        self.assertEqual(data["plan"]["entries"], 2)
        self.assertEqual(data["results"]["removed"], 2)


if __name__ == "__main__":
    unittest.main()
