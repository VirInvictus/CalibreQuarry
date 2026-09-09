"""The read surface's output guard.

The 2026-09-08 sweep's P0: no output writer compared its path to the
database path, so ``--export --output <library>/metadata.db`` replaced a
fixture database with a JSON report, exit 0. The read-only contract held
at the SQL layer only. Every read-mode file output goes through
:func:`open_output` (directory-target exporters through
:func:`ensure_output_dir`), which refuses the database and its sqlite
sidecars and writes through a temp file so a failed report never leaves
a truncated output behind.
"""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from typing import IO, Iterator

#: Clobbering a sidecar damages a live database just as surely as the
#: main file, so the refusal covers all three.
_SIDECARS = ("-wal", "-shm", "-journal")


class OutputRefusedError(ValueError):
    """An output target collides with the database; refused."""


def refused_target(path: str, db_path: str | None) -> str | None:
    """Why ``path`` may not be written given this database, or None."""
    if not db_path:
        return None
    db = os.path.abspath(os.path.expanduser(db_path))
    out = os.path.abspath(os.path.expanduser(path))
    if out == db:
        return f"{path} is the database itself"
    for suffix in _SIDECARS:
        if out == db + suffix:
            return f"{path} is the database sidecar {os.path.basename(db)}{suffix}"
    return None


@contextmanager
def open_output(
    output: str | None, db_path: str | None
) -> Iterator[tuple[IO, str | None]]:
    """Yield (stream, path) for one output file: stdout when ``output`` is
    falsy (path None), else a temp file replaced into place only after the
    writer closed clean. Raises OutputRefusedError when the target is the
    database or one of its sidecars."""
    if not output:
        yield sys.stdout, None
        return
    out_path = os.path.abspath(os.path.expanduser(output))
    reason = refused_target(out_path, db_path)
    if reason:
        raise OutputRefusedError(f"refusing to write the output: {reason}")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    tmp = out_path + ".cquarry-tmp"
    stream = open(tmp, "w", newline="", encoding="utf-8")
    try:
        yield stream, out_path
        stream.close()
        os.replace(tmp, out_path)
    finally:
        if not stream.closed:
            stream.close()
        if os.path.exists(tmp):
            os.remove(tmp)


def ensure_output_dir(outdir: str, db_path: str | None) -> str:
    """Validate and create a directory-target exporter's output directory.

    Beyond the database collision, the library root itself is refused: the
    directory exporters sweep and write beside the very files the
    read-only contract protects. Returns the resolved directory."""
    out = os.path.abspath(os.path.expanduser(outdir))
    reason = refused_target(out, db_path)
    if reason:
        raise OutputRefusedError(f"refusing to write the export: {reason}")
    if db_path:
        lib_root = os.path.dirname(os.path.abspath(os.path.expanduser(db_path)))
        if out == lib_root:
            raise OutputRefusedError(
                f"refusing to write the export into the library directory: {outdir}"
            )
    os.makedirs(out, exist_ok=True)
    return out
