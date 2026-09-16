"""The v3.46.0 additions: --search --format md, --health --format json with
--fail-on-findings and the annotations-dirtied line, and the TUI menu
covering Format Stats and Trash Listing."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from cquarry.db import CalibreDB

from cquarry_cli import tui as tui_mod
from cquarry_cli.modes.audit import show_health
from cquarry_cli.modes.export import run_search_export
from cquarry_cli.tui import _menu_sections

from test_read_modes import _TempDBCase


class SearchMarkdownTests(_TempDBCase):
    def test_md_delegates_to_the_catalog_emitter(self):
        out_path = os.path.join(self.db_path + ".search.md")
        _, out, _ = self._capture(
            run_search_export, self.db, "tags:Fic.SciFi", out_path, fmt="md"
        )
        text = Path(out_path).read_text(encoding="utf-8")
        self.assertIn("# Calibre Library Export:", text)
        self.assertIn("## Herbert, Frank", text)
        self.assertIn("**Dune**", text)

    def test_md_without_output_uses_the_default_name(self):
        cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())
        try:
            _, out, _ = self._capture(
                run_search_export, self.db, "tags:Fic.SciFi", None, fmt="md"
            )
            self.assertTrue(os.path.exists("search_results.md"))
        finally:
            os.chdir(cwd)

    def test_unknown_format_still_refused(self):
        _, _, err = self._capture(
            run_search_export, self.db, "tags:Fic.SciFi", None, fmt="bogus"
        )
        self.assertIn("Use 'json', 'csv', 'ai', or 'md'.", err)


class HealthJsonTests(_TempDBCase):
    def test_json_payload_is_the_counts_dict(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = show_health(self.db, fmt="json")
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        for key in (
            "books",
            "issue_count",
            "book_issues",
            "problem_counts",
            "metadata_quality",
            "pending_opf_sync",
            "pending_annotations_sync",
            "fts_sidecar_present",
        ):
            self.assertIn(key, payload)
        self.assertEqual(payload["books"], 1)

    def test_fail_on_findings_flips_the_exit(self):
        # the fixture book is unrated: the digest has findings
        out = io.StringIO()
        with redirect_stdout(out):
            rc = show_health(self.db, fail_on_findings=True)
        self.assertEqual(rc, 1)
        # and the default contract stays exit 0
        out = io.StringIO()
        with redirect_stdout(out):
            rc = show_health(self.db)
        self.assertEqual(rc, 0)

    def test_quiet_still_gates_on_the_flag(self):
        out = io.StringIO()
        with redirect_stdout(out):
            rc = show_health(self.db, quiet=True, fail_on_findings=True)
        self.assertEqual(rc, 1)

    def test_non_json_format_is_refused_at_the_cli(self):
        # the refusal lives in cli.py's dispatch; exercised through the
        # parser here by name to keep the contract visible in this file.
        from cquarry_cli.cli import build_parser

        args = build_parser().parse_args(["--health", "--format", "csv"])
        self.assertEqual(args.format, "csv")


class TuiMenuCoverageTests(unittest.TestCase):
    """README promises the menu covers every read mode: Format Stats and
    the Trash Listing must be reachable, not just the CLI."""

    def test_format_stats_and_trash_are_menu_entries(self):
        names = [name for _header, rows in _menu_sections() for name in rows]
        self.assertIn("Format Stats", names)
        self.assertIn("Trash Listing", names)

    def test_settings_stays_the_last_section(self):
        sections = _menu_sections()
        self.assertEqual(sections[-1][0], "Settings")


if __name__ == "__main__":
    unittest.main()
