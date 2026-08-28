"""Shared write-verb plumbing for CalibreQuarry.

Every mutating CLI flag and every TUI write action funnels through
:func:`run_write`, which owns the ``cquarry.write.WritableCalibreDB``
lifecycle: trigger UDF registration, atomic transactions, and the mapping of
lock/validation errors to clean exit codes (1) instead of tracebacks.
Argument-level problems are rejected by the dispatcher before the database
is ever opened (exit 2).

The write path is reached on purpose only from ``cli.py`` and ``tui.py``;
read modes never import this module (nor ``cquarry.write``).
"""

import sys


def parse_book_id(raw) -> int | None:
    """Coerce a CLI/TUI book id to int, or print an error and return None."""
    try:
        return int(raw)
    except TypeError, ValueError:
        print(f"ERROR: BOOK_ID must be an integer, got {raw!r}", file=sys.stderr)
        return None


def run_write(db_path: str, action) -> int:
    """Execute ``action(wdb)`` inside a WritableCalibreDB.

    Every write verb funnels through here so trigger UDFs get registered,
    transactions stay atomic, and lock/validation errors map to clean exit
    codes instead of tracebacks.
    """
    import sqlite3 as _sqlite3

    from cquarry.write import WritableCalibreDB

    try:
        with WritableCalibreDB(db_path) as wdb:
            return action(wdb)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except _sqlite3.OperationalError as e:
        print(
            f"ERROR: could not acquire the database write lock ({e}). "
            "Is Calibre running? Close it first.",
            file=sys.stderr,
        )
        return 1


# ---------------------------------------------------------------------------
# One executor per verb. Each opens its own WritableCalibreDB, prints a
# one-line result unless quiet, and returns a process exit code. The TUI
# calls these directly; the CLI reaches them through dispatch_write().
# ---------------------------------------------------------------------------


def op_set_title(db_path, book_id, title, *, quiet=False) -> int:
    def _do(wdb):
        wdb.update_title(book_id, title)
        if not quiet:
            print(
                f"Renamed book {book_id} to {title!r} "
                "(queued for OPF regeneration on Calibre's next startup)."
            )
        return 0

    return run_write(db_path, _do)


