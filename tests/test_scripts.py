"""Tests for the companion scripts' pure decision logic and DB sync.

The scripts in scripts/ are standalone (not a package), so they are loaded by
path. The Ghostscript and zip-reading I/O paths are not exercised; the Calibre
size sync is tested against a throwaway temporary SQLite fixture, never a live
metadata.db.
"""

import contextlib
import csv
import importlib.util
import io
import json
import os
import pathlib
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from cquarry.db import CalibreDB

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compress_pdf = _load("compress_pdf")


def _features(**over):
    base = {
        "size": 300 << 20,  # 300 MB
        "pages": 100,
        "optimized": False,
        "form": "none",
        "javascript": False,
        "attachments": 0,
        "avg_dpi": 300,
        "image_count": 10,
        "tagged": False,
    }
    base.update(over)
    return base


class TestRecommend(unittest.TestCase):
    def verdict(self, **over):
        return compress_pdf.recommend(_features(**over))[0]

    def test_skip_small(self):
        self.assertEqual(self.verdict(size=10 << 20), "skip-small")

    def test_manual_when_no_images(self):
        self.assertEqual(self.verdict(avg_dpi=None), "manual")

    def test_skip_already_low_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=120), "skip-already-low-dpi")

    def test_skip_optimized(self):
        self.assertEqual(
            self.verdict(optimized=True, size=150 << 20, avg_dpi=180),
            "skip-optimized",
        )

    def test_ebook_for_high_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=300), "ebook")

    def test_printer_when_risky(self):
        # A form field makes /printer the safer recommendation.
        self.assertEqual(self.verdict(avg_dpi=300, form="AcroForm"), "printer")

    def test_printer_for_moderate_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=200), "printer")


class TestFmtSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(compress_pdf.fmt_size(2 << 30), "2.00 GB")
        self.assertEqual(compress_pdf.fmt_size(5 << 20), "5.0 MB")
        self.assertTrue(compress_pdf.fmt_size(2048).endswith("KB"))


class TestCalibreSizeSync(unittest.TestCase):
    """update_calibre_size against a throwaway temp library (never the live DB)."""

    def _make_library(self, *, with_plugin_table=True):
        root = pathlib.Path(tempfile.mkdtemp(prefix="cq_lib_"))
        con = sqlite3.connect(root / "metadata.db")
        con.executescript("""
            CREATE TABLE books (id INTEGER PRIMARY KEY, path TEXT);
            CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT COLLATE NOCASE,
                uncompressed_size INT NOT NULL, name TEXT, UNIQUE(book, format));
            INSERT INTO books (id, path) VALUES (1, 'Author/Title (1)');
            INSERT INTO data (book, format, uncompressed_size, name) VALUES (1, 'PDF', 1000000, 'Title - Author');
        """)
        if with_plugin_table:
            con.executescript("""
                CREATE TABLE books_pages_link (book INTEGER PRIMARY KEY, pages INT DEFAULT 0,
                    algorithm INT DEFAULT 0, format TEXT DEFAULT '' COLLATE NOCASE,
                    format_size INT DEFAULT 0, timestamp TIMESTAMP, needs_scan INT DEFAULT 0);
                INSERT INTO books_pages_link (book, pages, format, format_size, needs_scan)
                    VALUES (1, 300, 'PDF', 1000000, 0);
            """)
        con.commit()
        con.close()
        return root

    def test_syncs_both_tables(self):
        root = self._make_library()
        pdf = root / "Author" / "Title (1)" / "Title - Author.pdf"
        compress_pdf.update_calibre_size(root, pdf, 250000)
        con = sqlite3.connect(root / "metadata.db")
        try:
            self.assertEqual(
                con.execute(
                    "SELECT uncompressed_size FROM data WHERE book=1 AND format='PDF'"
                ).fetchone()[0],
                250000,
            )
            size, needs = con.execute(
                "SELECT format_size, needs_scan FROM books_pages_link WHERE book=1"
            ).fetchone()
            self.assertEqual((size, needs), (250000, 1))
        finally:
            con.close()

    def test_works_without_plugin_table(self):
        # data.uncompressed_size must still update when books_pages_link is absent.
        root = self._make_library(with_plugin_table=False)
        pdf = root / "Author" / "Title (1)" / "Title - Author.pdf"
        compress_pdf.update_calibre_size(root, pdf, 99)
        con = sqlite3.connect(root / "metadata.db")
        try:
            self.assertEqual(
                con.execute(
                    "SELECT uncompressed_size FROM data WHERE book=1"
                ).fetchone()[0],
                99,
            )
        finally:
            con.close()

    def test_missing_book_does_not_raise(self):
        root = self._make_library()
        stray = root / "Nobody" / "Nothing (9)" / "x.pdf"
        compress_pdf.update_calibre_size(root, stray, 1)


class TestBackupGuard(unittest.TestCase):
    """A leftover .pre-compress.pdf rollback file must never be overwritten."""

    @unittest.skipUnless(shutil.which("gs"), "ghostscript not installed")
    def test_existing_backup_aborts_before_compressing(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_pdf_"))
        src = tmp / "book.pdf"
        backup = tmp / "book.pre-compress.pdf"
        src.write_bytes(b"%PDF-1.4 fake")
        backup.write_bytes(b"%PDF-1.4 original")
        rc = compress_pdf.compress(src, "ebook", dry_run=False)
        self.assertEqual(rc, 1)
        # both files untouched
        self.assertEqual(backup.read_bytes(), b"%PDF-1.4 original")
        self.assertEqual(src.read_bytes(), b"%PDF-1.4 fake")


class TestOutDirGuard(unittest.TestCase):
    """--out-dir promises the original is untouched, and lands the result at
    out_dir/<same name>. Pointed at the file's own directory those are the same
    path, so the safe mode used to replace the original with no rollback."""

    def test_out_dir_equal_to_the_source_dir_is_refused(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_outdir_"))
        try:
            src = tmp / "book.pdf"
            src.write_bytes(b"%PDF-1.4 original")
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = compress_pdf.compress(src, "ebook", dry_run=False, out_dir=tmp)
            self.assertEqual(rc, 2)
            self.assertEqual(src.read_bytes(), b"%PDF-1.4 original")
            self.assertIn("overwrite the original", buf.getvalue())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_symlinked_out_dir_is_still_caught(self):
        # The comparison resolves both sides, so aiming at a symlink to the
        # source directory does not sneak past it.
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_outdir_"))
        try:
            src = tmp / "book.pdf"
            src.write_bytes(b"%PDF-1.4 original")
            link = tmp / "alias"
            link.symlink_to(tmp)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = compress_pdf.compress(src, "ebook", dry_run=False, out_dir=link)
            self.assertEqual(rc, 2)
            self.assertEqual(src.read_bytes(), b"%PDF-1.4 original")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


spot_check = _load("spot_check")


class TestSpotCheckLint(unittest.TestCase):
    def test_case_garble_title_flags(self):
        flags = spot_check.lint_title("The Birth and Death of the Personal SPuter")
        self.assertTrue(any(f.startswith("TITLE_CASE_GARBLE") for f in flags))
        self.assertEqual(spot_check.lint_title("McHugh's HTTP Guide"), [])
        self.assertEqual(spot_check.lint_title("SQLite for QBasic Fans"), [])

    def test_mojibake_and_whitespace(self):
        self.assertIn("TITLE_MOJIBAKE", spot_check.lint_title("Itâ€™s Broken"))
        self.assertIn("TITLE_WHITESPACE", spot_check.lint_title("Double  Space"))

    def test_author_junk(self):
        flags = spot_check.lint_authors(["Mybooks Classics", "Jane Austen"])
        self.assertTrue(any(f.startswith("AUTHOR_JUNK") for f in flags))
        self.assertEqual(spot_check.lint_authors(["Ursula K. Le Guin"]), [])

    def test_comment_stub_and_missing(self):
        self.assertEqual(spot_check.lint_comment(None), ["COMMENT_MISSING"])
        self.assertTrue(
            spot_check.lint_comment("<p>short</p>")[0].startswith("COMMENT_STUB")
        )
        self.assertEqual(spot_check.lint_comment("x" * 200), [])

    def test_mojibake_covers_double_encoded_lead_byte(self):
        # "Ã¢" is the commonest lead byte of all and the old accent enumeration
        # missed it; a real "Ã" before an ASCII vowel must stay clean.
        self.assertTrue(spot_check._MOJIBAKE.search("youÃ¢??re"))
        self.assertTrue(spot_check._MOJIBAKE.search("SÃ£o Paulo"))
        self.assertTrue(spot_check._MOJIBAKE.search("cafÃ©"))
        self.assertTrue(spot_check._MOJIBAKE.search("bad � byte"))
        self.assertFalse(spot_check._MOJIBAKE.search("São Paulo"))
        self.assertFalse(spot_check._MOJIBAKE.search("café society"))
        self.assertFalse(spot_check._MOJIBAKE.search("Ãvila"))

    def test_comment_lint_decodes_entities(self):
        # Entities inflate raw length, so a stub could pass the gate; and mojibake
        # stored as entities was invisible to the old tag-strip-only path.
        self.assertEqual(spot_check.plain_text("<p>a&amp;b</p>"), "a&b")
        stub = "<p>" + "&amp;" * 40 + "</p>"  # 200 raw chars, 40 real ones
        self.assertTrue(spot_check.lint_comment(stub)[0].startswith("COMMENT_STUB"))

    def test_by_local_name_ignores_package_namespace(self):
        import xml.etree.ElementTree as ET

        oeb = (
            '<package xmlns="http://openebook.org/namespaces/oeb-package/1.0/">'
            '<manifest><item id="c1" href="text.xhtml"/></manifest>'
            '<spine><itemref idref="c1"/></spine></package>'
        )
        root = ET.fromstring(oeb)
        self.assertEqual(len(spot_check._by_local_name(root, "item")), 1)
        self.assertEqual(len(spot_check._by_local_name(root, "itemref")), 1)

    def test_comment_truncated(self):
        if spot_check._WORDS is None:
            self.skipTest("no system wordlist")
        prose = "She crossed the room and considered the whole miserable business. " * 3
        self.assertIn(
            "COMMENT_TRUNCATED", spot_check.lint_comment(prose + "her manipul")
        )
        # complete prose, proper-noun attributions, URLs and contents lists are not
        self.assertNotIn("COMMENT_TRUNCATED", spot_check.lint_comment(prose + "done."))
        self.assertNotIn(
            "COMMENT_TRUNCATED", spot_check.lint_comment(prose + "Publishers Weekly")
        )
        self.assertNotIn(
            "COMMENT_TRUNCATED", spot_check.lint_comment(prose + "see example.co.uk")
        )


class TestSpotCheckEpub(unittest.TestCase):
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )
    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )

    def _build(self, tmp, spine_doc=True):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            if spine_doc:
                z.writestr("text.xhtml", "<html>" + "x" * 40_000 + "</html>")
        return p

    def test_intact_epub_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(spot_check.check_epub(self._build(tmp)), [])

    def test_missing_spine_doc_is_hard_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            flags = spot_check.check_epub(self._build(tmp, spine_doc=False))
            self.assertTrue(any(f.startswith("EPUB_SPINE_MISSING") for f in flags))

    def test_garbage_file_is_badzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "junk.epub"
            p.write_bytes(b"not a zip at all")
            self.assertTrue(spot_check.check_epub(p)[0].startswith("EPUB_BADZIP"))


class TestSpotCheckReview(unittest.TestCase):
    """Review mode: the id reconciliation is the part that must not fail open."""

    def setUp(self):
        self.tmp = pathlib.Path(tempfile.mkdtemp())
        self.ids = self.tmp / "chunk.ids"
        self.ids.write_text("10\n20\n30\n")
        self.ledger = self.tmp / "ledger.tsv"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _verdicts(self, body):
        p = self.tmp / "v.tsv"
        p.write_text(body)
        return p

    def _record(self, body, against=True):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            rc = spot_check.record_verdicts(
                self._verdicts(body),
                self.ids if against else None,
                self.ledger,
                "2026-01-01",
            )
        return rc, buf.getvalue()

    def test_complete_verdicts_are_recorded(self):
        rc, _ = self._record(
            "10\tOK\tOK\tOK\t\n20\tOK\tBAD\tOK\tpublisher\n30\tOK\tOK\tOK\t\n"
        )
        self.assertEqual(rc, 0)
        led = spot_check.load_ledger(self.ledger)
        self.assertEqual(set(led), {10, 20, 30})
        self.assertEqual(led[20]["author"], "BAD")
        self.assertEqual(led[20]["note"], "publisher")

    def test_comma_delimited_note_keeps_its_commas(self):
        # Regression: comma-mode parsing dropped everything after the first
        # comma inside a free-text note, silently.
        rc, _ = self._record(
            "10,OK,OK,OK,\n20,OK,BAD,OK,wrong author, should be Jane Doe\n30,OK,OK,OK,\n"
        )
        self.assertEqual(rc, 0)
        led = spot_check.load_ledger(self.ledger)
        self.assertEqual(led[20]["note"], "wrong author, should be Jane Doe")

    def test_record_without_against_is_refused(self):
        # Regression: --record without --against skipped the id reconciliation
        # entirely, accepting exactly the short verdict lists it exists to catch.
        import sys
        from unittest import mock

        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["spot_check.py", "--record", "v.tsv"]),
            contextlib.redirect_stderr(buf),
        ):
            rc = spot_check.main()
        self.assertEqual(rc, 2)
        self.assertIn("--against", buf.getvalue())

    def test_a_dropped_id_is_refused(self):
        # The failure that has bitten repeatedly: a short list looks complete.
        rc, out = self._record("10\tOK\tOK\tOK\t\n20\tOK\tOK\tOK\t\n")
        self.assertEqual(rc, 1)
        self.assertIn("30", out)
        self.assertFalse(self.ledger.exists())

    def test_an_invented_id_is_refused(self):
        rc, out = self._record(
            "10\tOK\tOK\tOK\t\n20\tOK\tOK\tOK\t\n30\tOK\tOK\tOK\t\n99\tOK\tOK\tOK\t\n"
        )
        self.assertEqual(rc, 1)
        self.assertIn("99", out)
        self.assertFalse(self.ledger.exists())

    def test_an_unknown_verdict_word_is_refused(self):
        rc, out = self._record(
            "10\tOK\tMAYBE\tOK\t\n20\tOK\tOK\tOK\t\n30\tOK\tOK\tOK\t\n"
        )
        self.assertEqual(rc, 1)
        self.assertIn("MAYBE", out.upper())

    def test_ledger_roundtrips_and_worklist_lists_only_bad(self):
        self._record(
            "10\tOK\tOK\tOK\t\n20\tBAD\tOK\tOK\twrong book\n30\tOK\tOK\tOK\t\n"
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            spot_check.emit_worklist(self.ledger)
        out = buf.getvalue()
        self.assertIn("#20", out)
        self.assertIn("wrong book", out)
        self.assertNotIn("#10", out)

    def test_chunking_writes_matching_ids_files(self):
        rows = [
            {
                "id": i,
                "title": f"T{i}",
                "authors": "A",
                "tag": "x",
                "publisher": "p",
                "year": "2000",
                "formats": "EPUB",
                "series": "-",
                "comment": "c",
                "flags": "",
            }
            for i in range(1, 6)
        ]
        written = spot_check.write_review_chunks(rows, self.tmp / "r", 2)
        self.assertEqual(len(written), 3)
        seen = []
        for p in written:
            seen += [int(x) for x in p.with_suffix(".ids").read_text().split()]
        self.assertEqual(sorted(seen), [1, 2, 3, 4, 5])
        self.assertIn("##### 1", written[0].read_text())

    def test_chunking_an_empty_sample_writes_nothing(self):
        # Regression: main() then indexed written[0] for the --against hint and
        # died with IndexError instead of reporting an empty sample.
        self.assertEqual(spot_check.write_review_chunks([], self.tmp / "r", 2), [])

    def test_review_of_an_empty_sample_does_not_crash(self):
        import sys
        from unittest import mock

        db = self.tmp / "metadata.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT,"
            " pubdate TEXT);"
            "INSERT INTO books VALUES (1, 'T', 'A/T (1)', '2020-01-01');"
        )
        con.commit()
        con.close()
        argv = [
            "spot_check.py",
            "--db",
            str(db),
            "--n",
            "0",
            "--review",
            "--report",
            str(self.tmp / "r.tsv"),
            "--bundle",
            str(self.tmp / "b.txt"),
            "--ledger",
            str(self.tmp / "led.tsv"),
            "--review-prefix",
            str(self.tmp / "rev"),
        ]
        buf = io.StringIO()
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(buf):
            rc = spot_check.main()
        self.assertEqual(rc, 0)
        self.assertIn("nothing to review", buf.getvalue())


validate_metadata = _load("validate_metadata")


class TestFormatFictionPdf(unittest.TestCase):
    """One FORMAT_FICTION_PDF warning per book (regression: a crossover book
    carrying two fiction tags was warned about twice, inflating the count)."""

    def test_crossover_book_reported_once(self):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript("""
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (book INT, tag INT);
            CREATE TABLE data (book INT, format TEXT);
            INSERT INTO books VALUES (1, 'Crossover');
            INSERT INTO tags VALUES (1, 'Fic.Fantasy'), (2, 'Fic.Horror');
            INSERT INTO books_tags_link VALUES (1, 1), (1, 2);
            INSERT INTO data VALUES (1, 'PDF');
        """)
        report = validate_metadata.Reporter()
        validate_metadata.check_format_fiction_pdf(cur, report, ["Fic"])
        codes = [c for c, _ in report.warnings]
        self.assertEqual(codes.count("FORMAT_FICTION_PDF"), 1)
        msg = report.warnings[0][1]
        self.assertIn("Fic.Fantasy", msg)
        self.assertIn("Fic.Horror", msg)


fetch_library_codes = _load("fetch_library_codes")


class TestFetchLibraryCodesBackup(unittest.TestCase):
    """backup_db never overwrites an earlier restore point (regression: the
    date-only stamp clobbered the first backup on a same-day second run)."""

    def test_second_backup_gets_its_own_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib = pathlib.Path(tmp) / "Library"
            lib.mkdir()
            db = lib / "metadata.db"
            db.write_bytes(b"before first run")
            first = fetch_library_codes.backup_db(str(db))
            db.write_bytes(b"after first run")
            second = fetch_library_codes.backup_db(str(db))
            self.assertNotEqual(first, second)
            self.assertEqual(pathlib.Path(first).read_bytes(), b"before first run")
            self.assertEqual(pathlib.Path(second).read_bytes(), b"after first run")


class TestFetchLibraryCodesFindDb(unittest.TestCase):
    def test_missing_db_is_a_setup_error_exit_2(self):
        # Regression: sys.exit(<string>) exited 1, contradicting the documented
        # "2 = setup error" contract every other setup path follows.
        buf = io.StringIO()
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                with (
                    self.assertRaises(SystemExit) as cm,
                    contextlib.redirect_stderr(buf),
                ):
                    fetch_library_codes.find_db(None)
            finally:
                os.chdir(cwd)
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("metadata.db", buf.getvalue())


class TestFetchLibraryCodesGuard(unittest.TestCase):
    """calibre_running matches process NAMES only, never command lines.

    The 2026-08-27 refusal incident was recorded as a pgrep -f args
    false-positive, but the probe had matched exact names (-x) since
    2026-08-09 and could only refuse on a real calibre-named process; the
    record was a misdiagnosis. These tests pin the property that matters:
    args cannot trip the guard, and every calibre-named binary does.
    """

    def test_probe_is_name_only_never_args(self):
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=1)) as run:
            self.assertFalse(fetch_library_codes.calibre_running())
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "pgrep")
        self.assertNotIn("-f", argv)

    def test_probe_pattern_reaches_the_parallel_workers(self):
        # calibre-parallel's comm truncates to "calibre-paralle" (15 chars),
        # which the old exact-match -x "calibre" could never see; the anchored
        # prefix must. Verified live 2026-08-30 against a running Calibre with
        # four workers.
        with mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(fetch_library_codes.calibre_running())
        self.assertTrue(run.call_args.args[0][-1].startswith("^calibre"))

    def test_probe_timeout_assumes_calibre_is_running(self):
        # The gate fails closed: a hung probe refuses --apply, because
        # guessing wrong writes to a live database.
        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="pgrep", timeout=1)

        with mock.patch("subprocess.run", side_effect=hang):
            self.assertTrue(fetch_library_codes.calibre_running())


class TestFetchNeedsQuery(unittest.TestCase):
    """--all-codes selection: a book needs querying when the pass wants a
    code it does not already have."""

    def test_needs_query_semantics(self):
        f = fetch_library_codes
        both = {"has_lcc": True, "has_ddc": True}
        lcc_only = {"has_lcc": True, "has_ddc": False}
        neither = {"has_lcc": False, "has_ddc": False}
        self.assertFalse(f._needs_query(both, want_ddc=False))
        self.assertFalse(f._needs_query(both, want_ddc=True))
        self.assertFalse(f._needs_query(lcc_only, want_ddc=False))
        self.assertTrue(f._needs_query(lcc_only, want_ddc=True))
        self.assertTrue(f._needs_query(neither, want_ddc=False))
        self.assertTrue(f._needs_query(neither, want_ddc=True))


class TestFetchMissesWorklist(unittest.TestCase):
    """The manual-research pass starts from a file, not scrollback."""

    def test_worklist_written_with_misses_only(self):
        results = [
            {
                "id": 1,
                "title": "Has LCC",
                "isbn": "9780000000001",
                "lcc": "PS1",
                "ddc": None,
            },
            {
                "id": 2,
                "title": "No LCC",
                "isbn": "9780000000002",
                "lcc": None,
                "ddc": "519.5",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "misses.txt"
            n = fetch_library_codes.write_misses_worklist(results, str(path))
            self.assertEqual(n, 1)
            text = path.read_text()
        self.assertIn("2\t9780000000002\t519.5\tNo LCC", text)
        self.assertNotIn("Has LCC", text)

    def test_no_misses_no_file(self):
        results = [{"id": 1, "title": "T", "isbn": "x", "lcc": "PS1", "ddc": None}]
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "misses.txt"
            self.assertEqual(
                fetch_library_codes.write_misses_worklist(results, str(path)), 0
            )
            self.assertFalse(path.exists())


class TestFetchMissesDefault(unittest.TestCase):
    """Issue #7: the default worklist path must never be CWD-relative. The
    documented invocation runs from the library root, so the old default
    wrote fetch_library_codes_misses.txt INSIDE the library, where the tree
    audit flags it as a stray. The default now sits beside the script."""

    def test_default_is_absolute_and_beside_the_script(self):
        path = pathlib.Path(fetch_library_codes.default_misses_file())
        self.assertTrue(path.is_absolute())
        self.assertEqual(
            path.parent,
            pathlib.Path(fetch_library_codes.__file__).resolve().parent,
        )
        self.assertEqual(path.name, "fetch_library_codes_misses.txt")

    def test_default_is_independent_of_the_working_directory(self):
        # chdir into a fake library root: the default must not follow.
        cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                path = pathlib.Path(fetch_library_codes.default_misses_file())
            finally:
                os.chdir(cwd)
        self.assertNotEqual(path.parent, pathlib.Path(tmp))
        self.assertNotIn(str(pathlib.Path(tmp)), str(path))


class TestFetchWorkFallback(unittest.TestCase):
    """The title-fragment SRU fallback (--sru-fallback): the query shape
    issue #36 pinned (one pre-colon title fragment, no author clause, the
    author filters client-side), a cache key that cannot collide with an
    ISBN entry and carries the surname the records were filtered by, and
    targets that carry the author the fallback filter needs."""

    def test_work_query_shape(self):
        self.assertEqual(
            fetch_library_codes.work_query("Discourses"),
            'bath.title="Discourses"',
        )

    def test_work_query_strips_quotes(self):
        self.assertEqual(
            fetch_library_codes.work_query('Say "Ah"'), 'bath.title="Say Ah"'
        )

    def test_work_cache_key_normalizes_and_never_collides_with_isbn(self):
        f = fetch_library_codes
        key = f.work_cache_key("  The   Hobbit ", "Tolkien")
        self.assertEqual(key, f.work_cache_key("The Hobbit", "tolkien"))
        self.assertNotEqual(key, "9780261102214")
        self.assertTrue(key.startswith("work:"))

    def test_work_cache_key_separates_authors_sharing_a_fragment(self):
        f = fetch_library_codes
        self.assertNotEqual(
            f.work_cache_key("The Hobbit", "tolkien"),
            f.work_cache_key("The Hobbit", "gaiman"),
        )

    def test_load_targets_carries_authors_in_link_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = pathlib.Path(tmp) / "metadata.db"
            con = sqlite3.connect(db)
            con.executescript(
                """
                CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);
                CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
                CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
                CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT);
                """
            )
            con.execute("INSERT INTO books VALUES (1, 'Discourses')")
            con.execute("INSERT INTO authors VALUES (1, 'Epictetus')")
            con.execute("INSERT INTO books_authors_link (book, author) VALUES (1, 1)")
            con.execute("INSERT INTO tags VALUES (1, 'NonFic.Philosophy')")
            con.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 1)")
            con.execute(
                "INSERT INTO identifiers (book, type, val) VALUES (1, 'isbn', '9780465032001')"
            )
            con.commit()
            con.close()
            targets = fetch_library_codes.load_targets(str(db), [1], None)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["authors"], ["Epictetus"])


