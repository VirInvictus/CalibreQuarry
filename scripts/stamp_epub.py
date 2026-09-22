#!/usr/bin/env python3
"""stamp_epub: pre-stamp EPUB metadata so phase 2 imports real titles.

The EPUB sibling of stamp_pdf.py: the pre-stamp ritual is mandatory for ALL
filetypes (Brandon, 2026-09-22), and until now the EPUB half ran on a
one-off batch driver. Publishers' EPUBs carry metadata that is often
filename-grade, wrong-edition, or someone else's entirely (the 2026-09-22
Mandeville file carried "A Life in Letters" by Mozart wholesale and passed
every screen until it was stamped with its real identity). Stamping first
makes phase 3 start from real metadata.

MECHANICS ONLY. Choosing the VALUES is the agent's informed-judgment step
per the phase-1 skill: read the file's own copyright pages, research
online, triple-check every value against two independent sources. A wrong
embedded ISBN actively pulls the WRONG book's metadata wholesale during
import — this script deliberately offers no web lookup, and none should be
bolted on. One mechanical guard does live here: an --isbn failing its
check digit is refused outright (exit 2), in dry-run and apply alike, so
the guard sits where the write happens instead of in every caller's batch
script.

Field set is FIXED: `--title`, `--authors`, `--publisher`, `--isbn`, all
written by one `ebook-meta` invocation (Calibre's own writer; it rewrites
the OPF in place and REPLACES any existing isbn identifier — that replace
is the correction mechanism for wrong embedded ISBNs, so dc: metadata is
never hand-edited here). Multiple --author flags join with " & "
(ebook-meta's own multi-author separator; note `cquarry --set-authors`
splits on `;` instead — both are documented). Tags/series/comments stay
phase 3.

Verification is where EPUB differs from PDF (learned over three verifier
iterations on live files, 2026-09-22):
  - Title/authors/publisher verify from the `ebook-meta` read-back. The
    author line may render `Name [Sort, Form]`; only the display segment
    before the ` [` bracket is compared against the stamp.
  - ebook-meta's read-back NEVER displays ISBN, so the ISBN verifies by
    reading the OPF directly (zipfile -> the .opf entry -> dc:identifier
    elements) under VALUE EQUALITY: each identifier's value (and its id
    attribute) is normalized by stripping an optional leading
    urn:/isbn: prefix and then every non-alphanumeric character, and the
    stamp verifies when any normalized candidate equals the normalized
    stamp. Live shapes in the wild, all must verify: bare `isbn:978...`,
    `opf:scheme="ISBN"`, namespaced `ns2:scheme="ISBN"`, dashed values,
    and the ISBN carried in the element id. A file that ALREADY carried
    the right ISBN makes --isbn an honest no-op (the writer keeps the
    producer's spelling) — that verifies, never fails. When no --isbn was
    requested, the ISBN check is skipped entirely.

The target list is an exact-path mapping with bijection asserts: every
target must exist on disk, a target named twice is refused, and a stamp
that fails STOPS the run with every not-yet-stamped remainder named (no
staged file is ever left unstamped silently). Originals are copied to the
backup dir before each write.

Dry-run by default; `--apply` writes and REQUIRES `--backup-dir`, which is
refused when it resolves inside the directory holding any target file (a
stray backup beside the file gets imported — the phase-1 cardinal sin).

Exit codes: 0 = dry-run or all stamps verified, 1 = STAMP_FAILED,
2 = setup error.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

RESET = "\033[0m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
GREEN = "\033[1;32m"
DIM = "\033[2m"


def _derive_from_filename(path: Path) -> tuple[str, str]:
    """What Calibre would guess from the bare filename: dash-split Title/Author.

    "Author - Title.epub" -> ("Author", "Title") is what an unstamped bare
    file imports as. A preview aid only — the skill's rule is that filename
    guesses are exactly what stamping replaces.
    """
    stem = path.stem.strip()
    if " - " in stem:
        title, _, author = stem.partition(" - ")
        return title.strip(), author.strip()
    return stem, ""


def _run_ebook_meta(args: list[str]) -> subprocess.CompletedProcess:
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


def _display_author(rendered: str) -> str:
    """ebook-meta may render the author as `Display [Sort, Form]`; the stamp
    comparison targets only the display segment before the ` [` bracket."""
    return (rendered or "").partition(" [")[0].strip()


def _normalize_identifier(raw: str) -> str:
    """VALUE EQUALITY form for dc:identifier candidates: drop an optional
    leading urn:/isbn: (also the isbn_... element-id spelling), then every
    non-alphanumeric character, case-folded. Dashed values, `isbn:978...`
    values and `id="isbn_978..."` attributes all collapse onto the bare
    number."""
    s = (raw or "").strip().lower()
    s = re.sub(r"^(urn:)?isbn[:_ -]?", "", s)
    return re.sub(r"[^0-9a-z]", "", s).upper()


def _opf_identifier_candidates(path: Path) -> list[str]:
    """Every dc:identifier candidate string in the EPUB's OPF: the element
    text of each identifier plus its id attribute (the `id="isbn_978..."`
    live shape carries the ISBN there, not in the text). Raises on a
    missing container/OPF entry or malformed XML; the caller treats that
    as an unverifiable ISBN."""
    with zipfile.ZipFile(path) as z:
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_name = None
        for el in container.iter():
            if el.tag.split("}")[-1] == "rootfile" and el.get("full-path"):
                opf_name = el.get("full-path")
                break
        if opf_name is None:
            raise ValueError(f"no rootfile in {path}/META-INF/container.xml")
        opf = ET.fromstring(z.read(opf_name))
    candidates: list[str] = []
    for el in opf.iter():
        if el.tag.split("}")[-1] != "identifier":
            continue
        if el.text and el.text.strip():
            candidates.append(el.text.strip())
        if el.get("id"):
            candidates.append(el.get("id"))
    return candidates


def _check_files(files: list[Path]) -> int | None:
    seen: set[str] = set()
    for f in files:
        if not f.is_file():
            print(f"ERROR: no such file: {f}", file=sys.stderr)
            return 2
        if f.suffix.lower() != ".epub":
            print(
                f"ERROR: not an EPUB: {f} (stamp_pdf.py owns PDFs)",
                file=sys.stderr,
            )
            return 2
        key = str(f.resolve())
        if key in seen:
            # Bijection: one file, one stamp. A repeated target would
            # double-stamp and lie about how many files the batch holds.
            print(f"ERROR: target named twice: {f}", file=sys.stderr)
            return 2
        seen.add(key)
    return None


def _check_isbn(isbn: str) -> int | None:
    """The tool-boundary checksum guard (stamp_pdf 3.52.0's, same shape): an
    ISBN failing its check digit (10- or 13-form, separators tolerated) is
    refused before anything stamps. A stamped wrong ISBN pulls the wrong
    book's metadata wholesale on import."""
    from cquarry.helpers import isbn_check_digit_is_valid

    if isbn and not isbn_check_digit_is_valid(isbn):
        print(
            f"ERROR: --isbn {isbn!r} fails its check digit; refusing to stamp "
            "garbage. Re-verify the number against the book itself (a wrong "
            "ISBN pulls in the wrong book's metadata on import).",
            file=sys.stderr,
        )
        return 2
    return None


def _check_tools() -> int | None:
    # Only the write path needs the external CLI: a dry-run previews from
    # arguments alone and must work on machines without calibre.
    if shutil.which("ebook-meta") is None:
        print("ERROR: ebook-meta not found on PATH.", file=sys.stderr)
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


def _build_ebook_meta_args(
    title: str, authors: list[str], publisher: str, isbn: str
) -> list[str]:
    """The fixed field set, one ebook-meta invocation. Joining multi-authors
    with ' & ' (ebook-meta's own separator — the OPPOSITE of `cquarry
    --set-authors`, which splits on ';'). A field the caller leaves off is
    not passed at all, so the writer leaves it alone."""
    args = ["ebook-meta"]
    if title:
        args += ["--title", title]
    if authors:
        args += ["--authors", " & ".join(authors)]
    if publisher:
        args += ["--publisher", publisher]
    if isbn:
        args += ["--isbn", isbn]
    return args


def _verify(
    readback: dict[str, str],
    opf_candidates: list[str],
    title: str,
    authors: list[str],
    publisher: str,
    isbn: str,
) -> list[str]:
    """Requested values vs the written file. Title/authors/publisher come
    from Calibre's own reader (author compared as the display segment); the
    ISBN comes from the OPF's dc:identifier elements under VALUE EQUALITY.
    Returns the failed fields."""
    failed: list[str] = []
    joined = " & ".join(authors)
    if title and readback.get("title") != title:
        failed.append("title")
    if authors and _display_author(readback.get("author(s)", "")) != joined:
        failed.append("author")
    if publisher and readback.get("publisher") != publisher:
        failed.append("publisher")
    if isbn:
        want = _normalize_identifier(isbn)
        if not any(_normalize_identifier(c) == want for c in opf_candidates):
            failed.append("isbn")
    return failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pre-stamp EPUB metadata via ebook-meta (dry-run by "
        "default). Mechanics only: choosing the values is the agent's "
        "research step per the phase-1 skill."
    )
    parser.add_argument("files", nargs="+", help="EPUB files to stamp")
    parser.add_argument("--title", default="", help="embedded title (OPF dc:title)")
    parser.add_argument(
        "--author",
        action="append",
        default=[],
        help="author; repeat for multiple, joined with ' & ' (ebook-meta's "
        "separator; note `cquarry --set-authors` splits on ';' instead)",
    )
    parser.add_argument(
        "--publisher", default="", help="OPF dc:publisher (read by Calibre on import)"
    )
    parser.add_argument(
        "--isbn",
        default="",
        help="REPLACES any existing isbn identifier in the OPF — the "
        "correction mechanism for a wrong embedded ISBN. Leave OFF unless "
        "confirmed (a wrong ISBN pulls in the wrong book's metadata on import).",
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
    parser.add_argument(
        "--json",
        metavar="FILE",
        default="",
        help="also write the machine report ({files: [{file, status, ...}]})",
    )
    args = parser.parse_args()

    files = [Path(f).expanduser() for f in args.files]
    if rc := _check_files(files):
        return rc
    if rc := _check_isbn(args.isbn):
        return rc
    if not (args.title or args.author or args.publisher or args.isbn):
        print(
            "ERROR: nothing to stamp (no --title/--author/--publisher/--isbn).",
            file=sys.stderr,
        )
        return 2

    stamp = {
        "Title": args.title,
        "Author": " & ".join(args.author),
        "Publisher": args.publisher,
        "ISBN": args.isbn,
    }

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

    reports: list[dict] = []
    failed_any = False
    for index, f in enumerate(files):
        guess_title, guess_author = _derive_from_filename(f)
        print(f"{f.name}")
        print(
            f"  {DIM}filename import would be: Title {guess_title!r}, Author {guess_author!r}{RESET}"
        )
        for label, value in stamp.items():
            print(f"  {label:10}: {'(unchanged)' if not value else value!r}")

        report: dict = {
            "file": str(f),
            "status": "dry_run",
            "failed_fields": [],
            "error": None,
        }
        reports.append(report)

        if not args.apply:
            print(f"  {DIM}dry run — nothing written{RESET}")
            continue

        backup = backup_dir / f.name
        shutil.copy2(f, backup)
        write_args = _build_ebook_meta_args(
            args.title, args.author, args.publisher, args.isbn
        )
        write_args.append(str(f))
        proc = _run_ebook_meta(write_args)
        if proc.returncode != 0:
            print(
                f"  {RED}STAMP_FAILED (ebook-meta exited {proc.returncode}): {proc.stderr.strip()}{RESET}"
            )
            report["status"] = "stamp_failed"
            report["error"] = proc.stderr.strip()
            failed_any = True
            break

        readback = _read_ebook_meta(f)
        try:
            candidates = _opf_identifier_candidates(f)
        except Exception as e:  # malformed/absent OPF: the ISBN cannot verify
            candidates = []
            if args.isbn:
                report["error"] = f"OPF unreadable for ISBN verify: {e}"
        failed_fields = _verify(
            readback, candidates, args.title, args.author, args.publisher, args.isbn
        )
        if failed_fields:
            print(
                f"  {RED}STAMP_FAILED: read-back disagrees on: {', '.join(failed_fields)}{RESET}"
            )
            report["status"] = "stamp_failed"
            report["failed_fields"] = failed_fields
            failed_any = True
            break
        report["status"] = "stamped_verified"
        print(f"  {GREEN}stamped and verified{RESET} (backup: {backup})")

    if failed_any:
        rest = files[index + 1 :]
        if rest:
            print(f"\n{RED}Stopped: {len(rest)} file(s) left UNSTAMPED:{RESET}")
            for f in rest:
                print(f"  {RED}left unstamped: {f}{RESET}")
                reports.append(
                    {
                        "file": str(f),
                        "status": "left_unstamped",
                        "failed_fields": [],
                        "error": None,
                    }
                )
        print(
            f"\n{RED}One or more stamps FAILED verification; do not re-fight — "
            "note the file and let phase 3 fix the field in SQL.{RESET}"
        )
    elif not args.apply:
        print(f"\n{YELLOW}Dry run: pass --apply --backup-dir DIR to write.{RESET}")

    if args.json:
        payload = {
            "apply": args.apply,
            "backup_dir": str(backup_dir) if backup_dir else None,
            "files": reports,
        }
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
    return 1 if failed_any else 0


if __name__ == "__main__":
    sys.exit(main())
