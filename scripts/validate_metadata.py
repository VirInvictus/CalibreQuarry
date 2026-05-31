#!/usr/bin/env python3
"""
validate_metadata.py: an integrity linter for a Calibre `metadata.db`, with an
optional opinionated mode driven by a taxonomy file.

Two layers:

  * Integrity checks (always on, zero config). Taxonomy-agnostic, schema-level
    problems Calibre's UI and `cquarry --audit` do not surface: books with no
    language, duplicate ISBNs, placeholder/unparseable pubdates, junk
    identifier types, ISBN-10s filed as Amazon IDs, orphaned custom-column
    links. Safe to point at any library; needs no configuration.

  * Opinionated checks (on when a taxonomy is loaded). A `taxonomy.json`
    describes your tag tree, publisher consolidations, and identifier vocab,
    and these checks enforce it: every tag must be declared, alias publishers
    must be merged, and fiction should not be PDF-only. See
    `taxonomy.example.json` for a comprehensive, ready-to-adapt template.

Companion to audit_epub_content.py. That script audits EPUB *content* (wrong
language, injected notices); this one audits the *database* and never opens a
book file. `cquarry --audit` covers curation gaps (untagged, unrated,
coverless, duplicates, series gaps); this is scoped to what it does not, so the
two do not overlap. Strictly read-only (`mode=ro`); it makes no changes.

Run from inside a Calibre library directory, or pass a path:
    python3 validate_metadata.py                  # integrity checks on ./metadata.db
    python3 validate_metadata.py ~/Calibre        # a library directory
    python3 validate_metadata.py path/to/metadata.db
    python3 validate_metadata.py --strict         # also flag non-canonical identifier types
    python3 validate_metadata.py --quiet          # only problems; truncate long lists

Opinionated mode turns on automatically when a `taxonomy.json` sits next to the
library (or the script, or the working directory), or is passed explicitly:
    cp taxonomy.example.json taxonomy.json         # then edit it to match your tree
    python3 validate_metadata.py --taxonomy taxonomy.json
    python3 validate_metadata.py --no-taxonomy     # force integrity-only

Checks (ERROR = bad data Calibre or tooling can trip on; WARNING = hygiene):
    EVERY_BOOK_LANGUAGE       (E)  book has no language record
    NO_DUPLICATE_ISBN         (E)  one ISBN attached to more than one book
    PUBDATE_PARSEABLE         (E)  pubdate does not parse as a date
    ID_TYPE_FORBIDDEN         (E)  identifier type is junk (url, uri, guid, isbn13, ...)
    TAG_IN_SPEC               (E)  tag in use is not declared in the taxonomy   [opinionated]
    NO_SENTINEL_PUBDATE       (W)  pubdate is Calibre's 0101-01-01 placeholder
    AMAZON_IS_ISBN10          (W)  an ISBN-10 is filed under amazon / mobi-asin
    ID_TYPE_UNDECLARED        (W)  identifier type outside the canonical set (--strict/taxonomy)
    ORPHAN_CC_LINKS           (W)  custom-column link rows pointing at deleted books
    PUBLISHER_NOT_CONSOLIDATED (W) an alias publisher should be merged into its canonical [opinionated]
    FORMAT_FICTION_PDF        (W)  a fiction book is PDF-only (no EPUB)            [opinionated]

Exit codes:
    0 = clean (no errors; warnings do not fail the run)
    1 = one or more errors found
    2 = setup error (metadata.db not found/unreadable, or a bad taxonomy file)

Stdlib only. A locked database (Calibre open) is handled by reading a temporary
copy, mirroring how the cquarry package degrades.
"""

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

# ANSI colours; suppress when stdout isn't a TTY or NO_COLOR is set
# (matches audit_epub_content.py / the cquarry package).
USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

SENTINEL_PUBDATE_PREFIX = "0101-01-01"

# Defaults used when no taxonomy is loaded (a taxonomy may override both).
# Identifier types that are never legitimate: web locators, opaque GUIDs, and
# the many ways an ISBN gets mis-typed instead of going in the `isbn` field.
DEFAULT_FORBIDDEN_TYPES = frozenset(
    {
        "url",
        "uri",
        "urn",
        "guid",
        "id",
        "revision",
        "created",
        "genre",
        "author_origin",
        "notes_images",
        "maxpg",
        "grvotes",
        "grrating",
        "isbn-13",
        "isbn-10",
        "isbn13",
        "isbn10",
        "eisbn",
        "ean",
        "sbn",
        "urnisbn",
    }
)