class TestFetchWriteIdentifiers(unittest.TestCase):
    """The write path goes through cquarry's write module (dirtied queue,
    last_modified) and waits out concurrent writers."""

    def _make_db(self, path):
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                timestamp TEXT, last_modified TEXT, path TEXT);
            CREATE TABLE identifiers (
                id INTEGER PRIMARY KEY, book INTEGER, type TEXT, val TEXT,
                UNIQUE(book, type));
            CREATE TABLE metadata_dirtied (book INTEGER UNIQUE);
            """
        )
        con.execute("INSERT INTO books (id,title) VALUES (1,'T1')")
        con.execute("INSERT INTO books (id,title) VALUES (2,'T2')")
        con.commit()
        con.close()

    def test_stores_counts_changes_and_queues(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "metadata.db")
            self._make_db(db)
            n = fetch_library_codes.write_identifiers(
                db, [(1, "lcc", "PS1"), (2, "ddc", "519.5")]
            )
            self.assertEqual(n, 2)
            # A --refresh re-run with identical values changes nothing and
            # says so, instead of counting the same rows again.
            n2 = fetch_library_codes.write_identifiers(
                db, [(1, "lcc", "PS1"), (2, "ddc", "519.5")]
            )
            self.assertEqual(n2, 0)
            con = sqlite3.connect(db)
            try:
                pairs = dict(
                    con.execute("SELECT book, type FROM identifiers").fetchall()
                )
                self.assertEqual(pairs, {1: "lcc", 2: "ddc"})
                dirtied = [
                    r[0] for r in con.execute("SELECT book FROM metadata_dirtied")
                ]
                self.assertEqual(sorted(dirtied), [1, 2])
                lm = con.execute(
                    "SELECT last_modified FROM books WHERE id = 1"
                ).fetchone()[0]
                self.assertTrue(lm)
            finally:
                con.close()

    def test_write_waits_out_a_concurrent_writer(self):
        # The 2026-09-02 incident: two background --apply passes contending
        # on the write lock. The writer now waits (30s busy timeout) instead
        # of dying on "database is locked"; a short-held lock proves it.
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "metadata.db")
            self._make_db(db)
            blocker = sqlite3.connect(db, check_same_thread=False)
            blocker.execute("BEGIN IMMEDIATE")
            started = threading.Event()

            def release():
                started.wait(timeout=5)
                time.sleep(1.0)
                blocker.rollback()
                blocker.close()

            th = threading.Thread(target=release)
            th.start()
            started.set()
            try:
                n = fetch_library_codes.write_identifiers(db, [(1, "lcc", "PS1")])
            finally:
                th.join()
            self.assertEqual(n, 1)


screen_duplicate = _load("screen_duplicate")
stamp_pdf = _load("stamp_pdf")
stamp_epub = _load("stamp_epub")
extract_stamp_evidence = _load("extract_stamp_evidence")


def _make_epub(
    path,
    title="Stub Title",
    author="Stub Author",
    identifiers=(),
    opf_name="content.opf",
):
    """A minimal synthetic EPUB: mimetype + container.xml + one OPF whose
    metadata carries the given dc:identifier elements verbatim, so every
    producer spelling from the 2026-09-22 live exercise can be built."""
    ident_xml = "".join(f"    {ident}\n" for ident in identifiers)
    opf = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" version="2.0">\n'
        "  <metadata>\n"
        f"    <dc:title>{title}</dc:title>\n"
        f"    <dc:creator>{author}</dc:creator>\n"
        f"{ident_xml}"
        "  </metadata>\n"
        "</package>\n"
    )
    container = (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        '  <rootfiles><rootfile full-path="' + opf_name + '" '
        'media-type="application/oebps-package+xml"/></rootfiles>\n'
        "</container>\n"
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("META-INF/container.xml", container)
        z.writestr(opf_name, opf)


class TestScreenDuplicateNormalize(unittest.TestCase):
    def test_scrubs_subtitle_edition_and_article(self):
        # The roadmap's example pair: both normalize to the same token.
        self.assertEqual(
            screen_duplicate.normalize_title("Capital: Volume I"),
            screen_duplicate.normalize_title(
                "Capital: A Critique of Political Economy"
            ),
        )
        self.assertEqual(
            screen_duplicate.normalize_title("The Left Hand of Darkness"),
            "left hand of darkness",
        )
        self.assertEqual(screen_duplicate.normalize_title("L'Étranger"), "l etranger")

    def test_volume_signature_gates_series_matches(self):
        # Real-library smoke finding (2026-08-30): scrubbing the colon segments
        # collapsed every Wandering Inn volume into one title, flooding 19
        # candidates for Book 8. Equal volume signatures are required whenever
        # both sides carry one.
        self.assertEqual(
            screen_duplicate._volume_signature(
                "The Wandering Inn: Book 8: Blood of Liscor"
            ),
            {("book", "8")},
        )
        self.assertEqual(screen_duplicate._volume_signature("Capital: Volume I"), set())

    def test_author_bracket_annotations_do_not_block_matches(self):
        # Real-library smoke finding: download metadata annotates the author
        # ("Pirateaba [Pirateaba]"); the annotation must not break the match.
        self.assertEqual(
            screen_duplicate.normalize_author("Pirateaba [Pirateaba]"), "pirateaba"
        )
        self.assertEqual(
            screen_duplicate.normalize_author_brackets("Pirateaba [Pirateaba]"),
            "Pirateaba",
        )

    def test_fold_author(self):
        self.assertEqual(
            screen_duplicate.normalize_author("Ursula K. Le Guin"), "ursula k. le guin"
        )
        self.assertEqual(screen_duplicate.normalize_author("ÉRIC"), "eric")


class TestScreenDuplicateClassify(unittest.TestCase):
    """classify_titles is the work-identity verdict the 2026-09-21 waves
    needed: prefix containment is a candidate, not a silent pass; shared
    bases with differing real subtitles are distinct; declared volumes
    disagreeing are one series root, not duplicates."""

    def _check(self, a, b, want):
        self.assertEqual(screen_duplicate.classify_titles(a, b), want)

    def test_volume_1_and_2_of_one_root_are_volume_siblings(self):
        # The 2026-09-16 Moral Letters shape (roadmap): distinct declared
        # volumes of one series root, never duplicates.
        self._check(
            "Moral Letters to Lucilius. Volume 1",
            "Moral Letters to Lucilius. Volume 2",
            "volume_sibling",
        )
        # Roman declarations, which the arabic-only signature cannot see,
        # obey the same rule (this pair was a FALSE duplicate before).
        self._check("Capital: Volume I", "Capital: Volume II", "volume_sibling")

    def test_bare_ordinal_tail_is_a_volume_sibling_not_a_duplicate(self):
        # The 2026-09-21 Monster Vault shape (roadmap): the scrubbed title
        # collides, the trailing ordinal separates the volumes.
        self._check(
            "Tales of the Valiant: Monster Vault",
            "Tales of the Valiant: Monster Vault 2",
            "volume_sibling",
        )

    def test_differing_real_subtitles_are_distinct_works(self):
        # The 2026-09-21 sibling collapse: two real books sharing a base
        # title, different ISBNs, 520 vs 515 pages. Never a match.
        self._check(
            "Introduction to Computer Organization: ARM",
            "Introduction to Computer Organization: An Under-the-Hood Look "
            "at Hardware and x86-64 Assembly",
            "distinct",
        )

    def test_prefix_containment_is_related_not_a_pass(self):
        # The Arcana Unleashed shape (roadmap 2026-09-19 box): bare base vs
        # base + companion subtitle.
        self._check("Arcana Unleashed", "Arcana Unleashed: Deadfall", "related")

    def test_imprint_tags_are_store_fronts_not_identity(self):
        # Issue #13's live miss: the rip's embedded title carried the
        # imprint tag and the title judge had no opinion on an otherwise
        # exact duplicate. The known set strips; two different imprints of
        # one work collide into the hand-check class, which is the point.
        self._check(
            "The Mabinogion (Oxford World's Classics)", "The Mabinogion", "duplicate"
        )
        self._check(
            "The Iliad (Penguin Classics)",
            "The Iliad (Oxford World's Classics)",
            "duplicate",
        )
        self._check(
            "Wuthering Heights [Wordsworth Classics]", "Wuthering Heights", "duplicate"
        )
        # A trailing parenthetical qualifier (a translator credit, issue
        # #40) is store-front noise on the bare-held comparison: one side
        # carrying it strips and the pair binds, exactly like the
        # imprint tags above.
        self._check(
            "The Mabinogion (Translated by Sioned Davies)",
            "The Mabinogion",
            "duplicate",
        )
        # A known imprint named in prose is not a bracketed tag.
        self._check("The Penguin Classics Book", "The Penguin Classics", None)
        # The tag is noise at any colon position: the strip covers the
        # subtitle helpers too, so a colon-bearing classic judges on its
        # real subtitle alone.
        self._check(
            "The Mabinogion: A New Translation (Oxford World's Classics)",
            "The Mabinogion: A New Translation",
            "duplicate",
        )
        self._check(
            "The Iliad (Penguin Classics): Translated by Fagles",
            "The Iliad: Translated by Fagles",
            "duplicate",
        )
        # The masked-duplicate shape: the file title IS the library copy's
        # post-colon subtitle.
        self._check("Wages of Sin", "Mothership: Wages of Sin", "related")

    def test_edition_tags_are_annotations_not_identity(self):
        # Issue #17's live misses (the 2026-10-13 evening wave): the stamp
        # strips the edition tag from new arrivals, so a held title still
        # carrying ", Nth Edition" defeated the judge on a same-edition
        # pair (Rust 3rd, Linux Command Line 3rd). The strip covers the
        # comma, colon, and "ed." forms, and differing real editions still
        # surface (the Ghidra shape was flagged before only because its
        # two titles matched exactly).
        self._check(
            "The Rust Programming Language",
            "The Rust Programming Language, 3rd Edition",
            "duplicate",
        )
        self._check(
            "The Linux Command Line: A Complete Introduction",
            "The Linux Command Line, 2nd Edition: A Complete Introduction",
            "duplicate",
        )
        self._check(
            "The Ghidra Book: The Definitive Guide",
            "The Ghidra Book, 2nd Edition: The Definitive Guide",
            "duplicate",
        )
        self._check("Pattern Recognition, 4th ed.", "Pattern Recognition", "duplicate")
        # An edition tag as the whole subtitle, and inside a bracket.
        self._check("Foo: 3rd Edition", "Foo", "duplicate")
        self._check("Foo (2nd Edition)", "Foo", "duplicate")
        # Stacked with an imprint tag (both strips compose).
        self._check(
            "The Mabinogion (Oxford World's Classics), 3rd Edition",
            "The Mabinogion",
            "duplicate",
        )
        # A genuinely different work sharing the base title does not
        # newly collide: "Version 2.0" is no edition tag, and the bare
        # base vs real-subtitle shape stays a related candidate.
        self._check("Code", "Code: Version 2.0", "related")
        # Mid-title prose is not an annotation and never strips.
        self._check("The 2nd edition of the Handbook", "The Handbook", None)
        # The volume rules keep reading the RAW title: an edition tag on
        # one side cannot flip a declared-volume disagreement.
        self._check(
            "Moral Letters to Lucilius. Volume 1, 2nd Edition",
            "Moral Letters to Lucilius. Volume 2",
            "volume_sibling",
        )
        self._check(
            "The Wandering Inn",
            "The Wandering Inn: Book 8",
            None,
        )

    def test_trailing_series_annotation_binds_the_bare_held_copy(self):
        # Issue #40's live miss (2026-10-28): a z-lib rip titled
        # "A Court of Splintered Harmony (A Court of Thorns and Roses)"
        # passed the screen with zero candidates against the held
        # bare-form title (book 6.0 of the held ACOTAR row). The trailing
        # parenthetical is store-front noise like the imprint tags: one
        # side carrying it strips, and the pair binds as the refusal the
        # hand-check then rules on.
        self._check(
            "A Court of Splintered Harmony (A Court of Thorns and Roses)",
            "A Court of Splintered Harmony",
            "duplicate",
        )
        # Identical annotations on both sides strip identically.
        self._check(
            "A Court of Wings and Ruin (A Court of Thorns and Roses)",
            "A Court of Wings and Ruin (A Court of Thorns and Roses)",
            "duplicate",
        )
        # A bracket-form annotation strips the same way.
        self._check(
            "Name of the Wind [The Kingkiller Chronicle]",
            "Name of the Wind",
            "duplicate",
        )

    def test_differing_trailing_parentheticals_are_distinct(self):
        # The Plantz rule in its parenthetical form (issue #40's guard):
        # two DIFFERENT trailing qualifiers are work identity, never
        # stripped into a match. The colon form of the same rule stays
        # covered by test_differing_real_subtitles_are_distinct_works.
        self._check(
            "Computer Organization (ARM)",
            "Computer Organization (x86-64)",
            "distinct",
        )
        self._check("Foo: Subtitle (ARM)", "Foo: Subtitle (x86-64)", "distinct")
        # "Part One"/"Part Two" declare no volume the annotation gate can
        # see (the roman parser reads neither word), so the paren rule
        # owns the pair.
        self._check("Foo (Part One)", "Foo (Part Two)", "distinct")
        # Declared volumes in parens keep the volume rules, which read
        # the RAW title: a disagreement is volume_sibling, and a
        # one-sided arabic declaration stays a silent gap-fill.
        self._check("Foo (Book 8)", "Foo (Book 9)", "volume_sibling")
        self._check("Foo (Book 8)", "Foo", None)
        self._check("Foo (Volume II)", "Foo (Volume III)", "volume_sibling")
        # A non-trailing bracket is title text, as before: only the END
        # of the title strips.
        self._check("Foo (Series): Subtitle", "Foo: Subtitle", None)
        # Deliberate widening the strip carries: with the qualifier gone,
        # a paren-annotated title against a colon-subtitled edition of
        # the same base is bare-base containment (an advisory candidate,
        # not a silent pass).
        self._check("Foo (Bar)", "Foo: Bar", "related")

    def test_arabic_asymmetric_volume_stays_silent(self):
        # The 3.25.0 gate unchanged: one side declaring an arabic volume the
        # other does not mention is a gap-fill (the Wandering Inn smoke
        # finding), never a match — and never an advisory flood either.
        self._check(
            "The Wandering Inn",
            "The Wandering Inn: Book 8: Blood of Liscor",
            None,
        )
        self._check(
            "Moral letters to Lucilius. Volume 1", "Moral Letters to Lucilius", None
        )

    def test_roman_annotation_keeps_the_capital_case_matching(self):
        # The regression floor: "Volume I" is a pure annotation, so the
        # core subtitle is empty and the full-subtitle edition matches.
        self._check(
            "Capital: Volume I",
            "Capital: A Critique of Political Economy",
            "duplicate",
        )

    def test_annotations_read_roman_and_canonicalize_tokens(self):
        self.assertEqual(
            screen_duplicate._annotations("Capital: Volume I"),
            frozenset({("volume", 1)}),
        )
        self.assertEqual(
            screen_duplicate._annotations("Capital: Vol. 1"),
            frozenset({("volume", 1)}),
        )
        self.assertEqual(
            screen_duplicate._annotations("The Wandering Inn: Book 8"),
            frozenset({("book", 8)}),
        )


class TestScreenDuplicateMatching(unittest.TestCase):
    """The library match: exact ISBN first, then normalized title + first
    author over cquarry's tight `=` search. ebook-meta is stubbed."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        con = sqlite3.connect(os.path.join(self.temp_dir, "metadata.db"))
        c = con
        c.execute(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,"
            " author_sort TEXT, timestamp TEXT, pubdate TEXT, last_modified TEXT,"
            " series_index REAL, path TEXT, has_cover INTEGER)"
        )
        c.executemany(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (?, ?, ?, ?, ?, 1)",
            [
                (
                    1,
                    "Capital: A Critique of Political Economy",
                    "Capital",
                    "Marx, Karl",
                    "p1",
                ),
                (
                    2,
                    "The Left Hand of Darkness",
                    "Left Hand of Darkness",
                    "Le Guin, Ursula K.",
                    "p2",
                ),
                # The 2026-09-21 masked-duplicate fixture: the library copy
                # carries the series prefix the download's embedded title
                # lacks ("Mothership: Wages of Sin" vs "Wages of Sin").
                (
                    3,
                    "Mothership: Wages of Sin",
                    "Wages of Sin",
                    "Gearing, Luke",
                    "p3",
                ),
                # The 2026-09-21 volume-collapse fixture: the library holds
                # Monster Vault 2 while a Monster Vault (no ordinal)
                # download screens against it.
                (
                    4,
                    "Tales of the Valiant: Monster Vault 2",
                    "Tales of the Valiant: Monster Vault 2",
                    "Kobold Press",
                    "p4",
                ),
            ],
        )
        c.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute(
            "INSERT INTO authors VALUES (1, 'Karl Marx'), (2, 'Ursula K. Le Guin'),"
            " (3, 'Luke Gearing'), (4, 'Kobold Press')"
        )
        c.execute(
            "CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY,"
            " book INTEGER, author INTEGER)"
        )
        c.executemany(
            "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
            [(1, 1), (2, 2), (3, 3), (4, 4)],
        )
        c.execute("CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute(
            "CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INTEGER, tag INTEGER)"
        )
        c.execute(
            "CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER,"
            " format TEXT, uncompressed_size INTEGER, name TEXT)"
        )
        c.execute(
            "INSERT INTO data (book, format, uncompressed_size, name) VALUES (1, 'EPUB', 2048, 'capital')"
        )
        c.execute("CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT)")
        c.execute(
            "INSERT INTO identifiers (book, type, val) VALUES (1, 'isbn', '9780140445688')"
        )
        # The issue-#13 shape: the held Mabinogion carries the mobi-asin
        # that z-lib rips keep when they strip the ISBN.
        c.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (5, 'The Mabinogion', 'Mabinogion, The', 'Anonymous', 'p5', 1)"
        )
        c.execute("INSERT INTO authors VALUES (5, 'Anonymous')")
        c.execute("INSERT INTO books_authors_link (book, author) VALUES (5, 5)")
        c.execute(
            "INSERT INTO identifiers (book, type, val) VALUES"
            " (5, 'mobi-asin', 'B005Q8EAEI')"
        )
        # The issue-#28 shape: a held book that came in through the same
        # amateur tutorial conversion carries the SAME junk URL identifier
        # a new sibling conversion carries. The keypair searches must not
        # bind on it.
        c.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (6, 'A Tutorial Conversion', 'Tutorial Conversion, A',"
            " 'Someone, Else', 'p6', 1)"
        )
        c.execute("INSERT INTO authors VALUES (6, 'Someone Else')")
        c.execute("INSERT INTO books_authors_link (book, author) VALUES (6, 6)")
        c.execute(
            "INSERT INTO identifiers (book, type, val) VALUES"
            " (6, 'isbn', 'http://www.hxa7241.org/articles/content/epup-guide')"
        )
        # The issue-#33 fixture: the held A Pattern Language carries the
        # ISBN-10 notation and the middle-initial author form, while the
        # arriving rip's stamp carries the 13-form and the bare name. Book
        # 8 lists its Alexander second, the any-author gate's shape.
        c.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (7, 'A Pattern Language', 'Pattern Language, A',"
            " 'Alexander, Christopher W.', 'p7', 1)"
        )
        c.execute("INSERT INTO authors VALUES (7, 'Christopher W. Alexander')")
        c.execute("INSERT INTO authors VALUES (8, 'Howard Davis')")
        c.execute("INSERT INTO books_authors_link (book, author) VALUES (7, 7)")
        c.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (8, 'The Production of Houses', 'Production of Houses, The',"
            " 'Davis, Howard', 'p8', 1)"
        )
        c.execute("INSERT INTO authors VALUES (9, 'Christopher Alexander')")
        c.execute("INSERT INTO books_authors_link (book, author) VALUES (8, 8), (8, 9)")
        # The initials-only shape: no surname token exists, so the
        # library door's relaxed supply is the exact search (3.70.0
        # behavior) and the author gate's string equality passes it.
        c.execute(
            "INSERT INTO books (id, title, sort, author_sort, path, has_cover)"
            " VALUES (9, 'Tree and Leaf', 'Tree and Leaf', 'J. R. R.', 'p9', 1)"
        )
        c.execute("INSERT INTO authors VALUES (10, 'J. R. R.')")
        c.execute("INSERT INTO books_authors_link (book, author) VALUES (9, 10)")
        c.execute(
            "INSERT INTO identifiers (book, type, val) VALUES (7, 'isbn', '0195019199')"
        )
        # Tables _BOOK_SELECT joins; empty here, present so get_all_books works.
        c.execute("CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute(
            "CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INTEGER, series INTEGER)"
        )
        c.execute("CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute(
            "CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INTEGER, publisher INTEGER)"
        )
        c.execute("CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT)")
        c.execute(
            "CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INTEGER, lang_code INTEGER)"
        )
        c.execute("CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INTEGER)")
        c.execute(
            "CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INTEGER, rating INTEGER)"
        )
        con.commit()
        con.close()
        self.db = CalibreDB(os.path.join(self.temp_dir, "metadata.db"))

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.temp_dir)

    def test_exact_isbn_hit(self):
        fields = {
            "file": "/dl/x.epub",
            "title": "Capital (Penguin)",
            "authors": ["Karl Marx"],
            "isbn": "9780140445688",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([h["id"] for h in hits], [1])
        self.assertEqual(hits[0]["formats"], ["EPUB"])
        self.assertEqual(hits[0]["size"], 2048)

    def test_asin_hit_across_type_spellings(self):
        # Issue #13's live miss, end to end through the real search engine:
        # the rip carries no isbn, only mobi-asin B005Q8EAEI, and the held
        # book carries the SAME value. The exact-value keypair matches any
        # identifier type, so a file-side `asin` meets a library-side
        # `mobi-asin`. The title/author gates alone still find nothing:
        # the translator-form author matches no library author.
        fields = {
            "file": "/dl/mabi.epub",
            "title": "The Mabinogion (Oxford World's Classics)",
            "authors": ["Davies, Sioned"],
            "isbn": "",
            "asins": ["B005Q8EAEI"],
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([h["id"] for h in hits], [5])
        self.assertEqual(related, [])
        no_asin = dict(fields, asins=[])
        self.assertEqual(screen_duplicate._library_hits(self.db, no_asin), ([], []))

    def test_embedded_fields_collect_asins(self):
        # The Identifiers-line parse keeps ASIN-type values (both common
        # type spellings) alongside the isbn; other types stay out.
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            return_value={
                "title": "The Mabinogion (Oxford World's Classics)",
                "author(s)": "Davies, Sioned",
                "identifiers": "mobi-asin:B005Q8EAEI, uri:urn:uuid:x, asin:B000OTHER09",
            },
        ):
            rec = screen_duplicate._embedded_fields(Path("/dl/mabi.epub"))
        self.assertEqual(rec["isbn"], "")
        self.assertEqual(rec["asins"], ["B005Q8EAEI", "B000OTHER09"])

    def test_same_asin_binds_batch_duplicates(self):
        # The within-batch half of issue #13: two rips of one edition, no
        # ISBN either side, translator-form author vs the original. The
        # shared ASIN binds them like a shared ISBN, across type spellings.
        files = [Path("/dl/m1.epub"), Path("/dl/m2.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "The Mabinogion (Oxford World's Classics)",
                    "author(s)": "Davies, Sioned",
                    "identifiers": "mobi-asin:B005Q8EAEI",
                },
                {
                    "title": "The Mabinogion",
                    "author(s)": "Anonymous",
                    "identifiers": "asin:b005q8eaei",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/m2.epub"])
        self.assertEqual(out[1]["batch_duplicates"], ["/dl/m1.epub"])
        # Both also hit the fixture book on the same ASIN.
        self.assertEqual([h["id"] for h in out[0]["library_hits"]], [5])
        self.assertEqual([h["id"] for h in out[1]["library_hits"]], [5])

    def test_different_volume_is_not_a_hit(self):
        # Book 7 of a series is not a duplicate of a Book 8 download, even
        # though the scrubbed series root matches.
        fields = {
            "file": "/dl/x.epub",
            "title": "The Wandering Inn: Book 7: Rains of Liscor",
            "authors": ["Pirateaba [Pirateaba]"],
            "isbn": "",
        }
        fields8 = {
            "file": "/dl/x.epub",
            "title": "The Wandering Inn: Book 8: Blood of Liscor",
            "authors": ["Pirateaba [Pirateaba]"],
            "isbn": "",
        }
        self.assertEqual(
            screen_duplicate._volume_signature(fields["title"]), {("book", "7")}
        )
        self.assertEqual(
            screen_duplicate._volume_signature(fields8["title"]), {("book", "8")}
        )

    def test_scrubbed_title_matches_worded_differently_edition(self):
        # No ISBN on the file; the embedded title says "Volume I" while the
        # library copy is the full subtitle. Raw `=` equality would miss.
        fields = {
            "file": "/dl/x.pdf",
            "title": "Capital: Volume I",
            "authors": ["Karl Marx"],
            "isbn": "",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([h["id"] for h in hits], [1])
        self.assertEqual(related, [])

    def test_masked_duplicate_surfaces_as_related_not_a_pass(self):
        # The 2026-09-21 masked duplicate (named fixture): the library copy
        # is "Mothership: Wages of Sin", the download's embedded title is
        # just "Wages of Sin". The old normalized equality passed silently;
        # the containment candidate must reach the report instead.
        fields = {
            "file": "/dl/wages.epub",
            "title": "Wages of Sin",
            "authors": ["Luke Gearing"],
            "isbn": "",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual(hits, [])
        self.assertEqual([h["id"] for h in related], [3])

    def test_bare_ordinal_volume_file_is_not_a_library_duplicate(self):
        # The Monster Vault shape against the library: "Tales of the
        # Valiant: Monster Vault 2" owned, "Monster Vault" download arrives.
        # Distinct volumes: no duplicate, no related, nothing refused (the
        # phase-1 series census judges gaps).
        fields = {
            "file": "/dl/vault1.pdf",
            "title": "Tales of the Valiant: Monster Vault",
            "authors": ["Kobold Press"],
            "isbn": "",
        }
        self.assertEqual(screen_duplicate._library_hits(self.db, fields), ([], []))

    def test_different_author_is_not_a_hit(self):
        # Tight `=` author matching: Mark Lawrence is not T. E. Lawrence.
        fields = {
            "file": "/dl/x.epub",
            "title": "The Broken Empire",
            "authors": ["Mark Lawrence"],
            "isbn": "",
        }
        self.assertEqual(screen_duplicate._library_hits(self.db, fields), ([], []))

    def test_batch_duplicates_cross_reference(self):
        # Within-batch dedup: same ISBN binds the two files both ways.
        files = [Path("/dl/a.epub"), Path("/dl/b.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Capital: Volume I",
                    "author(s)": "Karl Marx",
                    "identifiers": "isbn:9780140445688",
                },
                {
                    "title": "Capital (Penguin Classics)",
                    "author(s)": "Karl Marx",
                    "identifiers": "isbn:9780140445688",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/b.epub"])
        self.assertEqual(out[1]["batch_duplicates"], ["/dl/a.epub"])
        # Both files also hit the real fixture book: the ISBN is book 1's.
        self.assertEqual([h["id"] for h in out[0]["library_hits"]], [1])
        self.assertEqual([h["id"] for h in out[1]["library_hits"]], [1])

    def test_sibling_editions_are_not_batch_duplicates(self):
        # The 2026-09-21 sibling collapse (named fixture): two REAL books
        # (different ISBNs, 520 vs 515 pages) sharing the base title. The
        # pre-colon scrub refused the newcomer twice in one day; the
        # differing real subtitles now separate them. Nothing is flagged.
        files = [Path("/dl/arm.pdf"), Path("/dl/x86.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Introduction to Computer Organization: ARM",
                    "author(s)": "Jonathan Bartlett",
                    "identifiers": "isbn:9781718502758",
                },
                {
                    "title": "Introduction to Computer Organization: An "
                    "Under-the-Hood Look at Hardware and x86-64 Assembly",
                    "author(s)": "Jonathan Bartlett",
                    "identifiers": "isbn:9781718500105",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_related", rec)
            self.assertNotIn("batch_volumes", rec)

    def test_volume_set_is_advised_not_refused(self):
        # The 2026-09-21 volume collapse (named fixture): Monster Vault and
        # Monster Vault 2 collided within the batch (the scrub made them
        # equal) and refused each other. Now they surface as a multi-volume
        # set: batch_volumes both ways, never batch_duplicates.
        files = [Path("/dl/vault.pdf"), Path("/dl/vault2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Tales of the Valiant: Monster Vault",
                    "author(s)": "Kobold Press",
                    "identifiers": "",
                },
                {
                    "title": "Tales of the Valiant: Monster Vault 2",
                    "author(s)": "Kobold Press",
                    "identifiers": "",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertNotIn("batch_duplicates", out[0])
        self.assertNotIn("batch_duplicates", out[1])
        self.assertEqual(out[0]["batch_volumes"], ["/dl/vault2.pdf"])
        self.assertEqual(out[1]["batch_volumes"], ["/dl/vault.pdf"])

    def test_moral_letters_volumes_advised_as_a_set(self):
        # The 2026-09-16 roadmap shape, within-batch: differing declared
        # arabic volumes are a set to approve together, not refusals.
        files = [Path("/dl/ml1.epub"), Path("/dl/ml2.epub"), Path("/dl/ml3.epub")]

        def meta(n):
            return {
                "title": f"Moral Letters to Lucilius. Volume {n}",
                "author(s)": "Lucius Annaeus Seneca",
                "identifiers": "",
            }

        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[meta(1), meta(2), meta(3)],
        ):
            out = screen_duplicate._screen(files, self.db)
        expected = [
            ["/dl/ml2.epub", "/dl/ml3.epub"],
            ["/dl/ml1.epub", "/dl/ml3.epub"],
            ["/dl/ml1.epub", "/dl/ml2.epub"],
        ]
        for rec, volumes in zip(out, expected):
            self.assertNotIn("batch_duplicates", rec)
            self.assertEqual(rec["batch_volumes"], volumes)

    def test_related_titles_advised_within_batch(self):
        # The 2026-09-19 Arcana shape (roadmap): bare base vs base +
        # companion subtitle. Advisory for human review, never a refusal.
        files = [Path("/dl/arcana.pdf"), Path("/dl/deadfall.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Arcana Unleashed",
                    "author(s)": "Wizards of the Coast",
                    "identifiers": "",
                },
                {
                    "title": "Arcana Unleashed: Deadfall",
                    "author(s)": "Wizards of the Coast",
                    "identifiers": "",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertNotIn("batch_duplicates", out[0])
        self.assertNotIn("batch_duplicates", out[1])
        self.assertEqual(out[0]["batch_related"], ["/dl/deadfall.pdf"])
        self.assertEqual(out[1]["batch_related"], ["/dl/arcana.pdf"])

    def test_filename_fallback_swapped_author_order_is_a_batch_duplicate(self):
        # Issue #5 (the Stroustrup pair): two metadata-less z-lib rips of one
        # book. ebook-meta reports the whole filename stem as the title and
        # Author(s) "Unknown", so the compared stems carried the bracketed
        # author segments in different orders and the screen passed silently.
        # The parsed seeds (first paren group = author, text before it =
        # title) with order-free token sets flag the pair.
        files = [Path("/dl/design1.pdf"), Path("/dl/design2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "The Design and Evolution of C++ (Bjarne Stroustrup) "
                    "(z-library.sk, Libgen, Z-Lib)",
                    "author(s)": "Unknown",
                },
                {
                    "title": "The design and evolution of C++ (Stroustrup, Bjarne) "
                    "(z-library.sk, Libgen, Z-Lib)",
                    "author(s)": "Unknown",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/design2.pdf"])
        self.assertEqual(out[1]["batch_duplicates"], ["/dl/design1.pdf"])

    def test_filename_fallback_never_crosses_authors(self):
        # The token gate keeps different works by different authors apart
        # even when the parsed titles agree: "Last, First" matches "First
        # Last", "Alice Smith" does not match "Bob Jones".
        files = [Path("/dl/math1.pdf"), Path("/dl/math2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "A History of Math (Alice Smith) (z-library.sk)",
                    "author(s)": "Unknown",
                },
                {
                    "title": "A history of math (Bob Jones) (z-library.sk)",
                    "author(s)": "Unknown",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_related", rec)
            self.assertNotIn("batch_volumes", rec)

    def test_filename_fallback_ignores_records_with_real_metadata(self):
        # One side carries a real author: the seed path is asymmetric (a
        # genuine title is never rewritten), so the pair falls back to the
        # raw stem comparison, unchanged.
        files = [Path("/dl/real.epub"), Path("/dl/loose.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "A History of Math",
                    "author(s)": "Alice Smith",
                    "identifiers": "isbn:9780140445688",
                },
                {
                    "title": "A History of Math (Alice Smith) (z-library.sk)",
                    "author(s)": "Unknown",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_related", rec)

    def test_filename_fallback_site_first_parens_do_not_over_match(self):
        # The review case: a site marker as the FIRST paren group is not an
        # author. Identical site groups make the author-token gate agree,
        # but the whole-stem token gate still separates two different works
        # whose real authors sit in a later group: the pair falls back to
        # the raw stem comparison (today's behavior).
        files = [Path("/dl/sf1.pdf"), Path("/dl/sf2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Star Fortune (z-library.sk) (Alice Smith)",
                    "author(s)": "Unknown",
                },
                {
                    "title": "star fortune (z-library.sk) (Bob Jones)",
                    "author(s)": "Unknown",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_related", rec)

    def test_filename_seed_parses_and_tokenizes(self):
        seed = screen_duplicate._filename_seed(
            {"title": "Foo (Stroustrup, Bjarne) (z-library.sk)", "authors": ["Unknown"]}
        )
        assert seed is not None
        self.assertEqual(seed["title"], "Foo")
        self.assertEqual(
            seed["tokens"],
            screen_duplicate._filename_seed(
                {
                    "title": "Foo (Bjarne Stroustrup) (z-library.sk)",
                    "authors": ["Unknown"],
                }
            )["tokens"],
        )

    def test_filename_seed_refuses_real_metadata_and_parenless_stems(self):
        self.assertIsNone(
            screen_duplicate._filename_seed(
                {"title": "Foo (Bar) (z-lib)", "authors": ["Alice Smith"]}
            )
        )
        self.assertIsNone(
            screen_duplicate._filename_seed(
                {"title": "Author - Title", "authors": ["Unknown"]}
            )
        )
        # An empty authors list is the other shape a stripped file takes.
        self.assertIsNotNone(
            screen_duplicate._filename_seed({"title": "Foo (Alice)", "authors": []})
        )

    def test_junk_typed_isbn_does_not_bind_within_batch(self):
        # Issue #28's live pair: two unrelated tutorial conversions carry
        # the SAME junk dc:identifier (the template's URL, typed isbn by
        # the converter). Identical junk values bind nothing: different
        # titles and authors never reach the title judge, and the
        # isbn-equality signal is shape-gated away.
        _JUNK = "http://www.hxa7241.org/articles/content/epup-guide"
        files = [Path("/dl/cid.epub"), Path("/dl/neveryon.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Poem of the Cid",
                    "author(s)": "Rita Hamilton",
                    "identifiers": f"isbn:{_JUNK}",
                },
                {
                    "title": "Return to Neveryon",
                    "author(s)": "Samuel R. Delany",
                    "identifiers": f"isbn:{_JUNK}",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_duplicate_basis", rec)

    def test_junk_isbn_keypair_searches_nothing(self):
        # The library-side half of the same gate: the held book #6 carries
        # the junk URL under isbn (another tutorial conversion imported
        # earlier), and the new sibling carries the same junk. The exact
        # isbn keypair must not match them; the title/author gates agree
        # (different works), so nothing surfaces.
        fields = {
            "file": "/dl/tutorial2.epub",
            "title": "A Different Book Entirely",
            "authors": ["Different Author"],
            "isbn": "http://www.hxa7241.org/articles/content/epup-guide",
        }
        self.assertEqual(screen_duplicate._library_hits(self.db, fields), ([], []))

    def test_junk_typed_asin_does_not_bind_within_batch(self):
        # Same gate on the asin slot: a junk URL filed under asin binds
        # nothing; the _bindable_asin shapes (B+9 alnum, ISBN-shaped) are
        # the only admissible values.
        _JUNK = "http://www.hxa7241.org/articles/content/epup-guide"
        files = [Path("/dl/j1.epub"), Path("/dl/j2.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "First Unrelated Work",
                    "author(s)": "Author One",
                    "identifiers": f"asin:{_JUNK}",
                },
                {
                    "title": "Second Unrelated Work",
                    "author(s)": "Author Two",
                    "identifiers": f"asin:{_JUNK}",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)

    def test_different_valid_isbns_are_evidence_against_a_duplicate(self):
        # Issue #28's second half, the 2026-10-15 seven-volume shape:
        # identical stamped titles + same author used to refuse as mutual
        # duplicates even though every pair carried a VALID, DIFFERENT
        # ISBN. Two checksum-passing different ISBNs are evidence AGAINST
        # a duplicate: no refusal at all.
        files = [Path("/dl/hob5.epub"), Path("/dl/hob6.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Heaven Official's Blessing",
                    "author(s)": "Mo Xiang Tong Xiu",
                    "identifiers": "isbn:9780316469005",
                },
                {
                    "title": "Heaven Official's Blessing",
                    "author(s)": "Mo Xiang Tong Xiu",
                    "identifiers": "isbn:9780316469012",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)
            self.assertNotIn("batch_related", rec)
            self.assertNotIn("batch_volumes", rec)

    def test_valid_vs_junk_isbn_difference_still_binds_on_title_author(self):
        # The suppression needs both ISBNs VALID: a valid-vs-junk
        # difference is not evidence (one side is garbage). The pair binds
        # through the title judge, and the basis names the conflicting
        # values so triage sees the junk side immediately (issue #30).
        files = [Path("/dl/ed1.epub"), Path("/dl/ed2.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:9780140445688",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:http://junk.example/template",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/ed2.epub"])
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/ed2.epub"],
            "title+author, isbn differs: 9780140445688 vs http://junk.example/template",
        )

    def test_identical_junk_isbn_pair_reads_plain_title_author_basis(self):
        # The review-found shape: two files sharing the SAME junk-typed
        # identifier AND the same title/author. The isbn equality is not
        # binding evidence (the shape gate refused it), and the two
        # values are identical, so "isbn differs: junk vs junk" would be
        # a lie in the durable record; the basis is the plain verdict.
        _JUNK = "http://junk.example/template"
        files = [Path("/dl/s1.epub"), Path("/dl/s2.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/s2.epub"])
        self.assertEqual(out[0]["batch_duplicate_basis"]["/dl/s2.epub"], "title+author")
        self.assertEqual(out[1]["batch_duplicate_basis"]["/dl/s1.epub"], "title+author")

    def test_batch_basis_isbn_and_title_routes(self):
        # Issue #30: every within-batch refusal carries its basis, keyed
        # by the sibling path, on BOTH records. p1<->p2 bind on the shared
        # ISBN; p1/p2<->p3 bind on titles with the isbn state named.
        files = [Path("/dl/p1.epub"), Path("/dl/p2.epub"), Path("/dl/p3.epub")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Capital: Volume I",
                    "author(s)": "Karl Marx",
                    "identifiers": "isbn:9780140445688",
                },
                {
                    "title": "Capital (Penguin Classics)",
                    "author(s)": "Karl Marx",
                    "identifiers": "isbn:9780140445688",
                },
                {
                    "title": "Capital",
                    "author(s)": "Karl Marx",
                    "identifiers": "",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/p2.epub"], "isbn 9780140445688"
        )
        self.assertEqual(
            out[1]["batch_duplicate_basis"]["/dl/p1.epub"], "isbn 9780140445688"
        )
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/p3.epub"],
            "title+author, isbn absent on one side",
        )
        self.assertEqual(
            out[2]["batch_duplicate_basis"]["/dl/p1.epub"],
            "title+author, isbn absent on one side",
        )
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/p2.epub", "/dl/p3.epub"])

    def test_library_hit_descriptors_carry_basis(self):
        # Issue #30 on the library side: each hit names the field that
        # matched, in the search order that found it first.
        fields = {
            "file": "/dl/tri.epub",
            "title": "The Left Hand of Darkness",
            "authors": ["Ursula K. Le Guin"],
            "isbn": "9780140445688",
            "asins": ["B005Q8EAEI"],
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual(
            [(h["id"], h["basis"]) for h in hits],
            [
                (1, "isbn 9780140445688"),
                (5, "asin B005Q8EAEI"),
                (2, "title+author"),
            ],
        )
        self.assertEqual(related, [])
        # The report descriptor keeps the basis through _describe.
        described = screen_duplicate._describe(hits[0])
        self.assertEqual(described["basis"], "isbn 9780140445688")

    def test_cross_notation_isbn_hits_the_library(self):
        # Issue #33's live pair: the rip's stamp carries the 13-form, the
        # held book the 10-form; the cross-notation keypair finds the ONE
        # edition and the basis names both spellings.
        fields = {
            "file": "/dl/apl.pdf",
            "title": "A Pattern Language",
            "authors": ["Christopher Alexander"],
            "isbn": "9780195019193",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual(
            [(h["id"], h["basis"]) for h in hits],
            [(7, "isbn 9780195019193, 13-form of 0195019199")],
        )
        self.assertEqual(related, [])

    def test_reverse_cross_notation_isbn_hits_the_library(self):
        # The mirror direction: the file carries the 10-form, the held
        # book the 978-prefixed 13-form (the Capital fixture).
        fields = {
            "file": "/dl/capital.pdf",
            "title": "Capital",
            "authors": ["Karl Marx"],
            "isbn": "0140445684",
        }
        hits, _ = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual(
            [(h["id"], h["basis"]) for h in hits],
            [(1, "isbn 9780140445688, 13-form of 0140445684")],
        )

    def test_middle_initial_author_still_hits_the_library(self):
        # Issue #33's secondary: the engine's exact author search cannot
        # supply a "Christopher W. Alexander" book for a bare "Christopher
        # Alexander" stamp. The surname search supplies the candidate and
        # the token gate passes it; the verdict stays title+author.
        fields = {
            "file": "/dl/apl2.pdf",
            "title": "A Pattern Language",
            "authors": ["Christopher Alexander"],
            "isbn": "",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([(h["id"], h["basis"]) for h in hits], [(7, "title+author")])
        self.assertEqual(related, [])

    def test_surname_match_without_the_author_stays_out(self):
        # The relaxed supply is surname-wide; the token gate is what
        # keeps a same-surname, different-author book out.
        fields = {
            "file": "/dl/other.pdf",
            "title": "A Pattern Language",
            "authors": ["Sara Alexander"],
            "isbn": "",
        }
        self.assertEqual(screen_duplicate._library_hits(self.db, fields), ([], []))

    def test_author_gate_checks_any_of_the_book_authors(self):
        # The engine's author search matches ANY author value; the token
        # gate keeps that semantics: the held Production of Houses lists
        # Christopher Alexander second, and a file stamped him first
        # still meets it on title+author.
        fields = {
            "file": "/dl/poh.pdf",
            "title": "The Production of Houses",
            "authors": ["Christopher Alexander"],
            "isbn": "",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([(h["id"], h["basis"]) for h in hits], [(8, "title+author")])
        self.assertEqual(related, [])

    def test_cross_notation_isbn_binds_batch_duplicates(self):
        # Issue #33's live pair within one completed/ mint: same edition,
        # ISBN-10 vs ISBN-13 stamps, middle-initial author variance. The
        # canonical 13-forms agree, so the pair binds on the ISBN with
        # both spellings named in the basis.
        files = [Path("/dl/apl1.pdf"), Path("/dl/apl2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "A Pattern Language",
                    "author(s)": "Christopher Alexander",
                    "identifiers": "isbn:9780195019193",
                },
                {
                    "title": "A Pattern Language",
                    "author(s)": "Christopher W. Alexander",
                    "identifiers": "isbn:0195019199",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        want = "isbn 9780195019193, 13-form of 0195019199"
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/apl2.pdf"])
        self.assertEqual(out[1]["batch_duplicates"], ["/dl/apl1.pdf"])
        self.assertEqual(out[0]["batch_duplicate_basis"]["/dl/apl2.pdf"], want)
        self.assertEqual(out[1]["batch_duplicate_basis"]["/dl/apl1.pdf"], want)
        # The same edition also meets the held #7 through the cross
        # notation, in the library route that runs first.
        self.assertEqual([h["id"] for h in out[0]["library_hits"]], [7])

    def test_middle_initial_variance_binds_on_title_author(self):
        # The author leg alone: same title, no identifiers either side,
        # one stamp carries the middle initial the other lacks. The pair
        # binds through the token gate.
        files = [Path("/dl/apl3.pdf"), Path("/dl/apl4.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "A Pattern Language",
                    "author(s)": "Christopher Alexander",
                    "identifiers": "",
                },
                {
                    "title": "A Pattern Language",
                    "author(s)": "Christopher W. Alexander",
                    "identifiers": "",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/apl4.pdf"])
        self.assertEqual(out[1]["batch_duplicates"], ["/dl/apl3.pdf"])
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/apl4.pdf"],
            "title+author, isbn absent on one side",
        )

    def test_cross_notation_of_different_editions_still_conflicts(self):
        # The evidence rule survives the canonical form: a 10-form and
        # the 13-form of a DIFFERENT edition convert to different
        # canonicals, both checksums pass, and the title+author bind
        # stays suppressed.
        files = [Path("/dl/x1.pdf"), Path("/dl/x2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:0195019199",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:9780316469012",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        for rec in out:
            self.assertNotIn("batch_duplicates", rec)

    def test_initials_only_author_still_hits_the_library(self):
        # No surname token exists ("J. R. R."), so the relaxed supply is
        # the exact search and the author gate's string equality passes
        # it: the 3.70.0 behavior survives for names the token form
        # cannot parse.
        fields = {
            "file": "/dl/tree.pdf",
            "title": "Tree and Leaf",
            "authors": ["J. R. R."],
            "isbn": "",
        }
        hits, related = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([(h["id"], h["basis"]) for h in hits], [(9, "title+author")])
        self.assertEqual(related, [])

    def test_junk_with_early_x_neither_crashes_nor_binds(self):
        # The review's crash class: a junk value whose digit reduction is
        # 10 chars with the X outside the check position used to reach
        # int() through the conversion; the shape gate keeps it a None
        # form, and an identical junk pair binds through the title judge
        # with the plain basis, exactly like any other identical junk.
        _JUNK = "X123456789"
        self.assertIsNone(screen_duplicate._isbn13_form(_JUNK))
        self.assertFalse(screen_duplicate._isbn_valid(_JUNK))
        files = [Path("/dl/jx1.pdf"), Path("/dl/jx2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/jx2.pdf"])
        self.assertEqual(out[0]["batch_duplicate_basis"]["/dl/jx2.pdf"], "title+author")

    def test_thirteen_char_x_junk_stays_off_the_isbn_bind(self):
        # The review's bypass class: a 13-char reduction carrying an X is
        # junk the shape gate refuses, so an identical pair never gains
        # the isbn basis 3.70.0 never gave it.
        _JUNK = "123456789012X"
        self.assertIsNone(screen_duplicate._isbn13_form(_JUNK))
        files = [Path("/dl/j13a.pdf"), Path("/dl/j13b.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": f"isbn:{_JUNK}",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/j13b.pdf"], "title+author"
        )

    def test_punctuation_variant_isbn_binds_as_one(self):
        # The same digits in two producer spellings are ONE ISBN: 3.70.0
        # read this pair as a conflict (raw strings differ, both valid)
        # and suppressed the bind; the canonical form binds it with the
        # plain single-value basis, since there is nothing to convert.
        files = [Path("/dl/pv1.pdf"), Path("/dl/pv2.pdf")]
        with mock.patch.object(
            screen_duplicate,
            "_ebook_meta",
            side_effect=[
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:978-0-14-044568-8",
                },
                {
                    "title": "Same Work",
                    "author(s)": "Same Author",
                    "identifiers": "isbn:9780140445688",
                },
            ],
        ):
            out = screen_duplicate._screen(files, self.db)
        self.assertEqual(out[0]["batch_duplicates"], ["/dl/pv2.pdf"])
        self.assertEqual(
            out[0]["batch_duplicate_basis"]["/dl/pv2.pdf"], "isbn 978-0-14-044568-8"
        )


class TestIdentifierShapes(unittest.TestCase):
    """The issue-#28 binding gates: only real identifier SHAPES bind, and
    only two VALID (checksum-passing) but different ISBNs count as
    evidence against a duplicate."""

    _JUNK = "http://www.hxa7241.org/articles/content/epup-guide"

    def test_isbn_shape_accepts_both_lengths_and_separators(self):
        for value in (
            "9780140445688",
            "978-0-14-044568-8",
            "978 0 14 044568 8",
            "978.0.14.044568.8",  # dotted producer spelling (the house strip)
            "0140445684",
            "0-14-044568-4",
            "014044568X",  # the X check-digit form is still the shape
        ):
            self.assertTrue(screen_duplicate._isbn_shaped(value), value)

    def test_isbn_shape_refuses_the_junk_classes(self):
        for value in (
            self._JUNK,
            "urn:uuid:0a1b-2c3d",
            "B005Q8EAEI",  # an ASIN is not an ISBN
            "12345",  # too short
            "9780140445688999",  # too long
            "",
        ):
            self.assertFalse(screen_duplicate._isbn_shaped(value), value)

    def test_isbn_valid_is_checksum_strict(self):
        self.assertTrue(screen_duplicate._isbn_valid("9780140445688"))
        self.assertTrue(screen_duplicate._isbn_valid("978-0-14-044568-8"))
        self.assertTrue(screen_duplicate._isbn_valid("0140445684"))
        self.assertFalse(
            screen_duplicate._isbn_valid("014044568X")
        )  # the X form is shaped; 4 is this number's real check digit
        self.assertFalse(screen_duplicate._isbn_valid("9780140445689"))  # one digit off
        self.assertFalse(screen_duplicate._isbn_valid(self._JUNK))

    def test_bindable_asin_takes_asin_and_isbn_shapes_only(self):
        self.assertTrue(screen_duplicate._bindable_asin("B005Q8EAEI"))
        self.assertTrue(
            screen_duplicate._bindable_asin("b005q8eaei")
        )  # case-folded rips
        self.assertTrue(
            screen_duplicate._bindable_asin("0140445684")
        )  # ISBN-10 values filed under asin bind (the pre-2001 ASINs)
        self.assertFalse(screen_duplicate._bindable_asin(self._JUNK))
        self.assertFalse(screen_duplicate._bindable_asin("urn:uuid:x"))


class TestIsbnNotationAndAuthorTokens(unittest.TestCase):
    """Issue #33's comparison forms: a 10-form and its 978-prefixed
    13-form are ONE edition, and a middle initial does not split one
    author in two."""

    def test_isbn13_form_converts_and_passes_through(self):
        self.assertEqual(screen_duplicate._isbn13_form("0195019199"), "9780195019193")
        self.assertEqual(
            screen_duplicate._isbn13_form("9780195019193"), "9780195019193"
        )
        self.assertEqual(
            screen_duplicate._isbn13_form("978-0-14-044568-8"), "9780140445688"
        )
        # An X check digit converts (123456789X is a valid ISBN-10).
        self.assertEqual(screen_duplicate._isbn13_form("123456789X"), "9781234567897")
        self.assertIsNone(screen_duplicate._isbn13_form(""))

    def test_isbn13_form_refuses_unshaped_values(self):
        # The junk URL strips to a non-10/13 digit string and binds
        # nothing; a too-short digit string neither.
        self.assertIsNone(
            screen_duplicate._isbn13_form(
                "http://www.hxa7241.org/articles/content/epup-guide"
            )
        )
        self.assertIsNone(screen_duplicate._isbn13_form("12345"))

    def test_cross_form_both_directions_and_the_979_dead_end(self):
        self.assertEqual(
            screen_duplicate._isbn_cross_form("9780195019193"), "0195019199"
        )
        self.assertEqual(
            screen_duplicate._isbn_cross_form("0195019199"), "9780195019193"
        )
        # An X check digit survives the 13 -> 10 recomputation.
        self.assertEqual(
            screen_duplicate._isbn_cross_form("9781234567897"), "123456789X"
        )
        # A 979-prefixed 13-form never carried a 10-form.
        self.assertIsNone(screen_duplicate._isbn_cross_form("9791234567890"))

    def test_pair_basis_is_order_free_and_quotes_both_spellings(self):
        want = "isbn 9780195019193, 13-form of 0195019199"
        self.assertEqual(
            screen_duplicate._isbn_pair_basis("9780195019193", "0195019199"), want
        )
        self.assertEqual(
            screen_duplicate._isbn_pair_basis("0195019199", "9780195019193"), want
        )

    def test_author_tokens_drop_middle_initials_only(self):
        self.assertEqual(
            screen_duplicate._author_tokens("Christopher W. Alexander"),
            screen_duplicate._author_tokens("Christopher Alexander"),
        )
        self.assertEqual(
            screen_duplicate._author_tokens("Ursula K. Le Guin"),
            screen_duplicate._author_tokens("Ursula Le Guin"),
        )
        # Sort form meets display form (order-free tokens).
        self.assertEqual(
            screen_duplicate._author_tokens("Alexander, Christopher W."),
            screen_duplicate._author_tokens("Christopher W. Alexander"),
        )
        # Multi-letter tokens all compare: different names stay different.
        self.assertNotEqual(
            screen_duplicate._author_tokens("Karl Marx"),
            screen_duplicate._author_tokens("Groucho Marx"),
        )
        self.assertNotEqual(
            screen_duplicate._author_tokens("Christopher Alexander"),
            screen_duplicate._author_tokens("Christopher Alexandra"),
        )

    def test_same_author_tokens_requires_a_real_token(self):
        self.assertTrue(
            screen_duplicate._same_author_tokens(
                "Christopher Alexander", "Christopher W. Alexander"
            )
        )
        self.assertFalse(screen_duplicate._same_author_tokens("", "Karl Marx"))
        # Identical folded names always match, whatever the token form
        # can see: initials-only names, and names with no ASCII tokens
        # at all (3.70.0's string equality covered both; the token form
        # must not take it away).
        self.assertTrue(screen_duplicate._same_author_tokens("J. R. R.", "J. R. R."))
        self.assertTrue(screen_duplicate._same_author_tokens("村上春樹", "村上春樹"))
        self.assertFalse(screen_duplicate._same_author_tokens("村上春樹", "Karl Marx"))

    def test_author_surname_needs_a_word(self):
        # The relaxed supply's search term must be a real word: a
        # one-letter token would substring-scan half the library, and a
        # non-Latin name has no token at all. Both fall back to the
        # exact search at the call site.
        self.assertEqual(screen_duplicate._author_surname("C. Alexander"), "alexander")
        self.assertEqual(screen_duplicate._author_surname("Stroustrup, B."), "")
        self.assertEqual(screen_duplicate._author_surname("村上春樹"), "")
        self.assertEqual(screen_duplicate._author_surname("J. R. R."), "")


class TestStampPdfGuards(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.pdf = self.dir / "5E - Wonderland.pdf"
        self.pdf.write_bytes(b"%PDF-1.4 fake")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_main(self, *extra):
        argv = ["stamp_pdf", str(self.pdf), *extra]
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(buf),
        ):
            return stamp_pdf.main(), buf.getvalue()

    def test_invalid_isbn_is_refused(self):
        # The 2026-09-19 roadmap case: the WPN-listed Arcana number fails
        # its check digit and rode past the tool into Keywords.
        rc, out = self._run_main("--isbn", "9780786967006", "--title", "X")
        self.assertEqual(rc, 2)
        self.assertIn("check digit", out)

    def test_invalid_isbn_is_refused_on_apply_too(self):
        # The guard is a usage error at the boundary: --apply does not
        # change it, and nothing is written (no backup dir demanded first).
        rc, out = self._run_main(
            "--isbn",
            "159863503",
            "--title",
            "X",
            "--apply",
            "--backup-dir",
            str(self.dir / "bak"),
        )
        self.assertEqual(rc, 2)
        self.assertIn("check digit", out)

    def test_valid_isbn_passes_the_guard(self):
        # The corrected Arcana number and a hyphenated ISBN-13 both pass
        # (separators tolerated); the dry run proceeds to its preview.
        # Note: the roadmap box's Hewitt "valid form" 1-59863-503-5 also
        # fails its check digit and is refused like every other bad number.
        for isbn in ("9780786970063", "978-0-14-044568-8"):
            rc, out = self._run_main("--isbn", isbn, "--title", "X")
            self.assertEqual(rc, 0, isbn)
            self.assertIn("dry run", out)

    def test_derive_from_filename(self):
        self.assertEqual(
            stamp_pdf._derive_from_filename(Path("5E - Wonderland.pdf")),
            ("5E", "Wonderland"),
        )
        self.assertEqual(
            stamp_pdf._derive_from_filename(Path("Bare.pdf")), ("Bare", "")
        )

    def test_exiftool_field_set_is_fixed(self):
        args = stamp_pdf._build_exiftool_args(
            "T", ["A One", "A Two"], "P", "9780140445688"
        )
        self.assertIn("-Title=T", args)
        self.assertIn("-XMP-dc:Title=T", args)
        self.assertIn("-Author=A One & A Two", args)
        self.assertIn("-XMP-dc:Creator=A One & A Two", args)
        self.assertIn("-XMP-dc:Publisher=P", args)
        self.assertIn("-Keywords=isbn:9780140445688", args)
        # The trap: Calibre maps -XMP-dc:Identifier to a bogus doi.
        self.assertFalse(any(a.startswith("-XMP-dc:Identifier") for a in args))

    def test_dry_run_default_never_invokes_exiftool(self):
        with mock.patch.object(stamp_pdf, "_run_exiftool") as run:
            rc, out = self._run_main("--title", "Wonderland")
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertIn("dry run", out)
        self.assertIn("'5E'", out)  # the filename-derivation preview

    def test_apply_requires_backup_dir(self):
        rc, out = self._run_main("--apply", "--title", "Wonderland")
        self.assertEqual(rc, 2)
        self.assertIn("backup-dir", out)

    def test_backup_dir_inside_target_dir_is_refused(self):
        rc, out = self._run_main(
            "--apply", "--title", "W", "--backup-dir", str(self.dir / "sub")
        )
        self.assertEqual(rc, 2)
        self.assertIn("OUTSIDE", out)

    def test_erase_args_carry_stamp_values_and_null_sort(self):
        # The erase pass re-carries the stamped values and nulls the sort so
        # Calibre's PDF writer regenerates the XMP packet with no
        # calibre-namespaced elements at all.
        args = stamp_pdf._erase_ebook_meta_args("T", ["A One", "A Two"], "P")
        self.assertEqual(
            args,
            [
                "ebook-meta",
                "--title",
                "T",
                "--authors",
                "A One & A Two",
                "--author-sort",
                "",
                "--publisher",
                "P",
            ],
        )
        # a field the stamp leaves alone is not re-carried either (Calibre's
        # writer leaves it alone too)
        self.assertNotIn(
            "--publisher", stamp_pdf._erase_ebook_meta_args("T", ["A"], "")
        )

    def _write_calls(self, run_mock):
        return [
            c
            for c in run_mock.call_args_list
            if any(a.startswith("-") and "=" in a for a in c.args[0])
        ]

    def test_apply_clears_stale_calibre_authorsort(self):
        # Roadmap 2026-09-21: a calibre-touched PDF carries XMP AuthorSort,
        # which rendered read-back as `Display [Sort]` and failed verify (and
        # Calibre's import honors it verbatim). The stamp now detects it and
        # rewrites the packet through ebook-meta before the read-back.
        backup = Path(self.tmp.name + "-backups")
        detect = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="WRONG SORT\n", stderr=""
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf, "_run_exiftool", side_effect=[detect, ok]
            ) as run,
            mock.patch.object(
                stamp_pdf,
                "_run_ebook_meta",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=""
                ),
            ) as erase,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland", "author(s)": "A One & A Two"},
            ),
        ):
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--author",
                "A One",
                "--author",
                "A Two",
            )
        self.assertEqual(rc, 0)
        self.assertIn("author_sort", out)
        erase_args = erase.call_args.args[0]
        self.assertEqual(erase_args[0], "ebook-meta")
        self.assertEqual(erase_args[erase_args.index("--author-sort") + 1], "")
        self.assertEqual(erase_args[-1], str(self.pdf))
        writes = self._write_calls(run)
        self.assertEqual(len(writes), 1, "exiftool writes exactly once")

    def test_apply_skips_erase_when_no_calibre_authorsort(self):
        backup = Path(self.tmp.name + "-backups")
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok),
            mock.patch.object(stamp_pdf, "_run_ebook_meta") as erase,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland", "author(s)": "A One"},
            ),
        ):
            rc, _ = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--author",
                "A One",
            )
        self.assertEqual(rc, 0)
        erase.assert_not_called()

    def test_apply_erase_failure_fails_the_stamp(self):
        backup = Path(self.tmp.name + "-backups")
        detect = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="WRONG SORT\n", stderr=""
        )
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", side_effect=[detect, ok]),
            mock.patch.object(
                stamp_pdf,
                "_run_ebook_meta",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=1, stdout="", stderr="ebook-meta died"
                ),
            ),
        ):
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--author",
                "A One",
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertIn("author_sort rewrite", out)

    def test_apply_stamps_and_verifies(self):
        backup = Path(self.tmp.name + "-backups")
        joined = "A One & A Two"
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok) as run,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={
                    "title": "Wonderland",
                    "author(s)": joined,
                    "identifiers": "isbn:9780140445688",
                },
            ),
        ):
            rc, _ = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--author",
                "A One",
                "--author",
                "A Two",
                "--isbn",
                "9780140445688",
            )
        self.assertEqual(rc, 0)
        writes = self._write_calls(run)
        self.assertEqual(len(writes), 1, "one detection probe plus one write")
        called = writes[0].args[0]
        self.assertEqual(called[-1], str(self.pdf))
        self.assertIn("-Title=Wonderland", called)
        self.assertIn(f"-Author={joined}", called)
        self.assertTrue(
            (backup / self.pdf.name).exists(), "backup copy made before writing"
        )

    def test_stubborn_xmp_fails_without_retry(self):
        backup = Path(self.tmp.name + "-backups")
        ok = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok) as run,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Something Else", "identifiers": ""},
            ),
        ):
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--isbn",
                "9780140445688",
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertIn("isbn", out)
        self.assertEqual(len(self._write_calls(run)), 1, "no re-fighting")

    # --- the parse-crash class (2026-09-26): exiftool rc != 0 with a
    # Perl-space error gets ONE qpdf --replace-input recovery + retry.

    def _crash(self, stderr=None):
        return subprocess.CompletedProcess(
            args=[],
            returncode=2,
            stderr=stderr
            if stderr is not None
            else "Can't find Root object at /usr/share/perl5/Image/ExifTool/PDF.pm line 189.\n",
            stdout="",
        )

    def _ok(self):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _qpdf_flags(self, run_mock):
        return [c.args[0][1] for c in run_mock.call_args_list]

    def test_parse_crash_rebuilds_and_retries_once(self):
        # The C++23 STL Cookbook live shape: exiftool cannot read the file
        # (qpdf can), the rebuild preserves the page count, the retry stamps.
        backup = Path(self.tmp.name + "-backups")
        counts = iter(["3", "3"])

        def qpdf(args):
            if "--show-npages" in args:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=next(counts), stderr=""
                )
            return self._ok()

        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf, "_run_exiftool", side_effect=[self._crash(), self._ok()]
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf", side_effect=qpdf) as q,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland"},
            ),
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 0)
        self.assertIn("attempting one qpdf rebuild", out)
        self.assertIn("Can't find Root object", out)
        self.assertIn("3 pages preserved", out)
        self.assertIn("stamped and verified", out)
        self.assertEqual(
            len(self._write_calls(run)), 2, "the failed write plus one retry"
        )
        self.assertEqual(
            self._qpdf_flags(q),
            ["--show-npages", "--replace-input", "--show-npages"],
        )

    def test_parse_crash_retry_still_failing_is_stamp_failed(self):
        # Exactly one recovery: the retried write dies too, so STAMP_FAILED
        # with no second rebuild.
        backup = Path(self.tmp.name + "-backups")
        counts = iter(["3", "3"])

        def qpdf(args):
            if "--show-npages" in args:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=next(counts), stderr=""
                )
            return self._ok()

        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf,
                "_run_exiftool",
                side_effect=[self._crash(), self._crash()],
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf", side_effect=qpdf) as q,
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertEqual(len(self._write_calls(run)), 2, "one retry, not three")
        self.assertEqual(
            self._qpdf_flags(q),
            ["--show-npages", "--replace-input", "--show-npages"],
            "no second rebuild after a failed retry",
        )

    def test_parse_crash_page_count_change_restores_backup_and_fails(self):
        # The rebuild's check gate: a changed page count restores the
        # backup copy and never retries.
        backup = Path(self.tmp.name + "-backups")
        counts = iter(["3", "4"])

        def qpdf(args):
            if "--show-npages" in args:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=next(counts), stderr=""
                )
            self.pdf.write_bytes(b"%PDF-1.4 rebuilt")
            return self._ok()

        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf, "_run_exiftool", side_effect=[self._crash()]
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf", side_effect=qpdf),
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertIn("backup", out)
        self.assertEqual(
            self.pdf.read_bytes(),
            b"%PDF-1.4 fake",
            "backup restored after the page count changed",
        )
        self.assertEqual(len(self._write_calls(run)), 1, "no retry after a bad rebuild")

    def test_parse_crash_without_qpdf_fails_without_recovery(self):
        backup = Path(self.tmp.name + "-backups")
        with (
            mock.patch.object(
                stamp_pdf.shutil,
                "which",
                side_effect=lambda name: None if name == "qpdf" else "/usr/bin/x",
            ),
            mock.patch.object(
                stamp_pdf, "_run_exiftool", side_effect=[self._crash()]
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf") as q,
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        q.assert_not_called()
        self.assertEqual(len(self._write_calls(run)), 1)

    def test_non_parse_exiftool_failure_never_recovers(self):
        # An rc != 0 without a Perl-space error is not the parse-crash
        # class: straight STAMP_FAILED, untouched qpdf.
        backup = Path(self.tmp.name + "-backups")
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf,
                "_run_exiftool",
                side_effect=[self._crash(stderr="Error: Unknown file type\n")],
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf") as q,
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        q.assert_not_called()
        self.assertEqual(len(self._write_calls(run)), 1)

    def test_is_parse_crash_shapes(self):
        # The live shape and the generic Perl die families match; rc 0 and
        # ordinary tool errors never do.
        self.assertTrue(stamp_pdf._is_parse_crash(self._crash()))
        self.assertTrue(
            stamp_pdf._is_parse_crash(
                self._crash(
                    stderr='Can\'t call method "GetSetValue" on unblessed reference'
                    " at /usr/share/perl5/Image/ExifTool/PDF.pm line 700.\n"
                )
            )
        )
        self.assertFalse(
            stamp_pdf._is_parse_crash(
                subprocess.CompletedProcess(
                    args=[], returncode=0, stdout="", stderr=self._crash().stderr
                )
            ),
            "rc 0 is the read-back's job, never a crash",
        )
        self.assertFalse(stamp_pdf._is_parse_crash(self._crash(stderr="died\n")))

    def test_refuses_non_pdf(self):
        epub = self.dir / "book.epub"
        epub.write_bytes(b"EPUB")
        argv = ["stamp_pdf", str(epub), "--title", "X"]
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(buf),
        ):
            rc = stamp_pdf.main()
        self.assertEqual(rc, 2)
        self.assertIn("EPUB", buf.getvalue())

    # --- the bad-xref write-failure class (2026-10-14, issue #21):
    # exiftool rc != 0 naming a damaged xref gets the same one-shot
    # qpdf rebuild + retry as the parse-crash class.

    def _xref_refusal(self):
        return subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stderr="Error: Invalid xref table\n",
            stdout="",
        )

    def test_xref_write_failure_rebuilds_and_retries_once(self):
        # The Language Implementation Patterns live shape: the write
        # refuses over the damaged xref, the rebuild preserves the page
        # count, the retry stamps and verifies.
        backup = Path(self.tmp.name + "-backups")
        counts = iter(["367", "367"])

        def qpdf(args):
            if "--show-npages" in args:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=next(counts), stderr=""
                )
            return self._ok()

        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf,
                "_run_exiftool",
                side_effect=[self._xref_refusal(), self._ok()],
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf", side_effect=qpdf) as q,
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland"},
            ),
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 0)
        self.assertIn("Invalid xref table", out)
        self.assertIn("attempting one qpdf rebuild", out)
        self.assertIn("367 pages preserved", out)
        self.assertIn("stamped and verified", out)
        self.assertEqual(
            len(self._write_calls(run)), 2, "the failed write plus one retry"
        )
        self.assertEqual(
            self._qpdf_flags(q),
            ["--show-npages", "--replace-input", "--show-npages"],
        )

    def test_xref_retry_still_failing_is_stamp_failed(self):
        # Exactly one recovery, shared with the parse-crash class: the
        # retried write dies too, so STAMP_FAILED with no second rebuild.
        backup = Path(self.tmp.name + "-backups")
        counts = iter(["3", "3"])

        def qpdf(args):
            if "--show-npages" in args:
                return subprocess.CompletedProcess(
                    args=[], returncode=0, stdout=next(counts), stderr=""
                )
            return self._ok()

        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_pdf,
                "_run_exiftool",
                side_effect=[self._xref_refusal(), self._xref_refusal()],
            ) as run,
            mock.patch.object(stamp_pdf, "_run_qpdf", side_effect=qpdf) as q,
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertEqual(len(self._write_calls(run)), 2, "one retry, not three")
        self.assertEqual(
            self._qpdf_flags(q),
            ["--show-npages", "--replace-input", "--show-npages"],
            "no second rebuild after a failed retry",
        )

    def test_is_xref_failure_shapes(self):
        # The live refusal shape matches, and the qpdf phrasing counts
        # when it rides stdout; the parse-crash class and plain tool
        # errors are never xref failures.
        self.assertTrue(stamp_pdf._is_xref_failure(self._xref_refusal()))
        self.assertTrue(
            stamp_pdf._is_xref_failure(
                subprocess.CompletedProcess(
                    args=[],
                    returncode=1,
                    stderr="",
                    stdout="Error: xref not found at offset 9737496\n",
                )
            )
        )
        self.assertFalse(
            stamp_pdf._is_xref_failure(self._crash()),
            "the parse-crash class has its own recovery",
        )
        self.assertFalse(
            stamp_pdf._is_xref_failure(self._crash(stderr="Error: Unknown file type\n"))
        )

    # --- the read-back arbitration (2026-10-14, issue #22): a verify
    # mismatch is re-read through exiftool; only fields it also fails to
    # hold are the stubborn-XMP class.

    def _arbitration_readback(self, payload):
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(payload) if not isinstance(payload, str) else payload,
            stderr="",
        )

    def test_arbiter_readback_parses_groups_and_lists(self):
        # -j -a -G1 returns group-prefixed keys; every group's copy of a
        # tag flattens into one list under the bare tag name; list values
        # flatten element-wise; SourceFile is dropped.
        payload = [
            {
                "SourceFile": "/tmp/x.pdf",
                "PDF:Title": "Wonderland",
                "XMP-dc:Title": "Wonderland",
                "PDF:Author": "A One",
                "XMP-dc:Creator": ["A One", "A Two"],
                "XMP-dc:Publisher": "Deep Press",
                "PDF:Keywords": "isbn:9780140445688",
            }
        ]
        with mock.patch.object(
            stamp_pdf, "_run_exiftool", return_value=self._arbitration_readback(payload)
        ) as m:
            readback = stamp_pdf._exiftool_arbitration_readback(Path("/tmp/x.pdf"))
        self.assertEqual(
            readback,
            {
                "title": ["Wonderland", "Wonderland"],
                "author": ["A One"],
                "creator": ["A One", "A Two"],
                "publisher": ["Deep Press"],
                "keywords": ["isbn:9780140445688"],
            },
        )
        argv = m.call_args.args[0]
        self.assertEqual(argv[:4], ["exiftool", "-j", "-a", "-G1"])
        self.assertEqual(argv[-1], "/tmp/x.pdf")
        self.assertTrue(all(a.startswith("-") for a in argv[4:-1]))

    def test_arbiter_readback_failure_returns_empty(self):
        # A dead or unparseable arbiter read returns {}: every disputed
        # field stays failed (fail-closed).
        dead = subprocess.CompletedProcess(
            args=[], returncode=2, stdout="", stderr="Error: Invalid xref table\n"
        )
        with mock.patch.object(stamp_pdf, "_run_exiftool", return_value=dead):
            self.assertEqual(
                stamp_pdf._exiftool_arbitration_readback(Path("x.pdf")), {}
            )
        with mock.patch.object(
            stamp_pdf,
            "_run_exiftool",
            return_value=self._arbitration_readback("<not json>"),
        ):
            self.assertEqual(
                stamp_pdf._exiftool_arbitration_readback(Path("x.pdf")), {}
            )

    def test_arbitration_saves_a_blind_readback(self):
        # The Martin Guerre / Sweet Science disposition: ebook-meta
        # reports the isbn missing while exiftool holds Keywords
        # isbn:... — the stamp landed; verified, with the blind reader
        # on the record.
        backup = Path(self.tmp.name + "-backups")
        ok = self._ok()
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok),
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland", "identifiers": ""},
            ),
            mock.patch.object(
                stamp_pdf,
                "_exiftool_arbitration_readback",
                return_value={
                    "title": ["Wonderland"],
                    "keywords": ["isbn:9780140445688"],
                },
            ) as arb,
        ):
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--isbn",
                "9780140445688",
            )
        self.assertEqual(rc, 0)
        self.assertIn("read back blind on: isbn", out)
        self.assertIn("exiftool holds the stamped values", out)
        self.assertIn("stamped and verified", out)
        arb.assert_called_once()

    def test_arbitration_confirmed_failure_names_both_readers(self):
        # The stubborn-XMP class proper: the arbitration read holds
        # nothing either ({}), so the failure names the field with
        # exiftool's agreement on the record.
        backup = Path(self.tmp.name + "-backups")
        ok = self._ok()
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok),
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Something Else"},
            ),
            mock.patch.object(
                stamp_pdf, "_exiftool_arbitration_readback", return_value={}
            ) as arb,
        ):
            rc, out = self._run_main(
                "--apply", "--backup-dir", str(backup), "--title", "Wonderland"
            )
        self.assertEqual(rc, 1)
        self.assertIn("read-back and exiftool both disagree on: title", out)
        arb.assert_called_once()

    def test_arbitration_partial_confirms_only_the_real_failures(self):
        # Mixed: the title arbitrates away, the isbn is confirmed; the
        # failure names only the confirmed field.
        backup = Path(self.tmp.name + "-backups")
        ok = self._ok()
        with (
            mock.patch.object(stamp_pdf.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(stamp_pdf, "_run_exiftool", return_value=ok),
            mock.patch.object(
                stamp_pdf,
                "_read_ebook_meta",
                return_value={"title": "Wonderland", "identifiers": ""},
            ),
            mock.patch.object(
                stamp_pdf,
                "_exiftool_arbitration_readback",
                return_value={"title": ["Wonderland"]},
            ),
        ):
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(backup),
                "--title",
                "Wonderland",
                "--isbn",
                "9780140445688",
            )
        self.assertEqual(rc, 1)
        self.assertIn("both disagree on: isbn", out)
        self.assertNotIn("both disagree on: isbn, title", out)

    def test_arbitrate_unit_selects_by_probed_tags(self):
        # A field passes on any group's copy (the " & " join included);
        # the expected value comes from the stamp arguments.
        readback = {"title": ["Wonderland"], "creator": ["A One", "A Two"]}
        with mock.patch.object(
            stamp_pdf,
            "_exiftool_arbitration_readback",
            return_value=readback,
        ):
            self.assertEqual(
                stamp_pdf._arbitrate_with_exiftool(
                    Path("x.pdf"), ["title"], "Wonderland", [], "", ""
                ),
                [],
            )
            self.assertEqual(
                stamp_pdf._arbitrate_with_exiftool(
                    Path("x.pdf"), ["author"], "", ["A One", "A Two"], "", ""
                ),
                [],
            )
            self.assertEqual(
                stamp_pdf._arbitrate_with_exiftool(
                    Path("x.pdf"), ["isbn"], "", [], "", "9780140445688"
                ),
                ["isbn"],
            )