def op_set_authors(db_path, book_id, names, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_authors(book_id, names)
        if not quiet:
            print(
                f"Set authors of book {book_id} to {' & '.join(names)} "
                "(queued for OPF regeneration)."
            )
        return 0

    return run_write(db_path, _do)


def op_set_rating(db_path, book_id, stars, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_rating(book_id, stars)
        if not quiet:
            print(f"Rated book {book_id} at {stars:g} stars.")
        return 0

    return run_write(db_path, _do)


def op_set_comments(db_path, book_id, text, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_comments(book_id, text)
        if not quiet:
            print(f"Comments updated on book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_clear_comments(db_path, book_id, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_comments(book_id, None)
        if not quiet:
            print(f"Comments cleared on book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_set_column(db_path, book_id, label, value, *, quiet=False) -> int:
    def _do(wdb):
        # set_custom_column refuses non-editable/composite columns
        # and validates enumerations itself.
        wdb.set_custom_column(book_id, label, value)
        if not quiet:
            print(f"Set #{label.lstrip('#')} = {value!r} on book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_clear_column(db_path, book_id, label, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_custom_column(book_id, label, None)
        if not quiet:
            print(f"Cleared #{label.lstrip('#')} on book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_add_tag(db_path, book_id, tags, *, quiet=False) -> int:
    clean = [
        t.strip()
        for t in (tags if isinstance(tags, list) else [tags])
        if t and t.strip()
    ]

    def _do(wdb):
        added = sum(1 for t in clean if wdb.add_tag(book_id, t))
        if not quiet:
            tail = (
                ""
                if added == len(clean)
                else f" ({len(clean) - added} already present)"
            )
            print(f"Added {added} tag(s) to book {book_id}{tail}.")
        return 0

    return run_write(db_path, _do)


def op_remove_tag(db_path, book_id, tags, *, quiet=False) -> int:
    clean = [
        t.strip()
        for t in (tags if isinstance(tags, list) else [tags])
        if t and t.strip()
    ]

    def _do(wdb):
        removed = [t for t in clean if wdb.remove_tag(book_id, t)]
        if not quiet:
            msg = f"Removed {len(removed)} tag(s) from book {book_id}."
            missing = [t for t in clean if t not in removed]
            if missing:
                msg += f" Not present: {', '.join(missing)}."
            print(msg)
        return 0

    return run_write(db_path, _do)


def op_set_identifier(db_path, book_id, id_type, value, *, quiet=False) -> int:
    value = (value or "").strip()

    def _do(wdb):
        changed = wdb.set_identifier(book_id, id_type, value or None)
        if not quiet:
            if value:
                print(f"Set identifier {id_type}={value!r} on book {book_id}.")
            else:
                state = "cleared" if changed else "already absent"
                print(f"Identifier {id_type!r} on book {book_id} {state}.")
        return 0

    return run_write(db_path, _do)


def op_clear_identifier(db_path, book_id, id_type, *, quiet=False) -> int:
    return op_set_identifier(db_path, book_id, id_type, "", quiet=quiet)


def op_set_series(db_path, book_id, name, index=None, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_series(book_id, name, index)
        if not quiet:
            idx = f" #{index:g}" if index is not None else ""
            print(f"Set series of book {book_id} to {name!r}{idx}.")
        return 0

    return run_write(db_path, _do)


def op_clear_series(db_path, book_id, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_series(book_id, None)
        if not quiet:
            print(f"Removed book {book_id} from its series.")
        return 0

    return run_write(db_path, _do)


def op_set_publisher(db_path, book_id, name, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_publisher(book_id, name)
        if not quiet:
            print(f"Set publisher of book {book_id} to {name!r}.")
        return 0

    return run_write(db_path, _do)


def op_clear_publisher(db_path, book_id, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_publisher(book_id, None)
        if not quiet:
            print(f"Cleared publisher of book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_set_languages(db_path, book_id, codes, *, quiet=False) -> int:
    def _do(wdb):
        # set_languages canonicalizes names/codes ("English" -> "eng"),
        # splits a bare comma-string, and replaces the whole list.
        wdb.set_languages(book_id, codes)
        if not quiet:
            print(f"Set languages of book {book_id} to: {codes}")
        return 0

    return run_write(db_path, _do)


def op_clear_languages(db_path, book_id, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_languages(book_id, None)
        if not quiet:
            print(f"Cleared languages of book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_add_format(db_path, book_id, fmt, name, size, *, quiet=False) -> int:
    def _do(wdb):
        wdb.add_format(book_id, fmt, name, size)
        if not quiet:
            print(
                f"Registered format {fmt.upper()} on book {book_id} "
                f"({name}.{fmt.lower()}, {size} bytes)."
            )
        return 0

    return run_write(db_path, _do)


def op_remove_format(db_path, book_id, fmt, *, quiet=False) -> int:
    def _do(wdb):
        changed = wdb.remove_format(book_id, fmt)
        if not quiet:
            state = "Removed" if changed else "Format"
            verb = "removed from" if changed else "not present on"
            print(f"{state} {fmt.upper()} {verb} book {book_id}.")
        return 0

    return run_write(db_path, _do)


def op_set_cover(db_path, book_id, has_cover, *, quiet=False) -> int:
    def _do(wdb):
        wdb.set_has_cover(book_id, has_cover)
        if not quiet:
            print(f"Cover flag for book {book_id} set to {bool(has_cover)}.")
        return 0

    return run_write(db_path, _do)


def op_remove_book(db_path, book_id, *, confirm=False, quiet=False) -> int:
    if not confirm:
        # Dry run: describe what would go inside the write transaction.
        def _describe(wdb):
            row = wdb.conn.execute(
                "SELECT title FROM books WHERE id = ?", (book_id,)
            ).fetchone()
            title = row["title"] if row else "<unknown>"
            fmts = [
                r[0]
                for r in wdb.conn.execute(
                    "SELECT format FROM data WHERE book = ?", (book_id,)
                )
            ]
            print(
                f"DRY RUN — would permanently remove book {book_id} "
                f"({title!r}, formats: {', '.join(fmts) or 'none'})."
            )
            print("Re-run with --confirm-remove to delete.")
            return 0

        return run_write(db_path, _describe)

    def _do_remove(wdb):
        wdb.remove_book(book_id)
        if not quiet:
            print(f"Book {book_id} removed.")
        return 0

    return run_write(db_path, _do_remove)


# ---------------------------------------------------------------------------
# CLI dispatcher: maps the argparse namespace onto the executors above in
# verb-priority order. Returns the process exit code, or None when no write
# verb was requested (the caller falls through to the read modes).
# ---------------------------------------------------------------------------


def dispatch_write(args, db_path: str) -> int | None:
    if getattr(args, "set_title", None):
        book_id = parse_book_id(args.set_title[0])
        if book_id is None:
            return 2
        return op_set_title(db_path, book_id, args.set_title[1], quiet=args.quiet)

    if getattr(args, "set_authors", None):
        book_id = parse_book_id(args.set_authors[0])
        if book_id is None:
            return 2
        names = [n.strip() for n in args.set_authors[1].split(";") if n.strip()]
        return op_set_authors(db_path, book_id, names, quiet=args.quiet)

    if getattr(args, "set_rating", None):
        book_id = parse_book_id(args.set_rating[0])
        if book_id is None:
            return 2
        try:
            stars = float(args.set_rating[1])
        except ValueError:
            print(
                f"ERROR: STARS must be a number 0-5, got {args.set_rating[1]!r}",
                file=sys.stderr,
            )
            return 2
        if not 0 <= stars <= 5:
            print("ERROR: STARS must be within 0-5.", file=sys.stderr)
            return 2
        return op_set_rating(db_path, book_id, stars, quiet=args.quiet)

    if getattr(args, "set_comments", None):
        book_id = parse_book_id(args.set_comments[0])
        if book_id is None:
            return 2
        return op_set_comments(db_path, book_id, args.set_comments[1], quiet=args.quiet)

    if getattr(args, "clear_comments", None):
        book_id = parse_book_id(args.clear_comments)
        if book_id is None:
            return 2
        return op_clear_comments(db_path, book_id, quiet=args.quiet)

    if getattr(args, "set_column", None):
        book_id = parse_book_id(args.set_column[0])
        if book_id is None:
            return 2
        return op_set_column(
            db_path, book_id, args.set_column[1], args.set_column[2], quiet=args.quiet
        )

    if getattr(args, "clear_column", None):
        book_id = parse_book_id(args.clear_column[0])
        if book_id is None:
            return 2
        return op_clear_column(db_path, book_id, args.clear_column[1], quiet=args.quiet)

    if getattr(args, "add_tag", None):
        for pair in args.add_tag:
            book_id = parse_book_id(pair[0])
            if book_id is None:
                return 2
            rc = op_add_tag(db_path, book_id, [pair[1]], quiet=args.quiet)
            if rc:
                return rc
        return 0

    if getattr(args, "remove_tag", None):
        for pair in args.remove_tag:
            book_id = parse_book_id(pair[0])
            if book_id is None:
                return 2
            rc = op_remove_tag(db_path, book_id, [pair[1]], quiet=args.quiet)
            if rc:
                return rc
        return 0

    if getattr(args, "set_identifier", None):
        book_id = parse_book_id(args.set_identifier[0])
        if book_id is None:
            return 2
        return op_set_identifier(
            db_path,
            book_id,
            args.set_identifier[1],
            args.set_identifier[2],
            quiet=args.quiet,
        )

    if getattr(args, "clear_identifier", None):
        book_id = parse_book_id(args.clear_identifier[0])
        if book_id is None:
            return 2
        return op_clear_identifier(
            db_path, book_id, args.clear_identifier[1], quiet=args.quiet
        )

    if getattr(args, "set_series", None):
        book_id = parse_book_id(args.set_series[0])
        if book_id is None:
            return 2
        return op_set_series(
            db_path, book_id, args.set_series[1], args.series_index, quiet=args.quiet
        )

    if getattr(args, "clear_series", None):
        book_id = parse_book_id(args.clear_series)
        if book_id is None:
            return 2
        return op_clear_series(db_path, book_id, quiet=args.quiet)

    if getattr(args, "set_publisher", None):
        book_id = parse_book_id(args.set_publisher[0])
        if book_id is None:
            return 2
        return op_set_publisher(
            db_path, book_id, args.set_publisher[1], quiet=args.quiet
        )

    if getattr(args, "clear_publisher", None):
        book_id = parse_book_id(args.clear_publisher)
        if book_id is None:
            return 2
        return op_clear_publisher(db_path, book_id, quiet=args.quiet)

    if getattr(args, "set_languages", None):
        book_id = parse_book_id(args.set_languages[0])
        if book_id is None:
            return 2
        return op_set_languages(
            db_path, book_id, args.set_languages[1], quiet=args.quiet
        )

    if getattr(args, "clear_languages", None):
        book_id = parse_book_id(args.clear_languages)
        if book_id is None:
            return 2
        return op_clear_languages(db_path, book_id, quiet=args.quiet)

    if getattr(args, "add_format", None):
        book_id = parse_book_id(args.add_format[0])
        if book_id is None:
            return 2
        try:
            size = int(args.add_format[3])
        except ValueError:
            print(
                "ERROR: SIZE must be an integer byte count, got "
                f"{args.add_format[3]!r}",
                file=sys.stderr,
            )
            return 2
        return op_add_format(
            db_path,
            book_id,
            args.add_format[1],
            args.add_format[2],
            size,
            quiet=args.quiet,
        )

    if getattr(args, "remove_format", None):
        book_id = parse_book_id(args.remove_format[0])
        if book_id is None:
            return 2
        return op_remove_format(
            db_path, book_id, args.remove_format[1], quiet=args.quiet
        )

    if getattr(args, "set_cover", None):
        book_id = parse_book_id(args.set_cover[0])
        if book_id is None:
            return 2
        raw = args.set_cover[1].strip().lower()
        if raw in ("y", "yes", "true", "1"):
            has_cover = True
        elif raw in ("n", "no", "false", "0"):
            has_cover = False
        else:
            print(
                f"ERROR: cover state must be yes/no (got {args.set_cover[1]!r}).",
                file=sys.stderr,
            )
            return 2
        return op_set_cover(db_path, book_id, has_cover, quiet=args.quiet)

    if getattr(args, "remove_book", None) is not None:
        book_id = parse_book_id(args.remove_book)
        if book_id is None:
            return 2
        return op_remove_book(
            db_path, book_id, confirm=args.confirm_remove, quiet=args.quiet
        )

    return None
