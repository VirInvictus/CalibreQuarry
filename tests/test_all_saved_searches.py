"""Tests for --all-saved-searches (Phase 19 A.5).

The sweep writes one catalog per saved search into --outdir, each
filtered to its search's ids and headed with the search's provenance.
Zero-hit searches write nothing and say so; unparseable searches are
skipped with a warning; --restrict narrows every catalog.
"""

import io
import json
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
"""

_BOOKS = [
    (1, "Dune", ["Fic.SciFi"]),
    (2, "Neuromancer", ["Fic.SciFi"]),
    (3, "Emma", ["Fic.Classic"]),
]

_SAVED = {
    "SciFi Picks": "tags:Fic.SciFi",
    "Classics": "tags:Fic.Classic",
    "Empty Picks": "tags:NoSuchTagAnywhere",
    "Broken Picks": "((nope",
}


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    tag_ids = {}
    for bid, title, tags in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','Sort {bid}','2024-01-01',"
            f"'2024-01-01',0,'2024-01-02 00:00:00',1.0,'a/b','uuid-{bid}')"
        )
        con.execute(
            f"INSERT INTO authors (id,name,sort) VALUES ({bid},'Author {bid}','S')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        for tag in tags:
            if tag not in tag_ids:
                tag_ids[tag] = len(tag_ids) + 1
                con.execute(
                    "INSERT INTO tags (id,name) VALUES (?,?)",
                    (tag_ids[tag], tag),
                )
            con.execute(
                "INSERT INTO books_tags_link (book,tag) VALUES (?,?)",
                (bid, tag_ids[tag]),
            )
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('saved_searches',?)",
        (json.dumps(_SAVED),),
    )
    con.commit()
    con.close()


class TestAllSavedSearches(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_ss_")
        os.close(fd)
        _build(self.db_path)
        self.addCleanup(os.unlink, self.db_path)
        self.outdir = tempfile.mkdtemp(prefix="cquarry_ss_out_")
        self.addCleanup(self._rm, self.outdir)

    def _rm(self, path):
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def list_outdir(self):
        return sorted(os.listdir(self.outdir))

    def test_one_catalog_per_resolvable_search(self):
        code, out, err = self.run_cli(
            "--all-saved-searches", "--outdir", self.outdir, "--db", self.db_path
        )
        self.assertEqual(code, 0)
        files = self.list_outdir()
        # The broken search is skipped with a warning; the empty one writes
        # nothing and says so.
        self.assertEqual(
            files, ["Classics_SavedSearch.txt", "SciFi_Picks_SavedSearch.txt"]
        )
        self.assertIn("Broken Picks", err)
        self.assertIn("skipped", err)
        self.assertIn("(no matches, no catalog written)", out)
        self.assertIn("2 saved-search catalog(s) written", out)

    def test_catalog_content_and_provenance(self):
        code, _, _ = self.run_cli(
            "--all-saved-searches", "--outdir", self.outdir, "--db", self.db_path
        )
        self.assertEqual(code, 0)
        text = open(os.path.join(self.outdir, "SciFi_Picks_SavedSearch.txt")).read()
        self.assertIn('saved search "SciFi Picks": tags:Fic.SciFi', text)
        self.assertIn("Dune", text)
        self.assertIn("Neuromancer", text)
        self.assertNotIn("Emma", text)
        self.assertIn("Total: 2 books", text)

    def test_restrict_scopes_the_sweep(self):
        code, out, _ = self.run_cli(
            "--all-saved-searches",
            "--outdir",
            self.outdir,
            "--restrict",
            "id:1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        text = open(os.path.join(self.outdir, "SciFi_Picks_SavedSearch.txt")).read()
        self.assertIn("Dune", text)
        self.assertNotIn("Neuromancer", text)

    def test_no_saved_searches(self):
        con = sqlite3.connect(self.db_path)
        con.execute("DELETE FROM preferences WHERE key='saved_searches'")
        con.commit()
        con.close()
        code, out, err = self.run_cli(
            "--all-saved-searches", "--outdir", self.outdir, "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("No saved searches defined", err)
        self.assertEqual(self.list_outdir(), [])


if __name__ == "__main__":
    unittest.main()
