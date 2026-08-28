"""Smoke tests for the v3.21 read modes: --book detail, --entities,
--reading-progress, --columns, and --info.

Each mode is exercised both directly (output assertions against a temp
Calibre-shaped database) and through cli.main() for exit-code plumbing.
Unknown-book ids must fail cleanly with exit 1, never a traceback.
"""

import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from cquarry.db import CalibreDB

from cquarry_cli.cli import main
from cquarry_cli.modes.detail import show_book
from cquarry_cli.modes.display import show_entities, show_reading_progress
from cquarry_cli.modes.info import show_columns, show_info

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
CREATE TABLE comments (book INT, text TEXT);
CREATE TABLE annotations (id INTEGER PRIMARY KEY, book INT, format TEXT,
    user_type TEXT, user TEXT, timestamp TEXT, annot_id TEXT, annot_type TEXT,
    annot_data TEXT);
CREATE TABLE last_read_positions (id INTEGER PRIMARY KEY, book INT, format TEXT,
    user TEXT, device TEXT, cfi TEXT, epoch INT, pos_frac REAL);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT,
    datatype TEXT, is_multiple BOOL);
CREATE TABLE custom_column_1 (id INTEGER PRIMARY KEY, book INT, value TEXT);
"""


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
        "has_cover,last_modified,series_index,path,uuid) VALUES "
        "(1,'Dune','Dune','Herbert, Frank','2024-01-01','2024-01-01',0,"
        "'2024-01-02 00:00:00',1.0,'herbert/dune','uuid-1')"
    )
    con.execute(
        "INSERT INTO authors (id,name,sort,link) VALUES "
        "(1,'Herbert, Frank','Herbert, Frank','https://example.com/frank')"
    )
    con.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
    con.execute("INSERT INTO tags (id,name) VALUES (1,'Fic.SciFi')")
    con.execute("INSERT INTO books_tags_link (book,tag) VALUES (1,1)")
    con.execute("INSERT INTO ratings (id,rating) VALUES (1,8)")
    con.execute("INSERT INTO books_ratings_link (book,rating) VALUES (1,1)")
    con.execute("INSERT INTO publishers (id,name) VALUES (1,'Ace')")
    con.execute("INSERT INTO books_publishers_link (book,publisher) VALUES (1,1)")
    con.execute("INSERT INTO languages (id,lang_code) VALUES (1,'eng')")
    con.execute("INSERT INTO books_languages_link (book,lang_code) VALUES (1,1)")
    con.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) "
        "VALUES (1,'EPUB','Dune',2048)"
    )
    con.execute(
        "INSERT INTO identifiers (book,type,val) VALUES (1,'isbn','9780441172719')"
    )
    con.execute(
        "INSERT INTO comments (book,text) VALUES (1,'<p>A <b>desert</b> planet.</p>')"
    )
    con.execute(
        "INSERT INTO annotations (book,format,user_type,user,timestamp,"
        "annot_id,annot_type,annot_data) VALUES "
        "(1,'EPUB','user','reader','2025-05-01T10:00:00','a1','highlight',"
        '\'{"text": "the spice must flow"}\')'
    )
    con.execute(
        "INSERT INTO last_read_positions (book,format,user,device,cfi,epoch,"
        "pos_frac) VALUES (1,'EPUB','reader','Kobo','/body/12',1735689600,0.42)"
    )
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('grouped_search_terms',?)",
        (json.dumps({"mygroup": ["tags", "series"]}),),
    )
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('user_categories',?)",
        (json.dumps({"Favourites": [["Dune", "books"]]}),),
    )
    con.execute(
        "INSERT INTO custom_columns (id,label,name,datatype,is_multiple) "
        "VALUES (1,'read','Read','bool',0)"
    )
    con.execute("INSERT INTO custom_column_1 (book,value) VALUES (1,'1')")
    con.commit()
    con.close()


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_read_")
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


class TestBookDetail(_TempDBCase):
    def test_full_record_sections(self):
        found, out, _ = self._capture(show_book, self.db, 1)
        self.assertTrue(found)
        for needle in (
            "Dune",
            "Herbert, Frank",
            "isbn: 9780441172719",
            "EPUB",
            "Fic.SciFi",
            "A desert planet.",  # HTML stripped
            "Kobo",
            "42%",
            "Identifiers:",
            "Reading progress:",
            "Read (#read): 1",
        ):
            self.assertIn(needle, out)
        self.assertNotIn("<p>", out)
        self.assertNotIn("<b>", out)

    def test_annotation_summary(self):
        _, out, _ = self._capture(show_book, self.db, 1)
        self.assertIn("Annotations (1):", out)
        self.assertIn("highlight", out)
        self.assertIn("the spice must flow", out)

    def test_unknown_id_reports_and_returns_false(self):
        found, out, err = self._capture(show_book, self.db, 999)
        self.assertFalse(found)
        self.assertIn("no book with id 999", err)

    def test_cli_exit_codes(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--book", "1", "--db", self.db_path])
        self.assertEqual(rc, 0)
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--book", "999", "--db", self.db_path])
        self.assertEqual(rc, 1)


class TestEntitiesAndProgress(_TempDBCase):
    def test_entities_authors_with_sort_and_link(self):
        _, out, _ = self._capture(show_entities, self.db, "authors")
        self.assertIn("Herbert, Frank", out)
        self.assertIn("https://example.com/frank", out)

    def test_entities_ratings_render_stars(self):
        _, out, _ = self._capture(show_entities, self.db, "ratings")
        self.assertIn("4.0", out)

    def test_entities_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            self._capture(show_entities, self.db, "goblins")

    def test_reading_progress(self):
        _, out, _ = self._capture(show_reading_progress, self.db)
        self.assertIn("Dune", out)
        self.assertIn("Kobo", out)
        self.assertIn("42.0%", out)


class TestInfoAndColumns(_TempDBCase):
    def test_info_sections(self):
        _, out, _ = self._capture(show_info, self.db)
        for needle in (
            "Identity:",
            "Saved searches (0):",
            "User categories (1):",
            "@Favourites",
            "Grouped search terms (1):",
            "mygroup: tags, series",
            "Sync queues:",
        ):
            self.assertIn(needle, out)

    def test_columns_lists_schema(self):
        _, out, _ = self._capture(show_columns, self.db)
        self.assertIn("#read", out)
        self.assertIn("bool", out)
        self.assertIn("editable", out)

    def test_cli_info_and_columns_exit_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--info", "--db", self.db_path])
        self.assertEqual(rc, 0)
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--columns", "--db", self.db_path])
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
