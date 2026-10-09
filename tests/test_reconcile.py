"""Tests for reconcile_file_metadata.py pure logic: normalisation, parsing,
and the per-format field diff. No DB or subprocess; the script lives in
scripts/, so it is imported by path."""

import contextlib
import importlib.util
import io
import re
import shutil
import sys
import sqlite3
import pathlib
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "reconcile_file_metadata",
    Path(__file__).resolve().parent.parent / "scripts" / "reconcile_file_metadata.py",
)
assert _spec and _spec.loader
rfm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rfm)

try:
    from cquarry_cli import epubdates as epd
except ImportError:
    # the script's exec above already put the repo's src/ on sys.path
    from cquarry_cli import epubdates as epd


def db_record(**over):
    base = {
        "id": 1,
        "title": "The Hobbit",
        "authors": ["J.R.R. Tolkien"],
        "series": "Middle-earth",
        "series_index": "1",
        "publisher": "Allen & Unwin",
        "pubdate": "1937-09-21 00:00:00+00:00",
        "languages": ["eng"],
        "tags": ["Fic.Fantasy.Classic"],
        "identifiers": {"isbn": "9780000000001"},
        "comments": "<p>A hobbit's tale.</p>",
        "formats": {},
    }
    base.update(over)
    return base


def file_meta(**over):
    base = {
        "title": "The Hobbit",
        "author(s)": "J.R.R. Tolkien [Tolkien, J.R.R.]",
        "series": "Middle-earth #1",
        "publisher": "Allen & Unwin",
        "published": "1937-09-21T00:00:00+00:00",
        "languages": "eng",
        "tags": "Fic.Fantasy.Classic",
        "identifiers": "isbn:9780000000001",
        "comments": "A hobbit's tale.",
    }
    base.update(over)
    return base


class TestNormalisation(unittest.TestCase):
    def test_norm_date_strips_time_and_sentinel(self):
        self.assertEqual(rfm.norm_date("2017-05-25T00:00:00+00:00"), "2017-05-25")
        self.assertEqual(rfm.norm_date("2017-05-25 00:00:00+00:00"), "2017-05-25")
        self.assertEqual(rfm.norm_date("0101-01-01 00:00:00+00:00"), "")
        self.assertEqual(rfm.norm_date(None), "")
        self.assertEqual(rfm.norm_date("not a date"), "")

    def test_norm_comment_ignores_html_and_case(self):
        self.assertEqual(
            rfm.norm_comment("<p>Hello   World</p>"), rfm.norm_comment("hello world")
        )

    def test_norm_set(self):
        self.assertEqual(rfm.norm_set(["A", " a ", ""]), frozenset({"a"}))


