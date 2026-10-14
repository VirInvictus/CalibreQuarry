"""Tests for scripts/version_guard.py: the toolchain announcement and the
floor-refusal contract (issue #20). Pure logic; the announce test reads
whatever the running environment really has, since the suite's other
script tests already import cquarry and vir_tui directly."""

import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

_spec = importlib.util.spec_from_file_location(
    "version_guard",
    Path(__file__).resolve().parent.parent / "scripts" / "version_guard.py",
)
assert _spec and _spec.loader
vg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vg)


class TestVersionTuple(unittest.TestCase):
    def test_parses_release_triplets(self):
        self.assertEqual(vg.version_tuple("3.63.4"), (3, 63, 4))
        self.assertEqual(vg.version_tuple("3.64.0"), (3, 64, 0))

    def test_unparseable_and_empty_are_none(self):
        # None means "cannot prove anything", which the floor check treats
        # as below-floor (fail-closed).
        self.assertIsNone(vg.version_tuple(None))
        self.assertIsNone(vg.version_tuple(""))
        self.assertIsNone(vg.version_tuple("garbage"))
        self.assertIsNone(vg.version_tuple("3.x.4"))


class TestImportedVersion(unittest.TestCase):
    def test_reads_the_module_objects_own_version(self):
        # The whole point (issue #20): the version of the module object the
        # process imported, not the dist metadata's claim -- the metadata
        # can lag a checkout fallback, and the fallback's module reports
        # itself here.
        sentinel = type(sys)("fake_cquarry_cli")
        sentinel.__version__ = "9.9.9"
        with mock.patch.dict(sys.modules, {"cquarry_cli": sentinel}):
            self.assertEqual(vg.imported_version("cquarry_cli"), "9.9.9")

    def test_unimported_or_unversioned_is_none(self):
        self.assertIsNone(vg.imported_version("definitely_not_imported_xyz"))
        bare = type(sys)("bare_module")
        with mock.patch.dict(sys.modules, {"cquarry_cli": bare}):
            self.assertIsNone(vg.imported_version("cquarry_cli"))


class TestInstalledVersion(unittest.TestCase):
    def test_workspace_dists_resolve(self):
        self.assertTrue(vg.installed_version("cquarry"))
        self.assertTrue(vg.installed_version("vir_tui"))
        self.assertTrue(vg.installed_version("cquarry_cli"))

    def test_absent_dist_is_none(self):
        self.assertIsNone(vg.installed_version("definitely_not_a_dist_xyz"))


class TestFloorRefusal(unittest.TestCase):
    def _refusal(self, found, floor=(3, 63, 5)):
        return vg.floor_refusal(
            found, floor, what="the module", why="because", fix="fix it"
        )

    def test_at_or_above_floor_passes(self):
        self.assertIsNone(self._refusal("3.63.5"))
        self.assertIsNone(self._refusal("3.64.0"))
        self.assertIsNone(self._refusal("4.0.0"))

    def test_below_floor_names_both_versions_and_the_fix(self):
        msg = self._refusal("3.63.4")
        self.assertIn("3.63.4", msg)
        self.assertIn("3.63.5+", msg)
        self.assertIn("fix it", msg)

    def test_unreadable_version_refuses_fail_closed(self):
        self.assertIsNotNone(self._refusal(None))
        self.assertIsNotNone(self._refusal(""))
        self.assertIsNotNone(self._refusal("garbage"))


class TestAnnounce(unittest.TestCase):
    def test_one_stderr_line_names_the_workspace(self):
        out = io.StringIO()
        with contextlib.redirect_stderr(out):
            vg.announce()
        line = out.getvalue()
        self.assertIn("toolchain:", line)
        for name in ("cquarry ", "vir_tui ", "cquarry_cli "):
            self.assertIn(name, line)

    def test_absent_packages_read_absent(self):
        # A package neither imported nor installed reads `absent`, never
        # raises: the announcement must work on any machine.
        with (
            mock.patch.object(vg, "imported_version", lambda name: None),
            mock.patch.object(vg, "installed_version", lambda name: None),
        ):
            out = io.StringIO()
            with contextlib.redirect_stderr(out):
                vg.announce()
        self.assertIn("cquarry_cli absent", out.getvalue())


if __name__ == "__main__":
    unittest.main()
