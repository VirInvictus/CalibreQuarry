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
import json
import pathlib
import shutil
import sqlite3
import tempfile
import unittest
import zipfile
from unittest import mock

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
    def test_labelled_isbn_counts_and_bare_runs_without_furniture_stay_noise(self):
        """A bare 13-digit run in running text is still a date, a phone
        number, a part number: without copyright furniture nearby it stays
        noise even on a 978/979 prefix. (The old rule was stricter -- only
        labelled numbers counted at all; issue #43 relaxed exactly that.)"""
        self.assertEqual(isbns.printed_isbns("call 9780201183993 today."), [])
        self.assertEqual(
            isbns.printed_isbns("All rights reserved. ISBN: 978-0-201-18399-3"),
            ["9780201183993"],
        )

    def test_counts_a_bare_978_run_beside_copyright_furniture(self):
        """The Vellum shape (issues #41/#43): a copyright page printing the
        number with no ISBN label at all. Corroboration, not the label, is
        what separates it from the phone numbers."""
        text = "All rights reserved. 978-1-63893-180-5 (ebook)"
        self.assertEqual(isbns.printed_isbns(text), ["9781638931805"])
        self.assertEqual(isbns.self_identified_isbns(text), ["9781638931805"])

    def test_bare_match_never_carves_a_longer_digit_run(self):
        """Boundary guards: a 14-digit token does not donate a checksum-valid
        13-digit phantom (9780140445688 out of 978014044568899)."""
        self.assertEqual(
            isbns.printed_isbns("All rights reserved. 978014044568899 (ebook)"), []
        )

    def test_typographic_dash_separators(self):
        """Pushkin Press sets the whole number in EN DASHES, which [-\s]
        never crossed (issue #43, live 11421 The Mill House Murders: the
        stored eISBN 9781782278344 sits two lines from its print twin)."""
        text = (
            "First published by Pushkin Press in 2023 1 3 5 7 9 8 6 4 2 "
            "ISBN 13: 978\u20131\u201378227\u2013833\u20137 "
            "eISBN: 978\u20131\u201378227\u2013834\u20134 All rights reserved."
        )
        self.assertEqual(isbns.printed_isbns(text), ["9781782278337", "9781782278344"])
        self.assertEqual(
            isbns.self_identified_isbns(text), ["9781782278337", "9781782278344"]
        )

    def test_the_issue_verbatim_dashed_label_line(self):
        """11468 Sorrow and Starlight, verbatim: the label joined to the
        number by a spaced dash, 'ISBN-13 - 978-1-914425-59-2'."""
        text = (
            "Sorrow and Starlight/Caroline Peckham & Susanne Valenti - 1st ed. "
            "ISBN-13 - 978-1-914425-59-2"
        )
        self.assertIn("9781914425592", isbns.printed_isbns(text))

    def test_space_separated_and_mixed_separator_forms(self):
        """More real 2026-batch lines: Macmillan sets spaces, NYRB mixes
        dashes into an otherwise bare run."""
        macmillan = (
            "ISBN (HB) 978 0 85705 309 1 ISBN (TPB) 978 0 85705 310 7 "
            "ISBN (Ebook) 978 1 84866 325 1"
        )
        self.assertIn("9781848663251", isbns.printed_isbns(macmillan))
        nyrb = "ISBN 978-1-59017-9079 \u2014 ISBN 978-1-59017-906-2 (paperback)"
        self.assertIn("9781590179079", isbns.printed_isbns(nyrb))

    def test_a_number_printed_twice_labelled_and_bare_lists_once(self):
        text = "ISBN 978-0-201-18399-3 and again 978-0-201-18399-3 (ebook)"
        self.assertEqual(isbns.printed_isbns(text), ["9780201183993"])

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
        # cquarry 1.8's to_isbn13 returns None for non-10/13 input (the old
        # local copy passed normalized junk through), so the fake printed
        # ISBNs are 10-digit strings that fold to real 13s.
        many = [
            i
            for i in (isbns.to_isbn13(f"04710{n:05d}") for n in range(40))
            if i is not None
        ]
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
        self.assertTrue(isbns.tag_matches(["NonFic"], ["NonFic"]))
        self.assertTrue(isbns.tag_matches(["NonFic.Tech.AI"], ["NonFic"]))
        self.assertFalse(isbns.tag_matches(["NonFiction"], ["NonFic"]))
        self.assertFalse(isbns.tag_matches(["Fic.SciFi"], ["NonFic"]))

    def test_tag_matches_accepts_several_prefixes(self):
        prefixes = ["NonFic.Philosophy", "NonFic.Religion"]
        self.assertTrue(isbns.tag_matches(["NonFic.Religion.Islam"], prefixes))
        self.assertFalse(isbns.tag_matches(["NonFic.Tech"], prefixes))

    def test_tag_matches_considers_every_tag_not_just_the_first(self):
        # The bug this pins: scoping used one tag pulled by a LIMIT 1 subquery
        # with no ORDER BY, so a book carrying two tags was filtered on whichever
        # one SQLite happened to return and went missing about half the time.
        self.assertTrue(isbns.tag_matches(["Fic.Fantasy", "NonFic.Tech"], ["NonFic"]))
        self.assertTrue(isbns.tag_matches(["NonFic.Tech", "Fic.Fantasy"], ["NonFic"]))
        self.assertFalse(isbns.tag_matches(["Fic.Fantasy", "Fic.SciFi"], ["NonFic"]))
        self.assertFalse(isbns.tag_matches([], ["NonFic"]))

    def test_display_tag_prefers_a_tag_the_filter_selected_on(self):
        tags = ["Fic.Fantasy", "NonFic.Tech"]
        self.assertEqual(isbns.display_tag(tags, ["NonFic"]), "NonFic.Tech")
        self.assertEqual(isbns.display_tag(tags, None), "Fic.Fantasy")
        self.assertEqual(isbns.display_tag([], ["NonFic"]), "")


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
            INSERT INTO tags VALUES (12, 'Ref.Manual');
            -- book 1 carries two tags, the one no filter here matches linked
            -- FIRST: a LIMIT 1 subquery would scope book 1 on 'Ref.Manual'.
            INSERT INTO books_tags_link VALUES (1, 12);
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

    def test_scope_finds_a_book_by_a_tag_that_is_not_its_first(self):
        # book 1's first-linked tag is Ref.Manual; scoping used to read exactly
        # one arbitrary tag, so filtering on its OTHER tag lost the book.
        with tempfile.TemporaryDirectory() as tmp:
            targets = isbns.load_targets(self._library(tmp), None, ["NonFic.Tech"])
            self.assertEqual([t["id"] for t in targets], [1])
            # reported under the tag the filter selected on, not the first one
            self.assertEqual(targets[0]["tag"], "NonFic.Tech.AI")
            self.assertEqual(targets[0]["tags"], ["NonFic.Tech.AI", "Ref.Manual"])

    def test_db_path_containing_uri_syntax_still_opens(self):
        # '#' starts a URI fragment: interpolated raw into the file: URI, this
        # opened some other database and failed with "no such table: books".
        with tempfile.TemporaryDirectory() as tmp:
            src = self._library(tmp)
            odd = pathlib.Path(tmp) / "Books #2"
            odd.mkdir()
            shutil.copy2(src, odd / "metadata.db")
            targets = isbns.load_targets(str(odd / "metadata.db"), None, None)
            self.assertEqual([t["id"] for t in targets], [1, 2])


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


