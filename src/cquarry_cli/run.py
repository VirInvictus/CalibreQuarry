"""The acquisition run verbs: ``cquarry run phase1|phase2|phase3``.

Phase 17 (roadmap): Brandon's three-phase acquisition pathway as commands
a user or an AI agent can run, surfacing judgment calls as structured
manifest decisions instead of re-reading prose. The manifest
(``acquisition-manifest/1``, :mod:`cquarry_cli.manifest`) is the hand-off;
the companion ``scripts/`` tools and ``bindery run`` are the driven
instruments. The 2026-09-06 decisions are encoded here:

- phase 1 is READ-ONLY against ``metadata.db`` and dry against book files
  unless ``--stamp`` (PDF stamping), ``--apply-lossy`` (bindery's gated
  content repairs), or ``--quarantine`` (moving true DRM hits aside) are
  passed; all three are file-side consents.
- phase 2 refuses to start unless the manifest is signed (the signature is
  the standing lossy consent for exactly the files the report listed),
  ``decisions_needed`` is empty, and Calibre is closed; it backs
  ``metadata.db`` up first (``--backup-dir``, outside the library) and
  commits the whole import as ONE ``batch()``. ``#source`` is stamped from
  manifest provenance and verified in-batch (a stamp that writes no state
  rolls the batch back), ``#audience`` unconditionally, tags and rating are
  cleared on the imported ids only. A file the library already has is
  refused and flagged, never imported twice. Metadata downloads run after
  the DB pass; a failure or ambiguity is a decision, never a guess.
- phase 3 consumes the manifest, curates via prompts on a TTY or an
  ``--answer-file``, commits its fixes as ONE ``batch()``, then drives
  ``bindery run phase3`` and ``reconcile_file_metadata.py`` and re-validates
  to 0 errors before emitting the prose batch record.

Subprocess instruments are resolved from the checkout's ``scripts/``
directory; the verbs are repo surfaces, not wheel data.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from cquarry_cli import manifest
from cquarry_cli.backups import make_backup
from cquarry_cli.manifest import DEFAULT_AUDIENCE

_PGREP_TIMEOUT = 15

#: The library NON-NEGOTIABLES columns (writeops.FORBIDDEN_COLUMNS is
#: the canonical tuple); phase 3's answer-file fixes refuse them too.
_BANNED_ANSWER_FIELDS = ("reading_status", "status", "date_read")

_EBOOK_EXTS = (".epub", ".pdf", ".mobi", ".azw3", ".djvu")

#: screen_duplicate.py's own screenable set (its EBOOK_EXTENSIONS). run.py
#: pre-filters the inventory with it so the screener only ever sees files it
#: screens: a djvu-only tree is a clean screen, not the script's exit-2
#: "no ebook files to screen" error. Keep the two sets in step.
_SCREEN_EXTS = (".epub", ".pdf", ".mobi", ".azw3")

#: The stamping path's filename convention, decided 2026-09-10 (roadmap
#: :902): "Author - Title". The stamp writer (_drive_stamp) emits exactly
#: these values and the observed corpus confirms the direction (libgen.li
#: names its files "[Series] Author - Title (year, publisher) - site").
#: Calibre's own filename fallback guesses the OPPOSITE order ("Title -
#: Author"; probed 2026-09-10: a metadata-less "Brian Jacques -
#: Mossflower.pdf" imports as Title "Brian Jacques"), and stamp_pdf's
#: preview deliberately mirrors that opposite guess because it previews
#: what an unstamped import would do. The seeds are mechanical and the
#: manifest review corrects them before signing either way.
_FILENAME_STAMP = re.compile(r"^(?P<author>.+?)\s+-\s+(?P<title>.+?)$")


def _scripts_dir() -> Path:
    """The checkout's scripts/ directory (run verbs drive its tools)."""
    candidates = [
        Path(__file__).resolve().parent.parent.parent / "scripts",
        Path("scripts"),
    ]
    for candidate in candidates:
        if (candidate / "validate_metadata.py").exists():
            return candidate
    raise FileNotFoundError(
        "companion scripts not found next to this checkout; the run verbs "
        "drive scripts/*.py and need the repository layout"
    )


def _run(
    cmd: list[str], *, cwd: str | None = None, timeout: int = 600
) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)


