"""Tests for the --restrict scoping modifier (Phase 19 A.2).

The RestrictedView scopes the read surface's universe to the ids matching
a search expression; every mode must compose with it. These tests pin the
per-mode semantics: stats/analytics/audit/export/catalog/search compute
over the restricted set only, wing resolution intersects, the SQL-level
aggregations (entities, format stats) are recounted, and the impossible
combinations (write verbs, --book/--id) refuse with exit 2 while a bad
expression exits 1.
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
from cquarry_cli.restrict import RestrictedView

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
"""

_BOOKS = [
    # (id, title, author, author_sort, tags, rating(0-10 or None), ts)
    (1, "Dune", "Frank Herbert", "Herbert, Frank", ["Fic.SciFi"], 8, "2024-01-01"),
    (
        2,
        "Neuromancer",
        "William Gibson",
        "Gibson, William",
        ["Fic.SciFi", "Fic.Cyberpunk"],
        None,
        "2024-02-01",
    ),
    (3, "Emma", "Jane Austen", "Austen, Jane", ["Fic.Classic"], 6, "2024-03-01"),
]


_TAG_IDS = {
    "Fic.SciFi": 1,
    "Fic.Cyberpunk": 2,
    "Fic.Classic": 3,
}


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for name, tid in _TAG_IDS.items():
        con.execute("INSERT INTO tags (id,name) VALUES (?,?)", (tid, name))
    for bid, title, author, author_sort, tags, rating, ts in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','{author_sort}','{ts}','2024-01-01',0,"
            f"'2024-01-02 00:00:00',1.0,'a/b','uuid-{bid}')"
        )
        con.execute(
            "INSERT INTO authors (id,name,sort,link) VALUES "
            f"({bid},'{author}','{author_sort}','https://example.com/{bid}')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        for tag in tags:
            con.execute(
                "INSERT INTO books_tags_link (book,tag) VALUES (?,?)",
                (bid, _TAG_IDS[tag]),
            )
        if rating is not None:
            rid = bid * 10
            con.execute("INSERT INTO ratings (id,rating) VALUES (?,?)", (rid, rating))
            con.execute(
                "INSERT INTO books_ratings_link (book,rating) VALUES (?,?)",
                (bid, rid),
            )
        con.execute(
            "INSERT INTO data (book,format,name,uncompressed_size) "
            f"VALUES ({bid},'EPUB','book{bid}',{bid * 1024})"
        )
    # A virtual library over the SciFi tag, for the vl: composition test.
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('virtual_libraries',?)",
        (json.dumps({"SciFi Wing": "tags:Fic.SciFi"}),),
    )
    # Reading positions on every book; the restricted view must filter them.
    for bid, *_ in _BOOKS:
        con.execute(
            "INSERT INTO last_read_positions (book,format,user,device,cfi,"
            f"epoch,pos_frac) VALUES ({bid},'EPUB','r','Kobo','/body',1,0.5)"
        )
    con.commit()
    con.close()


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_restrict_")
        os.close(fd)
        _build(self.db_path)
        self.addCleanup(os.unlink, self.db_path)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def open_view(self, expr):
        db = CalibreDB(self.db_path)
        self.addCleanup(db.close)
        return RestrictedView(db, set(db.search(expr)))


class TestRestrictView(_TempDBCase):
    def test_view_scopes_book_rows_and_counts(self):
        with self.open_view("tags:Fic.SciFi") as view:
            self.assertEqual({b["id"] for b in view.get_all_books()}, {1, 2})
            self.assertEqual(view.count_books(), 2)
            self.assertEqual(view.all_ids(), {1, 2})

    def test_search_composes_by_intersection(self):
        with self.open_view("tags:Fic.SciFi") as view:
            # tags:Fic.Classic alone hits book 3, outside the universe.
            self.assertEqual(view.search("tags:Fic.Classic"), set())
            self.assertEqual(view.search("tags:Fic.SciFi"), {1, 2})

    def test_resolve_vl_intersects(self):
        with self.open_view("id:1") as view:
            # The wing holds {1, 2} globally; the view's universe is {1}.
            self.assertEqual(view.resolve_vl("SciFi Wing"), {1})

    def test_series_rollup_recomputes_from_scoped_rows(self):
        con = sqlite3.connect(self.db_path)
        con.execute("INSERT INTO series (id,name) VALUES (1,'Realm')")
        con.executemany(
            "INSERT INTO books_series_link (book,series) VALUES (?,1)", [(1,), (3,)]
        )
        con.commit()
        con.close()
        with self.open_view("tags:Fic.SciFi") as view:
            names = [s["name"] for s in view.get_all_series()]
            self.assertEqual(names, ["Realm"])
            # book_count is the restricted count, not the global 2.
            self.assertEqual(view.get_all_series()[0]["book_count"], 1)

    def test_entities_recount_with_secondary_columns(self):
        with self.open_view("tags:Fic.SciFi") as view:
            rows = {r["name"]: r for r in view.get_entities("tags")}
            self.assertEqual(set(rows), {"Fic.SciFi", "Fic.Cyberpunk"})
            self.assertEqual(rows["Fic.SciFi"]["count"], 2)
            self.assertEqual(rows["Fic.Cyberpunk"]["count"], 1)
            authors = {r["name"]: r for r in view.get_entities("authors")}
            # Emma's author is outside the universe entirely.
            self.assertNotIn("Jane Austen", authors)
            self.assertEqual(authors["Frank Herbert"]["link"], "https://example.com/1")

    def test_format_stats_recount(self):
        with self.open_view("id:1") as view:
            stats = view.get_format_stats()
            self.assertEqual(stats["EPUB"]["count"], 1)
            self.assertEqual(stats["EPUB"]["bytes"], 1024)

    def test_last_read_positions_filter(self):
        with self.open_view("id:2") as view:
            rows = view.get_last_read_positions()
            self.assertEqual([r["book"] for r in rows], [2])

    def test_per_book_getters_refuse_outside_universe(self):
        with self.open_view("id:1") as view:
            self.assertIsNone(view.get_book(3))
            self.assertIsNone(view.get_book_dossier(3))
            self.assertIsNotNone(view.get_book(1))


