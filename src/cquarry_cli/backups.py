"""The one pre-write backup helper.

Three copies of this shape used to live side by side (run.py phase 2,
integrate.py's verbs, setwrite.py's --apply) with drifting exception
types; the shared door is the point. The recorded decision: a
``--backup-dir`` inside the library is a USAGE problem (exit 2 at every
door; all three already mapped it there), so the helper raises the
stdlib-neutral ``ValueError`` and each dispatcher keeps its own
usage-path mapping. Unwritable destinations and sqlite failures are the
same class of problem and raise ``ValueError`` too (setwrite already
wrapped them; run/integrate previously propagated a traceback).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path


def make_backup(db_path: str, backup_dir: str) -> str:
    """A pre-run backup that a second run cannot destroy: the copy is
    timestamped (a fixed name let a rerun overwrite the only restore
    point) and taken through sqlite's backup API, so a hot journal can
    never leave the snapshot internally inconsistent."""
    resolved = Path(backup_dir).expanduser().resolve()
    lib_dir = Path(db_path).resolve().parent
    if resolved == lib_dir or resolved.is_relative_to(lib_dir):
        raise ValueError(
            f"--backup-dir ({resolved}) must sit OUTSIDE the library "
            f"directory ({lib_dir}): a backup beside metadata.db invites "
            "re-import and shares the disk."
        )
    try:
        os.makedirs(resolved, exist_ok=True)
        stem = Path(db_path).stem
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = resolved / f"{stem}-{stamp}.db"
        n = 2
        while dest.exists():
            dest = resolved / f"{stem}-{stamp}-{n}.db"
            n += 1
        src = sqlite3.connect(db_path)
        try:
            dst = sqlite3.connect(str(dest))
            try:
                with dst:
                    src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except (OSError, sqlite3.Error) as e:
        raise ValueError(f"could not write the backup: {e}") from None
    return str(dest)
