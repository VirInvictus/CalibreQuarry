"""Tests for reconcile_file_metadata.py pure logic: normalisation, parsing,
and the per-format field diff. No DB or subprocess; the script lives in
scripts/, so it is imported by path."""

import importlib.util
import unittest
from pathlib import Path

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
        # "[sort]" stripped, "&"/comma split: still a match -> no drift.
        db = db_record(authors=["Neil Gaiman", "Terry Pratchett"])
        fm = file_meta(author_s="Neil Gaiman & Terry Pratchett [Gaiman, Neil]")
        fm["author(s)"] = fm.pop("author_s")
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


if __name__ == "__main__":
    unittest.main()
