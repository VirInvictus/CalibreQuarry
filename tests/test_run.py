"""Tests for the acquisition run verbs (`cquarry run phase1/2/3`).

The orchestrators drive external instruments (companion scripts, bindery,
calibredb) through subprocess seams; those seams are mocked here so the
suite exercises each verb's contract — guards, manifest flow, the one-
batch import, the decisions taxonomy, and phase 3's answer-file curation —
against throwaway fixture databases. The exception is TestPhase1Seams,
which pins the two phase-1 seams to their instruments' REAL shapes (the
2026-09-08 sweep found both seams mocked everywhere else, so a phase 1
that crashed on first real contact shipped green).
"""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

from cquarry_cli import manifest
from cquarry_cli.run import (
    _bindery_phase1,
    _screen_duplicates,
    _stamps_from_filename,
    run_phase1,
    run_phase2,
    run_phase3,
    sign_manifest,
)

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
CREATE TABLE custom_column_10 (id INTEGER PRIMARY KEY, value TEXT UNIQUE);
CREATE TABLE books_custom_column_10_link (book INTEGER, value INTEGER,
    UNIQUE(book, value));
CREATE TABLE custom_column_11 (id INTEGER PRIMARY KEY, value TEXT UNIQUE);
CREATE TABLE books_custom_column_11_link (book INTEGER, value INTEGER,
    UNIQUE(book, value));
-- #source mirrors the real library: enumeration, normalized storage, the
-- real enum values (cquarry 1.17's dispatch refuses the text+direct shape
-- this fixture used to model, which no real Calibre schema creates).
INSERT INTO custom_columns VALUES (10, 'source', 'Source', 'enumeration', 0, 1,
    '{"enum_values": ["Standard Ebooks", "Library Genesis", "Bought EPUB", "Bought physical", "ripped", "Anna''s Archive", "Free", "Gifted", "Other"]}');
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
                return_value={dup},
            ),
            mock.patch(
                "cquarry_cli.run._drm_verdicts",
                return_value={bad: "DRM", good: "CLEAN", dup: "CLEAN"},
            ),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path, quarantine=True)
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

    def test_benign_and_na_verdicts_never_quarantine(self):
        # The sweep's finding: audit_drm's BENIGN (font obfuscation) and
        # N/A (DJVU) are not locks; quarantining them moved every DJVU in
        # a batch and contradicted phase 1's dry promise.
        epub = self._make_file("font_obf.epub")
        djvu = self._make_file("plain_scan.djvu", payload=b"DJVUDATA")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch(
                "cquarry_cli.run._drm_verdicts",
                return_value={epub: "BENIGN", djvu: "N/A"},
            ),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(os.path.join(self.downloads, "_quarantine")))
        self.assertTrue(os.path.exists(epub))
        self.assertTrue(os.path.exists(djvu))
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertEqual(man["decisions_needed"], [])
        self.assertEqual(man["quarantines"], [])

    def test_drm_hit_without_the_flag_is_recorded_but_stays(self):
        path = self._make_file("locked.epub")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={path: "DRM"}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(path))  # dry: nothing moved
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertEqual(man["quarantines"][0]["moved_to"], None)
        self.assertEqual(man["decisions_needed"][0]["kind"], "manual_repair")
        self.assertNotIn(path, man["approved_for_import"])

    def test_error_verdict_quarantines_like_drm(self):
        # ERROR is the other half of audit_drm's is_problem set: a scan
        # that could not verify gets the same conservative treatment.
        path = self._make_file("unreadable.epub")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={path: "ERROR"}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path, quarantine=True)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(path))
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertNotEqual(man["quarantines"][0]["moved_to"], None)

    def test_missing_directory_exits_two(self):
        rc = run_phase1(os.path.join(self.temp_dir, "nope"), self.db_path)
        self.assertEqual(rc, 2)

    def test_empty_directory_is_clean_zero(self):
        rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)


