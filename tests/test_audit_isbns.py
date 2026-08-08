"""Tests for audit_isbns.py: ISBN arithmetic, extraction, and verdicts.

The script is standalone (not part of the cquarry package), so it is loaded by
path the same way test_audit_drm.py loads its sibling. Everything here is
offline: no network is involved anywhere in this tool, and the file readers are
exercised against zip fixtures built in a temp dir with stdlib only.

The verdict tests carry the real cases that motivated the tool, so a future
refactor that quietly reclassifies them fails loudly.
"""

import contextlib
import importlib.util
import io
import pathlib
import sqlite3
import tempfile
import unittest
import zipfile

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


isbns = _load("audit_isbns")


class TestIsbnArithmetic(unittest.TestCase):
    def test_valid_isbn13_and_isbn10(self):
        self.assertTrue(isbns.checksum_ok("9780201183993"))
        self.assertTrue(isbns.checksum_ok("0201183994"))
        self.assertTrue(isbns.checksum_ok("978-0-201-18399-3"))

    def test_isbn10_with_check_digit_x(self):
        self.assertTrue(isbns.checksum_ok("020163354X"))

    def test_rejects_bad_checksum_and_wrong_length(self):
        self.assertFalse(isbns.checksum_ok("9780201183994"))
        self.assertFalse(isbns.checksum_ok("123456789"))
        self.assertFalse(isbns.checksum_ok(""))

    def test_to_isbn13_folds_isbn10(self):
        self.assertEqual(isbns.to_isbn13("0201183994"), "9780201183993")
        self.assertEqual(isbns.to_isbn13("020163354X"), "9780201633542")

    def test_to_isbn13_leaves_isbn13_alone(self):
        self.assertEqual(isbns.to_isbn13("9780201183993"), "9780201183993")

    def test_same_registrant(self):
        # the real Leviathan pair: adjacent numbers from one publisher block
        self.assertTrue(isbns.same_registrant("9781542015622", "9781542015615"))
        # the real A Book on C pair: unrelated publishers
        self.assertFalse(isbns.same_registrant("9782147483649", "9780201183993"))

    def test_same_registrant_catches_short_registrants(self):
        """Big houses have SHORT registrants, so a prefix long enough for a
        one-book press splits HarperCollins from itself. The real Sabriel pair
        (978-0-06-447183-1 stored, 978-0-06-000548-1 printed) is one publisher."""
        self.assertTrue(isbns.same_registrant("9780064471831", "9780060005481"))


class TestPrintedIsbnExtraction(unittest.TestCase):
    def test_requires_the_isbn_label(self):
        """Bare digit runs are everywhere in books; only labelled ones count."""
        self.assertEqual(
            isbns.printed_isbns("call 9780201183993 today. All rights reserved."), []
        )
        self.assertEqual(
            isbns.printed_isbns("All rights reserved. ISBN: 978-0-201-18399-3"),
            ["9780201183993"],
        )

    def test_tolerates_label_variants_and_separators(self):
        for text in (
            "All rights reserved. ISBN 9780201183993",
            "Copyright © 2018. isbn-13: 978 0 201 18399 3",
            "ISBN (paperback): 0201183994",
        ):
            self.assertEqual(isbns.printed_isbns(text), ["9780201183993"], text)

    def test_ignores_an_isbn_a_book_cites_rather_than_claims(self):
        """The Atrocity Archives names The New Hacker's Dictionary's ISBN in a
        glossary entry. One citation is indistinguishable from a
        self-identification by count alone, so counting is not the test."""
        text = (
            "LART Luser Attitude Readjustment Tool—see The New Hacker's "
            "Dictionary, edited by Eric S. Raymond, MIT Press, "
            "ISBN 0-262680-92-0 [All] THE LAUNDRY"
        )
        self.assertEqual(isbns.self_identified_isbns(text), [])

    def test_ignores_a_joke_isbn_in_running_text(self):
        """Metamagical Themas lists one among self-referential book titles."""
        text = (
            "I Never Can Remember What It's Called The Great American Novel "
            "ISBN 0-943568-01-3 Self Referential Book Title"
        )
        self.assertEqual(isbns.self_identified_isbns(text), [])

    def test_ignores_a_publishers_advertisement(self):
        """C++ Primer Plus advertises six other Sams books in its back matter."""
        text = (
            "ESSENTIAL REFERENCES FOR PROGRAMMERS PHP & MySQL Web Development "
            "Luke Welling & Laura Thomson ISBN-13: 978-0-672-32916-6"
        )
        self.assertEqual(isbns.self_identified_isbns(text), [])

    def test_counts_an_isbn_beside_copyright_furniture(self):
        spelunky = (
            "Copyright © 2016 Derek Yu All rights reserved. "
            "ISBN 13: 978-1-940535-13-5 (paperback) First Printing: 2016"
        )
        cip = (
            "Cataloging-in-Publication Data Hopcroft, John E. 2nd ed. "
            "ISBN 0-201-44124-1 1. Machine theory"
        )
        tsr = (
            "prohibited without the express written permission of TSR, Inc. "
            "ISBN 1-56076-605-0"
        )
        norton = "Production manager: Julia Druskin ISBN 978-0-393-24482-3 (e-book)"
        pan = "www.panmacmillan.com ISBN 978-0-330-48053-6 in Adobe Reader format"
        for text in (spelunky, cip, tsr, norton, pan):
            self.assertTrue(isbns.self_identified_isbns(text), text[:48])

    def test_drops_bad_checksums_and_deduplicates(self):
        text = (
            "All rights reserved. ISBN 9780201183994 ISBN 9780201183993 ISBN 0201183994"
        )
        self.assertEqual(isbns.printed_isbns(text), ["9780201183993"])


