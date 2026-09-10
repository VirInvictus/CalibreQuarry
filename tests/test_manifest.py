"""Tests for the acquisition manifest (acquisition-manifest/1): the
Phase 17 box-1 schema module the run verbs will share.

The manifest is the machine-readable hand-off between the phases, with
the 2026-09-06 decisions baked in: provenance stamps #source, audience is
unconditional, a signed report is standing consent for listed lossy
repairs, refused duplicates and metadata-download failures land in
decisions_needed, and manifests are retained as the durable record.
"""

import json
import os
import tempfile
import unittest

from cquarry_cli import manifest


class TestManifestSchema(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.manifest = manifest.new_manifest(os.path.join(self.temp_dir, "dl"))
        self.entry = manifest.new_file_entry("book.epub")
        self.entry.update(size=1234, provenance="Standard Ebooks")
        self.entry["verdict"] = "approved_for_import"
        manifest.add_file(self.manifest, self.entry)
        manifest.approve(self.manifest, ["book.epub"])

    def tearDown(self):
        for name in ("m.json", "m.json.cquarry-tmp"):
            path = os.path.join(self.temp_dir, name)
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(self.temp_dir)

    def test_new_manifest_is_valid_and_typed(self):
        self.assertEqual(manifest.validate(self.manifest), [])
        self.assertEqual(self.manifest["schema"], "acquisition-manifest/1")
        self.assertFalse(self.manifest["signed"])

    def test_wrong_schema_name_rejected(self):
        self.manifest["schema"] = "acquisition-manifest/2"
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("schema" in p for p in problems))

    def test_unknown_verdict_and_decision_kind_rejected(self):
        self.manifest["files"][0]["verdict"] = "sure_why_not"
        manifest.add_decision(self.manifest, "duplicate", file="book.epub")
        with self.assertRaises(ValueError):
            manifest.add_decision(self.manifest, "vibes", file="book.epub")
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("verdict" in p for p in problems))

    def test_approval_requires_listed_file(self):
        with self.assertRaises(ValueError):
            manifest.approve(self.manifest, ["ghost.epub"])
        self.manifest["approved_for_import"].append("ghost.epub")
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("ghost.epub" in p for p in problems))

    def test_missing_required_file_keys_rejected(self):
        self.manifest["files"][0].pop("provenance")
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("provenance" in p for p in problems))

    def test_save_refuses_invalid_manifest(self):
        self.manifest["schema"] = "nope"
        path = os.path.join(self.temp_dir, "m.json")
        with self.assertRaises(ValueError):
            manifest.save(self.manifest, path)
        self.assertFalse(os.path.exists(path))

    def test_save_load_roundtrip_validates(self):
        manifest.sign(self.manifest)
        manifest.add_decision(
            self.manifest, "duplicate", file="other.epub", existing_id=4271
        )
        path = os.path.join(self.temp_dir, "m.json")
        manifest.save(self.manifest, path)
        self.assertFalse(os.path.exists(path + ".cquarry-tmp"))
        loaded = manifest.load(path)
        self.assertTrue(loaded["signed"])
        self.assertEqual(
            loaded["decisions_needed"],
            [{"kind": "duplicate", "file": "other.epub", "existing_id": 4271}],
        )
        self.assertEqual(loaded["approved_for_import"], ["book.epub"])

    def test_load_rejects_tampered_manifest(self):
        path = os.path.join(self.temp_dir, "m.json")
        manifest.save(self.manifest, path)
        data = json.load(open(path))
        data["files"][0]["verdict"] = "invented"
        json.dump(data, open(path, "w"))
        with self.assertRaises(ValueError):
            manifest.load(path)

    def test_sign_is_the_standing_lossy_consent_marker(self):
        self.manifest["files"][0]["lossy"] = {
            "flagged": True,
            "repairs": ["--strip-pagination"],
        }
        self.assertFalse(self.manifest["signed"])
        manifest.sign(self.manifest)
        self.assertTrue(self.manifest["signed"])
        self.assertIsNotNone(self.manifest["signed_at"])

    def test_import_block_defaults_match_the_decisions(self):
        block = self.entry["import"]
        self.assertIsNone(block["imported_id"])
        self.assertIn(block["download_outcome"], (None, *manifest.DOWNLOAD_OUTCOMES))
        self.assertEqual(block["clears"], {"tags_removed": 0, "rating_cleared": False})
        # No per-file audience column exists: the decision was unconditional.
        self.assertNotIn("audience", self.entry)

    def test_manifests_dir_is_library_local(self):
        # Any library path joins the same way; the real library's path is
        # none of a unit test's business.
        lib = os.path.join(self.temp_dir, "Some Library")
        self.assertEqual(
            manifest.manifests_dir(lib), os.path.join(lib, ".claude", "manifests")
        )


