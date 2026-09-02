"""Shared write-verb plumbing for CalibreQuarry.

Every mutating CLI flag and every TUI write action funnels through
:func:`run_write`, which owns the ``cquarry.write.WritableCalibreDB``
lifecycle: trigger UDF registration, atomic transactions, and the mapping of
lock/validation errors to clean exit codes (1) instead of tracebacks.
Argument-level problems are rejected by the dispatcher before the database
is ever opened (exit 2).

Several verbs in one invocation (e.g. ``--set-title ... --set-pubdate ...``)
run through :func:`run_write_batch`: one ``WritableCalibreDB``, one
``cquarry.batch()`` transaction, so a multi-field curation pass commits
exactly once and any failure rolls the whole pass back.

The write path is reached on purpose only from ``cli.py`` and ``tui.py``;
read modes never import this module (nor ``cquarry.write``).
"""

import sys
from collections.abc import Callable


def parse_book_id(raw) -> int | None:
    """Coerce a CLI/TUI book id to int, or print an error and return None."""
    try:
        return int(raw)
    except TypeError, ValueError:
        print(f"ERROR: BOOK_ID must be an integer, got {raw!r}", file=sys.stderr)
        return None


class _ArgError(Exception):
    """Argument-level problem: printed by dispatch_write, exit code 2.

    An empty message means the error was already printed (parse_book_id).
    """


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


def run_write_batch(db_path: str, actions, *, quiet: bool = False) -> int:
    """Execute several verbs in ONE WritableCalibreDB inside one batch().

    All-or-nothing: a failure anywhere rolls back every action in the pass
    (cquarry >= 1.7 batch semantics), so a multi-field curation pass commits
    exactly once instead of once per verb. Each action runs quiet; a one-line
    summary prints after the commit.
    """
    import sqlite3 as _sqlite3

    from cquarry.write import WritableCalibreDB

    try:
        with WritableCalibreDB(db_path) as wdb:
            with wdb.batch():
                for _label, action in actions:
                    rc = action(wdb)
                    if rc:
                        raise ValueError(f"verb failed with exit code {rc}")
        if not quiet:
            for label, _action in actions:
                print(f"ok: {label}")
            print(
                f"{len(actions)} mutations committed as one transaction "
                "(queued for OPF regeneration where applicable)."
            )
        return 0
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "Nothing was written: the whole batch rolled back.",
            file=sys.stderr,
        )
        return 1
    except _sqlite3.OperationalError as e:
        print(
            f"ERROR: could not acquire the database write lock ({e}). "
            "Is Calibre running? Close it first.",
            file=sys.stderr,
        )
        return 1


# ---------------------------------------------------------------------------
# One action builder per verb. Each returns the mutation as a callable taking
# the WritableCalibreDB, so the same verb runs alone (run_write) or batched
# with others (run_write_batch). The op_* wrappers below open their own
# connection for single-verb callers (the TUI); dispatch_write reaches the
# builders directly.
# ---------------------------------------------------------------------------


def action_set_title(book_id, title, *, quiet=False):
    def _do(wdb):
        wdb.update_title(book_id, title)
        if not quiet:
            print(
                f"Renamed book {book_id} to {title!r} "
                "(queued for OPF regeneration on Calibre's next startup)."
            )
        return 0

    return _do


def action_set_authors(book_id, names, *, quiet=False):
    def _do(wdb):
        wdb.set_authors(book_id, names)
        if not quiet:
            print(
                f"Set authors of book {book_id} to {' & '.join(names)} "
                "(queued for OPF regeneration)."
            )
        return 0

    return _do


def action_set_rating(book_id, stars, *, quiet=False):
    def _do(wdb):
        wdb.set_rating(book_id, stars)
        if not quiet:
            print(f"Rated book {book_id} at {stars:g} stars.")
        return 0

    return _do