class TestVerdicts(unittest.TestCase):
    MAX = isbns.DEFAULT_MAX_PRINTED

    def test_confirmed_when_stored_is_printed(self):
        self.assertEqual(
            isbns.classify("9780201183993", ["9780201183993"], self.MAX), "CONFIRMED"
        )

    def test_no_isbn_printed(self):
        self.assertEqual(
            isbns.classify("9780201183993", [], self.MAX), "NO_ISBN_PRINTED"
        )

    def test_mismatch_when_a_different_publisher_is_printed(self):
        """A Book on C: stored value was the 2147483649 overflow constant."""
        self.assertEqual(
            isbns.classify("9782147483649", ["9780201183993"], self.MAX), "MISMATCH"
        )

    def test_variant_when_same_publisher_block(self):
        """Leviathan: print vs ebook binding, not a wrong book."""
        self.assertEqual(
            isbns.classify("9781542015622", ["9781542015615"], self.MAX), "VARIANT"
        )

    def test_ambiguous_when_several_printed_and_none_match(self):
        """Designers & Dragons prints the whole series; a human picks."""
        self.assertEqual(
            isbns.classify(
                "9782000200567",
                ["9781613170755", "9781613170762", "9781613170779"],
                self.MAX,
            ),
            "AMBIGUOUS",
        )

    def test_bibliography_is_not_evidence_about_itself(self):
        """The Art of UNIX Programming prints 49 other books' ISBNs."""
        many = [isbns.to_isbn13(f"04710{n:04d}") for n in range(40)]
        many = [i for i in many if len(i) == 13]
        self.assertEqual(
            isbns.classify("9781039260719", many, self.MAX), "NO_ISBN_PRINTED"
        )

    def test_a_match_confirms_even_from_unclaimed_text(self):
        """The two directions take different evidence. A book printing the SAME
        number we store is conclusive whatever the surrounding text says: a
        citation coinciding with our own value does not happen. Requiring
        self-identification in BOTH directions cost 639 confirmations on a real
        library while removing only 25 false findings."""
        self.assertEqual(
            isbns.classify("9780201183993", ["9780201183993"], self.MAX, []),
            "CONFIRMED",
        )

    def test_a_difference_counts_only_when_the_book_claimed_it(self):
        """Same inputs, no match: with nothing self-identified there is no
        evidence either way, so it is not a finding."""
        self.assertEqual(
            isbns.classify("9782147483649", ["9780262680929"], self.MAX, []),
            "NO_ISBN_PRINTED",
        )
        self.assertEqual(
            isbns.classify(
                "9782147483649", ["9780262680929"], self.MAX, ["9780262680929"]
            ),
            "MISMATCH",
        )

    def test_bibliography_still_confirms_a_hit(self):
        many = ["9780201183993"] + [f"978020118{n:03d}" for n in range(10)]
        self.assertEqual(isbns.classify("9780201183993", many, self.MAX), "CONFIRMED")


class TestScoping(unittest.TestCase):
    def test_tag_matches_is_anchored_hierarchical(self):
        self.assertTrue(isbns.tag_matches("NonFic", ["NonFic"]))
        self.assertTrue(isbns.tag_matches("NonFic.Tech.AI", ["NonFic"]))
        self.assertFalse(isbns.tag_matches("NonFiction", ["NonFic"]))
        self.assertFalse(isbns.tag_matches("Fic.SciFi", ["NonFic"]))

    def test_tag_matches_accepts_several_prefixes(self):
        prefixes = ["NonFic.Philosophy", "NonFic.Religion"]
        self.assertTrue(isbns.tag_matches("NonFic.Religion.Islam", prefixes))
        self.assertFalse(isbns.tag_matches("NonFic.Tech", prefixes))