def calibre_running() -> bool:
    """The anchored-name pgrep guard (fetch_library_codes.py precedent).

    A timeout means assume running: refusing costs a re-run, guessing
    wrong writes to a live database.
    """
    try:
        return (
            subprocess.run(
                ["pgrep", "^calibre"],
                capture_output=True,
                timeout=_PGREP_TIMEOUT,
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        # An unrunnable pgrep is "can't tell" too: fail closed like a
        # timeout, never crash a write door with a traceback.
        return True


def _now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# phase 1: vet a downloads directory, emit the manifest + report
# ---------------------------------------------------------------------------


def _inventory(downloads_dir: str) -> list[str]:
    files = []
    for dirpath, _dirnames, filenames in os.walk(downloads_dir):
        rel = dirpath.replace(os.sep, "/")
        if "/_quarantine" in rel or "/_stamp_backups" in rel:
            continue
        for name in sorted(filenames):
            if os.path.splitext(name)[1].lower() in _EBOOK_EXTS:
                files.append(os.path.join(dirpath, name))
    return sorted(files)


def _stamps_from_filename(path: str) -> dict[str, Any]:
    """Filename-derived seed stamps (mechanical fixes only; the manifest is
    editable before signing, and phase 3 curates the real metadata).

    Reads "Author - Title" (the stamping path's convention; see
    _FILENAME_STAMP), never Calibre's opposite "Title - Author" import
    guess."""
    stem = os.path.splitext(os.path.basename(path))[0]
    stamp: dict[str, Any] = {"title": stem, "authors": []}
    m = _FILENAME_STAMP.match(stem)
    if m:
        author = m.group("author").replace("_", " ").strip()
        title = m.group("title").replace("_", " ").strip()
        if author and title:
            stamp = {
                "title": title,
                "authors": [a.strip() for a in author.split(",") if a.strip()],
            }
    return stamp


def _stamps_from_embedded(path: str) -> dict[str, Any] | None:
    """The file's embedded metadata as seed stamps, read through the
    ebook-meta seam (the 2026-09-18 roadmap box: z-library parenthetical
    filenames seeded whole-filename stamps and made the review pass the
    pipeline's highest-risk human step, while the skill's deep-stamp pass
    has usually already written verified metadata into the file).

    Non-empty embedded fields win over the filename parse (the merge is
    the caller's); ISBN is deliberately never seeded -- embedded
    identifiers are misidentification-prone, and the filename never
    seeded one either. ``pubdate`` is carried only when it parses as ISO:
    a bare year is unstoreable (cquarry's add_book raises on it) and a
    wrong-format seed must not fail phase 2. Returns None when ebook-meta
    is missing or the file cannot be read; an empty dict means the read
    succeeded and carried nothing usable. Authors arrive joined with
    " & " (calibre's ebook-meta display form)."""
    try:
        proc = _run(["ebook-meta", path], timeout=120)
    except OSError, subprocess.TimeoutExpired:
        return None
    if proc.returncode != 0:
        return None
    # A file calibre cannot parse still exits 0, prints ITS OWN
    # filename-derived guess (the opposite "Title - Author" reading this
    # pipeline deliberately rejects) and a traceback on stderr. That
    # guess must never beat the filename parse, so a traceback disables
    # the read, and calibre's "Unknown" placeholders are dropped.
    if "traceback" in (proc.stderr or "").lower():
        return None
    fields: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if " : " not in line:
            continue
        label, value = line.split(" : ", 1)
        fields[label.strip()] = value.strip()
    stamps: dict[str, Any] = {}
    title = fields.get("Title") or ""
    if title and title != "Unknown":
        stamps["title"] = title
    authors = [
        a.strip() for a in (fields.get("Author(s)") or "").split(" & ") if a.strip()
    ]
    if authors and authors != ["Unknown"]:
        stamps["authors"] = authors
    if fields.get("Publisher"):
        stamps["publisher"] = fields["Publisher"]
    if fields.get("Languages"):
        stamps["language"] = fields["Languages"].split(",")[0].strip()
    published = fields.get("Published") or ""
    if published:
        try:
            datetime.fromisoformat(published)
        except ValueError:
            published = ""
    if published:
        stamps["pubdate"] = published
    return stamps


#: Filename-evidence provenance seeds, mapped onto the library's #source
#: vocabulary (the 2026-09-06 decision binds phase 2's cc6 stamp to the
#: manifest's provenance). Observed download naming, 2026-09-08 and
#: 2026-09-10 runs: "(z-library.sk, 1lib.sk, z-lib.sk)" site suffixes, the
#: "-- Anna's Archive" trailer, and libgen.li. The trailer names its source
#: outright; libgen.* is the enum's "Library Genesis". The Z-Library enum
#: decision landed 2026-09-12, and the value was renamed to Brandon's
#: entry spelling `Z-Lib` on 2026-09-13 (observed in the library 2026-09-14:
#: the enum no longer carries the old 3.40-era "Z-Library" string), so the
#: seeder tracks `Z-Lib` -- the value must exist in the library's #source
#: enum BEFORE phase 2 stamps it (enum validation refuses unknown values,
#: deliberately loudly). A name carrying no marker seeds None: absence of
#: evidence is not a source.
_PROVENANCE_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"anna[’']s\s+archive", re.IGNORECASE), "Anna's Archive"),
    (re.compile(r"\b(?:z[\s_-]?lib(?:rary)?|1lib)\b", re.IGNORECASE), "Z-Lib"),
    (re.compile(r"\blibgen\b", re.IGNORECASE), "Library Genesis"),
)


def _provenance_from_filename(path: str) -> str | None:
    """Filename-derived seed provenance (a #source value or None), surfaced
    in the manifest for the review step to correct exactly like the stamps;
    phase 2 stamps cc6 from the reviewed value."""
    stem = os.path.splitext(os.path.basename(path))[0]
    for pattern, source in _PROVENANCE_PATTERNS:
        if pattern.search(stem):
            return source
    return None


def _screen_duplicates(files: list[str], db_path: str) -> set[str]:
    """screen_duplicate.py --format json over the inventoried files: the
    paths with a library or within-batch duplicate hit.

    The JSON report is a bare list holding EVERY screened file, so only
    records with hits count as duplicates. An unparseable report is a hard
    error: silence here would approve files the screen never judged."""
    if not files:
        return set()
    script = _scripts_dir() / "screen_duplicate.py"
    proc = _run(
        [sys.executable, str(script), *files, "--db", db_path, "--format", "json"]
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"screen_duplicate failed: {proc.stderr.strip()}")
    try:
        records = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"screen_duplicate report unreadable: {e}") from e
    if not isinstance(records, list):
        raise RuntimeError(
            "screen_duplicate report is not the documented list shape: "
            f"{type(records).__name__}"
        )
    return {
        record["file"]
        for record in records
        if isinstance(record, dict)
        and isinstance(record.get("file"), str)
        and (record.get("library_hits") or record.get("batch_duplicates"))
    }


def _drm_verdicts(downloads_dir: str) -> dict[str, str]:
    """audit_drm.py over the directory, as path -> verdict class."""
    script = _scripts_dir() / "audit_drm.py"
    csv_path = Path(downloads_dir) / "_drm_audit.csv"
    proc = _run(
        [sys.executable, str(script), downloads_dir, "--csv", str(csv_path)],
        timeout=1800,
    )
    verdicts: dict[str, str] = {}
    if csv_path.exists():
        import csv as _csv

        with open(csv_path, newline="", encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                verdicts[row["path"]] = row.get("status") or row.get("kind") or "scan"
        csv_path.unlink()
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"audit_drm failed: {proc.stderr.strip()}")
    return verdicts


def _pdf_battery(files: list[str]) -> dict[str, Any]:
    """check_pdf.py over the inventory's PDF/DJVU files (the phase-1
    battery: header, pages, qpdf real-vs-benign, fonts, text layer). The
    targets come from the same recursive inventory everything else uses,
    not a top-level re-scan that misses nested files."""
    targets = [f for f in files if os.path.splitext(f)[1].lower() in (".pdf", ".djvu")]
    if not targets:
        return {}
    fd, out = tempfile.mkstemp(prefix="cq-pdf-battery-", suffix=".json")
    os.close(fd)
    try:
        proc = _run(
            [
                sys.executable,
                str(_scripts_dir() / "check_pdf.py"),
                *targets,
                "--json",
                out,
            ],
            timeout=1800,
        )
        # Exit codes are check_pdf's: 0 clean, 1 structural findings (the
        # normal trouble outcome, report still written), 2 a file it
        # could not read at all.
        if proc.returncode not in (0, 1):
            raise RuntimeError(f"check_pdf failed: {proc.stderr.strip()}")
        try:
            with open(out, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"check_pdf report unreadable: {e}") from e
    finally:
        os.unlink(out)
    return {r["path"]: r for r in data.get("files", []) if isinstance(r, dict)}


def _bindery_version(bindery: str) -> tuple[int, ...] | None:
    """Parse `bindery --version` (it prints ``bindery 0.45.0``) into a
    comparable tuple, or None when it cannot be run or parsed."""
    try:
        proc = _run([bindery, "--version"], timeout=30)
    except OSError, subprocess.TimeoutExpired:
        return None
    text = (proc.stdout or proc.stderr).strip()
    m = re.search(r"(\d+(?:\.\d+)*)", text)
    if not m:
        return None
    return tuple(int(part) for part in m.group(1).split("."))


