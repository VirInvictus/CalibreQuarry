"""Contract tests for the run verbs' instruments (the 2026-09-08 sweep's
:809): the whole P0 class existed because the companion scripts were only
ever mocked. These tests invoke the actual scripts/ tools against /tmp
fixtures through the real seam adapters, so a drift in a script's report
shape or exit contract fails here before it fails a real run.
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

from cquarry_cli.run import _drm_verdicts, _pdf_battery

# A minimal, header-valid PDF body. The battery's other checks degrade
# gracefully where qpdf/pdfinfo are absent (CI), so the shape contract
# holds on every host.
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >> endobj\n"
    b"trailer << /Size 4 /Root 1 0 R >>\n%%EOF\n"
)


def _minimal_epub(path: str) -> None:
    """A real (if empty) EPUB: zipfile with a mimetype entry, no DRM."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip")


class TestRealInstruments(unittest.TestCase):
    """The actual scripts, through the actual seams, no subprocess mocks."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cquarry_instr_")
        self.downloads = os.path.join(self.temp_dir, "downloads")
        os.makedirs(self.downloads)

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_pdf_battery_against_the_real_script(self):
        pdf = os.path.join(self.downloads, "book.pdf")
        with open(pdf, "wb") as f:
            f.write(_MINIMAL_PDF)
        report = _pdf_battery([pdf])
        self.assertIn(pdf, report)
        entry = report[pdf]
        self.assertIn("kind", entry)
        self.assertIn("findings", entry)

    def test_drm_verdicts_against_the_real_script(self):
        # A real DRM-free epub audits CLEAN through the real audit_drm and
        # its CSV round-trip; the verdicts dict keys on the file path.
        epub = os.path.join(self.downloads, "clean.epub")
        _minimal_epub(epub)
        verdicts = _drm_verdicts(self.downloads)
        self.assertEqual(verdicts[epub], "CLEAN")

    def test_djvu_is_unscanned_not_drm(self):
        # audit_drm skips its N/A verdicts (DJVU has no DRM scheme) when
        # writing the CSV, so the runner sees such files as "unscanned" —
        # never as a quarantine reason.
        djvu = os.path.join(self.downloads, "scan.djvu")
        with open(djvu, "wb") as f:
            f.write(b"AT&TFORM\x00\x00\x00\x14DJVU INFO")
        verdicts = _drm_verdicts(self.downloads)
        self.assertEqual(verdicts.get(djvu, "unscanned"), "unscanned")


class TestDispatchRunWiring(unittest.TestCase):
    """`dispatch_run` had zero test references when the sweep ran; the
    argparse -> verb wiring is a contract like any other."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="cquarry_disp_")
        self.library = os.path.join(self.temp_dir, "library")
        self.downloads = os.path.join(self.temp_dir, "downloads")
        os.makedirs(self.library)
        os.makedirs(self.downloads)
        self.db_path = os.path.join(self.library, "metadata.db")
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "cq_test_run", os.path.join(os.path.dirname(__file__), "test_run.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod._build_library(self.db_path)
        with open(
            os.path.join(self.downloads, "Some Author - A Title.epub"), "wb"
        ) as f:
            f.write(b"EPUBDATA")

    def tearDown(self):
        shutil.rmtree(self.temp_dir)

    def test_phase1_dispatch_produces_a_manifest(self):
        from cquarry_cli.cli import main

        with (
            mock.patch("cquarry_cli.run._screen_duplicates", return_value=set()),
            mock.patch("cquarry_cli.run._drm_verdicts", return_value={}),
            mock.patch("cquarry_cli.run._pdf_battery", return_value={}),
            mock.patch("cquarry_cli.run._bindery_phase1", return_value={}),
        ):
            rc = main(["--db", self.db_path, "run", "phase1", self.downloads])
        self.assertEqual(rc, 0)
        manifests = os.path.join(self.library, ".claude", "manifests")
        (name,) = os.listdir(manifests)
        self.assertTrue(name.endswith("-batch.json"))

    def test_dispatch_needs_its_arguments(self):
        from cquarry_cli.cli import main

        # --db belongs to the top-level parser: it precedes the subcommand.
        self.assertEqual(
            main(["--db", self.db_path, "run", "phase1"]),
            2,
        )
        self.assertEqual(main(["run", "sign"]), 2)
        self.assertEqual(main(["run", "phase3"]), 2)


if __name__ == "__main__":
    unittest.main()
