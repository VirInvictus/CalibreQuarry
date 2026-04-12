#!/usr/bin/env python3
# filepath: cquarry.py
"""
Calibre library toolkit. Reads metadata.db directly — no calibredb dependency.

Modes:
  --catalog       Build a text catalog (all books or a specific wing)
  --wing NAME     Filter to a specific virtual library wing
  --all-wings     Generate separate catalogs for every virtual library
  --stats         Library statistics: format breakdown, rating distribution, tag tree
  --audit         Report issues: untagged, unrated, missing series, series gaps
  --recent N      Show N most recently added books (default: 20)
  --series        List all series with book counts and gap detection
  --export        Export full library to JSON or CSV for external tools
  --wings         List all defined virtual library wings
  --version       Show version and exit

Usage examples:
  python cquarry.py --catalog --db ~/Calibre/metadata.db
  python cquarry.py --wing "The Tabletop" --db ~/Calibre/metadata.db --primary-only
  python cquarry.py --catalog --db ~/Calibre/metadata.db --show-tags
  python cquarry.py --all-wings --db ~/Calibre/metadata.db --outdir ~/docs/catalogs
  python cquarry.py --stats --db ~/Calibre/metadata.db
  python cquarry.py --audit --db ~/Calibre/metadata.db --output audit.csv
  python cquarry.py --recent 10 --db ~/Calibre/metadata.db
  python cquarry.py --series --db ~/Calibre/metadata.db
  python cquarry.py --export --db ~/Calibre/metadata.db --format json --output library.json
  python cquarry.py --wings --db ~/Calibre/metadata.db

Notes:
  - Reads Calibre's metadata.db directly via sqlite3 (no calibredb needed)
  - Virtual library resolution uses the same tag-based search queries Calibre stores
  - Rating scale: Calibre stores 0-10 internally (2=1 star, 10=5 stars)
  - Run with no arguments for interactive menu
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sqlite3
import sys
import subprocess
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

try:
    import curses
    HAVE_CURSES = True
except ImportError:
    HAVE_CURSES = False


# =====================================
# Constants
# =====================================

VERSION = "1.0.4"

DEFAULT_DB_PATHS = [
    "metadata.db",
    os.path.expanduser("~/Calibre Library/metadata.db"),
    os.path.expanduser("~/calibre/metadata.db"),
]

CALIBRE_RATING_SCALE = 2  # Calibre stores rating * 2 (so 5 stars = 10)


# =====================================
# Database layer
# =====================================

class CalibreDB:
    """Read-only interface to Calibre's metadata.db."""

    def __init__(self, db_path: str):
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")
        self.db_path = db_path
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self._vl_cache: Optional[Dict[str, str]] = None
        self._books_cache: Optional[List[Dict[str, Any]]] = None
        self._all_ids_cache: Optional[Set[int]] = None

    def close(self):
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- Core queries ---

    def get_all_books(self) -> List[Dict[str, Any]]:
        """Fetch all books with full metadata via joins. Results are cached."""
        if self._books_cache is not None:
            return self._books_cache
        cur = self.conn.cursor()
        cur.execute("""
            SELECT
                b.id, b.title, b.sort as title_sort, b.author_sort,
                b.timestamp, b.pubdate, b.has_cover, b.last_modified,
                b.series_index,
                (SELECT GROUP_CONCAT(name, ', ') FROM (SELECT a_inner.name as name FROM books_authors_link bal JOIN authors a_inner ON a_inner.id = bal.author WHERE bal.book = b.id ORDER BY bal.id)) as authors,
                (SELECT GROUP_CONCAT(format, ', ') FROM data d WHERE d.book = b.id) as formats,
                (SELECT GROUP_CONCAT(name, ', ') FROM (SELECT t_inner.name as name FROM books_tags_link btl JOIN tags t_inner ON t_inner.id = btl.tag WHERE btl.book = b.id ORDER BY t_inner.name)) as tags,
                s.name as series,
                r.rating,
                p.name as publisher,
                (SELECT GROUP_CONCAT(l.lang_code, ', ') FROM books_languages_link bll JOIN languages l ON l.id = bll.lang_code WHERE bll.book = b.id) as languages
            FROM books b
            LEFT JOIN books_series_link bsl ON bsl.book = b.id
            LEFT JOIN series s ON s.id = bsl.series
            LEFT JOIN books_ratings_link brl ON brl.book = b.id
            LEFT JOIN ratings r ON r.id = brl.rating
            LEFT JOIN books_publishers_link bpl ON bpl.book = b.id
            LEFT JOIN publishers p ON p.id = bpl.publisher
            ORDER BY b.author_sort, b.sort
        """)
        self._books_cache = [dict(row) for row in cur.fetchall()]
        return self._books_cache

    def get_identifiers(self, book_id: int) -> Dict[str, str]:
        cur = self.conn.cursor()
        cur.execute("SELECT type, val FROM identifiers WHERE book = ?", (book_id,))
        return {row['type']: row['val'] for row in cur.fetchall()}

    def get_all_tags(self) -> List[str]:
        cur = self.conn.cursor()
        cur.execute("SELECT DISTINCT name FROM tags ORDER BY name")
        return [row['name'] for row in cur.fetchall()]

    def get_all_series(self) -> List[Dict[str, Any]]:
        cur = self.conn.cursor()
        cur.execute("""
            SELECT s.name,
                   COUNT(b.id) as book_count,
                   GROUP_CONCAT(b.series_index ORDER BY b.series_index) as indices,
                   MAX(b.series_index) as max_index,
                   GROUP_CONCAT(b.title ORDER BY b.series_index) as titles
            FROM books_series_link bsl
            JOIN series s ON s.id = bsl.series
            JOIN books b ON b.id = bsl.book
            GROUP BY s.name
            ORDER BY s.name
        """)
        return [dict(row) for row in cur.fetchall()]

    def get_virtual_libraries(self) -> Dict[str, str]:
        """Return {name: search_expression} from Calibre preferences."""
        if self._vl_cache is not None:
            return self._vl_cache
        cur = self.conn.cursor()
        cur.execute("SELECT val FROM preferences WHERE key = 'virtual_libraries'")
        row = cur.fetchone()
        if row:
            self._vl_cache = json.loads(row['val'])
        else:
            self._vl_cache = {}
        return self._vl_cache

    def count_books(self) -> int:
        if self._all_ids_cache is not None:
            return len(self._all_ids_cache)
        if self._books_cache is not None:
            return len(self._books_cache)
        cur = self.conn.cursor()
        cur.execute("SELECT COUNT(*) as c FROM books")
        return cur.fetchone()['c']

    # --- Virtual library resolution ---

    def resolve_vl(self, vl_name: str) -> Set[int]:
        """Resolve a virtual library name to a set of book IDs.

        Parses Calibre's VL search expressions, which use:
          tags:Pattern  — match books with a tag matching Pattern
          vl:Name       — reference another virtual library
          or / and / not — boolean combinators
          = prefix      — exact match (e.g., tags:\"=Fic.Speculative\")
        """
        vls = self.get_virtual_libraries()
        if vl_name not in vls:
            raise ValueError(f"Unknown virtual library: '{vl_name}'. "
                             f"Available: {', '.join(sorted(vls.keys()))}")
        return self._eval_vl_expr(vls[vl_name], set())

    def _eval_vl_expr(self, expr: str, seen: Set[str]) -> Set[int]:
        """Evaluate a Calibre VL search expression.

        This handles the subset of Calibre's search language actually used
        in virtual library definitions: tags:X, vl:X, or, and, not, parens.
        """
        expr = expr.strip()
        tokens = self._tokenize_vl(expr)
        return self._parse_or(tokens, seen)

    def _tokenize_vl(self, expr: str) -> List[str]:
        """Tokenize a VL expression into atoms and operators."""
        tokens: List[str] = []
        i = 0
        while i < len(expr):
            if expr[i].isspace():
                i += 1
                continue
            if expr[i] == '(':
                tokens.append('(')
                i += 1
            elif expr[i] == ')':
                tokens.append(')')
                i += 1
            elif expr[i:i + 5].lower() == 'tags:':
                # Read tag pattern
                i += 5
                pattern, i = self._read_value(expr, i)
                tokens.append(f"tags:{pattern}")
            elif expr[i:i + 3].lower() == 'vl:':
                i += 3
                name, i = self._read_value(expr, i)
                tokens.append(f"vl:{name}")
            elif expr[i:i + 2].lower() == 'or':
                if i + 2 >= len(expr) or not (expr[i + 2].isalnum() or expr[i + 2] == '_'):
                    tokens.append('OR')
                    i += 2
                else:
                    # Part of a word, skip
                    word, i = self._read_word(expr, i)
                    tokens.append(word)
            elif expr[i:i + 3].lower() == 'and':
                if i + 3 >= len(expr) or not (expr[i + 3].isalnum() or expr[i + 3] == '_'):
                    tokens.append('AND')
                    i += 3
                else:
                    word, i = self._read_word(expr, i)
                    tokens.append(word)
            elif expr[i:i + 3].lower() == 'not':
                if i + 3 >= len(expr) or not (expr[i + 3].isalnum() or expr[i + 3] == '_'):
                    tokens.append('NOT')
                    i += 3
                else:
                    word, i = self._read_word(expr, i)
                    tokens.append(word)
            else:
                word, i = self._read_word(expr, i)
                if word:
                    tokens.append(word)
        return tokens

    @staticmethod
    def _read_value(expr: str, i: int) -> Tuple[str, int]:
        """Read a quoted or unquoted value from position i."""
        if i < len(expr) and expr[i] == '"':
            # Quoted — find closing quote, or treat rest of string as value
            try:
                end = expr.index('"', i + 1)
            except ValueError:
                return expr[i + 1:], len(expr)
            return expr[i + 1:end], end + 1
        # Unquoted — read until space or paren
        start = i
        while i < len(expr) and expr[i] not in ' \t()':
            i += 1
        return expr[start:i], i

    @staticmethod
    def _read_word(expr: str, i: int) -> Tuple[str, int]:
        start = i
        while i < len(expr) and expr[i] not in ' \t()':
            i += 1
        return expr[start:i], i

    def _parse_or(self, tokens: List[str], seen: Set[str]) -> Set[int]:
        result = self._parse_and(tokens, seen)
        while tokens and tokens[0] == 'OR':
            tokens.pop(0)
            right = self._parse_and(tokens, seen)
            result = result | right
        return result

    def _parse_and(self, tokens: List[str], seen: Set[str]) -> Set[int]:
        result = self._parse_not(tokens, seen)
        while tokens and tokens[0] not in ('OR', ')'):
            if tokens[0] == 'AND':
                tokens.pop(0)
            right = self._parse_not(tokens, seen)
            result = result & right
        return result

    def _get_all_book_ids(self) -> Set[int]:
        """Return all book IDs, cached."""
        if self._all_ids_cache is None:
            self._all_ids_cache = {row['id'] for row in
                                   self.conn.execute("SELECT id FROM books").fetchall()}
        return self._all_ids_cache

    def _parse_not(self, tokens: List[str], seen: Set[str]) -> Set[int]:
        if tokens and tokens[0] == 'NOT':
            tokens.pop(0)
            operand = self._parse_not(tokens, seen)
            return self._get_all_book_ids() - operand
        return self._parse_atom(tokens, seen)

    def _parse_atom(self, tokens: List[str], seen: Set[str]) -> Set[int]:
        if not tokens:
            return set()

        token = tokens[0]

        if token == '(':
            tokens.pop(0)
            result = self._parse_or(tokens, seen)
            if tokens and tokens[0] == ')':
                tokens.pop(0)
            return result

        tokens.pop(0)

        if token.startswith('tags:'):
            pattern = token[5:]
            return self._match_tags(pattern)
        elif token.startswith('vl:'):
            vl_name = token[3:]
            if vl_name in seen:
                return set()  # Prevent infinite recursion
            vls = self.get_virtual_libraries()
            if vl_name in vls:
                return self._eval_vl_expr(vls[vl_name], seen | {vl_name})
            return set()

        return set()

    def _match_tags(self, pattern: str) -> Set[int]:
        """Match books whose tags match a pattern.

        Supports:
          tags:Foo         — books with tag 'Foo' or any tag prefixed by 'Foo.'
          tags:\"Foo.Bar\"   — books with tag 'Foo.Bar' or prefixed by 'Foo.Bar.'
          tags:\"=Foo\"      — books with exactly the tag 'Foo'

        Note: regex patterns (tags:~regex) are not supported and will match nothing.
        """
        exact = False
        if pattern.startswith('='):
            exact = True
            pattern = pattern[1:]

        # Remove surrounding quotes if present
        pattern = pattern.strip('"')

        cur = self.conn.cursor()
        if exact:
            cur.execute("""
                SELECT DISTINCT btl.book FROM books_tags_link btl
                JOIN tags t ON t.id = btl.tag
                WHERE t.name = ? COLLATE NOCASE
            """, (pattern,))
        else:
            # Calibre's default: prefix match on hierarchical tags
            # tags:Fic.Fantasy matches Fic.Fantasy, Fic.Fantasy.Epic, etc.
            cur.execute("""
                SELECT DISTINCT btl.book FROM books_tags_link btl
                JOIN tags t ON t.id = btl.tag
                WHERE t.name = ? COLLATE NOCASE OR t.name LIKE ?
            """, (pattern, f"{pattern}.%"))

        return {row['book'] for row in cur.fetchall()}