# Identifier types recognised as legitimate; --strict (or a taxonomy) flags
# everything else as ID_TYPE_UNDECLARED. Deliberately broad so a normal library
# stays quiet.
DEFAULT_CANONICAL_TYPES = frozenset(
    {
        "isbn",
        "amazon",
        "amazon_uk",
        "amazon_de",
        "amazon_fr",
        "amazon_es",
        "amazon_it",
        "amazon_ca",
        "amazon_jp",
        "amazon_in",
        "mobi-asin",
        "goodreads",
        "google",
        "storygraph",
        "openlibrary",
        "isfdb",
        "isfdb-title",
        "kobo",
        "barnesnoble",
        "fictiondb",
        "doi",
        "oclc",
        "lccn",
        "hardcover",
        "hardcover-edition",
        "librarything",
        "douban",
        "dnb",
        "worldcat",
        "issn",
    }
)


class Reporter:
    """Collects findings, grouped by check code, into errors and warnings."""

    def __init__(self) -> None:
        self.errors: list[tuple[str, str]] = []
        self.warnings: list[tuple[str, str]] = []

    def error(self, code: str, message: str) -> None:
        self.errors.append((code, message))

    def warning(self, code: str, message: str) -> None:
        self.warnings.append((code, message))


def is_isbn10(value: str) -> bool:
    """True if `value` is a valid ISBN-10 (checksum included). Strips hyphens
    and spaces first. The checksum is what separates a real ISBN-10 from a
    same-length Amazon ASIN (ASINs almost never satisfy it)."""
    digits = value.replace("-", "").replace(" ", "")
    if len(digits) != 10:
        return False
    total = 0
    for i, ch in enumerate(digits):
        if ch in "Xx" and i == 9:
            value_i = 10
        elif ch.isdigit():
            value_i = int(ch)
        else:
            return False
        total += (10 - i) * value_i
    return total % 11 == 0


def flatten_tree(nodes: list[Any], prefix: str = "") -> set[str]:
    """Flatten a taxonomy tree into the set of valid tag strings. A node is a
    string (leaf) or an object {name, bare_allowed?, children?}. A path is valid
    when the node is a leaf (no children) or its `bare_allowed` is true; children
    are recursed with the path as the new prefix. So `bare_allowed` on a branch
    permits the branch itself as a tag, on top of all its leaves."""
    allowed: set[str] = set()
    for node in nodes:
        if isinstance(node, str):
            name, children, bare = node, [], False
        else:
            name, children, bare = (
                node["name"],
                node.get("children", []),
                node.get("bare_allowed"),
            )
        path = f"{prefix}.{name}" if prefix else name
        if not children or bare:
            allowed.add(path)
        if children:
            allowed |= flatten_tree(children, path)
    return allowed