#: The oldest bindery whose phase-1 report this code trusts: every repair
#: record must carry the structured fix breakdown (the ``fixes`` dict plus
#: ``ncx_uid_synced``/``watermark_refusals``), shipped in v0.45.0. The
#: lossy-consent mirror classes repairs from that data; against an older
#: PATH binary it would silently class every strip as structural (the
#: vacuous-consent hole again), so too old is a hard error, not a
#: downgrade.
_BINDERY_MIN_VERSION = (0, 45, 0)


def _bindery_phase1(downloads_dir: str, *, apply_lossy: bool) -> dict[str, Any]:
    """bindery run phase1 --json FILE (the EPUB slice). Read-only unless the
    signed-consent flag is passed (phase 2's repair step uses that).

    Exit codes are bindery's library contract: 0 all-clean, 2 trouble found
    (the normal outcome over a directory holding any problem book; the
    report is still written), 1 a broken invocation or an epub-less tree
    (no report; the slice is simply unavailable). Any other code, or a
    missing report behind 0/2 -- an entry point too old to know the run
    verb -- is a hard error."""
    bindery = shutil.which("bindery")
    if bindery is None:
        return {}
    version = _bindery_version(bindery)
    if version is None or version < _BINDERY_MIN_VERSION:
        found = ".".join(str(part) for part in version) if version else "unreadable"
        raise RuntimeError(
            f"bindery on PATH is {found}, but the lossy-consent mirror needs "
            f"{_BINDERY_MIN_VERSION[0]}.{_BINDERY_MIN_VERSION[1]}."
            f"{_BINDERY_MIN_VERSION[2]}+ (the structured fix records); "
            "upgrade it (uv tool upgrade bindery-cli)"
        )
    fd, report = tempfile.mkstemp(prefix="bindery-phase1-", suffix=".json")
    os.close(fd)
    try:
        cmd = [bindery, "run", "phase1", downloads_dir, "--json", report]
        proc = _run(cmd + (["--apply-lossy"] if apply_lossy else []), timeout=3600)
        if proc.returncode == 1:
            return {}
        if proc.returncode not in (0, 2):
            raise RuntimeError(f"bindery run phase1 failed: {proc.stderr.strip()}")
        try:
            with open(report, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"bindery run phase1 ended {proc.returncode} but wrote no "
                f"readable report (is the bindery on PATH run-verb capable?): {e}"
            ) from e
    finally:
        os.unlink(report)


#: The fix keys that mark a bindery repair as LOSSY (a content strip),
#: the set bindery's own repair gate treats specially (their gain is
#: invisible to epubcheck): stripped_pagination, stripped_broken_tags,
#: stripped_watermarks, dropped_marker, stub_docs_dropped. A gate-accepted
#: repair without these is structural (alt text, invalid values, NCX
#: fixes) and consent-free. Judged against the record's structured
#: ``fixes`` dict (bindery-cli >= 0.45.0), never against the rendered
#: summary string: the 2026-09-18 bindery roadmap box replaced the old
#: string contract with data, and _BINDERY_MIN_VERSION enforces the
#: report shape this reads.
_LOSSY_REPAIR_MARKERS = (
    "stripped_pagination",
    "stripped_broken_tags",
    "stripped_watermarks",
    "dropped_marker",
    "stub_docs_dropped",
)


def _repair_is_lossy(fixes: dict[str, int]) -> bool:
    return any(fixes.get(marker) for marker in _LOSSY_REPAIR_MARKERS)


def _mirror_lossy(man: dict[str, Any], bindery_shape: dict[str, Any]) -> None:
    """Bindery's gate-accepted EPUB repairs become per-file ``lossy``
    records: the field the seal binds, so the signature consents to (and
    the batch record retains) exactly the repairs the report listed. The
    2026-09-14 hole: bindery carried an ``apply_lossy`` decision with
    gate-accepted repairs, and every lossy record stayed
    ``{flagged: false, repairs: []}`` -- sign consented to nothing.

    Every gate-accepted repair is recorded (with its lossy/structural
    class named), but only repairs carrying a lossy marker set the file's
    ``flagged`` -- the flag the consent gate and the seal's consent scope
    key off. Structural-only files keep their record and never block
    phase 2 on a vacuous consent."""
    if not bindery_shape:
        return
    applied = bool(bindery_shape.get("apply_lossy"))
    by_path = {os.path.abspath(f["path"]): f for f in man["files"]}
    for book in bindery_shape.get("books", []):
        repair = book.get("repair") if isinstance(book, dict) else None
        if not isinstance(repair, dict) or repair.get("status") not in (
            "accept",
            "partial",
        ):
            continue
        fixes = repair.get("fixes") or {}
        summary = (repair.get("summary") or "").strip()
        if not fixes and not summary:
            continue
        entry = by_path.get(os.path.abspath(str(book.get("path") or "")))
        if entry is None:
            continue
        # The class comes from the data; the summary rides along as the
        # human-readable audit line the decision detail quotes.
        lossy = _repair_is_lossy(fixes)
        entry["lossy"]["repairs"].append(
            {"tool": "bindery", "summary": summary, "applied": applied, "lossy": lossy}
        )
        if lossy:
            entry["lossy"]["flagged"] = True


def _mirror_bindery_decisions(
    man: dict[str, Any], bindery_shape: dict[str, Any], *, apply_lossy: bool
) -> None:
    """Bindery's run-report decisions become decisions_needed entries, the
    durable record the seal binds and phase 2 blocks on.

    Two mirrors land here. manual_watermark_repair becomes manual_repair
    per book (the sibling gap of the 3.43.0 lossy mirror: books bindery
    refuses to auto-strip used to vanish from the record entirely). And a
    dry pass (apply_lossy False) emits lossy_consent for every
    lossy-flagged file, the designed home for an in-manifest resolution:
    the reviewer sets the decision's "resolution" to "apply" and re-signs
    instead of re-running phase 1 with --apply-lossy (the re-run used to
    mint a second manifest under the unique-name rule and orphan the
    first). phase 2 consumes the resolution and drives the strips itself;
    an unresolved lossy_consent still blocks the import like any other
    open decision."""
    if not bindery_shape:
        return
    by_path = {os.path.abspath(f["path"]): f for f in man["files"]}
    for decision in bindery_shape.get("decisions_needed", []):
        if not isinstance(decision, dict):
            continue
        if decision.get("decision") != "manual_watermark_repair":
            continue
        for path in decision.get("books", []):
            if by_path.get(os.path.abspath(str(path))) is None:
                continue
            manifest.add_decision(
                man,
                "manual_repair",
                file=str(path),
                detail=(
                    "bindery refused the watermark strip (too large to "
                    "remove safely); remove the stamp by hand"
                ),
            )
    if apply_lossy:
        return
    for entry in man["files"]:
        if entry["lossy"]["flagged"]:
            lossy_bits = "; ".join(
                record["summary"]
                for record in entry["lossy"]["repairs"]
                if record.get("lossy")
            )
            manifest.add_decision(
                man,
                "lossy_consent",
                file=entry["path"],
                detail=(
                    f"lossy repairs pending ({lossy_bits}; this phase-1 "
                    "run was read-only): re-run phase 1 with "
                    "--apply-lossy, or set this decision's "
                    '"resolution" to "apply" and re-sign'
                ),
            )