class TestStampEpubVerify(unittest.TestCase):
    """The EPUB verify contract (the 2026-09-22 live exercise): title/
    authors/publisher from the ebook-meta read-back, the author compared as
    the display segment before the ` [` bracket; ISBN from the OPF's
    dc:identifier elements under VALUE EQUALITY."""

    def _verify(self, readback, candidates, **kw):
        return stamp_epub._verify(
            readback,
            candidates,
            kw.get("title", ""),
            kw.get("authors", []),
            kw.get("publisher", ""),
            kw.get("isbn", ""),
        )

    def test_author_display_segment_ignores_sort_bracket(self):
        # ebook-meta renders `Display [Sort, Form]` whenever a sort exists;
        # only the display segment is the stamp comparison target.
        failed = self._verify(
            {"title": "T", "author(s)": "Caitlín R. Kiernan [Kiernan, Caitlín R.]"},
            [],
            title="T",
            authors=["Caitlín R. Kiernan"],
        )
        self.assertEqual(failed, [])

    def test_wrong_display_author_still_fails(self):
        failed = self._verify(
            {"title": "T", "author(s)": "Someone Else [Else, Someone]"},
            [],
            title="T",
            authors=["Caitlín R. Kiernan"],
        )
        self.assertEqual(failed, ["author"])

    def test_isbn_value_equality_over_every_live_shape(self):
        # Bare isbn: value, scheme-attributed bare value, dashed value, and
        # the ISBN carried in the element id: all verify against one stamp.
        want = "9780786970063"
        for cand in (
            "isbn:9780786970063",
            "9780786970063",
            "978-0-7869-7006-3",
            "isbn_9780786970063",
            "urn:isbn:978-0-7869-7006-3",
        ):
            failed = self._verify({"title": "T"}, [cand], title="T", isbn=want)
            self.assertEqual(failed, [], cand)

    def test_wrong_isbn_fails(self):
        # The 2026-09-22 masked-duplicate class: a wrong number the writer
        # failed to replace must not read as the stamp.
        failed = self._verify(
            {"title": "T"},
            ["9781101218631"],
            title="T",
            isbn="9780786970063",
        )
        self.assertEqual(failed, ["isbn"])

    def test_no_isbn_requested_skips_the_check(self):
        failed = self._verify({"title": "T"}, [], title="T")
        self.assertEqual(failed, [])

    def test_missing_readback_fields_fail(self):
        failed = self._verify({}, [], title="T", authors=["A"], publisher="P")
        self.assertEqual(failed, ["title", "author", "publisher"])


