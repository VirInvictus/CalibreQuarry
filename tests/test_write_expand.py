"""Tests for the v3.21 write-verb expansion (cquarry >=1.5 setters).

add/remove tag, set/clear identifier, set/clear series (+ --series-index),
set/clear publisher, set/clear languages (with canonicalization), add/remove
format, and the --set-cover flag. Everything goes through cli.main()'s
dispatch into writeops, asserting actual stored state against a temp
database built with stdlib only.
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
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT,
    UNIQUE (book, type));
CREATE TABLE comments (book INT, text TEXT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT,
    datatype TEXT, is_multiple BOOL);
CREATE TABLE metadata_dirtied (id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
    UNIQUE(book));
"""


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_wexp_")
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
        os.unlink(self.db_path)

    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([*argv, "--db", self.db_path])
        return rc, out.getvalue(), err.getvalue()

    def _scalar(self, sql, params=()):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql, params).fetchone()
        finally:
            con.close()

    def _rows(self, sql, params=()):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()


class TestTagVerbs(_TempDBCase):
    def test_add_tag_creates_link_then_is_idempotent(self):
        rc, _, _ = self._run(["--add-tag", "1", "Audited"])
        self.assertEqual(rc, 0)
        rc, _, _ = self._run(["--add-tag", "1", "Audited"])
        self.assertEqual(rc, 0)
        rows = self._rows(
            "SELECT t.name FROM books_tags_link l JOIN tags t ON t.id=l.tag "
            "WHERE l.book=1"
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "Audited")

    def test_remove_tag_detaches_and_prunes_orphan(self):
        self._run(["--add-tag", "1", "Solo"])
        rc, _, _ = self._run(["--remove-tag", "1", "Solo"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._rows("SELECT 1 FROM books_tags_link WHERE book=1"), [])
        # The tag row itself is pruned once no book references it.
        self.assertEqual(self._rows("SELECT 1 FROM tags WHERE name='Solo'"), [])
        # Removing an absent tag is a clean no-op.
        rc, _, _ = self._run(["--remove-tag", "1", "Solo"])
        self.assertEqual(rc, 0)


class TestIdentifierVerbs(_TempDBCase):
    def test_upsert_replaces_same_type(self):
        rc, _, _ = self._run(["--set-identifier", "1", "isbn", "978-old"])
        self.assertEqual(rc, 0)
        rc, _, _ = self._run(["--set-identifier", "1", "isbn", "978-new"])
        self.assertEqual(rc, 0)
        rows = self._rows("SELECT val FROM identifiers WHERE book=1 AND type='isbn'")
        self.assertEqual(rows, [("978-new",)])

    def test_empty_value_clears(self):
        self._run(["--set-identifier", "1", "goodreads", "12345"])
        rc, _, _ = self._run(["--set-identifier", "1", "goodreads", ""])
        self.assertEqual(rc, 0)
        self.assertEqual(self._rows("SELECT 1 FROM identifiers WHERE book=1"), [])

    def test_clear_identifier_flag(self):
        self._run(["--set-identifier", "1", "isbn", "x"])
        rc, _, _ = self._run(["--clear-identifier", "1", "isbn"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._rows("SELECT 1 FROM identifiers WHERE book=1"), [])


class TestSeriesPublisherVerbs(_TempDBCase):
    def test_set_series_with_index_then_clear(self):
        rc, _, _ = self._run(
            ["--set-series", "1", "The First Law", "--series-index", "2.5"]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._scalar(
                "SELECT s.name FROM books_series_link l JOIN series s "
                "ON s.id=l.series WHERE l.book=1"
            ),
            ("The First Law",),
        )
        self.assertEqual(
            self._scalar("SELECT series_index FROM books WHERE id=1"), (2.5,)
        )
        # Clearing nulls the link, the index, and prunes the orphaned series.
        rc, _, _ = self._run(["--clear-series", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._rows("SELECT 1 FROM books_series_link WHERE book=1"), [])
        self.assertEqual(
            self._scalar("SELECT series_index FROM books WHERE id=1"), (None,)
        )
        self.assertEqual(self._rows("SELECT 1 FROM series"), [])

    def test_series_index_without_set_series_is_rejected(self):
        rc, _, err = self._run(["--series-index", "3"])
        self.assertEqual(rc, 2)
        self.assertIn("--series-index", err)

    def test_set_and_clear_publisher(self):
        rc, _, _ = self._run(["--set-publisher", "1", "Orbit"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._scalar(
                "SELECT p.name FROM books_publishers_link l JOIN publishers p "
                "ON p.id=l.publisher WHERE l.book=1"
            ),
            ("Orbit",),
        )
        rc, _, _ = self._run(["--clear-publisher", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._rows("SELECT 1 FROM books_publishers_link WHERE book=1"), []
        )
        self.assertEqual(self._rows("SELECT 1 FROM publishers"), [])


class TestLanguageFormatCoverVerbs(_TempDBCase):
    def test_set_languages_canonicalizes_then_clear(self):
        rc, _, _ = self._run(["--set-languages", "1", "English, fra"])
        self.assertEqual(rc, 0)
        codes = [
            r[0]
            for r in self._rows(
                "SELECT g.lang_code FROM books_languages_link l "
                "JOIN languages g ON g.id = l.lang_code "
                "WHERE l.book=1 ORDER BY l.id"
            )
        ]
        self.assertEqual(codes, ["eng", "fra"])
        rc, _, _ = self._run(["--clear-languages", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(
            self._rows("SELECT 1 FROM books_languages_link WHERE book=1"), []
        )

    def test_add_format_duplicate_fails_cleanly_then_remove(self):
        rc, _, _ = self._run(["--add-format", "1", "EPUB", "Old Title", "2048"])
        self.assertEqual(rc, 0)
        row = self._scalar(
            "SELECT format, name, uncompressed_size FROM data WHERE book=1"
        )
        self.assertEqual(row, ("EPUB", "Old Title", 2048))
        # cquarry refuses a duplicate format; surfaces as exit 1.
        rc, _, _ = self._run(["--add-format", "1", "epub", "Other", "1"])
        self.assertEqual(rc, 1)
        # --quiet keeps the success line silent but state still changes.
        rc, _, _ = self._run(["--remove-format", "1", "EPUB", "--quiet"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._rows("SELECT 1 FROM data WHERE book=1"), [])

    def test_add_format_rejects_non_integer_size(self):
        rc, _, err = self._run(["--add-format", "1", "EPUB", "Book", "big"])
        self.assertEqual(rc, 2)
        self.assertIn("SIZE", err)

    def test_set_cover_toggles_flag(self):
        rc, _, _ = self._run(["--set-cover", "1", "no"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._scalar("SELECT has_cover FROM books WHERE id=1"), (0,))
        rc, _, _ = self._run(["--set-cover", "1", "yes"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._scalar("SELECT has_cover FROM books WHERE id=1"), (1,))

    def test_set_cover_rejects_garbage_state(self):
        rc, _, err = self._run(["--set-cover", "1", "maybe"])
        self.assertEqual(rc, 2)
        self.assertIn("yes/no", err)


if __name__ == "__main__":
    unittest.main()