class TestPhase1Seams(RunCase):
    """The two phase-1 subprocess seams, against their instruments' real
    contract (the 2026-09-08 sweep's P0: both seams used to be mocked in
    every test, and both crashed on first real contact — the duplicate
    report was read as a dict, and bindery's --json was invoked with no
    value). Mocked here at the subprocess boundary with the real payload
    shapes, plus one run against the actual screen_duplicate.py."""

    def test_screen_duplicates_keeps_only_hit_records(self):
        # screen_duplicate's JSON report is a bare list holding EVERY
        # screened file; only records with hits mean a duplicate.
        hit = self._make_file("Hit Author - Hit Title.epub")
        clean = self._make_file("Clean Author - Clean Title.epub")
        report = [
            {
                "file": hit,
                "title": "Hit Title",
                "authors": ["Hit Author"],
                "isbn": "",
                "library_hits": [{"id": 1, "title": "Hit Title"}],
            },
            {
                "file": clean,
                "title": "Clean Title",
                "authors": ["Clean Author"],
                "isbn": "",
                "library_hits": [],
            },
            {
                "file": "/tmp/batch_mate.epub",
                "title": "Clean Title",
                "authors": ["Clean Author"],
                "isbn": "",
                "library_hits": [],
                "batch_duplicates": [clean],
            },
        ]
        proc = mock.Mock(returncode=0, stdout=json.dumps(report), stderr="")
        with mock.patch("cquarry_cli.run._run", return_value=proc):
            dups = _screen_duplicates([hit, clean], self.db_path)
        self.assertEqual(dups, {hit, "/tmp/batch_mate.epub"})

    def test_screen_duplicates_skips_the_subprocess_when_nothing_screenable(self):
        with mock.patch("cquarry_cli.run._run") as run_mock:
            self.assertEqual(_screen_duplicates([], self.db_path), set())
        run_mock.assert_not_called()

    def test_screen_duplicates_raises_on_setup_error(self):
        # rc 2 is screen_duplicate's setup error (unreadable library);
        # silence would approve files the screen never judged.
        proc = mock.Mock(returncode=2, stdout="", stderr="cannot open the library")
        with mock.patch("cquarry_cli.run._run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "screen_duplicate failed"):
                _screen_duplicates([self._make_file("x.epub")], self.db_path)

    def test_screen_duplicates_raises_on_unparseable_report(self):
        proc = mock.Mock(returncode=0, stdout="not json", stderr="")
        with mock.patch("cquarry_cli.run._run", return_value=proc):
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                _screen_duplicates([self._make_file("x.epub")], self.db_path)

    def test_screen_duplicates_against_the_real_script(self):
        # The instrument itself over the fixture library, no mocks: the
        # real bare-list report flows through the seam. (The screen tool's
        # filename fallback reads "A - B" as title A / author B — the
        # reverse of run.py's stamp parser — so the seeded match mirrors
        # that split; with embedded metadata the real fields come from
        # ebook-meta and no fallback happens.)
        if shutil.which("ebook-meta") is None:
            self.skipTest("needs Calibre's ebook-meta on PATH")
        epub = self._make_file("Seeded Author - Seeded Title.epub")
        con = sqlite3.connect(self.db_path)
        from cquarry.write import register_udfs

        register_udfs(con)
        book = con.execute(
            "INSERT INTO books (title) VALUES ('Seeded Author')"
        ).lastrowid
        author = con.execute(
            "INSERT INTO authors (name) VALUES ('Seeded Title')"
        ).lastrowid
        con.execute(
            "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
            (book, author),
        )
        con.commit()
        con.close()
        self.assertEqual(_screen_duplicates([epub], self.db_path), {epub})

    def test_phase1_hands_the_screen_only_screenable_files(self):
        # The pre-filter is screen_duplicate's own extension set: a djvu
        # inventory never reaches it (its exit-2 "no ebook files" was the
        # djvu-only crash), so the seam runs only when it has work.
        epub = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        djvu = self._make_file("broken_scan.djvu", payload=b"DJVUDATA")
        seen = {}

        def fake_screen(files, db):
            seen["files"] = files
            return set()

        with (
            mock.patch("cquarry_cli.run._screen_duplicates", side_effect=fake_screen),
            mock.patch(
                "cquarry_cli.run._drm_verdicts",
                return_value={epub: "clean", djvu: "clean"},
            ),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        self.assertEqual(seen["files"], [epub])

    def test_djvu_only_directory_runs_clean(self):
        # The regression: a djvu-only tree used to die at the screen seam
        # (AttributeError on the list report, or the script's exit 2).
        scan = self._make_file("broken_scan.djvu", payload=b"DJVUDATA")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={scan: "clean"}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertEqual(
            manifest.file_by_path(man, scan)["verdict"], "approved_for_import"
        )

    @staticmethod
    def _bindery_proc(returncode, payload=None, stderr=""):
        def side_effect(cmd, **kw):
            report = cmd[cmd.index("--json") + 1]
            if payload is not None:
                with open(report, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            return mock.Mock(returncode=returncode, stdout="prose", stderr=stderr)

        return side_effect

    _BINDERY_PAYLOAD = {
        "mode": "phase1",
        "root": "/tmp/downloads",
        "apply_lossy": False,
        "summary": {"books": 1, "clean": 0, "problem": 1, "error": 0},
        "decisions_needed": [],
        "books": [{"path": "/tmp/downloads/x.epub", "status": "problem"}],
    }

    # The bindery seam tests pin the entry-point lookup so they exercise
    # the report handling on hosts without a bindery installed (CI); the
    # absent-entry-point degrade has its own test below.
    @staticmethod
    def _has_bindery():
        return mock.patch("shutil.which", return_value="/opt/bindery/bin/bindery")

    def test_bindery_phase1_reads_the_report_file_and_tolerates_trouble(self):
        # Exit 2 is bindery's "trouble found" — the normal outcome over a
        # directory holding any problem book — with the report still
        # written; it must parse, not raise.
        seen = {}
        real_side_effect = self._bindery_proc(2, payload=self._BINDERY_PAYLOAD)

        def side_effect(cmd, **kw):
            seen["report"] = cmd[cmd.index("--json") + 1]
            return real_side_effect(cmd, **kw)

        with (
            self._has_bindery(),
            mock.patch("cquarry_cli.run._run", side_effect=side_effect),
        ):
            shape = _bindery_phase1(self.downloads, apply_lossy=False)
        self.assertEqual(shape["summary"]["problem"], 1)
        self.assertFalse(os.path.exists(seen["report"]))  # temp file cleaned up
        self.assertNotIn(self.downloads, seen["report"])  # never litter the tree

    def test_bindery_phase1_degrades_on_invocation_failure(self):
        # rc 1 is a broken invocation or an epub-less tree: no report, and
        # the slice is simply unavailable.
        proc = mock.Mock(returncode=1, stdout="", stderr="no .epub files under root")
        with (
            self._has_bindery(),
            mock.patch("cquarry_cli.run._run", return_value=proc),
        ):
            self.assertEqual(_bindery_phase1(self.downloads, apply_lossy=False), {})

    def test_bindery_phase1_raises_when_trouble_writes_no_report(self):
        # The pre-run-slices entry point: argparse rejects `run` with its
        # own exit 2 and no report. The seam names it instead of sailing on.
        proc = mock.Mock(returncode=2, stdout="", stderr="invalid choice: 'run'")
        with (
            self._has_bindery(),
            mock.patch("cquarry_cli.run._run", return_value=proc),
        ):
            with self.assertRaisesRegex(RuntimeError, "no readable report"):
                _bindery_phase1(self.downloads, apply_lossy=False)

    def test_bindery_phase1_raises_on_unexpected_exit_codes(self):
        proc = mock.Mock(returncode=3, stdout="", stderr="boom")
        with (
            self._has_bindery(),
            mock.patch("cquarry_cli.run._run", return_value=proc),
        ):
            with self.assertRaisesRegex(RuntimeError, "bindery run phase1 failed"):
                _bindery_phase1(self.downloads, apply_lossy=False)

    def test_bindery_phase1_without_the_entry_point_degrades(self):
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch("cquarry_cli.run._run") as run_mock,
        ):
            self.assertEqual(_bindery_phase1(self.downloads, apply_lossy=False), {})
        run_mock.assert_not_called()


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

    def test_phase2_refuses_a_tampered_manifest(self):
        # The seal (the sweep's P0): a manifest edited after signing must
        # fail the load loudly, never import the tampered content.
        path = self._make_file("Fifth Head.epub")
        man_path = self._manifest(path)
        with open(man_path, encoding="utf-8") as f:
            data = json.load(f)
        data["files"][0]["stamps"]["title"] = "Tampered"
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 2)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)  # nothing imported from the forged stamps

    def test_import_stamps_audience_source_and_records_download(self):
        # Distinct payloads: add_book's byte-identity floor (cquarry 1.15)
        # refuses two catalogued-identical files in one pass, as it should.
        first = self._make_file("Fifth Head of Data.epub")
        second = self._make_file("Ancillary Justice.epub", payload=b"ANCILLARY")
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
        source = con.execute(
            "SELECT c.value FROM books_custom_column_10_link l "
            "JOIN custom_column_10 c ON c.id = l.value"
        ).fetchall()
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


