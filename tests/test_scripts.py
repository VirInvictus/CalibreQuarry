"""Tests for the companion scripts' pure decision logic and DB sync.

The scripts in scripts/ are standalone (not a package), so they are loaded by
path. The Ghostscript and zip-reading I/O paths are not exercised; the Calibre
size sync is tested against a throwaway temporary SQLite fixture, never a live
metadata.db.
"""

import contextlib
import importlib.util
import os
import pathlib
import shutil
import sqlite3
import tempfile
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compress_pdf = _load("compress_pdf")
audit_epub = _load("audit_epub_content")


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


class TestScriptOf(unittest.TestCase):
    def test_known_scripts(self):
        self.assertEqual(audit_epub.script_of(0x0410), "Cyrillic")
        self.assertEqual(audit_epub.script_of(0x4E2D), "CJK-Han")
        self.assertEqual(audit_epub.script_of(0x0627), "Arabic")

    def test_latin_is_none(self):
        self.assertIsNone(audit_epub.script_of(ord("a")))


class TestFindings(unittest.TestCase):
    def _result(self, **over):
        r = {
            "lang": "en",
            "scripts": {},
            "nonlatin": 0,
            "nonlatin_frac": 0.0,
            "ratios": {
                "en": 0.30,
                "pt": 0.05,
                "de": 0.04,
                "fr": 0.03,
                "es": 0.03,
                "it": 0.03,
                "nl": 0.02,
            },
            "best": "en",
            "nwords": 4000,
            "signature": False,
        }
        r.update(over)
        return r

    def test_clean_english(self):
        self.assertEqual(audit_epub.findings(self._result()), [])

    def test_non_latin(self):
        cats = [
            c
            for c, _ in audit_epub.findings(
                self._result(nonlatin=500, nonlatin_frac=0.5, scripts={"Cyrillic": 500})
            )
        ]
        self.assertIn("NON-LATIN SCRIPT", cats)

    def test_latin_foreign(self):
        cats = [
            c
            for c, _ in audit_epub.findings(
                self._result(
                    best="pt",
                    ratios={
                        "pt": 0.30,
                        "en": 0.05,
                        "de": 0.02,
                        "fr": 0.02,
                        "es": 0.10,
                        "it": 0.08,
                        "nl": 0.02,
                    },
                )
            )
        ]
        self.assertIn("LATIN-SCRIPT FOREIGN", cats)

    def test_injection_signature(self):
        cats = [c for c, _ in audit_epub.findings(self._result(signature=True))]
        self.assertIn("INJECTION SIGNATURE", cats)


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
        compress_pdf.update_calibre_size(root, stray, 1)  # must not raise


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


class TestResolveLibraryRoot(unittest.TestCase):
    """audit_epub_content finds the library next to the script or in the cwd."""

    @contextlib.contextmanager
    def _cwd(self, path):
        old = os.getcwd()
        os.chdir(path)
        try:
            yield
        finally:
            os.chdir(old)

    def test_cwd_with_db_resolves(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_root_"))
        (tmp / "metadata.db").write_bytes(b"")
        with self._cwd(tmp):
            root = audit_epub.resolve_library_root()
        self.assertIsNotNone(root)
        self.assertEqual(root.resolve(), tmp.resolve())

    def test_no_db_anywhere_is_none(self):
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="cq_empty_"))
        with self._cwd(tmp):
            self.assertIsNone(audit_epub.resolve_library_root())


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