def action_set_pubdate(book_id, value, *, quiet=False):
    def _do(wdb):
        changed = wdb.set_pubdate(book_id, value)
        if not quiet:
            state = "Set" if changed else "Already set:"
            print(f"{state} pubdate of book {book_id} to {value!r}.")
        return 0

    return _do


def action_set_comments(book_id, text, *, quiet=False):
    def _do(wdb):
        wdb.set_comments(book_id, text)
        if not quiet:
            print(f"Comments updated on book {book_id}.")
        return 0

    return _do


def action_clear_comments(book_id, *, quiet=False):
    return action_set_comments(book_id, None, quiet=quiet)


def action_set_column(book_id, label, value, *, quiet=False):
    def _do(wdb):
        # set_custom_column refuses non-editable/composite columns
        # and validates enumerations itself.
        wdb.set_custom_column(book_id, label, value)
        if not quiet:
            print(f"Set #{label.lstrip('#')} = {value!r} on book {book_id}.")
        return 0

    return _do


def action_clear_column(book_id, label, *, quiet=False):
    def _do(wdb):
        wdb.set_custom_column(book_id, label, None)
        if not quiet:
            print(f"Cleared #{label.lstrip('#')} on book {book_id}.")
        return 0

    return _do


def action_add_tag(book_id, tags, *, quiet=False):
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

    return _do


def action_remove_tag(book_id, tags, *, quiet=False):
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

    return _do


def action_set_identifier(book_id, id_type, value, *, quiet=False):
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

    return _do


def action_clear_identifier(book_id, id_type, *, quiet=False):
    def _do(wdb):
        changed = wdb.clear_identifier(book_id, id_type)
        if not quiet:
            state = "cleared" if changed else "already absent"
            print(f"Identifier {id_type!r} on book {book_id} {state}.")
        return 0

    return _do


def action_set_series(book_id, name, index=None, *, quiet=False):
    def _do(wdb):
        wdb.set_series(book_id, name, index)
        if not quiet:
            idx = f" #{index:g}" if index is not None else ""
            print(f"Set series of book {book_id} to {name!r}{idx}.")
        return 0

    return _do


def action_clear_series(book_id, *, quiet=False):
    def _do(wdb):
        wdb.set_series(book_id, None)
        if not quiet:
            print(f"Removed book {book_id} from its series.")
        return 0

    return _do


def action_set_publisher(book_id, name, *, quiet=False):
    def _do(wdb):
        wdb.set_publisher(book_id, name)
        if not quiet:
            print(f"Set publisher of book {book_id} to {name!r}.")
        return 0

    return _do


def action_clear_publisher(book_id, *, quiet=False):
    def _do(wdb):
        wdb.set_publisher(book_id, None)
        if not quiet:
            print(f"Cleared publisher of book {book_id}.")
        return 0

    return _do


def action_set_languages(book_id, codes, *, quiet=False):
    def _do(wdb):
        # set_languages canonicalizes names/codes ("English" -> "eng"),
        # splits a bare comma-string, and replaces the whole list.
        wdb.set_languages(book_id, codes)
        if not quiet:
            print(f"Set languages of book {book_id} to: {codes}")
        return 0

    return _do


def action_clear_languages(book_id, *, quiet=False):
    def _do(wdb):
        wdb.set_languages(book_id, None)
        if not quiet:
            print(f"Cleared languages of book {book_id}.")
        return 0

    return _do


def action_add_format(book_id, fmt, name, size, *, quiet=False):
    def _do(wdb):
        wdb.add_format(book_id, fmt, name, size)
        if not quiet:
            print(
                f"Registered format {fmt.upper()} on book {book_id} "
                f"({name}.{fmt.lower()}, {size} bytes)."
            )
        return 0

    return _do


def action_remove_format(book_id, fmt, *, quiet=False):
    def _do(wdb):
        changed = wdb.remove_format(book_id, fmt)
        if not quiet:
            state = "Removed" if changed else "Format"
            verb = "removed from" if changed else "not present on"
            print(f"{state} {fmt.upper()} {verb} book {book_id}.")
        return 0

    return _do


