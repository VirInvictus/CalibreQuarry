"""Tests for Phase 16's set-oriented writes: one target source (--ids,
--from-search, --from-untagged, --from-manifest), id-less --batch-* verbs,
dry-run by default, the manifest-only rating carve-out, and honest
per-verb reporting.

Every write runs against a temp database built with stdlib only; the
Calibre-closed pgrep guard is stubbed so the suite never depends on
whether the real Calibre happens to be running.
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

from cquarry_cli.cli import main

_SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, sort TEXT, author_sort TEXT,
    timestamp TEXT, pubdate TEXT, has_cover INT, last_modified TEXT,
    series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT,
    UNIQUE (book, tag));
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT,
    UNIQUE (book, series));
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT,
    UNIQUE (book, rating));
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT,
    UNIQUE (book, type));
CREATE TABLE comments (book INT, text TEXT);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT, name TEXT,
    datatype TEXT, is_multiple BOOL, editable BOOL, display TEXT);
CREATE TABLE custom_column_1 (id INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE books_custom_column_1_link (id INTEGER PRIMARY KEY, book INT,
    value INT, UNIQUE (book, value));
CREATE TABLE metadata_dirtied (id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
    UNIQUE(book));
"""


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        # The DB lives in its own directory so a sibling temp dir counts as
        # OUTSIDE the library for the --backup-dir check, exactly as in
        # production.
        self._tmp = tempfile.mkdtemp(prefix="cquarry_lib_")
        self.db_path = os.path.join(self._tmp, "metadata.db")
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        con.executemany(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    1,
                    "Tagged",
                    "Tagged",
                    "Author A",
                    "2020-01-01",
                    "2020-01-01",
                    1,
                    "2020-01-01 00:00:00",
                    1.0,
                    "p1",
                    "u1",
                ),
                (
                    2,
                    "Untagged Target",
                    "Untagged Target",
                    "Author A",
                    "2020-01-02",
                    "2020-01-02",
                    0,
                    "2020-01-02 00:00:00",
                    1.0,
                    "p2",
                    "u2",
                ),
            ],
        )
        con.execute("INSERT INTO tags VALUES (1, 'Curated')")
        con.execute("INSERT INTO books_tags_link (book, tag) VALUES (1, 1)")
        con.execute("INSERT INTO ratings VALUES (1, 9)")
        con.execute("INSERT INTO books_ratings_link (book, rating) VALUES (1, 1)")
        con.execute(
            "INSERT INTO custom_columns VALUES (1, 'audience', 'Audience', "
            "'text', 1, 1, NULL)"
        )
        con.commit()
        con.close()
        # The suite must not depend on whether the real Calibre is running.
        self._guard = mock.patch(
            "cquarry_cli.setwrite._calibre_running", return_value=False
        )
        self._guard.start()
        self.addCleanup(self._guard.stop)

    def tearDown(self):
        shutil.rmtree(self._tmp)

    def _tag_map(self):
        con = sqlite3.connect(self.db_path)
        try:
            return {
                book: sorted(
                    n
                    for (n,) in con.execute(
                        "SELECT t.name FROM books_tags_link l JOIN tags t "
                        "ON t.id = l.tag WHERE l.book = ?",
                        (book,),
                    )
                )
                for book in (1, 2)
            }
        finally:
            con.close()


