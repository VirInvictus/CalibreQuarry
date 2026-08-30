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
import sys
import tempfile
import unittest
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


screen_duplicate = _load("screen_duplicate")
stamp_pdf = _load("stamp_pdf")


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
            ],
        )
        c.execute("CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT)")
        c.execute(
            "INSERT INTO authors VALUES (1, 'Karl Marx'), (2, 'Ursula K. Le Guin')"
        )
        c.execute(
            "CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY,"
            " book INTEGER, author INTEGER)"
        )
        c.executemany(
            "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
            [(1, 1), (2, 2)],
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
        hits = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([h["id"] for h in hits], [1])
        self.assertEqual(hits[0]["formats"], ["EPUB"])
        self.assertEqual(hits[0]["size"], 2048)

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
        hits = screen_duplicate._library_hits(self.db, fields)
        self.assertEqual([h["id"] for h in hits], [1])

    def test_different_author_is_not_a_hit(self):
        # Tight `=` author matching: Mark Lawrence is not T. E. Lawrence.
        fields = {
            "file": "/dl/x.epub",
            "title": "The Broken Empire",
            "authors": ["Mark Lawrence"],
            "isbn": "",
        }
        self.assertEqual(screen_duplicate._library_hits(self.db, fields), [])

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
                "--isbn",
                "9780140445688",
            )
        self.assertEqual(rc, 0)
        run.assert_called_once()
        called = run.call_args.args[0]
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
        run.assert_called_once()  # no re-fighting

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
