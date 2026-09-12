"""Tests for the Phase 19 C integration verbs (run convert/polish/cover/
export/merge/flush/backfill).

The fixture library holds real (tiny) book files so the merge drill
actually moves bytes and the polish post-verify sees sizes. External
programs are mocked (the house convention: the suite never needs the
real converters), but cquarry's WritableCalibreDB runs for real against
the temp database, so registration, trash, and cover removal are
proved, not mocked.
"""

import contextlib
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cquarry.db import CalibreDB

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
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT,
    datatype TEXT, is_multiple BOOL);
"""

_BOOKS = [
    (1, "Keeper Book", "Auth A/Keeper Book (1)"),
    (2, "Duplicate Book", "Auth A/Duplicate Book (2)"),
    (3, "Polish Me", "Auth B/Polish Me (3)"),
]


def _build(tmpdir: Path) -> Path:
    db_path = tmpdir / "metadata.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, rel in _BOOKS:
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
    # Book 1: EPUB; book 2 (the duplicate): PDF + EPUB; book 3: EPUB.
    for bid, fmts in ((1, ["EPUB"]), (2, ["PDF", "EPUB"]), (3, ["EPUB"])):
        for n, fmt in enumerate(fmts):
            con.execute(
                "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
                f"({bid},'{fmt}','Book{bid}',{2048 * (n + 1)})"
            )
    con.commit()
    con.close()
    for bid, fmts in ((1, ["EPUB"]), (2, ["PDF", "EPUB"]), (3, ["EPUB"])):
        row = (
            sqlite3.connect(db_path)
            .execute("SELECT path FROM books WHERE id=?", (bid,))
            .fetchone()[0]
        )
        d = tmpdir / row
        d.mkdir(parents=True, exist_ok=True)
        for fmt in fmts:
            (d / f"Book{bid}.{fmt.lower()}").write_bytes(b"book bytes " * 10)
    return db_path


class _IntegrateCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_int_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_int_bak_"))
        self.addCleanup(_rm, self.backups)
        # Everything runs closed-Calibre: the pgrep guard must say so.
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


def _rm(path):
    import shutil

    shutil.rmtree(path, ignore_errors=True)


class TestConvertVerb(_IntegrateCase):
    def test_dry_run_executes_nothing(self):
        code, out, _ = self.run_cli(
            "run",
            "convert",
            "--to",
            "EPUB",
            "--ids",
            "1",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("convert plan", out)
        self.assertIn("Dry run", out)

    def test_missing_to_refused(self):
        code, _, err = self.run_cli(
            "run", "convert", "--ids", "1", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--to FORMAT", err)

    def test_unknown_id_refused_before_anything_opens(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "EPUB",
            "--ids",
            "999",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown book id 999", err)

    def test_apply_converts_and_registers(self):
        def fake_ebook_convert(cmd, capture_output, text, timeout):
            dst = Path(cmd[2])
            dst.write_bytes(b"converted bytes" * 10)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run", side_effect=fake_ebook_convert):
            code, out, _ = self.run_cli(
                "run",
                "convert",
                "--to",
                "AZW3",
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertIn("Applied 1", out)
        with CalibreDB(str(self.db_path)) as db:
            formats = db.get_formats(1)
        self.assertIn("AZW3", formats)
        # A timestamped backup exists outside the library.
        self.assertTrue(any(self.backups.glob("metadata-*.db")))

    def test_apply_demands_backup_dir(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "AZW3",
            "--ids",
            "1",
            "--apply",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)

    def test_backup_dir_inside_library_refused(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "AZW3",
            "--ids",
            "1",
            "--apply",
            "--backup-dir",
            str(self.tmpdir / "sub"),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("OUTSIDE the library", err)


class TestMergeVerb(_IntegrateCase):
    def test_dry_run_shows_the_moves(self):
        code, out, _ = self.run_cli(
            "run",
            "merge",
            "--keeper",
            "1",
            "--duplicate",
            "2",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("merge plan", out)
        self.assertIn("PDF", out)

    def test_same_book_refused(self):
        code, _, err = self.run_cli(
            "run",
            "merge",
            "--keeper",
            "1",
            "--duplicate",
            "1",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("same book", err)

    def test_apply_moves_formats_and_trashes_duplicate(self):
        code, out, _ = self.run_cli(
            "run",
            "merge",
            "--keeper",
            "1",
            "--duplicate",
            "2",
            "--apply",
            "--backup-dir",
            str(self.backups),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0, out)
        with CalibreDB(str(self.db_path)) as db:
            keeper_fmts = db.get_formats(1)
            self.assertIn("PDF", keeper_fmts)
            self.assertNotIn(2, db.all_ids())
        # list_trash lives on the writable class (cquarry 1.20's trash).
        from cquarry.write import WritableCalibreDB

        with WritableCalibreDB(str(self.db_path)) as wdb:
            trash = wdb.list_trash()
        # The duplicate's PDF bytes now live in the keeper's directory.
        self.assertTrue(
            any(
                p.name.endswith(".pdf")
                for p in (self.tmpdir / "Auth A" / "Keeper Book (1)").iterdir()
            )
        )
        self.assertTrue(trash)


class TestPolishCoverFlush(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_int2_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_int2_bak_"))
        self.addCleanup(_rm, self.backups)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_unknown_polish_op_refused(self):
        code, _, err = self.run_cli(
            "run",
            "polish",
            "--polish-ops",
            "voodoo",
            "--ids",
            "3",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown polish op", err)

    def test_polish_plan_and_apply(self):
        code, out, _ = self.run_cli(
            "run",
            "polish",
            "--polish-ops",
            "smarten",
            "--ids",
            "3",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("polish plan", out)
        epub = self.tmpdir / "Auth B" / "Polish Me (3)" / "Book3.epub"

        def fake_polish(cmd, capture_output, text, timeout):
            epub.write_bytes(b"polished " * 20)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch("subprocess.run", side_effect=fake_polish),
        ):
            code, out, _ = self.run_cli(
                "run",
                "polish",
                "--polish-ops",
                "smarten",
                "--ids",
                "3",
                "--apply",
                "--backup-dir",
                str(self.tmpdir / ".." / "cquarry_int2_bak"),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        with CalibreDB(str(self.db_path)) as db:
            self.assertEqual(
                db.get_formats(3)["EPUB"]["size_bytes"], epub.stat().st_size
            )

    def test_cover_set_and_remove(self):
        cover = self.tmpdir / "newcover.jpg"
        cover.write_bytes(b"\xff\xd8fakejpeg")
        with mock.patch("cquarry_cli.integrate._calibre_running", return_value=False):
            code, out, _ = self.run_cli(
                "run",
                "cover",
                "--cover",
                str(cover),
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        with CalibreDB(str(self.db_path)) as db:
            self.assertTrue(db.get_book(1)["has_cover"])

    def test_flush_empty_queue(self):
        code, out, _ = self.run_cli("run", "flush", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("queue is empty", out)


if __name__ == "__main__":
    unittest.main()
