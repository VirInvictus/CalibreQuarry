"""Tests to ensure version numbers are synchronized across the repository."""

import re
import unittest
from pathlib import Path

from cquarry_cli import VERSION as CODE_VERSION


class TestVersionSync(unittest.TestCase):
    def test_versions_match(self):
        """Ensure pyproject.toml, VERSION file, and code version all match."""
        root_dir = Path(__file__).parent.parent

        # 1. Read pyproject.toml version
        pyproject_path = root_dir / "pyproject.toml"
        pyproject_version = None
        if pyproject_path.exists():
            with open(pyproject_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("version = "):
                        pyproject_version = line.split("=")[1].strip().strip('"')
                        break

        # 2. Read VERSION file version
        version_file_path = root_dir / "VERSION"
        file_version = None
        if version_file_path.exists():
            with open(version_file_path, encoding="utf-8") as f:
                file_version = f.read().strip()

        # Assertions
        self.assertIsNotNone(
            pyproject_version, "Could not find version in pyproject.toml"
        )
        self.assertIsNotNone(file_version, "Could not find version in VERSION file")

        self.assertEqual(
            pyproject_version,
            CODE_VERSION,
            f"pyproject.toml ({pyproject_version}) does not match code VERSION ({CODE_VERSION})",
        )

        self.assertEqual(
            file_version,
            CODE_VERSION,
            f"VERSION file ({file_version}) does not match code VERSION ({CODE_VERSION})",
        )

    def test_patchnotes_top_entry_matches(self):
        """The newest patchnotes heading must be the released version.

        The repo has shipped with the patchnotes entry trailing the code
        (a 'Patchnotes: 3.20.0' commit once landed with VERSION still at
        3.19.0); the newest heading is the cheapest drift signal.
        """
        root_dir = Path(__file__).parent.parent
        patchnotes_path = root_dir / "patchnotes.md"
        self.assertTrue(patchnotes_path.exists(), "patchnotes.md is missing")

        notes_version = None
        with open(patchnotes_path, encoding="utf-8") as f:
            for line in f:
                m = re.match(r"^#{1,6}\s+v?(\d+\.\d+\.\d+)\b", line.strip())
                if m:
                    notes_version = m.group(1)
                    break

        self.assertIsNotNone(notes_version, "No version heading found in patchnotes.md")
        self.assertEqual(
            notes_version,
            CODE_VERSION,
            f"patchnotes.md's newest entry ({notes_version}) does not match "
            f"code VERSION ({CODE_VERSION}); add the release entry in the same "
            "commit as the bump",
        )

    def test_spec_header_matches(self):
        """The spec's Version line is a carrier: it drifted six releases
        behind before this guard existed (live proof: the roadmap stamp
        said 'as of v3.26.0' against 3.42.0 everywhere else)."""
        root_dir = Path(__file__).parent.parent
        spec_path = root_dir / "spec.md"
        self.assertTrue(spec_path.exists(), "spec.md is missing")
        text = spec_path.read_text(encoding="utf-8")
        m = re.search(r"\*\*Version:\*\*\s*v?(\d+\.\d+\.\d+)", text)
        self.assertIsNotNone(m, "spec.md has no **Version:** header line")
        self.assertEqual(
            m.group(1),
            CODE_VERSION,
            f"spec.md's Version header ({m.group(1)}) does not match code "
            f"VERSION ({CODE_VERSION})",
        )

    def test_spec_dependency_floor_matches_pyproject(self):
        """The spec's Dependencies line names the cquarry floor; the
        floor rotted three times (1.7, 1.14, 1.21) before this guard."""
        root_dir = Path(__file__).parent.parent
        spec_text = (root_dir / "spec.md").read_text(encoding="utf-8")
        pyproject_text = (root_dir / "pyproject.toml").read_text(encoding="utf-8")
        pyproject_floor = re.search(r'"cquarry>=([\d.]+)"', pyproject_text)
        self.assertIsNotNone(pyproject_floor, "pyproject.toml has no cquarry floor")
        m = re.search(r"`cquarry` \(>=\s*([\d.]+)\)", spec_text)
        self.assertIsNotNone(m, "spec.md's Dependencies line names no cquarry floor")
        self.assertEqual(
            m.group(1),
            pyproject_floor.group(1),
            "spec.md's cquarry floor does not match pyproject.toml's",
        )

    def test_roadmap_header_matches(self):
        """The roadmap's 'Updated as of' stamp is a carrier too: it sat at
        v3.26.0 for six releases."""
        root_dir = Path(__file__).parent.parent
        roadmap_path = root_dir / "roadmap.md"
        self.assertTrue(roadmap_path.exists(), "roadmap.md is missing")
        text = roadmap_path.read_text(encoding="utf-8")
        m = re.search(r"Updated as of v(\d+\.\d+\.\d+)", text)
        self.assertIsNotNone(m, "roadmap.md's header names no 'Updated as of' stamp")
        self.assertEqual(
            m.group(1),
            CODE_VERSION,
            f"roadmap.md's stamp (v{m.group(1)}) does not match code VERSION "
            f"({CODE_VERSION}); bump it with the release",
        )
