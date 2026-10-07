"""The twelve destructive --batch-* mass verbs (the 2026-09-20 test-gap
audit's item 1): --batch-clear-identifier/-languages/-pubdate/-publisher/
-series, --batch-remove-format/-tag, and --batch-set-authors/-identifier/
-languages/-pubdate/-publisher had zero coverage while the gentler verbs
(add-tag, clear-tags, set-column, set-cover, the rating carve-out) were
tested in test_set_writes.py.

The audit's shape, per verb: through main() on a 2-book temp library the
dry run writes nothing, --apply changes both books AND queues both ids in
metadata_dirtied (cquarry's OPF-regeneration contract for every row-level
mutation), and a mid-batch failure rolls both books back. The fixture
seeds every entity these verbs touch so each one gets its honest
"applied" path; the shared fixture module builds it.
"""

import io
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from _fixtures import build_library

from cquarry_cli.cli import main

_BOOKS = [
    dict(
        book_id=1,
        title="First Target",
        authors=("Author A",),
        tags=("Curated",),
        languages=("eng",),
        publisher="Old Press",
        series="Old Series",
        series_index=2.0,
        identifiers={"isbn": "9780441172719"},
        formats=("EPUB",),
    ),
    dict(
        book_id=2,
        title="Second Target",
        authors=("Author A",),
        tags=("Curated",),
        languages=("eng",),
        publisher="Old Press",
        series="Old Series",
        series_index=3.0,
        identifiers={"isbn": "9780765377067"},
        formats=("EPUB",),
    ),
]


