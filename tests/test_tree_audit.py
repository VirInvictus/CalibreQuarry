"""Tests for the filesystem-vs-database tree audit (Phase 19 A.3).

The fixture plants a deliberately broken tree beside the temp
metadata.db: a clean book, a sloppy book (missing format file, extra
format, stray file, unclaimed cover), a db book whose directory is
gone, an orphan book dir, a malformed dir, an orphan author dir, and
stray files at the root and under an author dir. Verdicts are read
from the --audit CSV rows (issue_type ``tree``).
"""

import csv
import io
import os
import shutil
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
    # (id, title, path, has_cover)
    (1, "Clean Book", "Author A/Clean Book (1)", 1),
    (2, "Sloppy Book", "Author A/Sloppy Book (2)", 0),
    (3, "Ghost Db Book", "Author B/Ghost Db Book (3)", 1),
]


def _build_db(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, path, has_cover in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','Sort {bid}','2024-01-01',"
            f"'2024-01-01',{has_cover},'2024-01-02 00:00:00',1.0,"
            f"'{path}','uuid-{bid}')"
        )
        con.execute(
            f"INSERT INTO authors (id,name,sort) VALUES ({bid},'Author {bid}','S')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
    con.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
        "(1,'EPUB','Clean Book - A',2048)"
    )
    con.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
        "(2,'EPUB','Sloppy Book - B',2048)"
    )
    con.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
        "(3,'EPUB','Ghost Db Book - C',2048)"
    )
    con.commit()
    con.close()


def _build_tree(libdir):
    def mkdir(rel):
        os.makedirs(os.path.join(libdir, rel), exist_ok=True)

    def touch(rel, content=b"x"):
        with open(os.path.join(libdir, rel), "wb") as f:
            f.write(content)

    # Book 1: clean (format file, cover, metadata.opf all claimed/normal).
    mkdir("Author A/Clean Book (1)")
    touch("Author A/Clean Book (1)/Clean Book - A.epub")
    touch("Author A/Clean Book (1)/cover.jpg")
    touch("Author A/Clean Book (1)/metadata.opf")
    # Book 2: the epub is gone; a stray format, two stray files (one with
    # a book extension, one without), and an unclaimed cover sit there.
    mkdir("Author A/Sloppy Book (2)")
    touch("Author A/Sloppy Book (2)/extra.epub")
    touch("Author A/Sloppy Book (2)/notes.txt")
    touch("Author A/Sloppy Book (2)/thumbs.db")
    touch("Author A/Sloppy Book (2)/cover.jpg")
    # Book 3: claimed by the DB, absent from disk entirely.
    # Orphans and strays.
    mkdir("Author A/Orphan Book (999)")
    mkdir("Author B/Badly Named")
    mkdir("Ghost Author/Some Book (555)")
    touch("Author A/loose.txt")
    touch("stray.dat")


class _TreeAuditCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cquarry_tree_")
        self.addCleanup(self._cleanup_dir, self.tmpdir)
        self.db_path = os.path.join(self.tmpdir, "metadata.db")
        _build_db(self.db_path)
        _build_tree(self.tmpdir)

    def _cleanup_dir(self, path):
        import shutil

        shutil.rmtree(path, ignore_errors=True)

    def run_audit_csv(self, *extra):
        fd, path = tempfile.mkstemp(suffix=".csv", prefix="cquarry_tree_out_")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--audit", "--db", self.db_path, "--output", path, *extra])
        self.assertEqual(code, 0, err.getvalue())
        with open(path) as f:
            rows = [r for r in csv.DictReader(f) if r["issue_type"] == "tree"]
        return rows, out.getvalue()

    def findings(self, rows):
        return {(r["title"], r["issues"]) for r in rows}


