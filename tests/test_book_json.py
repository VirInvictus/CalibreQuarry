"""Tests for `--book --format json` (Phase 17 box 3): machine-readable
dossier output over cquarry's get_book_dossier, the structured input
phase 3 consumes.

Reuses test_read_modes' rich fixture (annotations, reading positions,
custom columns, comments) so the emitted dossiers are exercised end to
end through cli.main().
"""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

import sqlite3

from test_read_modes import _TempDBCase

from cquarry_cli.cli import main


class TestBookJson(_TempDBCase):
    def _add_book(self, book_id, title, tagged=False):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
                "has_cover,last_modified,series_index,path,uuid) VALUES "
                "(?,?,?,?,'2024-01-01','2024-01-01',0,'2024-01-02 00:00:00',1.0,?,?)",
                (book_id, title, title, "X", f"x/{book_id}", f"uuid-{book_id}"),
            )
            if tagged:
                con.execute(
                    "INSERT INTO books_tags_link (book,tag) VALUES (?,1)", (book_id,)
                )
            con.commit()
        finally:
            con.close()

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--db", self.db_path, *argv])
        return rc, out.getvalue(), err.getvalue()

    def test_single_id_emits_one_dossier_array(self):
        rc, out, _ = self._main("--book", "1", "--format", "json")
        self.assertEqual(rc, 0)
        dossiers = json.loads(out)
        self.assertIsInstance(dossiers, list)
        self.assertEqual(len(dossiers), 1)
        d = dossiers[0]
        self.assertEqual(d["book"]["title"], "Dune")
        self.assertEqual(d["book"]["id"], 1)
        self.assertIn("formats", d)
        self.assertIn("annotations", d)
        self.assertIn("reading_positions", d)
        self.assertIn("comments", d)
        self.assertIn("<b>", d["comments"]["html"])  # raw HTML, not stripped

    def test_batch_ids_emit_array_in_order(self):
        self._add_book(2, "Second Book")
        rc, out, _ = self._main("--book", "2,1", "--format", "json")
        self.assertEqual(rc, 0)
        dossiers = json.loads(out)
        self.assertEqual([d["book"]["id"] for d in dossiers], [2, 1])

    def test_unknown_id_reports_and_exits_one(self):
        rc, out, err = self._main("--book", "1,999", "--format", "json")
        self.assertEqual(rc, 1)
        self.assertIn("no book with id 999", err)
        dossiers = json.loads(out)
        self.assertEqual([d["book"]["id"] for d in dossiers], [1])

    def test_untagged_selector_feeds_json(self):
        self._add_book(2, "Second Book", tagged=False)
        rc, out, _ = self._main("--book", "--untagged", "--format", "json")
        self.assertEqual(rc, 0)
        dossiers = json.loads(out)
        self.assertEqual([d["book"]["id"] for d in dossiers], [2])

    def test_csv_and_ai_are_rejected_for_book(self):
        for fmt in ("csv", "ai"):
            rc, out, err = self._main("--book", "1", "--format", fmt)
            self.assertEqual(rc, 2, fmt)
            self.assertIn("--format json only", err)

    def test_quiet_emits_compact_json(self):
        rc, out, _ = self._main("--book", "1", "--format", "json", "--quiet")
        self.assertEqual(rc, 0)
        dossiers = json.loads(out)
        self.assertEqual(dossiers[0]["book"]["title"], "Dune")
        self.assertNotIn("\n  ", out)  # compact separators, no indent


if __name__ == "__main__":
    unittest.main()