class TestRestrictModes(_TempDBCase):
    def test_stats_scoped(self):
        code, out, _ = self.run_cli(
            "--stats", "--restrict", "tags:Fic.SciFi", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("2 books", out)

    def _tempfile(self, suffix):
        import tempfile as tf

        fd, path = tf.mkstemp(suffix=suffix, prefix="cquarry_restrict_")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        return path

    def test_export_scoped(self):
        out = self._tempfile(".json")
        code, _, _ = self.run_cli(
            "--export",
            "--format",
            "json",
            "--restrict",
            "tags:Fic.Classic",
            "--db",
            self.db_path,
            "--output",
            out,
        )
        self.assertEqual(code, 0)
        with CalibreDB(self.db_path) as db:
            self.assertEqual(db.count_books(), 3)  # the library itself untouched

    def test_export_json_content_scoped(self):
        path = self._tempfile(".json")
        code, _, _ = self.run_cli(
            "--export",
            "--format",
            "json",
            "--restrict",
            "tags:Fic.Classic",
            "--db",
            self.db_path,
            "--output",
            path,
        )
        self.assertEqual(code, 0)
        data = json.load(open(path))
        self.assertEqual([b["id"] for b in data], [3])

    def test_search_intersects(self):
        code, out, _ = self.run_cli(
            "--search",
            "tags:Fic.SciFi",
            "--restrict",
            "id:2",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        self.assertIn("Neuromancer", out)
        self.assertNotIn("Dune", out)

    def test_catalog_scoped_and_wing_composes(self):
        path = self._tempfile(".txt")
        code, _, _ = self.run_cli(
            "--catalog",
            "--restrict",
            "id:2",
            "--wing",
            "SciFi Wing",
            "--db",
            self.db_path,
            "--output",
            path,
        )
        self.assertEqual(code, 0)
        text = open(path).read()
        self.assertIn("Neuromancer", text)
        self.assertNotIn("Dune", text)
        self.assertIn("Total: 1 books", text)

    def test_recent_scoped(self):
        code, out, _ = self.run_cli(
            "--recent", "2", "--restrict", "tags:Fic.Classic", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("Emma", out)
        self.assertNotIn("Dune", out)

    def test_reading_progress_scoped(self):
        code, out, _ = self.run_cli(
            "--reading-progress", "--restrict", "id:3", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("Emma", out)

    def test_format_stats_scoped(self):
        code, out, _ = self.run_cli(
            "--format-stats", "--restrict", "id:1", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("1", out)

    def test_audit_scoped(self):
        out = self._tempfile(".csv")
        code, _, _ = self.run_cli(
            "--audit",
            "--restrict",
            "id:1",
            "--db",
            self.db_path,
            "--output",
            out,
        )
        self.assertEqual(code, 0)

    def test_bad_expression_exits_1(self):
        code, _, err = self.run_cli("--stats", "--restrict", "((", "--db", self.db_path)
        self.assertEqual(code, 1)
        self.assertIn("--restrict", err)

    def test_unknown_vl_in_expression_exits_1(self):
        code, _, err = self.run_cli(
            "--stats", "--restrict", "vl:NoSuchWing", "--db", self.db_path
        )
        self.assertEqual(code, 1)
        self.assertIn("--restrict", err)

    def test_write_verb_refused(self):
        code, _, err = self.run_cli(
            "--add-tag", "1", "x", "--restrict", "id:1", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("read modes only", err)

    def test_set_write_refused(self):
        code, _, err = self.run_cli(
            "--from-untagged",
            "--batch-add-tag",
            "x",
            "--restrict",
            "id:1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("read modes only", err)

    def test_book_refused(self):
        code, _, err = self.run_cli(
            "--book", "1", "--restrict", "id:1", "--db", self.db_path
        )
        self.assertEqual(code, 2)
        self.assertIn("--book", err)

    def test_explicit_annotation_id_refused(self):
        code, _, err = self.run_cli(
            "--export-annotations",
            "--id",
            "1",
            "--restrict",
            "id:1",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 2)
        self.assertIn("--id", err)

    def test_empty_restriction_is_a_valid_universe(self):
        code, out, _ = self.run_cli(
            "--stats", "--restrict", "tags:NoSuchTag", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("0 books", out)


if __name__ == "__main__":
    unittest.main()
