"""Tests for the acquisition run verbs (`cquarry run phase1/2/3`).

The orchestrators drive external instruments (companion scripts, bindery,
calibredb) through subprocess seams; those seams are mocked here so the
suite exercises each verb's contract — guards, manifest flow, the one-
batch import, the decisions taxonomy, and phase 3's answer-file curation —
against throwaway fixture databases.
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from cquarry_cli import manifest
from cquarry_cli.run import _stamps_from_filename, run_phase1, run_phase2, run_phase3

# The phase-2 import fixture: the add_book INSERT-path hazards (AUTOINCREMENT
# + books_insert_trg needing title_sort()/uuid4()) plus the #source (direct
# storage) and #audience (multi-valued link) columns the run stamps.
_RUN_SCHEMA = """
CREATE TABLE books (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, sort TEXT,
    author_sort TEXT, timestamp TEXT, pubdate TEXT, series_index REAL,
    has_cover INTEGER DEFAULT 0, uuid TEXT, path TEXT, last_modified TEXT
);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT UNIQUE, sort TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT);
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT,
    UNIQUE(book, tag));
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT UNIQUE, sort TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT);
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT UNIQUE);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT UNIQUE, sort TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT);
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT UNIQUE);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT);
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT,
    uncompressed_size INT, name TEXT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT,
    val TEXT, UNIQUE(book, type));
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT, val TEXT);
CREATE TABLE metadata_dirtied (id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
    UNIQUE(book));
CREATE TABLE books_pages_link (book INTEGER PRIMARY KEY, pages INTEGER DEFAULT 0);
CREATE TRIGGER books_insert_trg AFTER INSERT ON books
BEGIN
    UPDATE books SET sort = title_sort(NEW.title), uuid = uuid4()
    WHERE id = NEW.id;
END;
CREATE TRIGGER books_pages_link_create_trigger AFTER INSERT ON books FOR EACH ROW
BEGIN
    INSERT INTO books_pages_link(book) VALUES (NEW.id);
END;
CREATE TABLE custom_columns (
    id INTEGER PRIMARY KEY, label TEXT UNIQUE, name TEXT, datatype TEXT,
    is_multiple BOOL, editable BOOL DEFAULT 1, display TEXT DEFAULT '{}'
);
CREATE TABLE custom_column_10 (id INTEGER PRIMARY KEY, book INTEGER, value TEXT);
CREATE TABLE custom_column_11 (id INTEGER PRIMARY KEY, value TEXT UNIQUE);
CREATE TABLE books_custom_column_11_link (book INTEGER, value INTEGER,
    UNIQUE(book, value));
