"""Tests for the two-level help surface: the pre-parse intercept, the
topics, and the per-verb run pages. The flag tables are generated from
build_parser()'s argument groups, so these pins guard the curated layer:
the verb claim map (every run flag claimed by some verb), the topic
registry, the mode table, and the group assignment the topics render."""

import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

from cquarry_cli import helpcli
from cquarry_cli.cli import build_parser
from cquarry_cli.helpcli import (
    RUN_VERBS,
    SHARED_RUN_FLAGS,
    TOPICS,
    VERB_FLAGS,
    handle_help,
)


class TestIntercept(unittest.TestCase):
    """handle_help answers topic requests pre-parse; bare help falls
    through to the parser's custom overview action."""

    def test_bare_help_falls_through_to_the_parser(self):
        self.assertIsNone(handle_help(["--help"]))
        self.assertIsNone(handle_help(["-h"]))
        self.assertIsNone(handle_help(["--stats"]))
        self.assertIsNone(handle_help(["--db", "/tmp/x"]))
        self.assertIsNone(handle_help([]))

    def test_every_topic_renders(self):
        for topic in TOPICS:
            code, text = handle_help(["--help", topic])
            self.assertEqual(code, 0, topic)
            self.assertTrue(text.strip(), topic)

    def test_unknown_topic_is_a_usage_error_naming_the_topics(self):
        code, text = handle_help(["--help", "nope"])
        self.assertEqual(code, 2)
        self.assertIn("read, write, set, run, examples, all", text)

    def test_topic_beats_following_flags(self):
        # help exits first, exactly like argparse's own action
        code, text = handle_help(["--help", "write", "--db", "/tmp/x"])
        self.assertEqual(code, 0)
        self.assertIn("write verbs", text)

    def test_run_overview_when_no_verb_is_named(self):
        code, text = handle_help(["run", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("run VERB", text)
        code, text = handle_help(["run", "--db", "/x", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("run VERB", text)

    def test_both_verb_page_forms_for_every_verb(self):
        for verb in RUN_VERBS:
            for argv in (
                ["run", "--help", verb],
                ["run", verb, "--help"],
                ["run", verb, "--manifest", "x", "--help"],
            ):
                code, text = handle_help(argv)
                self.assertEqual(code, 0, argv)
                self.assertIn(verb, text, argv)

    def test_unknown_verb_is_a_usage_error_naming_the_verbs(self):
        code, text = handle_help(["run", "--help", "wat"])
        self.assertEqual(code, 2)
        self.assertIn("phase1", text)


class TestHelpPins(unittest.TestCase):
    """The anti-rot set: generated tables cover the parser, and the
    curated claim map covers every run flag."""

    def setUp(self):
        self.parser = build_parser()

    def _run_parser(self):
        for action in self.parser._actions:
            if action.dest == "subcommand":
                return action.choices["run"]
        raise AssertionError("no run subparser")

    def test_every_mode_appears_in_the_overview(self):
        text = helpcli.overview(self.parser)
        modes = next(
            g for g in self.parser._action_groups if g.title == helpcli.MODES_TITLE
        )
        for action in modes._group_actions:
            self.assertIn(action.option_strings[0], text)

    def test_every_top_level_flag_appears_in_help_all(self):
        text = handle_help(["--help", "all"])[1]
        for action in self.parser._actions:
            for opt in action.option_strings:
                if opt in ("-h", "--help"):
                    continue
                self.assertIn(opt, text, opt)

    def test_every_run_flag_is_claimed_by_a_verb_or_shared(self):
        claimed = set(SHARED_RUN_FLAGS)
        for flags in VERB_FLAGS.values():
            claimed.update(flags)
        for action in self._run_parser()._actions:
            if action.dest in ("help", "phase"):
                # phase is the verb selector itself, not a claimable flag
                continue
            self.assertIn(action.dest, claimed, action.dest)

    def test_every_run_choice_has_a_page_and_prose(self):
        run_p = self._run_parser()
        choices = next(a for a in run_p._actions if a.dest == "phase").choices
        self.assertEqual(set(choices), set(RUN_VERBS))
        for verb in choices:
            page = handle_help(["run", "--help", verb])[1]
            self.assertIn(verb, page)
            self.assertTrue(helpcli.VERB_PROSE[verb].strip(), verb)

    def test_topic_pages_render_their_groups(self):
        read_text = handle_help(["--help", "read"])[1]
        self.assertIn("--genre-depth", read_text)
        self.assertIn("--restrict", read_text)
        self.assertIn("--format", read_text)
        write_text = handle_help(["--help", "write"])[1]
        self.assertIn("--set-title", write_text)
        self.assertIn("--remove-tag", write_text)
        set_text = handle_help(["--help", "set"])[1]
        self.assertIn("--batch-add-tag", set_text)
        self.assertIn("--from-manifest", set_text)

    def test_parse_behavior_unchanged_by_the_help_layer(self):
        # the modes check in main() replaces argparse's exclusive group;
        # single modes still parse, and the grammar is untouched
        args = self.parser.parse_args(["--stats"])
        self.assertTrue(args.stats)
        args = self.parser.parse_args(["run", "phase1", "somedir"])
        self.assertEqual(args.phase, "phase1")


class TestColor(unittest.TestCase):
    """The CPython 3.14 argparse theme, lattice-music's contract: layout
    runs on plain text, paint happens on finished lines, piped output
    stays plain, and the painted render stripped of codes is byte-for-byte
    the plain one."""

    _CSI = re.compile(r"\x1b\[[0-9;]*m")

    def _plain(self, argv):
        with mock.patch.dict(os.environ, {"NO_COLOR": "1"}):
            return handle_help(argv)[1]

    def test_piped_output_stays_plain(self):
        # tests run piped; no forcing env in play
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORCE_COLOR", None)
            os.environ.pop("NO_COLOR", None)
            code, text = handle_help(["--help", "write"])
            self.assertEqual(code, 0)
            self.assertNotIn("\x1b", text)

    def test_force_color_paints_the_cpython_theme(self):
        plain = self._plain(["--help", "read"])
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            painted = helpcli.paint(plain)
        self.assertIn("\x1b[1;36m--genre-depth\x1b[0m", painted)
        self.assertIn("\x1b[1;33mRECENT\x1b[0m", painted)
        # underscored metavars do not match the ALL-CAPS label shape
        self.assertNotIn("\x1b[1;33mBOOK_ID\x1b[0m", painted)
        # force wins even though the test stream is not a tty
        self.assertNotEqual(painted, plain)

    def test_env_contract(self):
        text = "run VERB  --flag FILE"
        cases = [
            ({"FORCE_COLOR": "1"}, True),
            ({"NO_COLOR": "1"}, False),
            ({"NO_COLOR": "1", "FORCE_COLOR": "1"}, True),
            ({"PYTHON_COLORS": "0"}, False),
            ({"PYTHON_COLORS": "0", "FORCE_COLOR": "1"}, True),
            ({}, False),  # piped in tests: no tty, no force
        ]
        for env, want in cases:
            with self.subTest(env=env):
                with mock.patch.dict(os.environ, env):
                    painted = helpcli.paint(text)
                self.assertEqual("\x1b" in painted, want, env)

    def test_strip_invariant(self):
        pages = [
            ["--help", "read"],
            ["--help", "write"],
            ["--help", "set"],
            ["--help", "run"],
            ["--help", "examples"],
            ["--help", "all"],
            ["run", "--help", "phase2"],
            ["run", "--help", "device"],
        ]
        for argv in pages:
            with self.subTest(argv=argv):
                plain = self._plain(argv)
                with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
                    painted = helpcli.paint(plain)
                self.assertEqual(self._CSI.sub("", painted), plain)

    def test_overview_paints_modes_and_prog(self):
        from cquarry_cli.cli import build_parser

        plain = helpcli.overview(build_parser())
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            painted = helpcli.paint(plain)
        self.assertIn("\x1b[1;35mcquarry\x1b[0m", painted)
        self.assertIn("\x1b[1;36m--catalog\x1b[0m", painted)
        self.assertEqual(self._CSI.sub("", painted), plain)

    def test_main_print_path_colors_when_forced(self):
        import contextlib
        import io
        import os

        from cquarry_cli.cli import main

        out = io.StringIO()
        with (
            mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}),
            mock.patch.object(sys, "argv", ["cquarry", "--help", "write"]),
            contextlib.redirect_stdout(out),
        ):
            code = main(["--help", "write"])
        self.assertEqual(code, 0)
        self.assertIn("\x1b[1;36m--set-title\x1b[0m", out.getvalue())

    def test_main_print_path_plain_without_env(self):
        import contextlib
        import io
        import os

        from cquarry_cli.cli import main

        out = io.StringIO()
        env = {k: v for k, v in os.environ.items() if k != "FORCE_COLOR"}
        with (
            mock.patch.dict(os.environ, env, clear=True),
            contextlib.redirect_stdout(out),
        ):
            code = main(["--help", "write"])
        self.assertEqual(code, 0)
        self.assertNotIn("\x1b", out.getvalue())


class TestTruthedUpPages(unittest.TestCase):
    """The AI-grokability probe's findings, fixed: verb pages tell the
    truth about --apply and --backup-dir, and the json topic dumps the
    whole surface as one machine-readable artifact."""

    def test_pathway_pages_do_not_advertise_apply(self):
        for verb in ("phase1", "sign", "approve", "phase2", "phase3"):
            page = handle_help(["run", "--help", verb])[1]
            self.assertNotIn("--apply (execute", page, verb)

    def test_apply_pages_do_and_claim_backup_dir_where_required(self):
        from cquarry_cli.helpcli import VERB_FLAGS

        backup_verbs = {
            verb for verb, flags in VERB_FLAGS.items() if "backup_dir" in flags
        }
        self.assertEqual(
            backup_verbs,
            {
                "phase2",
                "convert",
                "polish",
                "cover",
                "merge",
                "flush",
                "backfill",
                "backup-metadata",
            },
        )
        for verb in ("flush", "convert", "backup-metadata"):
            page = handle_help(["run", "--help", verb])[1]
            self.assertIn("--apply (execute", page, verb)
            self.assertIn("--backup-dir", page, verb)

    def test_read_topic_names_the_two_mode_outcome(self):
        text = handle_help(["--help", "read"])[1]
        self.assertIn("two modes in one invocation", text)
        self.assertIn("exit 2", text)


class TestJsonTopic(unittest.TestCase):
    def test_valid_deterministic_and_versioned(self):
        import json as jsonlib

        first = handle_help(["--help", "json"])[1]
        second = handle_help(["--help", "json"])[1]
        self.assertEqual(first, second)
        data = jsonlib.loads(first)
        from cquarry_cli import VERSION

        self.assertEqual(data["version"], VERSION)
        self.assertEqual(data["tool"], "cquarry")
        self.assertEqual(len(data["run"]["verbs"]), 23)

    def test_json_covers_every_top_level_flag(self):
        import json as jsonlib

        data = jsonlib.loads(handle_help(["--help", "json"])[1])
        text = handle_help(["--help", "json"])[1]
        parser = build_parser()
        for action in parser._actions:
            for opt in action.option_strings:
                if opt in ("-h", "--help"):
                    continue
                self.assertIn(opt, text, opt)
        dests = {f["dest"] for group in data["groups"] for f in group["flags"]}
        for action in parser._actions:
            if action.dest in ("help", "subcommand"):
                continue
            self.assertIn(action.dest, dests, action.dest)

    def test_json_takes_apply_matches_the_apply_table(self):
        import json as jsonlib

        data = jsonlib.loads(handle_help(["--help", "json"])[1])
        verbs = data["run"]["verbs"]
        self.assertFalse(verbs["sign"]["takes_apply"])
        self.assertTrue(verbs["flush"]["takes_apply"])
        self.assertEqual(
            {v for v, info in verbs.items() if info["takes_apply"]},
            set(data["run"]["apply_verbs"]),
        )
        # the flush claim map carries the required backup flag now
        flush_dests = {f["dest"] for f in verbs["flush"]["flags"]}
        self.assertIn("backup_dir", flush_dests)

    def test_json_never_colored_even_forced(self):
        import os

        from cquarry_cli.helpcli import paint

        handle_help(["--help", "json"])
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            self.assertNotIn("\x1b", handle_help(["--help", "json"])[1])
            self.assertIn("\x1b", paint("run VERB --flag FILE"))


class TestDocsPins(unittest.TestCase):
    """The docs audit's structural ask: the hand-written mode tables
    (README Features, spec Modes) cannot silently drop a shipped mode.
    Presence-only pin: each parser mode flag string must appear in both
    files."""

    def _mode_flags(self):
        parser = build_parser()
        modes = next(g for g in parser._action_groups if g.title == helpcli.MODES_TITLE)
        return [a.option_strings[0] for a in modes._group_actions]

    def test_readme_features_table_lists_every_mode(self):
        readme = (Path(__file__).resolve().parent.parent / "README.md").read_text()
        for flag in self._mode_flags():
            self.assertIn(flag, readme, flag)

    def test_spec_modes_table_lists_every_mode(self):
        spec = (Path(__file__).resolve().parent.parent / "spec.md").read_text()
        for flag in self._mode_flags():
            self.assertIn(flag, spec, flag)


if __name__ == "__main__":
    unittest.main()
