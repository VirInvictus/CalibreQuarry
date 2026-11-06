"""The cquarry 1.29.0 adoption seam: Calibre 9.18's date-search
vocabulary (lastmonth, thisyear, lastyear, N monthsago, N yearsago)
through the engine this frontend routes every query through (issue #49,
the #12/#35 floor-adoption precedent).

On the 1.28.3 floor a patron query like `date:lastmonth` or
`pubdate:<2yearsago` died on ParseException while the installed Calibre
9.18 GUI understood the same words; the floor bump is how the vocabulary
reaches the workflow. The version-gated class pins the issue's two
semantic shapes (`date:thisyear` binds by added year,
`pubdate:<2yearsago` bounds at the first of the month N*12 months back)
and the whole five-word parse surface, so a regression cannot come back
through the floor.
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import date
from importlib.metadata import version as _dist_version

from cquarry.db import CalibreDB

from _fixtures import SCHEMA as _SCHEMA


def _cquarry_at_least(major: int, minor: int, patch: int) -> bool:
    try:
        parts = tuple(int(p) for p in _dist_version("cquarry").split(".")[:3])
    except Exception:
        return False
    return parts >= (major, minor, patch)


def _years_back(n: int) -> str:
    today = date.today()
    try:
        return today.replace(year=today.year - n).isoformat()
    except ValueError:  # Feb 29: clamp into the target year
        return today.replace(year=today.year - n, day=28).isoformat()


@unittest.skipUnless(
    _cquarry_at_least(1, 29, 0),
    "needs cquarry >= 1.29.0 (the Calibre 9.18 date-search vocabulary)",
)
class TestDateSearchVocabulary(unittest.TestCase):
    """Two books at fixed relative offsets: the fresh one carries today
    as its added date (and pubdate), the old one sits 3 years back, past
    the 2-yearsago boundary at every calendar position."""

    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.addCleanup(os.unlink, self.db_path)
        con = sqlite3.connect(self.db_path)
        con.executescript(_SCHEMA)
        today = date.today().isoformat()
        old = _years_back(3)
        for bid, title, ts in ((1, "Fresh", today), (2, "Old", old)):
            con.execute(
                "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
                "has_cover,last_modified,series_index,path,uuid) VALUES "
                f"({bid},'{title}','{title}','A, Writer','{ts}','{ts}',0,"
                f"'{ts} 00:00:00',1.0,'a/b','uuid-{bid}')"
            )
            con.execute(
                f"INSERT INTO authors (id,name,sort,link) VALUES "
                f"({bid},'A, Writer','A, Writer','https://example.com/{bid}')"
            )
            con.execute(
                f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
            )
        con.commit()
        con.close()

    def test_thisyear_binds_by_added_year(self):
        with CalibreDB(self.db_path) as db:
            self.assertEqual(set(db.search("date:thisyear")), {1})

    def test_pubdate_yearsago_bounds_at_the_boundary(self):
        with CalibreDB(self.db_path) as db:
            self.assertEqual(set(db.search("pubdate:<2yearsago")), {2})

    def test_the_full_nine_eighteen_vocabulary_parses(self):
        # The lexer's bare word is [^"()\s]+ (upstream _TOKEN_RE), so an
        # unquoted spaced spelling never reaches the date parser
        # ("pubdate:<3 monthsago" is a year-3 filter ANDed with a text
        # match, silently empty, not a date query). The glued forms are
        # what the calibre manual documents; the spaced form works only
        # quoted.
        parse_only = [
            "pubdate:lastmonth",
            "pubdate:thisyear",
            "pubdate:lastyear",
            "timestamp:2yearsago",
        ]
        with CalibreDB(self.db_path) as db:
            for q in parse_only:
                with self.subTest(query=q):
                    self.assertIsInstance(db.search(q), set)
            # The months word pinned semantically at month precision:
            # the 36-month-old book sits inside 13 monthsago's bound
            # (first of the month 13 back) and outside 48's, in both
            # spellings that reach the parser.
            self.assertEqual(set(db.search("pubdate:<13monthsago")), {2})
            self.assertEqual(set(db.search("pubdate:<48monthsago")), set())
            self.assertEqual(set(db.search('pubdate:"<13 monthsago"')), {2})


if __name__ == "__main__":
    unittest.main()
