"""The dest-list constants module: every parallel write-dest list is cut
from one source, and every member is a dest the real parser actually
defines, so a dest that exists only in a list (or only in the parser)
cannot rot."""

import unittest

from cquarry_cli import dests, restrict, setwrite, writeops
from cquarry_cli.cli import build_parser


def _parser_dests() -> set[str]:
    return {a.dest for a in build_parser()._actions}


class DestListsMatchTheParserTests(unittest.TestCase):
    def test_single_book_dests_exist_in_the_parser(self):
        parser_dests = _parser_dests()
        for dest in dests.SINGLE_BOOK_DESTS:
            self.assertIn(dest, parser_dests, dest)

    def test_set_mode_sources_exist_in_the_parser(self):
        parser_dests = _parser_dests()
        for dest in dests.SET_MODE_SOURCES:
            self.assertIn(dest, parser_dests, dest)

    def test_batch_dests_exist_in_the_parser(self):
        parser_dests = _parser_dests()
        for dest in dests.BATCH_VALUE_DESTS + dests.BATCH_BOOL_DESTS:
            self.assertIn(dest, parser_dests, dest)

    def test_the_four_lists_are_disjoint(self):
        single = set(dests.SINGLE_BOOK_DESTS)
        sources = set(dests.SET_MODE_SOURCES)
        batch = set(dests.BATCH_VALUE_DESTS) | set(dests.BATCH_BOOL_DESTS)
        self.assertFalse(single & sources)
        self.assertFalse(single & batch)
        self.assertFalse(sources & batch)

    def test_the_refusal_aggregate_is_the_composition(self):
        self.assertEqual(
            dests.WRITE_FLAG_DESTS,
            tuple(dests.SINGLE_BOOK_DESTS)
            + dests.SET_MODE_SOURCES
            + dests.BATCH_VALUE_DESTS
            + dests.BATCH_BOOL_DESTS,
        )


class ConsumersReadTheSharedSourceTests(unittest.TestCase):
    def test_restrict_refusal_gate_reads_the_aggregate(self):
        self.assertIs(restrict._WRITE_FLAG_DESTS, dests.WRITE_FLAG_DESTS)

    def test_writeops_reexports_the_shared_list(self):
        self.assertIs(writeops.SINGLE_BOOK_DESTS, dests.SINGLE_BOOK_DESTS)

    def test_setwrite_sources_are_the_shared_tuple(self):
        self.assertIs(setwrite._SOURCES, dests.SET_MODE_SOURCES)

    def test_has_verbs_counts_value_flags_by_presence(self):
        ns = build_parser().parse_args(["--from-untagged", "--batch-add-tag", ""])
        self.assertTrue(setwrite._has_verbs(ns))

    def test_has_verbs_counts_clear_flags_by_truthiness(self):
        ns = build_parser().parse_args(["--from-untagged", "--batch-clear-tags"])
        self.assertTrue(setwrite._has_verbs(ns))
        bare = build_parser().parse_args(["--from-untagged"])
        self.assertFalse(setwrite._has_verbs(bare))


if __name__ == "__main__":
    unittest.main()
