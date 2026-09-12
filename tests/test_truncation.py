"""Tests for audit_truncation.py (Phase 19 B.2).

The fixture hand-writes minimal PDFs (no third-party writer) and plants
books_pages_link rows (the Count Pages plugin's table) with known
truth: one row matching the real count, one disagreeing >20%, one
whose format_size drifted from the file (stale), and one EPUB-format
row that must be ignored (the plugin's EPUB pages are estimates, so
checking them would be a false positive by construction).
"""

import contextlib
import importlib.util
import io
import json
import shutil
import sqlite3
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


truncation = _load("audit_truncation")

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
CREATE TABLE books_pages_link (book INT, pages INT, algorithm INT, format TEXT,
    format_size INT, timestamp TEXT, needs_scan INT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
"""


def minimal_pdf(pages: int) -> bytes:
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{' '.join(f'{3 + i} 0 R' for i in range(pages))}]"
        f" /Count {pages} >>",
    ]
    for _ in range(pages):
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>")
    out = "%PDF-1.4\n"
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(len(out.encode()))
        out += f"{i} 0 obj\n{body}\nendobj\n"
    xref_pos = len(out.encode())
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n"
    out += (
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    )
    return out.encode()


_NOW = "2099-01-01 00:00:00+00:00"  # after any file mtime a test can create
_OLD = "2024-01-01 00:00:00+00:00"  # before the fixture files: stale by mtime

_BOOKS = [
    # (id, title, relpath, name, pdf_pages, claimed_pages, size_delta,
    #  epub_row, row timestamp)
    (1, "Honest Scan", "Auth A/Honest Scan (1)", "Honest Scan", 2, 2, 0, False, _NOW),
    (
        2,
        "Truncated Download",
        "Auth A/Truncated Download (2)",
        "Truncated Download",
        4,
        2,
        0,
        False,
        _OLD,
    ),
    (
        3,
        "Replaced File",
        "Auth B/Replaced File (3)",
        "Replaced File",
        3,
        3,
        5000,
        False,
        _OLD,
    ),
    (
        4,
        "Estimate Only",
        "Auth B/Estimate Only (4)",
        "Estimate Only",
        0,
        999,
        0,
        True,
        _NOW,
    ),
]


def _build(tmpdir: Path) -> Path:
    db_path = tmpdir / "metadata.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, rel, name, pdf_pages, claimed, delta, epub_row, ts in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','S','2024-01-01','2024-01-01',0,"
            f"'2024-01-02 00:00:00',1.0,'{rel}','uuid-{bid}')"
        )
        con.execute(f"INSERT INTO authors (id,name,sort) VALUES ({bid},'A','S')")
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        if pdf_pages:
            body = minimal_pdf(pdf_pages)
            fdir = tmpdir / rel
            fdir.mkdir(parents=True, exist_ok=True)
            (fdir / f"{name}.pdf").write_bytes(body)
            con.execute(
                "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
                f"({bid},'PDF','{name}',{len(body) + delta})"
            )
            con.execute(
                "INSERT INTO books_pages_link (book,pages,algorithm,format,"
                f"format_size,timestamp,needs_scan) VALUES ({bid},{claimed},"
                f"4,'PDF',{len(body) + delta if delta else len(body)},"
                f"'{ts}',0)"
            )
        if epub_row:
            con.execute(
                "INSERT INTO books_pages_link (book,pages,algorithm,format,"
                f"format_size,timestamp,needs_scan) VALUES ({bid},999,"
                f"4,'EPUB',123,'{ts}',0)"
            )
    con.commit()
    con.close()
    return db_path


@unittest.skipUnless(
    shutil.which("pdfinfo"),
    "poppler's pdfinfo is not installed (CI carries no poppler)",
)
class TestTruncation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_trunc_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)

    def run_tool(self, *extra):
        argv = ["audit_truncation.py", str(self.tmpdir), *extra]
        out = io.StringIO()
        with mock.patch.object(sys, "argv", argv):
            with contextlib.redirect_stdout(out):
                code = truncation.main()
        return code, out.getvalue()

    def test_mismatch_and_stale_classes(self):
        code, out = self.run_tool("--format", "json")
        self.assertEqual(code, 1)
        data = json.loads(out)
        by_book: dict[int, set[str]] = {}
        for f in data["findings"]:
            by_book.setdefault(f["book"], set()).add(f["class"])
        # Book 2: claims 2 pages, the file has 4 (>20% off).
        self.assertIn("page_count_mismatch", by_book[2])
        # Book 2's row also predates the file (mtime newer): stale too.
        self.assertIn("stale_plugin_data", by_book[2])
        # Book 3: the file's size drifted from format_size.
        self.assertIn("stale_plugin_data", by_book[3])
        # Book 1 is honest; book 4 is EPUB-only (never checked).
        self.assertNotIn(1, by_book)
        self.assertNotIn(4, by_book)
        self.assertEqual(data["checked"], 3)

    def test_epub_rows_never_checked(self):
        # The estimate-only book's EPUB row would "mismatch" 999 vs real;
        # it is by-design excluded, so 4 findings would only come from 2/3.
        code, out = self.run_tool("--format", "json")
        data = json.loads(out)
        self.assertEqual(sum(1 for f in data["findings"] if f["book"] == 4), 0)

    def test_text_report_and_exit_codes(self):
        code, out = self.run_tool()
        self.assertEqual(code, 1)
        self.assertIn("page_count_mismatch", out)
        self.assertIn("stale_plugin_data", out)
        code, out = self.run_tool("--ids", "1,4")
        self.assertEqual(code, 0)
        self.assertIn("matches the real PDFs", out)


def _rm(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
