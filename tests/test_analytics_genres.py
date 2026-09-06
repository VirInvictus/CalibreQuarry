"""Smoke tests for the v3.27 ``--analytics genres`` mode: the top-level
genre share breakdown rendered over cquarry.analytics' genre_distribution.

Exercised both directly (output assertions against a temp Calibre-shaped
database) and through cli.main() for exit-code plumbing, mirroring
test_read_modes.py.
"""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cquarry.db import CalibreDB

from cquarry_cli.cli import main
from cquarry_cli.modes.analytics import show_genre_breakdown

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
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code INT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
CREATE TABLE comments (book INT, text TEXT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
"""


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.executemany(
        "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
        "has_cover,last_modified,series_index,path,uuid) VALUES "
        "(?,?,?,?,?,'2024-01-01',0,'2024-01-02 00:00:00',1.0,?,?)",
        [
            (1, "Dune", "Dune", "Herbert, Frank", "2024-01-01", "p1", "uuid-1"),
            (
                2,
                "Ficciones",
                "Ficciones",
                "Borges, Jorge Luis",
                "2024-01-01",
                "p2",
                "uuid-2",
            ),
            (3, "OSR Guide", "OSR Guide", "Gygax, Gary", "2024-01-01", "p3", "uuid-3"),
            (4, "Untouched", "Untouched", "Unknown", "2024-01-01", "p4", "uuid-4"),
        ],
    )
    con.executemany(
        "INSERT INTO authors (id,name,sort) VALUES (?,?,?)",
        [
            (1, "Herbert, Frank", "Herbert, Frank"),
            (2, "Borges, Jorge Luis", "Borges, Jorge Luis"),
            (3, "Gygax, Gary", "Gygax, Gary"),
            (4, "Unknown", "Unknown"),
        ],
    )
    con.executemany(
        "INSERT INTO books_authors_link (book,author) VALUES (?,?)",
        [(1, 1), (2, 2), (3, 3), (4, 4)],
    )
    # Book 2 crosses roots (Fic and NonFic at once); book 4 is untagged.
    con.executemany(
        "INSERT INTO tags (id,name) VALUES (?,?)",
        [
            (1, "Fic.SciFi"),
            (2, "Fic.Fantasy"),
            (3, "NonFic.History"),
            (4, "Gaming.TTRPG"),
        ],
    )
    con.executemany(
        "INSERT INTO books_tags_link (book,tag) VALUES (?,?)",
        [(1, 1), (2, 2), (2, 3), (3, 4)],
    )
    con.commit()
    con.close()


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_genres_")
        os.close(fd)
        _build(self.db_path)
        self.db = CalibreDB(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def _capture(self, fn, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = fn(*args, **kwargs)
        return result, out.getvalue(), err.getvalue()


class TestGenreBreakdown(_TempDBCase):
    def test_renders_roots_shares_and_caveat(self):
        # 4 books: Fic 2/4, Gaming and NonFic 1/4 each, one book untagged.
        _, out, _ = self._capture(show_genre_breakdown, self.db)
        self.assertIn("Genre Breakdown (4 books)", out)
        self.assertIn("50.0%", out)
        self.assertIn("25.0%", out)
        # Roots only: the taxonomy's deeper nodes stay in the Tag Tree.
        self.assertNotIn("Fic.SciFi", out)
        self.assertNotIn("NonFic.History", out)
        # Biggest share first, ties by name, untagged last.
        self.assertLess(out.index("Fic"), out.index("Gaming"))
        self.assertLess(out.index("Gaming"), out.index("NonFic"))
        self.assertLess(out.index("NonFic"), out.index("untagged"))
        self.assertIn(
            "Multi-genre books count once per genre, so shares can sum over 100%.",
            out,
        )

    def test_cli_exit_code(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--analytics", "genres", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("Genre Breakdown", out.getvalue())

    def test_cli_rejects_unknown_analytics_choice(self):
        out, err = io.StringIO(), io.StringIO()
        with (
            redirect_stdout(out),
            redirect_stderr(err),
            self.assertRaises(SystemExit) as ctx,
        ):
            main(["--analytics", "nope", "--db", self.db_path])
        self.assertEqual(ctx.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
