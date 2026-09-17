"""The v3.47.0 additions: the duplicate predicate adopted from the engine,
rolled-up tag-tree counts, pace year granularity, and the identifierless
listing."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout

from test_read_modes import _TempDBCase

from cquarry_cli.cli import build_parser, main
from cquarry_cli.modes.analytics import show_pace_stats, show_tag_tree
from cquarry_cli.modes.audit import show_health


class DuplicatePredicateAdoptionTests(_TempDBCase):
    def _add_book(self, book_id, title, author_name, author_sort):
        import sqlite3

        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({book_id},'{title}','{title}','{author_sort}','2024-01-01',"
            f"'2024-01-01',0,'2024-01-02 00:00:00',1.0,'p/{book_id}','u{book_id}')"
        )
        con.execute(
            f"INSERT INTO authors (id,name,sort) VALUES "
            f"({book_id},'{author_name}','{author_sort}')"
        )
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({book_id},{book_id})"
        )
        con.commit()
        con.close()

    def test_duplicates_detected_through_the_engine_predicate(self):
        self._add_book(50, "Same Name", "Author, Shared", "Author, Shared")
        self._add_book(51, "Same Name", "Author, Shared", "Author, Shared")
        out = io.StringIO()
        with redirect_stdout(out):
            rc = show_health(self.db, fmt="json")
        self.assertEqual(rc, 0)
        payload = json.loads(out.getvalue())
        self.assertGreaterEqual(payload["duplicate_groups"], 1)

    def test_duplicate_row_ids_come_out_sorted(self):
        from cquarry.integrity import find_duplicate_books

        self._add_book(50, "Same Name", "Author, Shared", "Author, Shared")
        self._add_book(51, "Same Name", "Author, Shared", "Author, Shared")
        groups = find_duplicate_books(self.db)
        self.assertIn(("same name", "author, shared"), groups)
        self.assertEqual(groups[("same name", "author, shared")], [50, 51])


class IdentifierlessTests(_TempDBCase):
    def test_fully_identified_library_reports_clean(self):
        out = io.StringIO()
        err = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--identifierless", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("Every catalogued book carries", out.getvalue())


class PaceGranularityTests(_TempDBCase):
    def test_year_granularity_renders(self):
        out = io.StringIO()
        with redirect_stdout(out):
            show_pace_stats(self.db, quiet=True, granularity="year")
        self.assertIsInstance(out.getvalue(), str)

    def test_cli_flag_parses(self):
        args = build_parser().parse_args(
            ["--analytics", "pace", "--pace-granularity", "year"]
        )
        self.assertEqual(args.pace_granularity, "year")


class TagTreeRollupTests(_TempDBCase):
    def test_tree_nodes_carry_counts(self):
        out = io.StringIO()
        with redirect_stdout(out):
            show_tag_tree(self.db, quiet=True)
        self.assertIn("Fic (1)", out.getvalue())
        self.assertIn("SciFi (1)", out.getvalue())

    def _add_tagged_book(self, book_id, tag):
        import sqlite3

        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({book_id},'T{book_id}','T{book_id}','A','2024-01-01',"
            f"'2024-01-01',0,'2024-01-02 00:00:00',1.0,'p/{book_id}','u{book_id}')"
        )
        # Calibre dedupes tag names: get or create the one tag row.
        row = con.execute("SELECT id FROM tags WHERE name=?", (tag,)).fetchone()
        tag_id = (
            row[0]
            if row
            else con.execute(
                "INSERT INTO tags (id,name) VALUES ((SELECT COALESCE(MAX(id),0)+1 FROM tags),?)",
                (tag,),
            ).lastrowid
        )
        con.execute(
            "INSERT INTO books_tags_link (book,tag) VALUES (?,?)", (book_id, tag_id)
        )
        con.commit()
        con.close()

    def test_depth_three_tree_and_parents_with_direct_books(self):
        # The real-library shape the 3.47.0 renderer crashed on: a
        # depth-3 subtree (the inlined rollup recursed into a dict as an
        # addend) and a parent that carries its own direct books (the
        # leaves-only rollup dropped them). The engine's tag_rollup
        # arithmetic answers both: every node shows its subtree total.
        self._add_tagged_book(50, "Fic.Fantasy.Grimdark")
        self._add_tagged_book(51, "Fic.Fantasy.Grimdark")
        self._add_tagged_book(52, "Fic.Fantasy")
        self._add_tagged_book(53, "Fic.Standalone")
        out = io.StringIO()
        with redirect_stdout(out):
            show_tag_tree(self.db, quiet=True)
        body = out.getvalue()
        self.assertIn("Fic (5)", body)  # 2 + 1 + 1 beneath, 1 direct SciFi
        self.assertIn("Fantasy (3)", body)  # 2 Grimdark + 1 direct
        self.assertIn("Grimdark (2)", body)
        self.assertIn("Standalone (1)", body)


class IdentifierlessCliFlagTests(unittest.TestCase):
    def test_flag_is_registered(self):
        args = build_parser().parse_args(["--identifierless"])
        self.assertTrue(args.identifierless)


if __name__ == "__main__":
    unittest.main()
