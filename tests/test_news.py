"""Tests for `run news` (the Phase 20 recipe-fetch verb).

The seam is mocked at the house seam line (subprocess.run + shutil.which;
the suite never needs calibre), and the verb's own filesystem behavior is
proved for real: fetched files land, partial outputs are deleted, resume
skips on real files. The one live class needs ebook-convert and only
exercises the offline enumeration.
"""

import contextlib
import io
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from cquarry_cli.cli import main
from cquarry_cli.news import DEFAULT_RECIPES, _dest_name, _title_for

_ENUMERATION = (
    "Available recipes:\n"
    "\tThe Guardian and The Observer\n"
    "\tBBC News\n"
    "\tCNN\n"
    "\tDeutsche Welle\n"
    "\tDeutsche Welle\n"
    "\t fluter. \n"
    "1099 recipes available\n"
)


def _fake_conversions(succeed=(True, True)):
    """A subprocess.run double: the enumeration call answers the fixture
    list; each conversion call writes its output file (when its entry in
    `succeed` is True) and exits 0, else exits 1 leaving a PARTIAL file
    behind for the stale-output rule to delete."""
    calls = []
    state = {"n": 0}

    def fake_run(cmd, capture_output, text, timeout):
        calls.append(cmd)
        if "--list-recipes" in cmd:
            return mock.Mock(returncode=0, stdout=_ENUMERATION, stderr="")
        n = state["n"]
        state["n"] += 1
        target = Path(cmd[2])
        if n < len(succeed) and succeed[n]:
            target.write_bytes(b"edition bytes" * 100)
            return mock.Mock(returncode=0, stdout="", stderr="")
        target.write_bytes(b"partial")
        return mock.Mock(returncode=1, stdout="", stderr="Site is blocking us")

    return fake_run, calls