class TestStampEpubNormalize(unittest.TestCase):
    def test_shapes_collapse_onto_the_bare_number(self):
        for raw, want in (
            ("isbn:9780786970063", "9780786970063"),
            ("ISBN:9780786970063", "9780786970063"),
            ("urn:isbn:9780786970063", "9780786970063"),
            ("isbn_9780786970063", "9780786970063"),
            ("978-0-7869-7006-3", "9780786970063"),
            ("9780786970063", "9780786970063"),
            ("0-8044-2957-X", "080442957X"),
        ):
            self.assertEqual(stamp_epub._normalize_identifier(raw), want, raw)

    def test_dashed_stamp_equals_prefixed_candidate(self):
        self.assertEqual(
            stamp_epub._normalize_identifier("978-0-7869-7006-3"),
            stamp_epub._normalize_identifier("ISBN: 9780786970063"),
        )


class TestStampEpubOpfRead(unittest.TestCase):
    """_opf_identifier_candidates reads the real zip: the value AND the id
    attribute of every dc:identifier, via the container's rootfile path."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _candidates(self, ident_el, **kw):
        p = self.dir / "b.epub"
        _make_epub(p, identifiers=[ident_el], **kw)
        return stamp_epub._opf_identifier_candidates(p)

    def test_value_and_id_attribute_are_both_candidates(self):
        cands = self._candidates(
            '<dc:identifier id="isbn_9780786970063">978-0-7869-7006-3</dc:identifier>'
        )
        self.assertIn("978-0-7869-7006-3", cands)
        self.assertIn("isbn_9780786970063", cands)

    def test_namespaced_scheme_shape_is_read(self):
        cands = self._candidates(
            '<dc:identifier xmlns:ns2="http://www.idpf.org/2007/opf" '
            'ns2:scheme="ISBN">9780786970063</dc:identifier>'
        )
        self.assertEqual(cands, ["9780786970063"])

    def test_rootfile_path_is_honored_not_the_filename(self):
        # Real EPUBs commonly nest the OPF under OEBPS/; the container.xml
        # rootfile entry is the only authority.
        p = self.dir / "b.epub"
        _make_epub(p, identifiers=(), opf_name="OEBPS/content.opf")
        zip_names = zipfile.ZipFile(p).namelist()
        self.assertIn("OEBPS/content.opf", zip_names)
        self.assertEqual(stamp_epub._opf_identifier_candidates(p), [])


class TestStampEpubMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.epub = self.dir / "5E - Wonderland.epub"
        _make_epub(
            self.epub,
            identifiers=[
                '<dc:identifier id="badid" opf:scheme="ISBN">9781101218631'
                "</dc:identifier>"
            ],
        )
        self.backup = Path(self.tmp.name + "-backups")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_main(self, *extra, files=None):
        argv = ["stamp_epub", *(str(f) for f in (files or [self.epub])), *extra]
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(buf),
        ):
            return stamp_epub.main(), buf.getvalue()

    def test_invalid_isbn_is_refused(self):
        # The tool-boundary checksum guard, same shape as stamp_pdf 3.52.0.
        rc, out = self._run_main("--isbn", "9780786967006", "--title", "X")
        self.assertEqual(rc, 2)
        self.assertIn("check digit", out)

    def test_invalid_isbn_is_refused_on_apply_too(self):
        rc, out = self._run_main(
            "--isbn",
            "9780786967006",
            "--title",
            "X",
            "--apply",
            "--backup-dir",
            str(self.backup),
        )
        self.assertEqual(rc, 2)
        self.assertIn("check digit", out)

    def test_no_fields_is_a_usage_error(self):
        rc, out = self._run_main()
        self.assertEqual(rc, 2)
        self.assertIn("nothing to stamp", out)

    def test_missing_file_refused(self):
        rc, out = self._run_main("--title", "X", files=[self.dir / "gone.epub"])
        self.assertEqual(rc, 2)
        self.assertIn("no such file", out)

    def test_refuses_non_epub(self):
        pdf = self.dir / "book.pdf"
        pdf.write_bytes(b"%PDF-1.4 fake")
        rc, out = self._run_main("--title", "X", files=[pdf])
        self.assertEqual(rc, 2)
        self.assertIn("not an EPUB", out)

    def test_duplicate_target_refused(self):
        # Bijection: one file, one stamp; a repeated target would
        # double-stamp and lie about the batch.
        rc, out = self._run_main("--title", "X", files=[self.epub, self.epub])
        self.assertEqual(rc, 2)
        self.assertIn("twice", out)

    def test_dry_run_default_never_invokes_ebook_meta(self):
        with mock.patch.object(stamp_epub, "_run_ebook_meta") as run:
            rc, out = self._run_main("--title", "Wonderland")
        self.assertEqual(rc, 0)
        run.assert_not_called()
        self.assertIn("dry run", out)
        self.assertIn("'5E'", out)  # the filename-derivation preview

    def test_dry_run_works_without_ebook_meta_on_path(self):
        with mock.patch.object(stamp_epub.shutil, "which", return_value=None):
            rc, _ = self._run_main("--title", "Wonderland")
        self.assertEqual(rc, 0)

    def test_apply_requires_backup_dir(self):
        rc, out = self._run_main("--apply", "--title", "Wonderland")
        self.assertEqual(rc, 2)
        self.assertIn("backup-dir", out)

    def test_backup_dir_inside_target_dir_is_refused(self):
        rc, out = self._run_main(
            "--apply", "--title", "W", "--backup-dir", str(self.dir / "sub")
        )
        self.assertEqual(rc, 2)
        self.assertIn("OUTSIDE", out)

    def test_args_carry_only_requested_fields(self):
        # The fixed field set, joined " & " (ebook-meta's separator); a
        # field the caller leaves off is not passed at all.
        self.assertEqual(
            stamp_epub._build_ebook_meta_args("T", ["A One", "A Two"], "", ""),
            ["ebook-meta", "--title", "T", "--authors", "A One & A Two"],
        )
        self.assertEqual(
            stamp_epub._build_ebook_meta_args("T", [], "P", "9780786970063"),
            [
                "ebook-meta",
                "--title",
                "T",
                "--publisher",
                "P",
                "--isbn",
                "9780786970063",
            ],
        )

    def _apply_mocks(self, write_rc=0, readback=None):
        return (
            mock.patch.object(stamp_epub.shutil, "which", return_value="/usr/bin/x"),
            mock.patch.object(
                stamp_epub,
                "_run_ebook_meta",
                return_value=subprocess.CompletedProcess(
                    args=[], returncode=write_rc, stdout="", stderr="boom"
                ),
            ),
            mock.patch.object(
                stamp_epub, "_read_ebook_meta", return_value=readback or {}
            ),
        )

    def test_apply_stamps_and_verifies(self):
        # The fixture already carries the stamp ISBN in its OPF, so the
        # value-equality verify passes over the real zip without a write.
        _make_epub(
            self.epub,
            title="Real Title",
            author="Real Author",
            identifiers=[
                '<dc:identifier xmlns:ns2="http://www.idpf.org/2007/opf" '
                'ns2:scheme="ISBN">9780786970063</dc:identifier>'
            ],
        )
        which, run, read = self._apply_mocks(
            readback={
                "title": "Real Title",
                "author(s)": "Real Author [Real, Author]",
                "publisher": "Real Press",
            }
        )
        with which, run as run_mock, read:
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(self.backup),
                "--title",
                "Real Title",
                "--author",
                "Real Author",
                "--publisher",
                "Real Press",
                "--isbn",
                "9780786970063",
            )
        self.assertEqual(rc, 0)
        self.assertIn("stamped and verified", out)
        self.assertTrue(
            (self.backup / self.epub.name).exists(),
            "backup copy made before writing",
        )
        called = run_mock.call_args.args[0]
        self.assertEqual(called[0], "ebook-meta")
        self.assertEqual(called[-1], str(self.epub))
        self.assertIn("--authors", called)

    def test_write_failure_stops_the_list(self):
        second = self.dir / "second.epub"
        _make_epub(second)
        which, run, _ = self._apply_mocks(write_rc=1)
        with which, run as run_mock:
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(self.backup),
                "--title",
                "W",
                files=[self.epub, second],
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertEqual(run_mock.call_count, 1, "no re-fighting, no second write")
        self.assertIn("left unstamped", out)
        self.assertIn("second.epub", out)

    def test_verify_failure_stops_the_list(self):
        second = self.dir / "second.epub"
        _make_epub(second)
        which, run, read = self._apply_mocks(
            readback={"title": "Something Else"},
        )
        with which, run as run_mock, read:
            rc, out = self._run_main(
                "--apply",
                "--backup-dir",
                str(self.backup),
                "--title",
                "Wonderland",
                files=[self.epub, second],
            )
        self.assertEqual(rc, 1)
        self.assertIn("STAMP_FAILED", out)
        self.assertIn("title", out)
        self.assertEqual(run_mock.call_count, 1, "no re-fighting, no second write")
        self.assertIn("left unstamped", out)
        self.assertIn("second.epub", out)

    def test_json_report_records_every_target_once(self):
        # The bijection record: every input file appears exactly once with a
        # status, including the remainder a stop leaves unstamped.
        second = self.dir / "second.epub"
        _make_epub(second)
        json_path = self.dir / "report.json"
        which, run, read = self._apply_mocks(
            readback={"title": "Something Else"},
        )
        with which, run, read:
            rc, _ = self._run_main(
                "--apply",
                "--backup-dir",
                str(self.backup),
                "--title",
                "Wonderland",
                "--json",
                str(json_path),
                files=[self.epub, second],
            )
        self.assertEqual(rc, 1)
        report = json.loads(json_path.read_text(encoding="utf-8"))
        statuses = {r["file"]: r["status"] for r in report["files"]}
        self.assertEqual(len(report["files"]), 2)
        self.assertEqual(statuses[str(self.epub)], "stamp_failed")
        self.assertEqual(statuses[str(second)], "left_unstamped")


@unittest.skipUnless(shutil.which("ebook-meta"), "needs Calibre's ebook-meta on PATH")
class TestStampEpubLive(unittest.TestCase):
    """The real-writer contract (verified live 2026-09-22): ebook-meta
    REPLACES a wrong isbn identifier, keeps a matching producer's spelling,
    and the OPF read verifies every shape under value equality."""

    RIGHT = "9780786970063"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.backup = Path(self.tmp.name + "-backups")

    def tearDown(self):
        self.tmp.cleanup()

    def _stamp(self, name, identifiers, **kw):
        f = self.dir / name
        _make_epub(f, identifiers=identifiers, **kw)
        argv = [
            "stamp_epub",
            str(f),
            "--title",
            "Real Title",
            "--author",
            "Real Author",
            "--isbn",
            self.RIGHT,
            "--apply",
            "--backup-dir",
            str(self.backup),
        ]
        buf = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(buf),
        ):
            rc = stamp_epub.main()
        return rc, f

    def _identifiers(self, f):
        return stamp_epub._opf_identifier_candidates(f)

    def test_wrong_isbn_is_replaced_and_verifies(self):
        # The correction mechanism: a wrong ISBN-scheme identifier goes
        # away, the stamp's number takes its place, verify passes.
        rc, f = self._stamp(
            "wrong.epub",
            [
                '<dc:identifier id="badid" opf:scheme="ISBN">9781101218631'
                "</dc:identifier>"
            ],
        )
        self.assertEqual(rc, 0)
        wrong = stamp_epub._normalize_identifier("9781101218631")
        survivors = [
            c
            for c in self._identifiers(f)
            if stamp_epub._normalize_identifier(c) == wrong
        ]
        self.assertEqual(survivors, [], "the wrong ISBN must be replaced")
        self.assertIn(
            self.RIGHT,
            [stamp_epub._normalize_identifier(c) for c in self._identifiers(f)],
        )

    def test_already_right_isbn_no_op_verifies_in_each_shape(self):
        # The honest no-op: the writer keeps the producer's spelling (it
        # never destroys a matching identifier; it may add its own
        # canonical one alongside), and value equality verifies anyway.
        shapes = {
            "s1_bare_value": ["<dc:identifier>isbn:" + self.RIGHT + "</dc:identifier>"],
            "s2_opfscheme": [
                '<dc:identifier id="isbn" opf:scheme="ISBN">'
                + self.RIGHT
                + "</dc:identifier>"
            ],
            "s3_ns2scheme": [
                '<dc:identifier xmlns:ns2="http://www.idpf.org/2007/opf" '
                'ns2:scheme="ISBN">' + self.RIGHT + "</dc:identifier>"
            ],
            "s4_id_isbn": [
                '<dc:identifier id="isbn_' + self.RIGHT + '">978-0-7869-7006-3'
                "</dc:identifier>"
            ],
        }
        for name, idents in shapes.items():
            with self.subTest(shape=name):
                rc, f = self._stamp(f"{name}.epub", idents)
                self.assertEqual(rc, 0, name)
                opf_text = self._opf_text(f)
                self.assertIn(
                    stamp_epub._normalize_identifier(self.RIGHT),
                    [stamp_epub._normalize_identifier(c) for c in self._identifiers(f)],
                    name,
                )
                if name == "s1_bare_value":
                    self.assertIn("isbn:" + self.RIGHT, opf_text, name)
                elif name == "s4_id_isbn":
                    # The producer's distinctive spelling survives the write.
                    self.assertIn("isbn_" + self.RIGHT, opf_text, name)
                    self.assertIn("978-0-7869-7006-3", opf_text, name)
                else:
                    self.assertIn('scheme="ISBN"', opf_text, name)

    def test_clean_epub_gains_the_isbn(self):
        rc, f = self._stamp("clean.epub", [])
        self.assertEqual(rc, 0)
        self.assertIn(
            stamp_epub._normalize_identifier(self.RIGHT),
            [stamp_epub._normalize_identifier(c) for c in self._identifiers(f)],
        )

    def _opf_text(self, f):
        z = zipfile.ZipFile(f)
        opf_name = next(n for n in z.namelist() if n.endswith(".opf"))
        return z.read(opf_name).decode("utf-8")


class TestCommentsCensus(unittest.TestCase):
    """Phase 17 box 4: the description mechanical sweep as a standing
    tool. The sweep itself is pure text logic; the DB path is exercised
    against a throwaway fixture."""

    @classmethod
    def setUpClass(cls):
        cls.census = _load("comments_census")

    def test_clean_description_has_no_findings(self):
        text = (
            "<p>A desert planet. The well-known co-op of spice traders "
            "moves carefully.</p>"
        )
        self.assertEqual(self.census.sweep_text(text), [])

    def test_each_mechanical_defect_is_caught(self):
        cases = {
            "double_hyphen": "<p>He said -- quietly.</p>",
            "spaced_hyphen_dash": "<p>the dark -and stormy- night</p>",
            "markdown_bold": "<p>**New York Times** bestseller</p>",
            "tag_debris": "<p>Line one.<br>Also <div>nested</div></p>",
            "body_shape": "A description that lost its markup.",
            "soft_hyphen": "<p>ena\u00adblement</p>",
            "zero_width": "<p>zero\u200bwidth</p>",
            "mojibake": "<p>cafÃ© society</p>",
            "lost_ligature": "<p>the ?nal chapter</p>",
        }
        for kind, text in cases.items():
            with self.subTest(kind=kind):
                kinds = [f["kind"] for f in self.census.sweep_text(text)]
                self.assertIn(kind, kinds)

    def test_legitimate_hyphens_and_questions_pass(self):
        text = "<p>A well-known co-op; what could go wrong? Nothing.</p>"
        kinds = [f["kind"] for f in self.census.sweep_text(text)]
        self.assertEqual(kinds, [])

    def test_accented_text_is_not_mojibake(self):
        text = "<p>Café society, über alles, naïve-belle.</p>"
        kinds = [f["kind"] for f in self.census.sweep_text(text)]
        self.assertNotIn("mojibake", kinds)

    def test_find_duplicates_groups_exact_matches_only(self):
        groups = self.census.find_duplicates(
            {7: "<p>Same body.</p>", 9: "<p>Same body.</p>", 12: "<p>Other.</p>"}
        )
        self.assertEqual(groups, [[7, 9]])
        self.assertEqual(self.census.find_duplicates({7: "<p>One.</p>"}), [])

    def test_load_bodies_reads_ro_fixture(self):
        fd, db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.remove, db_path)
        con = sqlite3.connect(db_path)
        con.executescript(
            "CREATE TABLE comments (book INTEGER, text TEXT);"
            "INSERT INTO comments VALUES (1, '<p>One.</p>'), (2, ''), "
            "(3, '<p>Three.</p>');"
        )
        con.commit()
        con.close()
        bodies = self.census.load_bodies(db_path, None)
        self.assertEqual(bodies, {1: "<p>One.</p>", 3: "<p>Three.</p>"})
        scoped = self.census.load_bodies(db_path, [3])
        self.assertEqual(scoped, {3: "<p>Three.</p>"})


audit_isbns = _load("audit_isbns")


class TestCheckPdf(unittest.TestCase):
    """Phase 17 box 2's battery: classification is pure; the tool
    invocations are mocked so the suite never needs qpdf/poppler."""

    @classmethod
    def setUpClass(cls):
        cls.check_pdf = _load("check_pdf")

    def test_qpdf_classes(self):
        # qpdf's documented exit contract (qpdf --help=exit-status): 0
        # clean, 3 warnings (benign), 2 errors. The class reads the exit
        # code alone; the 2026-09-10 Redwall run caught the old marker
        # gate (exit 3 downgraded to errors whenever the marker was not
        # in stdout) misfiling warning-only files as structural damage.
        classify = self.check_pdf.classify_qpdf
        self.assertEqual(classify(0), "clean")
        self.assertEqual(classify(3), "warnings")
        self.assertEqual(classify(2), "errors")
        self.assertEqual(classify(None), "unavailable")
        self.assertEqual(classify(9), "errors")

    def test_warning_only_file_is_not_structural_damage(self):
        # The Redwall regression: qpdf writes its warnings (and the
        # "operation succeeded with warnings" summary) to stderr, so the
        # old stdout-only marker check never matched. A warning-only scan
        # must classify as warnings, carry the warning line as the
        # finding's detail, and never land in the structural count.
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)

        def fake_run(cmd, timeout=120, **kwargs):
            if cmd[:2] == ["qpdf", "--check"]:
                return mock.Mock(
                    returncode=3,
                    stdout="",
                    stderr="WARNING: unknown token while reading object; "
                    "treating as string\noperation succeeded with warnings",
                )
            return mock.Mock(returncode=0, stdout="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            report = self.check_pdf.check_file(path)
        self.assertEqual(report["qpdf_check"], "warnings")
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("qpdf_warnings", kinds)
        self.assertNotIn("qpdf_errors", kinds)
        warning = next(f for f in report["findings"] if f["kind"] == "qpdf_warnings")
        self.assertIn("unknown token", warning["detail"])

    def test_warning_exit_3_with_silent_stderr_records_benign(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)

        def fake_run(cmd, timeout=120, **kwargs):
            if cmd[:2] == ["qpdf", "--check"]:
                return mock.Mock(returncode=3, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            report = self.check_pdf.check_file(path)
        self.assertEqual(report["qpdf_check"], "warnings")
        warning = report["findings"][0]
        self.assertEqual(warning["kind"], "qpdf_warnings")
        self.assertEqual(warning["detail"], "benign")

    def test_unknown_header_reported_without_subprocess(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"not a pdf at all")
        os.close(fd)
        self.addCleanup(os.remove, path)
        report = self.check_pdf.check_file(path)
        self.assertEqual(report["kind"], "unknown")
        self.assertEqual(report["findings"][0]["kind"], "header")

    def test_djvu_uses_djvused_page_count(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".djvu")
        os.write(fd, b"AT&TFORM\x00\x00\x00\x00DJVM")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with mock.patch(
            "subprocess.run",
            return_value=mock.Mock(returncode=0, stdout="42\n"),
        ):
            report = self.check_pdf.check_file(path)
        self.assertEqual(report["kind"], "djvu")
        self.assertEqual(report["pages"], 42)

    def test_pdf_battery_collects_findings(self):
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)

        def fake_run(cmd, timeout=120, **kwargs):
            name = cmd[0]
            if name == "qpdf" and cmd[1] == "--check":
                return mock.Mock(returncode=2, stdout="", stderr="invalid xref")
            if name == "qpdf":
                return mock.Mock(returncode=0, stdout="7\n")
            if name == "pdffonts":
                return mock.Mock(
                    returncode=0,
                    stdout="name type encoding emb sub uni object ID\n"
                    "----------------- ------- --\n"
                    "ABC-Font TrueType Special no no yes 8 0\n"
                    "DEF-Font Type1C Special yes yes yes 9 0\n",
                )
            if name == "pdftotext":
                return mock.Mock(returncode=0, stdout="some text\n")
            if name == "pdfimages":
                return mock.Mock(
                    returncode=0,
                    stdout="page num  type   width height color comp bpc "
                    "enc interp object ID x-ppi y-ppi size ratio\n"
                    "-----------------------------------------------"
                    "--------------------------\n"
                    "   1   0 image   600   800  rgb     3   8  image  no"
                    "     187   0    72    72  2457600 jpeg\n",
                )
            return mock.Mock(returncode=0, stdout="")

        with mock.patch("subprocess.run", side_effect=fake_run):
            report = self.check_pdf.check_file(path)
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("qpdf_errors", kinds)
        self.assertIn("unembedded_fonts", kinds)
        self.assertEqual(report["pages"], 7)
        self.assertEqual(report["unembedded_fonts"], 1)
        # B.7: text_layer is now per sampled page (first/middle/last);
        # the run.py seam reads only paths and the exit contract, so the
        # shape change stops here by design.
        self.assertEqual(
            report["text_layer"],
            {"first": "present", "middle": "present", "last": "present"},
        )
        self.assertEqual(report["image_count"], 1)
        self.assertEqual(report["avg_dpi"], 72.0)
        self.assertIn("low_dpi", kinds)

    def test_qpdf_check_timeout_degrades_to_advisory_finding(self):
        # Issue #8: a slow or hung qpdf --check (a 733-page file under
        # battery contention fired this live) was swallowed as a bare
        # None and reported "unavailable", hiding the cause, while the
        # crash that actually killed the battery (rc 1, JSON report
        # unwritten, phase-1 verb dead on the empty report) was _run's
        # strict decode of non-UTF-8 tool output. A timeout now degrades
        # to a per-file verdict + finding, advisory: not in the
        # structural tuple, so the exit contract is unchanged.
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)

        def fake_run(cmd, timeout=120, **kwargs):
            if cmd[:2] == ["qpdf", "--check"]:
                raise subprocess.TimeoutExpired(cmd, timeout)
            return mock.Mock(returncode=0, stdout="7\n")

        with mock.patch("subprocess.run", side_effect=fake_run):
            report = self.check_pdf.check_file(path)
        self.assertEqual(report["qpdf_check"], "timeout")
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("qpdf_timeout", kinds)
        self.assertNotIn("qpdf_timeout", self.check_pdf._STRUCTURAL_KINDS)

    def test_every_tool_timeout_degrades_never_raises(self):
        # The whole battery timing out (contention worst case) leaves a
        # coherent report: the qpdf verdict names the timeout, the other
        # seams read the _TimedOut sentinel as unavailable, and
        # check_file returns instead of raising.
        import tempfile

        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.write(fd, b"%PDF-1.4 fake")
        os.close(fd)
        self.addCleanup(os.remove, path)

        with mock.patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(["qpdf"], 120),
        ):
            report = self.check_pdf.check_file(path)
        self.assertEqual(report["qpdf_check"], "timeout")
        self.assertIsNone(report["pages"])
        self.assertNotIn("unembedded_fonts", report)
        # pages is None, so the sampler falls back to one sample at
        # page 1, labeled "last" (the label dict's keys collapse).
        self.assertEqual(report["text_layer"], {"last": "unavailable"})
        self.assertEqual([f["kind"] for f in report["findings"]], ["qpdf_timeout"])

    def test_run_decodes_non_utf8_tool_output(self):
        # Issue #8's second crash mode: _run used text=True without
        # errors='replace', so qpdf echoing a PDF's raw non-UTF-8 bytes
        # into its warning context raised UnicodeDecodeError in
        # communicate() (a ValueError, outside the except clause). The
        # decode is lossy-replace now; this drives the REAL subprocess
        # path, which the mocked classes above never touch.
        proc = self.check_pdf._run(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x \\xa0\\xff y')",
            ]
        )
        self.assertIsNotNone(proc)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("\ufffd", proc.stdout)


class TestAuthorSortSanity(unittest.TestCase):
    """Phase 19 B.3: advisory author_sort sweep, hosted locally in the
    script by decision (the cquarry-predicate promotion is a future
    option, never assumed to exist)."""

    def _run(self, rows):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript("""
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author_sort TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
            CREATE TABLE books_authors_link (book INT, author INT);
        """)
        for bid, title, sort, authors in rows:
            cur.execute("INSERT INTO books VALUES (?,?,?)", (bid, title, sort))
            for aid, name in enumerate(authors, start=bid * 10):
                # authors rows carry (name, sort); a bare string means no
                # sort-column value.
                aname = name[0] if isinstance(name, tuple) else name
                asort = name[1] if isinstance(name, tuple) else None
                cur.execute("INSERT INTO authors VALUES (?,?,?)", (aid, aname, asort))
                cur.execute("INSERT INTO books_authors_link VALUES (?,?)", (bid, aid))
        report = validate_metadata.Reporter()
        validate_metadata.check_author_sort_sanity(cur, report)
        return report

    def test_never_inverted_multiword_sort_is_flagged(self):
        report = self._run([(1, "Dune", "Frank Herbert", ["Frank Herbert"])])
        codes = [c for c, _ in report.warnings]
        self.assertIn("AUTHOR_SORT_NOT_INVERTED", codes)

    def test_orphan_sort_is_flagged(self):
        report = self._run([(1, "Dune", "Smersh, Beastly", ["Herbert, Frank"])])
        codes = [c for c, _ in report.warnings]
        self.assertIn("AUTHOR_SORT_ORPHAN", codes)

    def test_real_library_shape_display_name_plus_sort_column(self):
        # The real-library regression: authors.name holds the DISPLAY name
        # and authors.sort the inverted one; a books.author_sort equal to
        # that sort is the healthy case and must flag nothing. The first
        # cut compared against display names only and flagged 7718 of
        # 7842 real books as orphans.
        report = self._run(
            [
                (1, "Dune", "Herbert, Frank", [("Frank Herbert", "Herbert, Frank")]),
                (
                    2,
                    "Joint Book",
                    "Herbert, Frank & Gibson, William",
                    [
                        ("Frank Herbert", "Herbert, Frank"),
                        ("William Gibson", "Gibson, William"),
                    ],
                ),
            ]
        )
        self.assertEqual(report.warnings, [])

    def test_proper_inversion_and_single_names_stay_silent(self):
        report = self._run(
            [
                (1, "Dune", "Herbert, Frank", ["Herbert, Frank"]),
                # Anonymous and single-word names: legitimate as-is.
                (2, "Meditations", "Anonymous", ["Anonymous"]),
                (3, "De Re Coquinaria", "Cicero", ["Cicero"]),
            ]
        )
        self.assertEqual(report.warnings, [])

    def test_empty_sort_stays_silent(self):
        report = self._run([(1, "Untitled", "", [])])
        self.assertEqual(report.warnings, [])


class TestIsbnChecksum(unittest.TestCase):
    """Phase 19 B.4: DB-level ISBN check-digit validation via cquarry's
    helper (the integer-constant case needs no file open)."""

    def _run(self, isbns):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript("CREATE TABLE identifiers (book INT, type TEXT, val TEXT)")
        for i, val in enumerate(isbns, start=1):
            cur.execute("INSERT INTO identifiers VALUES (?, 'isbn', ?)", (i, val))
        report = validate_metadata.Reporter()
        validate_metadata.check_isbn_checksum(cur, report)
        return report

    def test_bad_check_digit_is_flagged(self):
        report = self._run(["9780441172718"])  # one digit off the Dune ISBN
        codes = [c for c, _ in report.warnings]
        self.assertEqual(codes, ["INVALID_ISBN"])
        self.assertIn("9780441172718", report.warnings[0][1])

    def test_valid_isbn_stays_silent(self):
        report = self._run(["978-0441172719", "0441172717"])
        self.assertEqual(report.warnings, [])

    def test_empty_and_missing_values_ignored(self):
        report = self._run(["", "   "])
        self.assertEqual(report.warnings, [])


check_pdf = _load("check_pdf")


class TestCheckPdfDepth(unittest.TestCase):
    """Phase 19 B.7: text sampled at 1/middle/last, image DPI parsed from
    the existing pdfimages -list output (area-weighted)."""

    def test_sample_pages_covers_first_middle_last(self):
        self.assertEqual(check_pdf.sample_pages(1), [1])
        self.assertEqual(check_pdf.sample_pages(2), [1, 2])
        self.assertEqual(check_pdf.sample_pages(10), [1, 5, 10])
        self.assertEqual(check_pdf.sample_pages(None), [1])

    def test_partial_text_layer_is_its_own_finding(self):
        # The page-1-only OCR pass: text on page 1, none mid/last. The old
        # single-page sample called this file clean.
        outputs = {
            1: ("chapter one", 0),
            3: ("", 0),
            5: ("", 0),
        }

        def fake_run(cmd):
            page = int(cmd[cmd.index("-f") + 1])
            text, rc = outputs[page]
            return subprocess.CompletedProcess(cmd, rc, text, "")

        with mock.patch.object(check_pdf, "_run", side_effect=fake_run):
            samples = check_pdf._text_layer_samples("f.pdf", 5)
        self.assertEqual(
            samples, {"first": "present", "middle": "absent", "last": "absent"}
        )
        report = {"findings": []}
        present = [v for v in samples.values() if v == "present"]
        absent = [v for v in samples.values() if v == "absent"]
        if absent and present:
            report["findings"].append({"kind": "text_layer_partial"})
        self.assertEqual(report["findings"][0]["kind"], "text_layer_partial")

    def test_pdfimages_parse_weights_by_area(self):
        # A small sharp logo must not hide a full-page low-dpi scan.
        header = (
            "page num  type   width height color comp bpc enc interp "
            "object ID x-ppi y-ppi size ratio\n"
            "----------------------------------------------------------------"
            "---------------\n"
        )
        big_low = "   1   0 image   600   800  rgb     3   8  image  no     187   0    72    72  2457600 jpeg\n"
        small_sharp = "   1   1 image    50    50  rgb     3   8  image  no     201   0   600   600    7500 image\n"
        stats = check_pdf._parse_pdfimages_list(header + big_low + small_sharp)
        self.assertEqual(stats["count"], 2)
        # (600*800*72 + 50*50*600) / (600*800 + 50*50) ~= 74.9
        self.assertAlmostEqual(stats["weighted_ppi"], 74.9, delta=0.5)

    def test_pdfimages_parse_handles_empty_and_junk(self):
        self.assertEqual(
            check_pdf._parse_pdfimages_list("no data here"),
            {"count": 0, "weighted_ppi": None},
        )


class TestIsbnCopyrightYear(unittest.TestCase):
    """Phase 19 B.8: (c)-years captured in the front-matter pass; a gap
    over the tolerance against the pubdate year is VARIANT-class
    advisory (a reprint legitimately prints the original year)."""

    def test_year_shapes(self):
        years = audit_isbns._copyright_years(
            "Copyright (c) 1985, 2001 by Acme Books\n"
            "Printed in the United States of America. 2019 is a print year "
            "with no (c) marker."
        )
        self.assertEqual(years, [1985, 2001])

    def test_bare_years_are_not_captured(self):
        # Dates and phone fragments without copyright furniture stay out.
        self.assertEqual(audit_isbns._copyright_years("met in 2019 and 2020"), [])

    def test_gap_tolerance(self):
        # Two years off: a normal paperback-original lag, not a finding.
        self.assertFalse(audit_isbns._year_disagrees([2019], 2021))
        # Three years off: advisory.
        self.assertTrue(audit_isbns._year_disagrees([1985], 2020))
        self.assertFalse(audit_isbns._year_disagrees([], 2020))
        self.assertFalse(audit_isbns._year_disagrees([1985], None))


audit_drm = _load("audit_drm")


class TestDrmCsvCarriesNaRows(unittest.TestCase):
    """The DJVU box (2026-09-17): the directory scan used to skip N/A
    files before the CSV append, so the runner recorded `unscanned` for a
    format that WAS judged. N/A rows reach the CSV; they stay out of the
    counters and the summary."""

    def test_na_files_reach_the_csv(self):
        tmp = tempfile.mkdtemp(prefix="cquarry_drm_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        djvu = os.path.join(tmp, "scan_book.djvu")
        with open(djvu, "wb") as f:
            f.write(b"ATTACK1DjVu")
        csv_path = os.path.join(tmp, "drm.csv")
        rc = audit_drm.run_directory(pathlib.Path(tmp), pathlib.Path(csv_path))
        self.assertEqual(rc, 0)
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["path"] for r in rows], [djvu])
        self.assertEqual(rows[0]["status"], "N/A")
        self.assertEqual(rows[0]["kind"], "djvu")


class TestCodeClassificationGuards(unittest.TestCase):
    """Issue #10: LoC's LCDB answers mix real classifications with placeholder
    050s (ebook-preview control numbers, CPB box numbers, ACQUISITION IN
    PROCESS) and non-numeric Dewey values ([Fic]). All of them read as a
    miss now: never written, worklisted like any other gap."""

    def test_valid_lcc_shapes_pass(self):
        for value in (
            "BF637.S4 G63 2007",
            "B563",
            "QA 76.73 .P98",
            "PR9199.3 .M55 1998",
            "PZ 7",
        ):
            self.assertTrue(fetch_library_codes.valid_lcc(value), value)

    def test_placeholder_050_values_fail(self):
        for value in (
            "MLCM 2024/43047 (B)",
            "MLCS 2023/01234",
            "CPB Box no. 305 vol. 19",
            "ACQUISITION IN PROCESS",
            "Ephemera collection [item]",
            "",
            None,
        ):
            self.assertFalse(fetch_library_codes.valid_lcc(value), repr(value))

    def test_ddc_guard_takes_numbers_only(self):
        self.assertTrue(fetch_library_codes.valid_ddc("813.54"))
        self.assertTrue(fetch_library_codes.valid_ddc("823/.914"))
        self.assertTrue(fetch_library_codes.valid_ddc("001"))
        for value in ("[Fic]", "Fic", "Ephemera collection [item]", "", None):
            self.assertFalse(fetch_library_codes.valid_ddc(value), repr(value))

    def test_sanitize_codes_drops_only_the_guards_keys(self):
        f = fetch_library_codes
        self.assertEqual(
            f.sanitize_codes({"lcc": "MLCM 2024/43047 (B)", "ddc": "188"}),
            {"ddc": "188"},
        )
        self.assertEqual(
            f.sanitize_codes({"lcc": "B563", "ddc": "[Fic]"}), {"lcc": "B563"}
        )
        self.assertEqual(f.sanitize_codes({}), {})

    @staticmethod
    def _mods_response(*record_bodies: str) -> bytes:
        records = "".join(
            "<record><recordData>"
            '<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">'
            + body
            + "</mods:mods></recordData></record>"
            for body in record_bodies
        )
        return (
            '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f"<records>{records}</records></searchRetrieveResponse>"
        ).encode()

    def test_parse_mods_skips_junk_and_takes_a_later_real_record(self):
        raw = self._mods_response(
            '<mods:classification authority="lcc">MLCM 2024/43047 (B)</mods:classification>',
            '<mods:classification authority="lcc">B563</mods:classification>'
            '<mods:classification authority="ddc">188</mods:classification>',
        )
        self.assertEqual(
            fetch_library_codes.parse_mods(raw), {"lcc": "B563", "ddc": "188"}
        )

    def test_parse_mods_junk_only_reads_as_a_clean_miss(self):
        # A junk-only response must look like a miss so --sru-fallback fires
        # against it: the work genuinely has no classification yet.
        raw = self._mods_response(
            '<mods:classification authority="lcc">ACQUISITION IN PROCESS</mods:classification>',
            '<mods:classification authority="ddc">[Fic]</mods:classification>',
        )
        self.assertEqual(fetch_library_codes.parse_mods(raw), {})


class TestJunkClassificationGuardFlow(unittest.TestCase):
    """The issue #10 guard end to end through main(): a pre-guard cache entry
    carrying a placeholder 050 must not be written, must not print as a HIT,
    and must land in the misses worklist (with the work-level retry, since a
    sanitized junk-only entry reads as a miss); a junk value that arrives past
    parse_mods entirely (the mocked seam here) is caught by the
    consumption-side sanitize. Reuses the fallback harness's library and
    runner unchanged."""

    def _run(self, tmp, **kwargs):
        return TestFetchWorkFallbackFlow()._run(tmp, **kwargs)

    def test_cached_placeholder_050_is_a_miss_with_the_work_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, misses = self._run(
                tmp,
                seed_cache={"9780465032001": {"lcc": "MLCM 2024/43047 (B)"}},
                fetch_results=[{}],
            )
        self.assertEqual(rc, 0, out)
        self.assertNotIn("HIT", out)
        self.assertTrue(misses, "the book must land in the worklist")
        self.assertIn("no codes found", out)

    def test_fresh_junk_from_the_seam_is_caught_at_consumption(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, misses = self._run(
                tmp, fetch_results=[{"lcc": "ACQUISITION IN PROCESS"}]
            )
        self.assertEqual(rc, 0, out)
        self.assertNotIn("HIT", out)
        self.assertTrue(misses)
        self.assertIn("no codes found", out)

    def test_valid_cached_code_is_untouched(self):
        # The control: a real classification rides the cache to a HIT with
        # no network, and the guards reject nothing.
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, misses = self._run(
                tmp, seed_cache={"9780465032001": {"lcc": "B563"}}
            )
        self.assertEqual(rc, 0, out)
        self.assertIn("HIT", out)
        self.assertIn("B563", out)
        self.assertFalse(misses)


class TestFetchWorkFallbackFlow(unittest.TestCase):
    """The --sru-fallback flow through main(): the work-level hit (tagged,
    cached under its work: key), the cached work hit (no network), the
    cached-empty miss that stays a miss and lands in the worklist, and a
    fallback failure counting toward the abort counter. fetch_codes is
    mocked; the cache is a temp file; main() reads sys.argv."""

    def _library(self, tmp):
        db = pathlib.Path(tmp) / "metadata.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
            CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT);
            """
        )
        con.execute("INSERT INTO books VALUES (1, 'Discourses')")
        con.execute("INSERT INTO authors VALUES (1, 'Epictetus')")
        con.execute("INSERT INTO books_authors_link (book, author) VALUES (1, 1)")
        con.execute("INSERT INTO tags VALUES (1, 'NonFic.Philosophy')")
        con.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 1)")
        con.execute(
            "INSERT INTO identifiers (book, type, val) VALUES (1, 'isbn', '9780465032001')"
        )
        con.commit()
        con.close()
        return db

    def _run(self, tmp, *, fetch_results=None, seed_cache=None):
        f = fetch_library_codes
        db = self._library(tmp)
        cache = str(pathlib.Path(tmp) / "cache.json")
        misses = str(pathlib.Path(tmp) / "misses.txt")
        if seed_cache is not None:
            f.save_cache(cache, seed_cache)
        argv = [
            "fetch_library_codes.py",
            "--db",
            str(db),
            "--cache",
            cache,
            "--misses-file",
            misses,
            "--sru-fallback",
        ]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                f,
                "fetch_codes",
                side_effect=fetch_results
                if fetch_results is not None
                else AssertionError("network used"),
            ),
            mock.patch.object(f.time, "sleep"),
            contextlib.redirect_stdout(out),
        ):
            rc = f.main()
        return (
            rc,
            out.getvalue(),
            f.load_cache(cache),
            pathlib.Path(misses).exists(),
        )

    def test_work_level_hit_is_tagged_and_cached_under_its_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            # First query: the clean ISBN miss (empty dict). Second: the hit.
            rc, out, cached, _ = self._run(tmp, fetch_results=[{}, {"lcc": "B563"}])
        self.assertEqual(rc, 0, out)
        self.assertIn("(work-level)", out)
        self.assertIn("title-fragment fallback", out)
        work_keys = [k for k in cached if k.startswith("work:")]
        self.assertEqual(len(work_keys), 1)
        self.assertEqual(cached[work_keys[0]]["lcc"], "B563")

    def test_cached_work_hit_skips_the_network(self):
        f = fetch_library_codes
        key = f.work_cache_key("Discourses", "Epictetus")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, _ = self._run(
                tmp,
                seed_cache={"9780465032001": {}, key: {"lcc": "B563"}},
            )
        self.assertEqual(rc, 0, out)
        self.assertIn("(work-level)", out)

    def test_cached_empty_work_stays_a_miss(self):
        f = fetch_library_codes
        key = f.work_cache_key("Discourses", "Epictetus")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, misses = self._run(
                tmp,
                seed_cache={"9780465032001": {}, key: {}},
            )
        self.assertEqual(rc, 0)
        self.assertNotIn("(work-level)", out)
        self.assertTrue(misses, "the book must land in the worklist")

    def test_fallback_failure_leaves_the_book_a_miss(self):
        # A fallback network failure is not an ERR row and not a crash: the
        # book stays a miss (it lands in the worklist), the failure counts
        # toward the abort counter with the ISBN failures, and the pass
        # ends clean. Reaching the abort itself needs 8 consecutive
        # failures; the counter arithmetic is shared with the ISBN path.
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, misses = self._run(tmp, fetch_results=[{}, None])
        self.assertEqual(rc, 0)
        self.assertNotIn("ERR", out)
        self.assertTrue(misses)