# =====================================
# Helpers
# =====================================

def calibre_rating_to_stars(rating: Optional[int]) -> Optional[float]:
    """Convert Calibre's internal rating (0-10) to stars (0-5)."""
    if rating is None or rating == 0:
        return None
    return rating / CALIBRE_RATING_SCALE


def format_stars(rating: Optional[float]) -> str:
    if rating is None:
        return ""
    rating = max(0.0, min(5.0, rating))
    full = int(rating)
    half = rating - full >= 0.5
    empty = 5 - full - (1 if half else 0)
    s = "★" * full
    if half:
        s += "☆"
    s += "☆" * empty
    return f" [{s} {rating:.1f}/5]"


def normalize_author_display(authors: Optional[str], primary_only: bool = False) -> str:
    """Format author string for display."""
    if not authors:
        return "Unknown Author"
    parts = [a.strip() for a in authors.split(',')]
    if primary_only:
        return parts[0]
    return " & ".join(parts)


def author_sort_key(author_sort: Optional[str], primary_only: bool = False) -> str:
    key = (author_sort or "").lower()
    if primary_only:
        key = key.split('&')[0].strip()
    return key


def detect_series_gaps(indices_str: str, max_index: Optional[float]) -> List[int]:
    """Detect missing entries in a series based on index numbers."""
    if not indices_str or max_index is None:
        return []
    indices = set()
    for s in indices_str.split(','):
        try:
            idx = float(s)
            if idx == int(idx):
                indices.add(int(idx))
        except ValueError:
            continue
    expected = set(range(1, int(max_index) + 1))
    return sorted(expected - indices)


