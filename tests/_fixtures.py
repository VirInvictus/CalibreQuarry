"""One shared fixture module for the suite: the canonical Calibre-shaped
schema every temp-library test builds, plus a small builder over it.

Before 3.61.0 every test file carried its own ``_SCHEMA`` DDL copy (19 of
them by the 2026-10-07 count), drifted in both directions: the write-path
shapes (the id + UNIQUE(book, type) identifiers table cquarry's set_identifier
documents, the id-bearing comments table, the preferences UNIQUE(key) the
3.53 fixture trap pins) were missing from copies that later grew tests
needing them, and one copy carried ``custom_column_1 (book, value)``, a
shape real Calibre never had. This file is the ONE copy. Test modules
import it (``from _fixtures import SCHEMA, build_library``) and append
only their genuinely local extras (the FTS sidecar, schema-verb shapes)
after the import. ``tests/`` is on sys.path under unittest discover and
under direct file runs, so the plain import works both ways.

Constraint choices: UNIQUE carries only what the code under test relies
on or a recorded fixture trap pins (link-table pairs, identifiers
(book, type), comments (book), preferences (key), metadata_dirtied
(book), custom_columns.label). Entity tables stay loose on purpose:
authors/tags/ratings name uniqueness is Calibre's code-level job
(cquarry's _resolve_or_create), and several fixtures legitimately seed
repeated display rows.

The custom-column value/link tables (custom_column_N,
books_custom_column_N_link) are deliberately NOT here: their ids and
labels are fixture-specific (audience in one file, read in another,
source/audience enumerations in a third), and shipping an unregistered
value table would be a state no real library is in -- cquarry's
--add-custom-column allocates the next id and collides with it. A file
that needs value tables appends them after the import, beside the
registry rows it seeds.
"""

import sqlite3

SCHEMA = """
CREATE TABLE books (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, sort TEXT,
    author_sort TEXT, timestamp TEXT, pubdate TEXT, has_cover INT,
    last_modified TEXT, series_index REAL DEFAULT 1.0, path TEXT, uuid TEXT);
CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INT, author INT,
    UNIQUE (book, author));
CREATE TABLE tags (id INTEGER PRIMARY KEY, name TEXT, link TEXT);
CREATE TABLE books_tags_link (id INTEGER PRIMARY KEY, book INT, tag INT,
    UNIQUE (book, tag));
CREATE TABLE series (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
CREATE TABLE books_series_link (id INTEGER PRIMARY KEY, book INT, series INT,
    UNIQUE (book, series));
CREATE TABLE ratings (id INTEGER PRIMARY KEY, rating INT, link TEXT);
CREATE TABLE books_ratings_link (id INTEGER PRIMARY KEY, book INT, rating INT,
    UNIQUE (book, rating));
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher INT,
    UNIQUE (book, publisher));
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT, link TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code INT,
    item_order INT, UNIQUE (book, lang_code));
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT, name TEXT,
    uncompressed_size INT);
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT, type TEXT, val TEXT,
    UNIQUE (book, type));
CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT, UNIQUE (book));
CREATE TABLE annotations (id INTEGER PRIMARY KEY, book INT, format TEXT,
    user_type TEXT, user TEXT, timestamp TEXT, annot_id TEXT, annot_type TEXT,
    annot_data TEXT);
CREATE TABLE last_read_positions (id INTEGER PRIMARY KEY, book INT, format TEXT,
    user TEXT, device TEXT, cfi TEXT, epoch INT, pos_frac REAL);
CREATE TABLE books_pages_link (book INTEGER PRIMARY KEY, pages INTEGER,
    algorithm INTEGER, format TEXT, format_size INTEGER, timestamp TEXT,
    needs_scan INTEGER);
CREATE TABLE preferences (id INTEGER PRIMARY KEY, key TEXT NOT NULL,
    val TEXT NOT NULL, UNIQUE (key));
-- The modern custom_columns shape the column writers require
-- (editable/display/normalized are cquarry's create minimum;
-- mark_for_delete is the delete flag).
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT UNIQUE, name TEXT,
    datatype TEXT, is_multiple BOOL, editable BOOL DEFAULT 1,
    display TEXT DEFAULT '{}', normalized BOOL, mark_for_delete BOOL DEFAULT 0);
CREATE TABLE metadata_dirtied (id INTEGER PRIMARY KEY, book INTEGER NOT NULL,
    UNIQUE(book));
"""


