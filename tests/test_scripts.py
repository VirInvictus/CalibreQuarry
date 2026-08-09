"""Tests for the companion scripts' pure decision logic and DB sync.

The scripts in scripts/ are standalone (not a package), so they are loaded by
path. The Ghostscript and zip-reading I/O paths are not exercised; the Calibre
size sync is tested against a throwaway temporary SQLite fixture, never a live
metadata.db.
"""

import contextlib
import io
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
audit_epub = _load("audit_epub")


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


pagenum = audit_epub  # merged into audit_epub.py


class TestPageNumberValue(unittest.TestCase):
    def test_arabic_and_roman(self):
        self.assertEqual(pagenum.number_value("42"), 42)
        self.assertEqual(pagenum.number_value("xiv"), 14)
        self.assertEqual(pagenum.number_value("II"), 2)

    def test_rejects_non_numbers(self):
        self.assertIsNone(pagenum.number_value("Chapter"))
        self.assertIsNone(pagenum.number_value("12345"))  # >4 digits
        self.assertIsNone(pagenum.number_value("i"))  # lone roman i is too noisy
        self.assertIsNone(pagenum.number_value("42a"))


class TestIsDefective(unittest.TestCase):
    def _r(self, **over):
        r = {"n_hits": 10, "span": 0.9, "run": 5, "watermark": False, "examples": []}
        r.update(over)
        return r

    def test_clear_defect(self):
        self.assertTrue(pagenum.is_defective(self._r()))

    def test_too_few_hits(self):
        self.assertFalse(pagenum.is_defective(self._r(n_hits=4)))

    def test_localized_cluster_dropped_by_span(self):
        # a footnote-poem / scraped-comment cluster: many hits, tiny span
        self.assertFalse(pagenum.is_defective(self._r(n_hits=20, span=0.02)))


class TestPageNumberScan(unittest.TestCase):
    """End-to-end scan() over synthetic EPUBs: a baked-in conversion flags, a
    clean chapter-numbered book does not."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_baked_page_numbers_flag(self):
        # a page number wedged between an unfinished paragraph and its lowercase
        # continuation, repeated through the book: the real defect.
        para_a = "<p>" + ("the quick brown fox jumped over " * 6) + "and then</p>"
        para_b = "<p>" + ("continued in lowercase as the sentence ran on " * 6) + "</p>"
        body = "".join(f"{para_a}<p>{n}</p>{para_b}" for n in range(1, 8))
        with tempfile.TemporaryDirectory() as tmp:
            r = pagenum.scan_pagenumbers(self._epub(tmp, body))
        self.assertGreaterEqual(r["n_hits"], 5)
        self.assertTrue(pagenum.is_defective(r))

    def test_clean_chapter_numbers_do_not_flag(self):
        # a number that opens a chapter (next text is a fresh capitalized
        # sentence) is legitimate, not baked.
        chapter = "<p>" + ("A clean chapter of ordinary prose ends here. " * 6) + "</p>"
        body = "".join(f"<p>{n}</p>{chapter}" for n in range(1, 12))
        with tempfile.TemporaryDirectory() as tmp:
            r = pagenum.scan_pagenumbers(self._epub(tmp, body))
        self.assertFalse(pagenum.is_defective(r))


emptytext = audit_epub  # merged into audit_epub.py


class TestVisibleChars(unittest.TestCase):
    def test_strips_tags_scripts_and_styles(self):
        html = (
            "<style>p{color:red}</style><p>Hello <b>world</b></p>"
            "<script>var x = 1</script>"
        )
        self.assertEqual(emptytext._visible_chars(html), len("Hello world"))

    def test_decodes_entities(self):
        self.assertEqual(emptytext._visible_chars("<p>a &amp; b</p>"), len("a & b"))


class TestEmptyTextScan(unittest.TestCase):
    """End-to-end scan() over synthetic EPUBs: a content-less stub is EMPTY,
    a real-text book is OK, a short work is THIN (advisory)."""

    CONTAINER = (
        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="content.opf" '
        'media-type="application/oebps-package+xml"/></rootfiles></container>'
    )
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="text.xhtml" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_empty_stub_flagged(self):
        # a Bookmate-style stub: a single cover image, no body text
        body = '<p><img src="cover.png"/></p>'
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(r["chars"], 0)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "EMPTY")

    def test_full_text_ok(self):
        body = "<p>" + ("Real prose that fills the book. " * 1000) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")

    def test_thin_is_advisory(self):
        body = "<p>" + ("short story prose. " * 300) + "</p>"  # ~5700 chars
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, body))
        self.assertEqual(emptytext.classify(r, 2000, 20000), "THIN")


class TestPctDecode(unittest.TestCase):
    def test_reserved_char(self):
        self.assertEqual(
            audit_epub._pct_decode("Text/CR%21X_split.html"), "Text/CR!X_split.html"
        )

    def test_multibyte_utf8(self):
        # 'ö' is the two-byte run %C3%B6, which must decode together, not per-byte
        self.assertEqual(audit_epub._pct_decode("a%C3%B6b"), "aöb")

    def test_invalid_escape_left_literal(self):
        self.assertEqual(audit_epub._pct_decode("50%-off"), "50%-off")


class TestPercentEncodedSpine(unittest.TestCase):
    """Regression: a spine doc whose archive name has a reserved char is
    referenced percent-encoded in the OPF (Sigil/calibre emit '%21' for '!').
    The href must be decoded before matching the namelist, or a text-full book
    resolves to nothing and reads as EMPTY (the Serpent Sea false positive)."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = (
        '<package xmlns="http://www.idpf.org/2007/opf">'
        "<manifest>"
        '<item id="c1" href="Text/CR%21RT_split_001.html" media-type="application/xhtml+xml"/>'
        "</manifest>"
        '<spine><itemref idref="c1"/></spine></package>'
    )

    def _epub(self, tmp):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        body = "<p>" + ("Real prose that fills the book. " * 1000) + "</p>"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("Text/CR!RT_split_001.html", f"<html><body>{body}</body></html>")
        return p

    def test_encoded_href_resolves_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp))
        self.assertGreater(r["chars"], 20000)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")


