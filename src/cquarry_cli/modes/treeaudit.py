"""The filesystem-vs-database tree audit (Phase 19 A.3).

The CQ-native route, chosen over a ``calibredb check_library``
subprocess: this package's contract carries no calibredb dependency,
the audit composes with --restrict and the shared CSV shape, and
upstream's check class list (calibre/library/check_library.py CHECKS)
is the completeness checklist this module walks, not a program to
shell out to.

Classes (issue_type ``tree`` in --audit's CSV; the class name rides in
the issues column):

- ``missing_book_dir``: a book whose stored path does not exist on disk.
- ``missing_format_file``: a data-table format whose file is gone.
- ``extra_format_file``: a book-format file on disk that no data row
  claims (the classic leftover after a failed delete or a manual copy).
- ``extra_book_file``: an unknown file (or directory other than data/)
  inside a book directory.
- ``extra_cover_file``: a cover file on disk for a book whose
  has_cover flag is false (the DB does not claim it).
- ``orphan_book_dir``: a ``Title (id)`` directory whose id is not in
  the library at all (checked against the ORIGIN database: orphans
  are library shape, not restriction shape).
- ``orphan_author_dir``: a first-level directory that is no book's
  author component (likewise global).
- ``malformed_book_dir``: a second-level directory that does not end
  in ``Title (id)`` form, so no book can ever claim it.
- ``extra_library_file``: a stray file under an author directory or at
  the library root (metadata.db, its sqlite sidecars, the FTS sidecar,
  and Calibre's backup/restore files are whitelisted).
- ``failed_folder``: a directory that raised on scan (permissions), so
  the silence of the other classes stays trustworthy.

False-positive tolerances, on purpose: ``metadata.opf``, any ``*.opf``
(Calibre's legacy per-book metadata habit), ``cover.jpg/jpeg/png``,
and the ``data/`` directory are never extras; on-disk name comparison
is case-insensitive, so an extension cased differently from the data
row is not reported as a missing-plus-extra pair.

Book-level classes (missing dir/format/cover/extras) follow the active
--restrict view; library-shape classes (orphans, malformed, strays)
always report globally, since they belong to no restriction.
"""

import os
import re

from cquarry.db import CalibreDB
from cquarry.helpers import C_WARN, color

# Upstream's known book-format extensions (calibre/ebooks
# BOOK_EXTENSIONS, mirrored here): a file with one of these extensions
# inside a book directory that no data row claims is an extra format.
BOOK_EXTENSIONS = frozenset(
    {
        "lrf",
        "rar",
        "zip",
        "rtf",
        "lit",
        "txt",
        "txtz",
        "text",
        "htm",
        "xhtm",
        "html",
        "htmlz",
        "xhtml",
        "pdf",
        "pdb",
        "updb",
        "pdr",
        "prc",
        "mobi",
        "azw",
        "doc",
        "epub",
        "fb2",
        "fbz",
        "djv",
        "djvu",
        "lrx",
        "cbr",
        "cb7",
        "cbz",
        "cbc",
        "oebzip",
        "rb",
        "imp",
        "odt",
        "chm",
        "tpz",
        "azw1",
        "pml",
        "pmlz",
        "mbp",
        "tan",
        "snb",
        "xps",
        "oxps",
        "azw4",
        "book",
        "zbf",
        "pobi",
        "docx",
        "docm",
        "md",
        "textile",
        "markdown",
        "ibook",
        "ibooks",
        "iba",
        "azw3",
        "ps",
        "kepub",
    }
)

_COVER_STEMS = {"cover"}
_COVER_EXTS = {"jpg", "jpeg", "png"}
_METADATA_BASENAME = "metadata.opf"
_DATA_DIR_NAME = "data"

_ROOT_IGNORES = {
    "metadata.db",
    "metadata.db-wal",
    "metadata.db-shm",
    "metadata.db-journal",
    "full-text-search.db",
    "full-text-search.db-wal",
    "full-text-search.db-shm",
    "full-text-search.db-journal",
    "metadata_db_prefs_backup.json",
    "metadata_pre_restore.db",
    ".caltrash",
    ".calnotes",
}

_BOOK_DIR_ID = re.compile(r"^(.*) \((\d+)\)$")


def _norm_author_dir(path: str | None) -> str:
    return (path or "").replace(os.sep, "/").partition("/")[0]