class TestReporting(unittest.TestCase):
    def _results(self):
        return [
            {
                "id": 1,
                "title": "Wrong One",
                "isbn": "9782147483649",
                "printed": ["9780201183993"],
                "format": "pdf",
                "verdict": "MISMATCH",
            },
            {
                "id": 2,
                "title": "Fine",
                "isbn": "9780201183993",
                "printed": ["9780201183993"],
                "format": "epub",
                "verdict": "CONFIRMED",
            },
        ]

    def _capture(self, quiet):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            isbns.report_text(self._results(), quiet)
        return buf.getvalue()

    def test_quiet_keeps_findings_and_drops_decoration(self):
        """--quiet suppresses decorative output, never the findings."""
        out = self._capture(quiet=True)
        self.assertIn("MISMATCH", out)
        self.assertIn("#1", out)
        self.assertNotIn("Summary", out)

    def test_verbose_includes_the_summary(self):
        out = self._capture(quiet=False)
        self.assertIn("MISMATCH", out)
        self.assertIn("Summary", out)
        self.assertIn("CONFIRMED", out)


class TestLoadTargets(unittest.TestCase):
    def _library(self, tmp):
        path = pathlib.Path(tmp) / "metadata.db"
        con = sqlite3.connect(path)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT);
            CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (book INTEGER, tag INTEGER);
            INSERT INTO books VALUES (1, 'Tech Book', 'A/Tech Book (1)');
            INSERT INTO books VALUES (2, 'Novel', 'B/Novel (2)');
            INSERT INTO books VALUES (3, 'No Isbn', 'C/No Isbn (3)');
            INSERT INTO identifiers VALUES (1, 'isbn', '0201183994');
            INSERT INTO identifiers VALUES (2, 'isbn', '9781542015622');
            INSERT INTO identifiers VALUES (3, 'goodreads', '12345');
            INSERT INTO tags VALUES (10, 'NonFic.Tech.AI');
            INSERT INTO tags VALUES (11, 'Fic.SciFi');
            INSERT INTO books_tags_link VALUES (1, 10);
            INSERT INTO books_tags_link VALUES (2, 11);
            """
        )
        con.commit()
        con.close()
        return str(path)

    def test_only_books_with_an_isbn_and_folded_to_13(self):
        with tempfile.TemporaryDirectory() as tmp:
            targets = isbns.load_targets(self._library(tmp), None, None)
            self.assertEqual([t["id"] for t in targets], [1, 2])
            self.assertEqual(targets[0]["isbn"], "9780201183993")

    def test_scoped_by_tag_prefix(self):
        with tempfile.TemporaryDirectory() as tmp:
            targets = isbns.load_targets(self._library(tmp), None, ["NonFic"])
            self.assertEqual([t["id"] for t in targets], [1])

    def test_scoped_by_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            targets = isbns.load_targets(self._library(tmp), [2], None)
            self.assertEqual([t["id"] for t in targets], [2])


class TestEpubExtraction(unittest.TestCase):
    def _epub(self, tmp, name, docs):
        path = pathlib.Path(tmp) / name
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            for doc_name, body in docs.items():
                z.writestr(doc_name, body)
        return str(path)

    def test_reads_body_text_and_strips_markup(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._epub(
                tmp,
                "b.epub",
                {
                    "OEBPS/copyright.xhtml": "<p>All rights reserved.</p>"
                    "<p>ISBN: <b>978-0-201-18399-3</b></p>"
                },
            )
            text = isbns.epub_text(path)
            self.assertIsNotNone(text)
            self.assertEqual(isbns.printed_isbns(text), ["9780201183993"])

    def test_ignores_opf_metadata_so_the_check_is_not_circular(self):
        """reconcile writes the DB's values into the OPF; reading it would
        compare the database with itself."""
        with tempfile.TemporaryDirectory() as tmp:
            path = self._epub(
                tmp,
                "b.epub",
                {
                    "OEBPS/content.opf": "<dc:identifier>ISBN 9780201183993"
                    "</dc:identifier><meta>All rights reserved.</meta>",
                    "OEBPS/text.xhtml": "<p>no number here</p>",
                },
            )
            self.assertEqual(isbns.printed_isbns(isbns.epub_text(path) or ""), [])

    def test_unreadable_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "broken.epub"
            path.write_bytes(b"not a zip")
            self.assertIsNone(isbns.epub_text(str(path)))


class TestSkippedVsUnreadable(unittest.TestCase):
    """A format we never read is not the same finding as a file we could not."""

    def test_no_supported_format_reports_no_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "book.azw3").write_bytes(b"kindle")
            text, fmt = isbns.book_text(tmp)
            self.assertIsNone(text)
            self.assertIsNone(fmt, "MOBI/AZW3 are skipped by design, not unreadable")

    def test_supported_format_that_yields_nothing_names_the_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            (pathlib.Path(tmp) / "book.epub").write_bytes(b"not a zip")
            text, fmt = isbns.book_text(tmp)
            self.assertIsNone(text)
            self.assertEqual(fmt, "epub")


if __name__ == "__main__":
    unittest.main()