def _quarantine(downloads_dir: str, path: str, reason: str) -> str:
    """Move one problem file into _quarantine/, never onto an existing
    file: a basename collision gets a numbered sibling, so two same-named
    files in different subdirs cannot destroy each other."""
    dest_dir = os.path.join(downloads_dir, "_quarantine")
    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.basename(path)
    dest = os.path.join(dest_dir, base)
    if os.path.exists(dest):
        stem, ext = os.path.splitext(base)
        n = 2
        while os.path.exists(os.path.join(dest_dir, f"{stem}-{n}{ext}")):
            n += 1
        dest = os.path.join(dest_dir, f"{stem}-{n}{ext}")
    shutil.move(path, dest)
    return f"{reason}; moved to {dest}"


def _drive_stamp(files: list[str], backups: str) -> list[str]:
    """stamp_pdf.py --apply for PDFs whose filename parsed into stamps.
    File-side consent (--stamp): the caller opted in; originals backed up
    outside the downloads tree first (the stamp_pdf.py precedent)."""
    stamped = []
    for path in files:
        if os.path.splitext(path)[1].lower() != ".pdf":
            continue
        stamps = _stamps_from_filename(path)
        if not stamps.get("authors"):
            continue
        cmd = [
            sys.executable,
            str(_scripts_dir() / "stamp_pdf.py"),
            path,
            "--title",
            stamps["title"],
            "--apply",
            "--backup-dir",
            backups,
        ]
        for author in stamps["authors"]:
            cmd += ["--author", author]
        proc = _run(cmd, timeout=600)
        if proc.returncode == 0:
            stamped.append(path)
        else:
            # A failed stamp must not be a silent no-op: the file ships
            # with the wrong metadata and phase 3 needs to know why.
            print(
                f"WARNING: stamp_pdf failed on {os.path.basename(path)}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}",
                file=sys.stderr,
            )
    return stamped


def run_phase1(
    downloads_dir: str,
    db_path: str,
    *,
    bindery_report: str | None = None,
    stamp: bool = False,
    apply_lossy: bool = False,
    quarantine: bool = False,
    quiet: bool = False,
) -> int:
    """Vet a downloads directory and emit the batch manifest. Read-only
    against metadata.db; file-side writes only under --stamp (PDF
    stamping), --apply-lossy (bindery's gated repairs), or --quarantine
    (moving true DRM hits aside). Without --quarantine a DRM hit is
    recorded in the manifest and the file stays where it is."""
    downloads_dir = os.path.abspath(downloads_dir)
    if not os.path.isdir(downloads_dir):
        print(f"ERROR: no such directory: {downloads_dir}", file=sys.stderr)
        return 2
    library_dir = os.path.dirname(db_path)
    files = _inventory(downloads_dir)
    if not files:
        print(f"No ebook files under {downloads_dir}.")
        return 0

    # stamp_pdf refuses a backup dir inside the tree it stamps, which made
    # --stamp a silent no-op; the backups belong outside the vetted tree
    # (the stamp_pdf.py precedent), where a rerun cannot sweep them either.
    stamp_backups = os.path.join(
        tempfile.gettempdir(), f"cquarry-stamp-backups-{_now_stamp()}"
    )
    stamped_files = _drive_stamp(files, stamp_backups) if stamp else []

    drm = _drm_verdicts(downloads_dir)
    battery = _pdf_battery(files)
    dup_paths = _screen_duplicates(
        [f for f in files if os.path.splitext(f)[1].lower() in _SCREEN_EXTS],
        db_path,
    )

    man = manifest.new_manifest(downloads_dir)
    ebook_meta = shutil.which("ebook-meta")
    if ebook_meta is None:
        print(
            "WARNING: ebook-meta not found; stamp seeds fall back to the "
            "filename parse alone (embedded metadata unread).",
            file=sys.stderr,
        )
    for path in files:
        entry = manifest.new_file_entry(path)
        entry["size"] = os.path.getsize(path)
        entry["stamps"] = _stamps_from_filename(path)
        if ebook_meta is not None:
            embedded = _stamps_from_embedded(path)
            if embedded is None:
                # A file whose metadata cannot be read still ships, with
                # the filename seeds and the failure on the record for
                # the review pass to see.
                entry["repairs"].append(
                    "ebook-meta could not read embedded metadata; "
                    "stamps seeded from the filename"
                )
            else:
                for key, value in embedded.items():
                    if value:
                        entry["stamps"][key] = value
        entry["provenance"] = _provenance_from_filename(path)
        if path in stamped_files:
            entry["repairs"].append("stamped via stamp_pdf (--stamp)")
        drm_status = drm.get(path, "unscanned")
        entry["checks"]["drm"] = drm_status
        batt = battery.get(path)
        if batt is not None:
            entry["checks"]["pdf_battery"] = {
                "pages": batt.get("pages"),
                "qpdf_check": batt.get("qpdf_check"),
                "findings": len(batt.get("findings", [])),
            }
        # Quarantine audit_drm's own problem set only (its is_problem:
        # DRM, plus ERROR, a scan that could not verify). BENIGN (font
        # obfuscation, permission flags) and N/A (DJVU) are not locks and
        # never move a file; the docs' dry promise is for them too.
        if drm_status.upper() in ("DRM", "ERROR"):
            reason = f"DRM: {drm_status}"
            entry["verdict"] = "quarantined"
            if quarantine:
                entry["repairs"].append(_quarantine(downloads_dir, path, reason))
                moved_to = os.path.join(downloads_dir, "_quarantine")
            else:
                # Dry: the verdict and the decision are recorded, the
                # file stays where it is for Brandon to handle.
                moved_to = None
            man["quarantines"].append(
                {"path": path, "reason": reason, "moved_to": moved_to}
            )
            # The quarantined file gets a files[] entry too: the schema's
            # quarantined verdict was writer-dead while the writer skipped
            # exactly these files.
            manifest.add_file(man, entry)
            manifest.add_decision(man, "manual_repair", file=path, detail=reason)
            continue
        if path in dup_paths:
            entry["verdict"] = "duplicate_refused"
            manifest.add_file(man, entry)
            manifest.add_decision(
                man, "duplicate", file=path, detail="matches an existing library book"
            )
            continue
        entry["verdict"] = "needs_decision"
        manifest.add_file(man, entry)

    bindery_shape = _bindery_phase1(downloads_dir, apply_lossy=apply_lossy)
    if bindery_report:
        Path(bindery_report).write_text(
            json.dumps(bindery_shape, indent=1), encoding="utf-8"
        )

    # EPUB slice verdicts feed the checks; clean files become approved.
    for entry in man["files"]:
        entry["checks"]["bindery"] = "see report" if bindery_shape else "unavailable"
        if entry["verdict"] == "needs_decision":
            entry["verdict"] = "approved_for_import"
    _mirror_lossy(man, bindery_shape)
    _mirror_bindery_decisions(man, bindery_shape, apply_lossy=apply_lossy)
    manifest.approve(
        man,
        [f["path"] for f in man["files"] if f["verdict"] == "approved_for_import"],
    )

    # The retained manifest is the durable batch record phase 2 resume and
    # phase 3 consume, so a second same-day batch must never overwrite the
    # first: the filename collides only in the date, and the exists() loop
    # numbers the newcomer (the _backup_db pattern; the fixed name let two
    # batches on one library destroy the first batch's record).
    manifests = manifest.manifests_dir(library_dir)
    stamp = _now_stamp()
    manifest_path = os.path.join(manifests, f"{stamp}-batch.json")
    n = 2
    while os.path.exists(manifest_path):
        manifest_path = os.path.join(manifests, f"{stamp}-batch-{n}.json")
        n += 1
    manifest.save(man, manifest_path)

    if not quiet:
        print(f"Phase 1 over {downloads_dir}: {len(files)} file(s).")
        print(f"  approved: {len(man['approved_for_import'])}")
        print(
            f"  quarantined/duplicate: {len(man['quarantines']) + sum(1 for f in man['files'] if f['verdict'] == 'duplicate_refused')}"
        )
        print(f"  decisions_needed: {len(man['decisions_needed'])}")
        print(f"Manifest: {manifest_path}")
        print(
            "Review, edit stamps and provenance, then SIGN the manifest "
            '("signed": true) to authorize phase 2.'
        )
    return 0


