"""Tests for the companion scripts' pure decision logic and DB sync.

The scripts in scripts/ are standalone (not a package), so they are loaded by
path. The Ghostscript and zip-reading I/O paths are not exercised; the Calibre
size sync is tested against a throwaway temporary SQLite fixture, never a live
metadata.db.
"""

import contextlib
import importlib.util
import io
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()

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
        with mock.patch(
            "subprocess.run", return_value=mock.Mock(returncode=1)
        ) as run:
            self.assertFalse(fetch_library_codes.calibre_running())
        argv = run.call_args.args[0]
        self.assertEqual(argv[0], "pgrep")
        self.assertNotIn("-f", argv)

    def test_probe_pattern_reaches_the_parallel_workers(self):
        # calibre-parallel's comm truncates to "calibre-paralle" (15 chars),
        # which the old exact-match -x "calibre" could never see; the anchored
        # prefix must. Verified live 2026-08-30 against a running Calibre with
        # four workers.
        with mock.patch(
            "subprocess.run", return_value=mock.Mock(returncode=0)
        ) as run:
            self.assertTrue(fetch_library_codes.calibre_running())
        self.assertTrue(run.call_args.args[0][-1].startswith("^calibre"))

    def test_probe_timeout_assumes_calibre_is_running(self):
        # The gate fails closed: a hung probe refuses --apply, because
        # guessing wrong writes to a live database.
        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="pgrep", timeout=1)

        with mock.patch("subprocess.run", side_effect=hang):
            self.assertTrue(fetch_library_codes.calibre_running())
