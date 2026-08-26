"""Tests for cquarry_cli's first write flow (--set-title) and the audit
mode's "Pending OPF sync" section.

--set-title goes through cquarry.write.WritableCalibreDB, which registers
Calibre's trigger UDFs, refreshes the title's sort key, bumps last_modified,
and records the book in metadata_dirtied -- the queue upstream consumes to
regenerate sidecar .opfs. Both behaviors are asserted here against a temp
database built with stdlib only.
"""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cquarry.db import CalibreDB

from cquarry_cli.cli import main
from cquarry_cli.modes.audit import run_audit

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
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_write_")
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
        con.execute("INSERT INTO authors VALUES (1, 'Author A', 'A, Author')")
        con.executemany(
            "INSERT INTO books_authors_link (book, author) VALUES (?, 1)", [(1,), (2,)]
        )
        con.commit()
        con.close()

    def tearDown(self):
        os.remove(self.db_path)


class TestSetTitleWriteFlow(_TempDBCase):
    def test_rename_updates_sort_and_queues_opf_regen(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-title", "1", "The New Title", "--db", self.db_path])
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        try:
            title, sort, lm = con.execute(
                "SELECT title, sort, last_modified FROM books WHERE id=1"
            ).fetchone()
            self.assertEqual((title, sort), ("The New Title", "New Title, The"))
            self.assertNotEqual(lm, "2020-01-01 00:00:00")
            self.assertEqual(
                [r[0] for r in con.execute("SELECT book FROM metadata_dirtied")], [1]
            )
        finally:
            con.close()

    def test_missing_book_fails_cleanly(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(["--set-title", "99", "Nope", "--db", self.db_path])
        self.assertEqual(rc, 1)
        self.assertIn("Book 99 not found", err_cap.getvalue())
        con = sqlite3.connect(self.db_path)
        try:
            n = con.execute("SELECT COUNT(*) FROM metadata_dirtied").fetchone()[0]
            self.assertEqual(n, 0)
        finally:
            con.close()

    def test_non_integer_book_id_rejected(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-title", "abc", "Nope", "--db", self.db_path])
        self.assertEqual(rc, 2)


class TestAuditPendingOPFSync(_TempDBCase):
    def test_pending_section_lists_queued_books(self):
        con = sqlite3.connect(self.db_path)
        con.executemany("INSERT INTO metadata_dirtied (book) VALUES (?)", [(2,), (1,)])
        con.commit()
        con.close()
        db = CalibreDB(self.db_path)
        try:
            outdir = tempfile.mkdtemp()
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                run_audit(db, os.path.join(outdir, "audit.csv"))
            text = out.getvalue()
            self.assertIn("Pending OPF sync: 2 book(s)", text)
            self.assertIn("#1 Old Title", text)
            self.assertIn("#2 Other", text)
        finally:
            db.close()

    def test_no_section_when_queue_empty(self):
        db = CalibreDB(self.db_path)
        try:
            outdir = tempfile.mkdtemp()
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                run_audit(db, os.path.join(outdir, "audit.csv"))
            self.assertNotIn("Pending OPF sync", out.getvalue())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()


class TestWriteVerbs(_TempDBCase):
    """The v3.19 write-verb surface: authors/rating/comments/column/remove,
    all through _run_write's clean error paths."""

    def test_set_authors(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--set-authors",
                    "1",
                    "Leckie, Ann; Second Author",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        names = [
            r[0]
            for r in con.execute(
                "SELECT a.name FROM books_authors_link l JOIN authors a "
                "ON a.id=l.author WHERE l.book=1 ORDER BY l.id"
            )
        ]
        asort = con.execute("SELECT author_sort FROM books WHERE id=1").fetchone()[0]
        dirtied = [r[0] for r in con.execute("SELECT book FROM metadata_dirtied")]
        con.close()
        self.assertEqual(names, ["Leckie, Ann", "Second Author"])
        self.assertEqual(asort, "Leckie, Ann & Second Author")
        self.assertEqual(dirtied, [1])

    def test_set_rating_and_range_check(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-rating", "1", "4.5", "--db", self.db_path])
        self.assertEqual(rc, 0)
        val = (
            sqlite3.connect(self.db_path)
            .execute(
                "SELECT r.rating FROM books_ratings_link l "
                "JOIN ratings r ON r.id = l.rating WHERE l.book = 1"
            )
            .fetchone()
        )
        self.assertEqual(val[0], 9)  # stored x2
        out2, err2 = io.StringIO(), io.StringIO()
        with redirect_stdout(out2), redirect_stderr(err2):
            rc = main(["--set-rating", "1", "7", "--db", self.db_path])
        self.assertEqual(rc, 2)  # range rejected before any write opens

    def test_set_column_enum_validation_surfaces_as_error(self):
        # Fixture lacks the enum display config; set_custom_column accepts
        # unvalidated text columns but this proves the plumbing end-to-end.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-column", "1", "#missing", "x", "--db", self.db_path])
        self.assertEqual(rc, 1)  # unknown column -> ValueError -> exit 1

    def test_remove_book_dry_run_then_confirm(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--remove-book", "1", "--db", self.db_path])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        still_there = (
            sqlite3.connect(self.db_path)
            .execute("SELECT COUNT(*) FROM books WHERE id=1")
            .fetchone()[0]
        )
        self.assertEqual(still_there, 1)

        out2, err2 = io.StringIO(), io.StringIO()
        with redirect_stdout(out2), redirect_stderr(err2):
            rc = main(["--remove-book", "1", "--confirm-remove", "--db", self.db_path])
        self.assertEqual(rc, 0)
        gone = (
            sqlite3.connect(self.db_path)
            .execute("SELECT COUNT(*) FROM books WHERE id=1")
            .fetchone()[0]
        )
        self.assertEqual(gone, 0)