class TestMidBookCopyrightPage(unittest.TestCase):
    """Issue #43's dominant mechanism: the copyright doc sorts mid-book, so
    the head/tail window never sees it (live 11468: position 87 of 94, after
    forty lowercase chapter-*.xhtml docs). A window that finds nothing is
    the one verdict the full-file rescan can fix, and it must not disturb
    verdicts the window already settled."""

    def _epub(self, tmp, docs):
        path = pathlib.Path(tmp) / "b.epub"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            for doc_name, body in docs.items():
                z.writestr(doc_name, body)
        return str(path)

    @staticmethod
    def _filler(n):
        return f"<p>filler {n} with no digits or labels</p>"

    def _docs(self, copyright_body=None):
        docs = {f"doc-{n:02d}.xhtml": self._filler(n) for n in range(1, 15)}
        # doc-09 sorts outside the head-8/tail-3 window on purpose
        if copyright_body:
            docs["doc-09.xhtml"] = copyright_body
        return docs

    def test_window_misses_a_mid_book_copyright_doc_full_read_sees_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._epub(
                tmp,
                self._docs("<p>All rights reserved.</p><p>ISBN: 978-0-201-18399-3</p>"),
            )
            window_text = isbns.epub_text(path)
            self.assertIsNotNone(window_text)
            self.assertEqual(isbns.printed_isbns(window_text), [])
            full_text = isbns.epub_text(path, full=True)
            self.assertEqual(isbns.printed_isbns(full_text), ["9780201183993"])

    def _library(self, tmp, copyright_body, extra_docs=None):
        root = pathlib.Path(tmp)
        db = root / "metadata.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT,
                path TEXT, pubdate TEXT);
            CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (book INTEGER, tag INTEGER);
            INSERT INTO books VALUES (1, 'Tech Book', 'A/Tech Book (1)', '2019-01-01');
            INSERT INTO identifiers VALUES (1, 'isbn', '0201183994');
            """
        )
        con.commit()
        con.close()
        book_dir = root / "A" / "Tech Book (1)"
        book_dir.mkdir(parents=True)
        docs = self._docs(copyright_body)
        if extra_docs:
            docs.update(extra_docs)
        with zipfile.ZipFile(book_dir / "book.epub", "w") as z:
            z.writestr("mimetype", "application/epub+zip")
            for name, body in docs.items():
                z.writestr(name, body)
        return str(db)

    def _run_json(self, db):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", ["audit_isbns.py", "--db", db, "--format", "json"]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = isbns.main()
        return rc, json.loads(out.getvalue())

    def test_main_confirms_through_the_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(
                tmp, "<p>All rights reserved.</p><p>ISBN: 978-0-201-18399-3</p>"
            )
            rc, rows = self._run_json(db)
            self.assertEqual(rc, 0)
            self.assertEqual(rows[0]["verdict"], "CONFIRMED")
            self.assertEqual(rows[0]["printed"], ["9780201183993"])

    def test_no_printed_isbn_anywhere_stays_no_isbn_printed(self):
        """The fallback is a second look, not a confirmatory bias: a book
        that prints no ISBN at all (live 11419 The Fourth Door) still reads
        NO_ISBN_PRINTED, which is not a finding and exits 0."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(tmp, None)
            rc, rows = self._run_json(db)
            self.assertEqual(rc, 0)
            self.assertEqual(rows[0]["verdict"], "NO_ISBN_PRINTED")

    def test_rescan_fires_on_a_citation_only_window(self):
        """The gate is 'nothing self-identified', not 'empty window': a
        back-matter ad citing another book's ISBN (labelled, no furniture)
        reads the same as an empty window, and must not stop the rescan
        that would find the mid-book copyright page."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(
                tmp,
                "<p>All rights reserved.</p><p>ISBN: 978-0-201-18399-3</p>",
                extra_docs={
                    "doc-13.xhtml": "<p>More from the author. "
                    "ISBN 978-1-54201-562-2</p>"
                },
            )
            rc, rows = self._run_json(db)
            self.assertEqual(rc, 0)
            self.assertEqual(rows[0]["verdict"], "CONFIRMED")

    def test_pdf_fallback_reads_the_whole_document(self):
        """The pdf window can also miss a mid-book copyright page; the
        rescan there is one unwindowed pdftotext pass."""
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(tmp, None)
            book_dir = pathlib.Path(tmp) / "A" / "Tech Book (1)"
            (book_dir / "book.epub").unlink()
            (book_dir / "book.pdf").write_bytes(b"%PDF-1.4 fake")

            def fake_run(cmd):
                if cmd[0] == "pdfinfo":
                    return "Pages: 30\n"
                if "-f" in cmd:  # windowed head/tail calls: front matter only
                    return "A title page, no numbers\n"
                return "All rights reserved. ISBN 978-0-201-18399-3\n"

            with mock.patch.object(isbns, "_run", side_effect=fake_run):
                rc, rows = self._run_json(db)
            self.assertEqual(rc, 0)
            self.assertEqual(rows[0]["verdict"], "CONFIRMED")
            self.assertEqual(rows[0]["format"], "pdf")


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


class TestMachineOutput(unittest.TestCase):
    """Machine formats keep stdout to the payload (issue #27): the banner
    and setup prose ride stderr, so json.load on captured stdout and a tsv
    redirect work without offset hacks, and --json FILE writes the same
    payload --format json prints (the check_pdf.py shape). Text mode is
    unchanged. Every test drives main() end to end over a fixture library
    whose files each print their own stored ISBN (both CONFIRMED, exit 0).
    """

    def _library(self, tmp):
        root = pathlib.Path(tmp)
        db = root / "metadata.db"
        con = sqlite3.connect(db)
        con.executescript(
            """
            CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT,
                path TEXT, pubdate TEXT);
            CREATE TABLE identifiers (book INTEGER, type TEXT, val TEXT);
            CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE books_tags_link (book INTEGER, tag INTEGER);
            INSERT INTO books VALUES (1, 'Tech Book', 'A/Tech Book (1)', '2019-01-01');
            INSERT INTO books VALUES (2, 'Novel', 'B/Novel (2)', '2020-01-01');
            INSERT INTO identifiers VALUES (1, 'isbn', '0201183994');
            INSERT INTO identifiers VALUES (2, 'isbn', '9781542015622');
            """
        )
        con.commit()
        con.close()
        for rel, isbn in (
            ("A/Tech Book (1)", "978-0-201-18399-3"),
            ("B/Novel (2)", "978-1-54201-562-2"),
        ):
            book_dir = root / rel
            book_dir.mkdir(parents=True)
            with zipfile.ZipFile(book_dir / "book.epub", "w") as z:
                z.writestr(
                    "copyright.xhtml",
                    "<p>All rights reserved.</p><p>ISBN: " + isbn + "</p>",
                )
        return str(db)

    def _run(self, argv, db):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch("sys.argv", ["audit_isbns.py", "--db", db] + argv):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = isbns.main()
        return rc, out.getvalue(), err.getvalue()

    def test_json_stdout_is_bare_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run(["--format", "json"], self._library(tmp))
            self.assertEqual(rc, 0)
            rows = json.loads(out)  # the offset hack is gone
            self.assertEqual([r["id"] for r in rows], [1, 2])
            self.assertEqual(rows[0]["verdict"], "CONFIRMED")
            self.assertNotIn("audit_isbns.py", out, "the banner rides stdout")
            self.assertIn("audit_isbns.py - Execution", err)

    def test_json_file_writes_the_same_payload_beside_human_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(tmp)
            report = pathlib.Path(tmp) / "report.json"
            rc, out, _ = self._run(["--json", str(report)], db)
            self.assertEqual(rc, 0)
            self.assertIn("Summary", out)  # the human report still on stdout
            rows = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual([r["id"] for r in rows], [1, 2])
            self.assertEqual(rows[0]["verdict"], "CONFIRMED")

    def test_json_flag_alongside_json_format(self):
        # Both surfaces carry the same payload in one invocation.
        with tempfile.TemporaryDirectory() as tmp:
            db = self._library(tmp)
            report = pathlib.Path(tmp) / "report.json"
            rc, out, _ = self._run(["--format", "json", "--json", str(report)], db)
            self.assertEqual(rc, 0)
            self.assertEqual(
                json.loads(out), json.loads(report.read_text(encoding="utf-8"))
            )

    def test_tsv_stdout_is_table_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run(["--format", "tsv"], self._library(tmp))
            self.assertEqual(rc, 0)
            self.assertTrue(
                out.startswith("id\tverdict\tstored\tprinted\tformat\ttitle\n"), out
            )
            self.assertNotIn("====", out, "the banner rides stdout")
            self.assertIn("audit_isbns.py - Execution", err)

    def test_text_mode_stdout_still_carries_the_banner(self):
        # The human surface is unchanged by the machine-format fix.
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, _ = self._run([], self._library(tmp))
            self.assertEqual(rc, 0)
            self.assertIn("audit_isbns.py - Execution", out)
            self.assertIn("Summary", out)

    def test_setup_error_keeps_machine_stdout_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc, out, err = self._run(
                ["--format", "json", "--id", "notanumber"], self._library(tmp)
            )
            self.assertEqual(rc, 2)
            self.assertEqual(out, "")
            self.assertIn("setup error", err)


if __name__ == "__main__":
    unittest.main()
