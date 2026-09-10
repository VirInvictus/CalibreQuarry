"""Set-oriented write verbs: one target set, many ``--batch-*`` verbs.

Phase 16 (roadmap): the read side is batch-shaped; this is the write
side's counterpart. Exactly one target source per invocation (--ids,
--from-search, --from-untagged, --from-manifest) feeds any number of
id-less ``--batch-*`` verbs, each applied to every targeted book. Dry-run
by default: nothing opens ``WritableCalibreDB`` without ``--apply``.

``--apply`` demands a closed Calibre (anchored ``pgrep ^calibre`` guard)
and a mandatory ``--backup-dir`` outside the library directory, then runs
as ONE ``cquarry.batch()`` transaction: any per-(book, verb) failure rolls
the whole pass back and nothing is written (``--commit-per-book`` is the
documented non-default escape hatch for very large sets). Already-so rows
make a corrected re-run cheap.

Safety rails mechanically encoded here:

- ``--batch-clear-rating`` is accepted ONLY with ``--from-manifest``: the
  library NON-NEGOTIABLES ban bulk rating edits, and the manifest proves
  which ids that run imported. Every other source is refused (exit 2).
- The column verbs refuse ``#reading_status``, ``status``, and
  ``date_read`` by label, belt-and-braces on the absolute ban.
- Deletion has no set form: ``--remove-book`` stays per-book, explicit,
  and recoverable.

Reporting: per-verb applied/already-so/failed counts plus the per-id
failure list on stderr; ``--format json`` emits
``{target, verbs, results[{id, verb, status, detail}], committed,
dry_run}`` for an AI caller. Exit 0 committed/dry-run, 1 failures or
lock, 2 usage. ``--quiet`` suppresses the stdout report; failures and
the exit codes still speak.

This module is write-path code (imports writeops and cquarry.write);
read modes never import it.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

from cquarry_cli import writeops

_PGREP_TIMEOUT = 15

# Labels no set-mode column verb may touch, however spelled (# prefix and
# case fold away). The library NON-NEGOTIABLES outrank every convenience.
_FORBIDDEN_LABELS = ("reading_status", "status", "date_read")

_SOURCES = ("set_ids", "from_search", "from_untagged", "from_manifest")


class _UsageError(Exception):
    """Set-mode argument problem: printed by dispatch_set_write, exit 2."""


class _RollbackNeeded(Exception):
    """Raised inside the batch when any row failed, so nothing commits."""


def _calibre_running() -> bool:
    # Anchored NAME match, never -f: command lines must not be able to trip
    # this. The prefix covers the GUI, calibre-debug, and the calibre-parallel
    # workers whose comm truncates. Timeout means "assume running": refusing
    # costs a re-run, guessing wrong writes to a live database.
    try:
        return (
            subprocess.run(
                ["pgrep", "^calibre"], capture_output=True, timeout=_PGREP_TIMEOUT
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return True


def _parse_id_list(raw: str, what: str) -> list[int]:
    ids: list[int] = []
    for token in raw.replace(",", " ").split():
        try:
            ids.append(int(token))
        except ValueError:
            raise _UsageError(f"{what}: ids must be integers, got {token!r}") from None
    if not ids:
        raise _UsageError(f"{what}: no ids found")
    # A target set is a set: duplicates would just double-run each verb
    # (second pass already-so), so collapse them, preserving order.
    return list(dict.fromkeys(ids))


def _resolve_targets(args, db) -> tuple[list[int], str]:
    """Resolve the target set READ-ONLY; returns (ids, verbatim target)."""
    if args.set_ids is not None:
        ids = _parse_id_list(args.set_ids, "--ids")
        target = f"--ids {args.set_ids}"
    elif args.from_search is not None:
        try:
            ids = sorted(db.search(args.from_search))
        except Exception as e:
            raise _UsageError(f"--from-search could not be resolved: {e}") from None
        target = f"--from-search {args.from_search}"
    elif args.from_untagged:
        from cquarry.integrity import find_untagged

        ids = sorted(find_untagged(db))
        target = "--from-untagged"
    else:
        path = Path(args.from_manifest).expanduser()
        try:
            text = path.read_text()
        except OSError as e:
            raise _UsageError(f"--from-manifest unreadable: {e}") from None
        ids = _parse_id_list(text, f"manifest {path}")
        target = f"--from-manifest {args.from_manifest}"

    # Hand-supplied ids are validated against the library before anything
    # opens writable; search/untagged results exist by construction.
    if args.set_ids is not None or args.from_manifest is not None:
        known = set(db.search(""))
        unknown = [i for i in ids if i not in known]
        if unknown:
            raise _UsageError(
                "unknown book id(s): "
                + ", ".join(str(i) for i in unknown)
                + "; nothing will be written"
            )
    return ids, target


def _check_forbidden_label(label: str, flag: str) -> None:
    if label.lstrip("#").casefold() in _FORBIDDEN_LABELS:
        raise _UsageError(
            f"{flag}: #{label.lstrip('#')} is banned for set writes "
            "(library NON-NEGOTIABLES: reading_status, status, and date_read "
            "are never written by tools)."
        )


def _collect_verbs(args) -> list[tuple[str, str, object]]:
    """One (verb, label, make) per flag occurrence; make(book_id) returns
    the writeops action closure for one book, quiet because the run
    reports in aggregate."""
    specs: list[tuple[str, str, object]] = []

    if args.batch_add_tag:
        for tag in args.batch_add_tag:
            specs.append(
                (
                    "add-tag",
                    f"add tag {tag!r}",
                    lambda bid, t=tag: writeops.action_add_tag(bid, [t], quiet=True),
                )
            )
    if args.batch_remove_tag:
        for tag in args.batch_remove_tag:
            specs.append(
                (
                    "remove-tag",
                    f"remove tag {tag!r}",
                    lambda bid, t=tag: writeops.action_remove_tag(bid, [t], quiet=True),
                )
            )
    if args.batch_clear_tags:
        specs.append(
            (
                "clear-tags",
                "clear tags",
                lambda bid: writeops.action_clear_tags(bid, quiet=True),
            )
        )
    if args.batch_clear_rating:
        specs.append(
            (
                "clear-rating",
                "clear rating",
                lambda bid: writeops.action_clear_rating(bid, quiet=True),
            )
        )
    if args.batch_set_column:
        label, value = args.batch_set_column
        _check_forbidden_label(label, "--batch-set-column")
        specs.append(
            (
                "set-column",
                f"set #{label.lstrip('#')} = {value!r}",
                lambda bid, lbl=label, val=value: writeops.action_set_column(
                    bid, lbl, val, quiet=True
                ),
            )
        )
    if args.batch_clear_column:
        label = args.batch_clear_column
        _check_forbidden_label(label, "--batch-clear-column")
        specs.append(
            (
                "clear-column",
                f"clear #{label.lstrip('#')}",
                lambda bid, lbl=label: writeops.action_clear_column(
                    bid, lbl, quiet=True
                ),
            )
        )
    if args.batch_add_column_value:
        for label, value in args.batch_add_column_value:
            _check_forbidden_label(label, "--batch-add-column-value")
            specs.append(
                (
                    "add-column-value",
                    f"add #{label.lstrip('#')} value {value!r}",
                    lambda bid, lbl=label, val=value: writeops.action_add_column_value(
                        bid, lbl, val, quiet=True
                    ),
                )
            )
    if args.batch_set_title:
        specs.append(
            (
                "set-title",
                f"set title {args.batch_set_title!r}",
                lambda bid, t=args.batch_set_title: writeops.action_set_title(
                    bid, t, quiet=True
                ),
            )
        )
    if args.batch_set_authors:
        names = [n.strip() for n in args.batch_set_authors.split(";") if n.strip()]
        specs.append(
            (
                "set-authors",
                f"set authors to {' & '.join(names)}",
                lambda bid, n=names: writeops.action_set_authors(bid, n, quiet=True),
            )
        )
    if args.batch_set_pubdate:
        specs.append(
            (
                "set-pubdate",
                f"set pubdate {args.batch_set_pubdate!r}",
                lambda bid, v=args.batch_set_pubdate: writeops.action_set_pubdate(
                    bid, v, quiet=True
                ),
            )
        )
    if args.batch_clear_pubdate:
        specs.append(
            (
                "clear-pubdate",
                "clear pubdate",
                lambda bid: writeops.action_set_pubdate(bid, None, quiet=True),
            )
        )
    if args.batch_set_publisher:
        specs.append(
            (
                "set-publisher",
                f"set publisher {args.batch_set_publisher!r}",
                lambda bid, v=args.batch_set_publisher: writeops.action_set_publisher(
                    bid, v, quiet=True
                ),
            )
        )
    if args.batch_clear_publisher:
        specs.append(
            (
                "clear-publisher",
                "clear publisher",
                lambda bid: writeops.action_clear_publisher(bid, quiet=True),
            )
        )
    if args.batch_set_languages:
        specs.append(
            (
                "set-languages",
                f"set languages {args.batch_set_languages}",
                lambda bid, v=args.batch_set_languages: writeops.action_set_languages(
                    bid, v, quiet=True
                ),
            )
        )
    if args.batch_clear_languages:
        specs.append(
            (
                "clear-languages",
                "clear languages",
                lambda bid: writeops.action_clear_languages(bid, quiet=True),
            )
        )
    if args.batch_set_series:
        index = getattr(args, "series_index", None)
        specs.append(
            (
                "set-series",
                f"set series {args.batch_set_series!r}",
                lambda bid, n=args.batch_set_series, i=index: (
                    writeops.action_set_series(bid, n, i, quiet=True)
                ),
            )
        )
    if args.batch_clear_series:
        specs.append(
            (
                "clear-series",
                "clear series",
                lambda bid: writeops.action_clear_series(bid, quiet=True),
            )
        )
    if args.batch_set_identifier:
        id_type, value = args.batch_set_identifier
        specs.append(
            (
                "set-identifier",
                f"set identifier {id_type}={value!r}",
                lambda bid, t=id_type, v=value: writeops.action_set_identifier(
                    bid, t, v, quiet=True
                ),
            )
        )
    if args.batch_clear_identifier:
        specs.append(
            (
                "clear-identifier",
                f"clear identifier {args.batch_clear_identifier!r}",
                lambda bid, t=args.batch_clear_identifier: (
                    writeops.action_clear_identifier(bid, t, quiet=True)
                ),
            )
        )
    if args.batch_set_cover:
        has_cover = writeops.parse_cover_state(args.batch_set_cover)
        specs.append(
            (
                "set-cover",
                f"set cover flag to {has_cover}",
                lambda bid, h=has_cover: writeops.action_set_cover(bid, h, quiet=True),
            )
        )
    if args.batch_remove_format:
        specs.append(
            (
                "remove-format",
                f"remove format {args.batch_remove_format.upper()}",
                lambda bid, f=args.batch_remove_format: writeops.action_remove_format(
                    bid, f, quiet=True
                ),
            )
        )
    return specs


def _has_verbs(args) -> bool:
    return bool(
        args.batch_add_tag
        or args.batch_remove_tag
        or args.batch_clear_tags
        or args.batch_clear_rating
        or args.batch_set_column
        or args.batch_clear_column
        or args.batch_add_column_value
        or args.batch_set_title
        or args.batch_set_authors
        or args.batch_set_pubdate
        or args.batch_clear_pubdate
        or args.batch_set_publisher
        or args.batch_clear_publisher
        or args.batch_set_languages
        or args.batch_clear_languages
        or args.batch_set_series
        or args.batch_clear_series
        or args.batch_set_identifier
        or args.batch_clear_identifier
        or args.batch_set_cover
        or args.batch_remove_format
    )


def _validate(args) -> None:
    """Usage-level validation; every refusal here is exit 2, nothing opened."""
    sources = [s for s in _SOURCES if getattr(args, s, None)]
    if len(sources) > 1:
        names = ", ".join(f"--{s.replace('_', '-')}" for s in sources)
        raise _UsageError(f"exactly one target source is allowed (got {names}).")
    if sources and not _has_verbs(args):
        raise _UsageError("a target source needs at least one --batch-* verb.")
    if not sources and _has_verbs(args):
        raise _UsageError(
            "--batch-* verbs need a target source: --ids, --from-search, "
            "--from-untagged, or --from-manifest."
        )
    singles = [d for d in writeops.SINGLE_BOOK_DESTS if getattr(args, d, None)]
    singles += [d for d in ("add_tag", "remove_tag") if getattr(args, d, None)]
    if singles and (sources or _has_verbs(args)):
        raise _UsageError(
            "single-book write verbs cannot be combined with set mode "
            f"(--{singles[0].replace('_', '-')}); run them separately."
        )
    if args.batch_clear_rating and args.from_manifest is None:
        raise _UsageError(
            "--batch-clear-rating is only legal with --from-manifest: the "
            "library NON-NEGOTIABLES ban bulk rating edits, and the manifest "
            "proves which ids that run imported."
        )
    if getattr(args, "series_index", None) is not None and not args.batch_set_series:
        raise _UsageError(
            "--series-index is only valid together with --batch-set-series in set mode."
        )
    if args.format not in (None, "json"):
        raise _UsageError("set writes support --format json only.")
    if args.commit_per_book and not args.apply:
        raise _UsageError("--commit-per-book is only meaningful with --apply.")


def _make_backup(db_path: str, backup_dir_raw: str) -> Path:
    backup_dir = Path(backup_dir_raw).expanduser()
    resolved = backup_dir.resolve()
    lib_dir = Path(db_path).resolve().parent
    if resolved == lib_dir or resolved.is_relative_to(lib_dir):
        raise _UsageError(
            f"--backup-dir ({resolved}) must sit OUTSIDE the library "
            f"directory ({lib_dir}): a backup beside metadata.db invites "
            "re-import and shares the disk."
        )
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        dest = backup_dir / "metadata.db"
        shutil.copy2(db_path, dest)
    except OSError as e:
        raise _UsageError(f"could not write the backup: {e}") from None
    return dest


def _run_one(results, wdb, book_id: int, label: str, make) -> None:
    try:
        _rc, status = make(book_id)(wdb)
        results.append(
            {
                "id": book_id,
                "verb": label,
                "status": status or "applied",
                "detail": None,
            }
        )
    except Exception as e:
        # Recorded and the loop continues; any failed row rolls the whole
        # batch back at the end, so a half-applied pass can never commit.
        results.append(
            {"id": book_id, "verb": label, "status": "failed", "detail": str(e)}
        )


def _counts(results, label: str) -> dict[str, int]:
    rows = [r for r in results if r["verb"] == label]
    return {
        "applied": sum(1 for r in rows if r["status"] == "applied"),
        "already": sum(1 for r in rows if r["status"] == "already-so"),
        "failed": sum(1 for r in rows if r["status"] == "failed"),
    }


def _report_text(
    args, target: str, specs, results, committed, backup: Path | None, book_count: int
) -> None:
    if args.quiet:
        return
    print(f"Target: {target}")
    if backup is not None:
        print(f"Backed up metadata.db to {backup}")
    for _name, label, _make in specs:
        c = _counts(results, label)
        print(
            f"{label}: {c['applied']} applied, {c['already']} already-so, "
            f"{c['failed']} failed"
        )
    failures = [r for r in results if r["status"] == "failed"]
    if failures:
        print("Failures:", file=sys.stderr)
        for r in failures:
            print(f"  book {r['id']}, {r['verb']}: {r['detail']}", file=sys.stderr)
    applied = sum(1 for r in results if r["status"] == "applied")
    already = sum(1 for r in results if r["status"] == "already-so")
    if committed:
        if args.commit_per_book:
            head = f"Committed per book ({book_count} transactions)"
        else:
            head = "Committed as one transaction"
        print(
            f"{head}: {applied} applied, {already} already-so, {len(failures)} failed."
        )
    elif args.commit_per_book:
        rb = sorted({r["id"] for r in results if not r.get("book_committed", True)})
        kept = book_count - len(rb)
        print(
            f"Rolled back {len(rb)} of {book_count} book(s); "
            f"{kept} committed per book ({len(failures)} failure(s))."
        )
    else:
        print(
            f"Nothing was written: the whole pass rolled back "
            f"({len(failures)} failure(s))."
        )


def _report_json(
    target: str, verbs: list[str], results, committed: bool, dry_run: bool
) -> None:
    print(
        json.dumps(
            {
                "target": target,
                "verbs": verbs,
                "results": results,
                "committed": committed,
                "dry_run": dry_run,
            }
        )
    )


def dispatch_set_write(args, db_path: str) -> int | None:
    """Set-mode entry point, called before dispatch_write so the
    single-book/set combination is rejected before anything executes.
    Returns the process exit code, or None when no set-mode flag is
    present (the caller falls through to single-book writes and reads)."""
    apply_mode = bool(getattr(args, "apply", False))
    dangling = (
        apply_mode
        or getattr(args, "backup_dir", None)
        or getattr(args, "commit_per_book", False)
    )
    if not any(getattr(args, s, None) for s in _SOURCES) and not _has_verbs(args):
        if dangling:
            print(
                "ERROR: --apply/--backup-dir/--commit-per-book need a target "
                "source and at least one --batch-* verb.",
                file=sys.stderr,
            )
            return 2
        return None

    try:
        _validate(args)
        specs = _collect_verbs(args)
    except _UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    from cquarry.db import CalibreDB

    # Read-only resolution first: nothing writable is open yet.
    with CalibreDB(db_path) as db:
        try:
            ids, target = _resolve_targets(args, db)
        except _UsageError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2

    # The label is the per-occurrence identity in every report (two
    # --batch-add-tag flags must stay distinguishable); the machine name
    # in the specs tuples is only internal.
    verbs = list(dict.fromkeys(spec[1] for spec in specs))

    if not apply_mode:
        if getattr(args, "format", None) == "json":
            _report_json(target, verbs, [], False, True)
        elif not args.quiet:
            print(
                "SET WRITE DRY RUN (nothing written; commit with "
                "--apply --backup-dir DIR)"
            )
            print(f"Target: {target}")
            listing = ", ".join(str(i) for i in ids) if ids else "(none)"
            print(f"Resolved {len(ids)} book(s): {listing}")
            print("Planned verbs (each applies to every targeted book):")
            for _name, label, _make in specs:
                print(f"  - {label}")
        return 0

    if _calibre_running():
        print("ERROR: Calibre is running; close it before --apply.", file=sys.stderr)
        return 1
    if not args.backup_dir:
        print(
            "ERROR: --apply requires --backup-dir (back up metadata.db "
            "OUTSIDE the library directory).",
            file=sys.stderr,
        )
        return 2
    try:
        backup = _make_backup(db_path, args.backup_dir)
    except _UsageError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    import sqlite3 as _sqlite3

    from cquarry.write import WritableCalibreDB

    results: list[dict] = []
    committed = False
    rolled_back_books: list[int] = []
    try:
        with WritableCalibreDB(db_path) as wdb:
            if args.commit_per_book:
                # The escape hatch, for real: each book is its own
                # outermost batch, so a huge pass bounds the blast radius
                # of any one failure -- a book whose verbs failed rolls
                # back alone and the pass continues.
                for book_id in ids:
                    start = len(results)
                    try:
                        with wdb.batch():
                            for _name, label, make in specs:
                                _run_one(results, wdb, book_id, label, make)
                            if any(r["status"] == "failed" for r in results[start:]):
                                raise _RollbackNeeded()
                    except _RollbackNeeded:
                        rolled_back_books.append(book_id)
                        for r in results[start:]:
                            r["book_committed"] = False
                            if r["status"] in ("applied", "already-so"):
                                # The book's batch rolled back: those
                                # verbs wrote nothing and must not read
                                # as applied in any report.
                                r["status"] = "rolled_back"
                    else:
                        for r in results[start:]:
                            r["book_committed"] = True
                committed = not rolled_back_books
            else:
                with wdb.batch():
                    for book_id in ids:
                        for _name, label, make in specs:
                            _run_one(results, wdb, book_id, label, make)
                    if any(r["status"] == "failed" for r in results):
                        raise _RollbackNeeded()
                committed = True
    except _RollbackNeeded:
        committed = False
    except _sqlite3.OperationalError as e:
        print(
            f"ERROR: could not acquire the database write lock ({e}). "
            "Is Calibre running? Close it first.",
            file=sys.stderr,
        )
        return 1

    if getattr(args, "format", None) == "json":
        _report_json(target, verbs, results, committed, False)
    else:
        _report_text(args, target, specs, results, committed, backup, len(ids))
    return 0 if committed else 1