class TestPlaceholderExport(unittest.TestCase):
    """Partial / placeholder exports: most chapters are an identical stub (a DRM
    'content unavailable' notice) while one or two real chapters push the
    whole-book char count over the THIN floor, so the total-char check is fooled.
    classify() must call these PARTIAL; a full book with many DISTINCT small
    section dividers must stay OK (the false-positive guard)."""

    CONTAINER = TestEmptyTextScan.CONTAINER

    def _epub(self, tmp, docs):
        # docs: list of (name, body_html); spine follows the list order
        import zipfile as zf

        manifest = "".join(
            f'<item id="d{i}" href="{n}" media-type="application/xhtml+xml"/>'
            for i, (n, _) in enumerate(docs)
        )
        spine = "".join(f'<itemref idref="d{i}"/>' for i in range(len(docs)))
        opf = (
            '<package xmlns="http://www.idpf.org/2007/opf">'
            f"<manifest>{manifest}</manifest><spine>{spine}</spine></package>"
        )
        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", opf)
            for n, body in docs:
                z.writestr(n, f"<html><body>{body}</body></html>")
        return p

    def test_drm_signature_is_partial(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        stub = "<p>sorry something went wrong loading your content. Contact support@bookshout.com</p>"
        docs = [("c0.xhtml", real)] + [(f"c{i}.xhtml", stub) for i in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertGreater(r["chars"], 20000)  # would otherwise clear the THIN floor
        self.assertTrue(r["placeholder_sig"])
        self.assertEqual(emptytext.classify(r, 2000, 20000), "PARTIAL")

    def test_repeated_stub_without_signature_is_partial(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        stub = "<p>This chapter is not included in this edition preview.</p>"
        docs = [("c0.xhtml", real)] + [(f"c{i}.xhtml", stub) for i in range(1, 12)]
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertFalse(r["placeholder_sig"])
        self.assertGreaterEqual(r["stub_docs"], 3)
        self.assertEqual(emptytext.classify(r, 2000, 20000), "PARTIAL")

    def test_distinct_small_dividers_stay_ok(self):
        real = "<p>" + ("Real prose. " * 3000) + "</p>"
        docs = [("c0.xhtml", real)] + [
            (f"d{i}.xhtml", f"<p>Part {i}: a distinct section divider heading.</p>")
            for i in range(12)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            r = emptytext.scan_emptytext(self._epub(tmp, docs))
        self.assertLess(r["stub_docs"], 3)  # distinct text, so no repeated stub
        self.assertEqual(emptytext.classify(r, 2000, 20000), "OK")


ocr = audit_epub  # fourth analyzer, merged into audit_epub.py


class TestOcrSplitDetection(unittest.TestCase):
    """End-to-end scan_ocr() over synthetic EPUBs: a mid-sentence paragraph
    split counts; dialogue fragments and scene breaks do not."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = TestEmptyTextScan.OPF

    def _epub(self, tmp, body):
        import zipfile as zf

        p = pathlib.Path(tmp) / "t.epub"
        with zf.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER)
            z.writestr("content.opf", self.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return p

    def test_mid_sentence_split_counts(self):
        # the Jingo defect: a paragraph ends without terminal punctuation
        # (here on a function word, the line-wrap signature) and the next
        # starts lowercase, mid-sentence
        a = (
            "<p>"
            + ("He stared across the harbour and " * 5)
            + "could see the shape of</p>"
        )
        b = "<p>another boat, " + ("moving through the fog " * 5) + "slowly.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertEqual(r["splits"], 40)
        self.assertGreaterEqual(r["split_rate"], ocr.OCR_FLAG_RATE)
        self.assertEqual(r["func_frac"], 1.0)
        self.assertTrue(ocr.is_ocr_damaged(r))

    def test_image_interrupted_pair_is_cleared(self):
        # a formula/figure between the fragments renders fine; not a split
        a = "<p>" + ("The channel capacity is given by the value " * 4) + "shown by</p>"
        img = '<div><img src="eq1.png"/></div>'
        b = "<p>where the terms " + ("are defined in the usual way " * 4) + "here.</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + img + b) * 40))
        self.assertEqual(r["splits"], 0)

    def test_clause_boundary_style_measures_low_func_frac(self):
        # deliberately unpunctuated literary prose (Fosse / Evaristo shape):
        # splits abound but end at clause boundaries, so func_frac stays low
        # and the book is not called damaged
        a = "<p>" + ("she thinks back to when she started " * 5) + "out in theatre</p>"
        b = (
            "<p>when she and her running mate "
            + ("developed a reputation " * 5)
            + "</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + b) * 40))
        self.assertGreater(r["splits"], 0)
        self.assertLess(r["func_frac"], ocr.OCR_FUNC_MIN)
        self.assertFalse(ocr.is_ocr_damaged(r))

    def test_dialogue_fragment_is_not_a_split(self):
        # "'Course not." starts with a quote, not a lowercase letter
        prose = "<p>" + ("Ordinary narrative prose carries on here. " * 5) + "</p>"
        body = (prose + "<p>'Course not,' said Nobby.</p>") * 20
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, body))
        self.assertEqual(r["splits"], 0)

    def test_scene_break_is_not_a_split(self):
        # an unpunctuated paragraph end followed by a scene-break marker and a
        # fresh capitalized paragraph: a boundary, not a split
        a = "<p>" + ("The chapter wound down as the light faded " * 5) + "and so on</p>"
        marker = "<p>* * *</p>"
        b = "<p>Morning came bright and early. " + ("The day began anew. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, (a + marker + b) * 20))
        self.assertEqual(r["splits"], 0)

    def test_clean_prose_measures_zero(self):
        body = "<p>" + ("A clean paragraph ends with a period. " * 5) + "</p>"
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, body * 40))
        self.assertEqual(r["splits"], 0)
        self.assertFalse(ocr.is_ocr_damaged(r))

    def test_side_signals(self):
        prose = "<p>" + ("Filler prose to give the book body text. " * 5) + "</p>"
        damaged = (
            "<p>That bottom–feedin' scum said ' 'Course, guv.' "
            "They walked through AnkhMorpork, the city of Ankh-Morpork.</p>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            r = ocr.scan_ocr(self._epub(tmp, prose * 10 + damaged))
        self.assertEqual(r["en_dash_words"], 1)
        self.assertEqual(r["doubled_quotes"], 1)
        self.assertEqual(r["glued"], ["AnkhMorpork~Ankh-Morpork"])


class TestIsOcrDamaged(unittest.TestCase):
    """Threshold boundaries on the FLAG gate."""

    def _r(self, **over):
        r = {
            "paras": 1000,
            "splits": ocr.OCR_MIN_SPLITS,
            "split_rate": ocr.OCR_FLAG_RATE,
            "func_frac": ocr.OCR_FUNC_MIN,
        }
        r.update(over)
        return r

    def test_at_threshold_flags(self):
        self.assertTrue(ocr.is_ocr_damaged(self._r()))

    def test_rate_just_below_threshold_passes(self):
        self.assertFalse(
            ocr.is_ocr_damaged(self._r(split_rate=ocr.OCR_FLAG_RATE * 0.99))
        )

    def test_too_few_splits_passes(self):
        self.assertFalse(
            ocr.is_ocr_damaged(self._r(splits=ocr.OCR_MIN_SPLITS - 1, split_rate=0.5))
        )

    def test_too_few_paragraphs_passes(self):
        # fragmentary short works never have enough paragraphs for the rate
        # to mean anything
        self.assertFalse(
            ocr.is_ocr_damaged(
                self._r(paras=ocr.OCR_MIN_PARAS - 1, splits=40, split_rate=0.9)
            )
        )

    def test_clause_boundary_splits_pass(self):
        # a high split rate with a low function-word fraction is deliberate
        # style, not damage
        self.assertFalse(
            ocr.is_ocr_damaged(
                self._r(splits=200, split_rate=0.4, func_frac=ocr.OCR_FUNC_MIN * 0.5)
            )
        )


class TestAllIncludesOcr(unittest.TestCase):
    """`all` runs the ocr analyzer inside the same single decompression pass."""

    def test_all_tuple_has_ocr(self):
        self.assertIn("ocr", audit_epub.ALL)

    def test_directory_all_run_reports_ocr(self):
        import contextlib as cl
        import io

        a = (
            "<p>"
            + ("He stared across the harbour and " * 8)
            + "could see the shape of</p>"
        )
        b = "<p>another boat, " + ("moving through the fog " * 8) + "slowly.</p>"
        body = (a + b) * 40  # long enough that emptytext stays OK
        with tempfile.TemporaryDirectory() as tmp:
            import zipfile as zf

            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", TestEmptyTextScan.CONTAINER)
                z.writestr("content.opf", TestEmptyTextScan.OPF)
                z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
            buf = io.StringIO()
            with cl.redirect_stdout(buf):
                rc = audit_epub.run_directory(
                    pathlib.Path(tmp), list(audit_epub.ALL), 2000, 20000
                )
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertIn("ocr", out)
        self.assertIn("REVIEW", out)


class TestVisibleTextCache(unittest.TestCase):
    """emptytext and ocr both need the book's rendered text. Under `all` they
    each used to strip tags over the whole book independently, which is the
    expensive half of a pass whose whole point is touching each EPUB once."""

    def _book(self, tmp):
        import zipfile

        p = pathlib.Path(tmp) / "t.epub"
        body = "<p>" + ("Some ordinary prose here. " * 40) + "</p>"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", TestEmptyTextScan.CONTAINER)
            z.writestr("content.opf", TestEmptyTextScan.OPF)
            z.writestr("text.xhtml", f"<html><body>{body}</body></html>")
        return audit_epub.load_book(p)

    def test_text_is_extracted_once_and_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = self._book(tmp)
            calls = []
            real = audit_epub._visible_text
            audit_epub._visible_text = lambda html: (calls.append(1), real(html))[1]
            try:
                audit_epub.analyze_emptytext(book)
                after_first = len(calls)
                audit_epub.analyze_ocr(book)
                self.assertEqual(len(calls), after_first)  # ocr reused the cache
            finally:
                audit_epub._visible_text = real

    def test_cached_text_matches_a_direct_extraction(self):
        with tempfile.TemporaryDirectory() as tmp:
            book = self._book(tmp)
            direct = [
                audit_epub._visible_text(book.docs.get(d, "")) for d in book.spine
            ]
            self.assertEqual(book.visible_texts(), direct)


class TestAuditEpubConnectRo(unittest.TestCase):
    """Library mode used to open metadata.db with no lock handling at all, so
    a run while Calibre held the database died with a traceback."""

    def _library(self, tmp):
        path = pathlib.Path(tmp) / "metadata.db"
        con = sqlite3.connect(path)
        con.executescript(
            "CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT);"
            "INSERT INTO books VALUES (1, 'T', 'A/T (1)');"
        )
        con.commit()
        con.close()
        return path

    def test_unlocked_db_is_read_directly(self):
        with tempfile.TemporaryDirectory() as tmp:
            con, tmpdir = audit_epub.connect_ro(self._library(tmp))
            try:
                self.assertIsNone(tmpdir)
                self.assertEqual(con.execute("SELECT id FROM books").fetchone()[0], 1)
            finally:
                con.close()

    def test_a_locked_db_falls_back_to_a_snapshot(self):
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            path = self._library(tmp)
            locker = sqlite3.connect(path)
            locker.execute("BEGIN EXCLUSIVE")
            locker.execute("INSERT INTO books VALUES (2, 'T2', 'A/T2 (2)')")
            real_connect = sqlite3.connect

            def quick_connect(*a, **kw):
                kw.setdefault("timeout", 0.1)  # do not sit out the 5s busy wait
                return real_connect(*a, **kw)

            try:
                with mock.patch.object(
                    audit_epub.sqlite3, "connect", side_effect=quick_connect
                ):
                    con, tmpdir = audit_epub.connect_ro(path)
                try:
                    self.assertIsNotNone(tmpdir)
                    self.assertEqual(
                        con.execute("SELECT id FROM books").fetchone()[0], 1
                    )
                finally:
                    con.close()
                    shutil.rmtree(tmpdir, ignore_errors=True)
            finally:
                locker.rollback()
                locker.close()


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


class TestContentSections(unittest.TestCase):
    """An injection signature is a defect regardless of the expected-foreign
    flag (regression: a signature hit on a declared-foreign book printed
    "(expected-foreign)" and "0 file(s) need review" while still failing the
    run with exit code 1)."""

    def test_signature_on_expected_foreign_book_counts(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = audit_epub._content_sections(
                [],
                [],
                [(101, "Some Foreign Book", "fiction", True, "importknig signature")],
            )
        out = buf.getvalue()
        self.assertEqual(rc, 1)
        self.assertNotIn("expected-foreign", out)
        self.assertIn("1 file(s) need review", out)


class TestLoadBookCorruptEntry(unittest.TestCase):
    """A spine document with a corrupted archive entry (bad CRC) reads as
    empty text instead of crashing the whole book."""

    CONTAINER = TestEmptyTextScan.CONTAINER
    OPF = TestEmptyTextScan.OPF

    def test_bad_crc_entry_reads_empty(self):
        import struct
        import zipfile as zf

        with tempfile.TemporaryDirectory() as tmp:
            p = pathlib.Path(tmp) / "t.epub"
            with zf.ZipFile(p, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
                z.writestr("META-INF/container.xml", self.CONTAINER)
                z.writestr("content.opf", self.OPF)
                z.writestr("text.xhtml", "<html><body><p>real prose</p></body></html>")
            with zf.ZipFile(p) as z:
                offset = z.getinfo("text.xhtml").header_offset
            with open(p, "r+b") as f:
                f.seek(offset + 26)
                nlen, elen = struct.unpack("<HH", f.read(4))
                f.seek(offset + 30 + nlen + elen + 10)
                byte = f.read(1)
                f.seek(-1, os.SEEK_CUR)
                f.write(bytes([byte[0] ^ 0xFF]))
            book = audit_epub.load_book(p)
        self.assertEqual(book.spine, ["text.xhtml"])
        self.assertEqual(book.docs["text.xhtml"], "")


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
