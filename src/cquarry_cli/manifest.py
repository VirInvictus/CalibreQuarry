"""The acquisition manifest (``acquisition-manifest/1``): the
machine-readable hand-off between the three import phases and the calling
agent (Phase 17, roadmap box 1).

One JSON file per batch lives in the library-local ``.claude/manifests/``
directory and is RETAINED after phase 3 (Brandon's 2026-09-06 decision):
the prose ``.claude/project_*.md`` records stay the human summary, while
the manifest remains the durable structured record of which batch imported
which book from which file. ``cquarry run phase1`` creates it, phase 2
appends import outcomes, phase 3 consumes it and emits the batch record.

Shape (all writers must keep this honest; :func:`validate` is the gate):

- manifest-level: ``schema``, ``created``/``updated``, ``downloads_dir``,
  ``signed``/``signed_at`` (Brandon's sign-off of the phase-1 report;
  the signature IS standing consent for the lossy repairs the report
  listed, per the 2026-09-06 decision), ``files``, ``quarantines``,
  ``decisions_needed``, ``approved_for_import``.
- per-file: path, format, size, ``provenance`` (the ``#source`` stamp's
  origin, stamped mechanically at import per the 2026-09-06 decision),
  ``verdict``, ``checks``, ``lossy`` (flagged repairs; applied only when
  the manifest is signed), ``repairs`` + ``backup_path``, ``stamps``
  (the seed metadata phase 2 hands ``add_book``), ``duplicate_of``,
  and the phase-2 ``import`` block (imported id, download outcome,
  clears, mechanical fixes, clobber watch).
- ``decisions_needed`` entries carry a ``kind`` from a fixed taxonomy.
  The runner never guesses: refused duplicates (kind ``duplicate``),
  failed/ambiguous metadata downloads (``metadata_download``; the
  2026-09-06 decision pushes residue to phase 3 rather than a GUI
  fallback), missing lossy consent, and anything else the phases cannot
  decide alone land here while the batch continues.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

SCHEMA_NAME = "acquisition-manifest/1"

MANIFESTS_DIRNAME = os.path.join(".claude", "manifests")

#: cc9 `#audience` appended to every imported book (2026-09-06 decision:
#: unconditional, no per-file override in the schema).
DEFAULT_AUDIENCE = "Brandon"

FILE_VERDICTS = (
    "approved_for_import",
    "quarantined",
    "rejected",
    "duplicate_refused",
    "needs_decision",
)

DECISION_KINDS = (
    "duplicate",
    "metadata_download",
    "lossy_consent",
    "manual_repair",
    "investigate",
)

DOWNLOAD_OUTCOMES = (
    "ok",
    "no_result",
    "ambiguous",
    "failed",
    "deferred_to_phase3",
)

_REQUIRED_TOP = (
    "schema",
    "created",
    "updated",
    "downloads_dir",
    "files",
    "quarantines",
    "decisions_needed",
    "approved_for_import",
    "signed",
    "signed_at",
)
_REQUIRED_FILE = (
    "path",
    "format",
    "size",
    "provenance",
    "verdict",
    "checks",
    "lossy",
    "repairs",
    "backup_path",
    "stamps",
    "duplicate_of",
    "import",
)
_REQUIRED_IMPORT = (
    "imported_id",
    "download_outcome",
    "clears",
    "fixes",
    "clobber_watch",
)
_REQUIRED_LOSSY = ("flagged", "repairs")


def manifests_dir(library_dir: str) -> str:
    """The library-local manifests directory (``<library>/.claude/manifests``)."""
    return os.path.join(library_dir, MANIFESTS_DIRNAME)


def new_manifest(downloads_dir: str) -> dict[str, Any]:
    """A fresh, valid manifest for one vetting batch."""
    now = datetime.now(UTC).isoformat()
    return {
        "schema": SCHEMA_NAME,
        "created": now,
        "updated": now,
        "downloads_dir": os.path.abspath(downloads_dir),
        "signed": False,
        "signed_at": None,
        "files": [],
        "quarantines": [],
        "decisions_needed": [],
        "approved_for_import": [],
    }


def new_file_entry(path: str) -> dict[str, Any]:
    """One per-file record with every required key at its honest default."""
    return {
        "path": path,
        "format": os.path.splitext(path)[1][1:].upper(),
        "size": None,
        "provenance": None,
        "verdict": "needs_decision",
        "checks": {},
        "lossy": {"flagged": False, "repairs": []},
        "repairs": [],
        "backup_path": None,
        "stamps": {},
        "duplicate_of": None,
        "import": {
            "imported_id": None,
            "download_outcome": None,
            "clears": {"tags_removed": 0, "rating_cleared": False},
            "fixes": [],
            "clobber_watch": None,
        },
    }


def add_file(manifest: dict[str, Any], entry: dict[str, Any]) -> None:
    """Append a per-file record keyed by path (one entry per file)."""
    existing = {f["path"] for f in manifest["files"]}
    if entry["path"] in existing:
        raise ValueError(f"duplicate manifest file entry: {entry['path']!r}")
    manifest["files"].append(entry)


def approve(manifest: dict[str, Any], paths: list[str]) -> None:
    """Mark files approved for import (the phase-1 verdict the signer sees)."""
    known = {f["path"] for f in manifest["files"]}
    for path in paths:
        if path not in known:
            raise ValueError(f"cannot approve unknown file: {path!r}")
    manifest["approved_for_import"] = sorted(
        set(manifest["approved_for_import"]) | set(paths)
    )


def add_decision(manifest: dict[str, Any], kind: str, **detail: Any) -> dict[str, Any]:
    """Record a decisions_needed entry; the run continues, never guesses."""
    if kind not in DECISION_KINDS:
        raise ValueError(
            f"unknown decision kind {kind!r} (known: {', '.join(DECISION_KINDS)})"
        )
    entry = {"kind": kind, **detail}
    manifest["decisions_needed"].append(entry)
    return entry


def sign(manifest: dict[str, Any]) -> None:
    """Brandon signs the phase-1 report: standing consent for the lossy
    repairs the report listed, and the gate phase 2 refuses to run
    without."""
    manifest["signed"] = True
    manifest["signed_at"] = datetime.now(UTC).isoformat()


def file_by_path(manifest: dict[str, Any], path: str) -> dict[str, Any] | None:
    for entry in manifest["files"]:
        if entry["path"] == path:
            return entry
    return None


def validate(data: Any) -> list[str]:
    """Return every structural problem with ``data`` (empty list = valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]
    for key in _REQUIRED_TOP:
        if key not in data:
            errors.append(f"missing top-level key: {key}")
    if data.get("schema") != SCHEMA_NAME:
        errors.append(f"schema must be {SCHEMA_NAME!r}, got {data.get('schema')!r}")
    files = data.get("files")
    if not isinstance(files, list):
        errors.append("files must be a list")
        return errors
    known_paths: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            errors.append("every file entry must be an object")
            continue
        path = entry.get("path")
        if not path:
            errors.append("file entry missing path")
            continue
        if path in known_paths:
            errors.append(f"duplicate file entry: {path!r}")
        known_paths.add(path)
        for key in _REQUIRED_FILE:
            if key not in entry:
                errors.append(f"{path}: missing file key: {key}")
        if entry.get("verdict") not in FILE_VERDICTS:
            errors.append(f"{path}: unknown verdict {entry.get('verdict')!r}")
        lossy = entry.get("lossy")
        if isinstance(lossy, dict):
            for key in _REQUIRED_LOSSY:
                if key not in lossy:
                    errors.append(f"{path}: lossy missing key: {key}")
        import_block = entry.get("import")
        if isinstance(import_block, dict):
            for key in _REQUIRED_IMPORT:
                if key not in import_block:
                    errors.append(f"{path}: import missing key: {key}")
            outcome = import_block.get("download_outcome")
            if outcome is not None and outcome not in DOWNLOAD_OUTCOMES:
                errors.append(f"{path}: unknown download_outcome {outcome!r}")
    for decision in data.get("decisions_needed", []):
        if not isinstance(decision, dict) or "kind" not in decision:
            errors.append("every decisions_needed entry needs a kind")
        elif decision["kind"] not in DECISION_KINDS:
            errors.append(f"unknown decision kind {decision['kind']!r}")
    for path in data.get("approved_for_import", []):
        if path not in known_paths:
            errors.append(f"approved_for_import names an unlisted file: {path!r}")
    return errors


def save(manifest: dict[str, Any], path: str) -> None:
    """Stamp ``updated`` and write atomically (temp + os.replace)."""
    manifest["updated"] = datetime.now(UTC).isoformat()
    problems = validate(manifest)
    if problems:
        raise ValueError("refusing to save an invalid manifest: " + "; ".join(problems))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".cquarry-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def load(path: str) -> dict[str, Any]:
    """Load and validate a manifest; raises ValueError listing problems."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    problems = validate(data)
    if problems:
        raise ValueError(f"invalid manifest {path!r}: " + "; ".join(problems))
    return data
