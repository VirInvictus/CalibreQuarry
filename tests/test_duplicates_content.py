"""Tests for audit_duplicates_content.py (Phase 19 B.1).

The fixture builds a tiny library of real EPUB files with planted text
relationships: two books sharing identical text under different
metadata (near_duplicate), an omnibus containing a standalone's text
(omnibus_overlap), a unique book (no finding), and a front-matter-only
book that is too short to fingerprint (the false-positive exclusion).
"""

import contextlib
import importlib.util
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dup_content = _load("audit_duplicates_content")

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


def _words(seed: int, count: int = 400) -> str:
    """Deterministic pseudo-prose: enough distinct shingles to fingerprint."""
    vocab = [
        "dune",
        "spice",
        "arrakis",
        "sand",
        "wind",
        "desert",
        "water",
        "sun",
        "night",
        "stars",
        "ship",
        "guild",
        "emperor",
        "house",
        "blade",
        "shield",
        "stone",
        "river",
        "forest",
        "mountain",
        "storm",
        "canyon",
        "echo",
        "lantern",
        "harbor",
    ]
    words = [vocab[(seed * 7 + i * 13) % len(vocab)] for i in range(count)]
    # Vary every third word with its index so nearly every 3-word window
    # is a distinct shingle (repeated vocabulary alone would collapse).
    return " ".join(w if i % 3 else f"{w}{i // 3}" for i, w in enumerate(words))


def _make_epub(path, body_text):
    path.parent.mkdir(parents=True, exist_ok=True)
    chapter = "<html><body><p>" + body_text + "</p></body></html>"
    opf = (
        '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
        '<manifest><item id="c1" href="chapter1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>'
        '<spine><itemref idref="c1"/></spine></package>'
    )
    container = (
        '<?xml version="1.0"?>'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr("content.opf", opf)
        z.writestr("chapter1.xhtml", chapter)


_BOOKS = [
    # (id, title, author_sort, relpath, name, body text)
    (
        1,
        "The Desert World",
        "Auth, A",
        "Auth A/The Desert World (1)",
        "The Desert World",
        _words(1),
    ),
    (
        2,
        "Sand Planet Chronicles",
        "Auth, B",
        "Auth B/Sand Planet Chronicles (2)",
        "Sand Planet Chronicles",
        _words(1),
    ),  # same text, new metadata
    (
        3,
        "The Desert World Omnibus",
        "Auth, A",
        "Auth A/The Desert World Omnibus (3)",
        "The Desert World Omnibus",
        _words(1) + " " + _words(2),
    ),
    (
        4,
        "Something Else Entirely",
        "Auth, C",
        "Auth C/Something Else Entirely (4)",
        "Something Else Entirely",
        _words(3),
    ),
    (
        5,
        "Front Matter Only",
        "Auth, D",
        "Auth D/Front Matter Only (5)",
        "Front Matter Only",
        "a short front matter page",
    ),
]


def _build(tmpdir: Path) -> Path:
    db_path = tmpdir / "metadata.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, author_sort, rel, name, _body in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','{author_sort}','2024-01-01',"
            f"'2024-01-01',0,'2024-01-02 00:00:00',1.0,'{rel}','uuid-{bid}')"
        )
        con.execute(
            f"INSERT INTO authors (id,name,sort) VALUES ({bid},'A','{author_sort}')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        con.execute(
            "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
            f"({bid},'EPUB','{name}',4096)"
        )
        _make_epub(tmpdir / rel / f"{name}.epub", _body)
    con.commit()
    con.close()
    return db_path


class TestDuplicatesContent(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_dupc_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)

    def run_tool(self, *extra):
        argv = ["audit_duplicates_content.py", str(self.tmpdir), *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out):
                code = dup_content.main()
        return code, out.getvalue()

    def test_clusters_and_classes(self):
        code, out = self.run_tool("--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        pairs = {(c["a"], c["b"]): c for c in data["clusters"]}
        self.assertEqual(pairs[(1, 2)]["class"], "near_duplicate")
        self.assertEqual(pairs[(1, 3)]["class"], "omnibus_overlap")
        # The unique book and the front-matter book appear nowhere.
        self.assertNotIn(4, {c["a"] for c in data["clusters"]})
        self.assertNotIn(4, {c["b"] for c in data["clusters"]})
        self.assertNotIn(5, {c["a"] for c in data["clusters"]})
        # The short book was fingerprinted out, not silently counted.
        self.assertEqual(data["scanned"], 4)

    def test_clean_corpus_exits_0(self):
        # Only the unique book and the short one: nothing pairs.
        code, out = self.run_tool("--ids", "4,5")
        self.assertEqual(code, 0)
        self.assertIn("No content duplicates", out)

    def test_text_report_and_quiet(self):
        code, out = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("near_duplicate", out)
        self.assertIn("public-domain reissue", out)
        self.assertIn("omnibus_overlap", out)
        code, out = self.run_tool("--quiet")
        self.assertEqual(code, 1)
        self.assertIn("1,2", out)
        self.assertIn("1,3", out)
        self.assertNotIn("4,", out)

    def test_tool_is_read_only(self):
        before = os.stat(self.db_path).st_mtime_ns
        self.run_tool()
        self.assertEqual(before, os.stat(self.db_path).st_mtime_ns)


def _rm(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