class TestWorkFragmentAndAuthorFilter(unittest.TestCase):
    """Issue #36, query shape: the fallback queries a pre-colon fragment of
    the title (the full DB title dies on its subtitle in LoC's title index)
    and filters records by the stored author's surname client-side (the
    bath.author clause intermittently draws the unsupported-attribute
    diagnostic, so no author clause is sent at all)."""

    def test_fragment_cuts_at_the_first_colon(self):
        f = fetch_library_codes
        self.assertEqual(
            f.title_fragment(
                "One Two Three . . . Infinity: Facts and Speculations of Science"
            ),
            "One Two Three . . . Infinity",
        )

    def test_fragment_caps_on_a_word_boundary(self):
        f = fetch_library_codes
        self.assertEqual(
            f.title_fragment("A Very Long Title With Many Words Here", cap=30),
            "A Very Long Title With Many",
        )
        self.assertLessEqual(
            len(f.title_fragment("Supercalifragilistic" + "expialidocious" * 4)),
            45,
        )

    def test_fragment_without_a_colon_is_the_title(self):
        self.assertEqual(fetch_library_codes.title_fragment("Discourses"), "Discourses")

    def test_surname_key_forms(self):
        f = fetch_library_codes
        self.assertEqual(f.author_surname("George Gamow"), "gamow")
        self.assertEqual(f.author_surname("Gamow, George"), "gamow")
        self.assertEqual(f.author_surname("Ursula K. Le Guin"), "guin")
        self.assertEqual(f.author_surname("Le Guin, Ursula"), "le guin")
        self.assertEqual(f.author_surname("Epictetus"), "epictetus")

    def test_surname_key_is_empty_for_unusable_authors(self):
        f = fetch_library_codes
        self.assertEqual(f.author_surname("Unknown"), "")
        self.assertEqual(f.author_surname("unknown"), "")
        self.assertEqual(f.author_surname(""), "")
        self.assertEqual(f.author_surname(None), "")

    def test_author_matches_by_heading_equality_or_tail(self):
        f = fetch_library_codes
        self.assertTrue(f.author_matches(["Gamow, George, 1904-1968"], "George Gamow"))
        self.assertTrue(f.author_matches(["Le Guin, Ursula K."], "Ursula K. Le Guin"))
        self.assertTrue(f.author_matches(["Epictetus"], "Epictetus"))
        self.assertTrue(f.author_matches(["Gaiman, Neil"], "Gaiman, Neil"))

    def test_author_matches_reject_other_authors_and_unusable_names(self):
        f = fetch_library_codes
        self.assertFalse(f.author_matches(["Sagan, Carl"], "George Gamow"))
        self.assertFalse(f.author_matches(["Gamow, George"], "Unknown"))
        self.assertFalse(f.author_matches([], "George Gamow"))
        self.assertFalse(f.author_matches(["Gamow, George"], None))


