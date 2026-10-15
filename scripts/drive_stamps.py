#!/usr/bin/env python3
"""drive_stamps: the wave stamp driver (issue #29's tool-owned shape).

Loads a researched stamps map (filename basename -> {title, authors,
publisher, isbn, cut, notes}), coverage-asserts it against one target
directory (map keys minus discards == ebook files on disk, the diff
named both ways, the loaded size printed before anything runs), then
dispatches each file to stamp_epub.py or stamp_pdf.py by extension with
--apply --backup-dir, continuing past failures and naming the remainder.
Results land in stamp_results.json beside the stamps file (the
association the oct-15 hand-rolled driver got wrong when it wrote the
oct-14 wave's directory instead).

Retires the five hand-rolled per-wave drivers (oct11 through oct15):
adapting a prior wave's copy by string-replace silently no-op'd twice on
2026-10-15 and the adapted driver ran twice against the WRONG WAVE's
stamp table; only its hand-written coverage assert kept both runs
harmless. This driver runs that assert every time, against the ACTUAL
target directory passed, before any spawn -- pointing it at the wrong
table kills the run before any write, by construction.

Field contract (the shape the five copies converged on):
- "NONE" (or empty/absent) means the flag is OMITTED -- the stamp tools
  write exactly what they are passed, so a literal-NONE publisher would
  be a phase-3 defect;
- authors is a list, passed as repeated --author flags (the tools join
  with " & ");
- cut and notes are carried for the record and never stamped (cut names
  the byline entries removed at research time; stamp --author REPLACES
  the author list, so the cut is already applied in the authors list);
- a row with NO stampable field is a research-product bug and refuses
  the whole run (exit 2) before anything runs.

Stamps EPUBs and PDFs only. MOBI/AZW3/DJVU entries ride in the map for
the coverage assert but are NOT stamped here (the phase-1 skill routes
them by hand: ebook-meta --authors for MOBI, djvused + the filename
rename for DJVU); they are named skipped in the report and the summary.

Dry-run by default: the stamp tools run without --apply and one line
per file prints what would be dispatched; no stamp is written (the
results record still lands, so a dry review leaves its own trail).
Exit codes: 0 = every dispatched file clean, 1 = any stamp failed (the
remainder named), 2 = usage trouble (bad stamps table, coverage assert
fired, backup dir inside the target, results path inside the target).
"""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from version_guard import announce
except ImportError:  # a lone deployed copy: no siblings beside it

    def announce(stream=None):
        pass


#: Ext -> the stamp tool that owns it. Everything else in the map is a
#: named skip, never a silent one.
_STAMP_TOOLS = {".epub": "stamp_epub.py", ".pdf": "stamp_pdf.py"}

#: The on-disk enumeration set (the hand-rolled drivers' set: the screen's
#: EBOOK_EXTENSIONS plus DJVU). Top-level files only, like every wave
#: surface: a recursive walk would sweep a prior phase's _quarantine/ in.
_EBOOK_EXTS = (".epub", ".pdf", ".mobi", ".azw3", ".djvu")

#: The known stamp-row fields. title/authors/publisher/isbn stamp; cut and
#: notes ride for the record. An unknown field is a warning, never a
#: silent drop.
_KNOWN_FIELDS = frozenset({"title", "authors", "publisher", "isbn", "cut", "notes"})

_PER_FILE_TIMEOUT = 300


def _absent(value: object) -> bool:
    """The NONE family: a stamp value that means the flag is omitted.
    Case-insensitive on the word, so a Python-flavored "None" or a
    lowercase "none" in a hand-edited table cannot stamp as a literal
    title or publisher (the phase-3 defect class)."""
    return (
        value is None
        or value == ""
        or (isinstance(value, str) and value.casefold() == "none")
    )


