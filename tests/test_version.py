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
