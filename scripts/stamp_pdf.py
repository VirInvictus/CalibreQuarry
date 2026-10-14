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
web lookup, and none should be bolted on. One mechanical guard does live
here: an --isbn failing its check digit is refused outright (exit 2), in
dry-run and apply alike, so the guard sits where the write happens instead
of in every caller's batch script.

Field set is FIXED (the traps are already paid for):
  -Title + -XMP-dc:Title, -Author + -XMP-dc:Creator,
  -XMP-dc:Publisher for publisher, and ISBN via -Keywords="isbn:..." —
  NEVER -XMP-dc:Identifier, which Calibre maps to a bogus `doi`.
Multiple --author flags join with " & " (Calibre's separator; note the
`cquarry --set-authors` CLI splits on `;` instead — both are documented).
Verification reads back with `ebook-meta` (Calibre's own reader), never an
exiftool round-trip — except on disagreement. Since 2026-10-14 (issue #22)
a read-back mismatch is ARBITRATED before failure: the disputed fields are
re-read through exiftool (the tags the stamp actually writes), and a field
exiftool holds is a blind ebook-meta read (the 2026-10-10..14 class:
Learning Blender, Love unto crypt, Martin Guerre, The Sweet Science — the
stamp had landed, only the reader was blind), recorded as verified. Only
a field exiftool ALSO fails to hold is the stubborn-XMP class: STAMP_FAILED,
exit nonzero, do not keep fighting; phase 3 fixes the field in SQL instead.

Two rc != 0 write-failure shapes recover mechanically, each exactly once
(shared recovery: a `qpdf --replace-input` rebuild whose page count must
be preserved — a changed count restores the backup copy — then ONE stamp
retry): exiftool cannot PARSE the file at all (a Perl-space error;
2026-09-26: "Can't find Root object" off a malformed catalog Names array,
on a file `qpdf --check` passes), and exiftool refuses the WRITE over a
damaged xref (2026-10-14, issue #21: "Error: Invalid xref table" on a file
qpdf documents as "xref not found ... attempting to reconstruct"). A retry
that still fails, and every other rc != 0 shape, stays STAMP_FAILED
immediately.

A PDF Calibre has ever produced or touched also carries an XMP
`calibre:author_sort`. Left in place it breaks the stamp twice: ebook-meta
renders the author as `Display [Sort]` (failing verify), and Calibre's import
honors the embedded sort verbatim instead of recomputing from the stamped
authors. When the stamp sets authors and the file carries one, a follow-up
`ebook-meta` pass with `--author-sort ""` (empty is null, so Calibre writes no
sort of its own) rewrites the XMP packet without the calibre extensions; the
docinfo Keywords (the ISBN) survive that rewrite. Detection is
`exiftool -s3 -Author_sort`, the raw XMP property.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from version_guard import announce
except ImportError:  # a lone deployed copy: no siblings beside it

    def announce(stream=None):
        pass


RESET = "\033[0m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
GREEN = "\033[1;32m"
DIM = "\033[2m"


def _derive_from_filename(path: Path) -> tuple[str, str]:
    """What Calibre would guess from the bare filename: dash-split Title/Author.

    "5E - Wonderland.pdf" -> ("5E", "Wonderland"). A preview aid only — the
    skill's rule is that filename guesses are exactly what stamping replaces.

    Note the direction (roadmap :902, decided 2026-09-10): this mirrors
    Calibre's import fallback, "Title - Author", which is the OPPOSITE of
    run.py's stamp parser ("Author - Title", the direction the observed
    libgen.li corpus actually uses). The opposite is the point: this preview
    shows the wrong guess an unstamped import would make.
    """
    stem = path.stem.strip()
    if " - " in stem:
        title, _, author = stem.partition(" - ")
        return title.strip(), author.strip()
    return stem, ""


def _run_exiftool(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=300
    )


def _run_ebook_meta(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=300
    )


#: exiftool PARSE-crash signatures: a Perl-space error means it could not
#: even READ the file (2026-09-26: "Can't find Root object" off a malformed
#: catalog Names array, on a file `qpdf --check` passes). Deliberately not
#: the write-succeeded shape: a read-back disagreement is the stubborn-XMP
#: class and is never retried.
_PARSE_CRASH_RE = re.compile(
    r"\bCan't (?:call method|locate|find)\b"
    r"|\bNot a (?:HASH|ARRAY|CODE) reference\b"
    r"|\bUse of uninitialized value\b"
    r"|\bsyntax error\b"
)


def _run_qpdf(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, errors="replace", timeout=300
    )


def _crash_detail(proc: subprocess.CompletedProcess) -> str:
    """The first Perl-space error line (trimmed), for the recovery notice."""
    for stream in (proc.stderr, proc.stdout):
        for line in (stream or "").splitlines():
            if _PARSE_CRASH_RE.search(line):
                return line.strip()[:120]
    return ""


def _is_parse_crash(proc: subprocess.CompletedProcess) -> bool:
    """exiftool rc != 0 WITH a Perl-space parse error. Any other rc != 0
    failure never recovers; neither does rc == 0 (that is the read-back's
    job, the stubborn-XMP class)."""
    return proc.returncode != 0 and bool(_crash_detail(proc))


#: exiftool WRITE-refusal signatures naming a damaged xref (2026-10-14,
#: issue #21: "Error: Invalid xref table" on a file qpdf --check calls
#: "damaged, xref not found ... attempting to reconstruct"). The file is
#: readable enough to diagnose but the writer refused it; qpdf can
#: rebuild the tree, so the 3.54.0 one-shot recovery shape applies.
_XREF_FAIL_RE = re.compile(
    r"\bInvalid xref\b|\bxref(?:\s+table)?\s+not found\b", re.IGNORECASE
)


def _xref_detail(proc: subprocess.CompletedProcess) -> str:
    """The first xref error line (trimmed), for the recovery notice."""
    for stream in (proc.stderr, proc.stdout):
        for line in (stream or "").splitlines():
            if _XREF_FAIL_RE.search(line):
                return line.strip()[:120]
    return ""


def _is_xref_failure(proc: subprocess.CompletedProcess) -> bool:
    """exiftool rc != 0 with an error naming a bad xref: the write-refusal
    class the parse-crash regex deliberately does not match."""
    return proc.returncode != 0 and bool(_xref_detail(proc))


def _pdf_page_count(path: Path) -> int | None:
    proc = _run_qpdf(["qpdf", "--show-npages", str(path)])
    if proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _rebuild_in_place(path: Path, backup: Path) -> int | None:
    """One `qpdf --replace-input` rebuild, page count verified preserved.

    Returns the preserved page count, or None when the rebuild is refused
    (no qpdf on PATH, unreadable either side) or the page count changed;
    every refusal restores the backup copy, so a failed recovery never
    leaves the file more broken than it was found."""
    if shutil.which("qpdf") is None:
        return None
    before = _pdf_page_count(path)
    if before is None:
        return None
    proc = _run_qpdf(["qpdf", "--replace-input", str(path)])
    after = _pdf_page_count(path) if proc.returncode == 0 else None
    if after != before:
        shutil.copy2(backup, path)
        return None
    return after


def _has_calibre_authorsort(path: Path) -> bool:
    """True when the file's XMP carries a calibre-style author_sort (any PDF
    Calibre has produced or touched does). Detection reads the raw XMP
    property, not the ebook-meta rendering: an author whose NAME contains a
    bracket would render ambiguously, the property cannot."""
    proc = _run_exiftool(["exiftool", "-s3", "-Author_sort", str(path)])
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _erase_ebook_meta_args(title: str, authors: list[str], publisher: str) -> list[str]:
    """Value-preserving ebook-meta invocation for the author_sort erase: it
    re-carries the stamped values (fields the stamp leaves alone are not
    touched by Calibre's PDF writer either) and passes `--author-sort ""` so
    Calibre writes no sort of its own — its PDF writer drops every
    calibre-namespaced XMP element from the existing packet, stale sorts
    included."""
    args = ["ebook-meta"]
    if title:
        args += ["--title", title]
    if authors:
        args += ["--authors", " & ".join(authors), "--author-sort", ""]
    if publisher:
        args += ["--publisher", publisher]
    return args


def _read_ebook_meta(path: Path) -> dict[str, str]:
    """ebook-meta read-back, parsed the same tolerant way for verification."""
    proc = subprocess.run(
        ["ebook-meta", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
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


def _check_isbn(isbn: str) -> int | None:
    """The tool-boundary checksum guard: an ISBN failing its check digit (10-
    or 13-form, separators tolerated) is refused before anything stamps. The
    2026-09-19 wave wrote three bad numbers past this script before an
    external assert caught them, and a stamped wrong ISBN pulls the wrong
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


#: The ebook-meta read-back arbitration (issue #22, the fourth recurrence
#: 2026-10-14). Four live files (2026-10-10..14: Learning Blender, the
#: Love unto crypt variant, The Return of Martin Guerre, The Sweet
#: Science) carried every stamped value correctly under exiftool while
#: Calibre's own reader reported the stamp missing: the blind-verifier
#: class. On a verify mismatch the disagreeing fields are re-read through
#: exiftool (the tags the stamp actually writes, any group's copy), and
#: only a field exiftool ALSO fails to hold stays failed. EPUB
#: verification never arbitrates: ebook-meta reads EPUBs reliably. Keys
#: are the FLATTENED tag names (every group's copy lands in one list):
#: the stamp writes docinfo Title + XMP-dc:Title, docinfo Author +
#: XMP-dc:Creator, XMP-dc:Publisher, and docinfo Keywords.
_ARBITER_PROBES: dict[str, tuple[str, ...]] = {
    "title": ("title",),
    "author": ("author", "creator"),
    "publisher": ("publisher",),
    "isbn": ("keywords",),
}

_ARBITER_TAGS = sorted({t for probes in _ARBITER_PROBES.values() for t in probes})


def _exiftool_arbitration_readback(path: Path) -> dict[str, list[str]]:
    """One exiftool JSON read of every arbiter tag, flattened to
    lowercase tag name -> [values]. A read that dies (rc != 0, unparseable
    output) returns {} so every disputed field stays failed (fail-closed:
    a blind arbiter never certifies a stamp)."""
    proc = _run_exiftool(
        ["exiftool", "-j", "-a", "-G1", *(f"-{t}" for t in _ARBITER_TAGS), str(path)]
    )
    if proc.returncode != 0:
        return {}
    try:
        records = json.loads(proc.stdout or "[]")
    except ValueError:
        return {}
    if not isinstance(records, list):
        return {}
    out: dict[str, list[str]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        for key, value in record.items():
            if key == "SourceFile":
                continue
            values = value if isinstance(value, list) else [value]
            for v in values:
                if isinstance(v, str):
                    out.setdefault(key.split(":")[-1].lower(), []).append(v)
    return out


def _tag_holds(readback: dict[str, list[str]], tag: str, expected: str) -> bool:
    values = readback.get(tag, [])
    if any(v.strip() == expected for v in values):
        return True
    # A producer-side list re-reads element-wise; the " & " join is the
    # stamp's own written shape.
    return bool(values) and " & ".join(v.strip() for v in values) == expected


def _arbitrate_with_exiftool(
    path: Path,
    failed_fields: list[str],
    title: str,
    authors: list[str],
    publisher: str,
    isbn: str,
) -> list[str]:
    """The disputed fields a direct exiftool read confirms missing. A field
    passes when any group's copy of a tag the stamp wrote equals the
    stamped value."""
    if not failed_fields:
        return []
    readback = _exiftool_arbitration_readback(path)
    if not readback:
        return list(failed_fields)
    expected = {
        "title": title,
        "author": " & ".join(authors),
        "publisher": publisher,
        "isbn": f"isbn:{isbn}",
    }
    return [
        field
        for field in failed_fields
        if not any(
            _tag_holds(readback, tag, expected[field])
            for tag in _ARBITER_PROBES.get(field, ())
        )
    ]


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

    announce()

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
        need_sort_erase = bool(args.author) and _has_calibre_authorsort(f)
        exif_args = _build_exiftool_args(
            args.title, args.author, args.publisher, args.isbn
        )
        exif_args.append(str(f))
        proc = _run_exiftool(exif_args)
        if proc.returncode != 0 and (_is_parse_crash(proc) or _is_xref_failure(proc)):
            # The recoverable write-failure classes (one shot each):
            # parse-crash, exiftool cannot even read the file (2026-09-26);
            # bad-xref, the write refused over a damaged xref table
            # (2026-10-14). Same mechanical cure: a qpdf rebuild and
            # exactly one retry; anything less clean stays a failure.
            detail = _crash_detail(proc) or _xref_detail(proc)
            print(
                f"  {DIM}exiftool failed on the file ({detail}); "
                f"attempting one qpdf rebuild{RESET}"
            )
            pages = _rebuild_in_place(f, backup)
            if pages is not None:
                print(
                    f"  {DIM}rebuild verified ({pages} pages preserved); "
                    f"retrying the stamp once{RESET}"
                )
                # The rebuild rewrote the packet: re-read the raw property
                # instead of trusting the pre-write detection.
                need_sort_erase = bool(args.author) and _has_calibre_authorsort(f)
                proc = _run_exiftool(exif_args)
            else:
                print(
                    f"  {DIM}rebuild refused or page count changed (backup "
                    f"restored); keeping the failure{RESET}"
                )
        if proc.returncode != 0:
            print(
                f"  {RED}STAMP_FAILED (exiftool exited {proc.returncode}): {proc.stderr.strip()}{RESET}"
            )
            failed_any = True
            continue

        if need_sort_erase:
            erase_args = _erase_ebook_meta_args(args.title, args.author, args.publisher)
            erase_args.append(str(f))
            proc = _run_ebook_meta(erase_args)
            if proc.returncode != 0:
                print(
                    f"  {RED}STAMP_FAILED (calibre author_sort rewrite exited {proc.returncode}): {proc.stderr.strip()}{RESET}"
                )
                failed_any = True
                continue
            print(f"  {DIM}cleared a stale calibre author_sort (XMP rewrite){RESET}")

        readback = _read_ebook_meta(f)
        failed_fields = _verify(
            readback, args.title, args.author, args.publisher, args.isbn
        )
        if failed_fields:
            # The read-back disagreed; arbitrate before declaring failure
            # (issue #22): a direct exiftool read re-checks the disputed
            # fields, and only the ones it ALSO fails to hold are real
            # (the stubborn-XMP class — phase 3 fixes those in SQL). A
            # field exiftool holds was a blind ebook-meta read; the stamp
            # landed.
            confirmed = _arbitrate_with_exiftool(
                f, failed_fields, args.title, args.author, args.publisher, args.isbn
            )
            if confirmed:
                print(
                    f"  {RED}STAMP_FAILED: read-back and exiftool both disagree on: {', '.join(confirmed)}{RESET}"
                )
                failed_any = True
            else:
                print(
                    f"  {DIM}ebook-meta read back blind on: {', '.join(failed_fields)} "
                    f"(exiftool holds the stamped values){RESET}"
                )
                print(f"  {GREEN}stamped and verified{RESET} (backup: {backup})")
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