INSERT INTO custom_columns VALUES (10, 'source', 'Source', 'text', 0, 1, '{}');
INSERT INTO custom_columns VALUES (11, 'audience', 'Audience', 'text', 1, 1, '{}');
"""


def _build_library(db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.executescript(_RUN_SCHEMA)
    # The seeded inserts fire books_insert_trg.
    from cquarry.write import register_udfs

    register_udfs(con)
    con.commit()
    con.close()


class RunCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cquarry_run_")
        self.library = os.path.join(self.temp_dir, "library")
        self.downloads = os.path.join(self.temp_dir, "downloads")
        os.makedirs(self.library)
        os.makedirs(self.downloads)
        self.db_path = os.path.join(self.library, "metadata.db")
        _build_library(self.db_path)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def _make_file(self, name: str, payload: bytes = b"EPUBDATA") -> str:
        path = os.path.join(self.downloads, name)
        with open(path, "wb") as f:
            f.write(payload)
        return path


class TestFilenameStamps(unittest.TestCase):
    def test_author_title_pattern(self):
        stamps = _stamps_from_filename("Ann Leckie - Fifth Head of Data.epub")
        self.assertEqual(stamps["title"], "Fifth Head of Data")
        self.assertEqual(stamps["authors"], ["Ann Leckie"])

    def test_unparseable_falls_back_to_stem(self):
        stamps = _stamps_from_filename("random_download_9812.epub")
        self.assertEqual(stamps["title"], "random_download_9812")
        self.assertEqual(stamps["authors"], [])


class TestRunPhase1(RunCase):
    def test_vets_creates_manifest_quarantines_and_flags(self):
        good = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        bad = self._make_file("locked.pdf")
        dup = self._make_file("already_here.epub")
        with (
            mock.patch(
                "cquarry_cli.run._screen_duplicates",
                return_value={"duplicates": [{"path": dup}]},
            ),
            mock.patch(
                "cquarry_cli.run._drm_verdicts",
                return_value={bad: "ADEPT", good: "clean", dup: "clean"},
            ),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(bad))  # quarantined away
        self.assertTrue(os.path.isdir(os.path.join(self.downloads, "_quarantine")))
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        paths = {f["path"]: f["verdict"] for f in man["files"]}
        self.assertEqual(paths[good], "approved_for_import")
        self.assertEqual(paths[dup], "duplicate_refused")
        self.assertNotIn(bad, paths)  # moved; recorded in quarantines
        self.assertEqual(len(man["quarantines"]), 1)
        kinds = [d["kind"] for d in man["decisions_needed"]]
        self.assertIn("duplicate", kinds)
        self.assertIn("manual_repair", kinds)
        self.assertEqual(man["approved_for_import"], [good])
        entry = manifest.file_by_path(man, good)
        self.assertEqual(entry["stamps"]["authors"], ["Ann Leckie"])

    def test_missing_directory_exits_two(self):
        rc = run_phase1(os.path.join(self.temp_dir, "nope"), self.db_path)
        self.assertEqual(rc, 2)

    def test_empty_directory_is_clean_zero(self):
        rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)


class TestRunPhase2(RunCase):
    def _manifest(self, *paths, signed=True):
        man = manifest.new_manifest(self.downloads)
        for path in paths:
            entry = manifest.new_file_entry(path)
            entry["size"] = os.path.getsize(path)
            entry["provenance"] = "Standard Ebooks"
            entry["stamps"] = {
                "title": os.path.splitext(os.path.basename(path))[0],
                "authors": ["Ann Leckie"],
                "isbn": "9780000000000",
            }
            entry["verdict"] = "approved_for_import"
            manifest.add_file(man, entry)
        manifest.approve(man, list(paths))
        if signed:
            manifest.sign(man)
        path = os.path.join(self.temp_dir, "batch.json")
        manifest.save(man, path)
        return path

    def _import(self, manifest_path, **kw):
        backup = os.path.join(self.temp_dir, "backups")
        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=False),
            mock.patch("cquarry_cli.run._fetch_metadata", return_value="no_result"),
        ):
            rc = run_phase2(manifest_path, self.db_path, backup_dir=backup, **kw)
        return rc, backup

    def test_guards_fire_before_anything_touches_the_library(self):
        path = self._make_file("Fifth Head.epub")
        unsigned = self._manifest(path, signed=False)
        self.assertEqual(self._import(unsigned)[0], 2)
        signed = self._manifest(path)
        with mock.patch("cquarry_cli.run.calibre_running", return_value=True):
            rc = run_phase2(
                signed, self.db_path, backup_dir=os.path.join(self.temp_dir, "b")
            )
        self.assertEqual(rc, 2)
        with mock.patch("cquarry_cli.run.calibre_running", return_value=False):
            rc = run_phase2(signed, self.db_path, backup_dir=None)
        self.assertEqual(rc, 2)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)  # nothing imported by any refused run

    def test_open_decisions_block_the_import(self):
        path = self._make_file("Fifth Head.epub")
        man_path = self._manifest(path)
        man = manifest.load(man_path)
        manifest.add_decision(man, "duplicate", file=path, existing_id=1)
        manifest.save(man, man_path)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 2)

    def test_import_stamps_audience_source_and_records_download(self):
        first = self._make_file("Fifth Head of Data.epub")
        second = self._make_file("Ancillary Justice.epub")
        man_path = self._manifest(first, second)
        rc, backup = self._import(man_path)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(os.path.join(backup, "metadata.db")))
        con = sqlite3.connect(self.db_path)
        rows = con.execute("SELECT id, title FROM books ORDER BY id").fetchall()
        con.close()
        self.assertEqual(len(rows), 2)  # both approved files imported
        man = manifest.load(man_path)
        ids = [f["import"]["imported_id"] for f in man["files"]]
        self.assertEqual(sorted(ids), sorted(r[0] for r in rows))
        for entry in man["files"]:
            self.assertEqual(entry["import"]["clears"]["rating_cleared"], False)
            self.assertEqual(entry["import"]["download_outcome"], "no_result")
        kinds = [d["kind"] for d in man["decisions_needed"]]
        self.assertEqual(kinds, ["metadata_download", "metadata_download"])

    def test_source_and_audience_columns_written(self):
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        source = con.execute("SELECT value FROM custom_column_10").fetchall()
        audience = con.execute(
            "SELECT c.value FROM books_custom_column_11_link l "
            "JOIN custom_column_11 c ON c.id = l.value"
        ).fetchall()
        con.close()
        self.assertEqual(source, [("Standard Ebooks",)])
        self.assertEqual(audience, [("Brandon",)])

    def test_resumable_second_run_skips_imported(self):
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 0)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 1)  # the second run imported nothing new


class TestRunPhase3(RunCase):
    def _ready_manifest(self, imported_ids):
        man = manifest.new_manifest(self.downloads)
        entry = manifest.new_file_entry("book.epub")
        entry["import"]["imported_id"] = imported_ids[0]
        manifest.add_file(man, entry)
        for extra in imported_ids[1:]:
            e = manifest.new_file_entry(f"book{extra}.epub")
            e["import"]["imported_id"] = extra
            manifest.add_file(man, e)
        man["signed"] = True
        path = os.path.join(self.temp_dir, "batch.json")
        manifest.save(man, path)
        return path

    def _rich_library(self):
        """Books 10 (untagged) and 11 (tagged) in a dossier-capable schema,
        reusing test_read_modes' builder plus extra rows."""
        from test_read_modes import _build

        os.remove(self.db_path)  # setUp built the run schema; rebuild rich
        _build(self.db_path)
        con = sqlite3.connect(self.db_path)
        con.executescript(
            """
            DROP TABLE comments;
            CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT);
            INSERT INTO comments (book, text)
                VALUES (1, '<p>A <b>desert</b> planet.</p>');
            INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,
                has_cover,last_modified,series_index,path,uuid) VALUES
                (10,'New Import','New Import','X','2026-09-06','2026-09-06',
                 0,'2026-09-06',1.0,'x/new','uuid-10'),
                (11,'Tagged Import','Tagged Import','X','2026-09-06',
                 '2026-09-06',0,'2026-09-06',1.0,'x/tagged','uuid-11');
            INSERT INTO data (book,format,name,uncompressed_size) VALUES
                (10,'EPUB','New Import',10),(11,'EPUB','Tagged Import',10);
            INSERT INTO books_tags_link (book,tag) VALUES (11,1);
            """
        )
        con.commit()
        con.close()

    def _answers(self):
        path = os.path.join(self.temp_dir, "answers.json")
        with open(path, "w") as f:
            json.dump(
                {
                    "10": {
                        "tags": ["SciFi", "Curated"],
                        "comments_html": "<p>House voice.</p>",
                        "fixes": {"publisher": "Ace"},
                    }
                },
                f,
            )
        return path

    def test_curation_batch_mechanical_pass_and_record(self):
        self._rich_library()
        man_path = self._ready_manifest([10, 11])
        answers = self._answers()
        proc = mock.Mock(returncode=0, stderr="", stdout="")
        with mock.patch("cquarry_cli.run._run", return_value=proc) as run_mock:
            rc = run_phase3(man_path, self.db_path, answer_file=answers)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        tags = [
            r[0]
            for r in con.execute(
                "SELECT t.name FROM books_tags_link l JOIN tags t "
                "ON t.id = l.tag WHERE l.book = 10"
            )
        ]
        comments = con.execute("SELECT text FROM comments WHERE book = 10").fetchone()[
            0
        ]
        publisher = con.execute(
            "SELECT p.name FROM books_publishers_link pl JOIN publishers p "
            "ON p.id = pl.publisher WHERE pl.book = 10"
        ).fetchone()[0]
        con.close()
        self.assertEqual(sorted(tags), ["Curated", "SciFi"])
        self.assertEqual(comments, "<p>House voice.</p>")
        self.assertEqual(publisher, "Ace")
        driven = [" ".join(c.args[0][:2]) for c in run_mock.call_args_list]
        self.assertIn("bindery run", driven)
        self.assertTrue(
            any(
                "reconcile_file_metadata.py" in " ".join(c.args[0])
                for c in run_mock.call_args_list
            )
        )
        self.assertTrue(
            any(
                "validate_metadata.py" in " ".join(c.args[0])
                for c in run_mock.call_args_list
            )
        )
        records = os.listdir(os.path.join(self.library, ".claude"))
        self.assertTrue(any(n.startswith("project_import_") for n in records))

    def test_already_tagged_books_are_skipped(self):
        self._rich_library()
        man_path = self._ready_manifest([10, 11])
        answers = self._answers()
        with mock.patch(
            "cquarry_cli.run._run",
            return_value=mock.Mock(returncode=0, stderr="", stdout=""),
        ):
            rc = run_phase3(man_path, self.db_path, answer_file=answers)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        kept = con.execute(
            "SELECT COUNT(*) FROM books_tags_link WHERE book = 11"
        ).fetchone()[0]
        con.close()
        self.assertEqual(kept, 1)  # book 11 was not in the untagged batch set

    def test_no_answers_and_no_tty_exits_two(self):
        self._rich_library()
        man_path = self._ready_manifest([10])
        rc = run_phase3(man_path, self.db_path, answer_file=None)
        self.assertEqual(rc, 2)

    def test_validator_failure_exits_one(self):
        self._rich_library()
        man_path = self._ready_manifest([10])
        with mock.patch(
            "cquarry_cli.run._run",
            return_value=mock.Mock(returncode=1, stderr="", stdout=""),
        ):
            rc = run_phase3(
                man_path, self.db_path, answer_file=self._answers(), quiet=True
            )
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