def tree_audit(db: CalibreDB) -> list[dict[str, str]]:
    """Walk the library tree read-only; return --audit rows for it."""
    libdir = os.path.dirname(os.path.abspath(db.db_path))
    origin = getattr(db, "origin", db)
    all_ids = origin.all_ids()
    # Orphan-author detection is library shape: it needs every book's
    # author component, not the restricted subset's.
    author_dirs = {_norm_author_dir(b["path"]) for b in origin.get_all_books()}

    rows: list[dict[str, str]] = []

    def _row(book_id: int | str, path: str, issues: str) -> dict[str, str]:
        return {
            "id": str(book_id),
            "title": path,
            "author": "",
            "issue_type": "tree",
            "issues": issues,
        }

    for b in db.get_all_books():
        rel = (b["path"] or "").replace(os.sep, "/")
        if not rel:
            continue
        book_path = os.path.join(libdir, *rel.split("/"))
        formats = db.get_formats(b["id"]) or {}
        try:
            entries = list(os.scandir(book_path))
        except FileNotFoundError:
            rows.append(_row(b["id"], rel, "missing_book_dir"))
            continue
        except OSError as e:
            rows.append(_row(b["id"], rel, f"failed_folder: {e.strerror or e}"))
            continue

        on_disk = {e.name: e for e in entries}
        on_disk_lower = {e.name.lower(): e for e in entries}
        claimed_lower = set()
        for fmt, meta in formats.items():
            name = meta.get("name")
            if name:
                claimed_lower.add(f"{name}.{fmt.lower()}".lower())

        has_cover = bool(b["has_cover"])
        for fname, entry in on_disk.items():
            if entry.is_dir():
                if fname != _DATA_DIR_NAME:
                    rows.append(_row(b["id"], f"{rel}/{fname}", "extra_book_file"))
                continue
            stem, dot, ext = fname.rpartition(".")
            ext_l = ext.lower() if dot else ""
            if fname.lower() in claimed_lower:
                continue
            if stem.lower() in _COVER_STEMS and ext_l in _COVER_EXTS:
                if not has_cover:
                    rows.append(_row(b["id"], f"{rel}/{fname}", "extra_cover_file"))
                continue
            if fname == _METADATA_BASENAME or ext_l == "opf":
                continue
            if ext_l in BOOK_EXTENSIONS:
                rows.append(_row(b["id"], f"{rel}/{fname}", "extra_format_file"))
            else:
                rows.append(_row(b["id"], f"{rel}/{fname}", "extra_book_file"))

        for fmt, meta in formats.items():
            name = meta.get("name")
            if name and f"{name}.{fmt.lower()}".lower() not in on_disk_lower:
                rows.append(
                    _row(
                        b["id"],
                        f"{rel}/{name}.{fmt.lower()}",
                        "missing_format_file",
                    )
                )

    try:
        top = list(os.scandir(libdir))
    except OSError:
        # No readable library root: the disk-side classes would lie.
        return rows

    for entry in top:
        if entry.name in _ROOT_IGNORES or not entry.is_dir():
            continue
        if entry.name not in author_dirs:
            rows.append(_row("", entry.name, "orphan_author_dir"))
            continue
        try:
            book_dirs = list(os.scandir(entry.path))
        except OSError as e:
            rows.append(_row("", entry.name, f"failed_folder: {e.strerror or e}"))
            continue
        for bd in book_dirs:
            if not bd.is_dir():
                rows.append(_row("", f"{entry.name}/{bd.name}", "extra_library_file"))
                continue
            m = _BOOK_DIR_ID.match(bd.name)
            if not m:
                rows.append(_row("", f"{entry.name}/{bd.name}", "malformed_book_dir"))
            elif int(m.group(2)) not in all_ids:
                rows.append(
                    _row(m.group(2), f"{entry.name}/{bd.name}", "orphan_book_dir")
                )

    for entry in top:
        if not entry.is_dir() and entry.name not in _ROOT_IGNORES:
            rows.append(_row("", entry.name, "extra_library_file"))

    return rows


def print_tree_summary(tree_rows: list[dict[str, str]], *, quiet: bool = False) -> None:
    """The --audit prose section for the tree rows (already filtered)."""
    if not tree_rows:
        if not quiet:
            print("\nFilesystem tree: no discrepancies found.")
        return
    from collections import Counter

    counts = Counter(r["issues"].split(":")[0] for r in tree_rows)
    print("\n" + color(f"Filesystem tree: {len(tree_rows)} finding(s)", C_WARN))
    for cls, count in counts.most_common():
        print(f"  {cls}: {count}")
    shown = 0
    for r in tree_rows:
        if shown >= 10:
            print(f"  ... and {len(tree_rows) - shown} more")
            break
        suffix = f" (id {r['id']})" if r["id"] else ""
        print(f"  {r['title']}: {r['issues']}{suffix}")
        shown += 1