def action_set_cover(book_id, has_cover, *, quiet=False):
    def _do(wdb):
        wdb.set_has_cover(book_id, has_cover)
        if not quiet:
            print(f"Cover flag for book {book_id} set to {bool(has_cover)}.")
        return 0

    return _do


def action_remove_book(book_id, *, confirm=False, quiet=False):
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

        return _describe

    def _do_remove(wdb):
        wdb.remove_book(book_id)
        if not quiet:
            print(f"Book {book_id} removed.")
        return 0

    return _do_remove


# ---------------------------------------------------------------------------
# op_* executors: open their own WritableCalibreDB and run one verb. The TUI
# calls these directly; the CLI reaches the same mutations through
# dispatch_write()'s action builders.
# ---------------------------------------------------------------------------


def op_set_title(db_path, book_id, title, *, quiet=False) -> int:
    return run_write(db_path, action_set_title(book_id, title, quiet=quiet))


def op_set_authors(db_path, book_id, names, *, quiet=False) -> int:
    return run_write(db_path, action_set_authors(book_id, names, quiet=quiet))


def op_set_rating(db_path, book_id, stars, *, quiet=False) -> int:
    return run_write(db_path, action_set_rating(book_id, stars, quiet=quiet))


def op_set_pubdate(db_path, book_id, value, *, quiet=False) -> int:
    return run_write(db_path, action_set_pubdate(book_id, value, quiet=quiet))


def op_clear_pubdate(db_path, book_id, *, quiet=False) -> int:
    return run_write(db_path, action_set_pubdate(book_id, None, quiet=quiet))


def op_set_comments(db_path, book_id, text, *, quiet=False) -> int:
    return run_write(db_path, action_set_comments(book_id, text, quiet=quiet))


def op_clear_comments(db_path, book_id, *, quiet=False) -> int:
    return run_write(db_path, action_clear_comments(book_id, quiet=quiet))


def op_set_column(db_path, book_id, label, value, *, quiet=False) -> int:
    return run_write(db_path, action_set_column(book_id, label, value, quiet=quiet))


def op_clear_column(db_path, book_id, label, *, quiet=False) -> int:
    return run_write(db_path, action_clear_column(book_id, label, quiet=quiet))


def op_add_tag(db_path, book_id, tags, *, quiet=False) -> int:
    return run_write(db_path, action_add_tag(book_id, tags, quiet=quiet))


def op_remove_tag(db_path, book_id, tags, *, quiet=False) -> int:
    return run_write(db_path, action_remove_tag(book_id, tags, quiet=quiet))


def op_set_identifier(db_path, book_id, id_type, value, *, quiet=False) -> int:
    return run_write(
        db_path, action_set_identifier(book_id, id_type, value, quiet=quiet)
    )


def op_clear_identifier(db_path, book_id, id_type, *, quiet=False) -> int:
    # cquarry >= 1.9 has the explicit helper (same observable behavior as the
    # old set_identifier-with-empty-value route, minus the indirection).
    return run_write(db_path, action_clear_identifier(book_id, id_type, quiet=quiet))


def op_set_series(db_path, book_id, name, index=None, *, quiet=False) -> int:
    return run_write(db_path, action_set_series(book_id, name, index, quiet=quiet))


def op_clear_series(db_path, book_id, *, quiet=False) -> int:
    return run_write(db_path, action_clear_series(book_id, quiet=quiet))


def op_set_publisher(db_path, book_id, name, *, quiet=False) -> int:
    return run_write(db_path, action_set_publisher(book_id, name, quiet=quiet))


def op_clear_publisher(db_path, book_id, *, quiet=False) -> int:
    return run_write(db_path, action_clear_publisher(book_id, quiet=quiet))


def op_set_languages(db_path, book_id, codes, *, quiet=False) -> int:
    return run_write(db_path, action_set_languages(book_id, codes, quiet=quiet))


def op_clear_languages(db_path, book_id, *, quiet=False) -> int:
    return run_write(db_path, action_clear_languages(book_id, quiet=quiet))