class TestParsing(unittest.TestCase):
    def test_parse_series(self):
        self.assertEqual(rfm.parse_series("Card Mage #1"), ("card mage", "1"))
        self.assertEqual(rfm.parse_series("Foo #2.0"), ("foo", "2"))
        self.assertEqual(rfm.parse_series("Bar #0.5"), ("bar", "0.5"))
        self.assertEqual(rfm.parse_series(""), ("", ""))
        self.assertEqual(rfm.parse_series("No Index"), ("no index", ""))

    def test_parse_identifiers(self):
        self.assertEqual(
            rfm.parse_identifiers("isbn:9780123456789, amazon:B0ABC"),
            {"isbn": "9780123456789", "amazon": "b0abc"},
        )
        self.assertEqual(rfm.parse_identifiers(None), {})

    def test_parse_identifiers_keeps_spaces_inside_a_value(self):
        # A Library of Congress call number contains spaces. Splitting on
        # whitespace truncated it at the first space, so the file never matched
        # the database and the book stayed permanently "drifted".
        self.assertEqual(
            rfm.parse_identifiers(
                "google:95-xBXd4vn8C, isbn:9781591841661, lcc:BF637.S4 G63 2007"
            ),
            {
                "google": "95-xbxd4vn8c",
                "isbn": "9781591841661",
                "lcc": "bf637.s4 g63 2007",
            },
        )
        # A lone trailing value with no colon must not become a bogus entry.
        self.assertEqual(
            rfm.parse_identifiers("lcc:QA76.6 .C662 2009"),
            {"lcc": "qa76.6 .c662 2009"},
        )

    def test_djvused_unescape(self):
        # djvused prints non-ASCII as octal byte escapes; decode to UTF-8.
        self.assertEqual(rfm.djvused_unescape("Gr\\303\\266tschel"), "Grötschel")
        self.assertEqual(
            rfm.djvused_unescape("L\\303\\241szl\\303\\263 Lov\\303\\241sz"),
            "László Lovász",
        )
        self.assertEqual(rfm.djvused_unescape("Plain ASCII"), "Plain ASCII")

    def test_parse_id_list(self):
        self.assertEqual(rfm.parse_id_list("6688,6690"), {6688, 6690})
        self.assertEqual(rfm.parse_id_list("1, 2 3"), {1, 2, 3})
        self.assertIsNone(rfm.parse_id_list("6688,foo"))

    def test_is_repairable_pdf_error(self):
        # A broken cross-reference table is qpdf-repairable; unrelated failures
        # (e.g. permissions) are not, so --repair-pdf should leave them alone.
        self.assertTrue(rfm.is_repairable_pdf_error("Error: Invalid xref table"))
        self.assertTrue(rfm.is_repairable_pdf_error("warning: file is damaged"))
        self.assertFalse(rfm.is_repairable_pdf_error("Error: Permission denied"))
        self.assertFalse(rfm.is_repairable_pdf_error(""))