class TestManifestSeal(unittest.TestCase):
    """The signature is a real seal (the sweep's P0: `sign()` used to set a
    bare boolean in the same editable file, so a manifest whose rejected
    file was listed as approved passed phase 2, proven end to end)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.manifest = manifest.new_manifest(os.path.join(self.temp_dir, "dl"))
        self.entry = manifest.new_file_entry("book.epub")
        self.entry.update(
            size=1234,
            provenance="Standard Ebooks",
            stamps={"title": "Book", "authors": ["A. Author"]},
        )
        self.entry["verdict"] = "approved_for_import"
        manifest.add_file(self.manifest, self.entry)
        self.rejected = manifest.new_file_entry("sketchy.epub")
        self.rejected["verdict"] = "rejected"
        manifest.add_file(self.manifest, self.rejected)
        manifest.approve(self.manifest, ["book.epub"])
        self.path = os.path.join(self.temp_dir, "m.json")

    def tearDown(self):
        for name in ("m.json", "m.json.cquarry-tmp"):
            path = os.path.join(self.temp_dir, name)
            if os.path.exists(path):
                os.remove(path)
        os.rmdir(self.temp_dir)

    def _roundtrip(self):
        manifest.save(self.manifest, self.path)
        return manifest.load(self.path)

    def test_signing_seals_the_manifest(self):
        manifest.sign(self.manifest)
        loaded = self._roundtrip()  # save() re-seals the signed manifest
        self.assertTrue(loaded["signed"])
        self.assertTrue(loaded["signature"])
        self.assertTrue(manifest.verify_seal(loaded))
        self.assertEqual(manifest.validate(loaded), [])

    def test_unsigned_manifest_needs_no_seal(self):
        self.assertFalse(self.manifest["signed"])
        loaded = self._roundtrip()
        self.assertIsNone(loaded["signature"])
        self.assertEqual(manifest.validate(loaded), [])

    def test_editing_stamps_after_signing_fails_load(self):
        manifest.sign(self.manifest)
        manifest.save(self.manifest, self.path)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        data["files"][0]["stamps"]["title"] = "Tampered"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaisesRegex(ValueError, "seal mismatch"):
            manifest.load(self.path)

    def test_listing_a_rejected_file_as_approved_fails_validate(self):
        # The sweep's proven attack, now caught twice: the verdict
        # cross-check fires even if the seal is recomputed, and the seal
        # alone fires when it is not.
        self.manifest["approved_for_import"].append("sketchy.epub")
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("sketchy.epub" in p and "verdict" in p for p in problems))
        self.manifest["signed"] = True
        self.manifest["signature"] = "0" * 64
        problems = manifest.validate(self.manifest)
        self.assertTrue(any("verdict" in p for p in problems))

    def test_removing_a_blocking_decision_after_signing_fails_load(self):
        manifest.add_decision(
            self.manifest, "manual_repair", file="book.epub", detail="DRM: ADEPT"
        )
        manifest.sign(self.manifest)
        manifest.save(self.manifest, self.path)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        data["decisions_needed"] = []
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaisesRegex(ValueError, "seal mismatch"):
            manifest.load(self.path)

    def test_resign_after_a_deliberate_edit(self):
        # The re-sign path: structure checks hold, the stale seal does not
        # block, and re-signing approves the new content.
        manifest.sign(self.manifest)
        manifest.save(self.manifest, self.path)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        data["files"][0]["stamps"]["title"] = "Corrected"
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        with self.assertRaises(ValueError):
            manifest.load(self.path)
        self.assertEqual([p for p in manifest.validate(data, check_seal=False)], [])
        manifest.sign(data)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        loaded = manifest.load(self.path)
        self.assertEqual(loaded["files"][0]["stamps"]["title"], "Corrected")


if __name__ == "__main__":
    unittest.main()