def op_add_format(db_path, book_id, fmt, name, size, *, quiet=False) -> int:
    return run_write(db_path, action_add_format(book_id, fmt, name, size, quiet=quiet))


def op_remove_format(db_path, book_id, fmt, *, quiet=False) -> int:
    return run_write(db_path, action_remove_format(book_id, fmt, quiet=quiet))


def op_set_cover(db_path, book_id, has_cover, *, quiet=False) -> int:
    return run_write(db_path, action_set_cover(book_id, has_cover, quiet=quiet))


def op_remove_book(db_path, book_id, *, confirm=False, quiet=False) -> int:
    return run_write(db_path, action_remove_book(book_id, confirm=confirm, quiet=quiet))


# ---------------------------------------------------------------------------
# CLI dispatcher: collects every write verb present in the argparse namespace
# (in verb-priority order), then runs them. One verb behaves exactly as before
# (its own transaction, full messages). Several verbs run as one batched
# transaction via run_write_batch. Returns the process exit code, or None when
# no write verb was requested (the caller falls through to the read modes).
# ---------------------------------------------------------------------------


def _require_id(raw) -> int:
    book_id = parse_book_id(raw)
    if book_id is None:
        raise _ArgError()
    return book_id


def _collect_set_title(args, quiet):
    if not getattr(args, "set_title", None):
        return []
    book_id = _require_id(args.set_title[0])
    return [
        (
            f"set title of book {book_id}",
            action_set_title(book_id, args.set_title[1], quiet=quiet),
        )
    ]


def _collect_set_authors(args, quiet):
    if not getattr(args, "set_authors", None):
        return []
    book_id = _require_id(args.set_authors[0])
    names = [n.strip() for n in args.set_authors[1].split(";") if n.strip()]
    return [
        (
            f"set authors of book {book_id}",
            action_set_authors(book_id, names, quiet=quiet),
        )
    ]


def _collect_set_rating(args, quiet):
    if not getattr(args, "set_rating", None):
        return []
    book_id = _require_id(args.set_rating[0])
    try:
        stars = float(args.set_rating[1])
    except ValueError:
        raise _ArgError(
            f"STARS must be a number 0-5, got {args.set_rating[1]!r}"
        ) from None
    if not 0 <= stars <= 5:
        raise _ArgError("STARS must be within 0-5.")
    return [
        (
            f"rate book {book_id} {stars:g} stars",
            action_set_rating(book_id, stars, quiet=quiet),
        )
    ]


def _collect_set_pubdate(args, quiet):
    if not getattr(args, "set_pubdate", None):
        return []
    book_id = _require_id(args.set_pubdate[0])
    return [
        (
            f"set pubdate of book {book_id}",
            action_set_pubdate(book_id, args.set_pubdate[1], quiet=quiet),
        )
    ]


def _collect_clear_pubdate(args, quiet):
    if not getattr(args, "clear_pubdate", None):
        return []
    book_id = _require_id(args.clear_pubdate)
    return [
        (
            f"clear pubdate of book {book_id}",
            action_set_pubdate(book_id, None, quiet=quiet),
        )
    ]


def _collect_set_comments(args, quiet):
    if not getattr(args, "set_comments", None):
        return []
    book_id = _require_id(args.set_comments[0])
    return [
        (
            f"set comments on book {book_id}",
            action_set_comments(book_id, args.set_comments[1], quiet=quiet),
        )
    ]


def _collect_clear_comments(args, quiet):
    if not getattr(args, "clear_comments", None):
        return []
    book_id = _require_id(args.clear_comments)
    return [
        (
            f"clear comments on book {book_id}",
            action_clear_comments(book_id, quiet=quiet),
        )
    ]


def _collect_set_column(args, quiet):
    if not getattr(args, "set_column", None):
        return []
    book_id = _require_id(args.set_column[0])
    label, value = args.set_column[1], args.set_column[2]
    return [
        (
            f"set #{label} on book {book_id}",
            action_set_column(book_id, label, value, quiet=quiet),
        )
    ]