class TestDiff(unittest.TestCase):
    def test_in_sync_epub_has_no_drift(self):
        self.assertEqual(rfm.diff_fields(db_record(), file_meta(), "EPUB"), [])

    def test_title_drift(self):
        self.assertEqual(
            rfm.diff_fields(
                db_record(), file_meta(title="The Hobbit, Revised"), "EPUB"
            ),
            ["title"],
        )

    def test_author_sort_suffix_and_separators_ignored(self):
        # "[sort]" stripped, "&" split: still a match -> no drift.
        db = db_record(authors=["Neil Gaiman", "Terry Pratchett"])
        fm = file_meta(author_s="Neil Gaiman & Terry Pratchett [Gaiman, Neil]")
        fm["author(s)"] = fm.pop("author_s")
        self.assertNotIn("authors", rfm.diff_fields(db, fm, "EPUB"))

    def test_author_comma_is_part_of_the_name(self):
        # Regression: splitting on commas turned "Martin Luther King, Jr."
        # into two bogus authors, so the book reported as drifted forever,
        # even immediately after a successful re-embed.
        db = db_record(authors=["Martin Luther King, Jr."])
        fm = file_meta()
        fm["author(s)"] = "Martin Luther King, Jr."
        self.assertNotIn("authors", rfm.diff_fields(db, fm, "EPUB"))

    def test_author_semicolon_separator(self):
        # exiftool writes multi-author PDFs as "A & B"; ebook-meta can also
        # surface "A; B; C". Both separators must split to the same set.
        db = db_record(authors=["Brian Goetz", "Tim Peierls", "Joshua Bloch"])
        fm = file_meta()
        fm["author(s)"] = "Brian Goetz; Tim Peierls; Joshua Bloch"
        self.assertNotIn("authors", rfm.diff_fields(db, fm, "EPUB"))

    def test_pubdate_compares_date_only(self):
        self.assertEqual(
            rfm.diff_fields(db_record(), file_meta(published="1937-09-21"), "EPUB"), []
        )

    def test_identifier_drift_on_wrong_value(self):
        self.assertIn(
            "identifiers",
            rfm.diff_fields(
                db_record(), file_meta(identifiers="isbn:9789999999999"), "EPUB"
            ),
        )

    def test_identifier_missing_from_file_is_drift(self):
        # DB has an isbn the file lacks entirely.
        self.assertIn(
            "identifiers",
            rfm.diff_fields(db_record(), file_meta(identifiers="goodreads:1"), "EPUB"),
        )

    def test_extra_file_identifiers_are_not_drift(self):
        # File carries the curated isbn plus its own urn:uuid and an ean; the
        # curated id is present, so no drift (directional subset check).
        fm = file_meta(
            identifiers="uri:urn:uuid:abc, ean:4057664648839, isbn:9780000000001"
        )
        self.assertNotIn("identifiers", rfm.diff_fields(db_record(), fm, "EPUB"))

    def test_comment_html_insensitive(self):
        # File stores plain text, DB stores HTML of the same blurb -> no drift.
        self.assertNotIn("comments", rfm.diff_fields(db_record(), file_meta(), "EPUB"))

    def test_comment_truncated_by_ebook_meta_is_not_drift(self):
        # ebook-meta truncates long comments; a prefix of the DB text is fine.
        fm = file_meta()
        fm["comments"] = "A hobbit's"  # prefix of "A hobbit's tale."
        self.assertNotIn("comments", rfm.diff_fields(db_record(), fm, "EPUB"))

    def test_comment_empty_or_divergent_is_drift(self):
        fm = file_meta()
        fm["comments"] = ""
        self.assertIn("comments", rfm.diff_fields(db_record(), fm, "EPUB"))
        fm["comments"] = "A completely different blurb"
        self.assertIn("comments", rfm.diff_fields(db_record(), fm, "EPUB"))

    def test_pdf_ignores_tags_series_comments_and_pubdate(self):
        # PDF compares title/author/publisher only. Tags, series, comments, and
        # pubdate (timezone-fuzzy in PDF XMP) must not be reported for PDF.
        fm = file_meta(
            tags="Totally.Different.Tag", series="Other #9", published="1999-01-01"
        )
        fm["comments"] = "completely different blurb"
        self.assertEqual(rfm.diff_fields(db_record(), fm, "PDF"), [])

    def test_pdf_publisher_drift_caught(self):
        self.assertEqual(
            rfm.diff_fields(db_record(), file_meta(publisher="Wrong House"), "PDF"),
            ["publisher"],
        )

    def test_pdf_title_drift_still_caught(self):
        self.assertEqual(
            rfm.diff_fields(db_record(), file_meta(title="Wrong"), "PDF"), ["title"]
        )

    def test_djvu_only_title_and_author(self):
        # publisher drift ignored for DJVU; title drift caught.
        fm = file_meta(title="Wrong", publisher="Other")
        self.assertEqual(rfm.diff_fields(db_record(), fm, "DJVU"), ["title"])

    def test_missing_file_field_counts_as_drift(self):
        fm = file_meta()
        del fm["series"]
        self.assertIn("series", rfm.diff_fields(db_record(), fm, "EPUB"))


class TestPdfRepairCleanup(unittest.TestCase):
    """embed_pdf must delete the `.~qpdf-orig` backup that `qpdf --replace-input`
    leaves behind, so these full-size copies do not pile up in the library tree."""

    def _run(self, exiftool_results, qpdf_returncode, make_backup=True):
        with tempfile.TemporaryDirectory() as d:
            pdf = Path(d) / "book.pdf"
            pdf.write_bytes(b"%PDF-1.4\n")
            backup = pdf.with_name(pdf.name + ".~qpdf-orig")
            if make_backup:
                backup.write_bytes(b"old broken copy")
            with (
                mock.patch.object(rfm, "_run_exiftool", side_effect=exiftool_results),
                mock.patch.object(
                    rfm.subprocess,
                    "run",
                    return_value=SimpleNamespace(returncode=qpdf_returncode, stderr=""),
                ),
            ):
                ok = rfm.embed_pdf({"id": 1}, pdf, repair=True)
            return ok, backup.exists()

    def test_backup_removed_after_successful_repair(self):
        results = [
            SimpleNamespace(returncode=1, stderr="Error: Invalid xref table"),
            SimpleNamespace(returncode=0, stderr=""),  # retry after qpdf succeeds
        ]
        ok, backup_exists = self._run(results, qpdf_returncode=0)
        self.assertTrue(ok)
        self.assertFalse(backup_exists)

    def test_missing_backup_does_not_raise(self):
        # qpdf may not leave a backup (or it was already gone); unlink must no-op.
        results = [
            SimpleNamespace(returncode=1, stderr="warning: file is damaged"),
            SimpleNamespace(returncode=0, stderr=""),
        ]
        ok, _ = self._run(results, qpdf_returncode=3, make_backup=False)
        self.assertTrue(ok)


