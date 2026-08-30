#!/usr/bin/env python3
"""stamp_pdf: pre-stamp PDF metadata so phase 2 imports real titles.

Obscure PDFs (TTRPG modules, scans, indie releases) often carry NO embedded
metadata, so Calibre imports the filename as the title and its dash-remainder
as the author ("5E - Wonderland.pdf" imports as Title "5E", Author
"Wonderland"). Stamping first makes phase 3 start from real metadata.

MECHANICS ONLY. Choosing the VALUES is the agent's informed-judgment step per
the phase-1 skill: read the file's own credits/copyright pages
(`pdftotext -f 1 -l 4`), research online, triple-check every value against
two independent sources. A wrong embedded ISBN actively pulls the WRONG
book's metadata wholesale during import — this script deliberately offers no
web lookup, and none should be bolted on.

Field set is FIXED (the traps are already paid for):
  -Title + -XMP-dc:Title, -Author + -XMP-dc:Creator,
  -XMP-dc:Publisher for publisher, and ISBN via -Keywords="isbn:..." —
  NEVER -XMP-dc:Identifier, which Calibre maps to a bogus `doi`.
Multiple --author flags join with " & " (Calibre's separator; note the
`cquarry --set-authors` CLI splits on `;` instead — both are documented).
Verification reads back with `ebook-meta` (Calibre's own reader), never an
exiftool round-trip. On write-reports-success-but-readback-disagrees (the
stubborn-XMP class) the script prints STAMP_FAILED, exits nonzero, and stops:
do not keep fighting; phase 3 fixes the field in SQL instead.

Dry-run by default; `--apply` writes and REQUIRES `--backup-dir`, which is
refused when it resolves inside the directory holding any target file (a
stray backup beside the file gets imported — the phase-1 cardinal sin).

Exit codes: 0 = dry-run or all stamps verified, 1 = STAMP_FAILED,
2 = setup error.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

RESET = "\033[0m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
GREEN = "\033[1;32m"
DIM = "\033[2m"


def _derive_from_filename(path: Path) -> tuple[str, str]:
    """What Calibre would guess from the bare filename: dash-split Title/Author.

    "5E - Wonderland.pdf" -> ("5E", "Wonderland"). A preview aid only — the
    skill's rule is that filename guesses are exactly what stamping replaces.
    """
    stem = path.stem.strip()
    if " - " in stem:
        title, _, author = stem.partition(" - ")
        return title.strip(), author.strip()
    return stem, ""


def _run_exiftool(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=300)


def _read_ebook_meta(path: Path) -> dict[str, str]:
    """ebook-meta read-back, parsed the same tolerant way for verification."""
    proc = subprocess.run(
        ["ebook-meta", str(path)], capture_output=True, text=True, timeout=120
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def _check_files(files: list[Path]) -> int | None:
    for f in files:
        if not f.is_file():
            print(f"ERROR: no such file: {f}", file=sys.stderr)
            return 2
        if f.suffix.lower() != ".pdf":
            # The skill: do NOT stamp EPUBs (Calibre reads their OPF natively)
            # or anything that is not the PDF this script exists for.
            print(
                f"ERROR: not a PDF: {f} (EPUBs are read natively; never stamp them)",
                file=sys.stderr,
            )
            return 2
    return None


def _check_tools() -> int | None:
    # Only the write path needs the external CLIs: a dry-run previews from
    # arguments alone and must work on machines without exiftool.
    for tool in ("exiftool", "ebook-meta"):
        if shutil.which(tool) is None:
            print(f"ERROR: {tool} not found on PATH.", file=sys.stderr)
            return 2
    return None


def _validate_backup_dir(backup_dir: Path, files: list[Path]) -> int | None:
    """--backup-dir is required for --apply and must sit outside every target
    directory: a backup parked beside the file gets imported."""
    try:
        resolved = backup_dir.expanduser().resolve()
    except OSError as e:
        print(f"ERROR: unusable --backup-dir: {e}", file=sys.stderr)
        return 2
    for f in files:
        target_dir = f.resolve().parent
        if resolved == target_dir or target_dir in resolved.parents:
            print(
                f"ERROR: --backup-dir ({resolved}) must be OUTSIDE the directory "
                f"holding the target files ({target_dir}) — a backup beside the "
                f"file gets imported.",
                file=sys.stderr,
            )
            return 2
    return None


def _build_exiftool_args(
    title: str, authors: list[str], publisher: str, isbn: str
) -> list[str]:
    """The fixed field set. Joining multi-authors with ' & ' (Calibre's
    separator — the OPPOSITE of `cquarry --set-authors`, which splits on ';')."""
    args = ["exiftool", "-m", "-overwrite_original"]
    if title:
        args += [f"-Title={title}", f"-XMP-dc:Title={title}"]
    if authors:
        joined = " & ".join(authors)
        args += [f"-Author={joined}", f"-XMP-dc:Creator={joined}"]
    if publisher:
        args += [f"-XMP-dc:Publisher={publisher}"]
    if isbn:
        args += [f"-Keywords=isbn:{isbn}"]
    return args


def _verify(
    readback: dict[str, str], title: str, authors: list[str], publisher: str, isbn: str
) -> list[str]:
    """Requested values vs Calibre's own reader. Returns the failed fields."""
    failed: list[str] = []
    joined = " & ".join(authors)
    if title and readback.get("title") != title:
        failed.append("title")
    if authors and readback.get("author(s)") != joined:
        failed.append("author")
    if publisher and readback.get("publisher") != publisher:
        failed.append("publisher")
    if isbn:
        m = re.search(r"isbn:(\S+)", readback.get("identifiers") or "")
        if not m or m.group(1) != isbn:
            failed.append("isbn")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-stamp PDF metadata (dry-run by default). Mechanics only: "
        "choosing the values is the agent's research step per the phase-1 skill."
    )
    parser.add_argument("files", nargs="+", help="PDF files to stamp")
    parser.add_argument(
        "--title", default="", help="embedded Title (also XMP-dc:Title)"
    )
    parser.add_argument(
        "--author",
        action="append",
        default=[],
        help="author; repeat for multiple, joined with ' & ' (Calibre's separator; "
        "note `cquarry --set-authors` splits on ';' instead)",
    )
    parser.add_argument(
        "--publisher", default="", help="XMP-dc:Publisher (read by Calibre on import)"
    )
    parser.add_argument(
        "--isbn",
        default="",
        help="embedded as -Keywords='isbn:...' — NEVER -XMP-dc:Identifier, which "
        "Calibre maps to a bogus doi. Leave OFF unless confirmed (a wrong ISBN "
        "pulls in the wrong book's metadata on import).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the stamp (default: dry-run preview)",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="REQUIRED with --apply: copy each file here before writing; must be "
        "OUTSIDE every target file's directory",
    )
    args = parser.parse_args()

    files = [Path(f).expanduser() for f in args.files]
    if rc := _check_files(files):
        return rc
    if not (args.title or args.author or args.publisher or args.isbn):
        print(
            "ERROR: nothing to stamp (no --title/--author/--publisher/--isbn).",
            file=sys.stderr,
        )
        return 2

    backup_dir: Path | None = None
    if args.apply:
        if not args.backup_dir:
            print(
                "ERROR: --apply requires --backup-dir (back up originals OUTSIDE the batch).",
                file=sys.stderr,
            )
            return 2
        backup_dir = Path(args.backup_dir).expanduser()
        if rc := _validate_backup_dir(backup_dir, files):
            return rc
        if rc := _check_tools():
            return rc
        backup_dir.mkdir(parents=True, exist_ok=True)

    failed_any = False
    for f in files:
        guess_title, guess_author = _derive_from_filename(f)
        stamp = {
            "Title": args.title,
            "Author": " & ".join(args.author),
            "Publisher": args.publisher,
            "ISBN": args.isbn,
        }
        print(f"{f.name}")
        print(
            f"  {DIM}filename import would be: Title {guess_title!r}, Author {guess_author!r}{RESET}"
        )
        for label, value in stamp.items():
            print(f"  {label:10}: {'(unchanged)' if not value else value!r}")

        if not args.apply:
            print(f"  {DIM}dry run — nothing written{RESET}")
            continue

        backup = backup_dir / f.name
        shutil.copy2(f, backup)
        exif_args = _build_exiftool_args(
            args.title, args.author, args.publisher, args.isbn
        )
        exif_args.append(str(f))
        proc = _run_exiftool(exif_args)
        if proc.returncode != 0:
            print(
                f"  {RED}STAMP_FAILED (exiftool exited {proc.returncode}): {proc.stderr.strip()}{RESET}"
            )
            failed_any = True
            continue

        readback = _read_ebook_meta(f)
        failed_fields = _verify(
            readback, args.title, args.author, args.publisher, args.isbn
        )
        if failed_fields:
            # The stubborn-XMP class: exiftool claims success but Calibre's own
            # reader disagrees. Do not keep fighting — phase 3 fixes the field
            # in SQL instead.
            print(
                f"  {RED}STAMP_FAILED: read-back disagrees on: {', '.join(failed_fields)}{RESET}"
            )
            failed_any = True
        else:
            print(f"  {GREEN}stamped and verified{RESET} (backup: {backup})")

    if failed_any:
        print(
            f"\n{RED}One or more stamps FAILED verification; do not re-fight a stubborn XMP store — phase 3 fixes the field in SQL.{RESET}"
        )
        return 1
    if not args.apply:
        print(f"\n{YELLOW}Dry run: pass --apply --backup-dir DIR to write.{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
