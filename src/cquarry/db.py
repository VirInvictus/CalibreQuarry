from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, Dict, List, Optional, Set, Tuple


class CalibreDB:
    """Read-only interface to Calibre's metadata.db.

    If the database is locked by Calibre, automatically copies it to a
    temporary file and reads from the copy instead.
    """

    def __init__(self, db_path: str):
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"Database not found: {db_path}")
        self.db_path = db_path
        self._tmp_path: Optional[str] = None
        self._vl_cache: Optional[Dict[str, str]] = None
        self._books_cache: Optional[List[Dict[str, Any]]] = None
        self._all_ids_cache: Optional[Set[int]] = None

        self.conn = self._open(db_path)
        self.conn.row_factory = sqlite3.Row

    def _open(self, db_path: str) -> sqlite3.Connection:
        """Open the database read-only; fall back to a temp copy if locked."""
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            conn.execute("SELECT 1 FROM books LIMIT 1")
            return conn
        except sqlite3.OperationalError as e:
            conn.close()
            if "locked" not in str(e).lower():
                raise
        # Calibre has the DB locked — copy to a temp file and read from there
        print("NOTE: Database is locked (Calibre is running). "
              "Reading from a snapshot copy.", file=sys.stderr)
        fd, tmp = tempfile.mkstemp(suffix=".db", prefix="cquarry_")
        os.close(fd)
        shutil.copy2(db_path, tmp)
        # Also copy the WAL and SHM files if they exist so the snapshot is consistent
        for suffix in ("-wal", "-shm"):
            src = db_path + suffix
            if os.path.exists(src):
                shutil.copy2(src, tmp + suffix)
        self._tmp_path = tmp
        return sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)

    def close(self):
        self.conn.close()
        if self._tmp_path:
            for suffix in ("", "-wal", "-shm"):
                path = self._tmp_path + suffix
                try:
                    os.unlink(path)
                except OSError:
                    pass
            self._tmp_path = None

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
                b.series_index, b.path,
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

    def search(self, query: str) -> Set[int]:
        """Resolve an arbitrary Calibre search expression."""
        return self._eval_vl_expr(query, set())

    def resolve_vl(self, vl_name: str) -> Set[int]:
        """Resolve a virtual library name to a set of book IDs.

        Parses Calibre's VL search expressions, which use:
          tags:Pattern  -- match books with a tag matching Pattern
          vl:Name       -- reference another virtual library
          or / and / not -- boolean combinators
          = prefix      -- exact match (e.g., tags:\"=Fic.Speculative\")
        """
        vls = self.get_virtual_libraries()
        if vl_name not in vls:
            raise ValueError(f"Unknown virtual library: '{vl_name}'. "
                             f"Available: {', '.join(sorted(vls.keys()))}")
        return self._eval_vl_expr(vls[vl_name], set())

    def _eval_vl_expr(self, expr: str, seen: Set[str]) -> Set[int]:
        """Evaluate a Calibre VL search expression."""
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
            try:
                end = expr.index('"', i + 1)
            except ValueError:
                return expr[i + 1:], len(expr)
            return expr[i + 1:end], end + 1
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
                return set()
            vls = self.get_virtual_libraries()
            if vl_name in vls:
                return self._eval_vl_expr(vls[vl_name], seen | {vl_name})
            return set()

        return set()

    def _match_tags(self, pattern: str) -> Set[int]:
        """Match books whose tags match a pattern.

        Supports:
          tags:Foo         -- books with tag 'Foo' or any tag prefixed by 'Foo.'
          tags:\"Foo.Bar\"   -- books with tag 'Foo.Bar' or prefixed by 'Foo.Bar.'
          tags:\"=Foo\"      -- books with exactly the tag 'Foo'

        Note: regex patterns (tags:~regex) are not supported and will match nothing.
        """
        exact = False
        if pattern.startswith('='):
            exact = True
            pattern = pattern[1:]

        pattern = pattern.strip('"')

        cur = self.conn.cursor()
        if exact:
            cur.execute("""
                SELECT DISTINCT btl.book FROM books_tags_link btl
                JOIN tags t ON t.id = btl.tag
                WHERE t.name = ? COLLATE NOCASE
            """, (pattern,))
        else:
            cur.execute("""
                SELECT DISTINCT btl.book FROM books_tags_link btl
                JOIN tags t ON t.id = btl.tag
                WHERE t.name = ? COLLATE NOCASE OR t.name LIKE ?
            """, (pattern, f"{pattern}.%"))

        return {row['book'] for row in cur.fetchall()}
