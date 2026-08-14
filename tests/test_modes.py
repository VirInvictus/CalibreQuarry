"""Regression tests for mode functions' interaction with CalibreDB's caches.

Reuses the Calibre-shaped schema from test_search. The author_sort values are
chosen so SQLite's BINARY ordering ('B' < 'a') differs from the catalog's
case-folded sort; a write_catalog that sorts the shared cache in place would
flip the cached order and fail the assertion.
"""

import csv
import json
import os
import sqlite3
import tempfile
import unittest

from cquarry.db import CalibreDB
from cquarry.modes.audit import run_audit
from cquarry.modes.catalog import write_all_wings, write_catalog
from tests.test_search import _SCHEMA


class TestCatalogCacheIsolation(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_test_")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        con.executemany(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                # BINARY order: 'Banks, A' < 'anders, Z'; folded order reverses.
                (
                    1,
                    "T1",
                    "T1",
                    "anders, Z",
                    "2024-01-01",
                    "2020-01-01",
                    0,
                    "2024-01-01",
                    1.0,
                    "p1",
                    "u1",
                ),
                (
                    2,
                    "T2",
                    "T2",
                    "Banks, A",
                    "2024-01-01",
                    "2020-01-01",
                    0,
                    "2024-01-01",
                    1.0,
                    "p2",
                    "u2",
                ),
            ],
        )
        con.commit()
        con.close()
        self.db = CalibreDB(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_write_catalog_does_not_reorder_books_cache(self):
        before = [b["id"] for b in self.db.get_all_books()]
        self.assertEqual(before, [2, 1])  # SQL ORDER BY author_sort (BINARY)
        out = os.path.join(tempfile.gettempdir(), "cquarry_test_catalog.txt")
        try:
            write_catalog(self.db, out, quiet=True)
        finally:
            if os.path.exists(out):
                os.unlink(out)
        self.assertEqual([b["id"] for b in self.db.get_all_books()], before)

    def test_write_catalog_creates_a_missing_output_directory(self):
        # run_audit and run_export both makedirs first; write_catalog did not,
        # so --output reports/catalog.txt exited 1 on a FileNotFoundError.
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "reports", "nested", "catalog.txt")
            write_catalog(self.db, out, quiet=True)
            self.assertTrue(os.path.exists(out))


class TestWriteAllWingsFilenames(unittest.TestCase):
    """Wing names are sanitized to filenames, and sanitizing is lossy."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_test_")
        os.close(fd)
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            "(1,'T1','T1','A','2024-01-01','2020-01-01',0,'2024-01-01',1.0,'p1','u1')"
        )
        # Two wings whose names differ only in punctuation, plus one that
        # sanitizes away to nothing at all.
        con.execute(
            "INSERT INTO preferences (key, val) VALUES ('virtual_libraries', ?)",
            (
                json.dumps(
                    {
                        "Tabletop: RPG": "title:T1",
                        "Tabletop RPG": "title:T1",
                        "!!!": "title:T1",
                    }
                ),
            ),
        )
        con.commit()
        con.close()
        self.db = CalibreDB(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def test_colliding_wing_names_get_distinct_files(self):
        # "Tabletop: RPG" and "Tabletop RPG" both reduce to Tabletop_RPG, so one
        # wing's catalog used to silently overwrite the other's.
        with tempfile.TemporaryDirectory() as tmp:
            write_all_wings(self.db, tmp, quiet=True)
            files = sorted(os.listdir(tmp))
            self.assertEqual(len(files), 3, files)
            self.assertEqual(len(set(files)), 3, files)
            # a name that sanitizes to nothing still gets a usable filename
            self.assertNotIn("_Library.txt", files)


class TestAuditCoverChecks(unittest.TestCase):
    """run_audit's cover column: absent, present-and-small, present-in-DB-only."""

    def _library(self, tmp, *, has_cover, write_cover):
        db_path = os.path.join(tmp, "metadata.db")
        con = sqlite3.connect(db_path)
        con.executescript(_SCHEMA)
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            "(1,'T1','T1','A','2024-01-01','2020-01-01',?,'2024-01-01',1.0,'A/T1 (1)','u1')",
            (has_cover,),
        )
        con.commit()
        con.close()
        book_dir = os.path.join(tmp, "A", "T1 (1)")
        os.makedirs(book_dir)
        if write_cover:
            # 800x800 PNG header: large enough not to trip the low-res check.
            ihdr = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\r" + b"IHDR"
            ihdr += (800).to_bytes(4, "big") + (800).to_bytes(4, "big")
            with open(os.path.join(book_dir, "cover.jpg"), "wb") as f:
                f.write(ihdr)
        return db_path

    def _issues(self, db_path, tmp):
        db = CalibreDB(db_path)
        out = os.path.join(tmp, "audit.csv")
        try:
            run_audit(db, out, quiet=True)
        finally:
            db.close()
        with open(out, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        return [r["issues"] for r in rows if r["issue_type"] == "book"]

    def test_cover_file_missing_when_the_db_claims_one(self):
        # has_cover=1 with no file on disk used to be reported as clean.
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._library(tmp, has_cover=1, write_cover=False)
            self.assertIn("cover_file_missing", self._issues(db_path, tmp)[0])

    def test_present_cover_is_not_flagged_as_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._library(tmp, has_cover=1, write_cover=True)
            self.assertNotIn("cover_file_missing", self._issues(db_path, tmp)[0])

    def test_no_cover_still_reports_no_cover_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = self._library(tmp, has_cover=0, write_cover=False)
            issues = self._issues(db_path, tmp)[0]
            self.assertIn("no_cover", issues)
            self.assertNotIn("cover_file_missing", issues)


if __name__ == "__main__":
    unittest.main()
