"""Tests for the companion scripts' pure decision logic.

The scripts in scripts/ are standalone (not a package), so they are loaded by
path. Only side-effect-free functions are exercised here; the I/O paths
(Ghostscript, zip reading, DB writes) are not.
"""

import importlib.util
import pathlib
import unittest

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


compress_pdf = _load("compress_pdf")
audit_epub = _load("audit_epub_content")


def _features(**over):
    base = {
        "size": 300 << 20,  # 300 MB
        "pages": 100,
        "optimized": False,
        "form": "none",
        "javascript": False,
        "attachments": 0,
        "avg_dpi": 300,
        "image_count": 10,
        "tagged": False,
    }
    base.update(over)
    return base


class TestRecommend(unittest.TestCase):
    def verdict(self, **over):
        return compress_pdf.recommend(_features(**over))[0]

    def test_skip_small(self):
        self.assertEqual(self.verdict(size=10 << 20), "skip-small")

    def test_manual_when_no_images(self):
        self.assertEqual(self.verdict(avg_dpi=None), "manual")

    def test_skip_already_low_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=120), "skip-already-low-dpi")

    def test_skip_optimized(self):
        self.assertEqual(
            self.verdict(optimized=True, size=150 << 20, avg_dpi=180),
            "skip-optimized",
        )

    def test_ebook_for_high_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=300), "ebook")

    def test_printer_when_risky(self):
        # A form field makes /printer the safer recommendation.
        self.assertEqual(self.verdict(avg_dpi=300, form="AcroForm"), "printer")

    def test_printer_for_moderate_dpi(self):
        self.assertEqual(self.verdict(avg_dpi=200), "printer")


class TestFmtSize(unittest.TestCase):
    def test_units(self):
        self.assertEqual(compress_pdf.fmt_size(2 << 30), "2.00 GB")
        self.assertEqual(compress_pdf.fmt_size(5 << 20), "5.0 MB")
        self.assertTrue(compress_pdf.fmt_size(2048).endswith("KB"))


class TestScriptOf(unittest.TestCase):
    def test_known_scripts(self):
        self.assertEqual(audit_epub.script_of(0x0410), "Cyrillic")
        self.assertEqual(audit_epub.script_of(0x4E2D), "CJK-Han")
        self.assertEqual(audit_epub.script_of(0x0627), "Arabic")

    def test_latin_is_none(self):
        self.assertIsNone(audit_epub.script_of(ord("a")))


class TestFindings(unittest.TestCase):
    def _result(self, **over):
        r = {
            "lang": "en",
            "scripts": {},
            "nonlatin": 0,
            "nonlatin_frac": 0.0,
            "ratios": {
                "en": 0.30,
                "pt": 0.05,
                "de": 0.04,
                "fr": 0.03,
                "es": 0.03,
                "it": 0.03,
                "nl": 0.02,
            },
            "best": "en",
            "nwords": 4000,
            "signature": False,
        }
        r.update(over)
        return r

    def test_clean_english(self):
        self.assertEqual(audit_epub.findings(self._result()), [])

    def test_non_latin(self):
        cats = [
            c
            for c, _ in audit_epub.findings(
                self._result(nonlatin=500, nonlatin_frac=0.5, scripts={"Cyrillic": 500})
            )
        ]
        self.assertIn("NON-LATIN SCRIPT", cats)

    def test_latin_foreign(self):
        cats = [
            c
            for c, _ in audit_epub.findings(
                self._result(
                    best="pt",
                    ratios={
                        "pt": 0.30,
                        "en": 0.05,
                        "de": 0.02,
                        "fr": 0.02,
                        "es": 0.10,
                        "it": 0.08,
                        "nl": 0.02,
                    },
                )
            )
        ]
        self.assertIn("LATIN-SCRIPT FOREIGN", cats)

    def test_injection_signature(self):
        cats = [c for c, _ in audit_epub.findings(self._result(signature=True))]
        self.assertIn("INJECTION SIGNATURE", cats)


if __name__ == "__main__":
    unittest.main()
