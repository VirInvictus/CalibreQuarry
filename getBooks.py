#!/usr/bin/env python3
# filepath: getBooks.py
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
  python getBooks.py --catalog --db ~/Calibre/metadata.db
  python getBooks.py --wing "The Tabletop" --db ~/Calibre/metadata.db --primary-only
  python getBooks.py --catalog --db ~/Calibre/metadata.db --show-tags
  python getBooks.py --all-wings --db ~/Calibre/metadata.db --outdir ~/docs/catalogs
  python getBooks.py --stats --db ~/Calibre/metadata.db
  python getBooks.py --audit --db ~/Calibre/metadata.db --output audit.csv
  python getBooks.py --recent 10 --db ~/Calibre/metadata.db
  python getBooks.py --series --db ~/Calibre/metadata.db
  python getBooks.py --export --db ~/Calibre/metadata.db --format json --output library.json
  python getBooks.py --wings --db ~/Calibre/metadata.db

Notes:
  - Reads Calibre's metadata.db directly via sqlite3 (no calibredb needed)
  - Virtual library resolution uses the same tag-based search queries Calibre stores
  - Rating scale: Calibre stores 0-10 internally (2=1 star, 10=5 stars)
  - Run with no arguments for interactive menu
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

# =====================================
# Constants
# =====================================

VERSION = "1.0.3"

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
                GROUP_CONCAT(DISTINCT a.name) as authors,
                GROUP_CONCAT(DISTINCT d.format) as formats,
                GROUP_CONCAT(DISTINCT t.name) as tags,
                s.name as series,
                r.rating,
                p.name as publisher,
                GROUP_CONCAT(DISTINCT l.lang_code) as languages
            FROM books b
            LEFT JOIN books_authors_link bal ON bal.book = b.id
            LEFT JOIN authors a ON a.id = bal.author
            LEFT JOIN data d ON d.book = b.id
            LEFT JOIN books_tags_link btl ON btl.book = b.id
            LEFT JOIN tags t ON t.id = btl.tag
            LEFT JOIN books_series_link bsl ON bsl.book = b.id
            LEFT JOIN series s ON s.id = bsl.series
            LEFT JOIN books_ratings_link brl ON brl.book = b.id
            LEFT JOIN ratings r ON r.id = brl.rating
            LEFT JOIN books_publishers_link bpl ON bpl.book = b.id
            LEFT JOIN publishers p ON p.id = bpl.publisher
            LEFT JOIN books_languages_link bll ON bll.book = b.id
            LEFT JOIN languages l ON l.id = bll.lang_code
            GROUP BY b.id
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
        while tokens and tokens[0] == 'AND':
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
            operand = self._parse_atom(tokens, seen)
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
                WHERE t.name = ?
            """, (pattern,))
        else:
            # Calibre's default: prefix match on hierarchical tags
            # tags:Fic.Fantasy matches Fic.Fantasy, Fic.Fantasy.Epic, etc.
            cur.execute("""
                SELECT DISTINCT btl.book FROM books_tags_link btl
                JOIN tags t ON t.id = btl.tag
                WHERE t.name = ? OR t.name LIKE ?
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


def author_sort_key(author_sort: Optional[str]) -> str:
    return (author_sort or "").lower()


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
            key = author_sort_key(book['author_sort'])

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
        prog="getBooks.py",
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


def _prompt_str(label: str, default: Optional[str]) -> str:
    display = default if default else ""
    try:
        raw = input(f"{label} [{display}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        sys.exit(130)
    return raw or (default or "")


def _prompt_int(label: str, default: int) -> int:
    s = _prompt_str(label, str(default))
    try:
        return int(s)
    except ValueError:
        return default


def interactive_menu() -> int:
    # Find DB first
    db_path = _prompt_str("Path to metadata.db", DEFAULT_DB_PATHS[0])
    try:
        db_path = find_db(db_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    with CalibreDB(db_path) as db:
        while True:
            print(f"\n=== getBooks.py v{VERSION} — Menu ===")
            print("1) Build catalog (full or by wing)")
            print("2) Generate all wing catalogs")
            print("3) Library statistics")
            print("4) Audit (issues report)")
            print("5) Recently added")
            print("6) Series list (with gap detection)")
            print("7) Export (JSON/CSV)")
            print("8) List wings")
            print("q) Quit")
            try:
                choice = input("Select [1-8/q]: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 130

            if choice in ("1", "catalog"):
                wing = _prompt_str("Wing name (blank for all)", "")
                wing = wing if wing else None
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                output = _prompt_str("Output file", "catalog.txt")
                write_catalog(db, output, wing=wing, primary_only=primary,
                              show_tags=tags, show_id=ids)

            elif choice in ("2", "all"):
                outdir = _prompt_str("Output directory", "catalogs")
                primary = _prompt_str("Primary author only? (y/N)", "N").lower().startswith('y')
                tags = _prompt_str("Show tags instead of ratings? (y/N)", "N").lower().startswith('y')
                ids = _prompt_str("Show book IDs? (y/N)", "N").lower().startswith('y')
                write_all_wings(db, outdir, primary_only=primary,
                                show_tags=tags, show_id=ids)

            elif choice in ("3", "stats"):
                show_stats(db)

            elif choice in ("4", "audit"):
                output = _prompt_str("Output CSV", "audit.csv")
                run_audit(db, output)

            elif choice in ("5", "recent"):
                count = _prompt_int("How many", 20)
                show_recent(db, count)

            elif choice in ("6", "series"):
                show_series(db)

            elif choice in ("7", "export"):
                fmt = _prompt_str("Format (json/csv)", "json")
                output = _prompt_str("Output file", f"library.{fmt}")
                run_export(db, output, fmt)

            elif choice in ("8", "wings"):
                show_wings(db)

            elif choice in ("q", "quit", "exit"):
                return 0
            else:
                print("Invalid selection.")

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
    sys.exit(main())