def _collect_clear_column(args, quiet):
    if not getattr(args, "clear_column", None):
        return []
    book_id = _require_id(args.clear_column[0])
    label = args.clear_column[1]
    return [
        (
            f"clear #{label} on book {book_id}",
            action_clear_column(book_id, label, quiet=quiet),
        )
    ]


def _collect_add_tag(args, quiet):
    if not getattr(args, "add_tag", None):
        return []
    out = []
    for pair in args.add_tag:
        book_id = _require_id(pair[0])
        out.append(
            (
                f"add tag {pair[1]!r} to book {book_id}",
                action_add_tag(book_id, [pair[1]], quiet=quiet),
            )
        )
    return out


def _collect_remove_tag(args, quiet):
    if not getattr(args, "remove_tag", None):
        return []
    out = []
    for pair in args.remove_tag:
        book_id = _require_id(pair[0])
        out.append(
            (
                f"remove tag {pair[1]!r} from book {book_id}",
                action_remove_tag(book_id, [pair[1]], quiet=quiet),
            )
        )
    return out


def _collect_set_identifier(args, quiet):
    if not getattr(args, "set_identifier", None):
        return []
    book_id = _require_id(args.set_identifier[0])
    id_type, value = args.set_identifier[1], args.set_identifier[2]
    return [
        (
            f"set identifier {id_type} on book {book_id}",
            action_set_identifier(book_id, id_type, value, quiet=quiet),
        )
    ]


def _collect_clear_identifier(args, quiet):
    if not getattr(args, "clear_identifier", None):
        return []
    book_id = _require_id(args.clear_identifier[0])
    id_type = args.clear_identifier[1]
    return [
        (
            f"clear identifier {id_type} on book {book_id}",
            action_set_identifier(book_id, id_type, "", quiet=quiet),
        )
    ]


def _collect_set_series(args, quiet):
    if not getattr(args, "set_series", None):
        return []
    book_id = _require_id(args.set_series[0])
    index = getattr(args, "series_index", None)
    return [
        (
            f"set series of book {book_id}",
            action_set_series(book_id, args.set_series[1], index, quiet=quiet),
        )
    ]


def _collect_clear_series(args, quiet):
    if not getattr(args, "clear_series", None):
        return []
    book_id = _require_id(args.clear_series)
    return [
        (
            f"clear series of book {book_id}",
            action_clear_series(book_id, quiet=quiet),
        )
    ]


def _collect_set_publisher(args, quiet):
    if not getattr(args, "set_publisher", None):
        return []
    book_id = _require_id(args.set_publisher[0])
    return [
        (
            f"set publisher of book {book_id}",
            action_set_publisher(book_id, args.set_publisher[1], quiet=quiet),
        )
    ]


def _collect_clear_publisher(args, quiet):
    if not getattr(args, "clear_publisher", None):
        return []
    book_id = _require_id(args.clear_publisher)
    return [
        (
            f"clear publisher of book {book_id}",
            action_clear_publisher(book_id, quiet=quiet),
        )
    ]


def _collect_set_languages(args, quiet):
    if not getattr(args, "set_languages", None):
        return []
    book_id = _require_id(args.set_languages[0])
    return [
        (
            f"set languages of book {book_id}",
            action_set_languages(book_id, args.set_languages[1], quiet=quiet),
        )
    ]


def _collect_clear_languages(args, quiet):
    if not getattr(args, "clear_languages", None):
        return []
    book_id = _require_id(args.clear_languages)
    return [
        (
            f"clear languages of book {book_id}",
            action_clear_languages(book_id, quiet=quiet),
        )
    ]


