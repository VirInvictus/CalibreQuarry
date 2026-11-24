"""The scratch library: a throwaway Calibre-shaped database for plugin verify.

The verify harness runs plugin probes against a scratch, never the live
library. The schema here is the same canonical Calibre shape the test
suite's ``tests/_fixtures.py`` builds (kept in step with it; the fixture
module is the canonical copy and this one documents the dependency), so a
plugin's probes exercise real tables without calibre binaries or the real
library.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

SCRATCH_SCHEMA = """
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
CREATE TABLE identifiers (id INTEGER PRIMARY KEY, book INT NOT NULL, type TEXT
    NOT NULL, val TEXT NOT NULL, UNIQUE (book, type));
CREATE TABLE comments (id INTEGER PRIMARY KEY, book INT, text TEXT);
CREATE TABLE publishers (id INTEGER PRIMARY KEY, name TEXT, sort TEXT, link TEXT);
CREATE TABLE books_publishers_link (id INTEGER PRIMARY KEY, book INT, publisher
    INT, UNIQUE (book, publisher));
CREATE TABLE data (id INTEGER PRIMARY KEY, book INT, format TEXT UNCHECKED,
    uncompressed_size INT, name TEXT, UNIQUE (book, format));
CREATE TABLE preferences (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT UNIQUE,
    val TEXT);
CREATE TABLE custom_columns (id INTEGER PRIMARY KEY, label TEXT UNIQUE, name TEXT,
    datatype TEXT, mark_for_delete BOOL DEFAULT 0, is_multiple BOOL NOT NULL
    DEFAULT 0);

CREATE TRIGGER books_delete_trg AFTER DELETE ON books
BEGIN
    DELETE FROM books_authors_link WHERE book = OLD.id;
    DELETE FROM books_publishers_link WHERE book = OLD.id;
    DELETE FROM books_ratings_link WHERE book = OLD.id;
    DELETE FROM books_series_link WHERE book = OLD.id;
    DELETE FROM books_tags_link WHERE book = OLD.id;
    DELETE FROM books_languages_link WHERE book = OLD.id;
    DELETE FROM data WHERE book = OLD.id;
    DELETE FROM comments WHERE book = OLD.id;
    DELETE FROM identifiers WHERE book = OLD.id;
END;
CREATE TABLE languages (id INTEGER PRIMARY KEY, lang_code TEXT);
CREATE TABLE books_languages_link (id INTEGER PRIMARY KEY, book INT, lang_code
    INT, UNIQUE (book, lang_code));
"""


def build_scratch(root: Path) -> Path:
    """Create a scratch library dir with a seeded metadata.db; returns its path.

    Two books, one with an isbn identifier: enough shape for a probe to
    prove a plugin can read the library and plan a write without touching
    anything real.
    """
    root = Path(root)
    if root.exists():
        # A scratch is disposable: rebuild over any prior run (the DDL has
        # no IF NOT EXISTS, and a stale scratch must never poison a probe).
        shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    db_path = root / "metadata.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCRATCH_SCHEMA)
        conn.execute(
            "INSERT INTO books (title, sort, author_sort, timestamp, pubdate,"
            " has_cover, last_modified, series_index, path, uuid)"
            " VALUES ('Scratch One', 'One, Scratch', 'One, Scratch',"
            " '2026-01-01 00:00:00+00:00', '2026-01-01 00:00:00+00:00', 0,"
            " '2026-01-01 00:00:00+00:00', 1.0, 'Scratch One (1)', 'uuid-scratch-1')"
        )
        conn.execute(
            "INSERT INTO books (title, sort, author_sort, timestamp, pubdate,"
            " has_cover, last_modified, series_index, path, uuid)"
            " VALUES ('Scratch Two', 'Two, Scratch', 'Two, Scratch',"
            " '2026-01-02 00:00:00+00:00', '2026-01-02 00:00:00+00:00', 1,"
            " '2026-01-02 00:00:00+00:00', 1.0, 'Scratch Two (2)', 'uuid-scratch-2')"
        )
        conn.execute(
            "INSERT INTO authors (name, sort) VALUES ('Scratch, Author', 'Scratch, Author')"
        )
        conn.execute("INSERT INTO books_authors_link (book, author) VALUES (1, 1)")
        conn.execute(
            "INSERT INTO identifiers (book, type, val) VALUES (1, 'isbn', '9780140445688')"
        )
        conn.commit()
    finally:
        conn.close()
    return db_path