class _DestructiveCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="cquarry_destructive_")
        self.db_path = build_library(os.path.join(self._tmp, "metadata.db"), _BOOKS)
        # The suite must not depend on whether the real Calibre is running.
        guard = mock.patch("cquarry_cli.setwrite._calibre_running", return_value=False)
        guard.start()
        self.addCleanup(guard.stop)
        self.addCleanup(shutil.rmtree, self._tmp, True)

    def _run(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _apply(self, *verb):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_destructive_bak_")
        self.addCleanup(shutil.rmtree, backup_dir, True)
        return self._run(
            "--ids",
            "1,2",
            *verb,
            "--apply",
            "--backup-dir",
            backup_dir,
            "--db",
            self.db_path,
        )

    def _con(self):
        return sqlite3.connect(self.db_path)

    def _dirtied(self):
        con = self._con()
        try:
            return sorted(
                r[0] for r in con.execute("SELECT book FROM metadata_dirtied")
            )
        finally:
            con.close()

    def _scalars(self, sql):
        con = self._con()
        try:
            return {bid: val for bid, val in con.execute(sql)}
        finally:
            con.close()

    def _tag_map(self):
        con = self._con()
        try:
            return {
                book: sorted(
                    name
                    for (name,) in con.execute(
                        "SELECT t.name FROM books_tags_link l JOIN tags t "
                        "ON t.id = l.tag WHERE l.book = ?",
                        (book,),
                    )
                )
                for book in (1, 2)
            }
        finally:
            con.close()


class TestBatchRemoveTag(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-remove-tag", "Curated", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertIn("- remove tag 'Curated'", out)
        self.assertEqual(self._tag_map(), {1: ["Curated"], 2: ["Curated"]})
        self.assertEqual(self._dirtied(), [])

    def test_apply_detaches_and_queues_both_books(self):
        code, out, err = self._apply("--batch-remove-tag", "Curated")
        self.assertEqual(code, 0, err)
        self.assertIn("remove tag 'Curated': 2 applied", out)
        self.assertEqual(self._tag_map(), {1: [], 2: []})
        # The last user of the tag is gone, so the orphaned tag row goes too.
        con = self._con()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tags").fetchone()[0], 0)
        finally:
            con.close()
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchSetAuthors(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids",
            "1,2",
            "--batch-set-authors",
            "B, New;C, Other",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        self.assertIn("- set authors to B, New & C, Other", out)
        sorts = self._scalars("SELECT id, author_sort FROM books")
        self.assertEqual(sorts, {1: "Author A", 2: "Author A"})
        self.assertEqual(self._dirtied(), [])

    def test_apply_relinks_and_recomputes_author_sort(self):
        code, out, err = self._apply("--batch-set-authors", "B, New;C, Other")
        self.assertEqual(code, 0, err)
        self.assertIn("set authors to B, New & C, Other: 2 applied", out)
        con = self._con()
        try:
            names = {
                bid: sorted(
                    n
                    for (n,) in con.execute(
                        "SELECT a.name FROM books_authors_link l JOIN authors a "
                        "ON a.id = l.author WHERE l.book = ?",
                        (bid,),
                    )
                )
                for bid in (1, 2)
            }
        finally:
            con.close()
        # New author rows store Calibre's legacy comma-as-pipe display
        # form; the sort keys keep the inverted form.
        self.assertEqual(names, {1: ["B| New", "C| Other"], 2: ["B| New", "C| Other"]})
        # cquarry recomputes books.author_sort from the authors' sort keys.
        sorts = self._scalars("SELECT id, author_sort FROM books")
        self.assertEqual(sorts, {1: "B, New & C, Other", 2: "B, New & C, Other"})
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchSetIdentifier(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids",
            "1,2",
            "--batch-set-identifier",
            "goodreads",
            "42",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        self.assertIn("- set identifier goodreads='42'", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM identifiers WHERE type='goodreads'"
                ).fetchone()[0],
                0,
            )
        finally:
            con.close()

    def test_apply_upserts_on_both_books(self):
        code, out, err = self._apply("--batch-set-identifier", "goodreads", "42")
        self.assertEqual(code, 0, err)
        self.assertIn("set identifier goodreads='42': 2 applied", out)
        con = self._con()
        try:
            rows = dict(
                con.execute(
                    "SELECT book, val FROM identifiers WHERE type = 'goodreads'"
                )
            )
        finally:
            con.close()
        self.assertEqual(rows, {1: "42", 2: "42"})
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchClearIdentifier(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-clear-identifier", "isbn", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- clear identifier 'isbn'", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0], 2
            )
        finally:
            con.close()

    def test_apply_clears_the_type_on_both_books(self):
        code, out, err = self._apply("--batch-clear-identifier", "isbn")
        self.assertEqual(code, 0, err)
        self.assertIn("clear identifier 'isbn': 2 applied", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM identifiers").fetchone()[0], 0
            )
        finally:
            con.close()
        self.assertEqual(self._dirtied(), [1, 2])

    def test_absent_type_is_already_so_on_both(self):
        code, out, err = self._apply("--batch-clear-identifier", "goodreads")
        self.assertEqual(code, 0, err)
        self.assertIn("clear identifier 'goodreads': 0 applied, 2 already-so", out)
        self.assertEqual(self._dirtied(), [])


class TestBatchSetLanguages(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-set-languages", "fra", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- set languages fra", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM languages WHERE lang_code='fra'"
                ).fetchone()[0],
                0,
            )
        finally:
            con.close()

    def test_apply_replaces_on_both_books(self):
        code, out, err = self._apply("--batch-set-languages", "French")
        self.assertEqual(code, 0, err)
        self.assertIn("set languages French: 2 applied", out)
        con = self._con()
        try:
            codes = {
                bid: [
                    c
                    for (c,) in con.execute(
                        "SELECT g.lang_code FROM books_languages_link l "
                        "JOIN languages g ON g.id = l.lang_code WHERE l.book = ?",
                        (bid,),
                    )
                ]
                for bid in (1, 2)
            }
        finally:
            con.close()
        # set_languages canonicalizes names to ISO codes.
        self.assertEqual(codes, {1: ["fra"], 2: ["fra"]})
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchClearLanguages(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-clear-languages", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- clear languages", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_languages_link").fetchone()[0],
                2,
            )
        finally:
            con.close()

    def test_apply_clears_both_books(self):
        code, out, err = self._apply("--batch-clear-languages")
        self.assertEqual(code, 0, err)
        self.assertIn("clear languages: 2 applied", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_languages_link").fetchone()[0],
                0,
            )
        finally:
            con.close()
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchSetPubdate(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-set-pubdate", "1999-12-31", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- set pubdate '1999-12-31'", out)
        pubdates = self._scalars("SELECT id, pubdate FROM books")
        self.assertEqual(pubdates, {1: "2020-01-01", 2: "2020-01-01"})
        self.assertEqual(self._dirtied(), [])

    def test_apply_sets_both_books(self):
        code, out, err = self._apply("--batch-set-pubdate", "1999-12-31")
        self.assertEqual(code, 0, err)
        self.assertIn("set pubdate '1999-12-31': 2 applied", out)
        pubdates = self._scalars("SELECT id, pubdate FROM books")
        for bid, stored in pubdates.items():
            self.assertTrue(str(stored).startswith("1999-12-31"), (bid, stored))
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchClearPubdate(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-clear-pubdate", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- clear pubdate", out)
        pubdates = self._scalars("SELECT id, pubdate FROM books")
        self.assertEqual(pubdates, {1: "2020-01-01", 2: "2020-01-01"})

    def test_apply_stores_the_sentinel_on_both_books(self):
        code, out, err = self._apply("--batch-clear-pubdate")
        self.assertEqual(code, 0, err)
        self.assertIn("clear pubdate: 2 applied", out)
        # set_pubdate(None) stores Calibre's undefined-date sentinel, the
        # value every reader (search, analytics, exportlt) treats as no
        # pubdate; it is not SQL NULL.
        pubdates = self._scalars("SELECT id, pubdate FROM books")
        for bid, stored in pubdates.items():
            self.assertTrue(str(stored).startswith("0101"), (bid, stored))
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchSetPublisher(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids",
            "1,2",
            "--batch-set-publisher",
            "Deep Press",
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        self.assertIn("- set publisher 'Deep Press'", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM publishers WHERE name='Deep Press'"
                ).fetchone()[0],
                0,
            )
        finally:
            con.close()

    def test_apply_replaces_on_both_books(self):
        code, out, err = self._apply("--batch-set-publisher", "Deep Press")
        self.assertEqual(code, 0, err)
        self.assertIn("set publisher 'Deep Press': 2 applied", out)
        con = self._con()
        try:
            names = {
                bid: con.execute(
                    "SELECT p.name FROM books_publishers_link l JOIN publishers p "
                    "ON p.id = l.publisher WHERE l.book = ?",
                    (bid,),
                ).fetchone()[0]
                for bid in (1, 2)
            }
            # The replaced entity is pruned once its last link goes.
            orphans = con.execute(
                "SELECT COUNT(*) FROM publishers WHERE name='Old Press'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(names, {1: "Deep Press", 2: "Deep Press"})
        self.assertEqual(orphans, 0)
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchClearPublisher(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-clear-publisher", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- clear publisher", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_publishers_link").fetchone()[0],
                2,
            )
        finally:
            con.close()

    def test_apply_clears_both_books(self):
        code, out, err = self._apply("--batch-clear-publisher")
        self.assertEqual(code, 0, err)
        self.assertIn("clear publisher: 2 applied", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_publishers_link").fetchone()[0],
                0,
            )
        finally:
            con.close()
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchClearSeries(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-clear-series", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- clear series", out)
        indexes = self._scalars("SELECT id, series_index FROM books")
        self.assertEqual(indexes, {1: 2.0, 2: 3.0})
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_series_link").fetchone()[0],
                2,
            )
        finally:
            con.close()

    def test_apply_unlinks_and_resets_the_index(self):
        code, out, err = self._apply("--batch-clear-series")
        self.assertEqual(code, 0, err)
        self.assertIn("clear series: 2 applied", out)
        con = self._con()
        try:
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM books_series_link").fetchone()[0],
                0,
            )
        finally:
            con.close()
        # Calibre's no-series state is index 1.0 with no link row, not
        # NULL and not the stale index.
        indexes = self._scalars("SELECT id, series_index FROM books")
        self.assertEqual(indexes, {1: 1.0, 2: 1.0})
        self.assertEqual(self._dirtied(), [1, 2])