class TestVerifyEmbedded(unittest.TestCase):
    """The post-embed read-back (the #9177 class): embed_metadata exited 0
    while the EPUB kept its own dc:date, so the file stayed drifted forever
    with nothing on the record. verify_embedded re-diffs every claimed file."""

    def test_in_sync_after_embed_has_no_residual(self):
        with mock.patch.object(rfm, "file_metadata", return_value=file_meta()) as fm:
            residuals = rfm.verify_embedded([(db_record(), Path("/x/a.epub"), "EPUB")])
        self.assertEqual(residuals, [])
        fm.assert_called_once_with(Path("/x/a.epub"), "EPUB")

    def test_pubdate_still_old_is_a_residual(self):
        # The live shape: the writer reported success, the file kept its
        # original EPUB3 dc:date, the pubdate still differs.
        with mock.patch.object(
            rfm, "file_metadata", return_value=file_meta(published="2010-10-25")
        ):
            residuals = rfm.verify_embedded([(db_record(), Path("/x/a.epub"), "EPUB")])
        self.assertEqual(len(residuals), 1)
        bid, title, fmt, drift = residuals[0]
        self.assertEqual(bid, 1)
        self.assertEqual(fmt, "EPUB")
        self.assertEqual(drift, ["pubdate"])

    def test_unreadable_read_back_is_a_residual(self):
        # Silence must not stand in for a file nobody could re-read.
        with mock.patch.object(rfm, "file_metadata", return_value=None):
            residuals = rfm.verify_embedded([(db_record(), Path("/x/a.epub"), "EPUB")])
        self.assertEqual(len(residuals), 1)
        self.assertEqual(residuals[0][3], ["<unreadable after embed>"])


