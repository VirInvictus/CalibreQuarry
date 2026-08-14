"""Tests to ensure version numbers are synchronized across the repository."""

import unittest
from pathlib import Path

from cquarry.config import VERSION as CODE_VERSION


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
