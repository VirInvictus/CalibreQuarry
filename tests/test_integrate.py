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
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cquarry.db import CalibreDB

from cquarry_cli import integrate
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
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT,
    val TEXT, UNIQUE(book, type));
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

    def test_apply_to_an_existing_target_skips_at_plan_time(self):
        # 3.41.0: the old cut planned the conversion anyway, so --apply
        # OVERWROTE the EPUB the book already has and then died on the
        # add_format registration. The plan skips the book, apply never
        # invokes ebook-convert, and the file is untouched.
        epub = self.tmpdir / "Auth A" / "Keeper Book (1)" / "Book1.epub"
        before = epub.read_bytes()
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
        self.assertIn("already has EPUB", out)

        calls = []

        def fake_ebook_convert(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch("subprocess.run", side_effect=fake_ebook_convert),
        ):
            code, out, _ = self.run_cli(
                "run",
                "convert",
                "--to",
                "EPUB",
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertEqual(calls, [], "ebook-convert must not run for a skip")
        self.assertIn("Applied 0", out)
        self.assertEqual(epub.read_bytes(), before)

    def test_registration_failure_is_a_report_row_not_a_traceback(self):
        def fake_ebook_convert(cmd, **kw):
            Path(cmd[2]).write_bytes(b"converted bytes" * 10)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch("subprocess.run", side_effect=fake_ebook_convert),
            mock.patch(
                "cquarry_cli.integrate.WritableCalibreDB",
                side_effect=RuntimeError("database is locked"),
            ),
        ):
            code, out, err = self.run_cli(
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
        self.assertEqual(code, 1)
        self.assertIn("registration failed", out)
        self.assertNotIn("Traceback", err)


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


class TestCalibredbSeams(unittest.TestCase):
    """3.39.1 regression: --library takes the library DIRECTORY (not the
    .db path) and export's skip-OPF flag is --dont-write-opf. Both were
    caught by the live facility drills, not by mocked tests."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_int3_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_flush_passes_directory_and_id_list(self):
        import sqlite3 as s3

        con = s3.connect(self.db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS metadata_dirtied (book INT, "
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, format TEXT, "
            "timestamp TIMESTAMP)"
        )
        con.execute("INSERT INTO metadata_dirtied (book) VALUES (1)")
        con.commit()
        con.close()
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "flush",
                "--apply",
                "--backup-dir",
                str(self.tmpdir / ".." / "bak"),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertIn("--library", calls[-1])
        self.assertEqual(
            Path(calls[-1][calls[-1].index("--library") + 1]),
            self.tmpdir,
        )
        self.assertIn("1", calls[-1])

    def test_export_flag_is_dont_write_opf(self):
        def fake_run(cmd, **kw):
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run) as mp,
        ):
            code, out, _ = self.run_cli(
                "run",
                "export",
                "--dest",
                str(self.tmpdir / "out"),
                "--ids",
                "1",
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        cmd = mp.call_args[0][0]
        self.assertIn("--dont-write-opf", cmd)
        self.assertNotIn("--dont-save-opf", cmd)
        self.assertEqual(Path(cmd[cmd.index("--library") + 1]), self.tmpdir)


class TestMatrixCFixes(unittest.TestCase):
    """3.39.2 regression set from the functional-pass matrix: the
    backfill apply path, the exactly-one target rule, clean parse
    errors, and the report flags in subcommand position."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_mc_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_mc_bak_"))
        self.addCleanup(_rm, self.backups)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_search_and_ids_are_exclusive(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "EPUB",
            "--search",
            "formats:EPUB",
            "--ids",
            "1",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("exclusive", err)

    def test_bad_search_expression_is_a_clean_error(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "EPUB",
            "--search",
            "(((",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("could not parse", err)
        self.assertNotIn("Traceback", err)

    def test_comments_field_refused(self):
        code, _, err = self.run_cli(
            "run",
            "backfill",
            "--fields",
            "comments",
            "--ids",
            "1",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown field", err)

    def test_backfill_apply_requires_backup_dir(self):
        with mock.patch("cquarry_cli.integrate._calibre_running", return_value=False):
            code, _, err = self.run_cli(
                "run",
                "backfill",
                "--fields",
                "title",
                "--ids",
                "1",
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)

    def test_backfill_apply_end_to_end(self):
        # A fake metadata source that actually writes the OPF its --opf
        # argument names (the 3.39.0 cut orphaned that argument and every
        # book failed).
        opf_text = (
            '<?xml version="1.0"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/">'
            "<metadata><dc:title>Backfilled Title</dc:title>"
            "<dc:publisher>Real Press</dc:publisher></metadata></package>"
        )

        def fake_fetch(cmd, capture_output, text, timeout):
            # The tool prints the OPF to stdout under `-o` (the 3.39.2
            # cut passed --opf <file>, which fetch-ebook-metadata does
            # not even know); the verb stages stdout to a temp file.
            assert "-o" in cmd
            return mock.Mock(returncode=0, stdout=opf_text, stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/fetch"
            ),
            mock.patch("subprocess.run", side_effect=fake_fetch),
        ):
            code, out, _ = self.run_cli(
                "run",
                "backfill",
                "--fields",
                "title,publisher",
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
        # The fetched OPF's --opf argument carried the temp path, and the
        # title actually landed through cquarry's write module.
        with CalibreDB(str(self.db_path)) as db:
            self.assertEqual(db.get_book(1)["title"], "Backfilled Title")

    def test_plan_lines_carry_no_trailing_space(self):
        # The Matrix 3 cosmetic: detail-less plan lines (backfill, polish,
        # cover) ended with a trailing space after the action.
        import contextlib as cl

        buf = io.StringIO()
        with cl.redirect_stdout(buf), cl.redirect_stderr(io.StringIO()):
            rc = main(
                [
                    "run",
                    "backfill",
                    "--fields",
                    "title",
                    "--ids",
                    "1",
                    "--db",
                    str(self.db_path),
                ]
            )
        self.assertEqual(rc, 0)
        for line in buf.getvalue().splitlines():
            self.assertFalse(line != line.rstrip(), repr(line))

    def test_format_json_after_run(self):
        code, out, _ = self.run_cli(
            "run",
            "convert",
            "--to",
            "AZW3",
            "--ids",
            "1",
            "--format",
            "json",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["plan"][0]["action"], "convert")


class TestCalibreGuard(unittest.TestCase):
    """3.41.0: the pgrep guard is fail-closed. A timeout (or a pgrep
    that cannot run) used to answer "not running" and --apply proceeded
    against a live Calibre; run.py's guard has always assumed-running."""

    def test_timeout_assumes_running(self):
        import subprocess as sp

        with mock.patch(
            "subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="pgrep", timeout=10),
        ):
            self.assertTrue(integrate._calibre_running())

    def test_pgrep_unavailable_assumes_running(self):
        with mock.patch("subprocess.run", side_effect=OSError("no pgrep")):
            self.assertTrue(integrate._calibre_running())

    def test_a_clean_answer_passes_through(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)):
            self.assertFalse(integrate._calibre_running())
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)):
            self.assertTrue(integrate._calibre_running())


