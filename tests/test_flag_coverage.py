"""Flag-coverage backfill (the wave-2 P3 work order): the catalog
modifiers --show-tags/--show-id/--primary-only/--plugin-data/
--show-author-details and the comments write verbs --set-comments/
--clear-comments had zero tests. Output-shape assertions against the
read-modes temp library; the write verbs through main()'s dispatch like
the other write flows."""

import io
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from test_read_modes import _TempDBCase
from test_write_flow import _SCHEMA as _WRITE_SCHEMA

from cquarry_cli.cli import main


class CatalogFlagTests(_TempDBCase):
    """The five modifier flags over the seeded one-book library (Dune,
    author Herbert, Frank with sort + link, tag Fic.SciFi, rating 8)."""

    def setUp(self):
        super().setUp()
        self.out_path = os.path.join(
            tempfile.mkdtemp(prefix="cquarry_flag_"), "cat.txt"
        )

    def _catalog(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(
                ["--catalog", "--output", self.out_path, "--db", self.db_path, *extra]
            )
        self.assertEqual(code, 0)
        with open(self.out_path, encoding="utf-8") as f:
            return f.read()

    def test_show_tags_replaces_the_stars(self):
        body = self._catalog("--show-tags")
        self.assertIn("[Fic.SciFi]", body)
        self.assertNotIn("★★★★", body)

    def test_show_id_prefixes_the_book_line(self):
        body = self._catalog("--show-id")
        self.assertIn("* [1] Dune", body)

    def test_primary_only_collapses_multi_author_headings(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            "(2,'Dual Author','Dual Author','Herbert, Frank & Side, Second',"
            "'2024-02-01','2024-02-01',0,'2024-02-02 00:00:00',1.0,'x/2','uuid-2')"
        )
        con.execute(
            "INSERT INTO authors (id,name,sort) VALUES (2,'Side, Second','Side, Second')"
        )
        con.executemany(
            "INSERT INTO books_authors_link (book,author) VALUES (?,?)",
            [(2, 1), (2, 2)],
        )
        con.commit()
        con.close()
        full = self._catalog()
        self.assertIn("Herbert, Frank & Side, Second", full)
        collapsed = self._catalog("--primary-only")
        self.assertIn("[Herbert, Frank]", collapsed)
        self.assertNotIn("Side, Second", collapsed)

    def test_plugin_data_appends_the_named_value(self):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "CREATE TABLE books_plugin_data (id INTEGER PRIMARY KEY, book INT, name TEXT, val TEXT)"
        )
        con.execute(
            "INSERT INTO books_plugin_data (book,name,val) VALUES (1,'goodreads_id','12345')"
        )
        con.commit()
        con.close()
        body = self._catalog("--plugin-data", "goodreads_id")
        self.assertIn("<goodreads_id: 12345>", body)

    def test_show_author_details_appends_sort_and_link(self):
        body = self._catalog("--show-author-details")
        self.assertIn("{Herbert, Frank; https://example.com/frank}", body)


class CommentsWriteVerbTests(unittest.TestCase):
    """--set-comments / --clear-comments had zero tests; the real write
    path (WritableCalibreDB, metadata_dirtied queue) asserted here."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_comments_")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(_WRITE_SCHEMA)
        # Calibre's comments table carries an id column (the write path
        # addresses rows by it); the --set-title fixture's plainer shape
        # never noticed.
        con.executescript(
            "DROP TABLE comments;"
            "CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT);"
        )
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            "(1,'Commented','Commented','A, Author','2020-01-01','2020-01-01',0,"
            "'2020-01-01 00:00:00',1.0,'p1','u1')"
        )
        con.execute("INSERT INTO authors VALUES (1, 'Author A', 'A, Author')")
        con.execute("INSERT INTO books_authors_link (book, author) VALUES (1, 1)")
        con.execute("INSERT INTO comments (book, text) VALUES (1, '<p>Old.</p>')")
        con.commit()
        con.close()

    def tearDown(self):
        os.remove(self.db_path)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _comment(self):
        con = sqlite3.connect(self.db_path)
        try:
            row = con.execute("SELECT text FROM comments WHERE book=1").fetchone()
        finally:
            con.close()
        return row[0] if row else None

    def test_set_comments_overwrites_an_existing_row(self):
        code, _, err = self._run(
            "--set-comments", "1", "Fresh description", "--db", self.db_path
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(self._comment(), "Fresh description")
        con = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(
                [r[0] for r in con.execute("SELECT book FROM metadata_dirtied")], [1]
            )
        finally:
            con.close()

    def test_clear_comments_removes_the_row(self):
        self._run("--set-comments", "1", "Temporary", "--db", self.db_path)
        code, _, err = self._run("--clear-comments", "1", "--db", self.db_path)
        self.assertEqual(code, 0, err)
        self.assertIsNone(self._comment())

    def test_missing_book_fails_cleanly(self):
        code, _, err = self._run("--set-comments", "99", "Nope", "--db", self.db_path)
        self.assertEqual(code, 1)
        self.assertIn("Book 99 not found", err)


if __name__ == "__main__":
    unittest.main()