class TestVerifyWiring(unittest.TestCase):
    """main()'s verify-after-embed wiring: the function tests pin
    verify_embedded's branches; these drive main() past a mocked rc-0
    embed whose read-back still (or no longer) drifts, which is the
    #9177 shape the whole feature exists for."""

    def _library(self, tmp):
        root = pathlib.Path(tmp)
        con = sqlite3.connect(root / "metadata.db")
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT,
                author_sort TEXT, timestamp TEXT, pubdate TEXT, series_index REAL,
                path TEXT, uuid TEXT);
            CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
            CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT);
            CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
            CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT, item_order INT);
            CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
            CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
            CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
            CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
            CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT);
            CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT);
            CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
                uncompressed_size INT);
            """
        )
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "series_index,path,uuid) VALUES (1,'The Hobbit','Hobbit, The','T',"
            "'2024-01-01','1937-09-21 00:00:00+00:00',1.0,'a/b','u1')"
        )
        con.execute("INSERT INTO authors (id,name) VALUES (1,'J.R.R. Tolkien')")
        con.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
        con.execute("INSERT INTO languages (id,lang_code) VALUES (1,'eng')")
        con.execute(
            "INSERT INTO books_languages_link (book,lang_code,item_order) VALUES (1,1,0)"
        )
        # Match the module-level file_meta() helper's tag and isbn, so its
        # default dict reads as fully in-sync against this fixture.
        con.execute("INSERT INTO tags (id,name) VALUES (1,'Fic.Fantasy.Classic')")
        con.execute("INSERT INTO books_tags_link (book,tag) VALUES (1,1)")
        con.execute(
            "INSERT INTO identifiers (book,type,val) VALUES (1,'isbn','9780000000001')"
        )
        con.execute("INSERT INTO series (id,name) VALUES (1,'Middle-earth')")
        con.execute("INSERT INTO books_series_link (book,series) VALUES (1,1)")
        con.execute("INSERT INTO publishers (id,name) VALUES (1,'Allen & Unwin')")
        con.execute("INSERT INTO books_publishers_link (book,publisher) VALUES (1,1)")
        con.execute(
            "INSERT INTO comments (book,text) VALUES (1,'<p>A hobbit''s tale.</p>')"
        )
        con.execute(
            "INSERT INTO data (book,format,name,uncompressed_size) "
            "VALUES (1,'EPUB','The Hobbit',10)"
        )
        con.commit()
        con.close()
        book_dir = root / "a" / "b"
        book_dir.mkdir(parents=True)
        (book_dir / "The Hobbit.epub").write_bytes(b"epub")
        return root

    def _run_main(self, tmp, read_backs, normalize_result=True):
        root = self._library(tmp)
        argv = [
            "reconcile_file_metadata.py",
            str(root),
            "--apply",
        ]
        out = io.StringIO()
        err = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(rfm, "shutil") as _sh,
            mock.patch.object(rfm, "file_metadata", side_effect=read_backs),
            mock.patch.object(rfm, "embed_calibredb", return_value=True) as embed,
            mock.patch.object(
                rfm, "normalize_epub_dates", return_value=normalize_result
            ) as norm,
            mock.patch.object(rfm, "calibre_running", return_value=False),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            rc = rfm.main()
        return rc, out.getvalue(), embed.call_count, norm, err.getvalue()

    def test_in_sync_read_back_is_done(self):
        # First read (the drift scan) sees the old date; the post-embed
        # read-back sees the corrected one: a clean DONE, exit 0.
        drifted = file_meta(published="2010-10-25")
        fixed = file_meta()
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds, norm, err = self._run_main(tmp, [drifted, fixed])
        self.assertEqual(rc, 0, out + err)
        self.assertIn("DONE", out)
        self.assertIn("verified by read-back", out)
        self.assertEqual(embeds, 1)
        norm.assert_called_once()

    def test_still_drifted_read_back_is_a_residual_and_fails(self):
        # The #9177 shape: embed_metadata exits 0, the EPUB keeps its own
        # dc:date. The read-back catches it; the run fails with a RESIDUAL.
        drifted = file_meta(published="2010-10-25")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds, norm, err = self._run_main(
                tmp, [drifted, file_meta(published="2010-10-25")]
            )
        self.assertEqual(rc, 1, out + err)
        self.assertIn("RESIDUAL", out)
        self.assertIn("pubdate", out)
        self.assertEqual(embeds, 1)

    def test_normalization_targets_the_db_pubdate_at_utc_midnight(self):
        # The dc:date pass runs once per embedded EPUB with the database
        # pubdate reduced to its date at UTC midnight as a full ISO instant
        # (the canonical element value; see TestEpub3DateRoundTripLive for
        # why it is not date-only).
        drifted = file_meta(published="2010-10-25")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds, norm, err = self._run_main(tmp, [drifted, file_meta()])
        self.assertEqual(rc, 0, out + err)
        args = norm.call_args.args
        self.assertEqual(args[0].name, "The Hobbit.epub")
        self.assertEqual(args[1], "1937-09-21T00:00:00Z")

    def test_failed_normalization_fails_the_run(self):
        # An OPF the normalizer cannot rewrite is a named failure, not a
        # silent pass-through to the read-back.
        drifted = file_meta(published="2010-10-25")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds, norm, err = self._run_main(
                tmp, [drifted, file_meta()], normalize_result=False
            )
        self.assertEqual(rc, 1, out + err)
        self.assertIn("normalization failed", err)
        self.assertIn("The Hobbit.epub", err)


class TestEpubDateNormalization(unittest.TestCase):
    """The post-embed dc:date pass (issue #3, the #9177/#9635 live class).
    calibredb's EPUB2 writer rewrites only the earliest dc:date and leaves
    every other one standing, while calibre's EPUB2 reader reports the
    minimum: a file carrying a fetch-era run-clock value plus the publisher
    original read back as permanent pubdate drift that re-embedding never
    converged. The pass forces the OPF to exactly one canonical dc:date.
    The functions live in cquarry_cli.epubdates (run flush shares them);
    the script re-exports what it uses."""

    def test_script_reexports_are_the_module_objects(self):
        # Single source: the script must not carry a fork of the surgery.
        self.assertIs(rfm.norm_date, epd.norm_date)
        self.assertIs(rfm.canonical_date_target, epd.canonical_date_target)
        self.assertIs(rfm.normalize_epub_dates, epd.normalize_epub_dates)

    LIVE_OPF = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<package version="2.0" xmlns="http://www.idpf.org/2007/opf" '
        'unique-identifier="BookId">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">\n'
        "    <dc:title>The Great Change</dc:title>\n"
        '    <dc:creator opf:role="aut">Joe Abercrombie</dc:creator>\n'
        "    <dc:publisher>Subterranean Press</dc:publisher>\n"
        "    <dc:date>2023-09-28T07:00:00+00:00</dc:date>\n"
        "    <dc:date>2023-09-28T00:00:00+00:00</dc:date>\n"
        '    <dc:date opf:event="modification">2023-06-06</dc:date>\n'
        '    <dc:identifier id="BookId" opf:scheme="ISBN">9780857661349'
        "</dc:identifier>\n"
        '    <meta name="cover" content="cover-image"/>\n'
        "  </metadata>\n"
        "  <manifest>\n"
        '    <item id="cover-image" href="cover.jpg" media-type="image/jpeg"/>\n'
        "  </manifest>\n"
        "</package>\n"
    )

    CONTAINER_XML = (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )

    def dates_in(self, opf_text):
        return re.findall(r"<dc:date[^>]*>[^<]*</dc:date>", opf_text)

    def test_live_three_date_shape_collapses_to_one(self):
        out = epd.canonical_dc_dates(self.LIVE_OPF, "2023-09-28")
        self.assertIsNotNone(out)
        self.assertEqual(self.dates_in(out), ["<dc:date>2023-09-28</dc:date>"])
        # the canonical element sits where the publisher's first date sat,
        # on its own line at the same indentation
        self.assertIn(
            "    <dc:publisher>Subterranean Press</dc:publisher>\n"
            "    <dc:date>2023-09-28</dc:date>\n"
            "    <dc:identifier",
            out,
        )
        # nothing outside the date set moved
        for keep in (
            "<dc:title>The Great Change</dc:title>",
            '<meta name="cover" content="cover-image"/>',
            '<item id="cover-image" href="cover.jpg" media-type="image/jpeg"/>',
            "</package>\n",
        ):
            self.assertIn(keep, out)

    def test_already_canonical_file_is_byte_identical(self):
        opf = self.LIVE_OPF.replace(
            "    <dc:date>2023-09-28T07:00:00+00:00</dc:date>\n"
            "    <dc:date>2023-09-28T00:00:00+00:00</dc:date>\n"
            '    <dc:date opf:event="modification">2023-06-06</dc:date>\n',
            "    <dc:date>2023-09-28</dc:date>\n",
        )
        self.assertEqual(epd.canonical_dc_dates(opf, "2023-09-28"), opf)

    def test_second_pass_is_idempotent(self):
        once = epd.canonical_dc_dates(self.LIVE_OPF, "2023-09-28")
        self.assertEqual(epd.canonical_dc_dates(once, "2023-09-28"), once)

    def test_missing_target_removes_every_date(self):
        # '' is the unset/sentinel DB pubdate: the canonical shape is no
        # dc:date at all, matching how diff_fields compares.
        out = epd.canonical_dc_dates(self.LIVE_OPF, "")
        self.assertIsNotNone(out)
        self.assertEqual(self.dates_in(out), [])
        self.assertIn("<dc:publisher>Subterranean Press</dc:publisher>", out)
        self.assertIn("<dc:identifier", out)

    def test_no_dates_inserts_after_publisher(self):
        opf = self.LIVE_OPF
        opf = re.sub(r"    <dc:date[^>]*>[^<]*</dc:date>\n", "", opf)
        out = epd.canonical_dc_dates(opf, "2023-09-28")
        self.assertIsNotNone(out)
        self.assertIn(
            "    <dc:publisher>Subterranean Press</dc:publisher>\n"
            "    <dc:date>2023-09-28</dc:date>",
            out,
        )

    def test_insertion_without_dc_binding_is_refused(self):
        opf = self.LIVE_OPF.replace(' xmlns:dc="http://purl.org/dc/elements/1.1/"', "")
        opf = re.sub(r"    <dc:date[^>]*>[^<]*</dc:date>\n", "", opf)
        self.assertIsNone(epd.canonical_dc_dates(opf, "2023-09-28"))

    def test_no_metadata_block_is_refused(self):
        self.assertIsNone(epd.canonical_dc_dates("<html><body/></html>", "2023-09-28"))

    def test_epub3_dcterms_modified_survives(self):
        opf = self.LIVE_OPF.replace(
            '    <meta name="cover" content="cover-image"/>\n',
            '    <meta name="cover" content="cover-image"/>\n'
            '    <meta property="dcterms:modified">2023-07-01T00:00:00Z</meta>\n',
        )
        out = epd.canonical_dc_dates(opf, "2023-09-28")
        self.assertIn(
            '<meta property="dcterms:modified">2023-07-01T00:00:00Z</meta>', out
        )

    def _build_epub(self, root, opf_text):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER_XML)
            z.writestr("OEBPS/content.opf", opf_text)
        path = pathlib.Path(root) / "book.epub"
        path.write_bytes(buf.getvalue())
        return path

    def test_zip_rewrite_preserves_other_entries_and_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_epub(tmp, self.LIVE_OPF)
            before = zipfile.ZipFile(path)
            other = {
                n: before.read(n) for n in before.namelist() if n != "OEBPS/content.opf"
            }
            before.close()
            self.assertTrue(epd.normalize_epub_dates(path, "2023-09-28"))
            after = zipfile.ZipFile(path)
            self.assertEqual(after.namelist()[0], "mimetype")
            self.assertEqual(after.namelist(), list(other) + ["OEBPS/content.opf"])
            self.assertEqual(
                self.dates_in(after.read("OEBPS/content.opf").decode()),
                ["<dc:date>2023-09-28</dc:date>"],
            )
            for name, payload in other.items():
                self.assertEqual(after.read(name), payload)
            after.close()

    def test_canonical_zip_is_not_rewritten(self):
        opf = self.LIVE_OPF.replace(
            "    <dc:date>2023-09-28T07:00:00+00:00</dc:date>\n"
            "    <dc:date>2023-09-28T00:00:00+00:00</dc:date>\n"
            '    <dc:date opf:event="modification">2023-06-06</dc:date>\n',
            "    <dc:date>2023-09-28</dc:date>\n",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_epub(tmp, opf)
            raw = path.read_bytes()
            self.assertTrue(epd.normalize_epub_dates(path, "2023-09-28"))
            self.assertEqual(path.read_bytes(), raw)

    def test_unopenable_file_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "book.epub"
            path.write_bytes(b"epub")
            self.assertFalse(epd.normalize_epub_dates(path, "2023-09-28"))

    def test_zip_without_container_fails_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "book.epub"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("mimetype", "application/epub+zip")
            self.assertFalse(epd.normalize_epub_dates(path, "2023-09-28"))


class TestCanonicalDateTarget(unittest.TestCase):
    """The caller-facing policy over canonical_dc_dates's generic target:
    the database pubdate's date at UTC midnight as a full ISO instant."""

    def test_full_iso_utc_midnight_from_any_db_shape(self):
        self.assertEqual(
            epd.canonical_date_target("2020-10-01 07:00:00+00:00"),
            "2020-10-01T00:00:00Z",
        )
        self.assertEqual(
            epd.canonical_date_target("2020-10-01T23:15:00-04:00"),
            "2020-10-01T00:00:00Z",
        )
        self.assertEqual(
            epd.canonical_date_target("2020-10-01"), "2020-10-01T00:00:00Z"
        )

    def test_sentinel_and_garbage_stay_empty(self):
        self.assertEqual(epd.canonical_date_target("0101-01-01 00:00:00+00:00"), "")
        self.assertEqual(epd.canonical_date_target(None), "")
        self.assertEqual(epd.canonical_date_target("not a date"), "")


@unittest.skipUnless(shutil.which("ebook-meta"), "ebook-meta not on PATH")
class TestEpub3DateRoundTripLive(unittest.TestCase):
    """Issue #4, the five-book live class. calibre's EPUB3 reader nudges a
    date-only dc:date off month boundaries (fix_only_date: day 1 becomes
    day 2, a month's last day is pulled back one), so the 3.56.0 date-only
    canonical read back as the NEXT day through ebook-meta for every day-1
    pubdate and the read-back re-diff never converged (2020-10-01 in the
    file read 2020-10-02T00:00:00+00:00). The canonical target is a full
    ISO instant, which round-trips exactly; pinned against the real
    ebook-meta, which CI (no calibre) skips."""

    DAY_ONE_OPF = (
        "<?xml version='1.0' encoding='utf-8'?>\n"
        '<package version="3.0" xmlns="http://www.idpf.org/2007/opf" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" unique-identifier="uid">\n'
        "  <metadata>\n"
        '    <dc:identifier id="uid">urn:uuid:date-probe</dc:identifier>\n'
        "    <dc:title>Day One Probe</dc:title>\n"
        "    <dc:creator>Probe Author</dc:creator>\n"
        "    <dc:language>en</dc:language>\n"
        "    <dc:date>2020-10-01</dc:date>\n"
        '    <meta property="dcterms:modified">2020-10-01T23:59:59Z</meta>\n'
        "  </metadata>\n"
        '  <manifest><item id="c1" href="c1.xhtml" '
        'media-type="application/xhtml+xml"/></manifest>\n'
        '  <spine><itemref idref="c1"/></spine>\n'
        "</package>\n"
    )

    CONTAINER_XML = (
        '<?xml version="1.0"?>\n'
        '<container version="1.0" '
        'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
        "  <rootfiles>\n"
        '    <rootfile full-path="OEBPS/content.opf" '
        'media-type="application/oebps-package+xml"/>\n'
        "  </rootfiles>\n"
        "</container>\n"
    )

    def _build_epub(self, root, opf_text):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            z.writestr("META-INF/container.xml", self.CONTAINER_XML)
            z.writestr("OEBPS/content.opf", opf_text)
        path = pathlib.Path(root) / "book.epub"
        path.write_bytes(buf.getvalue())
        return path

    def test_day_one_pubdate_survives_the_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_epub(tmp, self.DAY_ONE_OPF)
            target = epd.canonical_date_target("2020-10-01 07:00:00+00:00")
            self.assertEqual(target, "2020-10-01T00:00:00Z")
            self.assertTrue(epd.normalize_epub_dates(path, target))
            fm = rfm.read_ebook_meta(path)
            self.assertIsNotNone(fm)
            # calibre renders the instant in UTC with a +00:00 suffix; the
            # date part must be the curated day, not the reader-nudged next
            # day the date-only form produced.
            self.assertTrue(
                fm.get("published", "").startswith("2020-10-01"),
                f"day-1 pubdate read back as {fm.get('published')!r} "
                "(the issue #4 class)",
            )
            self.assertEqual(rfm.norm_date(fm.get("published")), "2020-10-01")

    def test_second_normalize_pass_is_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._build_epub(tmp, self.DAY_ONE_OPF)
            target = epd.canonical_date_target("2020-10-01 00:00:00+00:00")
            self.assertTrue(epd.normalize_epub_dates(path, target))
            raw = path.read_bytes()
            self.assertTrue(epd.normalize_epub_dates(path, target))
            self.assertEqual(path.read_bytes(), raw)


if __name__ == "__main__":
    unittest.main()