class TestDispatchDiscipline(_IntegrateCase):
    """3.43.0: usage guards run BEFORE the library resolves (dispatch_run's
    rule), and the Calibre-running refusal is lock-class exit 1 everywhere
    (the five run/integrate doors used to exit 2 while setwrite exited 1)."""

    def test_missing_target_is_usage_even_when_the_library_is_missing(self):
        code, _, err = self.run_cli(
            "run",
            "convert",
            "--to",
            "EPUB",
            "--db",
            str(self.tmpdir / "nope" / "metadata.db"),
        )
        self.assertEqual(code, 2)
        self.assertIn("--search EXPR or --ids", err)

    def test_calibre_running_refusal_is_exit_one(self):
        with mock.patch("cquarry_cli.integrate._calibre_running", return_value=True):
            code, _, err = self.run_cli(
                "run",
                "convert",
                "--to",
                "EPUB",
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 1)
        self.assertIn("Calibre is running", err)


class TestFlushIdTargets(unittest.TestCase):
    """3.41.0 regression: embed_metadata gets SPACE-SEPARATED ids. The
    old cut joined a chunk's distinct ids into one hyphen range
    ("5-900"), and calibredb reads a range as EVERY book between the
    endpoints, so a non-contiguous dirtied set wrote embedded metadata
    into books the queue never named."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_flush_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_flush_bak_"))
        self.addCleanup(_rm, self.backups)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _dirty(self, *ids):
        import sqlite3 as s3

        con = s3.connect(self.db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS metadata_dirtied (book INT, "
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, format TEXT, "
            "timestamp TIMESTAMP)"
        )
        for bid in ids:
            con.execute("INSERT INTO metadata_dirtied (book) VALUES (?)", (bid,))
        con.commit()
        con.close()

    def test_noncontiguous_queue_never_becomes_a_range(self):
        self._dirty(1, 3)  # book 2 sits between and must NOT be touched
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "flush",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertIn("Flushed 2 book(s)", out)
        cmd = calls[-1]
        tail = cmd[cmd.index("embed_metadata") + 1 :]
        ids = [t for t in tail if t not in ("--library",) and not t.startswith("/")]
        # Every dirtied id named individually; no "1-3" range that would
        # silently cover the untouched book 2.
        self.assertIn("1", ids)
        self.assertIn("3", ids)
        self.assertNotIn("1-3", ids)
        self.assertEqual(len(ids), 2, cmd)

    def test_contiguous_queue_stays_individual_ids(self):
        self._dirty(1, 2, 3)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, _, _ = self.run_cli(
                "run",
                "flush",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        cmd = calls[-1]
        self.assertIn("1", cmd)
        self.assertIn("2", cmd)
        self.assertIn("3", cmd)
        self.assertNotIn("1-3", cmd)

    def test_bad_ids_list_is_a_usage_error_not_a_traceback(self):
        code, _, err = self.run_cli(
            "run", "flush", "--ids", "1,notanid", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid book id", err)
        self.assertNotIn("Traceback", err)

    def test_bad_search_expression_is_a_usage_error(self):
        code, _, err = self.run_cli(
            "run", "flush", "--search", "((", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("could not parse", err)
        self.assertNotIn("Traceback", err)

    def test_empty_intersection_is_named_apart_from_an_empty_queue(self):
        # Flush honesty: with a target set and a non-empty queue holding none
        # of the targets, the old cut printed "The OPF queue is empty", a lie
        # about the queue's state.
        self._dirty(1)
        code, out, _ = self.run_cli(
            "run", "flush", "--ids", "2", "--db", str(self.db_path)
        )
        self.assertEqual(code, 0)
        self.assertIn("No targeted book is in the OPF queue", out)

    def test_empty_queue_still_says_so(self):
        code, out, _ = self.run_cli("run", "flush", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("The OPF queue is empty", out)

    def test_apply_reports_queue_remaining(self):
        # The test fixture's fake embed never drains metadata_dirtied, so the
        # post-apply re-read must report what is still queued instead of
        # silence.
        self._dirty(1)
        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ),
        ):
            code, out, _ = self.run_cli(
                "run",
                "flush",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertIn("Flushed 1 book(s)", out)
        self.assertIn("still queued", out)

    def test_apply_json_carries_queue_remaining(self):
        self._dirty(1)
        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch(
                "subprocess.run",
                return_value=mock.Mock(returncode=0, stdout="", stderr=""),
            ),
        ):
            code, out, _ = self.run_cli(
                "run",
                "flush",
                "--apply",
                "--format",
                "json",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        payload = json.loads(out)
        self.assertEqual(payload["results"]["flushed"], 1)
        self.assertEqual(payload["results"]["queue_remaining"], 1)


class TestBackfillHardening(unittest.TestCase):
    """3.41.0: a backfill book that cannot apply is a COUNTED failure
    (the old cut reported Applied 0, failed 0 and exited 0), a malformed
    OPF is a report row rather than a traceback, and --fields isbn picks
    the scheme-tagged identifier instead of the first dc:identifier."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_bf_"))
        self.addCleanup(_rm, self.tmpdir)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_bf_bak_"))
        self.addCleanup(_rm, self.backups)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _apply(self, stdout):
        def fake_fetch(cmd, **kw):
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        with (
            mock.patch("cquarry_cli.integrate._calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/fetch"
            ),
            mock.patch("subprocess.run", side_effect=fake_fetch),
        ):
            return self.run_cli(
                "run",
                "backfill",
                "--fields",
                "isbn",
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )

    def _identifiers(self):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(
                "SELECT type, val FROM identifiers WHERE book=1"
            ).fetchall()
        finally:
            con.close()

    def test_malformed_opf_is_a_counted_failure(self):
        code, out, err = self._apply("this is not xml at all")
        self.assertEqual(code, 1)
        self.assertIn("Applied 0, failed/skipped 1", out)
        self.assertIn("OPF unreadable", out)
        self.assertNotIn("Traceback", err)

    def test_refused_write_is_a_counted_failure(self):
        with mock.patch(
            "cquarry_cli.integrate.WritableCalibreDB",
            side_effect=RuntimeError("database is locked"),
        ):
            code, out, err = self._apply(
                "<?xml version='1.0'?><package "
                "xmlns='http://www.idpf.org/2007/opf' "
                "xmlns:opf='http://www.idpf.org/2007/opf' "
                "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                "<metadata><dc:identifier opf:scheme='ISBN'>"
                "9780306406157</dc:identifier></metadata></package>"
            )
        self.assertEqual(code, 1)
        self.assertIn("Applied 0, failed/skipped 1", out)
        self.assertIn("database is locked", out)
        self.assertNotIn("Traceback", err)

    def test_isbn_prefers_the_scheme_tagged_identifier(self):
        code, out, _ = self._apply(
            "<?xml version='1.0'?><package "
            "xmlns='http://www.idpf.org/2007/opf' "
            "xmlns:opf='http://www.idpf.org/2007/opf' "
            "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<metadata>"
            "<dc:identifier>GR-999</dc:identifier>"
            "<dc:identifier opf:scheme='ISBN'>978-0-306-40615-7</dc:identifier>"
            "</metadata></package>"
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self._identifiers(), [("isbn", "9780306406157")])

    def test_isbn_falls_back_to_an_isbn_shape(self):
        code, out, _ = self._apply(
            "<?xml version='1.0'?><package "
            "xmlns='http://www.idpf.org/2007/opf' "
            "xmlns:opf='http://www.idpf.org/2007/opf' "
            "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<metadata>"
            "<dc:identifier>GR-999</dc:identifier>"
            "<dc:identifier>0061020052</dc:identifier>"
            "</metadata></package>"
        )
        self.assertEqual(code, 0, out)
        # The 10-digit shape normalized through cquarry's to_isbn13,
        # which recomputes the 13-digit check digit (it does not carry
        # the ISBN-10 one over).
        self.assertEqual(self._identifiers(), [("isbn", "9780061020056")])

    def test_no_isbn_shape_writes_nothing(self):
        code, out, _ = self._apply(
            "<?xml version='1.0'?><package "
            "xmlns='http://www.idpf.org/2007/opf' "
            "xmlns:opf='http://www.idpf.org/2007/opf' "
            "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<metadata><dc:identifier>GR-999</dc:identifier>"
            "</metadata></package>"
        )
        self.assertEqual(code, 0, out)
        self.assertEqual(self._identifiers(), [])