class TestSruResponseGate(unittest.TestCase):
    """Issue #36, cache poisoning: a body that is not an SRU records answer
    (the throttle page, a proxy body, truncated XML) is a FAILED request,
    never a miss. fetch_codes retries it like a network error and returns
    None, so nothing is cached from it; a genuine zero-hit answer is
    well-formed SRU carrying no records and stays a cacheable {}. The gate
    is never strict-fatal: the query form cannot produce a non-SRU body."""

    def test_unparseable_body_raises_sru_response_error(self):
        with self.assertRaises(fetch_library_codes.SRUResponseError):
            fetch_library_codes.parse_mods(b"<html><body>slow down</body></html>")

    def test_wrong_root_raises_sru_response_error(self):
        with self.assertRaises(fetch_library_codes.SRUResponseError):
            fetch_library_codes.parse_mods(
                b"<error><message>throttled</message></error>"
            )

    def test_sru_response_error_is_a_diagnostic_subclass(self):
        self.assertTrue(
            issubclass(
                fetch_library_codes.SRUResponseError,
                fetch_library_codes.SRUDiagnostic,
            )
        )

    def test_genuine_zero_hit_still_parses_as_a_cacheable_miss(self):
        raw = (
            '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            "<numberOfRecords>0</numberOfRecords></searchRetrieveResponse>"
        ).encode()
        self.assertEqual(fetch_library_codes.parse_mods(raw), {})

    @staticmethod
    def _response(raw: bytes):
        resp = mock.MagicMock()
        resp.__enter__.return_value = resp
        resp.read.return_value = raw
        return resp

    def test_fetch_codes_retries_a_throttle_page_to_none(self):
        f = fetch_library_codes
        with (
            mock.patch.object(
                f.urllib.request,
                "urlopen",
                return_value=self._response(b"<html>429</html>"),
            ) as urlopen,
            mock.patch.object(f.time, "sleep"),
        ):
            self.assertIsNone(f.fetch_codes("bath.isbn=9780000000001", 5))
        self.assertEqual(urlopen.call_count, 3, "every retry fired")

    def test_strict_first_query_does_not_abort_on_a_throttle_page(self):
        # The strict abort is for query-form diagnostics (the dead
        # dc.identifier index answered that way every time); a non-SRU body
        # cannot be the query's fault, so it retries out to None even at
        # queried == 0.
        f = fetch_library_codes
        with (
            mock.patch.object(
                f.urllib.request, "urlopen", return_value=self._response(b"garbage")
            ),
            mock.patch.object(f.time, "sleep"),
        ):
            self.assertIsNone(f.fetch_codes("bath.isbn=x", 5, strict=True))

    def test_fetch_codes_returns_empty_dict_on_a_genuine_zero_hit(self):
        raw = (
            '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            "<numberOfRecords>0</numberOfRecords></searchRetrieveResponse>"
        ).encode()
        f = fetch_library_codes
        with (
            mock.patch.object(
                f.urllib.request, "urlopen", return_value=self._response(raw)
            ),
            mock.patch.object(f.time, "sleep"),
        ):
            self.assertEqual(f.fetch_codes("bath.isbn=9780000000001", 5), {})