def find_db(explicit: Optional[str]) -> str:
    """Locate metadata.db — explicit path or search defaults."""
    if explicit:
        path = os.path.expanduser(explicit)
        if os.path.isdir(path):
            path = os.path.join(path, "metadata.db")
        if os.path.exists(path):
            return path
        raise FileNotFoundError(f"Database not found: {path}")

    for p in DEFAULT_DB_PATHS:
        if os.path.exists(p):
            return p

    raise FileNotFoundError(
        "Could not find metadata.db. Specify with --db /path/to/metadata.db"
    )


# =====================================
# Mode: Catalog
# =====================================

def write_catalog(db: CalibreDB, output: str, *,
                  wing: Optional[str] = None, primary_only: bool = False,
                  show_tags: bool = False, show_id: bool = False,
                  quiet: bool = False) -> None:
    """Write a formatted text catalog, optionally filtered to a virtual library."""
    books = db.get_all_books()

    if wing:
        try:
            valid_ids = db.resolve_vl(wing)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return
        books = [b for b in books if b['id'] in valid_ids]
        if not quiet:
            print(f"Wing '{wing}': {len(books)} books")

    if not books:
        if not quiet:
            print("No books found.")
        return

    # Sort books by the derived author_sort_key to ensure contiguous groups
    books.sort(key=lambda b: (author_sort_key(b['author_sort'], primary_only), b['title_sort'] or ''))

    with open(output, 'w', encoding='utf-8') as f:
        header = f"Calibre Library Export — {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        if wing:
            header += f" [{wing}]"
        f.write(header + "\n")
        f.write("=" * len(header) + "\n\n")

        current_author_key = None
        book_count = 0

        for book in books:
            author_display = normalize_author_display(book['authors'], primary_only)
            key = author_sort_key(book['author_sort'], primary_only)

            if key != current_author_key:
                if current_author_key is not None:
                    f.write("\n")
                f.write(f"[{author_display}]\n")
                f.write("-" * (len(author_display) + 2) + "\n")
                current_author_key = key

            title = book['title'] or 'Unknown Title'

            if show_tags:
                # Show tags instead of rating
                tag_list = [t.strip() for t in (book['tags'] or '').split(',') if t.strip()]
                meta_str = f" [{', '.join(tag_list)}]" if tag_list else ""
            else:
                rating = calibre_rating_to_stars(book['rating'])
                meta_str = format_stars(rating)

            series_str = ""
            if book['series']:
                idx = book['series_index']
                if idx is not None and idx == int(idx):
                    series_str = f" ({book['series']} #{int(idx)})"
                elif idx is not None:
                    series_str = f" ({book['series']} #{idx})"
                else:
                    series_str = f" ({book['series']})"

            formats = book['formats'] or ''
            fmt_str = f" [{formats}]" if formats else ""

            id_str = f"[{book['id']}] " if show_id else ""
            f.write(f"  * {id_str}{title}{series_str}{fmt_str}{meta_str}\n")
            book_count += 1

        f.write(f"\n{'=' * 40}\n")
        f.write(f"Total: {book_count} books\n")

    if not quiet:
        print(f"Catalog written: {output} ({book_count} books)")


