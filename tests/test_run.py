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

import io
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from unittest import mock

from cquarry_cli import manifest
from cquarry_cli.backups import make_backup
from cquarry_cli.cli import main
from cquarry_cli.run import (
    _apply_opf,
    _bindery_phase1,
    _date_only,
    _drive_stamp,
    _fetch_metadata,
    _inventory,
    _precedent_tags,
    _provenance_from_filename,
    _screen_duplicates,
    _stamps_from_embedded,
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
CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT);
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
-- `Z-Lib` is the renamed value (Brandon's spelling, 2026-09-13); the
-- 3.40-era "Z-Library" string no longer exists in the enum.
INSERT INTO custom_columns VALUES (10, 'source', 'Source', 'enumeration', 0, 1,
    '{"enum_values": ["Standard Ebooks", "Library Genesis", "Bought EPUB", "Bought physical", "ripped", "Anna''s Archive", "Free", "Gifted", "Other", "Z-Lib"]}');
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
        # The ebook-meta seam is mocked OFF by default: the fixture files
        # are payload bytes, and a real calibre on PATH would exit 0 with
        # its own filename-derived guess (CI has no calibre at all). The
        # seeding tests opt back in with explicit patching.
        which = mock.patch("cquarry_cli.run.shutil.which", return_value=None)
        which.start()
        self.addCleanup(which.stop)

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

    def test_observed_libgen_name_keeps_the_author_first_reading(self):
        # The decided convention (roadmap :902, 2026-09-10): the stamp
        # parser reads "Author - Title", the direction the real corpus
        # uses (libgen.li names, verified in the 09-10 Redwall run).
        # Calibre's import fallback guesses the opposite on purpose;
        # stamp_pdf's preview mirrors Calibre, this parser does not.
        stamps = _stamps_from_filename(
            "Brian Jacques - Mossflower (2012, Random House UK) - libgen.li.epub"
        )
        self.assertEqual(
            stamps["title"], "Mossflower (2012, Random House UK) - libgen.li"
        )
        self.assertEqual(stamps["authors"], ["Brian Jacques"])

    def test_unparseable_falls_back_to_stem(self):
        stamps = _stamps_from_filename("random_download_9812.epub")
        self.assertEqual(stamps["title"], "random_download_9812")
        self.assertEqual(stamps["authors"], [])


class TestProvenanceSeeds(unittest.TestCase):
    """The observed download naming (2026-09-08 and 2026-09-10 runs),
    mapped onto the library's #source vocabulary."""

    def test_the_anna_s_archive_trailer_seeds_anna_s_archive(self):
        # The real 09-10 name, typographic apostrophe and all.
        self.assertEqual(
            _provenance_from_filename(
                "Mattimeo (Redwall #3) -- Brian Jacques - -- Firebird -- "
                "Anna\u2019s Archive.epub"
            ),
            "Anna's Archive",
        )

    def test_z_library_site_naming_seeds_the_z_lib_enum_value(self):
        # The Z-Library enum decision (2026-09-12) as renamed on
        # 2026-09-13: z-lib naming seeds `Z-Lib`, Brandon's entry spelling
        # and the only form the library's #source enum carries now. The
        # 3.40-era "Z-Library" seed failed every phase-2 stamp until the
        # manifest was hand-corrected (the 2026-09-14 run).
        self.assertEqual(
            _provenance_from_filename(
                "How to do things with videogames (Bogost, Ian) "
                "(z-library.sk, 1lib.sk, z-lib.sk).pdf"
            ),
            "Z-Lib",
        )

    def test_libgen_names_seed_library_genesis(self):
        self.assertEqual(
            _provenance_from_filename(
                "[Redwall Book 2] Brian Jacques - Mossflower "
                "(2012, Random House UK) - libgen.li.epub"
            ),
            "Library Genesis",
        )

    def test_a_bare_name_seeds_nothing(self):
        self.assertIsNone(_provenance_from_filename("Lord Brocktree.epub"))


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
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
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
        self.assertEqual(paths[bad], "quarantined")  # in files[] AND quarantines
        self.assertEqual(len(man["quarantines"]), 1)
        kinds = [d["kind"] for d in man["decisions_needed"]]
        self.assertIn("duplicate", kinds)
        self.assertIn("manual_repair", kinds)
        self.assertEqual(man["approved_for_import"], [good])
        entry = manifest.file_by_path(man, good)
        self.assertEqual(entry["stamps"]["authors"], ["Ann Leckie"])

    def test_provenance_seeds_land_in_the_manifest(self):
        # The 09-10 box: the manifest's provenance field was writer-dead,
        # so phase 2's cc6 stamp fell back to a blanket Anna's Archive and
        # phase 3 re-derived provenance from filenames every batch.
        named = self._make_file(
            "Mattimeo (Redwall #3) -- Brian Jacques - -- Firebird -- "
            "Anna\u2019s Archive.epub"
        )
        bare = self._make_file("Lord Brocktree.epub", payload=b"BARE")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertEqual(
            manifest.file_by_path(man, named)["provenance"], "Anna's Archive"
        )
        self.assertIsNone(manifest.file_by_path(man, bare)["provenance"])

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

    def test_same_day_batches_never_overwrite_the_first_manifest(self):
        # THE FINAL AUDIT's HIGH: the fixed {date}-batch.json name let a
        # second same-day batch silently destroy the first batch's durable
        # record (imported ids, decisions) that phase 2 resume and phase 3
        # consume. The newcomer takes a numbered sibling instead.
        def vet():
            with (
                mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
                mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
                mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
                mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
            ):
                return run_phase1(self.downloads, self.db_path, quiet=True)

        first = self._make_file("Ann Leckie - First Book.epub")
        self.assertEqual(vet(), 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (first_manifest,) = os.listdir(manifests)
        with open(os.path.join(manifests, first_manifest), encoding="utf-8") as f:
            first_record = json.load(f)

        os.unlink(first)
        second = self._make_file("Ann Leckie - Second Book.epub")
        self.assertEqual(vet(), 0)
        self.assertEqual(len(os.listdir(manifests)), 2)
        second_manifest = first_manifest[: -len("-batch.json")] + "-batch-2.json"
        self.assertIn(second_manifest, os.listdir(manifests))
        with open(os.path.join(manifests, second_manifest), encoding="utf-8") as f:
            second_record = json.load(f)
        # The first record stands exactly as written; the sibling carries
        # the second batch's file.
        self.assertEqual([p["path"] for p in first_record["files"]], [first])
        self.assertEqual([p["path"] for p in second_record["files"]], [second])

    def test_quarantine_collision_gets_a_sibling_not_a_replacement(self):
        # Two same-named DRM files in different subdirs: the second move
        # used to land on the first and destroy it.
        first = self._make_file("same.epub")
        subdir = os.path.join(self.downloads, "sub")
        os.makedirs(subdir)
        second = os.path.join(subdir, "same.epub")
        with open(second, "wb") as f:
            f.write(b"SECOND")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch(
                "cquarry_cli.run._drm_verdicts",
                return_value={first: "DRM", second: "DRM"},
            ),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path, quarantine=True)
        self.assertEqual(rc, 0)
        quarantine_dir = os.path.join(self.downloads, "_quarantine")
        moved = sorted(os.listdir(quarantine_dir))
        self.assertEqual(moved, ["same-2.epub", "same.epub"])
        for name in moved:
            self.assertTrue(os.path.getsize(os.path.join(quarantine_dir, name)) > 0)

    def test_bindery_gate_accepted_repairs_mirror_into_lossy(self):
        # The 2026-09-14 hole: bindery's apply_lossy decision named books
        # with gate-accepted repairs, and the manifest still wrote
        # {flagged: false, repairs: []} -- the seal bound nothing, so
        # signing consented to repairs it never saw.
        pending = self._make_file("Security in Computing.epub")
        partial = self._make_file("half_stripped.epub")
        untouched = self._make_file("clean_edition.epub")
        shape = {
            "apply_lossy": False,
            "books": [
                {
                    "path": pending,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_pagination:3",
                        "fixes": {"stripped_pagination": 3},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                },
                {
                    "path": partial,
                    "repair": {
                        "status": "partial",
                        "summary": "stripped_watermarks:1 dropped_marker:1",
                        "fixes": {"stripped_watermarks": 1, "dropped_marker": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                },
                {"path": untouched, "repair": None},
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        lossy = manifest.file_by_path(man, pending)["lossy"]
        self.assertTrue(lossy["flagged"])
        self.assertEqual(
            lossy["repairs"],
            [
                {
                    "tool": "bindery",
                    "summary": "stripped_pagination:3",
                    "applied": False,
                    "lossy": True,
                }
            ],
        )
        self.assertTrue(manifest.file_by_path(man, partial)["lossy"]["flagged"])
        self.assertFalse(manifest.file_by_path(man, untouched)["lossy"]["flagged"])

    def test_structural_only_repairs_record_but_never_consent_gate(self):
        # The 2026-09-17/19 roadmap box: bindery's gate-accepted STRUCTURAL
        # fixes (alt text, invalid values, NCX sync) used to fire a
        # lossy_consent decision per file and the reviewer signed vacuous
        # consent. They still land in the sealed record (lossy: false on
        # each repair), but nothing flags and no decision is emitted.
        structural = self._make_file("confusable_classic.epub")
        shape = {
            "apply_lossy": False,
            "books": [
                {
                    "path": structural,
                    "repair": {
                        "status": "accept",
                        "summary": "add-img-alt:4, strip-invalid-value:2, ncx_uid_synced",
                        "fixes": {"add-img-alt": 4, "strip-invalid-value": 2},
                        "ncx_uid_synced": True,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, structural)
        self.assertFalse(entry["lossy"]["flagged"])
        self.assertEqual(
            entry["lossy"]["repairs"],
            [
                {
                    "tool": "bindery",
                    "summary": "add-img-alt:4, strip-invalid-value:2, ncx_uid_synced",
                    "applied": False,
                    "lossy": False,
                }
            ],
        )
        self.assertEqual(
            [d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"], []
        )

    def test_mixed_repair_flags_and_names_the_lossy_classes(self):
        # One file, both classes: the record carries each repair's class
        # and the decision detail names the lossy ones, so the reviewer
        # can see exactly what consent buys.
        mixed = self._make_file("annotated_edition.epub")
        shape = {
            "apply_lossy": False,
            "books": [
                {
                    "path": mixed,
                    "repair": {
                        "status": "accept",
                        "summary": "add-img-alt:2, stripped_watermarks:1",
                        "fixes": {"add-img-alt": 2, "stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, mixed)
        self.assertTrue(entry["lossy"]["flagged"])
        # One combined summary per book: the record is classed by its
        # whole string, and it carries the lossy key.
        self.assertEqual(
            [(r["summary"], r["lossy"]) for r in entry["lossy"]["repairs"]],
            [("add-img-alt:2, stripped_watermarks:1", True)],
        )
        (decision,) = [
            d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"
        ]
        self.assertIn("stripped_watermarks:1", decision["detail"])

    def test_lossy_class_comes_from_the_fixes_data_not_the_summary(self):
        # The 2026-09-21 adoption of bindery v0.45.0's structured records:
        # the class is judged by the fixes dict, never by vocabulary in the
        # rendered summary. This summary names no marker string, and under
        # the old substring contract the strip would have consent-gated as
        # structural -- the exact hole the data contract closes.
        terse = self._make_file("quiet_strip.epub")
        shape = {
            "apply_lossy": False,
            "books": [
                {
                    "path": terse,
                    "repair": {
                        "status": "accept",
                        "summary": "3 page layers removed",
                        "fixes": {"stripped_pagination": 3},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, terse)
        self.assertTrue(entry["lossy"]["flagged"])
        (decision,) = [
            d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"
        ]
        self.assertIn("3 page layers removed", decision["detail"])

    def test_embedded_metadata_seeds_stamps_over_the_filename_parse(self):
        # The 2026-09-18 seeding box: the file's embedded metadata (the
        # skill's deep-stamp pass wrote it) beats the filename parse;
        # ISBN is never seeded.
        path = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        embedded = {
            "title": "Verified Title",
            "authors": ["Verified Author"],
            "publisher": "Verified Press",
            "pubdate": "2001-05-01T00:00:00+00:00",
            "language": "eng",
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
            mock.patch(
                "cquarry_cli.run.shutil.which", return_value="/usr/bin/ebook-meta"
            ),
            mock.patch("cquarry_cli.run._stamps_from_embedded", return_value=embedded),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, path)
        self.assertEqual(entry["stamps"], embedded)
        self.assertNotIn("isbn", entry["stamps"])
        self.assertEqual(entry["repairs"], [])

    def test_unreadable_embedded_metadata_falls_back_with_a_record(self):
        # A file ebook-meta cannot read still ships: filename seeds plus
        # the failure on the record for the review pass to see.
        path = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
            mock.patch(
                "cquarry_cli.run.shutil.which", return_value="/usr/bin/ebook-meta"
            ),
            mock.patch("cquarry_cli.run._stamps_from_embedded", return_value=None),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, path)
        self.assertEqual(entry["stamps"]["title"], "Fifth Head of Data")
        self.assertEqual(entry["stamps"]["authors"], ["Ann Leckie"])
        self.assertEqual(
            entry["repairs"],
            [
                "ebook-meta could not read embedded metadata; "
                "stamps seeded from the filename"
            ],
        )

    def test_missing_ebook_meta_binary_keeps_the_filename_seeds(self):
        path = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
            mock.patch("cquarry_cli.run.shutil.which", return_value=None),
        ):
            buffer = io.StringIO()
            with redirect_stderr(buffer):
                rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        self.assertIn("ebook-meta not found", buffer.getvalue())
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, path)
        self.assertEqual(entry["stamps"]["title"], "Fifth Head of Data")
        self.assertEqual(entry["repairs"], [])

    def test_apply_lossy_run_records_the_repairs_as_applied(self):
        # --apply-lossy means the strips already happened file-side during
        # phase 1: the manifest's record must say so, or the batch record
        # understates what was done to the books.
        path = self._make_file("stripped_already.epub")
        shape = {
            "apply_lossy": True,
            "books": [
                {
                    "path": path,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                        "fixes": {"stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        lossy = manifest.file_by_path(man, path)["lossy"]
        self.assertEqual(lossy["repairs"][0]["applied"], True)

    def test_dry_phase1_emits_lossy_consent_decisions(self):
        # The lossy-pending flow's in-manifest home (roadmap: the double-
        # manifest wrinkle): a read-only phase 1 records a lossy_consent
        # decision per flagged file, so the reviewer can resolve it in
        # the manifest instead of the --apply-lossy re-run minting a
        # second batch file.
        flagged = self._make_file("Security in Computing.epub")
        clean = self._make_file("clean_edition.epub")
        shape = {
            "apply_lossy": False,
            "books": [
                {
                    "path": flagged,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                        "fixes": {"stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                },
                {"path": clean, "repair": None},
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        consents = [d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"]
        self.assertEqual([d["file"] for d in consents], [flagged])

    def test_apply_lossy_phase1_emits_no_consent_decisions(self):
        # The consent was carried by the flag itself; the applied record
        # needs no open decision (one would block phase 2 forever).
        path = self._make_file("stripped_already.epub")
        shape = {
            "apply_lossy": True,
            "books": [
                {
                    "path": path,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                        "fixes": {"stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, apply_lossy=True, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        self.assertEqual(
            [d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"], []
        )

    def test_watermark_refusals_mirror_as_manual_repair_decisions(self):
        # bindery's manual_watermark_repair books used to vanish from the
        # durable record entirely (the 3.43.0 lossy mirror's sibling gap).
        refused = self._make_file("watermarked_edition.epub")
        outside = "/tmp/not-in-this-batch.epub"
        shape = {
            "apply_lossy": False,
            "books": [{"path": refused, "repair": None}],
            "decisions_needed": [
                {
                    "decision": "manual_watermark_repair",
                    "detail": "1 book(s) carry a watermark the strip refused",
                    "books": [refused, outside],
                }
            ],
        }
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape),
        ):
            rc = run_phase1(self.downloads, self.db_path, quiet=True)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        mirrored = [d for d in man["decisions_needed"] if d["kind"] == "manual_repair"]
        # The in-tree book mirrors; a path bindery names that is not in
        # the manifest cannot.
        self.assertEqual([d["file"] for d in mirrored], [refused])
        self.assertIn("watermark", mirrored[0]["detail"])

    def test_inventory_skips_stamp_backups(self):
        # A rerun used to sweep _stamp_backups into the batch as books.
        backups = os.path.join(self.downloads, "_stamp_backups")
        os.makedirs(backups)
        with open(os.path.join(backups, "original.pdf"), "wb") as f:
            f.write(b"ORIGINAL")
        live = self._make_file("real_book.epub")
        self.assertEqual(_inventory(self.downloads), [live])

    def test_stamp_backups_live_outside_the_tree(self):
        seen = {}
        self._make_file("Ann Leckie - Fifth Head of Data.epub")

        def fake_drive(files, backups):
            seen["backups"] = backups
            return []

        with (
            mock.patch("cquarry_cli.run._drive_stamp", side_effect=fake_drive),
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path, stamp=True)
        self.assertEqual(rc, 0)
        self.assertFalse(seen["backups"].startswith(self.downloads))

    def test_stamp_failure_warns_instead_of_passing_silently(self):
        pdf = self._make_file("Ann Leckie - Fifth Head of Data.pdf", payload=b"PDF")
        proc = mock.Mock(returncode=1, stdout="", stderr="STAMP_FAILED: XMP refuses")
        with mock.patch("cquarry_cli.run._run", return_value=proc):
            err = io.StringIO()
            with redirect_stderr(err):
                stamped = _drive_stamp([pdf], "/tmp/cq-stamp-test")
        self.assertEqual(stamped, [])
        self.assertIn("STAMP_FAILED", err.getvalue())

    def test_apply_lossy_reaches_the_bindery_slice(self):
        # The sweep: --apply-lossy was accepted and never used.
        self._make_file("Ann Leckie - Fifth Head of Data.epub")
        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}) as bindery,
        ):
            rc = run_phase1(self.downloads, self.db_path, apply_lossy=True)
        self.assertEqual(rc, 0)
        self.assertTrue(bindery.call_args.kwargs["apply_lossy"])

    def test_missing_directory_exits_two(self):
        rc = run_phase1(os.path.join(self.temp_dir, "nope"), self.db_path)
        self.assertEqual(rc, 2)

    def test_empty_directory_is_clean_zero(self):
        rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)

    def test_phase1_records_screen_advisories_without_refusing(self):
        # The 2026-09-21 semantics: related containment candidates and
        # multi-volume sets ride out of the screen into the manifest's
        # checks, and never set a verdict. The file stays approved; the
        # review pass judges.
        epub = self._make_file("Luke Gearing - Wages of Sin.epub")
        notes = {
            epub: {
                "related": [{"with": "#3", "title": "Mothership: Wages of Sin"}],
                "volumes": [],
            }
        }

        def fake_screen(files, db, out_notes=None):
            if out_notes is not None:
                out_notes.update(notes)
            return set()

        with (
            mock.patch("cquarry_cli.run._screen_duplicates", side_effect=fake_screen),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={epub: "CLEAN"}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = run_phase1(self.downloads, self.db_path)
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (manifest_path,) = os.listdir(manifests)
        man = manifest.load(os.path.join(manifests, manifest_path))
        entry = manifest.file_by_path(man, epub)
        self.assertEqual(entry["verdict"], "approved_for_import")
        self.assertEqual(
            entry["checks"]["related_works"],
            [{"with": "#3", "title": "Mothership: Wages of Sin"}],
        )
        self.assertNotIn("volume_siblings", entry["checks"])


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

    def test_screen_duplicates_notes_carry_advisories_not_refusals(self):
        # The advisory half of the report: related containment candidates
        # and volume-set siblings reach the notes dict, and a file with
        # ONLY advisories stays out of the refused set.
        hit = self._make_file("Luke Gearing - Wages of Sin.epub")
        report = [
            {
                "file": hit,
                "title": "Wages of Sin",
                "authors": ["Luke Gearing"],
                "isbn": "",
                "library_hits": [],
                "related_hits": [{"id": 3, "title": "Mothership: Wages of Sin"}],
                "batch_volumes": ["/tmp/vault2.epub"],
            },
        ]
        proc = mock.Mock(returncode=1, stdout=json.dumps(report), stderr="")
        notes: dict = {}
        with mock.patch("cquarry_cli.run._run", return_value=proc):
            dups = _screen_duplicates([hit], self.db_path, notes)
        self.assertEqual(dups, set())
        self.assertEqual(
            notes,
            {
                hit: {
                    "related": [{"with": "#3", "title": "Mothership: Wages of Sin"}],
                    "volumes": [{"with": "/tmp/vault2.epub", "title": ""}],
                }
            },
        )

    def test_phase1_hands_the_screen_only_screenable_files(self):
        # The pre-filter is screen_duplicate's own extension set: a djvu
        # inventory never reaches it (its exit-2 "no ebook files" was the
        # djvu-only crash), so the seam runs only when it has work.
        epub = self._make_file("Ann Leckie - Fifth Head of Data.epub")
        djvu = self._make_file("broken_scan.djvu", payload=b"DJVUDATA")
        seen = {}

        def fake_screen(files, db, notes=None):
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
            if "--version" in cmd:
                return mock.Mock(returncode=0, stdout="bindery 0.45.0\n", stderr="")
            report = cmd[cmd.index("--json") + 1]
            if payload is not None:
                with open(report, "w", encoding="utf-8") as f:
                    json.dump(payload, f)
            return mock.Mock(returncode=returncode, stdout="prose", stderr=stderr)

        return side_effect

    @staticmethod
    def _version_proc(line):
        return mock.Mock(returncode=0, stdout=line, stderr="")

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
            if "--version" in cmd:
                return mock.Mock(returncode=0, stdout="bindery 0.45.0\n", stderr="")
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
            mock.patch(
                "cquarry_cli.run._run",
                side_effect=[self._version_proc("bindery 0.45.0"), proc],
            ),
        ):
            self.assertEqual(_bindery_phase1(self.downloads, apply_lossy=False), {})

    def test_bindery_phase1_raises_when_trouble_writes_no_report(self):
        # The pre-run-slices entry point: argparse rejects `run` with its
        # own exit 2 and no report. The seam names it instead of sailing on.
        proc = mock.Mock(returncode=2, stdout="", stderr="invalid choice: 'run'")
        with (
            self._has_bindery(),
            mock.patch(
                "cquarry_cli.run._run",
                side_effect=[self._version_proc("bindery 0.45.0"), proc],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "no readable report"):
                _bindery_phase1(self.downloads, apply_lossy=False)

    def test_bindery_phase1_raises_on_unexpected_exit_codes(self):
        proc = mock.Mock(returncode=3, stdout="", stderr="boom")
        with (
            self._has_bindery(),
            mock.patch(
                "cquarry_cli.run._run",
                side_effect=[self._version_proc("bindery 0.45.0"), proc],
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "bindery run phase1 failed"):
                _bindery_phase1(self.downloads, apply_lossy=False)

    def test_bindery_phase1_refuses_a_bindery_below_the_floor(self):
        # The structured fix records are a versioned contract (bindery-cli
        # 0.45.0). An older PATH bindery would class every strip as
        # structural -- the vacuous-consent hole again -- so too old is a
        # hard error naming the upgrade, never a silent downgrade.
        with (
            self._has_bindery(),
            mock.patch(
                "cquarry_cli.run._run",
                return_value=self._version_proc("bindery 0.44.1\n"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "0.45.0") as ctx:
                _bindery_phase1(self.downloads, apply_lossy=False)
        self.assertIn("upgrade", str(ctx.exception))

    def test_bindery_phase1_refuses_an_unreadable_version(self):
        with (
            self._has_bindery(),
            mock.patch("cquarry_cli.run._run", return_value=self._version_proc("")),
        ):
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                _bindery_phase1(self.downloads, apply_lossy=False)

    def test_bindery_phase1_without_the_entry_point_degrades(self):
        with (
            mock.patch("shutil.which", return_value=None),
            mock.patch("cquarry_cli.run._run") as run_mock,
        ):
            self.assertEqual(_bindery_phase1(self.downloads, apply_lossy=False), {})
        run_mock.assert_not_called()


class TestPrecedentTags(RunCase):
    """The phase-3 prompt's tag-by-precedent read: the four-table JOIN
    was promoted to cquarry 1.22 (CalibreDB.precedent_tags, THE FINAL
    AUDIT L2.6); this pins the read end to end through the wrapper."""

    def test_precedent_tags_read_through_cquarry(self):
        con = sqlite3.connect(self.db_path)
        # The books INSERT fires books_insert_trg, which calls Calibre's
        # title_sort()/uuid4() UDFs; the raw connection needs them too.
        from cquarry.write import register_udfs

        register_udfs(con)
        con.executescript(
            """
            INSERT INTO books (id,title) VALUES (10,'Existing'),
                (11,'New Import');
            INSERT INTO authors (id,name) VALUES (10,'Ann Leckie');
            INSERT INTO books_authors_link (book,author) VALUES (10,10),
                (11,10);
            INSERT INTO tags (id,name) VALUES (1,'Fic.SciFi'),(2,'Redwall');
            INSERT INTO books_tags_link (book,tag) VALUES (10,1),(10,2);
            """
        )
        con.commit()
        con.close()
        # Alphabetical: the promoted read orders for stability (the raw
        # JOIN relied on SQLite's arbitrary DISTINCT order).
        self.assertEqual(
            _precedent_tags(self.db_path, ["Ann Leckie"]),
            ["Fic.SciFi", "Redwall"],
        )
        self.assertEqual(_precedent_tags(self.db_path, []), [])
        self.assertEqual(_precedent_tags(self.db_path, ["Nobody, Alice"]), [])


class TestApplyOpfPubdate(RunCase):
    """The 2026-09-16/21 pubdate poison: the downloaded OPF carried the
    edition date at the download run's clock time (42 of 47 books in one
    batch shared the same minute-band), and phase 3 had to normalize every
    one by hand. The apply seam writes date-only precision now."""

    def _book_and_opf(self, date_text):
        con = sqlite3.connect(self.db_path)
        from cquarry.write import register_udfs

        register_udfs(con)
        book_id = con.execute(
            "INSERT INTO books (title) VALUES ('Some Title')"
        ).lastrowid
        con.commit()
        con.close()
        opf = os.path.join(self.temp_dir, "book.opf")
        with open(opf, "w", encoding="utf-8") as f:
            f.write(
                "<?xml version='1.0' encoding='utf-8'?>\n"
                "<package xmlns='http://www.idpf.org/2007/opf' version='2.0'>"
                "<metadata xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                "<dc:title>Some Title</dc:title>"
                f"<dc:date>{date_text}</dc:date>"
                "</metadata></package>"
            )
        return book_id, opf

    def test_date_only_shapes(self):
        # The run clock is stripped; a bare date survives unchanged; exotic
        # input rides through to fail set_pubdate exactly as before.
        self.assertEqual(_date_only("2010-05-25T01:49:00.233821+00:00"), "2010-05-25")
        self.assertEqual(_date_only("2010-05-25"), "2010-05-25")
        self.assertEqual(_date_only("2010"), "2010")

    def test_apply_writes_the_edition_date_without_the_run_clock(self):
        book_id, opf = self._book_and_opf("2010-05-25T01:49:00.233821+00:00")
        self.assertTrue(_apply_opf(book_id, opf, self.db_path))
        con = sqlite3.connect(self.db_path)
        stored = con.execute(
            "SELECT pubdate FROM books WHERE id = ?", (book_id,)
        ).fetchone()[0]
        con.close()
        # cquarry's canonical midnight form: no 01:49 minute-band anywhere.
        self.assertTrue(stored.startswith("2010-05-25 00:00:00"), stored)

    def test_apply_of_a_date_only_opf_is_unchanged(self):
        book_id, opf = self._book_and_opf("2010-05-25")
        self.assertTrue(_apply_opf(book_id, opf, self.db_path))
        con = sqlite3.connect(self.db_path)
        stored = con.execute(
            "SELECT pubdate FROM books WHERE id = ?", (book_id,)
        ).fetchone()[0]
        con.close()
        self.assertTrue(stored.startswith("2010-05-25 00:00:00"), stored)


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
        # Lock-class refusal (exit 1): the run doors used to exit 2 here
        # while set mode exited 1; unified to the recorded discipline
        # (usage problems exit 2, an open Calibre is not a usage problem).
        self.assertEqual(rc, 1)
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

    def _lossy_manifest(self, *paths, resolved=False):
        """A signed manifest with one lossy-flagged file and its
        lossy_consent decision (resolved in-manifest on request)."""
        man_path = self._manifest(*paths)
        man = manifest.load(man_path)
        for p in paths:
            entry = manifest.file_by_path(man, p)
            entry["lossy"]["flagged"] = True
            entry["lossy"]["repairs"].append(
                {
                    "tool": "bindery",
                    "summary": "stripped_watermarks:1",
                    "applied": False,
                }
            )
            decision = manifest.add_decision(
                man, "lossy_consent", file=p, detail="pending consent"
            )
            if resolved:
                decision["resolution"] = "apply"
        manifest.save(man, man_path)
        return man_path

    _APPLY_SHAPE = {
        "apply_lossy": True,
        "books": [],  # filled per test; the paths differ per fixture
    }

    def _apply_shape(self, *paths):
        return {
            "apply_lossy": True,
            "books": [
                {
                    "path": p,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                    },
                }
                for p in paths
            ],
        }

    def test_unresolved_lossy_consent_blocks_the_import(self):
        path = self._make_file("Security in Computing.epub")
        man_path = self._lossy_manifest(path)
        rc, _ = self._import(man_path)
        self.assertEqual(rc, 2)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)

    def test_resolved_lossy_consent_applies_then_imports(self):
        # The double-manifest wrinkle's fix: resolution "apply" in the
        # manifest replaces the --apply-lossy phase-1 re-run; phase 2
        # drives the strips itself, flips the lossy records to applied,
        # consumes the decisions, and imports the repaired files.
        path = self._make_file("Security in Computing.epub")
        man_path = self._lossy_manifest(path, resolved=True)
        with mock.patch(
            "cquarry_cli.run._bindery_phase1",
            return_value=self._apply_shape(path),
        ) as bindery:
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 0)
        self.assertTrue(bindery.call_args.kwargs["apply_lossy"])
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 1)
        man = manifest.load(man_path)
        self.assertEqual(
            [d for d in man["decisions_needed"] if d["kind"] == "lossy_consent"], []
        )
        self.assertTrue(
            manifest.file_by_path(man, path)["lossy"]["repairs"][0]["applied"]
        )

    def test_partial_lossy_consent_refused_before_anything_runs(self):
        # Consent is all-or-nothing: bindery's re-drive applies every
        # gate-accepted repair in the tree, so resolving one of two
        # flagged files would silently outpace the consent.
        first = self._make_file("Security in Computing.epub")
        second = self._make_file("watermarked_edition.epub")
        man_path = self._lossy_manifest(first, second)
        man = manifest.load(man_path)
        next(
            d
            for d in man["decisions_needed"]
            if d["kind"] == "lossy_consent" and d["file"] == first
        )["resolution"] = "apply"
        manifest.save(man, man_path)
        with mock.patch("cquarry_cli.run._bindery_phase1") as bindery:
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 2)
        bindery.assert_not_called()
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)

    def test_failed_consent_apply_leaves_the_library_unwritten(self):
        # The re-drive's report is the proof: a book bindery could not
        # strip fails the verb BEFORE the import batch opens.
        path = self._make_file("Security in Computing.epub")
        man_path = self._lossy_manifest(path, resolved=True)
        shape = {
            "apply_lossy": True,
            "books": [{"path": path, "repair": None}],
        }
        with mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape):
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 1)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)

    def test_consent_re_drive_flips_structural_records_too(self):
        # The narrowed consent gates only lossy-flagged files, but
        # bindery's re-drive applies EVERY recorded repair; walking only
        # the flagged files left a structural record at applied=false,
        # lying about the file on disk.
        lossy_path = self._make_file("watermarked_edition.epub")
        # A distinct payload: cquarry refuses a byte-identical re-import,
        # and both fixture files are approved in one manifest.
        structural_path = self._make_file(
            "confusable_classic.epub", payload=b"EPUBDATA-STRUCTURAL"
        )
        man_path = self._manifest(lossy_path, structural_path)
        man = manifest.load(man_path)
        lossy_entry = manifest.file_by_path(man, lossy_path)
        lossy_entry["lossy"]["flagged"] = True
        lossy_entry["lossy"]["repairs"].append(
            {
                "tool": "bindery",
                "summary": "stripped_watermarks:1",
                "applied": False,
                "lossy": True,
            }
        )
        decision = manifest.add_decision(
            man, "lossy_consent", file=lossy_path, detail="pending consent"
        )
        decision["resolution"] = "apply"
        entry = manifest.file_by_path(man, structural_path)
        entry["lossy"]["repairs"].append(
            {
                "tool": "bindery",
                "summary": "add-img-alt:4, ncx_uid_synced",
                "applied": False,
                "lossy": False,
            }
        )
        manifest.save(man, man_path)
        shape = {
            "apply_lossy": True,
            "books": [
                {
                    "path": lossy_path,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                        "fixes": {"stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                },
                {
                    "path": structural_path,
                    "repair": {
                        "status": "accept",
                        "summary": "add-img-alt:4, ncx_uid_synced",
                        "fixes": {"add-img-alt": 4},
                        "ncx_uid_synced": True,
                        "watermark_refusals": 0,
                    },
                },
            ],
        }
        with mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape):
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 2)
        man = manifest.load(man_path)
        self.assertTrue(
            manifest.file_by_path(man, structural_path)["lossy"]["repairs"][0][
                "applied"
            ]
        )
        self.assertTrue(
            manifest.file_by_path(man, lossy_path)["lossy"]["repairs"][0]["applied"]
        )

    def test_consent_re_drive_refuses_when_a_structural_repair_fails(self):
        # The refuse pass walks every recorded repair, not just the
        # consented lossy ones: a file the re-drive can no longer repair
        # fails the verb before the import opens.
        lossy_path = self._make_file("watermarked_edition.epub")
        structural_path = self._make_file("confusable_classic.epub")
        man_path = self._manifest(lossy_path, structural_path)
        man = manifest.load(man_path)
        lossy_entry = manifest.file_by_path(man, lossy_path)
        lossy_entry["lossy"]["flagged"] = True
        lossy_entry["lossy"]["repairs"].append(
            {
                "tool": "bindery",
                "summary": "stripped_watermarks:1",
                "applied": False,
                "lossy": True,
            }
        )
        decision = manifest.add_decision(
            man, "lossy_consent", file=lossy_path, detail="pending consent"
        )
        decision["resolution"] = "apply"
        entry = manifest.file_by_path(man, structural_path)
        entry["lossy"]["repairs"].append(
            {
                "tool": "bindery",
                "summary": "add-img-alt:4",
                "applied": False,
                "lossy": False,
            }
        )
        manifest.save(man, man_path)
        shape = {
            "apply_lossy": True,
            "books": [
                {
                    "path": lossy_path,
                    "repair": {
                        "status": "accept",
                        "summary": "stripped_watermarks:1",
                        "fixes": {"stripped_watermarks": 1},
                        "ncx_uid_synced": False,
                        "watermark_refusals": 0,
                    },
                }
            ],
        }
        with mock.patch("cquarry_cli.run._bindery_phase1", return_value=shape):
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 1)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)

    def test_silent_source_stamp_failure_rolls_the_batch_back(self):
        # The 2026-09-18/19 cc6 observations: a #source stamp that writes
        # no state must fail the batch, never land an import whose
        # provenance did not reach the column. set_custom_column's
        # changed flag is the in-batch signal (a fresh book's first
        # #source write always changes state); False raises inside the
        # one batch() and the whole import rolls back.
        path = self._make_file("Fifth Head.epub")
        man_path = self._manifest(path)
        with mock.patch(
            "cquarry.write.WritableCalibreDB.set_custom_column",
            return_value=False,
        ):
            rc, _ = self._import(man_path)
        self.assertEqual(rc, 1)
        con = sqlite3.connect(self.db_path)
        count = con.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        con.close()
        self.assertEqual(count, 0)
        man = manifest.load(man_path)
        self.assertIsNone(man["files"][0]["import"]["imported_id"])

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
        self.assertTrue(os.path.exists(backup))  # the pre-run restore point
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

    def test_crash_in_downloads_still_leaves_the_resume_record(self):
        # The resume record used to be saved only after the unguarded
        # download segment, so a crash there cost the imported ids.
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        backup = os.path.join(self.temp_dir, "backups")
        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=False),
            mock.patch(
                "cquarry_cli.run._fetch_metadata",
                side_effect=RuntimeError("network exploded"),
            ),
        ):
            with self.assertRaises(RuntimeError):
                run_phase2(man_path, self.db_path, backup_dir=backup)
        man = manifest.load(man_path)
        self.assertEqual(man["files"][0]["import"]["imported_id"], 1)

    def test_no_audience_flag_means_the_documented_default(self):
        # Through the real dispatch: the argparse None used to be stamped
        # into #audience as the literal string 'None'.
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        backup = os.path.join(self.temp_dir, "backups")
        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=False),
            mock.patch("cquarry_cli.run._fetch_metadata", return_value="no_result"),
        ):
            rc = main(
                [
                    "--db",
                    self.db_path,
                    "run",
                    "phase2",
                    "--manifest",
                    man_path,
                    "--backup-dir",
                    backup,
                ]
            )
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        try:
            none_rows = con.execute(
                "SELECT COUNT(*) FROM books_custom_column_11_link l "
                "JOIN custom_column_11 c ON c.id = l.value WHERE c.value = 'None'"
            ).fetchone()[0]
            brandon = con.execute(
                "SELECT COUNT(*) FROM books_custom_column_11_link l "
                "JOIN custom_column_11 c ON c.id = l.value WHERE c.value = 'Brandon'"
            ).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(none_rows, 0)
        self.assertEqual(brandon, 1)

    def test_downloaded_opf_applies_without_calibredb(self):
        # The repo constraint is no calibredb: the ok path now applies the
        # OPF through cquarry's write module in one batch.
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        backup = os.path.join(self.temp_dir, "backups")

        def fake_fetch(isbn, opf_path):
            with open(opf_path, "w", encoding="utf-8") as f:
                f.write(
                    "<?xml version='1.0'?><package "
                    "xmlns='http://www.idpf.org/2007/opf' "
                    "xmlns:dc='http://purl.org/dc/elements/1.1/' "
                    "xmlns:opf='http://www.idpf.org/2007/opf'>"
                    "<metadata><dc:title>Real Title</dc:title>"
                    "<dc:creator>Ann Leckie</dc:creator>"
                    "<dc:publisher>Orbit</dc:publisher>"
                    "<dc:date>2019-01-01</dc:date>"
                    "<dc:identifier opf:scheme='ISBN'>9780000000000</dc:identifier>"
                    "<dc:description>Real description.</dc:description>"
                    "</metadata></package>"
                )
            return "ok"

        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=False),
            mock.patch("cquarry_cli.run._fetch_metadata", side_effect=fake_fetch),
        ):
            rc = run_phase2(man_path, self.db_path, backup_dir=backup)
        self.assertEqual(rc, 0)
        con = sqlite3.connect(self.db_path)
        title = con.execute("SELECT title FROM books").fetchone()[0]
        publisher = con.execute(
            "SELECT p.name FROM books_publishers_link pl "
            "JOIN publishers p ON p.id = pl.publisher"
        ).fetchone()[0]
        comments = con.execute("SELECT text FROM comments").fetchone()[0]
        ident = con.execute("SELECT type, val FROM identifiers").fetchall()
        con.close()
        self.assertEqual(title, "Real Title")
        self.assertEqual(publisher, "Orbit")
        self.assertEqual(comments, "Real description.")
        self.assertIn(("isbn", "9780000000000"), ident)
        man = manifest.load(man_path)
        self.assertEqual(man["files"][0]["import"]["download_outcome"], "ok")

    def test_downloads_defer_when_calibre_opens_after_the_commit(self):
        # The guard window closed at commit: a Calibre that opens before
        # the download segment is never raced with calibredb.
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        backup = os.path.join(self.temp_dir, "backups")
        with (
            mock.patch("cquarry_cli.run.calibre_running", side_effect=[False, True]),
            mock.patch("cquarry_cli.run._fetch_metadata") as fetch,
        ):
            rc = run_phase2(man_path, self.db_path, backup_dir=backup)
        self.assertEqual(rc, 0)
        fetch.assert_not_called()
        man = manifest.load(man_path)
        self.assertEqual(
            man["files"][0]["import"]["download_outcome"], "deferred_to_phase3"
        )

    def test_backup_survives_a_second_run(self):
        bdir = os.path.join(self.temp_dir, "backups")
        first = make_backup(self.db_path, bdir)
        second = make_backup(self.db_path, bdir)
        self.assertNotEqual(first, second)
        self.assertTrue(os.path.exists(first))  # the first restore point survives
        con = sqlite3.connect(first)
        try:
            con.execute("SELECT COUNT(*) FROM books").fetchone()
        finally:
            con.close()

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

    def test_phase2_ok_path_is_live_end_to_end(self):
        # The 3.41.0 fetch fix's end-to-end half: only the subprocess
        # seam is mocked, so the staging, the ok branch, _apply_opf, and
        # the clobber watch all run for real (they were dead code while
        # _fetch_metadata passed a stray positional after -o).
        path = self._make_file("Fifth Head of Data.epub")
        man_path = self._manifest(path)
        backup = os.path.join(self.temp_dir, "backups")

        def fake_fetch(cmd, **kw):
            opf = (
                "<?xml version='1.0'?><package "
                "xmlns='http://www.idpf.org/2007/opf' "
                "xmlns:dc='http://purl.org/dc/elements/1.1/'>"
                "<metadata><dc:title>Real Title</dc:title>"
                "<dc:creator>Someone Else</dc:creator>"
                "</metadata></package>"
            )
            return mock.Mock(returncode=0, stdout=opf, stderr="")

        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=False),
            mock.patch("cquarry_cli.run.subprocess.run", side_effect=fake_fetch),
        ):
            rc = run_phase2(man_path, self.db_path, backup_dir=backup)
        self.assertEqual(rc, 0)
        man = manifest.load(man_path)
        entry = man["files"][0]
        self.assertEqual(entry["import"]["download_outcome"], "ok")
        # The ok branch applied the OPF through cquarry writes (no
        # metadata_download decision queued), and the author clobber
        # watch recorded the stamped-vs-downloaded drift.
        kinds = [d["kind"] for d in man["decisions_needed"]]
        self.assertEqual(kinds, [])
        con = sqlite3.connect(self.db_path)
        try:
            title = con.execute("SELECT title FROM books").fetchone()[0]
            author = con.execute(
                "SELECT a.name FROM books_authors_link l "
                "JOIN authors a ON a.id = l.author"
            ).fetchall()
        finally:
            con.close()
        self.assertEqual(title, "Real Title")
        self.assertEqual(author, [("Someone Else",)])
        self.assertEqual(
            entry["import"]["clobber_watch"]["post_download"], ["Someone Else"]
        )


class TestFetchMetadataSeam(unittest.TestCase):
    """3.41.0 regression: the phase-2 fetch stages STDOUT. fetch-ebook-
    metadata's -o/--opf is a store flag (the OPF arrives on stdout), so
    the old cut's `-o <path>` made the path a silently-ignored stray
    positional: the success gate always failed, every import queued a
    bogus metadata_download decision, and the ok branch, _apply_opf,
    and the clobber watch were dead code. These tests mock the
    subprocess seam, not _fetch_metadata, so the real staging runs."""

    _FETCH_OPF = (
        "<?xml version='1.0'?><package "
        "xmlns='http://www.idpf.org/2007/opf' "
        "xmlns:dc='http://purl.org/dc/elements/1.1/' "
        "xmlns:opf='http://www.idpf.org/2007/opf'>"
        "<metadata><dc:title>Real Title</dc:title>"
        "<dc:creator>Ann Leckie</dc:creator>"
        "<dc:publisher>Orbit</dc:publisher>"
        "<dc:identifier opf:scheme='ISBN'>9780000000000</dc:identifier>"
        "</metadata></package>"
    )

    def test_the_command_has_no_stray_positional_and_stages_stdout(self):
        opf_path = os.path.join(tempfile.mkdtemp(prefix="cquarry_fetch_"), "staged.opf")
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=0, stdout=self._FETCH_OPF, stderr="")

        with mock.patch("cquarry_cli.run.subprocess.run", side_effect=fake_run):
            outcome = _fetch_metadata("9780000000000", opf_path)
        self.assertEqual(outcome, "ok")
        # Exactly the flag and nothing after it: the old cut appended the
        # path here, and the binary ignored it.
        self.assertEqual(calls[0][-1], "-o")
        self.assertEqual(len(calls[0]), 4, calls[0])
        with open(opf_path, encoding="utf-8") as f:
            self.assertEqual(f.read(), self._FETCH_OPF)

    def test_no_result_output_is_not_ambiguous(self):
        # The no-result log says "No matches found with query", so the
        # old bare-word "matches" sniff classified every empty lookup as
        # ambiguous; only "multiple" carries the ambiguity signal.
        with mock.patch(
            "cquarry_cli.run.subprocess.run",
            return_value=mock.Mock(
                returncode=1, stdout="", stderr="No matches found with query"
            ),
        ):
            outcome = _fetch_metadata("9780000000000", "unused.opf")
        self.assertEqual(outcome, "no_result")

    def test_multiple_matches_stays_ambiguous(self):
        with mock.patch(
            "cquarry_cli.run.subprocess.run",
            return_value=mock.Mock(
                returncode=1, stdout="", stderr="Multiple matches for query"
            ),
        ):
            outcome = _fetch_metadata("9780000000000", "unused.opf")
        self.assertEqual(outcome, "ambiguous")

    def test_a_hung_lookup_is_failed(self):
        import subprocess as sp

        with mock.patch(
            "cquarry_cli.run.subprocess.run",
            side_effect=sp.TimeoutExpired(cmd="fetch", timeout=120),
        ):
            outcome = _fetch_metadata("9780000000000", "unused.opf")
        self.assertEqual(outcome, "failed")


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
    def setUp(self):
        super().setUp()
        # The whole class runs closed-Calibre: only the refusal test
        # exercises the guard itself (it overrides this patch). Without
        # it, a desktop Calibre that happens to be open flips every
        # outcome to the guard's exit 2.
        pg = mock.patch("cquarry_cli.run.calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

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

    def test_phase3_refuses_when_calibre_is_running(self):
        # The same rail as phase 2 and set mode; the sweep found phase 3
        # opening a writable handle with no closed-Calibre check at all.
        self._rich_library()
        man_path = self._ready_manifest([10])
        answers = self._answers()
        with (
            mock.patch("cquarry_cli.run.calibre_running", return_value=True),
            mock.patch("cquarry_cli.run._run") as run_mock,
        ):
            rc = run_phase3(man_path, self.db_path, answer_file=answers)
        # Lock-class refusal (exit 1), unified with phase 2/setwrite.
        self.assertEqual(rc, 1)
        run_mock.assert_not_called()  # nothing mechanical, nothing written
        con = sqlite3.connect(self.db_path)
        untagged = con.execute(
            "SELECT COUNT(*) FROM books_tags_link WHERE book = 10"
        ).fetchone()[0]
        con.close()
        self.assertEqual(untagged, 0)

    def test_answer_file_may_not_name_banned_columns(self):
        # The fixes fallback set_custom_column would have taken
        # #reading_status from the answer file: the NON-NEGOTIABLES column.
        self._rich_library()
        man_path = self._ready_manifest([10])
        answers = os.path.join(self.temp_dir, "banned.json")
        with open(answers, "w") as f:
            json.dump({"10": {"fixes": {"#reading_status": "read"}}}, f)
        with mock.patch("cquarry_cli.run.calibre_running", return_value=False):
            rc = run_phase3(man_path, self.db_path, answer_file=answers)
        self.assertEqual(rc, 2)
        con = sqlite3.connect(self.db_path)
        kept = con.execute(
            "SELECT COUNT(*) FROM books_tags_link WHERE book = 10"
        ).fetchone()[0]
        con.close()
        self.assertEqual(kept, 0)  # refused before anything opened writable

    def test_mechanical_trouble_fails_the_verb(self):
        # bindery's rc 2 (trouble found) used to be a warning suppressed
        # under --quiet while a clean validator still exited 0.
        self._rich_library()
        man_path = self._ready_manifest([10])
        answers = self._answers()
        procs = [
            mock.Mock(returncode=2, stderr="trouble found", stdout=""),
            mock.Mock(returncode=0, stderr="", stdout=""),
            mock.Mock(returncode=0, stderr="", stdout=""),
        ]
        with mock.patch("cquarry_cli.run._run", side_effect=procs):
            rc = run_phase3(man_path, self.db_path, answer_file=answers)
        self.assertEqual(rc, 1)
        records = sorted(
            n
            for n in os.listdir(os.path.join(self.library, ".claude"))
            if n.startswith("project_import_")
        )
        with open(os.path.join(self.library, ".claude", records[-1])) as f:
            record = f.read()
        self.assertIn("bindery/reconcile trouble: yes", record)

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


class TestEmbeddedStamps(unittest.TestCase):
    """The ebook-meta seam's parse (the 2026-09-18 seeding box): embedded
    metadata seeds the manifest, the filename parse is the fallback, and
    a wrong-format pubdate must never reach phase 2."""

    def _proc(self, stdout, returncode=0):
        return mock.Mock(returncode=returncode, stdout=stdout, stderr="")

    def test_labels_parse_into_stamps_and_identifiers_stay_out(self):
        # The real calibre 9.14 display form: padded labels, " : "
        # separator, authors joined with " & ". Identifiers are read but
        # never seeded (misidentification-prone).
        stdout = (
            "Title               : Probe Title\n"
            "Author(s)           : Ada Author & Bob B. Author\n"
            "Publisher           : Probe Press\n"
            "Languages           : eng\n"
            "Published           : 2001-05-01T04:00:00+00:00\n"
            "Identifiers         : isbn:9780123456789, goodreads:1234\n"
        )
        with mock.patch("cquarry_cli.run._run", return_value=self._proc(stdout)):
            stamps = _stamps_from_embedded("/tmp/probe.epub")
        self.assertEqual(
            stamps,
            {
                "title": "Probe Title",
                "authors": ["Ada Author", "Bob B. Author"],
                "publisher": "Probe Press",
                "language": "eng",
                "pubdate": "2001-05-01T04:00:00+00:00",
            },
        )
        self.assertNotIn("isbn", stamps)

    def test_year_only_pubdate_is_dropped(self):
        # cquarry's add_book raises on an unparseable pubdate: a bare
        # year (common in PDF metadata) must not seed one.
        stdout = "Title               : T\nPublished           : 2001\n"
        with mock.patch("cquarry_cli.run._run", return_value=self._proc(stdout)):
            stamps = _stamps_from_embedded("/tmp/probe.pdf")
        self.assertEqual(stamps, {"title": "T"})

    def test_a_failed_read_returns_none(self):
        with mock.patch(
            "cquarry_cli.run._run", return_value=self._proc("", returncode=1)
        ):
            self.assertIsNone(_stamps_from_embedded("/tmp/probe.epub"))

    def test_a_timeout_returns_none(self):
        with mock.patch(
            "cquarry_cli.run._run",
            side_effect=subprocess.TimeoutExpired(cmd="ebook-meta", timeout=120),
        ):
            self.assertIsNone(_stamps_from_embedded("/tmp/probe.epub"))

    def test_an_empty_read_is_a_dict_not_a_failure(self):
        # returncode 0 with nothing usable: the caller keeps the filename
        # seeds and records no failure.
        with mock.patch("cquarry_cli.run._run", return_value=self._proc("")):
            self.assertEqual(_stamps_from_embedded("/tmp/probe.epub"), {})


if __name__ == "__main__":
    unittest.main()