class TestParseModsForAuthor(unittest.TestCase):
    """Issue #36, parse half: a fragment query asks for 20 records back, and
    only records whose name headings match the requesting book's author may
    donate a classification. Unfiltered parse_mods keeps its
    first-record-carrying-each contract (the ISBN path never passes the
    author)."""

    @staticmethod
    def _mods_response(*record_bodies: str) -> bytes:
        records = "".join(
            "<record><recordData>"
            '<mods:mods xmlns:mods="http://www.loc.gov/mods/v3">'
            + body
            + "</mods:mods></recordData></record>"
            for body in record_bodies
        )
        return (
            '<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">'
            f"<records>{records}</records></searchRetrieveResponse>"
        ).encode()

    @staticmethod
    def _record(name_parts: list[str], body: str = "") -> str:
        parts = "".join(
            f'<mods:name type="personal"><mods:namePart>{p}</mods:namePart></mods:name>'
            for p in name_parts
        )
        return parts + body

    _SAGAN = '<mods:classification authority="lcc">QB64</mods:classification>'
    _GAMOW = '<mods:classification authority="lcc">QC773</mods:classification>'

    def test_a_matching_record_donates_its_code(self):
        raw = self._mods_response(
            self._record(["Sagan, Carl"], self._SAGAN),
            self._record(["Gamow, George, 1904-1968"], self._GAMOW),
        )
        self.assertEqual(
            fetch_library_codes.parse_mods_for_author(raw, "George Gamow"),
            {"lcc": "QC773"},
        )

    def test_no_matching_record_is_a_clean_miss(self):
        raw = self._mods_response(self._record(["Sagan, Carl"], self._SAGAN))
        self.assertEqual(
            fetch_library_codes.parse_mods_for_author(raw, "George Gamow"), {}
        )

    def test_junk_code_on_a_matching_record_skips_to_a_later_one(self):
        raw = self._mods_response(
            self._record(
                ["Gamow, George"],
                '<mods:classification authority="lcc">MLCM 2024/43047 (B)</mods:classification>',
            ),
            self._record(["Gamow, George"], self._GAMOW),
        )
        self.assertEqual(
            fetch_library_codes.parse_mods_for_author(raw, "George Gamow"),
            {"lcc": "QC773"},
        )

    def test_unfiltered_parse_mods_takes_the_first_records_code(self):
        # The control: parse_mods stays record-order-first, so the author
        # gate exists only where the fallback asks for it.
        raw = self._mods_response(
            self._record(["Sagan, Carl"], self._SAGAN),
            self._record(["Gamow, George"], self._GAMOW),
        )
        self.assertEqual(fetch_library_codes.parse_mods(raw), {"lcc": "QB64"})


class TestRefreshBypassesResponseCache(unittest.TestCase):
    """Issue #36, --refresh: its contract is re-query, so it reads past the
    response cache entirely (a stored {}, poisoned or genuine, can no
    longer answer) while cache writes still land and the rest of the cache
    file survives the run. Without --refresh the stored miss still answers
    (the resume contract)."""

    def _run(self, tmp, *, fetch_results, seed_cache, extra=()):
        f = fetch_library_codes
        db = TestFetchWorkFallbackFlow()._library(tmp)
        cache = str(pathlib.Path(tmp) / "cache.json")
        misses = str(pathlib.Path(tmp) / "misses.txt")
        if seed_cache is not None:
            f.save_cache(cache, seed_cache)
        argv = [
            "fetch_library_codes.py",
            "--db",
            str(db),
            "--cache",
            cache,
            "--misses-file",
            misses,
            *extra,
        ]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(f, "fetch_codes", side_effect=fetch_results) as fetch,
            mock.patch.object(f.time, "sleep"),
            contextlib.redirect_stdout(out),
        ):
            rc = f.main()
        return rc, out.getvalue(), f.load_cache(cache), fetch

    def test_refresh_requeries_a_cached_miss_and_overwrites_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, fetch = self._run(
                tmp,
                fetch_results=[{"lcc": "B563"}],
                seed_cache={"9780465032001": {}},
                extra=["--refresh"],
            )
        self.assertEqual(rc, 0, out)
        self.assertEqual(
            fetch.call_count, 1, "--refresh must not answer from the cache"
        )
        self.assertIn("HIT", out)
        self.assertEqual(cached["9780465032001"], {"lcc": "B563"})

    def test_refresh_keeps_entries_outside_this_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, cached, _ = self._run(
                tmp,
                fetch_results=[{"lcc": "B563"}],
                seed_cache={"9780000000009": {"lcc": "Z1"}, "9780465032001": {}},
                extra=["--refresh"],
            )
        self.assertEqual(rc, 0, out)
        self.assertEqual(cached.get("9780000000009"), {"lcc": "Z1"})
        self.assertIn("response cache bypassed", out)

    def test_without_refresh_the_cached_miss_still_answers(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _, fetch = self._run(
                tmp,
                fetch_results=AssertionError("network used"),
                seed_cache={"9780465032001": {}},
            )
        self.assertEqual(rc, 0, out)
        self.assertEqual(fetch.call_count, 0)


class TestMissesWorklistSections(unittest.TestCase):
    """Issue #36, the worklist: runs append timestamped sections instead of
    truncating (any later run, however small, used to clobber the
    accumulated list); the file keeps the most recent
    MISSES_RUN_SECTIONS_KEPT sections."""

    @staticmethod
    def _results(*ids: int) -> list[dict]:
        return [
            {
                "id": i,
                "title": f"T{i}",
                "isbn": f"978000000000{i % 10}",
                "lcc": None,
                "ddc": None,
            }
            for i in ids
        ]

    def test_two_runs_append_two_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "misses.txt")
            fetch_library_codes.write_misses_worklist(self._results(1, 2), path)
            fetch_library_codes.write_misses_worklist(
                self._results(3), path, mode="APPLY"
            )
            text = pathlib.Path(path).read_text()
        self.assertEqual(text.count("# --- run "), 2)
        self.assertIn("(APPLY)", text)
        self.assertIn("T3", text)
        self.assertIn("T1", text, "the first run's section survives the second run")

    def test_header_carries_counts_and_the_id_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "misses.txt")
            fetch_library_codes.write_misses_worklist(self._results(7, 8, 9), path)
            text = pathlib.Path(path).read_text()
        self.assertIn("3 miss(es) of 3 queried", text)
        self.assertIn("# ids: 7 8 9", text)
        self.assertIn("7\t9780000000007\t\tT7", text)

    def test_cap_keeps_the_most_recent_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(pathlib.Path(tmp) / "misses.txt")
            for i in range(fetch_library_codes.MISSES_RUN_SECTIONS_KEPT + 2):
                fetch_library_codes.write_misses_worklist(self._results(i + 1), path)
            text = pathlib.Path(path).read_text()
        self.assertEqual(
            text.count("# --- run "),
            fetch_library_codes.MISSES_RUN_SECTIONS_KEPT,
        )
        self.assertNotIn("T1\n", text, "the oldest section is pruned")
        self.assertIn("T10", text)

    def test_legacy_pre_marker_content_counts_as_one_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "misses.txt"
            path.write_text(
                "# fetch_library_codes misses: old format\n"
                "# id\tisbn\tddc\ttitle\n"
                "9\t978\t\tOld\n"
            )
            fetch_library_codes.write_misses_worklist(self._results(1), str(path))
            text = path.read_text()
        self.assertIn("old format", text, "legacy content is preserved until pruned")
        self.assertEqual(text.count("# --- run "), 1)

    def test_a_trailing_line_without_a_newline_does_not_fuse_sections(self):
        # A hand-edited worklist can end mid-line; the marker must start a
        # real line or every later run reads the file as ONE legacy section.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "misses.txt"
            path.write_text("9\t978\t\tOld")
            fetch_library_codes.write_misses_worklist(self._results(1), path)
            fetch_library_codes.write_misses_worklist(self._results(2), str(path))
            text = path.read_text()
        self.assertIn("9\t978\t\tOld\n", text, "the dangling line is healed")
        self.assertEqual(text.count("# --- run "), 2)

    def test_a_non_utf8_worklist_is_read_with_replacement_not_a_crash(self):
        # The read happens at the tail of a run (after the network pass,
        # before an --apply's DB write); a stray byte in a hand-edited file
        # must degrade the old content, never kill the pass.
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "misses.txt"
            path.write_bytes(b"9\t978\t\t\xffOld\n")
            n = fetch_library_codes.write_misses_worklist(self._results(1), str(path))
            text = path.read_text(errors="replace")
        self.assertEqual(n, 1)
        self.assertIn("# --- run ", text)
        self.assertIn("Old", text)


class TestValidatorCoreChecks(unittest.TestCase):
    """The six checks the 2026-09-20 test-gap audit named as untested
    (its item 5): duplicate ISBN, identifier types, amazon-isbn10, and
    the pubdate parseable/sentinel and language pairs. Same shape as the
    other validator classes: an in-memory library, the check function,
    and the Reporter's code lists."""

    def _cur(self, ddl, inserts=()):
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.executescript(ddl)
        for stmt in inserts:
            cur.execute(stmt)
        return cur

    _BOOKS_DDL = """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, pubdate TEXT);
        CREATE TABLE books_languages_link (book INT, lang_code INT);
    """

    def test_books_without_any_language_are_flagged(self):
        cur = self._cur(
            self._BOOKS_DDL,
            [
                "INSERT INTO books VALUES (1, 'Tagged', '2020-01-01')",
                "INSERT INTO books VALUES (2, 'Bare', '2020-01-01')",
                "INSERT INTO books_languages_link VALUES (1, 1)",
            ],
        )
        report = validate_metadata.Reporter()
        validate_metadata.check_every_book_language(cur, report)
        self.assertEqual([c for c, _ in report.errors], ["EVERY_BOOK_LANGUAGE"])
        self.assertIn("'Bare'", report.errors[0][1])

    def test_every_book_language_clean_library_is_silent(self):
        cur = self._cur(
            self._BOOKS_DDL,
            [
                "INSERT INTO books VALUES (1, 'Tagged', '2020-01-01')",
                "INSERT INTO books_languages_link VALUES (1, 1)",
            ],
        )
        report = validate_metadata.Reporter()
        validate_metadata.check_every_book_language(cur, report)
        self.assertEqual(report.errors, [])

    _ISBN_DDL = """
        CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
        INSERT INTO identifiers VALUES (1, 'isbn', '9780441172719');
        INSERT INTO identifiers VALUES (2, 'isbn', '9780441172719');
        INSERT INTO identifiers VALUES (3, 'isbn', '9780765377067');
        INSERT INTO identifiers VALUES (4, 'isbn', '');
    """

    def test_isbn_shared_by_two_books_is_flagged_once(self):
        cur = self._cur(self._ISBN_DDL)
        report = validate_metadata.Reporter()
        validate_metadata.check_no_duplicate_isbn(cur, report)
        self.assertEqual([c for c, _ in report.errors], ["NO_DUPLICATE_ISBN"])
        self.assertIn("books: 1,2", report.errors[0][1])

    def test_same_isbn_twice_on_one_book_is_not_a_duplicate(self):
        cur = self._cur(
            """
            CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
            INSERT INTO identifiers VALUES (1, 'isbn', '9780441172719');
            INSERT INTO identifiers VALUES (1, 'isbn', '9780441172719');
        """
        )
        report = validate_metadata.Reporter()
        validate_metadata.check_no_duplicate_isbn(cur, report)
        self.assertEqual(report.errors, [])

    _PUBDATE_DDL = """
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, pubdate TEXT);
        INSERT INTO books VALUES (1, 'Real Date', '1965-08-01');
        INSERT INTO books VALUES (2, 'Sentinel', '0101-01-01');
        INSERT INTO books VALUES (3, 'Garbage', 'not-a-date');
        INSERT INTO books VALUES (4, 'Null', NULL);
    """

    def test_unparseable_pubdate_is_an_error(self):
        cur = self._cur(self._PUBDATE_DDL)
        report = validate_metadata.Reporter()
        validate_metadata.check_pubdate_parseable(cur, report)
        self.assertEqual([c for c, _ in report.errors], ["PUBDATE_PARSEABLE"])
        self.assertIn("'Garbage'", report.errors[0][1])

    def test_sentinel_pubdate_is_a_warning(self):
        cur = self._cur(self._PUBDATE_DDL)
        report = validate_metadata.Reporter()
        validate_metadata.check_no_sentinel_pubdate(cur, report)
        self.assertEqual([c for c, _ in report.warnings], ["NO_SENTINEL_PUBDATE"])
        self.assertIn("'Sentinel'", report.warnings[0][1])
        # The same rows through the parseable check: the sentinel PARSES,
        # so it is a warning class, never an error class.
        parse_report = validate_metadata.Reporter()
        validate_metadata.check_pubdate_parseable(cur, parse_report)
        self.assertEqual([c for c, _ in parse_report.errors], ["PUBDATE_PARSEABLE"])
        self.assertIn("'Garbage'", parse_report.errors[0][1])

    def test_identifier_type_forbidden_is_an_error_even_unstrict(self):
        cur = self._cur(
            """
            CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
            INSERT INTO identifiers VALUES (1, 'url', 'https://x');
            INSERT INTO identifiers VALUES (2, 'isbn', '9780441172719');
        """
        )
        report = validate_metadata.Reporter()
        validate_metadata.check_identifier_types(
            cur,
            report,
            validate_metadata.DEFAULT_FORBIDDEN_TYPES,
            validate_metadata.DEFAULT_CANONICAL_TYPES,
            strict=False,
        )
        self.assertEqual([c for c, _ in report.errors], ["ID_TYPE_FORBIDDEN"])
        self.assertIn("'url'", report.errors[0][1])

    def test_identifier_type_undeclared_needs_strict(self):
        ddl = """
            CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
            INSERT INTO identifiers VALUES (1, 'libgen', '28374');
        """
        strict = validate_metadata.Reporter()
        validate_metadata.check_identifier_types(
            self._cur(ddl),
            strict,
            validate_metadata.DEFAULT_FORBIDDEN_TYPES,
            validate_metadata.DEFAULT_CANONICAL_TYPES,
            strict=True,
        )
        self.assertEqual([c for c, _ in strict.warnings], ["ID_TYPE_UNDECLARED"])
        loose = validate_metadata.Reporter()
        validate_metadata.check_identifier_types(
            self._cur(ddl),
            loose,
            validate_metadata.DEFAULT_FORBIDDEN_TYPES,
            validate_metadata.DEFAULT_CANONICAL_TYPES,
            strict=False,
        )
        self.assertEqual(loose.warnings, [])

    _AMAZON_DDL = """
        CREATE TABLE identifiers (book INT, type TEXT, val TEXT);
        INSERT INTO identifiers VALUES (1, 'amazon', '0441172717');
        INSERT INTO identifiers VALUES (2, 'mobi-asin', 'B002RI9K5I');
        INSERT INTO identifiers VALUES (3, 'amazon', '9780441172719');
    """

    def test_real_isbn10_filed_as_amazon_is_flagged(self):
        cur = self._cur(self._AMAZON_DDL)
        report = validate_metadata.Reporter()
        validate_metadata.check_amazon_is_isbn10(cur, report)
        self.assertEqual([c for c, _ in report.warnings], ["AMAZON_IS_ISBN10"])
        # The ASIN (never a valid ISBN-10 checksum) and the ISBN-13 stay
        # silent: only the misfiled ISBN-10 is named.
        self.assertIn("'0441172717'", report.warnings[0][1])
        self.assertIn("amazon", report.warnings[0][1])


class TestSeamDecoding(unittest.TestCase):
    """Issue #8's class across the sibling seams (3.62.2): every spawn
    decodes with errors='replace', so non-UTF-8 tool output (qpdf
    echoing a PDF's raw bytes, exiftool on a mojibake Info dict) reads
    as replacement characters instead of raising UnicodeDecodeError in
    communicate(). Real subprocesses: the mocked seam tests never
    exercise the decoder. Driven through the spawn helpers, which take
    any argv, so no calibre tool needs to exist."""

    BAD = "import sys; sys.stdout.buffer.write(b'x \\xa0\\xff y')"

    def _assert_lossy(self, proc):
        import locale

        self.assertEqual(proc.returncode, 0)
        if "UTF" in locale.getpreferredencoding(False).upper():
            self.assertIn("\ufffd", proc.stdout)

    def test_stamp_pdf_helpers_decode_lossily(self):
        for helper in (
            stamp_pdf._run_exiftool,
            stamp_pdf._run_ebook_meta,
            stamp_pdf._run_qpdf,
        ):
            with self.subTest(helper=helper.__name__):
                self._assert_lossy(helper([sys.executable, "-c", self.BAD]))

    def test_stamp_epub_helper_decodes_lossily(self):
        self._assert_lossy(stamp_epub._run_ebook_meta([sys.executable, "-c", self.BAD]))

    def test_every_text_spawn_carries_errors_replace(self):
        # The 3.62.2 blanket: ANY subprocess call in src/ or scripts/
        # that passes text=True must also pass errors='replace' (the
        # issue-#8 class: strict decoding/encoding of piped output is
        # one non-UTF-8 byte from an unhandled exception). AST walk, so
        # formatting and kwarg order cannot rot the pin.
        import ast

        roots = [
            Path(__file__).resolve().parent.parent / "scripts",
            Path(__file__).resolve().parent.parent / "src",
        ]
        for root in roots:
            for py in sorted(root.rglob("*.py")):
                tree = ast.parse(py.read_text())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not (
                        isinstance(func, ast.Attribute)
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess"
                    ):
                        continue
                    keywords = {kw.arg for kw in node.keywords if kw.arg}
                    textmode = (
                        "text" in keywords
                        or "universal_newlines" in keywords
                        or "encoding" in keywords
                    )
                    if not textmode:
                        continue
                    values = {
                        kw.arg: kw.value
                        for kw in node.keywords
                        if kw.arg and isinstance(kw.value, ast.Constant)
                    }
                    errors = values.get("errors")
                    self.assertEqual(
                        getattr(errors, "value", errors),
                        "replace",
                        f"{py.relative_to(roots[0].parent)}:"
                        f"{node.lineno}: a text-mode spawn without "
                        'errors="replace"',
                    )


