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

import re
import sqlite3
import subprocess
import sys

from cquarry_cli.backups import make_backup
from cquarry_cli.dests import SCHEMA_WRITE_DESTS, SET_MODE_SOURCES, SINGLE_BOOK_DESTS
from cquarry_cli.writeops import FORBIDDEN_COLUMNS

_PGREP_TIMEOUT = 15

#: The datatypes cquarry's create_custom_column accepts (upstream's set).
_COLUMN_DATATYPES = (
    "rating",
    "text",
    "comments",
    "datetime",
    "int",
    "float",
    "bool",
    "series",
    "composite",
    "enumeration",
)


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


def _check_column_label(label: str, flag: str) -> None:
    """The NON-NEGOTIABLES by label, at the schema doors too: deleting the
    reading_status column would be the biggest write to it there is."""
    if str(label).lstrip("#").casefold() in FORBIDDEN_COLUMNS:
        raise _UsageError(
            f"{flag}: #{str(label).lstrip('#')} is banned (library "
            "NON-NEGOTIABLES: reading_status, status, and date_read are "
            "never written by tools; deleting the column is the biggest "
            "write there is)."
        )


def dispatch_schema_write(args, db_path: str) -> int | None:
    """Dispatch the library-schema writes; None when none is present (the
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
        column_multiple = bool(getattr(args, "column_is_multiple", False))
        if column_multiple and verb != "add_custom_column":
            raise _UsageError(
                "--column-is-multiple is a modifier of --add-custom-column."
            )

        if verb == "add_custom_column":
            label, name, datatype = args.add_custom_column
            _check_column_label(label, "--add-custom-column")
            label = label.strip()
            # cquarry's rule (upstream's): \w+ starting with a letter,
            # lowercase throughout.
            if (
                not label
                or not label[0].isalpha()
                or label != label.lower()
                or re.match(r"^\w*$", label) is None
            ):
                raise _UsageError(
                    f"--add-custom-column: the label must contain only "
                    f"lowercase letters, digits, and underscores and start "
                    f"with a letter, got {label!r}"
                )
            datatype = (datatype or "").strip().lower()
            if datatype not in _COLUMN_DATATYPES:
                raise _UsageError(
                    f"--add-custom-column: {datatype!r} is not a supported "
                    f"datatype (available: {', '.join(_COLUMN_DATATYPES)})"
                )
            _reject_empty((name,), "--add-custom-column")

            def action(wdb):
                return wdb.create_custom_column(
                    label,
                    name.strip(),
                    datatype,
                    is_multiple=column_multiple,
                )

            label_text = (
                f"create custom column #{label} ({name.strip()!r}, "
                f"{datatype}{', is_multiple' if column_multiple else ''})"
            )
        elif verb == "remove_custom_column":
            # metavar LABEL with no nargs: a plain string.
            label = args.remove_custom_column
            _check_column_label(label, "--remove-custom-column")
            label = label.strip()

            def action(wdb):
                return wdb.delete_custom_column(label)

            label_text = f"delete custom column #{label.lstrip('#')}"
        elif verb == "saved_search_add":
            name, expression = args.saved_search_add
            _reject_empty((name, expression), "--saved-search-add")

            def action(wdb):
                return wdb.saved_search_add(name, expression)

            label_text = f"add saved search {name.strip()!r} -> {expression.strip()!r}"
        elif verb == "saved_search_delete":
            # metavar NAME with no nargs: argparse stores a plain string.
            name = args.saved_search_delete
            _reject_empty((name,), "--saved-search-delete")

            def action(wdb):
                return wdb.saved_search_delete(name)

            label_text = f"delete saved search {name.strip()!r}"
        else:
            old_name, new_name = args.saved_search_rename
            _reject_empty((old_name, new_name), "--saved-search-rename")

            def action(wdb):
                return wdb.saved_search_rename(old_name, new_name)

            label_text = (
                f"rename saved search {old_name.strip()!r} -> {new_name.strip()!r}"
            )

        if not apply:
            print(f"schema-write plan: {label_text}")
            from cquarry.db import CalibreDB

            db = CalibreDB(db_path)
            try:
                if verb.startswith("saved_search"):
                    names = sorted(db.get_saved_searches())
                    print(
                        "Saved searches now: "
                        f"{', '.join(names) if names else '(none)'}."
                    )
                else:
                    # get_custom_columns is keyed by display NAME; the
                    # lookup here is by label, so scan the values.
                    key = label.lstrip("#")
                    col = next(
                        (
                            c
                            for c in db.get_custom_columns().values()
                            if str(c.get("label", "")).lower() == key.lower()
                        ),
                        None,
                    )
                    if col is not None:
                        print(
                            f"Column today: #{col.get('label')} "
                            f"{col.get('name')!r} ({col.get('datatype')}); "
                            "deletion FLAGS the column (mark_for_delete) and "
                            "Calibre purges the storage at its next start."
                        )
                    elif verb == "remove_custom_column":
                        raise _UsageError(
                            f"no custom column named #{key}; nothing to delete"
                        )
            finally:
                db.close()
            print(
                "Dry run: nothing executed. --apply with --backup-dir "
                "(outside the library, Calibre closed) runs it."
                + (
                    " NOTE: a new column sets Calibre's "
                    "update_all_last_mod_dates_on_start, so the next Calibre "
                    "start refreshes every book's last_modified."
                    if verb == "add_custom_column"
                    else ""
                )
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
        tail = ""
        if verb == "add_custom_column" and isinstance(changed, int):
            tail = f" as column number {changed}"
        print(f"{status}: {label_text}{tail}.")
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
