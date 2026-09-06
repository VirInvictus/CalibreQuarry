"""Tests for the batch write summary's honest reporting (cquarry's changed
returns threaded through the action builders).

run_write_batch used to print "ok: <verb>" unconditionally, so a verb that
found the row already in the wanted state was indistinguishable from one
that wrote. The summary now lists per-verb status (applied / already-so)
plus counts, while single-verb runs keep their exact historical output.
"""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cquarry_cli.cli import main

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, author_sort TEXT,
    timestamp TEXT, pubdate TEXT, has_cover INT, last_modified TEXT,
    series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT);
CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
CREATE TABLE comments (book INT, text TEXT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT, datatype TEXT, is_multiple BOOL);
CREATE TABLE metadata_dirtied (id INTEGER PRIMARY KEY, book INTEGER NOT NULL, UNIQUE(book));
"""


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_report_")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        con.executemany(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    1,
                    "Old Title",
                    "Old Title",
                    "Author A",
                    "2020-01-01",
                    "2020-01-01",
                    1,
                    "2020-01-01 00:00:00",
                    1.0,
                    "p1",
                    "u1",
                ),
                (
                    2,
                    "Other",
                    "Other",
                    "Author A",
                    "2020-01-02",
                    "2020-01-02",
                    0,
                    "2020-01-02 00:00:00",
                    1.0,
                    "p2",
                    "u2",
                ),
            ],
        )
        con.commit()
        con.close()

    def tearDown(self):
        os.remove(self.db_path)


class TestBatchSummaryStatuses(_TempDBCase):
    """Several verbs in one invocation report applied vs already-so."""

    def test_mixed_applied_and_already_so(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--add-tag",
                    "1",
                    "Curated",
                    "--add-tag",
                    "1",
                    "Curated",
                    "--remove-tag",
                    "1",
                    "Ghost",
                    "--remove-tag",
                    "1",
                    "Ghost",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("applied: add tag 'Curated' to book 1", text)
        self.assertIn("already-so: add tag 'Curated' to book 1", text)
        self.assertEqual(text.count("already-so: remove tag 'Ghost' from book 1"), 2)
        self.assertIn("Committed as one transaction: 1 applied, 3 already-so", text)
        con = sqlite3.connect(self.db_path)
        try:
            tags = [
                r[0]
                for r in con.execute(
                    "SELECT t.name FROM books_tags_link l JOIN tags t "
                    "ON t.id=l.tag WHERE l.book=1"
                )
            ]
        finally:
            con.close()
        self.assertEqual(tags, ["Curated"])

    def test_all_already_so_still_exits_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--remove-tag",
                    "1",
                    "Ghost",
                    "--remove-tag",
                    "2",
                    "Ghost",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn(
            "Committed as one transaction: 0 applied, 2 already-so",
            out.getvalue(),
        )

    def test_quiet_flag_suppresses_the_summary(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--add-tag",
                    "1",
                    "Curated",
                    "--add-tag",
                    "1",
                    "Curated",
                    "--quiet",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertEqual(out.getvalue(), "")


class TestSingleVerbOutputUnchanged(_TempDBCase):
    """One verb per invocation keeps its historical message shape: no
    status lines, and a repeated single verb still reports the same way."""

    def test_single_set_title_has_no_status_lines(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-title", "1", "New Title", "--db", self.db_path])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Renamed book 1 to 'New Title'", text)
        self.assertNotIn("applied:", text)
        self.assertNotIn("already-so:", text)

    def test_single_repeated_add_tag_reports_already_present(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--add-tag", "1", "Curated", "--db", self.db_path])
        self.assertEqual(rc, 0)
        out2, err2 = io.StringIO(), io.StringIO()
        with redirect_stdout(out2), redirect_stderr(err2):
            rc = main(["--add-tag", "1", "Curated", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("(1 already present)", out2.getvalue())
        self.assertNotIn("already-so:", out2.getvalue())


class TestBatchRollbackUnchanged(_TempDBCase):
    """The all-or-nothing failure contract is untouched by the status
    threading: any failure rolls the whole pass back with the same
    message, whatever the would-be statuses were."""

    def test_failure_after_an_applied_verb_rolls_back(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                [
                    "--add-tag",
                    "1",
                    "Curated",
                    "--set-pubdate",
                    "99",
                    "2001-02-03",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("rolled back", err_cap.getvalue())
        con = sqlite3.connect(self.db_path)
        try:
            tags = con.execute(
                "SELECT COUNT(*) FROM books_tags_link WHERE book=1"
            ).fetchone()[0]
            dirtied = con.execute("SELECT COUNT(*) FROM metadata_dirtied").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(tags, 0)
        self.assertEqual(dirtied, 0)


if __name__ == "__main__":
    unittest.main()