def _entity_id(con, table, column, value, extra=""):
    """Resolve-or-create one entity row; returns its id. Entity ids grow
    from 1 like every hand-seeded fixture before this module."""
    row = con.execute(f"SELECT id FROM {table} WHERE {column} = ?", (value,)).fetchone()
    if row:
        return row[0]
    con.execute(f"INSERT INTO {table} ({column}{extra}) VALUES (?)", (value,))
    return con.execute(
        f"SELECT id FROM {table} WHERE {column} = ?", (value,)
    ).fetchone()[0]


def seed_book(
    con,
    book_id,
    title,
    *,
    authors=("A, Author",),
    tags=(),
    languages=(),
    publisher=None,
    series=None,
    series_index=1.0,
    pubdate="2020-01-01",
    timestamp=None,
    last_modified=None,
    has_cover=0,
    rating=None,
    identifiers=None,
    formats=(),
    path=None,
    uuid=None,
):
    """Seed one book row and every entity the spec names. Authors are
    display names (or (display, sort) pairs); formats are FMT strings or
    (FMT, name, size) tuples; identifiers map type -> value."""
    author_sort = " & ".join(
        (pair[1] if isinstance(pair, tuple) else pair) for pair in authors
    ).strip()
    con.execute(
        "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
        "has_cover,last_modified,series_index,path,uuid) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?)",
        (
            book_id,
            title,
            title,
            author_sort,
            timestamp or pubdate,
            pubdate,
            has_cover,
            last_modified or f"{pubdate} 00:00:00",
            series_index,
            path or f"{title}/{title} ({book_id})",
            uuid or f"uuid-{book_id}",
        ),
    )
    for pair in authors:
        name, sort = pair if isinstance(pair, tuple) else (pair, pair)
        aid = _entity_id(con, "authors", "name", name)
        if sort != name:
            con.execute("UPDATE authors SET sort = ? WHERE id = ?", (sort, aid))
        con.execute(
            "INSERT INTO books_authors_link (book, author) VALUES (?, ?)",
            (book_id, aid),
        )
    for tag in tags:
        tid = _entity_id(con, "tags", "name", tag)
        con.execute(
            "INSERT INTO books_tags_link (book, tag) VALUES (?, ?)", (book_id, tid)
        )
    for code in languages:
        lid = _entity_id(con, "languages", "lang_code", code)
        con.execute(
            "INSERT INTO books_languages_link (book, lang_code) VALUES (?, ?)",
            (book_id, lid),
        )
    if publisher is not None:
        pid = _entity_id(con, "publishers", "name", publisher)
        con.execute(
            "INSERT INTO books_publishers_link (book, publisher) VALUES (?, ?)",
            (book_id, pid),
        )
    if series is not None:
        sid = _entity_id(con, "series", "name", series)
        con.execute(
            "INSERT INTO books_series_link (book, series) VALUES (?, ?)",
            (book_id, sid),
        )
    if rating is not None:
        rid = _entity_id(con, "ratings", "rating", rating)
        con.execute(
            "INSERT INTO books_ratings_link (book, rating) VALUES (?, ?)",
            (book_id, rid),
        )
    for id_type, val in (identifiers or {}).items():
        con.execute(
            "INSERT INTO identifiers (book, type, val) VALUES (?, ?, ?)",
            (book_id, id_type, val),
        )
    for fmt in formats:
        name, size = (fmt, 1024) if isinstance(fmt, str) else (fmt[1], fmt[2])
        con.execute(
            "INSERT INTO data (book, format, name, uncompressed_size) "
            "VALUES (?, ?, ?, ?)",
            (book_id, fmt if isinstance(fmt, str) else fmt[0], name, size),
        )


def build_library(path, books=()):
    """Create the canonical schema at ``path`` and seed each book spec
    through seed_book. Returns the path (chaining-friendly)."""
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        for spec in books:
            seed_book(con, **spec)
        con.commit()
    finally:
        con.close()
    return path