def _load_stamps(path: Path) -> dict:
    """Read the stamps map. Anything but a JSON object of objects is a
    usage error (exit 2): a wrong-shaped table must stop before the
    coverage assert can even be computed."""
    try:
        stamps = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: stamps file unreadable: {e}", file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(stamps, dict):
        print(
            f"ERROR: stamps file is {type(stamps).__name__}, not an object of "
            "filename -> stamp rows.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return stamps


def _row_problems(fname: str, row: object) -> list[str]:
    """The plan-time validation of one stamp row. Strictness here is the
    incident protection: a malformed research product stops the run
    before any spawn, naming the rows."""
    problems: list[str] = []
    if not isinstance(row, dict):
        return [f"{fname}: row is {type(row).__name__}, not an object"]
    unknown = sorted(set(row) - _KNOWN_FIELDS)
    if unknown:
        print(
            f"WARNING: {fname}: unknown stamp field(s) ignored: {', '.join(unknown)}",
            file=sys.stderr,
        )
    present = [f for f in ("title", "publisher", "isbn") if not _absent(row.get(f))]
    authors = row.get("authors")
    if isinstance(authors, list) and authors:
        present.append("authors")
    if not present:
        return [
            f"{fname}: no stampable field (title/authors/publisher/isbn all "
            "NONE, empty, or absent)"
        ]
    if authors is not None and not _absent(authors):
        if not isinstance(authors, list) or not all(
            isinstance(a, str) and a.strip() for a in authors
        ):
            problems.append(f"{fname}: authors must be a list of non-empty strings")
    for field in ("title", "publisher", "isbn"):
        value = row.get(field)
        if value is not None and not _absent(value) and not isinstance(value, str):
            problems.append(f"{fname}: {field} must be a string")
    return problems


def _stamp_args(row: dict) -> list[str]:
    """The flag set for one row: NONE-family values OMIT the flag (the
    tools write exactly what they are passed)."""
    args: list[str] = []
    if not _absent(row.get("title")):
        args += ["--title", str(row["title"])]
    for a in row.get("authors") or []:
        args += ["--author", str(a)]
    if not _absent(row.get("publisher")):
        args += ["--publisher", str(row["publisher"])]
    if not _absent(row.get("isbn")):
        args += ["--isbn", str(row["isbn"])]
    return args


def _on_disk(target: Path) -> set[str]:
    """The target directory's top-level ebook files, by basename."""
    return {
        f
        for f in os.listdir(target)
        if f.lower().endswith(_EBOOK_EXTS) and (target / f).is_file()
    }


def _spawn(cmd: list[str]) -> tuple[int | None, str]:
    """One stamp-tool invocation, decoded with errors="replace" (the
    tree-wide decode blanket). Returns (returncode, first diagnostic)."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_PER_FILE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return None, f"timed out after {_PER_FILE_TIMEOUT}s"
    detail = (proc.stderr or proc.stdout or "").strip().splitlines()
    return proc.returncode, (detail[0][:160] if detail else "")


def _outside(child: Path, parent: Path) -> bool:
    """True when child is not parent or inside it (the backup-dir rule).
    Fail-closed: a path that cannot resolve is treated as inside."""
    try:
        child_resolved = child.resolve()
        parent_resolved = parent.resolve()
    except OSError:
        return False
    return (
        parent_resolved not in child_resolved.parents
        and child_resolved != parent_resolved
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drive stamp_epub/stamp_pdf over a researched stamps map "
        "(coverage-asserted against the target directory first; dry-run by "
        "default). Mechanics only: choosing the values is the research step "
        "per the phase-1 skill."
    )
    parser.add_argument("stamps", help="the stamps JSON map (filename -> stamp row)")
    parser.add_argument("target", help="the directory holding the files it names")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the stamps (default: dry-run preview)",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="REQUIRED with --apply: copy each file here before writing; must "
        "be OUTSIDE the target directory (the stamp tools' rule)",
    )
    parser.add_argument(
        "--format",
        choices=("epub", "pdf"),
        default="",
        help="dispatch only this format (the pdf/epub split); the coverage "
        "assert still covers the whole map and directory",
    )
    parser.add_argument(
        "--discard",
        action="append",
        default=[],
        metavar="NAME",
        help="map key excluded from the coverage (a quarantined or deleted "
        "file); repeatable, and a discarded name still on disk is an error",
    )
    parser.add_argument(
        "--results",
        default="",
        help="the results JSON path (default: stamp_results.json beside the "
        "stamps file; refused inside the target directory)",
    )
    args = parser.parse_args()

    announce()

    stamps_path = Path(args.stamps).expanduser()
    target = Path(args.target).expanduser()
    if not target.is_dir():
        print(f"ERROR: target directory not found: {target}", file=sys.stderr)
        return 2
    stamps = _load_stamps(stamps_path)

    problems: list[str] = []
    for fname, row in stamps.items():
        problems.extend(_row_problems(str(fname), row))
    if problems:
        print(
            "ERROR: bad stamp rows (fix the research product, then re-run):",
            file=sys.stderr,
        )
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2

    discards = set(args.discard)
    for name in sorted(discards - set(stamps)):
        print(f"WARNING: --discard {name!r} is not in the stamps map", file=sys.stderr)
    planned = {k: v for k, v in stamps.items() if k not in discards}

    on_disk = _on_disk(target)
    missing = set(planned) - on_disk
    extra = on_disk - set(planned)
    if missing or extra:
        print("ERROR: coverage assert FAILED:", file=sys.stderr)
        if missing:
            print(
                f"  stamp map names {len(missing)} file(s) not on disk:",
                file=sys.stderr,
            )
            for f in sorted(missing):
                print(f"    {f}", file=sys.stderr)
        if extra:
            print(
                f"  {len(extra)} file(s) on disk with no stamp:",
                file=sys.stderr,
            )
            for f in sorted(extra):
                print(f"    {f}", file=sys.stderr)
        return 2
    discard_note = f", {len(discards)} discarded" if discards else ""
    print(
        f"coverage assert OK: {len(planned)} stamps{discard_note} "
        f"== {len(on_disk)} files on disk",
        flush=True,
    )

    backup_dir: Path | None = None
    if args.apply:
        if not args.backup_dir:
            print(
                "ERROR: --apply requires --backup-dir (back up originals OUTSIDE "
                "the target directory).",
                file=sys.stderr,
            )
            return 2
        backup_dir = Path(args.backup_dir).expanduser()
        if not _outside(backup_dir, target):
            print(
                "ERROR: --backup-dir must sit OUTSIDE the target directory "
                "(the stamp tools' rule).",
                file=sys.stderr,
            )
            return 2
        backup_dir.mkdir(parents=True, exist_ok=True)
    elif args.backup_dir:
        # A dry preview with a backup dir shows the command the apply
        # run would carry, not a literal "None".
        backup_dir = Path(args.backup_dir).expanduser()

    results_path = (
        Path(args.results).expanduser()
        if args.results
        else stamps_path.resolve().parent / "stamp_results.json"
    )
    if not _outside(results_path, target):
        print(
            "ERROR: the results file must not land inside the target directory "
            "(nothing writes into the wave tree).",
            file=sys.stderr,
        )
        return 2
    if results_path.resolve() == stamps_path.resolve():
        print(
            "ERROR: the results file must not be the stamps file itself.",
            file=sys.stderr,
        )
        return 2

    scripts_dir = Path(__file__).parent
    done: list[str] = []
    failed: list[dict] = []
    skipped: list[dict] = []
    total = len(planned)
    for index, fname in enumerate(sorted(planned), 1):
        row = planned[fname]
        ext = os.path.splitext(fname)[1].lower()
        tool = _STAMP_TOOLS.get(ext)
        path = str(target / fname)
        if tool is None:
            skipped.append(
                {
                    "file": fname,
                    "reason": f"unstampable format ({ext}; the phase-1 skill "
                    "routes it by hand)",
                }
            )
            print(
                f"[{index}/{total}] SKIP (unstampable format {ext}) {fname[:56]}",
                flush=True,
            )
            continue
        if args.format and ext != f".{args.format}":
            skipped.append({"file": fname, "reason": f"outside --format {args.format}"})
            print(
                f"[{index}/{total}] SKIP (outside --format {args.format}) {fname[:56]}",
                flush=True,
            )
            continue
        flags = _stamp_args(row)
        cmd = [sys.executable, str(scripts_dir / tool), path, *flags]
        if backup_dir is not None:
            cmd += ["--backup-dir", str(backup_dir)]
        if args.apply:
            cmd.append("--apply")
        mode = "APPLY" if args.apply else "DRY"
        print(f"[{index}/{total}] {mode} {tool} {fname[:56]}", flush=True)
        print(f"    {' '.join(flags)}", flush=True)
        rc, detail = _spawn(cmd)
        if rc == 0:
            done.append(fname)
        else:
            failed.append({"file": fname, "detail": detail})
            print(
                f"[{index}/{total}] FAIL ({tool} rc {rc}) {fname[:56]} :: {detail}",
                flush=True,
            )

    report = {
        "stamps_file": str(stamps_path),
        "target_dir": str(target),
        "backup_dir": str(backup_dir) if backup_dir else None,
        "apply": bool(args.apply),
        "format_filter": args.format or None,
        "loaded": total,
        "done": done,
        "failed": failed,
        "skipped": skipped,
    }
    try:
        results_path.write_text(
            json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        # The stamps (if any) already ran; a dead results path loses the
        # record, never the summary or the exit contract (setup trouble
        # is a named error, never a traceback).
        print(f"ERROR: could not write the results file: {e}", file=sys.stderr)
    suffix = f", {len(skipped)} skipped" if skipped else ""
    results_note = f"; results: {results_path}" if results_path.exists() else ""
    print(
        f"\n{'stamped' if args.apply else 'previewed'} "
        f"{len(done)}, FAILED {len(failed)}{suffix}{results_note}",
        flush=True,
    )
    for entry in failed:
        print(f"  REMAINDER: {entry['file']}", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