class TestBatchRemoveFormat(_DestructiveCase):
    def test_dry_run_writes_nothing(self):
        code, out, _ = self._run(
            "--ids", "1,2", "--batch-remove-format", "EPUB", "--db", self.db_path
        )
        self.assertEqual(code, 0)
        self.assertIn("- remove format EPUB", out)
        con = self._con()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM data").fetchone()[0], 2)
        finally:
            con.close()

    def test_apply_drops_the_format_rows(self):
        code, out, err = self._apply("--batch-remove-format", "epub")
        self.assertEqual(code, 0, err)
        self.assertIn("remove format EPUB: 2 applied", out)
        con = self._con()
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM data").fetchone()[0], 0)
        finally:
            con.close()
        self.assertEqual(self._dirtied(), [1, 2])

    def test_json_report_carries_every_row(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_destructive_bak_")
        self.addCleanup(shutil.rmtree, backup_dir, True)
        code, out, _ = self._run(
            "--ids",
            "1,2",
            "--batch-remove-format",
            "EPUB",
            "--apply",
            "--format",
            "json",
            "--backup-dir",
            backup_dir,
            "--db",
            self.db_path,
        )
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertTrue(report["committed"])
        self.assertEqual(
            [(r["id"], r["verb"], r["status"]) for r in report["results"]],
            [
                (1, "remove format EPUB", "applied"),
                (2, "remove format EPUB", "applied"),
            ],
        )


class TestMidBatchFailureRollsBackBothBooks(_DestructiveCase):
    """The set-mode guarantee the audit pinned to the destructive verbs:
    one failed row rolls the whole pass back -- the tag survives on both
    books and nothing lands in metadata_dirtied."""

    def test_failed_verb_rolls_back_the_applied_one(self):
        code, out, err = self._apply(
            "--batch-remove-tag", "Curated", "--batch-set-column", "missing", "X"
        )
        self.assertEqual(code, 1)
        self.assertIn("book 1, set #missing = 'X'", err)
        self.assertIn("Nothing was written", out)
        self.assertEqual(self._tag_map(), {1: ["Curated"], 2: ["Curated"]})
        self.assertEqual(self._dirtied(), [])


if __name__ == "__main__":
    unittest.main()