class TestBackupMetadata(_IntegrateCase):
    """`run backup-metadata`: calibredb backup_metadata over the dirtied
    queue (the headless form of the daemon sidecar job). Pairs with run
    flush: flush embeds into the format files, this refreshes the sidecars.
    The queue read is cquarry's (get_dirtied_books); the missing-binary
    seam refusal and the backup requirement follow the flush precedent."""

    def _dirty(self, *ids):
        con = sqlite3.connect(self.db_path)
        con.execute(
            "CREATE TABLE IF NOT EXISTS metadata_dirtied (book INT, "
            "seq INTEGER PRIMARY KEY AUTOINCREMENT, format TEXT, "
            "timestamp TIMESTAMP)"
        )
        for bid in ids:
            con.execute("INSERT INTO metadata_dirtied (book) VALUES (?)", (bid,))
        con.commit()
        con.close()

    def test_empty_queue_is_an_honest_no_op(self):
        code, out, _ = self.run_cli("run", "backup-metadata", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("The OPF queue is empty", out)

    def test_dry_run_lists_the_queue_and_runs_nothing(self):
        self._dirty(1, 3)
        code, out, _ = self.run_cli("run", "backup-metadata", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("backup-metadata plan (2)", out)
        self.assertIn("Dry run", out)

    def test_all_widens_to_every_book(self):
        code, out, _ = self.run_cli(
            "run", "backup-metadata", "--all", "--db", str(self.db_path)
        )
        self.assertEqual(code, 0)
        self.assertIn("backup-metadata plan (3)", out)

    def test_apply_demands_backup_dir(self):
        self._dirty(1)
        code, _, err = self.run_cli(
            "run", "backup-metadata", "--apply", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)

    def test_missing_calibredb_is_a_setup_refusal(self):
        self._dirty(1)
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            code, _, err = self.run_cli(
                "run",
                "backup-metadata",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 2)
        self.assertIn("calibredb is not on PATH", err)

    def test_apply_drives_calibredb_with_the_library_directory(self):
        self._dirty(1, 2)
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "backup-metadata",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        self.assertIn("Regenerated sidecar OPFs for 2 book(s)", out)
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["calibredb", "backup_metadata"])
        self.assertIn("--library", cmd)
        # The --all widening rides its own flag, never a book list.
        self.assertNotIn("--all", cmd)


class TestRestoreDatabase(_IntegrateCase):
    """`run restore-database` creates a database, so destination confusion
    is refused at the plan door: explicit --target, an existing
    metadata.db only over --force, and at least one stored OPF."""

    def _target(self, *, opfs=2, db=False):
        d = self.tmpdir / "restore_target"
        d.mkdir(exist_ok=True)
        for i in range(opfs):
            book = d / f"Author - Book {i} ({i})"
            book.mkdir(exist_ok=True)
            (book / "metadata.opf").write_text("<opf/>")
        if db:
            (d / "metadata.db").write_bytes(b"corrupt")
        return d

    def test_missing_target_is_a_usage_error(self):
        code, _, err = self.run_cli(
            "run", "restore-database", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--target DIR", err)

    def test_nonexistent_target_refused(self):
        code, _, err = self.run_cli(
            "run",
            "restore-database",
            "--target",
            str(self.tmpdir / "nope"),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_existing_db_refused_without_force(self):
        target = self._target(db=True)
        code, _, err = self.run_cli(
            "run",
            "restore-database",
            "--target",
            str(target),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("--force", err)

    def test_no_opfs_refused(self):
        target = self._target(opfs=0)
        code, _, err = self.run_cli(
            "run",
            "restore-database",
            "--target",
            str(target),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("nothing to restore from", err)

    def test_dry_run_describes_and_executes_nothing(self):
        target = self._target()
        code, out, _ = self.run_cli(
            "run",
            "restore-database",
            "--target",
            str(target),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("2 OPF-bearing book folder(s)", out)
        self.assertIn("Dry run", out)
        self.assertIn("loses", out)

    def test_apply_drives_calibredb_against_the_target(self):
        target = self._target(db=True)
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(
                returncode=0, stdout="Restoring database succeeded", stderr=""
            )

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "restore-database",
                "--target",
                str(target),
                "--force",
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["calibredb", "restore_database"])
        self.assertIn("--really-do-it", cmd)
        self.assertEqual(cmd[cmd.index("--library") + 1], str(target))

    def test_missing_calibredb_is_a_setup_refusal(self):
        target = self._target()
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            code, _, err = self.run_cli(
                "run",
                "restore-database",
                "--target",
                str(target),
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 2)
        self.assertIn("calibredb is not on PATH", err)


class TestClone(_IntegrateCase):
    """`run clone`: calibredb clone, the schema-fresh half of a library copy.
    The clone carries the columns and settings but NO books, so the target
    guards and the dry-run honesty are the point."""

    def _target(self, *, files=0):
        # Outside the fixture library: a nested empty target is refused by
        # the very guard this class pins.
        d = Path(tempfile.mkdtemp(prefix="cquarry_clone_tgt_")) / "clone_target"
        self.addCleanup(_rm, d.parent)
        d.mkdir(exist_ok=True)
        for i in range(files):
            (d / f"junk{i}.txt").write_text("x")
        return d

    def test_missing_target_is_a_usage_error(self):
        code, _, err = self.run_cli("run", "clone", "--db", str(self.db_path))
        self.assertEqual(code, 2)
        self.assertIn("--target DIR", err)

    def test_target_may_not_be_the_library(self):
        code, _, err = self.run_cli(
            "run",
            "clone",
            "--target",
            str(self.tmpdir),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("current library", err)

    def test_nonempty_target_refused(self):
        target = self._target(files=2)
        code, _, err = self.run_cli(
            "run", "clone", "--target", str(target), "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("not empty", err)

    def test_library_subdirectory_target_refused(self):
        target = self.tmpdir / "nested_empty"
        target.mkdir()
        code, _, err = self.run_cli(
            "run", "clone", "--target", str(target), "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("sits inside the library", err)

    def test_dry_run_says_no_books_copy(self):
        target = self._target()
        code, out, _ = self.run_cli(
            "run", "clone", "--target", str(target), "--db", str(self.db_path)
        )
        self.assertEqual(code, 0)
        self.assertIn("NO books", out)
        self.assertIn("Dry run", out)

    def test_apply_drives_calibredb_clone(self):
        target = self._target()
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "clone",
                "--target",
                str(target),
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["calibredb", "clone"])
        self.assertEqual(cmd[-1], str(target))
        # The clone's --library is the source library DIRECTORY.
        self.assertEqual(cmd[cmd.index("--library") + 1], str(self.tmpdir))

    def test_missing_calibredb_is_a_setup_refusal(self):
        target = self._target()
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            code, _, err = self.run_cli(
                "run",
                "clone",
                "--target",
                str(target),
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 2)
        self.assertIn("calibredb is not on PATH", err)


class TestFtsIndex(_IntegrateCase):
    """`run fts-index`: calibredb fts_index over the dirtied queue (read
    through cquarry's get_dirtied_formats). The default action reindexes
    exactly the queued (book:format) pairs; --status and --enable are the
    other two doors. No metadata.db backup for the reindex (sidecar only);
    --enable writes a preference row and carries its own backup rule."""

    def _sidecar_queue(self, *pairs):
        # cquarry's sidecar read requires the plain books_text table to
        # exist (a bare sidecar counts as absent), so create the real shape.
        con = sqlite3.connect(self.tmpdir / "full-text-search.db")
        con.execute(
            "CREATE TABLE IF NOT EXISTS books_text (book INTEGER, "
            "format TEXT, format_size INTEGER, format_hash TEXT, "
            "searchable_text TEXT, text_size INTEGER, text_hash TEXT, "
            "err_msg TEXT, timestamp TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS dirtied_formats (book INTEGER, "
            "format TEXT, timestamp TEXT)"
        )
        for book, fmt in pairs:
            con.execute(
                "INSERT INTO dirtied_formats (book, format) VALUES (?, ?)",
                (book, fmt),
            )
        con.commit()
        con.close()

    def test_missing_binary_is_a_setup_refusal(self):
        # The refusal lives with the spawns: --fts-status executes
        # immediately, so it refuses; the dry runs and honest no-ops do not
        # (a runner without calibredb still gets plans).
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            code, _, err = self.run_cli(
                "run", "fts-index", "--fts-status", "--db", str(self.db_path)
            )
        self.assertEqual(code, 2)
        self.assertIn("calibredb is not on PATH", err)

    def test_dry_run_runs_without_the_binary(self):
        # The CI shape: no calibredb anywhere, and the plan still prints.
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            self._sidecar_queue((1, "EPUB"))
            code, out, _ = self.run_cli("run", "fts-index", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("fts-index plan", out)

    def test_status_and_enable_are_exclusive(self):
        code, _, err = self.run_cli(
            "run",
            "fts-index",
            "--fts-status",
            "--enable",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_empty_queue_is_an_honest_no_op(self):
        self._sidecar_queue()
        code, out, _ = self.run_cli("run", "fts-index", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("extraction queue is empty", out)

    def test_dry_run_lists_queued_pairs(self):
        self._sidecar_queue((1, "EPUB"), (3, "PDF"))
        code, out, _ = self.run_cli("run", "fts-index", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("2 queued (book:format) pair(s)", out)
        self.assertIn("1:EPUB", out)
        self.assertIn("3:PDF", out)

    def test_apply_reindexes_exactly_the_queue(self):
        self._sidecar_queue((1, "EPUB"), (3, "PDF"))
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run", "fts-index", "--apply", "--db", str(self.db_path)
            )
        self.assertEqual(code, 0, out)
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["calibredb", "fts_index"])
        self.assertIn("reindex", cmd)
        self.assertIn("1:EPUB", cmd)
        self.assertIn("3:PDF", cmd)

    def test_status_disabled_reads_as_a_report(self):
        # Upstream exits 2 when indexing is disabled: a report outcome here,
        # not a usage error.
        def fake_run(cmd, capture_output, text, timeout):
            return mock.Mock(returncode=2, stdout="", stderr="disabled")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run", "fts-index", "--fts-status", "--db", str(self.db_path)
            )
        self.assertEqual(code, 0)
        self.assertIn("disabled", out)

    def test_enable_apply_demands_backup_dir(self):
        code, _, err = self.run_cli(
            "run", "fts-index", "--enable", "--apply", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)

    def test_enable_dry_run_needs_nothing(self):
        code, out, _ = self.run_cli(
            "run", "fts-index", "--enable", "--db", str(self.db_path)
        )
        self.assertEqual(code, 0)
        self.assertIn("enable plan", out)


class TestCatalogVerbs(_IntegrateCase):
    """`run catalog-epub` / `run catalog-bibtex`: calibredb catalog through
    the catalog plugins. calibredb picks the plugin from the output
    extension and SILENTLY falls back to EPUB, so the extension is
    enforced here; the verb reads the library and never writes it."""

    def test_missing_dest_refused(self):
        code, _, err = self.run_cli("run", "catalog-epub", "--db", str(self.db_path))
        self.assertEqual(code, 2)
        self.assertIn("--dest FILE", err)

    def test_wrong_extension_refused(self):
        # The silent plugin fallback is exactly what this refuses.
        code, _, err = self.run_cli(
            "run",
            "catalog-bibtex",
            "--dest",
            "catalog.epub",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn(".bib file", err)

    def test_unknown_id_refused(self):
        code, _, err = self.run_cli(
            "run",
            "catalog-epub",
            "--dest",
            "catalog.epub",
            "--ids",
            "999",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown book id 999", err)

    def test_dry_run_plans_the_whole_library_without_a_selection(self):
        code, out, _ = self.run_cli(
            "run",
            "catalog-epub",
            "--dest",
            "catalog.epub",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("3 book(s)", out)
        self.assertIn("Dry run", out)

    def test_apply_drives_calibredb_catalog_with_ids(self):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="", stderr="")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which", return_value="/usr/bin/calibredb"
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "catalog-bibtex",
                "--dest",
                "refs.bib",
                "--ids",
                "1,2",
                "--apply",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0, out)
        cmd = calls[0]
        self.assertEqual(cmd[:2], ["calibredb", "catalog"])
        self.assertIn("refs.bib", cmd)
        self.assertIn("1,2", cmd)


class TestCustomize(unittest.TestCase):
    """`run customize`: calibre-customize headless. Needs no library: main()
    routes it before any database resolution, so these tests build none."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        # Everything runs closed-Calibre; the guard tests override.
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def test_no_action_is_a_usage_error(self):
        code, _, err = self.run_cli("run", "customize")
        self.assertEqual(code, 2)
        self.assertIn("needs one of", err)

    def test_two_actions_refused(self):
        code, _, err = self.run_cli(
            "run", "customize", "--list-plugins", "--enable-plugin", "X"
        )
        self.assertEqual(code, 2)
        self.assertIn("exactly one action", err)

    def test_list_drives_calibre_customize(self):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout="Type|Name|Version", stderr="")

        with (
            mock.patch(
                "cquarry_cli.integrate.shutil.which",
                return_value="/usr/bin/calibre-customize",
            ),
            mock.patch("subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli("run", "customize", "--list-plugins")
        self.assertEqual(code, 0, out)
        self.assertEqual(calls[0], ["calibre-customize", "-l"])

    def test_add_plugin_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "plugin.zip"
            zip_path.write_bytes(b"zip")
            code, out, _ = self.run_cli(
                "run", "customize", "--add-plugin", str(zip_path)
            )
        self.assertEqual(code, 0)
        self.assertIn("customize plan: install", out)
        self.assertIn("Dry run", out)

    def test_add_plugin_missing_zip_refused(self):
        code, _, err = self.run_cli(
            "run", "customize", "--add-plugin", "/no/such/plugin.zip"
        )
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_apply_drives_calibre_customize(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "bindery_repair.zip"
            zip_path.write_bytes(b"zip")

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(cmd)
                return mock.Mock(
                    returncode=0, stdout="Plugin added: Bindery", stderr=""
                )

            with (
                mock.patch(
                    "cquarry_cli.integrate.shutil.which",
                    return_value="/usr/bin/calibre-customize",
                ),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                code, out, _ = self.run_cli(
                    "run",
                    "customize",
                    "--add-plugin",
                    str(zip_path),
                    "--apply",
                )
        self.assertEqual(code, 0, out)
        cmd = calls[0]
        self.assertEqual(cmd[0], "calibre-customize")
        self.assertEqual(cmd[1], "-a")
        self.assertEqual(cmd[2], str(zip_path))

    def test_apply_refuses_a_live_calibre(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "plugin.zip"
            zip_path.write_bytes(b"zip")
            with mock.patch(
                "cquarry_cli.integrate._calibre_running", return_value=True
            ):
                code, _, err = self.run_cli(
                    "run", "customize", "--add-plugin", str(zip_path), "--apply"
                )
        self.assertEqual(code, 1)
        self.assertIn("Calibre is running", err)

    def test_missing_binary_at_spawn_refused(self):
        with mock.patch("cquarry_cli.integrate.shutil.which", return_value=None):
            code, _, err = self.run_cli("run", "customize", "--list-plugins")
        self.assertEqual(code, 2)
        self.assertIn("calibre-customize is not on PATH", err)


class TestDebugTools(unittest.TestCase):
    """`run debug-tools`: the curated calibre-debug subset. No library; the
    -e/--exec-file surface is deliberately unparseable."""

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def setUp(self):
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def test_no_action_is_a_usage_error(self):
        code, _, err = self.run_cli("run", "debug-tools")
        self.assertEqual(code, 2)
        self.assertIn("exactly one of", err)

    def test_two_actions_refused(self):
        code, _, err = self.run_cli(
            "run", "debug-tools", "--diff", "a", "b", "--diff", "c", "d"
        )
        self.assertEqual(code, 2)

    def test_exec_file_is_not_a_verb(self):
        # The parser does not offer it: argparse refuses the unknown flag
        # with its own usage error (exit 2), which is the point.
        with self.assertRaises(SystemExit) as cm:
            self.run_cli("run", "debug-tools", "--exec-file", "x.py")
        self.assertEqual(cm.exception.code, 2)

    def test_missing_path_refused_at_the_plan_door(self):
        code, _, err = self.run_cli(
            "run", "debug-tools", "--kepubify", "/no/such/book.epub"
        )
        self.assertEqual(code, 2)
        self.assertIn("no such path", err)

    def test_dry_run_prints_the_exact_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "book.epub"
            book.write_bytes(b"epub")
            code, out, _ = self.run_cli("run", "debug-tools", "--kepubify", str(book))
        self.assertEqual(code, 0)
        self.assertIn("debug-tools plan: calibre-debug --kepubify", out)

    def test_apply_drives_calibre_debug(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "book.epub"
            book.write_bytes(b"epub")

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(cmd)
                return mock.Mock(returncode=0, stdout="converted", stderr="")

            with (
                mock.patch(
                    "cquarry_cli.integrate.shutil.which",
                    return_value="/usr/bin/calibre-debug",
                ),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                code, out, _ = self.run_cli(
                    "run", "debug-tools", "--kepubify", str(book), "--apply"
                )
        self.assertEqual(code, 0, out)
        self.assertEqual(calls[0], ["calibre-debug", "--kepubify", str(book)])

    def test_diff_is_a_read_and_runs_without_apply(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.epub"
            b = Path(tmp) / "b.epub"
            a.write_bytes(b"a")
            b.write_bytes(b"b")

            def fake_run(cmd, capture_output, text, timeout):
                calls.append(cmd)
                return mock.Mock(returncode=0, stdout="", stderr="")

            with (
                mock.patch(
                    "cquarry_cli.integrate.shutil.which",
                    return_value="/usr/bin/calibre-debug",
                ),
                mock.patch("subprocess.run", side_effect=fake_run),
            ):
                code, _, _ = self.run_cli(
                    "run", "debug-tools", "--diff", str(a), str(b)
                )
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ["calibre-debug", "--diff", str(a), str(b)])

    def test_apply_refuses_a_live_calibre(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = Path(tmp) / "book.epub"
            book.write_bytes(b"epub")
            with mock.patch(
                "cquarry_cli.integrate._calibre_running", return_value=True
            ):
                code, _, err = self.run_cli(
                    "run", "debug-tools", "--kepubify", str(book), "--apply"
                )
        self.assertEqual(code, 1)
        self.assertIn("Calibre is running", err)


if __name__ == "__main__":
    unittest.main()