class TestTargetSetSources(_TempDBCase):
    """Exactly one source, always with verbs; combinations are refused
    before anything executes."""

    def test_two_sources_refused(self):
        # The mutually exclusive argparse group catches this before main()
        # runs, exiting 2 the same way an argument problem should.
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            with self.assertRaises(SystemExit) as caught:
                main(
                    [
                        "--ids",
                        "1",
                        "--from-untagged",
                        "--batch-clear-tags",
                        "--db",
                        self.db_path,
                    ]
                )
        self.assertEqual(caught.exception.code, 2)

    def test_verbs_need_a_source(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--batch-clear-tags", "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("need a target source", err.getvalue())

    def test_source_needs_verbs(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--ids", "1", "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("at least one --batch-* verb", err.getvalue())

    def test_single_book_verb_combination_refused_and_nothing_ran(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--set-title",
                    "1",
                    "Should Not Stick",
                    "--batch-clear-tags",
                    "--ids",
                    "2",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("cannot be combined", err.getvalue())
        title = (
            sqlite3.connect(self.db_path)
            .execute("SELECT title FROM books WHERE id=1")
            .fetchone()[0]
        )
        self.assertEqual(title, "Tagged")

    def test_unknown_ids_abort_before_anything_opens_writable(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(["--ids", "1,99", "--batch-clear-tags", "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("unknown book id(s): 99", err_cap.getvalue())

    def test_bad_id_token_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--ids", "1,x", "--batch-clear-tags", "--db", self.db_path])
        self.assertEqual(rc, 2)

    def test_missing_manifest_file_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--from-manifest",
                    "/nonexistent/batch.ids",
                    "--batch-clear-tags",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("unreadable", err.getvalue())

    def test_manifest_unknown_id_refused(self):
        fd, path = tempfile.mkstemp(suffix=".ids", text=True)
        os.write(fd, b"1\n2, 99\n")
        os.close(fd)
        self.addCleanup(os.remove, path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                ["--from-manifest", path, "--batch-clear-tags", "--db", self.db_path]
            )
        self.assertEqual(rc, 2)
        self.assertIn("unknown book id(s): 99", err_cap.getvalue())

    def test_from_search_resolves_read_only(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--from-search",
                    "tags:Curated",
                    "--batch-clear-tags",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Resolved 1 book(s): 1", out.getvalue())
        self.assertIn("--from-search tags:Curated", out.getvalue())
        self.assertEqual(self._tag_map()[1], ["Curated"])

    def test_from_untagged_resolves_read_only(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                ["--from-untagged", "--batch-add-tag", "Pending", "--db", self.db_path]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Resolved 1 book(s): 2", out.getvalue())

    def test_bad_search_expression_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                [
                    "--from-search",
                    'search:"No Such Search"',
                    "--batch-clear-tags",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("could not be resolved", err_cap.getvalue())


class TestDryRunDefault(_TempDBCase):
    """Nothing opens WritableCalibreDB without --apply."""

    def test_dry_run_prints_plan_and_writes_nothing(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                ["--ids", "1,2", "--batch-add-tag", "Batch", "--db", self.db_path]
            )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("DRY RUN", text)
        self.assertIn("Target: --ids 1,2", text)
        self.assertIn("Resolved 2 book(s): 1, 2", text)
        self.assertIn("- add tag 'Batch'", text)
        self.assertEqual(self._tag_map(), {1: ["Curated"], 2: []})

    def test_dry_run_json_shape(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-clear-tags",
                    "--format",
                    "json",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["dry_run"], True)
        self.assertEqual(report["committed"], False)
        self.assertEqual(report["results"], [])
        self.assertEqual(report["target"], "--ids 1")
        self.assertEqual(report["verbs"], ["clear tags"])
        self.assertEqual(self._tag_map()[1], ["Curated"])


class TestApply(_TempDBCase):
    """--apply: mandatory --backup-dir outside the library, one batch()
    transaction, honest per-verb reporting."""

    def test_apply_requires_backup_dir(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-add-tag",
                    "Batch",
                    "--apply",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("--backup-dir", err_cap.getvalue())
        self.assertEqual(self._tag_map()[1], ["Curated"])

    def test_backup_dir_inside_library_refused(self):
        lib_dir = os.path.dirname(self.db_path)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-add-tag",
                    "Batch",
                    "--apply",
                    "--backup-dir",
                    lib_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)
        self.assertIn("OUTSIDE the library directory", err_cap.getvalue())

    def test_apply_happy_path(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1,2",
                    "--batch-add-tag",
                    "Batch",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("Backed up metadata.db to", text)
        self.assertIn("add tag 'Batch': 2 applied, 0 already-so, 0 failed", text)
        self.assertIn(
            "Committed as one transaction: 2 applied, 0 already-so, 0 failed.",
            text,
        )
        self.assertEqual(self._tag_map(), {1: ["Batch", "Curated"], 2: ["Batch"]})
        self.assertTrue(os.path.exists(os.path.join(backup_dir, "metadata.db")))

    def test_apply_reports_already_so(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1,2",
                    "--batch-clear-tags",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("clear tags: 1 applied, 1 already-so, 0 failed", out.getvalue())
        self.assertEqual(self._tag_map(), {1: [], 2: []})

    def test_failure_rolls_everything_back(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-add-tag",
                    "Batch",
                    "--batch-set-column",
                    "missing",
                    "X",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 1)
        self.assertIn("book 1, set #missing = 'X'", err_cap.getvalue())
        self.assertIn("Nothing was written", out.getvalue())
        self.assertEqual(self._tag_map()[1], ["Curated"])

    def test_commit_per_book_escape_hatch(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1,2",
                    "--batch-add-tag",
                    "Batch",
                    "--apply",
                    "--commit-per-book",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("Committed per book (2 transactions)", out.getvalue())
        self.assertEqual(self._tag_map(), {1: ["Batch", "Curated"], 2: ["Batch"]})

    def test_apply_json_shape(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-add-tag",
                    "Batch",
                    "--apply",
                    "--format",
                    "json",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        report = json.loads(out.getvalue())
        self.assertEqual(report["committed"], True)
        self.assertEqual(report["dry_run"], False)
        self.assertEqual(report["verbs"], ["add tag 'Batch'"])
        self.assertEqual(
            report["results"],
            [{"id": 1, "verb": "add tag 'Batch'", "status": "applied", "detail": None}],
        )

    def test_series_index_lands(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1,2",
                    "--batch-set-series",
                    "Wings",
                    "--series-index",
                    "2.5",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        try:
            rows = con.execute(
                "SELECT l.book, s.name, b.series_index FROM books_series_link l "
                "JOIN series s ON s.id = l.series JOIN books b ON b.id = l.book "
                "ORDER BY l.book"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(rows, [(1, "Wings", 2.5), (2, "Wings", 2.5)])

    def test_series_index_without_batch_set_series_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-clear-tags",
                    "--series-index",
                    "2",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)


class TestRatingCarveOut(_TempDBCase):
    """--batch-clear-rating is manifest-only, mechanically enforced; the
    banned labels are refused by name."""

    def test_rating_clear_refused_with_ids(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(["--ids", "1", "--batch-clear-rating", "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("--from-manifest", err_cap.getvalue())
        linked = (
            sqlite3.connect(self.db_path)
            .execute("SELECT COUNT(*) FROM books_ratings_link")
            .fetchone()[0]
        )
        self.assertEqual(linked, 1)

    def test_rating_clear_refused_with_search_and_untagged(self):
        for source in (["--from-search", "tags:Curated"], ["--from-untagged"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = main(source + ["--batch-clear-rating", "--db", self.db_path])
            self.assertEqual(rc, 2)

    def test_rating_clear_legal_with_manifest(self):
        fd, path = tempfile.mkstemp(suffix=".ids", text=True)
        os.write(fd, b"1\n")
        os.close(fd)
        self.addCleanup(os.remove, path)
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--from-manifest",
                    path,
                    "--batch-clear-rating",
                    "--batch-clear-tags",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        try:
            linked = con.execute(
                "SELECT COUNT(*) FROM books_ratings_link WHERE book = 1"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(linked, 0)  # a true clear, no phantom 0 row
        self.assertIn("clear rating: 1 applied", out.getvalue())

    def test_banned_labels_refused_by_name(self):
        cases = [
            ["--batch-set-column", "reading_status", "x"],
            ["--batch-set-column", "#status", "x"],
            ["--batch-clear-column", "DATE_READ"],
            ["--batch-add-column-value", "date_read", "x"],
        ]
        for verb in cases:
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err) as err_cap:
                rc = main(["--ids", "1"] + verb + ["--db", self.db_path])
            self.assertEqual(rc, 2, verb)
            self.assertIn("banned for set writes", err_cap.getvalue())

    def test_add_column_value_appends(self):
        backup_dir = tempfile.mkdtemp(prefix="cquarry_bak_")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-add-column-value",
                    "audience",
                    "Brandon",
                    "--apply",
                    "--backup-dir",
                    backup_dir,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertIn("add #audience value 'Brandon': 1 applied", out.getvalue())
        con = sqlite3.connect(self.db_path)
        try:
            values = [
                r[0]
                for r in con.execute(
                    "SELECT c.value FROM books_custom_column_1_link l "
                    "JOIN custom_column_1 c ON c.id = l.value WHERE l.book = 1"
                )
            ]
        finally:
            con.close()
        self.assertEqual(values, ["Brandon"])


class TestApplyModeDangling(_TempDBCase):
    """--apply and friends mean nothing without a set write."""

    def test_apply_alone_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err) as err_cap:
            rc = main(["--apply", "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("need a target source", err_cap.getvalue())

    def test_backup_dir_alone_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--backup-dir", "/tmp", "--db", self.db_path])
        self.assertEqual(rc, 2)

    def test_csv_format_refused_for_set_writes(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--ids",
                    "1",
                    "--batch-clear-tags",
                    "--format",
                    "csv",
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 2)


class TestSingleBookRatingZeroRemap(_TempDBCase):
    """--set-rating ID 0 remaps to a true clear (Brandon's 2026-09-06
    decision): the phantom 0-rating row is gone, matching Calibre's own
    0-stars semantics."""

    def test_zero_clears_an_existing_rating(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-rating", "1", "0", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("Cleared rating of book 1.", out.getvalue())
        con = sqlite3.connect(self.db_path)
        try:
            linked = con.execute(
                "SELECT COUNT(*) FROM books_ratings_link WHERE book = 1"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(linked, 0)

    def test_zero_on_unrated_book_is_an_honest_no_op(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--set-rating", "2", "0", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("Already clear:", out.getvalue())
        con = sqlite3.connect(self.db_path)
        try:
            for_book_2 = con.execute(
                "SELECT COUNT(*) FROM books_ratings_link WHERE book = 2"
            ).fetchone()[0]
            total = con.execute("SELECT COUNT(*) FROM ratings").fetchone()[0]
        finally:
            con.close()
        self.assertEqual(for_book_2, 0)  # no phantom row written
        self.assertEqual(total, 1)  # book 1's seeded rating untouched


if __name__ == "__main__":
    unittest.main()
