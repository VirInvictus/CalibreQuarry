"""Tests for --fts content search and the FTS staleness report (Phase 19 A.1).

The fixture synthesizes a full-text-search.db sidecar beside the temp
metadata.db with known coverage: book 1 indexed (stale: queued in
dirtied_formats), book 2 indexed empty plus a PDF extraction error, and
book 3 never indexed. The missing-sidecar case (the real library's
state) is covered by deleting the sidecar.
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
from cquarry_cli.modes.fts import fts_staleness

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
    (1, "Dune", "Herbert, Frank", ["EPUB"]),
    (2, "Nova", "Delany, Samuel R.", ["EPUB", "PDF"]),
    (3, "Emma", "Austen, Jane", ["EPUB"]),
]

_SIDECAR_SCHEMA = """
CREATE TABLE books_text (book INTEGER, format TEXT, format_size INTEGER,
    format_hash TEXT, searchable_text TEXT, text_size INTEGER, text_hash TEXT,
    err_msg TEXT, timestamp TEXT);
CREATE TABLE dirtied_formats (book INTEGER, format TEXT, timestamp TEXT);
"""


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, author_sort, formats in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','{author_sort}','2024-01-01',"
            f"'2024-01-01',0,'2024-01-02 00:00:00',1.0,'a/b','uuid-{bid}')"
        )
        con.execute(
            "INSERT INTO authors (id,name,sort) VALUES "
            f"({bid},'{'A, Author' if bid == 3 else author_sort}','{author_sort}')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        if bid == 1:
            con.execute("INSERT INTO tags (id,name) VALUES (1,'Visible')")
            con.execute("INSERT INTO books_tags_link (book,tag) VALUES (1,1)")
        for n, fmt in enumerate(formats):
            con.execute(
                "INSERT INTO data (book,format,name,uncompressed_size) "
                f"VALUES ({bid},'{fmt}','book{bid}',{1024 * (n + 1)})"
            )
    con.commit()
    con.close()


def _build_sidecar(lib_dir):
    path = os.path.join(lib_dir, "full-text-search.db")
    con = sqlite3.connect(path)
    con.executescript(_SIDECAR_SCHEMA)
    con.execute(
        "INSERT INTO books_text VALUES (1,'EPUB',4096,'h1',"
        "'The spice must flow across the dunes.',39,'t1','','2024-01-03')"
    )
    con.execute(
        "INSERT INTO books_text VALUES (2,'EPUB',2048,'h2','',0,'t2','','2024-01-03')"
    )
    con.execute(
        "INSERT INTO books_text VALUES (2,'PDF',2048,'h3','',0,'t3',"
        "'DRM: encryption found','2024-01-03')"
    )
    con.execute("INSERT INTO dirtied_formats VALUES (1,'EPUB','2024-01-04')")
    con.commit()
    con.close()


class _FtsCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cquarry_fts_")
        self.addCleanup(self._cleanup_dir, self.tmpdir)
        self.db_path = os.path.join(self.tmpdir, "metadata.db")
        _build(self.db_path)
        _build_sidecar(self.tmpdir)

    def _cleanup_dir(self, path):
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def open_db(self):
        db = CalibreDB(self.db_path)
        self.addCleanup(db.close)
        return db


class TestFtsSearch(_FtsCase):
    def test_content_match(self):
        code, out, _ = self.run_cli("--fts", "spice", "--db", self.db_path)
        self.assertEqual(code, 0)
        # One match; the staleness tail below names other books by design,
        # so assert on the match header, not the whole output.
        self.assertIn("FTS Content Search: 'spice' (1 books)", out)
        self.assertIn("#1 Dune", out)

    def test_case_folding(self):
        code, out, _ = self.run_cli("--fts", "SPICE", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("Dune", out)

    def test_no_match_exits_0(self):
        code, out, _ = self.run_cli("--fts", "zzzunfindable", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("0 book(s)", out)

    def test_empty_query_refused(self):
        code, _, err = self.run_cli("--fts", "  ", "--db", self.db_path)
        self.assertEqual(code, 2)
        self.assertIn("non-empty", err)

    def test_json_output(self):
        fd, path = tempfile.mkstemp(suffix=".json", prefix="cquarry_fts_out_")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        code, _, _ = self.run_cli(
            "--fts",
            "spice",
            "--format",
            "json",
            "--output",
            path,
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        data = json.load(open(path))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["id"], 1)
        self.assertEqual(data[0]["formats"], ["EPUB"])

    def test_restrict_composes(self):
        # Book 1 is the only content match; a universe without it finds none.
        code, out, _ = self.run_cli(
            "--fts", "spice", "--restrict", "id:3", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("0 book(s)", out)
        # The staleness tail is scoped too: book 3 alone is never indexed.
        self.assertIn("Never indexed: 1", out)


class TestFtsStaleness(_FtsCase):
    def test_staleness_classes(self):
        with self.open_db() as db:
            report = fts_staleness(db)
        self.assertTrue(report["sidecar_present"])
        self.assertEqual(report["never_indexed"], [(3, "EPUB")])
        self.assertEqual(report["indexed_empty"], [(2, "EPUB")])
        self.assertEqual(
            report["extraction_errors"], {2: {"PDF": "DRM: encryption found"}}
        )
        self.assertEqual(report["stale_queued"], [(1, "EPUB")])

    def test_status_mode_reports_all_classes(self):
        code, out, _ = self.run_cli("--fts-status", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("Never indexed: 1", out)
        self.assertIn("Indexed empty (no extractable text): 1", out)
        self.assertIn("Stale, queued for re-index (dirtied_formats): 1", out)
        self.assertIn("Extraction errors: 1", out)

    def test_missing_sidecar_degrades(self):
        os.unlink(os.path.join(self.tmpdir, "full-text-search.db"))
        code, out, _ = self.run_cli("--fts-status", "--db", self.db_path)
        self.assertEqual(code, 0)
        self.assertIn("sidecar", out)
        self.assertIn("Never indexed: 4", out)  # 1/EPUB, 2/EPUB, 2/PDF, 3/EPUB

    def test_staleness_is_read_only(self):
        before = os.stat(os.path.join(self.tmpdir, "full-text-search.db"))
        code, _, _ = self.run_cli("--fts-status", "--db", self.db_path)
        self.assertEqual(code, 0)
        after = os.stat(os.path.join(self.tmpdir, "full-text-search.db"))
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)


class TestFtsCoverageInAudit(_FtsCase):
    """Phase 19 B.6: the staleness classes render as --audit rows
    (fts_coverage) when the sidecar exists, and stay out of the CSV
    when it does not (the whole library would be one class)."""

    def run_audit_csv(self, *extra):
        import csv as csv_mod
        import tempfile as tf

        fd, path = tf.mkstemp(suffix=".csv", prefix="cquarry_fts_audit_")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--audit", "--db", self.db_path, "--output", path, *extra])
        self.assertEqual(code, 0, err.getvalue())
        with open(path) as f:
            rows = [
                r for r in csv_mod.DictReader(f) if r["issue_type"] == "fts_coverage"
            ]
        return rows, out.getvalue()

    def test_coverage_rows_render_with_sidecar(self):
        rows, out = self.run_audit_csv()
        found = {(r["id"], r["issues"]) for r in rows}
        self.assertIn(("3", "fts_never_indexed [EPUB]"), found)
        self.assertIn(("2", "fts_indexed_empty [EPUB]"), found)
        self.assertIn(("2", "fts_extraction_error [PDF]"), found)
        self.assertIn(("1", "fts_stale_queued [EPUB]"), found)
        self.assertIn("FTS coverage: 4 finding(s)", out)

    def test_no_sidecar_keeps_csv_silent(self):
        os.unlink(os.path.join(self.tmpdir, "full-text-search.db"))
        rows, out = self.run_audit_csv()
        self.assertEqual(rows, [])
        self.assertIn("FTS sidecar absent", out)
        self.assertIn("4 text-capable format(s) never indexed", out)

    def test_restrict_scopes_coverage_rows(self):
        rows, _ = self.run_audit_csv("--restrict", "id:2")
        found = {(r["id"], r["issues"]) for r in rows}
        self.assertIn(("2", "fts_indexed_empty [EPUB]"), found)
        self.assertNotIn(("3", "fts_never_indexed [EPUB]"), found)
        self.assertNotIn(("1", "fts_stale_queued [EPUB]"), found)


if __name__ == "__main__":
    unittest.main()
