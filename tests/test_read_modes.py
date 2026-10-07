"""Smoke tests for the v3.21 read modes: --book detail, --entities,
--reading-progress, --columns, and --info.

Each mode is exercised both directly (output assertions against a temp
Calibre-shaped database) and through cli.main() for exit-code plumbing.
Unknown-book ids must fail cleanly with exit 1, never a traceback.
"""

import csv
import io
import json
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from cquarry.db import CalibreDB

from cquarry_cli.cli import main
from cquarry_cli.modes.detail import show_book
from cquarry_cli.modes.display import show_entities, show_reading_progress
from cquarry_cli.modes.info import show_columns, show_info

from _fixtures import SCHEMA as _SCHEMA

# The fixture's #read column is direct-storage (bool: no value-table +
# link pair); cquarry probes for the link table and reads (book, value).
_SCHEMA += """
CREATE TABLE custom_column_1 (id INTEGER PRIMARY KEY, book INT, value TEXT);
"""


def _build(db_path):
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    con.execute(
        "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
        "has_cover,last_modified,series_index,path,uuid) VALUES "
        "(1,'Dune','Dune','Herbert, Frank','2024-01-01','2024-01-01',0,"
        "'2024-01-02 00:00:00',1.0,'herbert/dune','uuid-1')"
    )
    con.execute(
        "INSERT INTO authors (id,name,sort,link) VALUES "
        "(1,'Herbert, Frank','Herbert, Frank','https://example.com/frank')"
    )
    con.execute("INSERT INTO books_authors_link (book,author) VALUES (1,1)")
    con.execute("INSERT INTO tags (id,name) VALUES (1,'Fic.SciFi')")
    con.execute("INSERT INTO books_tags_link (book,tag) VALUES (1,1)")
    con.execute("INSERT INTO ratings (id,rating) VALUES (1,8)")
    con.execute("INSERT INTO books_ratings_link (book,rating) VALUES (1,1)")
    con.execute("INSERT INTO publishers (id,name) VALUES (1,'Ace')")
    con.execute("INSERT INTO books_publishers_link (book,publisher) VALUES (1,1)")
    con.execute("INSERT INTO languages (id,lang_code) VALUES (1,'eng')")
    con.execute("INSERT INTO books_languages_link (book,lang_code) VALUES (1,1)")
    con.execute(
        "INSERT INTO data (book,format,name,uncompressed_size) "
        "VALUES (1,'EPUB','Dune',2048)"
    )
    con.execute(
        "INSERT INTO identifiers (book,type,val) VALUES (1,'isbn','9780441172719')"
    )
    con.execute(
        "INSERT INTO comments (book,text) VALUES (1,'<p>A <b>desert</b> planet.</p>')"
    )
    con.execute(
        "INSERT INTO annotations (book,format,user_type,user,timestamp,"
        "annot_id,annot_type,annot_data) VALUES "
        "(1,'EPUB','user','reader','2025-05-01T10:00:00','a1','highlight',"
        '\'{"text": "the spice must flow"}\')'
    )
    con.execute(
        "INSERT INTO last_read_positions (book,format,user,device,cfi,epoch,"
        "pos_frac) VALUES (1,'EPUB','reader','Kobo','/body/12',1735689600,0.42)"
    )
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('grouped_search_terms',?)",
        (json.dumps({"mygroup": ["tags", "series"]}),),
    )
    con.execute(
        "INSERT INTO preferences (key,val) VALUES ('user_categories',?)",
        (json.dumps({"Favourites": [["Dune", "books"]]}),),
    )
    con.execute(
        "INSERT INTO custom_columns (id,label,name,datatype,is_multiple) "
        "VALUES (1,'read','Read','bool',0)"
    )
    con.execute("INSERT INTO custom_column_1 (book,value) VALUES (1,'1')")
    con.commit()
    con.close()


