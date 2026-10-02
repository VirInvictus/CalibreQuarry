"""Library-schema writes: --saved-search add/delete/rename.

The Phase 20 preference-parity verbs over cquarry 1.24's typed
``set_preference`` writer (the five-key whitelist) and its
``saved_search_add``/``saved_search_delete``/``saved_search_rename``
wrappers. They change library-wide GUI state (one ``preferences`` row),
not books, so they refuse the company of any book-targeting verb, and
the standing rails apply in full: dry-run by default, ``--apply`` with
the closed-Calibre guard and an out-of-tree timestamped backup, exit
0/1/2. Read modes never import this module.
"""

import sqlite3
import subprocess
import sys

from cquarry_cli.backups import make_backup
from cquarry_cli.dests import SCHEMA_WRITE_DESTS, SET_MODE_SOURCES, SINGLE_BOOK_DESTS

_PGREP_TIMEOUT = 15


def _calibre_running() -> bool:
    # Anchored NAME match; fail closed on anything that is not a clean
    # "not running" (the integrate/setwrite shape).
    try:
        return (
            subprocess.run(
                ["pgrep", "^calibre"], capture_output=True, timeout=_PGREP_TIMEOUT
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return True


class _UsageError(Exception):
    """Argument-level problem: printed by the dispatcher, exit 2."""


def _reject_empty(values, flag: str) -> None:
    for value in values:
        if not str(value or "").strip():
            raise _UsageError(
                f"{flag}: an empty name or expression is refused "
                "(cquarry's writer would raise anyway; this is the door)."
            )


def dispatch_schema_write(args, db_path: str) -> int | None:
    """Dispatch the saved-search writes; None when none is present (the
    caller falls through). Returns the process exit code otherwise."""
    present = [d for d in SCHEMA_WRITE_DESTS if getattr(args, d, None)]
    if not present:
        return None
    try:
        combos = [d for d in SET_MODE_SOURCES if getattr(args, d, None)] + [
            d for d in SINGLE_BOOK_DESTS if getattr(args, d, None)
        ]
        if combos:
            raise _UsageError(
                "saved-search writes refuse company: they change "
                "library-wide state, not books; drop the book verb(s) "
                f"(--{'/--'.join(c.replace('_', '-') for c in combos)})."
            )
        if len(present) > 1:
            raise _UsageError(
                "run one saved-search write per invocation, got: "
                + ", ".join("--" + d.replace("_", "-") for d in present)
            )
        verb = present[0]
        apply = bool(getattr(args, "apply", False))

        if verb == "saved_search_add":
            name, expression = args.saved_search_add
            _reject_empty((name, expression), "--saved-search-add")

            def action(wdb):
                return wdb.saved_search_add(name, expression)

            label = f"add saved search {name.strip()!r} -> {expression.strip()!r}"
        elif verb == "saved_search_delete":
            # metavar NAME with no nargs: argparse stores a plain string.
            name = args.saved_search_delete
            _reject_empty((name,), "--saved-search-delete")

            def action(wdb):
                return wdb.saved_search_delete(name)

            label = f"delete saved search {name.strip()!r}"
        else:
            old_name, new_name = args.saved_search_rename
            _reject_empty((old_name, new_name), "--saved-search-rename")

            def action(wdb):
                return wdb.saved_search_rename(old_name, new_name)

            label = f"rename saved search {old_name.strip()!r} -> {new_name.strip()!r}"

        if not apply:
            print(f"saved-search plan: {label}")
            from cquarry.db import CalibreDB

            db = CalibreDB(db_path)
            try:
                names = sorted(db.get_saved_searches())
            finally:
                db.close()
            print(f"Saved searches now: {', '.join(names) if names else '(none)'}.")
            print(
                "Dry run: nothing executed. --apply with --backup-dir "
                "(outside the library, Calibre closed) runs it."
            )
            return 0
        if _calibre_running():
            # Lock-class refusal (exit 1), matching setwrite and integrate.
            print(
                "ERROR: Calibre is running; close it before --apply.",
                file=sys.stderr,
            )
            return 1
        if not getattr(args, "backup_dir", None):
            print(
                "ERROR: --apply requires --backup-dir (outside the library): "
                "the saved searches live in one preferences row.",
                file=sys.stderr,
            )
            return 2
        try:
            make_backup(db_path, args.backup_dir)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        from cquarry.write import WritableCalibreDB

        with WritableCalibreDB(db_path) as wdb:
            with wdb.batch():
                changed = action(wdb)
        status = "applied" if changed else "already-so"
        print(f"{status}: {label}.")
        return 0
    except _UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        # cquarry's writer refusals: a rename onto an existing name, an
        # unknown old name, an unparsable payload. Validation class, exit 1.
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except sqlite3.OperationalError as e:
        print(
            f"ERROR: could not acquire the database write lock ({e}). "
            "Is Calibre running? Close it first.",
            file=sys.stderr,
        )
        return 1
