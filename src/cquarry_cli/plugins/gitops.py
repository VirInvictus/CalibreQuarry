"""The git seam: every registry git operation is a ``git`` binary spawn.

The closed dependency set holds (no git library); the standard subprocess
seam applies (text mode decodes with errors="replace"; the AST tripwire in
tests/test_scripts.py enforces the blanket). Commands run with a hard
timeout so a wedged remote can never hang the verb.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT = 120


class GitError(RuntimeError):
    """A git operation failed; the message carries the command and stderr."""


def _run(args: list[str], cwd: Path | None = None) -> str:
    cmd = ["git", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError(f"{' '.join(cmd)} failed: {e}") from e
    if proc.returncode != 0:
        raise GitError(
            f"{' '.join(cmd)} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()}"
        )
    return proc.stdout.strip()


def clone(url: str, dest: Path) -> str:
    """Clone ``url`` into ``dest`` (must not exist); returns the HEAD sha."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["clone", "--quiet", url, str(dest)])
    return _run(["rev-parse", "HEAD"], cwd=dest)


def fetch(path: Path) -> None:
    _run(["fetch", "--quiet", "origin"], cwd=path)


def sync(path: Path) -> str:
    """Fetch and move the clone's working tree to the fetched state.

    A registry-managed clone has no local work of its own, and a bare
    ``fetch`` never touches the checked-out files -- the tree would stay
    stale while HEAD reported progress. The sync is fetch plus a hard
    reset to FETCH_HEAD, then the new HEAD sha.
    """
    fetch(path)
    _run(["reset", "--hard", "--quiet", "FETCH_HEAD"], cwd=path)
    return head_commit(path)


def head_commit(path: Path) -> str:
    return _run(["rev-parse", "HEAD"], cwd=path)


def is_repo(path: Path) -> bool:
    try:
        _run(["rev-parse", "--is-inside-work-tree"], cwd=path)
        return True
    except GitError:
        return False


def slug_for(url: str) -> str:
    """A cache-directory slug from a git URL or local path's basename."""
    tail = url.rstrip("/").rsplit("/", 1)[-1]
    if tail.endswith(".git"):
        tail = tail[: -len(".git")]
    return tail or "source"