class _NewsCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_news_"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TestEnumerationParse(unittest.TestCase):
    def test_parse_keeps_the_exact_title_multiset(self):
        from cquarry_cli.news import list_recipe_titles

        with mock.patch(
            "cquarry_cli.news.shutil.which", return_value="/opt/calibre/ebook-convert"
        ):
            with mock.patch(
                "cquarry_cli.news.subprocess.run",
                return_value=mock.Mock(returncode=0, stdout=_ENUMERATION, stderr=""),
            ):
                titles = list_recipe_titles()
        # The header and the count line drop; the raw post-tab bytes
        # survive (recipe resolution compares the input exactly).
        self.assertEqual(
            titles,
            [
                "The Guardian and The Observer",
                "BBC News",
                "CNN",
                "Deutsche Welle",
                "Deutsche Welle",
                " fluter. ",
            ],
        )

    def test_missing_binary_refuses_at_the_enumeration(self):
        from cquarry_cli.news import list_recipe_titles

        with mock.patch("cquarry_cli.news.shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "not on PATH"):
                list_recipe_titles()

    def test_spawn_failure_is_a_runtime_error_with_stderr(self):
        from cquarry_cli.news import list_recipe_titles

        with mock.patch(
            "cquarry_cli.news.shutil.which", return_value="/opt/calibre/ebook-convert"
        ):
            with mock.patch(
                "cquarry_cli.news.subprocess.run",
                return_value=mock.Mock(returncode=2, stdout="", stderr="boom"),
            ):
                with self.assertRaisesRegex(RuntimeError, "boom"):
                    list_recipe_titles()

    def test_title_helpers(self):
        titles = [" fluter. ", "BBC News", "Deutsche Welle", "Deutsche Welle"]
        self.assertEqual(_title_for(titles, "BBC News"), "BBC News")
        # stripped input resolves to the RAW enumerated string
        self.assertEqual(_title_for(titles, "fluter."), " fluter. ")
        self.assertIsNone(_title_for(titles, "No Such Paper"))
        self.assertEqual(
            _dest_name("Al Jazeera in English"), "Al Jazeera in English.epub"
        )
        self.assertEqual(_dest_name("Up/Down"), "Up_Down.epub")


class TestLiveEnumeration(unittest.TestCase):
    """The one live seam: ebook-convert --list-recipes is offline (it
    reads the bundled recipe manifest) and proves the parse against the
    real output shape."""

    def test_real_enumeration(self):
        if shutil.which("ebook-convert") is None:
            self.skipTest("ebook-convert not on PATH")
        from cquarry_cli.news import list_recipe_titles

        titles = list_recipe_titles()
        self.assertGreater(len(titles), 900)
        self.assertIn("BBC News", titles)
        # the ambiguity the design refuses on is real in the live list
        self.assertGreater(titles.count("Deutsche Welle"), 1)


class TestNewsSelection(_NewsCase):
    def test_curated_default_is_the_plan_and_degrades_to_warnings(self):
        fake_run, calls = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, err = self.run_cli("run", "news")
        self.assertEqual(code, 0)
        self.assertIn("news plan:", out)
        # Only the fixture's titles plan; the rest of the curated tuple
        # degrades to warnings (a calibre upgrade renaming one default
        # must not kill the batch).
        planned = [line for line in out.splitlines() if line.startswith("  ")]
        self.assertEqual(len(planned), 3, out)
        for name in ("BBC News", "CNN", "The Guardian and The Observer"):
            self.assertIn(name, out)
        self.assertIn("is not in this calibre's recipe list; skipped", err)
        self.assertIn("Dry run", out)
        # the dry run spawns the enumeration and nothing else
        self.assertEqual(len(calls), 1)
        self.assertIn("--list-recipes", calls[0])

    def test_explicit_recipe_overrides_the_default(self):
        fake_run, calls = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli("run", "news", "--recipe", "BBC News")
        self.assertEqual(code, 0)
        self.assertIn("BBC News.epub", out)
        self.assertNotIn("CNN", out)

    def test_unknown_recipe_is_a_usage_error_naming_the_list(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, _, err = self.run_cli("run", "news", "--recipe", "No Such Paper")
        self.assertEqual(code, 2)
        self.assertIn("unknown recipe", err)
        self.assertIn("--list", err)

    def test_ambiguous_title_is_refused_for_explicit_asks(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, _, err = self.run_cli("run", "news", "--recipe", "Deutsche Welle")
        self.assertEqual(code, 2)
        self.assertIn("2 different builtin recipes", err)

    def test_duplicate_recipe_arguments_deduplicate(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run", "news", "--recipe", "BBC News", "--recipe", "BBC News"
            )
        self.assertEqual(code, 0)
        self.assertEqual(out.count("BBC News.epub"), 1)

    def test_stripped_title_resolves_the_raw_enumerated_string(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli("run", "news", "--recipe", "fluter.")
        self.assertEqual(code, 0)
        self.assertIn("fluter..epub", out)

    def test_list_prints_titles_and_keeps_stdout_machine_clean(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, err = self.run_cli("run", "news", "--list")
        self.assertEqual(code, 0)
        self.assertEqual(
            out.splitlines(),
            [
                "The Guardian and The Observer",
                "BBC News",
                "CNN",
                "Deutsche Welle",
                "Deutsche Welle",
                " fluter. ",
            ],
        )
        self.assertIn("6 recipes available.", err)

    def test_list_json_shape(self):
        import json

        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli("run", "news", "--list", "--format", "json")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["count"], 6)
        self.assertIn("BBC News", data["recipes"])

    def test_list_refuses_selection_flags(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, _, err = self.run_cli("run", "news", "--list", "--recipe", "BBC News")
        self.assertEqual(code, 2)
        self.assertIn("takes no selection flags", err)

    def test_all_expands_to_every_enumerated_title(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, err = self.run_cli("run", "news", "--all")
        self.assertEqual(code, 0)
        # 6 fixture titles; Deutsche Welle (x2) degrades to a skip
        # warning because title addressing cannot choose between them
        self.assertIn("4 edition(s)", out)
        self.assertNotIn("Deutsche Welle.epub", out)
        self.assertIn("names 2 recipes in this calibre; skipped", err)

    def test_dest_file_refused(self):
        blocker = self.tmpdir / "blocker"
        blocker.write_text("not a directory")
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, _, err = self.run_cli(
                "run", "news", "--recipe", "BBC News", "--dest", str(blocker)
            )
        self.assertEqual(code, 2)
        self.assertIn("not a directory", err)

    def test_missing_binary_refuses_even_the_dry_run(self):
        with mock.patch("cquarry_cli.news.shutil.which", return_value=None):
            code, _, err = self.run_cli("run", "news")
        self.assertEqual(code, 2)
        self.assertIn("ebook-convert is not on PATH", err)

    def test_curated_defaults_are_documented_stable_titles(self):
        # The tuple is convenience, not contract (a stale entry degrades
        # to a warning); these pins just keep it deliberate.
        self.assertIn("BBC News", DEFAULT_RECIPES)
        self.assertIn("Associated Press", DEFAULT_RECIPES)
        self.assertNotIn("The Wall Street Journal", DEFAULT_RECIPES)


class TestNewsApply(_NewsCase):
    def test_apply_fetches_each_edition_through_its_own_spawn(self):
        fake_run, calls = _fake_conversions()
        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "news",
                "--recipe",
                "BBC News",
                "--recipe",
                "CNN",
                "--dest",
                str(dest),
                "--apply",
            )
        self.assertEqual(code, 0, out)
        self.assertIn("News: 2 fetched, 0 already present, 0 skipped, 0 failed", out)
        self.assertTrue((dest / "BBC News.epub").exists())
        self.assertTrue((dest / "CNN.epub").exists())
        conversions = [c for c in calls if "--list-recipes" not in c]
        self.assertEqual(
            conversions[0],
            [
                "/opt/calibre/ebook-convert",
                "BBC News.recipe",
                str(dest / "BBC News.epub"),
            ],
        )
        # strictly sequential: one spawn at a time, in plan order
        self.assertEqual([c[1] for c in conversions], ["BBC News.recipe", "CNN.recipe"])

    def test_apply_json_shape(self):
        import json

        fake_run, _ = _fake_conversions()
        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "news",
                "--recipe",
                "BBC News",
                "--dest",
                str(dest),
                "--apply",
                "--format",
                "json",
            )
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["fetched"], 1)
        self.assertEqual(data["failed"], 0)
        self.assertEqual(data["results"][0]["recipe"], "BBC News")
        self.assertEqual(data["results"][0]["status"], "fetched")
        self.assertGreater(data["results"][0]["bytes"], 0)
        self.assertIn("seconds", data["results"][0])

    def test_one_dead_recipe_does_not_kill_the_batch(self):
        fake_run, _ = _fake_conversions(succeed=(False, True))
        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "news",
                "--recipe",
                "BBC News",
                "--recipe",
                "CNN",
                "--dest",
                str(dest),
                "--apply",
            )
        self.assertEqual(code, 1)
        self.assertIn("News: 1 fetched, 0 already present, 0 skipped, 1 failed", out)
        # CNN still fetched after BBC died
        self.assertTrue((dest / "CNN.epub").exists())
        # and the failure's PARTIAL output is gone: it must never stand
        # in for a real edition on the next resume
        self.assertFalse((dest / "BBC News.epub").exists())

    def test_timeout_is_one_report_row_and_the_batch_continues(self):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            if "--list-recipes" in cmd:
                return mock.Mock(returncode=0, stdout=_ENUMERATION, stderr="")
            if "BBC News.recipe" in cmd:
                raise subprocess.TimeoutExpired(cmd, timeout)
            Path(cmd[2]).write_bytes(b"edition bytes" * 100)
            return mock.Mock(returncode=0, stdout="", stderr="")

        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run",
                "news",
                "--recipe",
                "BBC News",
                "--recipe",
                "CNN",
                "--dest",
                str(dest),
                "--apply",
                "--timeout",
                "5",
            )
        self.assertEqual(code, 1)
        self.assertIn("TIMEOUT: BBC News", out)
        self.assertIn("1 failed", out)
        self.assertTrue((dest / "CNN.epub").exists())
        self.assertFalse((dest / "BBC News.epub").exists())

    def test_rc_zero_without_a_file_is_a_failure(self):
        def fake_run(cmd, capture_output, text, timeout):
            if "--list-recipes" in cmd:
                return mock.Mock(returncode=0, stdout=_ENUMERATION, stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(
                "run", "news", "--recipe", "BBC News", "--dest", str(dest), "--apply"
            )
        self.assertEqual(code, 1)
        self.assertFalse((dest / "BBC News.epub").exists())

    def test_resume_skips_existing_editions_and_force_refetches(self):
        fake_run, calls = _fake_conversions()
        dest = self.tmpdir / "editions"
        argv = (
            "run",
            "news",
            "--recipe",
            "BBC News",
            "--dest",
            str(dest),
            "--apply",
        )
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(*argv)
            self.assertEqual(code, 0)
            self.assertIn("1 fetched", out)
            code, out, _ = self.run_cli(*argv)
        self.assertEqual(code, 0)
        self.assertIn("News: 0 fetched, 1 already present", out)
        conversions = [c for c in calls if "--list-recipes" not in c]
        self.assertEqual(len(conversions), 1, "the resume must not re-fetch")
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, _ = self.run_cli(*argv, "--force")
        self.assertEqual(code, 0)
        self.assertIn("1 fetched", out)
        conversions = [c for c in calls if "--list-recipes" not in c]
        self.assertEqual(
            len(conversions), 2, "exactly the first pass and the --force pass"
        )

    def test_interrupt_reports_the_partial_batch(self):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)
            if "--list-recipes" in cmd:
                return mock.Mock(returncode=0, stdout=_ENUMERATION, stderr="")
            if "BBC News.recipe" in cmd:
                Path(cmd[2]).write_bytes(b"edition bytes" * 100)
                return mock.Mock(returncode=0, stdout="", stderr="")
            raise KeyboardInterrupt()

        dest = self.tmpdir / "editions"
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
        ):
            code, out, err = self.run_cli(
                "run",
                "news",
                "--recipe",
                "BBC News",
                "--recipe",
                "CNN",
                "--dest",
                str(dest),
                "--apply",
            )
        self.assertEqual(code, 1)
        self.assertIn("Interrupted: 1 fetched", err)
        # the fetched edition stays; a resume skips it
        self.assertTrue((dest / "BBC News.epub").exists())

    def test_empty_batch_after_curated_skips_is_an_honest_no_op(self):
        fake_run, _ = _fake_conversions()
        with (
            mock.patch(
                "cquarry_cli.news.shutil.which",
                return_value="/opt/calibre/ebook-convert",
            ),
            mock.patch("cquarry_cli.news.subprocess.run", side_effect=fake_run),
            mock.patch(
                "cquarry_cli.news.DEFAULT_RECIPES",
                ("Not A Real Recipe",),
            ),
        ):
            code, out, err = self.run_cli("run", "news", "--apply")
        self.assertEqual(code, 0)
        self.assertIn("No recipes to fetch.", out)
        self.assertIn("is not in this calibre's recipe list", err)


if __name__ == "__main__":
    unittest.main()
