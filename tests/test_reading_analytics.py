"""Tests for --analytics reading (Phase 19 A.4).

READ-ONLY surface: the tests assert nothing writes (the library
NON-NEGOTIABLES ban writing #reading_status/#date_read; the mode only
reads). The fixture uses Calibre's real storage layouts: the enum
column normalized into a value table + link table, the datetime column
stored directly.
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
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT,
    datatype TEXT, is_multiple BOOL, editable BOOL, display TEXT);
CREATE TABLE custom_column_1 (id INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE books_custom_column_1_link (id INTEGER PRIMARY KEY, book INT,
    value INT);
CREATE TABLE custom_column_2 (book INT, value TEXT);
"""

_BOOKS = [
    # (id, title, added)
    (1, "Royal Assassin", "2026-01-01"),
    (2, "The Blade Itself", "2026-01-01"),
    (3, "Dune", "2026-01-01"),
]


def _build(db_path, with_columns=True):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, added in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','Sort {bid}','{added} 12:00:00',"
            f"'{added}',0,'2024-01-02 00:00:00',1.0,'a/b','uuid-{bid}')"
        )
        con.execute(
            f"INSERT INTO authors (id,name,sort) VALUES ({bid},'Author {bid}','S')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
    if with_columns:
        con.execute(
            "INSERT INTO custom_columns VALUES (1,'reading_status','Status',"
            '\'enumeration\',0,1,\'{"enum_values": ["Wish", "Active", '
            '"Done"]}\')'
        )
        con.execute(
            "INSERT INTO custom_columns VALUES (2,'date_read','Date Read',"
            "'datetime',0,1,'{}')"
        )
        # Enum values live in the normalized value table + link table.
        con.execute("INSERT INTO custom_column_1 VALUES (1,'Done')")
        con.execute("INSERT INTO custom_column_1 VALUES (2,'Active')")
        con.execute("INSERT INTO books_custom_column_1_link (book,value) VALUES (1,1)")
        con.execute("INSERT INTO books_custom_column_1_link (book,value) VALUES (2,2)")
        # Book 3 carries no status. Finish dates: book 1 finished after
        # being added (+120 days); book 2 "before" its added date, which
        # exercises the stale-timestamp note.
        con.execute("INSERT INTO custom_column_2 VALUES (1,'2026-05-01')")
        con.execute("INSERT INTO custom_column_2 VALUES (2,'2025-12-15')")
    con.commit()
    con.close()


class _ReadingCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_read_")
        os.close(fd)
        _build(self.db_path, with_columns=self.with_columns)
        self.addCleanup(os.unlink, self.db_path)

    with_columns = True

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestReadingAnalytics(_ReadingCase):
    def test_status_funnel_follows_enum_order(self):
        code, out, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("Reading status funnel:", out)
        active_pos = out.index("Active")
        done_pos = out.index("Done")
        nostatus_pos = out.index("(no status)")
        # Enum order (Wish is zero-count and skipped), then no-status last.
        self.assertLess(active_pos, done_pos)
        self.assertLess(done_pos, nostatus_pos)
        self.assertRegex(out, r"Done\s+\d\s+33\.3%")
        self.assertRegex(out, r"\(no status\)\s+1\s+33\.3%")

    def test_recent_finishes_newest_first(self):
        code, out, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertIn("Recently finished (2 with #date_read):", out)
        first = out.index("2026-05-01  Royal Assassin")
        second = out.index("2025-12-15  The Blade Itself")
        self.assertLess(first, second)

    def test_days_to_read_includes_stale_note(self):
        code, out, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertIn("Days from added to finished:", out)
        self.assertIn("mean 51.5", out)
        self.assertIn("min -17", out)
        self.assertIn("max 120", out)
        self.assertIn("finished before their added date", out)
        self.assertIn("not the date reading started", out)

    def test_restrict_scopes_every_section(self):
        code, out, _ = self.run_cli(
            "--analytics", "reading", "--restrict", "id:2", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("(1 books)", out)
        self.assertRegex(out, r"Active\s+1\s+100\.0%")
        self.assertIn("(1 with #date_read):", out)
        self.assertNotIn("Royal Assassin", out)

    def test_missing_columns_degrade(self):
        code, out, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertEqual(code, 0)

    def test_read_only_columns(self):
        before = os.stat(self.db_path).st_mtime_ns
        code, _, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertEqual(before, os.stat(self.db_path).st_mtime_ns)


class TestReadingAnalyticsNoColumns(_ReadingCase):
    with_columns = False

    def test_missing_columns_message(self):
        code, out, _ = self.run_cli("--analytics", "reading", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("No #reading_status or #date_read column", out)


if __name__ == "__main__":
    unittest.main()