class TestTreeAudit(_TreeAuditCase):
    def test_all_planted_findings_reported(self):
        rows, _ = self.run_audit_csv()
        found = self.findings(rows)
        self.assertIn(("Author B/Ghost Db Book (3)", "missing_book_dir"), found)
        self.assertIn(
            ("Author A/Sloppy Book (2)/Sloppy Book - B.epub", "missing_format_file"),
            found,
        )
        self.assertIn(
            ("Author A/Sloppy Book (2)/extra.epub", "extra_format_file"), found
        )
        # .txt is in the (upstream-mirrored) book-extension list, so a
        # notes file counts as an unclaimed FORMAT, not an unknown file.
        self.assertIn(
            ("Author A/Sloppy Book (2)/notes.txt", "extra_format_file"), found
        )
        self.assertIn(("Author A/Sloppy Book (2)/thumbs.db", "extra_book_file"), found)
        self.assertIn(("Author A/Sloppy Book (2)/cover.jpg", "extra_cover_file"), found)
        self.assertIn(("Author A/Orphan Book (999)", "orphan_book_dir"), found)
        self.assertIn(("Author B/Badly Named", "malformed_book_dir"), found)
        self.assertIn(("Ghost Author", "orphan_author_dir"), found)
        self.assertIn(("Author A/loose.txt", "extra_library_file"), found)
        self.assertIn(("stray.dat", "extra_library_file"), found)

    def test_clean_book_and_whitelists_stay_silent(self):
        rows, out = self.run_audit_csv()
        found = self.findings(rows)
        clean_prefix = "Author A/Clean Book (1)/"
        self.assertFalse(
            [f for f in found if f[0].startswith(clean_prefix)],
            "the clean book must produce no tree rows",
        )
        self.assertIn("Filesystem tree: 11 finding(s)", out)
        # The orphan book dir names its id in the prose tail.
        self.assertIn("Orphan Book (999): orphan_book_dir (id 999)", out)

    def test_restrict_scopes_book_classes_keeps_library_shape(self):
        rows, _ = self.run_audit_csv("--restrict", "id:2")
        found = self.findings(rows)
        # Book 2's own problems survive; book 1 and 3 are out of universe.
        self.assertIn(("Author A/Sloppy Book (2)/cover.jpg", "extra_cover_file"), found)
        self.assertNotIn(("Author A/Ghost Db Book (3)", "missing_book_dir"), found)
        # Library-shape classes still report globally.
        self.assertIn(("Author A/Orphan Book (999)", "orphan_book_dir"), found)
        self.assertIn(("Ghost Author", "orphan_author_dir"), found)


if __name__ == "__main__":
    unittest.main()


class TestRootWhitelist(unittest.TestCase):
    """The 2026-09-12 decision: root dot-entries and the workspace
    doc/tool set are never findings -- a library that doubles as a
    working checkout carries them by design."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="cquarry_tree_wl_")
        self.addCleanup(shutil.rmtree, self.tmpdir, ignore_errors=True)
        self.db_path = os.path.join(self.tmpdir, "metadata.db")
        _build_db(self.db_path)
        for name in (
            ".claude",
            ".ruff_cache",
            ".nomedia",
            ".trackerignore",
            "CLAUDE.md",
            "MEMORY.md",
            "roadmap.md",
            "refresh.md",
            "TAXONOMY.md",
            "taxonomy.yaml",
            "validate_library.py",
        ):
            path = os.path.join(self.tmpdir, name)
            if name in (".claude", ".ruff_cache"):
                os.makedirs(path)
            else:
                with open(path, "w") as f:
                    f.write("x")

    def test_furniture_silent_real_strays_still_report(self):
        out = io.StringIO()
        err = io.StringIO()
        fd, path = tempfile.mkstemp(suffix=".csv")
        os.close(fd)
        self.addCleanup(os.unlink, path)
        with redirect_stdout(out), redirect_stderr(err):
            code = main(["--audit", "--db", self.db_path, "--output", path])
        self.assertEqual(code, 0)
        with open(path) as f:
            rows = [r for r in csv.DictReader(f) if r["issue_type"] == "tree"]
        reported = {r["title"] for r in rows}
        for name in (
            ".claude",
            ".ruff_cache",
            ".nomedia",
            ".trackerignore",
            "CLAUDE.md",
            "MEMORY.md",
            "roadmap.md",
            "refresh.md",
            "TAXONOMY.md",
            "taxonomy.yaml",
            "validate_library.py",
        ):
            self.assertNotIn(name, reported)
        # No book directories were built in this test: the three
        # missing_book_dir rows are the only findings left, and every
        # piece of furniture stayed silent.
        self.assertEqual(
            reported,
            {
                "Author A/Clean Book (1)",
                "Author A/Sloppy Book (2)",
                "Author B/Ghost Db Book (3)",
            },
        )
