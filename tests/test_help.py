"""Tests for the two-level help surface: the pre-parse intercept, the
topics, and the per-verb run pages. The flag tables are generated from
build_parser()'s argument groups, so these pins guard the curated layer:
the verb claim map (every run flag claimed by some verb), the topic
registry, the mode table, and the group assignment the topics render."""

import unittest

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
