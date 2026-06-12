"""Regression tests for mode functions' interaction with CalibreDB's caches.

Reuses the Calibre-shaped schema from test_search. The author_sort values are
chosen so SQLite's BINARY ordering ('B' < 'a') differs from the catalog's
case-folded sort; a write_catalog that sorts the shared cache in place would
flip the cached order and fail the assertion.
"""

import os
import sqlite3
import tempfile
import unittest

from cquarry.db import CalibreDB
from cquarry.modes.catalog import write_catalog

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


if __name__ == "__main__":
    unittest.main()
