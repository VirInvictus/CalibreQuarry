"""Tests for audit_cover_aspect.py (Phase 19 B.5).

Cover files are hand-crafted PNG headers (signature + IHDR with the
wanted width/height; enough for cquarry's header-only sizer, no image
library needed). The fixture plants a portrait cover (in band), a
narrow spine-scan-shaped cover, a wide square cover, and a book whose
cover file is missing (skipped, another audit's class).
"""

import contextlib
import importlib.util
import io
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


aspect = _load("audit_cover_aspect")

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
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
"""

_PNG_SIG = b"\x89PNG\r\n\x1a\n"


def png_header(width: int, height: int) -> bytes:
    ihdr = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    return _PNG_SIG + struct.pack(">I", 13) + b"IHDR" + ihdr


_BOOKS = [
    # (id, title, relpath, name, cover size or None to omit the file)
    (1, "Portrait Cover", "Auth A/Portrait Cover (1)", "Portrait Cover", (400, 600)),
    (2, "Spine Scan", "Auth A/Spine Scan (2)", "Spine Scan", (120, 600)),
    (3, "Square Art", "Auth B/Square Art (3)", "Square Art", (600, 600)),
    (4, "Missing File", "Auth B/Missing File (4)", "Missing File", None),
]


def _build(tmpdir: Path) -> Path:
    db_path = tmpdir / "metadata.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, rel, name, size in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','S','2024-01-01','2024-01-01',1,"
            f"'2024-01-02 00:00:00',1.0,'{rel}','uuid-{bid}')"
        )
        con.execute(f"INSERT INTO authors (id,name,sort) VALUES ({bid},'A','S')")
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        if size is not None:
            fdir = tmpdir / rel
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / "cover.jpg").write_bytes(png_header(*size))
    con.commit()
    con.close()
    return db_path


class TestCoverAspect(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_aspect_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)

    def run_tool(self, *extra):
        argv = ["audit_cover_aspect.py", str(self.tmpdir), *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out):
                code = aspect.main()
        return code, out.getvalue()

    def test_bands_classify(self):
        code, out = self.run_tool("--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        by_book = {f["book"]: f["class"] for f in data["findings"]}
        # 400x600 = 0.667: inside the bands, not a finding.
        self.assertEqual(by_book, {2: "cover_aspect_narrow", 3: "cover_aspect_wide"})
        # The missing-file book was skipped, not reported.
        self.assertNotIn(4, by_book)
        self.assertEqual(data["skipped"], 1)

    def test_clean_library_exits_0(self):
        code, out = self.run_tool("--ids", "1")
        self.assertEqual(code, 0)
        self.assertIn("ratio bands", out)

    def test_custom_bands(self):
        # A window wide enough to hold the portrait and square covers.
        code, out = self.run_tool("--low", "0.5", "--high", "1.1", "--ids", "1,3")
        self.assertEqual(code, 0)


def _rm(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