class TestRunSign(RunCase):
    """`cquarry run sign`: the sanctioned sealing path. Hand-editing
    `"signed": true` stopped being a signature the moment signatures bound
    to content."""

    def _unsigned_manifest(self):
        man = manifest.new_manifest(self.downloads)
        path = self._make_file("Fifth Head.epub")
        entry = manifest.new_file_entry(path)
        entry["size"] = os.path.getsize(path)
        entry["verdict"] = "approved_for_import"
        entry["stamps"] = {"title": "Fifth Head", "authors": ["Ann Leckie"]}
        manifest.add_file(man, entry)
        manifest.approve(man, [path])
        man_path = os.path.join(self.temp_dir, "batch.json")
        manifest.save(man, man_path)
        return man_path

    def test_signs_and_loads(self):
        man_path = self._unsigned_manifest()
        self.assertEqual(sign_manifest(man_path), 0)
        man = manifest.load(man_path)
        self.assertTrue(man["signed"])
        self.assertTrue(man["signature"])

    def test_resign_after_a_post_sign_edit(self):
        man_path = self._unsigned_manifest()
        self.assertEqual(sign_manifest(man_path), 0)
        with open(man_path, encoding="utf-8") as f:
            data = json.load(f)
        data["files"][0]["stamps"]["title"] = "Corrected"
        with open(man_path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(ValueError):
            manifest.load(man_path)
        self.assertEqual(sign_manifest(man_path), 0)
        man = manifest.load(man_path)
        self.assertEqual(man["files"][0]["stamps"]["title"], "Corrected")

    def test_missing_manifest_exits_two(self):
        self.assertEqual(sign_manifest(os.path.join(self.temp_dir, "nope.json")), 2)


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
