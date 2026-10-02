"""Tests for reconcile_file_metadata.py pure logic: normalisation, parsing,
and the per-format field diff. No DB or subprocess; the script lives in
scripts/, so it is imported by path."""

import contextlib
import importlib.util
import io
import sys
import sqlite3
import pathlib
import tempfile
import unittest
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

    def _run_main(self, tmp, read_backs):
        root = self._library(tmp)
        argv = [
            "reconcile_file_metadata.py",
            str(root),
            "--apply",
        ]
        out = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(rfm, "shutil") as _sh,
            mock.patch.object(rfm, "file_metadata", side_effect=read_backs),
            mock.patch.object(rfm, "embed_calibredb", return_value=True) as embed,
            mock.patch.object(rfm, "calibre_running", return_value=False),
            contextlib.redirect_stdout(out),
        ):
            rc = rfm.main()
        return rc, out.getvalue(), embed.call_count

    def test_in_sync_read_back_is_done(self):
        # First read (the drift scan) sees the old date; the post-embed
        # read-back sees the corrected one: a clean DONE, exit 0.
        drifted = file_meta(published="2010-10-25")
        fixed = file_meta()
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds = self._run_main(tmp, [drifted, fixed])
        self.assertEqual(rc, 0, out)
        self.assertIn("DONE", out)
        self.assertIn("verified by read-back", out)
        self.assertEqual(embeds, 1)

    def test_still_drifted_read_back_is_a_residual_and_fails(self):
        # The #9177 shape: embed_metadata exits 0, the EPUB keeps its own
        # dc:date. The read-back catches it; the run fails with a RESIDUAL.
        drifted = file_meta(published="2010-10-25")
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, embeds = self._run_main(
                tmp, [drifted, file_meta(published="2010-10-25")]
            )
        self.assertEqual(rc, 1, out)
        self.assertIn("RESIDUAL", out)
        self.assertIn("pubdate", out)
        self.assertEqual(embeds, 1)


if __name__ == "__main__":
    unittest.main()
