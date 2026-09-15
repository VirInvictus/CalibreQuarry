"""The read-only trash surface (``--trash``).

``run merge`` sends duplicates to cquarry's ``.caltrash/`` and its own
docs say "recoverable by hand" -- this listing is what makes that
recovery possible: one line per trash entry (category, book id, age,
file count) straight from the filesystem, mirroring upstream's layout
(``b/<id>/`` for books, ``f/<id>/`` for formats). The lifecycle verbs
themselves are cquarry's (``run trash`` drives them); this module only
looks. Library-shape like the tree audit: no ``--restrict`` scoping,
because the trash is not part of any book universe.
"""

from __future__ import annotations

import os
import time

from cquarry.db import CalibreDB
from cquarry.helpers import C_TITLE, color

_TRASH_DIRNAME = ".caltrash"
_CATEGORIES = (("b", "book"), ("f", "format"))


def collect_trash_entries(library_dir: str) -> list[dict]:
    """Inventory ``<library>/.caltrash``: ``{category, book_id, age_days,
    files}`` sorted by category then id. A missing trash dir is an empty
    list (the common case before the first merge)."""
    root = os.path.join(library_dir, _TRASH_DIRNAME)
    now = time.time()
    out: list[dict] = []
    for dirname, label in _CATEGORIES:
        base = os.path.join(root, dirname)
        if not os.path.isdir(base):
            continue
        for name in sorted(os.listdir(base)):
            path = os.path.join(base, name)
            if not os.path.isdir(path):
                continue
            files: list[str] = []
            for sub_root, _dirs, names in os.walk(path):
                files.extend(names)
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                mtime = 0.0
            out.append(
                {
                    "category": label,
                    "book_id": int(name) if name.isdigit() else name,
                    "age_days": max(0.0, (now - mtime) / 86400),
                    "files": sorted(files),
                }
            )
    out.sort(key=lambda e: (e["category"], str(e["book_id"])))
    return out


def show_trash(db: CalibreDB, *, quiet: bool = False) -> None:
    """Render the trash listing; exit surfaces through the caller."""
    library_dir = os.path.dirname(os.path.abspath(db.db_path))
    entries = collect_trash_entries(library_dir)
    if not entries:
        print(f"No trash under {library_dir} (nothing merged away yet).")
        return
    if not quiet:
        print(color(f"=== Trash ({len(entries)} entries) ===", C_TITLE))
        print()
    for e in entries:
        print(
            f"  [{e['category']}] book {e['book_id']}: "
            f"{len(e['files'])} file(s), {e['age_days']:.1f} days old"
        )
        for name in e["files"]:
            print(f"      {name}")
    print()
    total = sum(len(e["files"]) for e in entries)
    print(
        f"{len(entries)} entries, {total} files. "
        "Recover by hand, or `cquarry run trash --empty` / `--expire DAYS` "
        "(dry run by default; --apply executes)."
    )