class _TempDBCase(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_read_")
        os.close(fd)
        _build(self.db_path)
        self.db = CalibreDB(self.db_path)

    def tearDown(self):
        self.db.close()
        os.unlink(self.db_path)

    def _capture(self, fn, *args, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            result = fn(*args, **kwargs)
        return result, out.getvalue(), err.getvalue()


class TestBookDetail(_TempDBCase):
    def test_full_record_sections(self):
        found, out, _ = self._capture(show_book, self.db, 1)
        self.assertTrue(found)
        for needle in (
            "Dune",
            "Herbert, Frank",
            "isbn: 9780441172719",
            "EPUB",
            "Fic.SciFi",
            "A desert planet.",  # HTML stripped
            "Kobo",
            "42%",
            "Identifiers:",
            "Reading progress:",
            "Read (#read): 1",
        ):
            self.assertIn(needle, out)
        self.assertNotIn("<p>", out)
        self.assertNotIn("<b>", out)

    def test_annotation_summary(self):
        _, out, _ = self._capture(show_book, self.db, 1)
        self.assertIn("Annotations (1):", out)
        self.assertIn("highlight", out)
        self.assertIn("the spice must flow", out)

    def test_unknown_id_reports_and_returns_false(self):
        found, out, err = self._capture(show_book, self.db, 999)
        self.assertFalse(found)
        self.assertIn("no book with id 999", err)

    def test_cli_exit_codes(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--book", "1", "--db", self.db_path])
        self.assertEqual(rc, 0)
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--book", "999", "--db", self.db_path])
        self.assertEqual(rc, 1)


class TestBookBatchForms(_TempDBCase):
    """v3.26.0: --book takes comma-separated ids and an --untagged selector
    (the phase-3 entry state: show every untagged book's dossier)."""

    def _add_book(self, book_id, title, tagged=False):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(
                "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
                "has_cover,last_modified,series_index,path,uuid) VALUES "
                "(?,?,?,?,'2024-01-01','2024-01-01',0,'2024-01-02 00:00:00',1.0,?,?)",
                (book_id, title, title, "X", f"x/{book_id}", f"uuid-{book_id}"),
            )
            if tagged:
                con.execute(
                    "INSERT INTO books_tags_link (book,tag) VALUES (?,1)", (book_id,)
                )
            con.commit()
        finally:
            con.close()

    def _main(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([*argv, "--db", self.db_path])
        return rc, out.getvalue(), err.getvalue()

    def test_comma_list_renders_each_book(self):
        self._add_book(2, "Specific Heat")
        rc, out, _ = self._main("--book", "1,2")
        self.assertEqual(rc, 0)
        self.assertIn("Dune", out)
        self.assertIn("Specific Heat", out)

    def test_comma_list_unknown_id_renders_the_rest_and_fails(self):
        rc, out, err = self._main("--book", "1,999")
        self.assertEqual(rc, 1)
        self.assertIn("Dune", out)
        self.assertIn("no book with id 999", err)

    def test_untagged_selector_renders_only_untagged(self):
        self._add_book(2, "Untagged Book")
        self._add_book(3, "Another Tagged", tagged=True)
        rc, out, _ = self._main("--book", "--untagged")
        self.assertEqual(rc, 0)
        self.assertIn("Untagged Book", out)
        self.assertNotIn("Dune", out)
        self.assertNotIn("Another Tagged", out)

    def test_untagged_selector_clean_when_fully_tagged(self):
        rc, out, _ = self._main("--book", "--untagged")
        self.assertEqual(rc, 0)
        self.assertIn("No untagged books", out)

    def test_untagged_with_ids_is_a_usage_error(self):
        rc, _, _ = self._main("--book", "1", "--untagged")
        self.assertEqual(rc, 2)

    def test_bare_book_flag_without_selector_is_a_usage_error(self):
        rc, _, err = self._main("--book")
        self.assertEqual(rc, 2)
        self.assertIn("--book needs at least one id", err)

    def test_show_custom_accepts_label_form(self):
        # cquarry 1.9's dual resolution reaches the CLI through
        # load_custom_column: the #label form and the display name both work.
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "catalog.txt")
            rc, _, _ = self._main(
                "--catalog", "--show-custom", "#read", "--output", out_path
            )
            self.assertEqual(rc, 0)
            with open(out_path, encoding="utf-8") as fh:
                catalog = fh.read()
        self.assertIn("#read", catalog)
        # The historical display-name form resolves to the same column.
        with tempfile.TemporaryDirectory() as td:
            out_path = os.path.join(td, "catalog.txt")
            rc, _, _ = self._main(
                "--catalog", "--show-custom", "Read", "--output", out_path
            )
            self.assertEqual(rc, 0)
            with open(out_path, encoding="utf-8") as fh:
                catalog = fh.read()
        self.assertIn("Read", catalog)


class TestEntitiesAndProgress(_TempDBCase):
    def test_entities_authors_with_sort_and_link(self):
        _, out, _ = self._capture(show_entities, self.db, "authors")
        self.assertIn("Herbert, Frank", out)
        self.assertIn("https://example.com/frank", out)

    def test_entities_ratings_render_stars(self):
        _, out, _ = self._capture(show_entities, self.db, "ratings")
        self.assertIn("4.0", out)

    def test_entities_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            self._capture(show_entities, self.db, "goblins")

    def test_reading_progress(self):
        _, out, _ = self._capture(show_reading_progress, self.db)
        self.assertIn("Dune", out)
        self.assertIn("Kobo", out)
        self.assertIn("42.0%", out)


class TestInfoAndColumns(_TempDBCase):
    def test_info_sections(self):
        _, out, _ = self._capture(show_info, self.db)
        for needle in (
            "Identity:",
            "Saved searches (0):",
            "User categories (1):",
            "@Favourites",
            "Grouped search terms (1):",
            "mygroup: tags, series",
            "Sync queues:",
        ):
            self.assertIn(needle, out)

    def test_columns_lists_schema(self):
        _, out, _ = self._capture(show_columns, self.db)
        self.assertIn("#read", out)
        self.assertIn("bool", out)
        self.assertIn("editable", out)

    def test_cli_info_and_columns_exit_zero(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--info", "--db", self.db_path])
        self.assertEqual(rc, 0)
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--columns", "--db", self.db_path])
        self.assertEqual(rc, 0)


class TestReadSurfaceExitCodes(_TempDBCase):
    """The :780 normalization: search parse failures and unknown wings
    used to exit 0 (the search one leaving nothing, the catalog one
    leaving a stale file); exportlt's self-check verdict was discarded."""

    def test_bad_search_expression_exits_one(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--search", "(((", "--db", self.db_path])
        self.assertEqual(rc, 1)

    def test_unknown_wing_exits_two(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--catalog", "--wing", "NoSuchWing", "--db", self.db_path])
        self.assertEqual(rc, 2)

    def test_unknown_export_format_exits_two(self):
        # md is a legal --format for catalogs (3.42.0), but --export only
        # serializes json/csv/ai. The refusal used to print "Unknown
        # format" and exit 0, reporting success while writing nothing.
        out = self.db_path + ".export.json"
        rc, _, err = self._capture(
            main,
            ["--export", "--format", "md", "--output", out, "--db", self.db_path],
        )
        self.assertEqual(rc, 2)
        self.assertIn("Unknown format", err)
        self.assertFalse(os.path.exists(out))

    def test_bad_show_custom_exits_one(self):
        # The sibling silent exit-0: an unknown column was reported and
        # then discarded with a success code (run_search_export's shape).
        out = self.db_path + ".export.json"
        rc, _, err = self._capture(
            main,
            [
                "--export",
                "--show-custom",
                "#nope",
                "--output",
                out,
                "--db",
                self.db_path,
            ],
        )
        self.assertEqual(rc, 1)
        self.assertFalse(os.path.exists(out))

    def test_implicit_wing_catalog_honors_format_md(self):
        # The no-mode fallback used to call write_catalog without fmt, so
        # `--wing W --format md` silently rendered plain text while the
        # documented `--catalog --wing W --format md` rendered Markdown.
        con = sqlite3.connect(self.db_path)
        con.execute(
            "INSERT INTO preferences (key,val) VALUES ('virtual_libraries',?)",
            (json.dumps({"Wing": "tags:Fic.SciFi"}),),
        )
        con.commit()
        con.close()
        out = self.db_path + ".wing.md"
        self.addCleanup(lambda: os.path.exists(out) and os.unlink(out))
        rc, _, _ = self._capture(
            main,
            [
                "--wing",
                "Wing",
                "--format",
                "md",
                "--output",
                out,
                "--db",
                self.db_path,
            ],
        )
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read(2), "# ")

    def test_exportlt_self_check_fails_the_verb(self):
        with mock.patch(
            "cquarry_cli.cli.run_librarything_export", return_value=1
        ) as lt:
            rc, _, _ = self._capture(
                main,
                ["--exportlt", "--outdir", self.db_path + ".lt", "--db", self.db_path],
            )
        self.assertEqual(rc, 1)
        lt.return_value = 1

    def test_negative_recent_is_refused(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--recent", "-3", "--db", self.db_path])
        self.assertEqual(rc, 2)

    def test_corrupt_epoch_does_not_kill_reading_progress(self):
        # A milliseconds-valued epoch used to traceback the whole listing.
        con = sqlite3.connect(self.db_path)
        con.execute(
            "UPDATE last_read_positions SET epoch = 1725888000000 WHERE book = 1"
        )
        con.commit()
        con.close()
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--reading-progress", "--db", self.db_path])
        self.assertEqual(rc, 0)
        self.assertIn("1725888000000?", out.getvalue())


class TestOutputGuard(_TempDBCase):
    """The read surface's last mile (the sweep's P0): a report aimed at
    metadata.db used to replace the database at exit 0. Every writer now
    refuses the database and its sidecars, before anything is opened."""

    def _db_bytes(self):
        with open(self.db_path, "rb") as f:
            return f.read()

    def _refused(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main([*argv, "--db", self.db_path])
        self.assertEqual(rc, 2)
        self.assertIn("refusing", err.getvalue())
        self.assertEqual(self._db_bytes(), self._sentinel)
        self.assertFalse(os.path.exists(self.db_path + ".cquarry-tmp"))

    def setUp(self):
        super().setUp()
        self._sentinel = self._db_bytes()

    def test_export_over_the_database_is_refused(self):
        # The sweep's proven attack, verbatim.
        self._refused(["--export", "--format", "json", "--output", self.db_path])

    def test_audit_catalog_and_annotations_refuse_the_database(self):
        self._refused(["--audit", "--output", self.db_path])
        self._refused(["--catalog", "--output", self.db_path])
        self._refused(["--export-annotations", "--output", self.db_path])

    def test_sidecars_are_refused_too(self):
        self._refused(
            ["--export", "--format", "csv", "--output", self.db_path + "-wal"]
        )
        self._refused(
            ["--export", "--format", "csv", "--output", self.db_path + "-journal"]
        )

    def test_exportlt_refuses_the_library_root_and_the_database(self):
        # The directory exporters also sweep and write beside the library
        # files; the root and the db path are both out of bounds.
        lib_root = os.path.dirname(self.db_path)
        self._refused(["--exportlt", "--outdir", lib_root])
        self._refused(["--exportlt", "--outdir", self.db_path])

    def test_normal_outputs_still_write(self):
        out, err = io.StringIO(), io.StringIO()
        export_path = self.db_path + ".report.json"
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(
                [
                    "--export",
                    "--format",
                    "json",
                    "--output",
                    export_path,
                    "--db",
                    self.db_path,
                ]
            )
        self.assertEqual(rc, 0)
        self.assertTrue(os.path.exists(export_path))
        self.assertFalse(os.path.exists(export_path + ".cquarry-tmp"))
        os.unlink(export_path)
        self.assertEqual(self._db_bytes(), self._sentinel)


class TestExportltRowShape(unittest.TestCase):
    """The 2026-09-20 test-gap audit's item 4: --exportlt's happy path
    (the actual CSV row shape) had no coverage; only the self-check
    failure verdict and the output-guard refusals were tested. LibraryThing's
    importer takes a FIXED eleven-column template, so the rows are pinned
    cell-exact: ISBN-10 folds to 13, the sentinel pubdate yields an empty
    year, rating halves to stars, date-read truncates to the date, pages
    <= 0 stay empty, translator credits split into one tag per name, and
    a Read book lands in its own file (read/unread is a property of the
    LT import batch, not of a row)."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db", prefix="cquarry_lt_")
        os.close(fd)
        self.outdir = self.db_path + ".lt"
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        # Direct-storage custom columns (no link table): the read path
        # probes for the link table and falls back to (book, value).
        for cid, label, name in (
            (2, "reading_status", "Reading Status"),
            (3, "translators", "Translators"),
            (4, "date_read", "Date Read"),
        ):
            con.execute(
                "INSERT INTO custom_columns (id,label,name,datatype,is_multiple) "
                "VALUES (?,?,?,?,?)",
                (cid, label, name, "text", 1 if label == "translators" else 0),
            )
            con.execute(
                f"CREATE TABLE custom_column_{cid} "
                "(id INTEGER PRIMARY KEY, book INT, value TEXT)"
            )
        rows = [
            (
                1,
                "Read Classic",
                "Herbert, Frank",
                "1965-08-01",
                8,
                "Ace",
                "9780441172719",
            ),
            (2, "Translator Novel", "Aaa, Translator", "0101-01-01", None, None, None),
            (3, "Negative Pages", "Zzz, Author", "1990-05-01", 10, "Deep Press", None),
        ]
        for bid, title, sort, pubdate, rating, publisher, isbn in rows:
            con.execute(
                "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
                "has_cover,last_modified,series_index,path,uuid) VALUES "
                f"({bid},'{title}','{title}','{sort}','2024-01-01','{pubdate}',0,"
                f"'2024-01-02 00:00:00',1.0,'a/t{bid}','uuid-{bid}')"
            )
            if rating:
                con.execute(
                    "INSERT INTO ratings (id,rating) VALUES (?,?)", (rating, rating)
                )
                con.execute(
                    "INSERT INTO books_ratings_link (book,rating) VALUES (?,?)",
                    (bid, rating),
                )
            if publisher:
                row = con.execute(
                    "SELECT id FROM publishers WHERE name = ?", (publisher,)
                ).fetchone()
                if row:
                    pid = row[0]
                else:
                    cur = con.execute(
                        "INSERT INTO publishers (name) VALUES (?)", (publisher,)
                    )
                    pid = cur.lastrowid
                con.execute(
                    "INSERT INTO books_publishers_link (book,publisher) VALUES (?,?)",
                    (bid, pid),
                )
        con.execute(
            "INSERT INTO identifiers (book,type,val) VALUES (1,'isbn','0441172717')"
        )
        con.execute("INSERT INTO tags (id,name) VALUES (1,'Fic.SciFi')")
        con.execute("INSERT INTO books_tags_link (book,tag) VALUES (1,1)")
        con.execute("INSERT INTO books_pages_link (book,pages) VALUES (1,412)")
        con.execute("INSERT INTO custom_column_2 (book,value) VALUES (1,'Read')")
        con.execute(
            "INSERT INTO custom_column_3 (book,value) VALUES "
            "(2,'Trevor Le Gassick, Salma Khadra Jayyusi')"
        )
        con.execute(
            "INSERT INTO custom_column_4 (book,value) VALUES (1,'2025-06-01T10:00:00')"
        )
        con.commit()
        con.close()

    def tearDown(self):
        os.unlink(self.db_path)
        for stale in os.listdir(self.outdir):
            os.unlink(os.path.join(self.outdir, stale))
        os.rmdir(self.outdir)

    def test_happy_path_rows_match_the_lt_template(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["--exportlt", "--outdir", self.outdir, "--db", self.db_path])
        self.assertEqual(rc, 0, err.getvalue())
        self.assertEqual(
            sorted(os.listdir(self.outdir)),
            ["librarything_main.csv", "librarything_read.csv"],
        )
        with open(
            os.path.join(self.outdir, "librarything_read.csv"),
            encoding="utf-8",
            newline="",
        ) as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(
            rows,
            [
                [
                    "'TITLE'",
                    "'AUTHOR (last, first)'",
                    "'DATE'",
                    "'ISBN'",
                    "'PUBLICATION INFO'",
                    "'TAGS'",
                    "'RATING'",
                    "'REVIEW'",
                    "'DATE READ'",
                    "'PAGE COUNT'",
                    "'CALL NUMBER'",
                ],
                [
                    "Read Classic",
                    "Herbert, Frank",
                    "1965",
                    "9780441172719",  # ISBN-10 0441172717 folded to 13
                    "Ace",
                    "Fic.SciFi",
                    "4",  # stored 8 halves to 4 stars
                    "",  # REVIEW deliberately empty
                    "2025-06-01",  # the datetime truncates to the date
                    "412",
                    "",
                ],
            ],
        )
        with open(
            os.path.join(self.outdir, "librarything_main.csv"),
            encoding="utf-8",
            newline="",
        ) as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(
            rows[1:],  # rows[0] is the same header, asserted above
            [
                [
                    "Translator Novel",
                    "Aaa, Translator",
                    "",  # the 0101 sentinel yields no year
                    "",  # no ISBN stays blank (verbatim import)
                    "",
                    "translator:Trevor Le Gassick, translator:Salma Khadra Jayyusi",
                    "",
                    "",
                    "",
                    "",  # no pages row
                    "",
                ],
                [
                    "Negative Pages",
                    "Zzz, Author",
                    "1990",
                    "",
                    "Deep Press",
                    "",
                    "5",
                    "",
                    "",
                    "",  # -2 pages is junk and stays empty
                    "",
                ],
            ],
        )


if __name__ == "__main__":
    unittest.main()