def connect_ro(db_path: Path) -> tuple[sqlite3.Connection, str | None]:
    """Open `db_path` read-only. If Calibre holds the lock, read a temp copy
    instead (returning the temp dir so the caller can clean it up)."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("SELECT 1 FROM books LIMIT 1")
        return con, None
    except sqlite3.OperationalError:
        con.close()
    tmpdir = tempfile.mkdtemp(prefix="cquarry-validate-")
    for suffix in ("", "-wal", "-shm"):
        src = Path(str(db_path) + suffix)
        if src.exists():
            shutil.copy2(src, Path(tmpdir) / ("metadata.db" + suffix))
    con = sqlite3.connect(f"file:{Path(tmpdir) / 'metadata.db'}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con, tmpdir


# --- Integrity checks (always on) ------------------------------------------


def check_every_book_language(cur, report: Reporter) -> None:
    cur.execute("""
        SELECT b.id, b.title FROM books b
        WHERE NOT EXISTS (SELECT 1 FROM books_languages_link WHERE book = b.id)
    """)
    for r in cur.fetchall():
        report.error(
            "EVERY_BOOK_LANGUAGE", f"#{r['id']} '{r['title']}' has no language"
        )


def check_no_duplicate_isbn(cur, report: Reporter) -> None:
    cur.execute("""
        SELECT val, GROUP_CONCAT(book) AS books FROM identifiers
        WHERE type = 'isbn' AND val <> ''
        GROUP BY val HAVING COUNT(DISTINCT book) > 1
    """)
    for r in cur.fetchall():
        report.error(
            "NO_DUPLICATE_ISBN", f"ISBN {r['val']} appears on books: {r['books']}"
        )


def check_pubdate_parseable(cur, report: Reporter) -> None:
    cur.execute("""
        SELECT id, title, pubdate FROM books
        WHERE pubdate IS NOT NULL AND date(pubdate) IS NULL
    """)
    for r in cur.fetchall():
        report.error(
            "PUBDATE_PARSEABLE",
            f"#{r['id']} '{r['title']}' has unparseable pubdate '{r['pubdate']}'",
        )


def check_no_sentinel_pubdate(cur, report: Reporter) -> None:
    cur.execute(
        "SELECT id, title, pubdate FROM books WHERE pubdate LIKE ?",
        (SENTINEL_PUBDATE_PREFIX + "%",),
    )
    for r in cur.fetchall():
        report.warning(
            "NO_SENTINEL_PUBDATE",
            f"#{r['id']} '{r['title']}' has sentinel pubdate {r['pubdate']}",
        )


def check_identifier_types(
    cur, report: Reporter, forbidden: set[str], canonical: set[str], strict: bool
) -> None:
    cur.execute("SELECT type, COUNT(*) AS n FROM identifiers GROUP BY type")
    for r in cur.fetchall():
        t, n = r["type"], r["n"]
        if t in forbidden:
            report.error(
                "ID_TYPE_FORBIDDEN",
                f"identifier type '{t}' is forbidden (on {n} book(s))",
            )
        elif strict and t not in canonical:
            report.warning(
                "ID_TYPE_UNDECLARED",
                f"identifier type '{t}' is not in the canonical list (on {n} book(s))",
            )


def check_amazon_is_isbn10(cur, report: Reporter) -> None:
    cur.execute(
        "SELECT book, type, val FROM identifiers WHERE type IN ('amazon', 'mobi-asin')"
    )
    for r in cur.fetchall():
        if is_isbn10(r["val"]):
            report.warning(
                "AMAZON_IS_ISBN10",
                f"#{r['book']} has ISBN-10 '{r['val']}' in {r['type']} field; should be isbn",
            )


def check_orphan_cc_links(cur, report: Reporter) -> None:
    cur.execute("SELECT id, label FROM custom_columns")
    for col in cur.fetchall():
        link = f"books_custom_column_{col['id']}_link"
        try:
            n = cur.execute(
                f"SELECT COUNT(*) AS n FROM {link} WHERE book NOT IN (SELECT id FROM books)"
            ).fetchone()["n"]
        except sqlite3.OperationalError:
            continue  # column has no standard link table; nothing to orphan-check
        if n:
            report.warning(
                "ORPHAN_CC_LINKS",
                f"custom column '{col['label']}' (cc{col['id']}) has {n} link row(s) "
                "pointing at deleted books",
            )


# --- Opinionated checks (taxonomy-driven) ----------------------------------


def check_tags_in_spec(cur, report: Reporter, allowed: set[str]) -> None:
    cur.execute(
        "SELECT DISTINCT t.name FROM tags t JOIN books_tags_link bl ON bl.tag = t.id "
        "ORDER BY t.name"
    )
    for r in cur.fetchall():
        if r["name"] not in allowed:
            report.error(
                "TAG_IN_SPEC", f"tag '{r['name']}' is not declared in the taxonomy"
            )


def check_publisher_canonicals(
    cur, report: Reporter, canonicals: dict[str, list[str]]
) -> None:
    cur.execute("SELECT name FROM publishers")
    names = {r["name"] for r in cur.fetchall()}
    for canonical, aliases in canonicals.items():
        for alias in aliases:
            if alias in names:
                report.warning(
                    "PUBLISHER_NOT_CONSOLIDATED",
                    f"publisher '{alias}' should be merged into '{canonical}'",
                )


def check_format_fiction_pdf(cur, report: Reporter, fiction_roots: list[str]) -> None:
    cur.execute("""
        SELECT DISTINCT b.id, b.title, t.name AS tag FROM books b
        JOIN books_tags_link bl ON bl.book = b.id
        JOIN tags t ON t.id = bl.tag
        JOIN data d ON d.book = b.id
        WHERE d.format = 'PDF'
          AND NOT EXISTS (SELECT 1 FROM data d2 WHERE d2.book = b.id AND d2.format = 'EPUB')
    """)
    for r in cur.fetchall():
        tag = r["tag"]
        if any(tag == root or tag.startswith(root + ".") for root in fiction_roots):
            report.warning(
                "FORMAT_FICTION_PDF",
                f"#{r['id']} '{r['title']}' (tag '{tag}') is PDF-only; fiction prefers EPUB",
            )


# --- Resolution & driver ----------------------------------------------------


def resolve_db_path(arg: str | None) -> Path | None:
    """Find metadata.db from an explicit path, the script's own directory, or
    the current working directory (in that order)."""
    candidates: list[Path] = []
    if arg:
        p = Path(arg).expanduser()
        candidates.append(p if p.suffix == ".db" else p / "metadata.db")
    else:
        candidates.append(Path(__file__).resolve().parent / "metadata.db")
        candidates.append(Path.cwd() / "metadata.db")
    for c in candidates:
        if c.exists():
            return c
    return None


def resolve_taxonomy_path(arg: str | None, db_path: Path) -> Path | None:
    """An explicit --taxonomy wins; otherwise auto-detect a `taxonomy.json`
    next to the library, the script, or the working directory. The shipped
    `taxonomy.example.json` is a template and is never auto-loaded."""
    if arg:
        return Path(arg).expanduser()
    for d in (db_path.parent, Path(__file__).resolve().parent, Path.cwd()):
        candidate = d / "taxonomy.json"
        if candidate.exists():
            return candidate
    return None


def print_group(findings: list[tuple[str, str]], colour: str, quiet: bool) -> None:
    by_code: dict[str, list[str]] = {}
    for code, msg in findings:
        by_code.setdefault(code, []).append(msg)
    for code in sorted(by_code):
        msgs = by_code[code]
        print(f"  {colour}{code}{RESET} ({len(msgs)})")
        shown = msgs if not quiet else msgs[:5]
        for m in shown:
            print(f"    {m}")
        if quiet and len(msgs) > len(shown):
            print(f"    ... and {len(msgs) - len(shown)} more")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Integrity linter for a Calibre metadata.db, with an "
        "optional taxonomy-driven opinionated mode.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="library directory or metadata.db (default: ./metadata.db)",
    )
    parser.add_argument(
        "--taxonomy", help="path to a taxonomy.json (enables opinionated checks)"
    )
    parser.add_argument(
        "--no-taxonomy",
        action="store_true",
        help="skip taxonomy auto-detection; integrity only",
    )
    parser.add_argument(
        "--strict", action="store_true", help="also flag non-canonical identifier types"
    )
    parser.add_argument(
        "--quiet", action="store_true", help="print only problems; truncate long lists"
    )
    args = parser.parse_args()

    db_path = resolve_db_path(args.path)
    if db_path is None:
        where = args.path or "the current directory"
        print(f"ERROR: no metadata.db found at {where}.", file=sys.stderr)
        return 2

    spec: dict | None = None
    spec_path: Path | None = None
    if not args.no_taxonomy:
        spec_path = resolve_taxonomy_path(args.taxonomy, db_path)
        if spec_path is not None:
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(
                    f"ERROR: cannot read taxonomy {spec_path}: {exc}", file=sys.stderr
                )
                return 2
        elif args.taxonomy:
            print(f"ERROR: taxonomy not found: {args.taxonomy}", file=sys.stderr)
            return 2

    forbidden = (
        set(spec.get("forbidden_identifier_types", DEFAULT_FORBIDDEN_TYPES))
        if spec
        else set(DEFAULT_FORBIDDEN_TYPES)
    )
    canonical = (
        set(spec.get("canonical_identifier_types", DEFAULT_CANONICAL_TYPES))
        if spec
        else set(DEFAULT_CANONICAL_TYPES)
    )
    # A loaded taxonomy is an explicit identifier vocabulary, so honour it like --strict.
    strict_ids = args.strict or spec is not None

    try:
        con, tmpdir = connect_ro(db_path)
    except sqlite3.OperationalError as exc:
        print(f"ERROR: cannot open {db_path}: {exc}", file=sys.stderr)
        return 2

    try:
        cur = con.cursor()
        report = Reporter()
        # Integrity layer
        check_every_book_language(cur, report)
        check_no_duplicate_isbn(cur, report)
        check_pubdate_parseable(cur, report)
        check_no_sentinel_pubdate(cur, report)
        check_identifier_types(cur, report, forbidden, canonical, strict_ids)
        check_amazon_is_isbn10(cur, report)
        check_orphan_cc_links(cur, report)
        # Opinionated layer
        if spec is not None:
            allowed = flatten_tree(spec.get("tree", []))
            check_tags_in_spec(cur, report, allowed)
            check_publisher_canonicals(
                cur, report, spec.get("publisher_canonicals", {})
            )
            if spec.get("format_prefer_epub"):
                check_format_fiction_pdf(cur, report, spec.get("fiction_roots", []))
    finally:
        con.close()
        if tmpdir:
            shutil.rmtree(tmpdir, ignore_errors=True)

    n_err, n_warn = len(report.errors), len(report.warnings)
    if not args.quiet:
        print(f"{BOLD}Validating{RESET} {db_path}")
        print(f"Taxonomy: {spec_path if spec_path else 'none (integrity checks only)'}")
    if n_err:
        print(f"\n{BOLD}ERRORS{RESET} ({n_err})")
        print_group(report.errors, RED, args.quiet)
    if n_warn:
        print(f"\n{BOLD}WARNINGS{RESET} ({n_warn})")
        print_group(report.warnings, YELLOW, args.quiet)

    if n_err:
        print(f"\n{RED}FAIL{RESET}: {n_err} error(s), {n_warn} warning(s).")
        return 1
    if n_warn:
        print(f"\n{YELLOW}PASS WITH WARNINGS{RESET}: 0 errors, {n_warn} warning(s).")
        return 0
    if not args.quiet:
        print(f"\n{GREEN}PASS{RESET}: 0 errors, 0 warnings.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