def write_all_wings(db: CalibreDB, outdir: str, *, primary_only: bool = False,
                    show_tags: bool = False, show_id: bool = False,
                    quiet: bool = False) -> None:
    """Generate a catalog file for each virtual library wing."""
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.", file=sys.stderr)
        return

    os.makedirs(outdir, exist_ok=True)

    for name in sorted(vls.keys()):
        safe_name = re.sub(r'[^\w\s-]', '', name).strip().replace(' ', '_')
        output = os.path.join(outdir, f"{safe_name}_Library.txt")
        if not quiet:
            print(f"→ {name}")
        write_catalog(db, output, wing=name, primary_only=primary_only,
                      show_tags=show_tags, show_id=show_id, quiet=True)

    if not quiet:
        print(f"\nAll wings written to: {outdir}")


# =====================================
# Mode: Stats
# =====================================

def show_stats(db: CalibreDB, *, quiet: bool = False) -> None:
    """Display library statistics."""
    books = db.get_all_books()
    total = len(books)

    if not quiet:
        print(f"=== Library Statistics ({total} books) ===\n")

    # Format breakdown
    format_counts: Counter = Counter()
    for b in books:
        if b['formats']:
            for fmt in b['formats'].split(','):
                format_counts[fmt.strip()] += 1
    print("Formats:")
    for fmt, count in format_counts.most_common():
        bar = "█" * (count * 40 // total) if total else ""
        print(f"  {fmt:6s} {count:5d}  {bar}")

    # Rating distribution
    print("\nRatings:")
    rated = 0
    rating_counts: Counter = Counter()
    for b in books:
        stars = calibre_rating_to_stars(b['rating'])
        if stars is not None:
            rating_counts[stars] += 1
            rated += 1
    for stars in sorted(rating_counts.keys()):
        count = rating_counts[stars]
        max_count = max(rating_counts.values())
        bar = "█" * (count * 40 // max_count) if max_count else ""
        print(f"  {'★' * int(stars):5s} ({stars:.1f})  {count:5d}  {bar}")
    unrated = total - rated
    pct = f"{unrated * 100 / total:.1f}%" if total else "N/A"
    print(f"  Unrated:       {unrated:5d}  ({pct})")

    # Tag taxonomy
    tags = db.get_all_tags()
    top_level: Counter = Counter()
    for t in tags:
        prefix = t.split('.')[0] if '.' in t else t
        top_level[prefix] += 1
    print(f"\nTag taxonomy ({len(tags)} tags):")
    for prefix, count in top_level.most_common():
        print(f"  {prefix}: {count} tags")

    # Series
    all_series = db.get_all_series()
    print(f"\nSeries: {len(all_series)} series tracked")
    print("Largest:")
    for s in sorted(all_series, key=lambda x: x['book_count'], reverse=True)[:10]:
        print(f"  {s['name']}: {s['book_count']} books")

    # Publisher
    pub_counts: Counter = Counter()
    for b in books:
        if b['publisher']:
            pub_counts[b['publisher']] += 1
    print(f"\nPublishers: {len(pub_counts)} distinct")
    print("Most represented:")
    for pub, count in pub_counts.most_common(10):
        print(f"  {pub}: {count}")

    # Language
    lang_counts: Counter = Counter()
    for b in books:
        if b['languages']:
            for lang in b['languages'].split(','):
                lang_counts[lang.strip()] += 1
    if len(lang_counts) > 1:
        print("\nLanguages:")
        for lang, count in lang_counts.most_common():
            print(f"  {lang}: {count}")

    # Cover status
    no_cover = sum(1 for b in books if not b['has_cover'])
    if no_cover:
        print(f"\nMissing covers: {no_cover}")

    # Recently added
    if not quiet:
        print("\nMost recently added:")
        by_date = sorted(books, key=lambda b: b['timestamp'] or '', reverse=True)
        for b in by_date[:5]:
            date = (b['timestamp'] or '')[:10]
            author = normalize_author_display(b['authors'], primary_only=True)
            print(f"  [{date}] {author} — {b['title']}")


# =====================================
# Mode: Audit
# =====================================

def run_audit(db: CalibreDB, output: str, *, quiet: bool = False) -> None:
    """Report library issues to CSV."""
    books = db.get_all_books()
    all_series = db.get_all_series()
    issues: List[Dict[str, str]] = []

    # Per-book issues
    for b in books:
        problems: List[str] = []

        if not b['tags']:
            problems.append("no_tags")
        if b['rating'] is None or b['rating'] == 0:
            problems.append("unrated")
        if not b['authors'] or b['authors'] == 'Unknown':
            problems.append("no_author")
        if not b['formats']:
            problems.append("no_file")
        if not b['has_cover']:
            problems.append("no_cover")

        if problems:
            issues.append({
                "id": str(b['id']),
                "title": b['title'] or '',
                "author": b['author_sort'] or '',
                "issue_type": "book",
                "issues": ", ".join(problems),
            })

    # Series gap detection
    for s in all_series:
        gaps = detect_series_gaps(s['indices'], s['max_index'])
        if gaps:
            issues.append({
                "id": "",
                "title": s['name'],
                "author": "",
                "issue_type": "series_gap",
                "issues": f"missing indices: {', '.join(str(g) for g in gaps)}",
            })

    # Write CSV
    fieldnames = ["id", "title", "author", "issue_type", "issues"]
    out_path = os.path.abspath(output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in issues:
            w.writerow(row)

    if not quiet:
        # Summary
        book_issues = [i for i in issues if i['issue_type'] == 'book']
        series_issues = [i for i in issues if i['issue_type'] == 'series_gap']

        issue_counts: Counter = Counter()
        for i in book_issues:
            for problem in i['issues'].split(', '):
                issue_counts[problem] += 1

        print(f"Audited {len(books)} books, {len(all_series)} series.")
        print(f"Found {len(issues)} issues total.\n")

        if issue_counts:
            print("Book issues:")
            for problem, count in issue_counts.most_common():
                print(f"  {problem}: {count}")

        if series_issues:
            print(f"\nSeries with gaps: {len(series_issues)}")
            for i in series_issues[:10]:
                print(f"  {i['title']}: {i['issues']}")

        print(f"\nFull report: {out_path}")


# =====================================
# Mode: Recent
# =====================================

def show_recent(db: CalibreDB, count: int = 20, *, quiet: bool = False) -> None:
    """Show most recently added books."""
    books = db.get_all_books()
    by_date = sorted(books, key=lambda b: b['timestamp'] or '', reverse=True)

    if not quiet:
        print(f"=== {count} Most Recently Added ===\n")
    for b in by_date[:count]:
        date = (b['timestamp'] or '')[:10]
        author = normalize_author_display(b['authors'], primary_only=True)
        rating = calibre_rating_to_stars(b['rating'])
        rating_str = format_stars(rating)
        tags = b['tags'] or ''
        tag_str = f" ({tags.split(',')[0].strip()})" if tags else ""
        series_str = ""
        if b['series']:
            idx = b['series_index']
            if idx is not None and idx == int(idx):
                series_str = f" [{b['series']} #{int(idx)}]"
            elif idx is not None:
                series_str = f" [{b['series']} #{idx}]"
            else:
                series_str = f" [{b['series']}]"

        print(f"  [{date}] {author} — {b['title']}{series_str}{tag_str}{rating_str}")


# =====================================
# Mode: Series
# =====================================

def show_series(db: CalibreDB, *, quiet: bool = False) -> None:
    """List all series with gap detection."""
    all_series = db.get_all_series()

    if not quiet:
        print(f"=== Series ({len(all_series)} total) ===\n")

    for s in sorted(all_series, key=lambda x: x['name'].lower()):
        gaps = detect_series_gaps(s['indices'], s['max_index'])
        gap_str = f"  ⚠ missing: {', '.join(str(g) for g in gaps)}" if gaps else ""
        count = s['book_count']
        raw_max = s['max_index']
        if raw_max is not None and raw_max == int(raw_max):
            max_idx = int(raw_max)
        else:
            max_idx = raw_max

        # Show completeness
        if gaps:
            status = "incomplete"
        elif raw_max is not None and count == int(raw_max):
            status = "complete"
        else:
            status = ""

        status_str = f" ({status})" if status else ""
        print(f"  {s['name']}: {count} of {max_idx}{status_str}{gap_str}")


# =====================================
# Mode: Export
# =====================================

def run_export(db: CalibreDB, output: str, fmt: str = "json", *,
               quiet: bool = False) -> None:
    """Export full library to JSON or CSV."""
    books = db.get_all_books()
    out_path = os.path.abspath(output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    if fmt == "json":
        export_data = []
        for b in books:
            stars = calibre_rating_to_stars(b['rating'])
            export_data.append({
                "id": b['id'],
                "title": b['title'],
                "authors": [a.strip() for a in b['authors'].split(',')] if b['authors'] else [],
                "author_sort": b['author_sort'],
                "tags": [t.strip() for t in b['tags'].split(',')] if b['tags'] else [],
                "series": b['series'],
                "series_index": b['series_index'],
                "formats": [f.strip() for f in b['formats'].split(',')] if b['formats'] else [],
                "rating": stars,
                "publisher": b['publisher'],
                "languages": [l.strip() for l in b['languages'].split(',')] if b['languages'] else [],
                "added": (b['timestamp'] or '')[:10],
                "has_cover": bool(b['has_cover']),
            })
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)

    elif fmt == "csv":
        fieldnames = [
            "id", "title", "authors", "author_sort", "tags", "series",
            "series_index", "formats", "rating", "publisher", "languages",
            "added", "has_cover"
        ]
        with open(out_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for b in books:
                stars = calibre_rating_to_stars(b['rating'])
                w.writerow({
                    "id": b['id'],
                    "title": b['title'],
                    "authors": b['authors'] or '',
                    "author_sort": b['author_sort'] or '',
                    "tags": b['tags'] or '',
                    "series": b['series'] or '',
                    "series_index": b['series_index'] if b['series_index'] is not None else '',
                    "formats": b['formats'] or '',
                    "rating": stars if stars is not None else '',
                    "publisher": b['publisher'] or '',
                    "languages": b['languages'] or '',
                    "added": (b['timestamp'] or '')[:10],
                    "has_cover": b['has_cover'],
                })
    else:
        print(f"Unknown format: {fmt}. Use 'json' or 'csv'.", file=sys.stderr)
        return

    if not quiet:
        print(f"Exported {len(books)} books to: {out_path}")


# =====================================
# Mode: List wings
# =====================================

def show_wings(db: CalibreDB) -> None:
    """List all virtual library wings with book counts."""
    vls = db.get_virtual_libraries()
    if not vls:
        print("No virtual libraries defined.")
        return

    print(f"=== Virtual Libraries ({len(vls)} wings) ===\n")
    for name in sorted(vls.keys()):
        try:
            ids = db.resolve_vl(name)
            print(f"  {name}: {len(ids)} books")
        except ValueError as e:
            print(f"  {name}: (error resolving: {e})")

    print(f"\n  Total library: {db.count_books()} books")


# =====================================
# CLI wiring
# =====================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cquarry.py",
        description="Calibre library toolkit: catalog, stats, audit, export"
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")

    group = p.add_mutually_exclusive_group()
    group.add_argument("--catalog", action="store_true",
                       help="Build a text catalog")
    group.add_argument("--all-wings", dest="all_wings", action="store_true",
                       help="Generate catalogs for all virtual libraries")
    group.add_argument("--stats", action="store_true",
                       help="Show library statistics")
    group.add_argument("--audit", action="store_true",
                       help="Report issues (untagged, unrated, series gaps)")
    group.add_argument("--recent", type=int, nargs='?', const=20, default=None,
                       help="Show N most recently added books (default: 20)")
    group.add_argument("--series", action="store_true",
                       help="List all series with completeness and gap detection")
    group.add_argument("--export", action="store_true",
                       help="Export library to JSON or CSV")
    group.add_argument("--wings", action="store_true",
                       help="List all virtual library wings")

    p.add_argument("--db", default=None,
                   help="Path to Calibre metadata.db (auto-detected if omitted)")
    p.add_argument("--wing", default=None,
                   help="Filter to a specific virtual library wing")
    p.add_argument("--output", default=None, help="Output file path")
    p.add_argument("--outdir", default=None,
                   help="Output directory for --all-wings (default: current dir)")
    p.add_argument("--format", choices=["json", "csv"], default="json",
                   help="Export format (default: json)")
    p.add_argument("--primary-only", dest="primary_only", action="store_true",
                   help="Use only the first author (useful for TTRPG collections)")
    p.add_argument("--show-tags", dest="show_tags", action="store_true",
                   help="Show tags instead of ratings in catalog output")
    p.add_argument("--show-id", dest="show_id", action="store_true",
                   help="Prefix each book with its Calibre ID for scripting")
    p.add_argument("--quiet", action="store_true", help="Minimize output")

    return p



def _reset_terminal() -> None:
    if not sys.stdin.isatty():
        return
    try:
        subprocess.run(["stty", "sane"], stdin=sys.stdin, check=False)
    except Exception:
        pass

# =====================================
# Curses TUI
# =====================================

_CP_FRAME = 1
_CP_TITLE = 2
_CP_HEADER = 3
_CP_ITEM = 4
_CP_SELECTED = 5
_CP_HINT = 6

def _init_tui_colors() -> None:
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_FRAME, curses.COLOR_CYAN, -1)
    curses.init_pair(_CP_TITLE, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_HEADER, curses.COLOR_YELLOW, -1)
    curses.init_pair(_CP_ITEM, curses.COLOR_WHITE, -1)
    curses.init_pair(_CP_SELECTED, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_HINT, curses.COLOR_WHITE, -1)

_TUI_BOX_W = 46
_TUI_INNER = _TUI_BOX_W - 2

def _safe_addstr(stdscr, y: int, x: int, text: str, attr: int) -> None:
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass

def _tui_select(title: str, sections: list,
                hints: str = "\u2191\u2193 Navigate  \u23ce Select  q Quit") -> Optional[tuple]:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    flat: list[tuple[int, int]] = []
    for si, (_, items) in enumerate(sections):
        for ii in range(len(items)):
            flat.append((si, ii))

    def _draw(stdscr, cur: int) -> None:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        bx = max(0, (w - BOX_W) // 2)
        fa = curses.color_pair(_CP_FRAME)

        box_h = 3
        for si, (hdr, items) in enumerate(sections):
            if si > 0: box_h += 1
            if hdr: box_h += 1
            box_h += len(items)
        box_h += 1

        y = max(0, (h - box_h - 2) // 2)

        _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
        y += 1
        _safe_addstr(stdscr, y, bx, "\u2551", fa)
        _safe_addstr(stdscr, y, bx + 1, f" {title:^{INNER - 2}} ", curses.color_pair(_CP_TITLE) | curses.A_BOLD)
        _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
        y += 1
        _safe_addstr(stdscr, y, bx, "\u2560" + "\u2550" * INNER + "\u2563", fa)
        y += 1

        idx = 0
        for si, (hdr, items) in enumerate(sections):
            if si > 0:
                _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
                y += 1
            if hdr:
                content = f"  {hdr}" + " " * (INNER - len(hdr) - 2)
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, content, curses.color_pair(_CP_HEADER) | curses.A_BOLD)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
            for ii, label in enumerate(items):
                is_sel = idx == cur
                if is_sel:
                    text = f" \u25ba {label}"
                    attr = curses.color_pair(_CP_SELECTED) | curses.A_BOLD
                else:
                    text = f"   {label}"
                    attr = curses.color_pair(_CP_ITEM)
                padded = text + " " * max(0, INNER - len(text))
                _safe_addstr(stdscr, y, bx, "\u2551", fa)
                _safe_addstr(stdscr, y, bx + 1, padded[:INNER], attr)
                _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
                y += 1
                idx += 1

        _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
        y += 2
        hx = max(0, (w - len(hints)) // 2)
        _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)
        stdscr.refresh()

    def _run(stdscr) -> Optional[tuple]:
        _init_tui_colors()
        curses.curs_set(0)
        cur = 0
        while True:
            _draw(stdscr, cur)
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')): cur = (cur - 1) % len(flat)
            elif key in (curses.KEY_DOWN, ord('j')): cur = (cur + 1) % len(flat)
            elif key in (curses.KEY_ENTER, 10, 13): return flat[cur]
            elif key in (ord('q'), ord('Q'), 27): return None
            elif key == curses.KEY_RESIZE: pass

    try:
        return curses.wrapper(_run)
    except curses.error:
        return None

def _tui_prompt_str(label: str, default: Optional[str]) -> str:
    BOX_W = _TUI_BOX_W
    INNER = _TUI_INNER

    def _run(stdscr) -> str:
        _init_tui_colors()
        curses.curs_set(1)
        buf = list(default or "")
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            bx = max(0, (w - BOX_W) // 2)
            fa = curses.color_pair(_CP_FRAME)
            y = max(0, (h - 8) // 2)

            _safe_addstr(stdscr, y, bx, "\u2554" + "\u2550" * INNER + "\u2557", fa)
            y += 1
            lbl = f"  {label}"
            padded_lbl = lbl + " " * max(0, INNER - len(lbl))
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, padded_lbl[:INNER], curses.color_pair(_CP_HEADER) | curses.A_BOLD)
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255f" + "\u2500" * INNER + "\u2562", fa)
            y += 1
            display = "".join(buf)
            max_input = INNER - 4
            visible = "\u2026" + display[-(max_input - 1):] if len(display) > max_input else display
            input_text = f" > {visible}" + " " * max(0, INNER - len(visible) - 3)
            _safe_addstr(stdscr, y, bx, "\u2551", fa)
            _safe_addstr(stdscr, y, bx + 1, input_text[:INNER], curses.color_pair(_CP_ITEM))
            _safe_addstr(stdscr, y, bx + BOX_W - 1, "\u2551", fa)
            input_y = y
            y += 1
            _safe_addstr(stdscr, y, bx, "\u255a" + "\u2550" * INNER + "\u255d", fa)
            y += 2
            hints = "\u23ce Confirm  Esc Default"
            hx = max(0, (w - len(hints)) // 2)
            _safe_addstr(stdscr, y, hx, hints, curses.color_pair(_CP_HINT) | curses.A_DIM)

            cursor_x = bx + 4 + min(len(display), max_input)
            try:
                stdscr.move(input_y, min(cursor_x, bx + BOX_W - 2))
            except curses.error:
                pass
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_ENTER, 10, 13):
                result = "".join(buf).strip()
                return result if result else (default or "")
            elif key == 27:
                return default or ""
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                if buf: buf.pop()
            elif key == curses.KEY_RESIZE: pass
            elif 32 <= key <= 126: buf.append(chr(key))

    try:
        return curses.wrapper(_run)
    except curses.error:
        try:
            raw = input(f"  {label} [{default}]: ").strip()
        except (EOFError, KeyboardInterrupt):
            sys.exit(130)
        return raw or (default or "")

def _box_menu(title: str, sections: list, width: int = 44) -> None:
    iw = width - 4
    print(f"\n  \u2554{'\\u2550' * (width - 2)}\u2557")
    print(f"  \u2551 {title:^{iw}} \u2551")
    print(f"  \u2560{'\\u2550' * (width - 2)}\u2563")
    first = True
    for header, items in sections:
        if not first:
            print(f"  \u255f{'\\u2500' * (width - 2)}\u2562")
        first = False
        if header:
            print(f"  \u2551  {header:<{iw - 1}} \u2551")
        for item in items:
            print(f"  \u2551    {item:<{iw - 3}} \u2551")
    print(f"  \u255a{'\\u2550' * (width - 2)}\u255d")

def _pause() -> None:
    try:
        input("\n  Press Enter to continue...")
    except (EOFError, KeyboardInterrupt):
        pass

def _fallback_input(prompt: str, mapping: dict) -> Any:
    try:
        ch = input(prompt).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return None
    return mapping.get(ch, "invalid")

_USE_CURSES = HAVE_CURSES and sys.stdin.isatty()


def _tui_scroll_text(title: str, text: str) -> None:
    lines = text.expandtabs(4).splitlines()
    def _run(stdscr):
        _init_tui_colors()
        curses.curs_set(0)
        top = 0
        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            
            # Content width logic
            max_line_len = max((len(l) for l in lines), default=0)
            content_w = min(w, max(_TUI_BOX_W, max_line_len + 4))
            bx = max(0, (w - content_w) // 2)
            
            fa = curses.color_pair(_CP_FRAME)
            
            _safe_addstr(stdscr, 0, bx, "\u2554" + "\u2550" * (content_w - 2) + "\u2557", fa)
            _safe_addstr(stdscr, 0, bx + 2, f" {title} ", curses.color_pair(_CP_TITLE) | curses.A_BOLD)
            _safe_addstr(stdscr, h - 2, bx, "\u255a" + "\u2550" * (content_w - 2) + "\u255d", fa)
            
            hints = "\u2191\u2193 Scroll  PgUp/Dn  q/Esc Close"
            _safe_addstr(stdscr, h - 1, max(0, (w - len(hints)) // 2), hints, curses.color_pair(_CP_HINT) | curses.A_DIM)
            
            max_lines = max(1, h - 3)
            for i in range(max_lines):
                if top + i < len(lines):
                    line = lines[top + i][:content_w - 4]
                    _safe_addstr(stdscr, i + 1, bx, "\u2551", fa)
                    _safe_addstr(stdscr, i + 1, bx + 2, line, curses.color_pair(_CP_ITEM))
                    _safe_addstr(stdscr, i + 1, bx + content_w - 1, "\u2551", fa)
                else:
                    _safe_addstr(stdscr, i + 1, bx, "\u2551", fa)
                    _safe_addstr(stdscr, i + 1, bx + content_w - 1, "\u2551", fa)
            stdscr.refresh()
            
            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')): top = max(0, top - 1)
            elif key in (curses.KEY_DOWN, ord('j')): top = min(max(0, len(lines) - max_lines), top + 1)
            elif key in (curses.KEY_PPAGE,): top = max(0, top - max_lines)
            elif key in (curses.KEY_NPAGE,): top = min(max(0, len(lines) - max_lines), top + max_lines)
            elif key in (curses.KEY_HOME, ord('g')): top = 0
            elif key in (curses.KEY_END, ord('G')): top = max(0, len(lines) - max_lines)
            elif key in (ord('q'), ord('Q'), 27, curses.KEY_ENTER, 10, 13): return
            elif key == curses.KEY_RESIZE: pass

    try:
        curses.wrapper(_run)
    except curses.error:
        pass

def _show_in_pager(title: str, func) -> None:
    old_stdout = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        func()
    finally:
        sys.stdout = old_stdout
    
    text = buf.getvalue().rstrip()
    if not text:
        return
        
    if _USE_CURSES:
        _tui_scroll_text(title, text)
    else:
        print(text)
        _pause()

def _prompt_str(label: str, default: Optional[str]) -> str:
    if _USE_CURSES:
        return _tui_prompt_str(label, default)
    display = default if default else ""
    try:
        raw = input(f"  {label} [{display}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)
    return raw or (default or "")



def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default



_MAIN_SECTIONS = [
    ("OUTPUT", [
        "Build catalog (full or by wing)",
        "Generate all wing catalogs",
        "Library statistics",
        "Audit (issues report)",
    ]),
    ("LISTS", [
        "Recently added",
        "Series list (with gap detection)",
        "List wings",
    ]),
    ("EXPORT", [
        "Export (JSON/CSV)",
    ]),
    ("", ["Quit"]),
]

_MAIN_FALLBACK_MAP = {
    "1": (0, 0), "catalog": (0, 0),
    "2": (0, 1), "all": (0, 1),
    "3": (0, 2), "stats": (0, 2),
    "4": (0, 3), "audit": (0, 3),
    "5": (1, 0), "recent": (1, 0),
    "6": (1, 1), "series": (1, 1),
    "7": (1, 2), "wings": (1, 2),
    "8": (2, 0), "export": (2, 0),
    "q": None, "quit": None, "exit": None,
}

def _select_main() -> Optional[tuple]:
    if _USE_CURSES:
        return _tui_select(f"CalibreQuarry v{VERSION}", _MAIN_SECTIONS)
    _box_menu(f"CalibreQuarry v{VERSION}", [
        ("OUTPUT", ["1) Build catalog", "2) Generate all wings", "3) Statistics", "4) Audit"]),
        ("LISTS", ["5) Recently added", "6) Series list", "7) List wings"]),
        ("EXPORT", ["8) Export (JSON/CSV)"]),
        ("", ["q) Quit"]),
    ])
    return _fallback_input("  Select [1-8/q]: ", _MAIN_FALLBACK_MAP)

def interactive_menu() -> int:
    db_path = _prompt_str("Path to metadata.db", DEFAULT_DB_PATHS[0])
    try:
        db_path = find_db(db_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    with CalibreDB(db_path) as db:
        while True:
            _reset_terminal()
            result = _select_main()

            if result == "invalid":
                if not _USE_CURSES:
                    print("  Invalid selection.")
                continue

            if result is None or result == (3, 0):  # Quit
                return 0

            if result == (0, 0):
                wing = _prompt_str("Wing name (blank for all)", "")
                wing = wing if wing else None
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                output = _prompt_str("Output file", "catalog.txt")
                _reset_terminal()
                _show_in_pager("Catalog", lambda: write_catalog(db, output, wing=wing, primary_only=primary,
                              show_tags=tags, show_id=ids))

            elif result == (0, 1):
                outdir = _prompt_str("Output directory", "catalogs")
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                _reset_terminal()
                _show_in_pager("Generate Wings", lambda: write_all_wings(db, outdir, primary_only=primary,
                                show_tags=tags, show_id=ids))

            elif result == (0, 2):
                _reset_terminal()
                _show_in_pager("Statistics", lambda: show_stats(db))

            elif result == (0, 3):
                output = _prompt_str("Output CSV", "audit.csv")
                _reset_terminal()
                _show_in_pager("Audit", lambda: run_audit(db, output))

            elif result == (1, 0):
                count = _prompt_int("How many", 20)
                _reset_terminal()
                _show_in_pager("Recently Added", lambda: show_recent(db, count))

            elif result == (1, 1):
                _reset_terminal()
                _show_in_pager("Series List", lambda: show_series(db))

            elif result == (1, 2):
                _reset_terminal()
                _show_in_pager("Virtual Libraries", lambda: show_wings(db))

            elif result == (2, 0):
                fmt = _prompt_str("Format (json/csv)", "json")
                output = _prompt_str("Output file", f"library.{fmt}")
                _reset_terminal()
                _show_in_pager("Export", lambda: run_export(db, output, fmt))

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) == 0:
        return interactive_menu()

    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        db_path = find_db(args.db)

        with CalibreDB(db_path) as db:
            if args.catalog:
                output = args.output or "catalog.txt"
                write_catalog(db, output, wing=args.wing,
                              primary_only=args.primary_only,
                              show_tags=args.show_tags, show_id=args.show_id,
                              quiet=args.quiet)
                return 0

            if args.all_wings:
                outdir = args.outdir or "catalogs"
                write_all_wings(db, outdir, primary_only=args.primary_only,
                                show_tags=args.show_tags, show_id=args.show_id,
                                quiet=args.quiet)
                return 0

            if args.stats:
                show_stats(db, quiet=args.quiet)
                return 0

            if args.audit:
                output = args.output or "audit.csv"
                run_audit(db, output, quiet=args.quiet)
                return 0

            if args.recent is not None:
                show_recent(db, args.recent, quiet=args.quiet)
                return 0

            if args.series:
                show_series(db, quiet=args.quiet)
                return 0

            if args.export:
                fmt = args.format or "json"
                output = args.output or f"library.{fmt}"
                run_export(db, output, fmt, quiet=args.quiet)
                return 0

            if args.wings:
                show_wings(db)
                return 0

            # If --wing was given without a mode, default to catalog
            if args.wing:
                output = args.output or "catalog.txt"
                write_catalog(db, output, wing=args.wing,
                              primary_only=args.primary_only,
                              show_tags=args.show_tags, show_id=args.show_id,
                              quiet=args.quiet)
                return 0

            parser.print_help()
            return 2

    except (FileNotFoundError, PermissionError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        try:
            _reset_terminal()
        except NameError:
            pass

