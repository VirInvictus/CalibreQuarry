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
        self.assertEqual(
            manifest.manifests_dir("/home/bdkl/docs/Calibre Library"),
            os.path.join("/home/bdkl/docs/Calibre Library", ".claude", "manifests"),
        )


if __name__ == "__main__":
    unittest.main()