class TestExtractStampEvidence(unittest.TestCase):
    """The wave evidence feeder (issue #24): one JSON row per file,
    attribute-order-independent OPF parsing (the 2026-10-14 href-first
    regression), the thin-front census as the extraction's quality gate."""

    #: Deterministic ebook-meta display dump for every main() run; the
    #: missing-binary and traceback guards get their own tests.
    SEED_DUMP = (
        "Title            : Stub\n"
        "Author(s)        : Stub Author [Author, Stub]\n"
        "Published        : 2001-01-01T00:00:00+00:00\n"
        "Languages        : eng\n"
        "Identifiers      : isbn:9780140445688\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wave = Path(self.tmp.name) / "wave"
        self.wave.mkdir()
        self.out_dir = Path(self.tmp.name) / "out"
        self.out_dir.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_epub(self, path, docs, href_first=True):
        """docs: [(doc_id, xhtml_text)]. The manifest items put href first
        (the 2026-10-14 wave's shape) unless href_first is False."""
        if href_first:
            items = "".join(
                f'<item href="{did}.xhtml" id="{did}" '
                'media-type="application/xhtml+xml"/>\n'
                for did, _ in docs
            )
        else:
            items = "".join(
                f'<item id="{did}" href="{did}.xhtml" '
                'media-type="application/xhtml+xml"/>\n'
                for did, _ in docs
            )
        spine = "".join(f'<itemref idref="{did}"/>' for did, _ in docs)
        opf = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<package xmlns="http://www.idpf.org/2007/opf" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0">'
            "<metadata><dc:title>Stub</dc:title>"
            "<dc:identifier>urn:isbn:9780140445688</dc:identifier></metadata>"
            f"<manifest>{items}</manifest><spine>{spine}</spine></package>"
        )
        container = (
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
            '<rootfiles><rootfile full-path="OEBPS/content.opf" '
            'media-type="application/oebps-package+xml"/></rootfiles></container>'
        )
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", container)
            z.writestr("OEBPS/content.opf", opf)
            for did, text in docs:
                z.writestr(
                    f"OEBPS/{did}.xhtml", f"<html><body><p>{text}</p></body></html>"
                )

    def _run_main(self, *extra, inputs=None, ebook_meta=None):
        """ebook_meta: an optional _run_ebook_meta stand-in (return value
        or side_effect list); the default answers the SEED_DUMP display
        read for every file."""
        argv = [
            "extract_stamp_evidence",
            *(inputs if inputs is not None else [str(self.wave)]),
            "--out",
            str(self.out_dir / "evidence.json"),
            *extra,
        ]
        buf = io.StringIO()
        seed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self.SEED_DUMP, stderr=""
        )
        if isinstance(ebook_meta, (list, BaseException)):
            # a per-call list or an exception stand-in (the missing-binary
            # degrade); anything else is the fixed return value
            patch = {"side_effect": ebook_meta}
        elif ebook_meta is not None:
            patch = {"return_value": ebook_meta}
        else:
            patch = {"return_value": seed}
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(extract_stamp_evidence, "_run_ebook_meta", **patch),
            contextlib.redirect_stdout(buf),
            contextlib.redirect_stderr(buf),
        ):
            rc = extract_stamp_evidence.main()
        return rc, buf.getvalue()

    def _rows(self):
        return json.loads((self.out_dir / "evidence.json").read_text())

    def test_href_first_opf_still_extracts_front(self):
        # The 2026-10-14 regression that retired the hand-rolled copies:
        # 87 of 111 EPUBs put href before id, an id-first regex matched
        # nothing, and front went silently empty for three quarters of
        # the batch.
        p = self.wave / "Author - Book (z-library.sk, 1lib.sk).epub"
        self._make_epub(
            p,
            [
                ("copyright", "First published 1987. ISBN 978-0-12-345678-9"),
                ("chapter1", "Once upon a time in the stacks. " * 60),
            ],
        )
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        self.assertIn("978-0-12-345678-9", row["front"])
        self.assertEqual(row["isbn_snips"], ["978-0-12-345678-9"])
        self.assertEqual(row["opf_ids"], ["urn:isbn:9780140445688"])
        self.assertIsNone(row["error"])

    def test_id_first_opf_extracts_too(self):
        p = self.wave / "b.epub"
        self._make_epub(p, [("c1", "Some front matter text. " * 60)], href_first=False)
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        self.assertIn("Some front matter text", row["front"])

    def test_only_the_first_four_spine_docs(self):
        docs = [(f"d{i}", f"doc{i} marker ") for i in range(6)]
        self._make_epub(self.wave / "b.epub", docs)
        rc, out = self._run_main("--thin-chars", "1")
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        for i in range(4):
            self.assertIn(f"doc{i} marker", row["front"])
        self.assertNotIn("doc4 marker", row["front"])

    def test_seed_is_the_display_dump_subset(self):
        self._make_epub(self.wave / "b.epub", [("c1", "text " * 60)])
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        self.assertEqual(
            row["seed"],
            {
                "title": "Stub",
                "author(s)": "Stub Author [Author, Stub]",
                "published": "2001-01-01T00:00:00+00:00",
                "languages": "eng",
                "identifiers": "isbn:9780140445688",
            },
        )

    def test_seed_seam_missing_binary_degrades_to_a_row_error(self):
        self._make_epub(self.wave / "b.epub", [("c1", "text " * 60)])
        missing = FileNotFoundError(2, "ebook-meta not found")
        rc, out = self._run_main(ebook_meta=missing)
        self.assertEqual(rc, 0, "a missing binary is a per-file error, not a crash")
        (row,) = self._rows()
        self.assertIsNone(row["seed"])
        self.assertIn("ebook-meta", row["error"])

    def test_seed_seam_traceback_refuses_the_read(self):
        # Calibre exits 0 with ITS OWN filename guess plus a traceback on
        # stderr; that guess must never become the seed (run.py's guard).
        bad = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout="Title : Filename Guess\n",
            stderr="Traceback (most recent call last):\nValueError\n",
        )
        with mock.patch.object(
            extract_stamp_evidence, "_run_ebook_meta", return_value=bad
        ):
            seed, err = extract_stamp_evidence._seed(Path("x.epub"))
        self.assertIsNone(seed)
        self.assertIn("unparseable", err)

    def test_pdf_front_seam_command_shape(self):
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="page text " * 50, stderr=""
        )
        with mock.patch.object(
            extract_stamp_evidence.subprocess, "run", return_value=proc
        ) as m:
            front = extract_stamp_evidence._pdf_front(Path("/wave/x.pdf"))
        self.assertEqual(
            m.call_args.args[0],
            ["pdftotext", "-f", "1", "-l", "4", "/wave/x.pdf", "-"],
        )
        self.assertTrue(front.startswith("page text"))

    def test_pdf_front_failure_is_a_row_error_and_thin(self):
        (self.wave / "broken.pdf").write_bytes(b"%PDF-1.4 broken")
        proc = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Syntax Error: no trailer\n"
        )
        with mock.patch.object(
            extract_stamp_evidence, "_run_pdftotext", return_value=proc
        ):
            rc, out = self._run_main()
        self.assertEqual(rc, 1, "a pdf with no front is a thin row")
        (row,) = self._rows()
        self.assertIn("pdftotext exited 1", row["error"])

    def test_thin_front_census_fails_the_run(self):
        self._make_epub(self.wave / "Empty Book.epub", [("cover", "")])
        rc, out = self._run_main()
        self.assertEqual(rc, 1)
        self.assertIn("THIN FRONT (1 of 1)", out)
        self.assertIn("Empty Book.epub", out)

    def test_non_front_formats_are_not_census_judged(self):
        (self.wave / "scan.djvu").write_bytes(b"DJVU")
        rc, out = self._run_main()
        self.assertEqual(rc, 0, "no extractor for djvu: not an extraction failure")
        (row,) = self._rows()
        self.assertEqual(row["fmt"], "djvu")
        self.assertEqual(row["front"], "")

    def test_bare_name_seeds_z_lib_and_markers_seed_their_site(self):
        self._make_epub(self.wave / "Bare Name Book.epub", [("c1", "text " * 60)])
        self._make_epub(
            self.wave / "Wave Book -- Anna\u2019s Archive.epub", [("c1", "text " * 60)]
        )
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        by_file = {r["file"]: r["provenance"] for r in self._rows()}
        # The standing 2026-10-12 ruling: bare names seed Z-Lib.
        self.assertEqual(by_file["Bare Name Book.epub"], "Z-Lib")
        self.assertEqual(
            by_file["Wave Book -- Anna\u2019s Archive.epub"], "Anna's Archive"
        )

    def test_rows_are_a_bare_list_with_the_contract_shape(self):
        self._make_epub(self.wave / "b.epub", [("c1", "text " * 60)])
        rc, out = self._run_main()
        rows = self._rows()
        self.assertIsInstance(rows, list)
        self.assertEqual(
            set(rows[0]),
            {
                "file",
                "path",
                "fmt",
                "seed",
                "opf_ids",
                "front",
                "isbn_snips",
                "copyright_snips",
                "provenance",
                "error",
            },
        )

    def test_out_inside_a_scanned_dir_is_refused(self):
        self._make_epub(self.wave / "b.epub", [("c1", "text " * 60)])
        rc, out = self._run_main("--out", str(self.wave / "evidence.json"))
        self.assertEqual(rc, 2)
        self.assertIn("OUTSIDE", out)

    def test_out_on_a_scanned_file_is_refused(self):
        target = self.wave / "b.epub"
        self._make_epub(target, [("c1", "text " * 60)])
        # The file named directly as the only input: the dir guard cannot
        # fire (no directory scanned), the file guard must.
        rc, out = self._run_main("--out", str(target), inputs=[str(target)])
        self.assertEqual(rc, 2)
        self.assertIn("scanned file", out)

    def test_missing_input_and_empty_scan_are_refused(self):
        rc, out = self._run_main(inputs=[str(self.wave / "nope")])
        self.assertEqual(rc, 2)
        empty = self.out_dir / "nothing-here"
        empty.mkdir()
        rc, out = self._run_main(inputs=[str(empty)])
        self.assertEqual(rc, 2)
        self.assertIn("no ebook files", out)

    def test_directory_listing_is_top_level_only(self):
        # A prior phase's _quarantine/ subtree must never sweep into the
        # evidence; directories list their immediate files only.
        (self.wave / "Bare Name Book.epub").write_bytes(b"EPUB")
        quarantined = self.wave / "_quarantine"
        quarantined.mkdir()
        (quarantined / "locked.epub").write_bytes(b"EPUB")
        rc, out = self._run_main()
        self.assertEqual(rc, 1, "the fake EPUB has no front: a thin row")
        self.assertEqual([r["file"] for r in self._rows()], ["Bare Name Book.epub"])

    def test_evidence_extensions_match_run_dot_py(self):
        # The scanned set is run.py's _EBOOK_EXTS by comment; the pin
        # makes the comment load-bearing.
        from cquarry_cli import run as cq_run

        self.assertEqual(extract_stamp_evidence.EVIDENCE_EXTENSIONS, cq_run._EBOOK_EXTS)

    def test_isbn_snips_read_the_isbn_13_label_shape(self):
        # Copyright pages label the number "ISBN-13:"; the label gap must
        # cross the edition digits, and a bare year stays a non-match.
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("ISBN-13: 978-0-306-40615-7"),
            ["978-0-306-40615-7"],
        )
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("ISBN 13: 9780306406157"),
            ["9780306406157"],
        )
        self.assertEqual(
            extract_stamp_evidence._isbn_snips(
                "First published 2013 by Academic Press"
            ),
            [],
        )

    def test_isbn_snips_read_the_label_free_shape_and_dedup(self):
        # Issue #41's live mechanism (the Zando/Slowburn Weaver trilogy,
        # 2026-10-29): Vellum copyright pages print the number block with
        # NO "ISBN" label, which the labeled shape can never see.
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("978-1-63893-180-5 (ebook)"),
            ["978-1-63893-180-5"],
        )
        # Separator-free and space-separated spellings match, and the
        # match ends at the check digit (no trailing-hyphen eat).
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("9781638931805"),
            ["9781638931805"],
        )
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("978 1 63893 180 5"),
            ["978 1 63893 180 5"],
        )
        # One number in two spellings lists once; the hyphenated form
        # sorts first and wins.
        self.assertEqual(
            extract_stamp_evidence._isbn_snips(
                "ISBN 9780306406157 and 978-0-306-40615-7"
            ),
            ["978-0-306-40615-7"],
        )
        # A 978-prefixed number that is too short is not an ISBN shape.
        self.assertEqual(extract_stamp_evidence._isbn_snips("ref 97812345 filed"), [])
        # The X check digit folds case through the dedup key.
        self.assertEqual(
            extract_stamp_evidence._isbn_snips("ISBN 0-306-40615-X, isbn 030640615x"),
            ["0-306-40615-X"],
        )

    def test_copyright_snips_read_edition_and_lccn_lines(self):
        # Issue #41's evidence classes, in the live spellings: the
        # edition month line and BOTH LCCN label forms.
        self.assertEqual(
            extract_stamp_evidence._copyright_snips(
                "First Edition: June 2024 Text design by someone"
            ),
            ["First Edition: June 2024"],
        )
        self.assertEqual(
            extract_stamp_evidence._copyright_snips(
                "Library of Congress Control Number: 2023948985"
            ),
            ["Library of Congress Control Number: 2023948985"],
        )
        self.assertEqual(
            extract_stamp_evidence._copyright_snips("LCCN 2024949155"),
            ["LCCN 2024949155"],
        )
        self.assertEqual(extract_stamp_evidence._copyright_snips("no signals"), [])

    def test_snips_scan_the_full_front_doc_past_the_sample_cap(self):
        # Issue #41's truncation class: the ISBN block rides the TAIL of
        # a copyright doc longer than the per-document sample cap. The
        # row's front still truncates (the display shape), the snips do
        # not (they scan the full text).
        tail = "filler prose. " * 260 + (
            "First Edition: June 2024. 978-1-63893-180-5 (ebook). "
            "Library of Congress Control Number: 2023948985"
        )
        self._make_epub(self.wave / "Long Tail Book.epub", [("copyright", tail)])
        rc, out = self._run_main()
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        self.assertLess(len(row["front"]), len(tail), "the sample still caps")
        self.assertNotIn("978-1-63893-180-5", row["front"])
        self.assertEqual(row["isbn_snips"], ["978-1-63893-180-5"])
        self.assertEqual(
            row["copyright_snips"],
            [
                "First Edition: June 2024",
                "Library of Congress Control Number: 2023948985",
            ],
        )

    def test_pdf_snips_scan_past_the_page_sample_cap(self):
        (self.wave / "Long Tail.pdf").write_bytes(b"%PDF-1.4 stub")
        stdout = "page text " * 500 + "978-1-63893-174-4 (ebook)"
        proc = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=stdout, stderr=""
        )
        with mock.patch.object(
            extract_stamp_evidence, "_run_pdftotext", return_value=proc
        ):
            rc, out = self._run_main()
        self.assertEqual(rc, 0)
        (row,) = self._rows()
        self.assertLessEqual(len(row["front"]), extract_stamp_evidence._PDF_FRONT_CHARS)
        self.assertNotIn("978-1-63893-174-4", row["front"])
        self.assertEqual(row["isbn_snips"], ["978-1-63893-174-4"])

    def test_stale_module_seeder_falls_back_to_the_vendored_copy(self):
        # The issue-#20 skew shape: an installed cquarry_cli below the
        # 3.66.0 floor still seeds None for bare names, so the module
        # import must be floor-checked and the vendored fallback used.
        vg_spec = importlib.util.spec_from_file_location(
            "version_guard", _SCRIPTS / "version_guard.py"
        )
        vg = importlib.util.module_from_spec(vg_spec)
        vg_spec.loader.exec_module(vg)
        sys.modules["version_guard"] = vg
        with mock.patch.object(vg, "imported_version", return_value="3.65.0"):
            stale = _load("extract_stamp_evidence")
        self.assertIn("_PROVENANCE_PATTERNS", stale.__dict__)
        self.assertEqual(
            stale._provenance_from_filename("Lord Brocktree.epub"), "Z-Lib"
        )
        with mock.patch.object(vg, "imported_version", return_value="3.66.0"):
            fresh = _load("extract_stamp_evidence")
        self.assertNotIn("_PROVENANCE_PATTERNS", fresh.__dict__)


drive_stamps = _load("drive_stamps")


class TestDriveStamps(unittest.TestCase):
    """Issue #29: the wave stamp driver as a tool. The coverage assert
    against the ACTUAL target directory runs before any spawn (the
    wrong-table incident protection: the oct-15 hand-rolled copy ran
    twice against the wrong wave's stamp table), NONE stamps omit the
    flag, and the results record lands beside the stamps file. The stamp
    tools themselves are mocked at the spawn seam."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.wave = self.root / "wave"
        self.target = self.root / "target"
        self.target.mkdir()
        self.wave.mkdir()
        self.backup = self.root / "backup"
        self.stamps = self.wave / "stamps_final.json"

    def tearDown(self):
        self.tmp.cleanup()

    def _write_map(self, rows):
        self.stamps.write_text(json.dumps(rows), encoding="utf-8")

    def _file(self, name):
        (self.target / name).write_bytes(b"PAYLOAD")
        return name

    def _run_main(self, *extra, spawn_rc=(0, "")):
        out, err = io.StringIO(), io.StringIO()
        argv = ["drive_stamps.py", str(self.stamps), str(self.target), *extra]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(drive_stamps, "_spawn", return_value=spawn_rc) as spawn,
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = drive_stamps.main()
        return rc, spawn, out.getvalue(), err.getvalue()

    def _spawned_cmds(self, spawn):
        return [call.args[0] for call in spawn.call_args_list]

    def test_command_shape_omits_none_flags(self):
        # The NONE contract: a NONE/empty value OMITS the flag -- the
        # tools write exactly what they are passed, so a literal-NONE
        # publisher would be a phase-3 defect.
        self._file("A Book (One).epub")
        self._write_map(
            {
                "A Book (One).epub": {
                    "title": "A Book",
                    "authors": ["Author One", "Author Two"],
                    "publisher": "NONE",
                    "isbn": "",
                    "cut": ["Translator (translator)"],
                    "notes": "carried, never stamped",
                }
            }
        )
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 0)
        (cmd,) = self._spawned_cmds(spawn)
        self.assertEqual(cmd[0], sys.executable)
        self.assertTrue(cmd[1].endswith("stamp_epub.py"))
        self.assertEqual(cmd[2], str(self.target / "A Book (One).epub"))
        self.assertIn("--title", cmd)
        self.assertEqual(cmd[cmd.index("--title") + 1], "A Book")
        self.assertEqual(cmd.count("--author"), 2)
        self.assertNotIn("--publisher", cmd)
        self.assertNotIn("--isbn", cmd)
        self.assertNotIn("--apply", cmd)  # dry run by default
        # A dry preview with a backup dir shows the real command: the
        # value rides through, never a literal "None".
        self.assertEqual(cmd[cmd.index("--backup-dir") + 1], str(self.backup))
        self.assertIn("coverage assert OK: 1 stamps == 1 files on disk", out)

    def test_apply_mode_passes_the_apply_flag(self):
        name = self._file("B Book.pdf")
        self._write_map(
            {
                name: {
                    "title": "B Book",
                    "authors": [],
                    "publisher": "Pr Press",
                    "isbn": "9780140445688",
                }
            }
        )
        rc, spawn, out, err = self._run_main(
            "--apply", "--backup-dir", str(self.backup)
        )
        self.assertEqual(rc, 0)
        (cmd,) = self._spawned_cmds(spawn)
        self.assertTrue(cmd[1].endswith("stamp_pdf.py"))
        self.assertEqual(cmd[-1], "--apply")
        # An empty authors list omits --author entirely.
        self.assertNotIn("--author", cmd)

    def test_coverage_assert_names_both_diffs_before_any_spawn(self):
        # The incident protection: map vs ACTUAL target directory, both
        # directions named, exit 2, and the spawn seam never reached.
        self._file("On Disk.epub")
        self._file("Stray.epub")
        self._write_map(
            {
                "On Disk.epub": {"title": "On Disk", "authors": ["A"]},
                "Missing.epub": {"title": "Missing", "authors": ["A"]},
            }
        )
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 2)
        self.assertEqual(spawn.call_args_list, [])
        self.assertIn("coverage assert FAILED", err)
        self.assertIn("Missing.epub", err)
        self.assertIn("Stray.epub", err)

    def test_bad_rows_stop_the_run_before_coverage(self):
        self._file("Fine.epub")
        self._write_map(
            {
                "Fine.epub": {"title": "Fine", "authors": ["A"]},
                "Empty.epub": {"title": "NONE", "authors": [], "cut": []},
            }
        )
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 2)
        self.assertEqual(spawn.call_args_list, [])
        self.assertIn("no stampable field", err)
        self.assertIn("Empty.epub", err)

    def test_unknown_field_warns_but_runs(self):
        self._file("Warned.epub")
        self._write_map(
            {
                "Warned.epub": {
                    "title": "Warned",
                    "authors": ["A"],
                    "edition": "3rd",
                }
            }
        )
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 0)
        self.assertIn("unknown stamp field(s) ignored: edition", err)

    def test_unstampable_format_is_a_named_skip(self):
        # DJVU/MOBI ride the coverage assert but are never stamped here:
        # the phase-1 skill routes them by hand, and the skip is named in
        # the summary and the results record, never silent.
        self._file("Scan.djvu")
        self._write_map({"Scan.djvu": {"title": "Scan", "authors": ["A"]}})
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 0)
        self.assertEqual(spawn.call_args_list, [])
        self.assertIn("SKIP (unstampable format .djvu)", out)
        report = json.loads((self.wave / "stamp_results.json").read_text())
        self.assertEqual(report["skipped"][0]["file"], "Scan.djvu")

    def test_format_filter_skips_the_other_format(self):
        # The pdf/epub split: --format pdf dispatches PDFs only; the
        # coverage assert still covers the whole map and directory.
        self._file("E Book.epub")
        self._file("P Book.pdf")
        self._write_map(
            {
                "E Book.epub": {"title": "E", "authors": ["A"]},
                "P Book.pdf": {"title": "P", "authors": ["A"]},
            }
        )
        rc, spawn, out, err = self._run_main(
            "--backup-dir", str(self.backup), "--format", "pdf"
        )
        self.assertEqual(rc, 0)
        (cmd,) = self._spawned_cmds(spawn)
        self.assertTrue(cmd[1].endswith("stamp_pdf.py"))
        self.assertEqual(cmd[2], str(self.target / "P Book.pdf"))
        self.assertIn("SKIP (outside --format pdf) E Book.epub", out)

    def test_results_land_beside_the_stamps_file(self):
        # The association the oct-15 hand-rolled driver got wrong (it
        # wrote the oct-14 wave's directory): the default is the stamps
        # file's own directory, never the other wave's.
        self._file("Solo.epub")
        self._write_map({"Solo.epub": {"title": "Solo", "authors": ["A"]}})
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 0)
        report = json.loads((self.wave / "stamp_results.json").read_text())
        self.assertEqual(report["done"], ["Solo.epub"])
        self.assertEqual(report["failed"], [])
        self.assertFalse(report["apply"])
        self.assertEqual(report["loaded"], 1)

    def test_results_inside_the_target_are_refused(self):
        self._file("X.epub")
        self._write_map({"X.epub": {"title": "X", "authors": ["A"]}})
        rc, spawn, out, err = self._run_main("--results", str(self.target / "res.json"))
        self.assertEqual(rc, 2)
        self.assertIn("must not land inside the target directory", err)

    def test_results_must_not_be_the_stamps_file(self):
        self._write_map({"X.epub": {"title": "X", "authors": ["A"]}})
        (self.target / "X.epub").write_bytes(b"P")
        rc, spawn, out, err = self._run_main("--results", str(self.stamps))
        self.assertEqual(rc, 2)
        self.assertIn("must not be the stamps file", err)

    def test_apply_requires_backup_dir(self):
        self._write_map({"X.epub": {"title": "X", "authors": ["A"]}})
        (self.target / "X.epub").write_bytes(b"P")
        rc, spawn, out, err = self._run_main("--apply")
        self.assertEqual(rc, 2)
        self.assertIn("--apply requires --backup-dir", err)

    def test_backup_dir_inside_the_target_is_refused(self):
        self._write_map({"X.epub": {"title": "X", "authors": ["A"]}})
        (self.target / "X.epub").write_bytes(b"P")
        rc, spawn, out, err = self._run_main(
            "--apply", "--backup-dir", str(self.target / "bak")
        )
        self.assertEqual(rc, 2)
        self.assertIn("must sit OUTSIDE the target directory", err)

    def test_failure_names_the_remainder_and_exits_1(self):
        self._file("Bad.epub")
        self._file("Good.epub")
        self._write_map(
            {
                "Bad.epub": {"title": "Bad", "authors": ["A"]},
                "Good.epub": {"title": "Good", "authors": ["A"]},
            }
        )
        # Sorted dispatch order is Bad then Good: the first spawn fails,
        # the second succeeds, and the run continues past the failure
        # (the copies' shape) with the remainder named.
        out, err = io.StringIO(), io.StringIO()
        argv = [
            "drive_stamps.py",
            str(self.stamps),
            str(self.target),
            "--backup-dir",
            str(self.backup),
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                drive_stamps,
                "_spawn",
                side_effect=[(1, "read-back disagrees"), (0, "")],
            ),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = drive_stamps.main()
        self.assertEqual(rc, 1)
        self.assertIn("FAIL", out.getvalue())
        self.assertIn("REMAINDER: Bad.epub", out.getvalue())
        report = json.loads((self.wave / "stamp_results.json").read_text())
        self.assertEqual(report["failed"][0]["file"], "Bad.epub")
        self.assertEqual(report["failed"][0]["detail"], "read-back disagrees")
        self.assertEqual(report["done"], ["Good.epub"])

    def test_discard_excludes_a_key_and_a_stray_discard_is_an_error(self):
        # A discarded key leaves the coverage; a discarded name still on
        # disk stays an EXTRA (the quarantined file is still sitting
        # there, and the assert must name it).
        self._file("Kept.epub")
        self._write_map(
            {
                "Kept.epub": {"title": "Kept", "authors": ["A"]},
                "Gone.epub": {"title": "Gone", "authors": ["A"]},
            }
        )
        rc, spawn, out, err = self._run_main(
            "--backup-dir", str(self.backup), "--discard", "Gone.epub"
        )
        self.assertEqual(rc, 0)
        self.assertIn("1 discarded", out)
        self.assertEqual(
            [c[2] for c in self._spawned_cmds(spawn)], [str(self.target / "Kept.epub")]
        )
        rc, spawn, out, err = self._run_main(
            "--backup-dir", str(self.backup), "--discard", "Kept.epub"
        )
        self.assertEqual(rc, 2)
        self.assertIn("Kept.epub", err)

    def test_unwritable_results_dir_degrades_to_a_named_error(self):
        # The stamps already ran; a dead results path must not traceback
        # over the summary or flip the exit contract (setup trouble is a
        # named error, never a traceback).
        self._file("Solo.epub")
        self._write_map({"Solo.epub": {"title": "Solo", "authors": ["A"]}})
        rc, spawn, out, err = self._run_main(
            "--backup-dir",
            str(self.backup),
            "--results",
            str(self.root / "no-such-dir" / "res.json"),
        )
        self.assertEqual(rc, 0)
        self.assertIn("could not write the results file", err)
        self.assertIn("previewed 1, FAILED 0", out)

    def test_none_is_recognized_in_any_spelling(self):
        # A hand-edited table carrying Python-flavored "None" must omit
        # the flag exactly like "NONE": a literal-NONE publisher is the
        # phase-3 defect class.
        self._file("Case.epub")
        self._write_map(
            {
                "Case.epub": {
                    "title": "Case",
                    "authors": ["A"],
                    "publisher": "None",
                    "isbn": "none",
                }
            }
        )
        rc, spawn, out, err = self._run_main("--backup-dir", str(self.backup))
        self.assertEqual(rc, 0)
        (cmd,) = self._spawned_cmds(spawn)
        self.assertNotIn("--publisher", cmd)
        self.assertNotIn("--isbn", cmd)

    def test_unreadable_stamps_file_exits_2(self):
        self.stamps.write_text("{not json", encoding="utf-8")
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()) as err,
            mock.patch.object(
                sys, "argv", ["drive_stamps.py", str(self.stamps), str(self.target)]
            ),
            self.assertRaises(SystemExit) as raised,
        ):
            drive_stamps.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unreadable", err.getvalue())


if __name__ == "__main__":
    unittest.main()