# ---------------------------------------------------------------------------
# phase 2: the automated import
# ---------------------------------------------------------------------------


def _fetch_metadata(isbn: str, opf_path: str) -> str:
    """fetch-ebook-metadata for one ISBN. Returns ok / no_result /
    ambiguous / failed (the timeout the manifest schema records).

    -o/--opf is a store flag: the OPF arrives on STDOUT, never at a
    path. The old cut passed opf_path after -o, a silently-ignored
    stray positional, so the file never existed, the success gate
    always failed, and the ok branch, _apply_opf, and the clobber
    watch were dead code (every import queued a bogus
    metadata_download decision). The stdout is staged to opf_path,
    exactly like run_backfill stages its own fetch."""
    try:
        proc = _run(
            ["fetch-ebook-metadata", "--identifier", f"isbn:{isbn}", "-o"],
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        # A hung lookup is a failure, not an ambiguity: there is nothing
        # to disambiguate.
        return "failed"
    if proc.returncode != 0 or not proc.stdout.strip():
        # "multiple" only: the no-result output also says "No matches
        # found", so the bare word "matches" classified every empty
        # lookup as ambiguous.
        text = (proc.stdout + proc.stderr).lower()
        if "multiple" in text:
            return "ambiguous"
        return "no_result"
    with open(opf_path, "w", encoding="utf-8") as f:
        f.write(proc.stdout)
    return "ok"


_OPF_NS = "{http://www.idpf.org/2007/opf}"


def _apply_opf(book_id: int, opf_path: str, db_path: str) -> bool:
    """Apply a downloaded OPF through cquarry's write module (the repo's
    no-calibredb constraint). The fields are the ones a metadata download
    is for; anything the OPF does not carry is left as imported. One
    batch per book; False on any failure."""
    try:
        import xml.etree.ElementTree as ET

        tree = ET.parse(opf_path)
    except OSError, ET.ParseError:
        return False

    def local(el):
        return el.tag.rsplit("}", 1)[-1]

    def first_text(tag):
        for el in tree.getroot().iter():
            if local(el) == tag and (el.text or "").strip():
                return (el.text or "").strip()
        return None

    def all_texts(tag):
        return [
            (el.text or "").strip()
            for el in tree.getroot().iter()
            if local(el) == tag and (el.text or "").strip()
        ]

    from cquarry.write import WritableCalibreDB

    try:
        with WritableCalibreDB(db_path) as wdb:
            with wdb.batch():
                title = first_text("title")
                if title:
                    wdb.update_title(book_id, title)
                creators = all_texts("creator")
                if creators:
                    wdb.set_authors(book_id, creators)
                publisher = first_text("publisher")
                if publisher:
                    wdb.set_publisher(book_id, publisher)
                pubdate = first_text("date")
                if pubdate:
                    wdb.set_pubdate(book_id, pubdate)
                description = first_text("description")
                if description:
                    wdb.set_comments(book_id, description)
                for el in tree.getroot().iter():
                    if local(el) != "identifier":
                        continue
                    scheme = (
                        el.get(f"{_OPF_NS}scheme") or el.get("scheme") or ""
                    ).strip()
                    value = (el.text or "").strip()
                    if scheme and value:
                        wdb.set_identifier(book_id, scheme.lower(), value)
                return True
    except Exception:
        return False


def run_phase2(
    manifest_path: str,
    db_path: str,
    *,
    backup_dir: str | None = None,
    audience: str = DEFAULT_AUDIENCE,
    quiet: bool = False,
) -> int:
    """Import the manifest's approved files. Guards first: signed,
    decisions_needed empty, Calibre closed; --backup-dir mandatory."""
    try:
        man = manifest.load(manifest_path)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not man["signed"]:
        print(
            "ERROR: the manifest is not signed; sign the phase-1 report "
            "(the signature is the standing lossy consent) before phase 2.",
            file=sys.stderr,
        )
        return 2
    # metadata_download entries are phase 2's own product (phase 3's
    # input); every other kind predates the import and blocks it. The
    # one exception is a lossy_consent the reviewer resolved in-manifest
    # ("resolution": "apply"): that resolution IS the consent, and the
    # strips are driven below instead of blocking the import.
    blockers = [
        d
        for d in man["decisions_needed"]
        if d["kind"] != "metadata_download"
        and not (d["kind"] == "lossy_consent" and d.get("resolution") == "apply")
    ]
    if blockers:
        print(
            f"ERROR: {len(blockers)} decision(s) still open; "
            "resolve them before importing.",
            file=sys.stderr,
        )
        return 2
    if calibre_running():
        # Lock-class refusal (exit 1, like setwrite and integrate): the
        # door used to exit 2 here while set mode exited 1, and scripts
        # branch on the split. Usage problems exit 2; an open Calibre is
        # not a usage problem.
        print("ERROR: Calibre is running; close it before phase 2.", file=sys.stderr)
        return 1
    if not backup_dir:
        print(
            "ERROR: phase 2 requires --backup-dir (outside the library).",
            file=sys.stderr,
        )
        return 2
    try:
        backup = make_backup(db_path, backup_dir)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    # The in-manifest lossy consent (see _mirror_bindery_decisions): the
    # strips happen NOW, file-side, before anything is imported -- the
    # --apply-lossy phase-1 re-run's outcome without the second manifest.
    # Consent is all-or-nothing (bindery's re-drive applies every
    # gate-accepted repair in the tree), so a partial resolution refuses
    # before any file is touched. Each consumed decision is removed and
    # the lossy records flip to applied, so a resume never re-drives a
    # strip that already happened.
    consents = {
        d["file"]
        for d in man["decisions_needed"]
        if d["kind"] == "lossy_consent" and d.get("resolution") == "apply"
    }
    if consents:
        flagged = {f["path"] for f in man["files"] if f["lossy"]["flagged"]}
        if consents != flagged:
            print(
                "ERROR: lossy consent is all-or-nothing: "
                f"{len(consents)} decision(s) resolved against "
                f"{len(flagged)} lossy-flagged file(s); resolve every "
                "lossy_consent (or none), then re-sign.",
                file=sys.stderr,
            )
            return 2
        shape = _bindery_phase1(man["downloads_dir"], apply_lossy=True)
        repaired = {
            os.path.abspath(str(b.get("path") or "")): b.get("repair")
            for b in shape.get("books", [])
            if isinstance(b, dict)
        }
        # Bindery's re-drive applies EVERY gate-accepted repair (the
        # consent only gated the lossy ones), so the refuse/flip pass
        # walks every file with a recorded repair, lossy or structural;
        # a structural record left applied=false would lie about the
        # file on disk.
        recorded = {f["path"] for f in man["files"] if f["lossy"]["repairs"]}
        for path in sorted(recorded):
            repair = repaired.get(os.path.abspath(path))
            if (
                not isinstance(repair, dict)
                or repair.get("status") not in ("accept", "partial")
                or not (repair.get("summary") or "").strip()
            ):
                print(
                    "ERROR: bindery could not apply the consented repairs "
                    f"to {os.path.basename(path)}; nothing was imported.",
                    file=sys.stderr,
                )
                return 1
            entry = manifest.file_by_path(man, path)
            for record in entry["lossy"]["repairs"]:
                record["applied"] = True
        man["decisions_needed"] = [
            d for d in man["decisions_needed"] if d["kind"] != "lossy_consent"
        ]
        # Durable before the import: a crash after this point must not
        # re-drive the strips on a resume (save re-seals the signed
        # manifest).
        manifest.save(man, manifest_path)

    from cquarry.write import WritableCalibreDB

    imported: list[tuple[str, int]] = []
    try:
        with WritableCalibreDB(db_path) as wdb:
            with wdb.batch():
                for path in man["approved_for_import"]:
                    entry = manifest.file_by_path(man, path)
                    if entry is None or entry["import"]["imported_id"]:
                        continue  # resumable: already imported
                    if not os.path.exists(path):
                        manifest.add_decision(
                            man,
                            "manual_repair",
                            file=path,
                            detail="approved file vanished since phase 1",
                        )
                        continue
                    stamps = entry["stamps"] or {}
                    book_id = wdb.add_book(
                        stamps.get("title") or "Unknown",
                        stamps.get("authors") or [],
                        formats=[path],
                        identifiers=(
                            {"isbn": stamps["isbn"]} if stamps.get("isbn") else None
                        ),
                        language=stamps.get("language"),
                        pubdate=stamps.get("pubdate"),
                        publisher=stamps.get("publisher"),
                    )
                    entry["import"]["imported_id"] = book_id
                    imported.append((path, book_id))
                    # The pathway's reset, scoped to the ids this run imported.
                    entry["import"]["clears"]["tags_removed"] = wdb.clear_tags(book_id)
                    entry["import"]["clears"]["rating_cleared"] = wdb.clear_rating(
                        book_id
                    )
                    provenance = entry.get("provenance")
                    if provenance:
                        # The in-batch cc6-vs-manifest check (the
                        # 2026-09-18/19 observations): a fresh book's
                        # first #source write must report changed, and
                        # the setter validates the enum loudly; False
                        # means the stamp silently landed nowhere, and
                        # the batch rolls back rather than commit an
                        # import whose provenance never reached the
                        # column.
                        if not wdb.set_custom_column(book_id, "#source", provenance):
                            raise RuntimeError(
                                f"#source stamp {provenance!r} for book "
                                f"{book_id} ({os.path.basename(path)}) "
                                "wrote no state; the import batch rolls "
                                "back"
                            )
                    wdb.add_custom_column_values(book_id, "#audience", [audience])
                    entry["import"]["fixes"].append("filename-derived stamps applied")
    except Exception as e:
        # The batch rolled back, so the library was never modified (and
        # since cquarry 1.15 no orphaned book directories survive it
        # either). The backup is the belt-and-braces restore point.
        print(
            f"ERROR: import failed; the batch rolled back and nothing was "
            f"written ({e}). Pre-run backup: {backup}.",
            file=sys.stderr,
        )
        return 1

    # The resume record goes to disk BEFORE the download segment: the
    # downloads are unguarded subprocess work, and a crash there must not
    # cost the imported ids (a rerun would re-import or refuse on the
    # byte-identity floor).
    manifest.save(man, manifest_path)

    # The download segment: per-book, Calibre-open-safe, never a guess.
    # The guard window closed at commit: if Calibre opened since, defer
    # the downloads to phase 3 rather than write beside a live Calibre.
    from cquarry.db import CalibreDB

    live_after_commit = calibre_running()
    for path, book_id in imported:
        entry = manifest.file_by_path(man, path)
        isbn = (entry["stamps"] or {}).get("isbn")
        outcome = "deferred_to_phase3"
        if not live_after_commit and isbn:
            opf_dir = manifest.manifests_dir(os.path.dirname(db_path))
            os.makedirs(opf_dir, exist_ok=True)
            opf_path = os.path.join(opf_dir, f"_download_{book_id}.opf")
            outcome = _fetch_metadata(isbn, opf_path)
            if outcome == "ok" and not _apply_opf(book_id, opf_path, db_path):
                outcome = "failed"
            Path(opf_path).unlink(missing_ok=True)
        entry["import"]["download_outcome"] = outcome
        if outcome != "ok":
            manifest.add_decision(
                man,
                "metadata_download",
                file=path,
                book_id=book_id,
                detail=outcome,
            )
        else:
            db = CalibreDB(db_path)
            try:
                book = db.get_book(book_id)
                downloaded_authors = (book or {}).get("authors") or []
                stamped = (entry["stamps"] or {}).get("authors") or []
                if downloaded_authors != stamped:
                    entry["import"]["clobber_watch"] = {
                        "stamped": stamped,
                        "post_download": downloaded_authors,
                    }
            finally:
                db.close()

    manifest.save(man, manifest_path)
    if not quiet:
        print(f"Phase 2: imported {len(imported)} book(s); backup {backup}.")
        for path, book_id in imported:
            print(f"  {os.path.basename(path)} -> book {book_id}")
        pending = len(man["decisions_needed"])
        if pending:
            print(f"{pending} decision(s) queued for phase 3.")
    return 0


# ---------------------------------------------------------------------------
# phase 3: curation, then the mechanical pass
# ---------------------------------------------------------------------------


def _precedent_tags(db_path: str, authors: list[str]) -> list[str]:
    """Tags shared by the same author's other books (tag-by-precedent).

    The four-table JOIN is cquarry's since 1.22 (CalibreDB.precedent_tags;
    the frontend-only split owns every SQL read, and this was the last
    unrecorded raw-SQL read in the tier). This wrapper only opens the
    read-only connection."""
    from cquarry.db import CalibreDB

    db = CalibreDB(db_path)
    try:
        return db.precedent_tags(authors)
    finally:
        db.close()


def _prompt_answers(db_path: str, dossiers: list[dict]) -> dict[int, dict]:
    """Interactive decision gates (tag-by-precedent, description rewrite,
    field fixes). Only used on a TTY; scripts pass --answer-file."""
    answers: dict[int, dict] = {}
    for d in dossiers:
        b = d["book"]
        print(f"\n=== {b['id']}: {b['title']} ===")
        suggestions = _precedent_tags(db_path, b["authors"])
        if suggestions:
            print(f"  precedent tags: {', '.join(suggestions)}")
        raw = input("tags (comma-separated, empty to skip): ").strip()
        entry: dict[str, Any] = {}
        if raw:
            entry["tags"] = [t.strip() for t in raw.split(",") if t.strip()]
        raw = input("replace description with pasted HTML? (empty = keep): ").strip()
        if raw:
            entry["comments_html"] = raw
        answers[b["id"]] = entry
    return answers


def run_phase3(
    manifest_path: str,
    db_path: str,
    *,
    answer_file: str | None = None,
    quiet: bool = False,
) -> int:
    """Curate the phase-2 imports (one batch for the decided fixes), then
    the mechanical pass: bindery phase3, file reconciliation, re-validate,
    and the prose batch record."""
    try:
        man = manifest.load(manifest_path)
    except (OSError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    imported_ids = [
        f["import"]["imported_id"] for f in man["files"] if f["import"]["imported_id"]
    ]
    if not imported_ids:
        print(
            "ERROR: the manifest records no imported ids; run phase 2 first.",
            file=sys.stderr,
        )
        return 2

    from cquarry.integrity import find_untagged

    from cquarry.db import CalibreDB

    db = CalibreDB(db_path)
    try:
        untagged = set(find_untagged(db))
        batch_set = [i for i in imported_ids if i in untagged]
        already = [i for i in imported_ids if i not in untagged]
        dossiers = [
            d
            for d in (db.get_book_dossier(i, include_comments=True) for i in batch_set)
            if d is not None
        ]
    finally:
        db.close()
    if already and not quiet:
        print(f"{len(already)} imported book(s) already tagged; skipping them.")

    # The same rail as phase 2 and set mode: no writable open against a
    # live Calibre. It sits before the answer gates so a refused run
    # never prompts.
    if calibre_running():
        # Lock-class refusal (exit 1), matching phase 2, setwrite, and
        # integrate.
        print("ERROR: Calibre is running; close it before phase 3.", file=sys.stderr)
        return 1

    if answer_file:
        try:
            with open(answer_file, encoding="utf-8") as f:
                raw = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"ERROR: cannot read the answer file: {e}", file=sys.stderr)
            return 2
        if not isinstance(raw, dict):
            print(
                "ERROR: the answer file must be a JSON object keyed by book id.",
                file=sys.stderr,
            )
            return 2
        try:
            answers = {int(k): v for k, v in raw.items()}
        except ValueError, TypeError:
            print(
                "ERROR: every answer-file key must be an integer book id.",
                file=sys.stderr,
            )
            return 2
        known = set(imported_ids)
        unknown = sorted(set(answers) - known)
        if unknown:
            # Extra answers are never silently dropped: they name books
            # this manifest did not import, which is usually a mistake.
            print(
                f"WARNING: answer file names ids this manifest did not "
                f"import (ignored): {unknown}",
                file=sys.stderr,
            )
        for book_id, answer in answers.items():
            for field in answer.get("fixes") or {}:
                if str(field).lstrip("#").casefold() in _BANNED_ANSWER_FIELDS:
                    print(
                        f"ERROR: answer file for book {book_id} names {field!r}: "
                        f"{'/'.join(_BANNED_ANSWER_FIELDS)} are never written "
                        "by tools (library NON-NEGOTIABLES).",
                        file=sys.stderr,
                    )
                    return 2
    elif sys.stdin.isatty():
        answers = _prompt_answers(db_path, dossiers)
    else:
        print(
            "ERROR: no --answer-file and stdin is not a TTY; phase 3's "
            "decision gates need answers or a terminal.",
            file=sys.stderr,
        )
        return 2

    from cquarry.write import WritableCalibreDB

    curated = 0
    try:
        with WritableCalibreDB(db_path) as wdb:
            with wdb.batch():
                for d in dossiers:
                    book_id = d["book"]["id"]
                    answer = answers.get(book_id) or {}
                    if not answer:
                        continue
                    if answer.get("tags") is not None:
                        wdb.clear_tags(book_id)
                        for tag in answer["tags"]:
                            wdb.add_tag(book_id, tag)
                    if answer.get("comments_html") is not None:
                        wdb.set_comments(book_id, answer["comments_html"])
                    for field, value in (answer.get("fixes") or {}).items():
                        if field == "title":
                            wdb.update_title(book_id, value)
                        elif field == "authors":
                            wdb.set_authors(book_id, value)
                        elif field == "publisher":
                            wdb.set_publisher(book_id, value or None)
                        elif field == "pubdate":
                            wdb.set_pubdate(book_id, value)
                        else:
                            wdb.set_custom_column(book_id, field, value)
                    curated += 1
    except Exception as e:
        print(f"ERROR: curation batch failed and rolled back: {e}", file=sys.stderr)
        return 1

    scripts = _scripts_dir()
    library_dir = os.path.dirname(db_path)
    if imported_ids:
        ids_csv = ",".join(str(i) for i in imported_ids)
        bindery = _run(
            ["bindery", "run", "phase3", "--ids", ids_csv],
            cwd=library_dir,
            timeout=3600,
        )
        if bindery.returncode not in (0, 1):
            # Failure detail survives --quiet: quiet suppresses
            # decoration, never trouble.
            print(
                f"bindery run phase3 reported: {bindery.stderr.strip()}",
                file=sys.stderr,
            )
        reconcile = _run(
            [
                sys.executable,
                str(scripts / "reconcile_file_metadata.py"),
                library_dir,
                "--apply",
                "--repair-pdf",
                "--id",
                ids_csv,
            ],
            cwd=library_dir,
            timeout=3600,
        )
        if reconcile.returncode not in (0, 1):
            print(f"reconcile reported: {reconcile.stderr.strip()}", file=sys.stderr)
        validate = _run(
            [sys.executable, str(scripts / "validate_metadata.py"), library_dir],
            cwd=library_dir,
            timeout=1800,
        )
        validator_clean = validate.returncode == 0
        mechanical_trouble = bindery.returncode not in (0, 1) or (
            reconcile.returncode not in (0, 1)
        )
    else:
        validator_clean = True
        mechanical_trouble = False

    record_path = os.path.join(
        library_dir,
        ".claude",
        f"project_import_{datetime.now().strftime('%Y%m%d-%H%M%S')}.md",
    )
    os.makedirs(os.path.dirname(record_path), exist_ok=True)
    with open(record_path, "w", encoding="utf-8") as f:
        f.write(f"# Import batch {manifest_path}\n\n")
        f.write(f"- imported: {len(imported_ids)} book(s)\n")
        f.write(f"- curated this run: {curated}\n")
        f.write(f"- validator clean: {validator_clean}\n")
        f.write(
            f"- bindery/reconcile trouble: {'yes' if mechanical_trouble else 'no'}\n"
        )
        f.write(f"- decisions still open: {len(man['decisions_needed'])}\n\n")
        for entry in man["files"]:
            if entry["import"]["imported_id"]:
                f.write(
                    f"- #{entry['import']['imported_id']} "
                    f"{entry['stamps'].get('title', entry['path'])} "
                    f"(source: {entry.get('provenance') or 'unrecorded'})\n"
                )
    if not quiet:
        print(
            f"Phase 3: curated {curated} book(s); validator "
            f"{'clean' if validator_clean else 'NOT clean'}; record {record_path}."
        )
        print("Remember to declare any new taxonomy vocabulary.")
    return 0 if validator_clean and not mechanical_trouble else 1


def sign_manifest(manifest_path: str) -> int:
    """Seal the reviewed manifest for phase 2 (``cquarry run sign``).

    Loads the raw JSON and checks structure but NOT the seal, so both the
    first sign and a re-sign after a deliberate post-sign edit work; the
    human re-signing IS the approval of the new content."""
    try:
        with open(manifest_path, encoding="utf-8") as f:
            man = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR: cannot read the manifest: {e}", file=sys.stderr)
        return 2
    if not isinstance(man, dict):
        print("ERROR: the manifest is not a JSON object.", file=sys.stderr)
        return 2
    problems = manifest.validate(man, check_seal=False)
    if problems:
        print("ERROR: invalid manifest: " + "; ".join(problems), file=sys.stderr)
        return 2
    again = bool(man.get("signed"))
    manifest.sign(man)
    try:
        manifest.save(man, manifest_path)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    lossy = sum(1 for f in man["files"] if f["lossy"]["flagged"])
    print(
        f"{'Re-signed' if again else 'Signed'}: {manifest_path} "
        f"({len(man['approved_for_import'])} approved, {lossy} lossy-flagged)."
    )
    print("phase 2 recomputes the seal at load and refuses any later edit.")
    return 0


INTEGRATE_PHASES = (
    "convert",
    "polish",
    "cover",
    "export",
    "merge",
    "flush",
    "backfill",
    "trash",
)


def dispatch_run(args) -> int:
    """The `cquarry run` subcommand dispatch (the acquisition phases and
    the Phase 19 C integration verbs)."""
    from cquarry_cli.cli import find_db

    # --restrict scopes the read surface only (spec 3.4); the run verbs
    # take explicit targets. main() dispatches `run` before the
    # read-surface refusal gate, so `--restrict EXPR run ...` used to
    # silently ignore the modifier entirely.
    if getattr(args, "restrict", None):
        print(
            "ERROR: --restrict scopes read modes only; the run verbs "
            "take explicit targets (--ids/--search).",
            file=sys.stderr,
        )
        return 2

    if args.phase in INTEGRATE_PHASES:
        from cquarry_cli.integrate import dispatch_integrate

        return dispatch_integrate(args)

    quiet = bool(getattr(args, "quiet", False))
    # Usage guards precede the library resolution for every phase: a
    # missing argument must exit 2 whether or not a library is
    # discoverable, and the seal works on the manifest file alone.
    if args.phase == "sign":
        if not args.manifest:
            print("ERROR: run sign needs --manifest FILE.", file=sys.stderr)
            return 2
        return sign_manifest(args.manifest)
    if args.phase == "phase1" and not args.dir:
        print("ERROR: run phase1 needs the downloads directory.", file=sys.stderr)
        return 2
    if args.phase in ("phase2", "phase3") and not args.manifest:
        print(f"ERROR: run {args.phase} needs --manifest FILE.", file=sys.stderr)
        return 2
    db_path = find_db(getattr(args, "db", None))
    if args.phase == "phase1":
        return run_phase1(
            args.dir,
            db_path,
            bindery_report=args.bindery_report,
            stamp=args.stamp,
            apply_lossy=args.apply_lossy,
            quarantine=args.quarantine,
            quiet=quiet,
        )
    if args.phase == "phase2":
        return run_phase2(
            args.manifest,
            db_path,
            backup_dir=args.backup_dir,
            # No flag means the documented default, never the literal
            # string 'None' the old argparse None produced.
            audience=args.audience or DEFAULT_AUDIENCE,
            quiet=quiet,
        )
    return run_phase3(
        args.manifest,
        db_path,
        answer_file=args.answer_file,
        quiet=quiet,
    )