def _collect_add_format(args, quiet):
    if not getattr(args, "add_format", None):
        return []
    book_id = _require_id(args.add_format[0])
    fmt, name = args.add_format[1], args.add_format[2]
    try:
        size = int(args.add_format[3])
    except ValueError:
        raise _ArgError(
            f"SIZE must be an integer byte count, got {args.add_format[3]!r}"
        ) from None
    return [
        (
            f"register {fmt.upper()} on book {book_id}",
            action_add_format(book_id, fmt, name, size, quiet=quiet),
        )
    ]


def _collect_remove_format(args, quiet):
    if not getattr(args, "remove_format", None):
        return []
    book_id = _require_id(args.remove_format[0])
    fmt = args.remove_format[1]
    return [
        (
            f"remove {fmt.upper()} from book {book_id}",
            action_remove_format(book_id, fmt, quiet=quiet),
        )
    ]


def _collect_set_cover(args, quiet):
    if not getattr(args, "set_cover", None):
        return []
    book_id = _require_id(args.set_cover[0])
    raw = args.set_cover[1].strip().lower()
    if raw in ("y", "yes", "true", "1"):
        has_cover = True
    elif raw in ("n", "no", "false", "0"):
        has_cover = False
    else:
        raise _ArgError(f"cover state must be yes/no (got {args.set_cover[1]!r}).")
    return [
        (
            f"set cover flag of book {book_id}",
            action_set_cover(book_id, has_cover, quiet=quiet),
        )
    ]


def _collect_remove_book(args, quiet):
    if getattr(args, "remove_book", None) is None:
        return []
    book_id = _require_id(args.remove_book)
    confirm = bool(getattr(args, "confirm_remove", False))
    return [
        (
            f"remove book {book_id}",
            action_remove_book(book_id, confirm=confirm, quiet=quiet),
        )
    ]


_COLLECTORS: list[Callable] = [
    _collect_set_title,
    _collect_set_authors,
    _collect_set_rating,
    _collect_set_pubdate,
    _collect_clear_pubdate,
    _collect_set_comments,
    _collect_clear_comments,
    _collect_set_column,
    _collect_clear_column,
    _collect_add_tag,
    _collect_remove_tag,
    _collect_set_identifier,
    _collect_clear_identifier,
    _collect_set_series,
    _collect_clear_series,
    _collect_set_publisher,
    _collect_clear_publisher,
    _collect_set_languages,
    _collect_clear_languages,
    _collect_add_format,
    _collect_remove_format,
    _collect_set_cover,
    _collect_remove_book,
]


def dispatch_write(args, db_path: str) -> int | None:
    try:
        # The multi-verb decision must be known before actions are built (it
        # silences their per-verb prints in favour of a post-commit summary).
        # Repeated --add-tag/--remove-tag flags count individually: three tag
        # adds are three mutations and deserve the same single transaction.
        verb_count = sum(
            1
            for dest in (
                "set_title",
                "set_authors",
                "set_rating",
                "set_pubdate",
                "clear_pubdate",
                "set_comments",
                "clear_comments",
                "set_column",
                "clear_column",
                "set_identifier",
                "clear_identifier",
                "set_series",
                "clear_series",
                "set_publisher",
                "clear_publisher",
                "set_languages",
                "clear_languages",
                "add_format",
                "remove_format",
                "set_cover",
                "remove_book",
            )
            if getattr(args, dest, None)
        )
        verb_count += max(0, len(getattr(args, "add_tag", None) or []) - 1)
        verb_count += max(0, len(getattr(args, "remove_tag", None) or []) - 1)
        quiet = bool(getattr(args, "quiet", False)) or verb_count > 1

        collected = []
        for collect in _COLLECTORS:
            collected.extend(collect(args, quiet))
    except _ArgError as e:
        if str(e):
            print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if not collected:
        return None
    if len(collected) == 1:
        _label, action = collected[0]
        return run_write(db_path, action)
    if any(label.startswith("remove book") for label, _ in collected):
        print(
            "ERROR: --remove-book cannot be combined with other write verbs.",
            file=sys.stderr,
        )
        return 2
    return run_write_batch(db_path, collected, quiet=getattr(args, "quiet", False))
