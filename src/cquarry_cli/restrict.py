"""The --restrict scoping view (Phase 19 A.2).

A read-only CalibreDB subclass whose universe is the set of book ids
matching a --restrict search expression (a virtual library reaches it
the same way, as ``--restrict 'vl:Name'``). The view scopes only the
inputs the read surface consumes; every predicate and stat is still
derived by cquarry over the restricted universe: the analytics
functions and the integrity predicates call ``get_all_books()`` through
the view, the series rollup recomputes from the scoped rows, and wing
resolution intersects. Re-deriving any of those here would fork
cquarry's logic; scoping the inputs does not.

The two aggregation methods that are SQL inside cquarry (entity counts,
format stats) are recounted from the scoped book rows and merged with
the real rows' secondary columns, so ``--entities`` and
``--format-stats`` show restricted counts instead of global ones.
"""

import argparse

from cquarry.db import CalibreDB


# The write-verb dests (single-book, set-mode target sources, and every
# --batch-* verb). --restrict is a read-surface scoping modifier: write
# targets are chosen by --ids/--from-search, not by scoping, so the
# combination is refused rather than silently ignored. Keep in step with
# build_parser()'s write/set groups.
_WRITE_FLAG_DESTS = (
    "set_title",
    "set_authors",
    "set_rating",
    "set_pubdate",
    "clear_pubdate",
    "set_comments",
    "clear_comments",
    "set_column",
    "clear_column",
    "add_tag",
    "remove_tag",
    "set_identifier",
    "clear_identifier",
    "set_series",
    "clear_series",
    "set_publisher",
    "clear_publisher",
    "set_languages",
    "clear_languages",
    "add_format",
    "remove_format",
    "set_cover",
    "remove_book",
    "set_ids",
    "from_search",
    "from_untagged",
    "from_manifest",
    "batch_add_tag",
    "batch_remove_tag",
    "batch_clear_tags",
    "batch_clear_rating",
    "batch_set_column",
    "batch_clear_column",
    "batch_add_column_value",
    "batch_set_title",
    "batch_set_authors",
    "batch_set_pubdate",
    "batch_clear_pubdate",
    "batch_set_publisher",
    "batch_clear_publisher",
    "batch_set_languages",
    "batch_clear_languages",
    "batch_set_series",
    "batch_clear_series",
    "batch_set_identifier",
    "batch_clear_identifier",
    "batch_set_cover",
    "batch_remove_format",
)


def restrict_refusal(args: argparse.Namespace) -> str | None:
    """Why --restrict cannot compose with this invocation, or None if it can."""
    for dest in _WRITE_FLAG_DESTS:
        if getattr(args, dest, None):
            return (
                "scopes read modes only; write targets are chosen by "
                "--ids/--from-search, not by restriction"
            )
    if getattr(args, "book", None) is not None or getattr(args, "untagged", False):
        return (
            "does not scope --book: an explicit id list is not a set to "
            "narrow (a scoped untagged sweep would also hide ids)"
        )
    if getattr(args, "book_id", None) is not None:
        return "does not scope --id: it names one explicit book"
    return None


class RestrictedView(CalibreDB):
    """A CalibreDB read view limited to a fixed set of book ids.

    Built from an already-open CalibreDB (whose ``__dict__`` is shared,
    so the connection, cache, and helpers carry over). Every method not
    overridden here behaves exactly as the real database's, but sees the
    restricted universe through the overridden collection methods.
    """

    def __init__(self, db: CalibreDB, ids: set[int]):
        self.__dict__.update(db.__dict__)
        self._ids = frozenset(ids)

    @property
    def restrict_ids(self) -> frozenset[int]:
        return self._ids

    # --- the collection methods every mode and predicate consumes ---

    def get_all_books(self) -> list[dict]:
        return [b for b in CalibreDB.get_all_books(self) if b["id"] in self._ids]

    def count_books(self) -> int:
        return len(self.get_all_books())

    def all_ids(self) -> set[int]:
        return set(self._ids)

    def search(self, query: str) -> set[int]:
        return CalibreDB.search(self, query) & self._ids

    def resolve_vl(self, vl_name: str) -> set[int]:
        return CalibreDB.resolve_vl(self, vl_name) & self._ids

    def get_all_tags(self) -> list[str]:
        # Distinct tags across the restricted books (the real method reads
        # the tags table globally; the scoped universe is the book rows).
        tags = {t for b in self.get_all_books() for t in (b["tags"] or [])}
        return sorted(tags)

    def get_dirtied_books(self) -> list[int]:
        return [i for i in CalibreDB.get_dirtied_books(self) if i in self._ids]

    def get_annotations_dirtied_books(self) -> list[int]:
        return [
            i for i in CalibreDB.get_annotations_dirtied_books(self) if i in self._ids
        ]

    def get_conversion_profiles(self, book_id: int | None = None) -> list[dict]:
        if book_id is not None and book_id not in self._ids:
            return []
        return [
            row
            for row in CalibreDB.get_conversion_profiles(self)
            if row["book"] in self._ids
        ]

    def get_last_read_positions(self, book_id: int | None = None) -> list[dict]:
        if book_id is not None and book_id not in self._ids:
            return []
        return [
            row
            for row in CalibreDB.get_last_read_positions(self)
            if row["book"] in self._ids
        ]

    def get_annotations(self, book_id: int | None = None) -> list[dict]:
        if book_id is not None and book_id not in self._ids:
            return []
        return [
            row
            for row in CalibreDB.get_annotations(self, book_id)
            if row["book"] in self._ids
        ]

    # --- the per-book getters: outside the universe nothing exists ---

    def get_book(self, book_id: int, include_comments: bool = False):
        if book_id not in self._ids:
            return None
        return CalibreDB.get_book(self, book_id, include_comments)

    def get_book_dossier(self, book_id: int, *, include_comments: bool = False):
        if book_id not in self._ids:
            return None
        return CalibreDB.get_book_dossier(
            self, book_id, include_comments=include_comments
        )

    # --- the SQL-level aggregations, recounted over the scoped rows ---

    def get_entities(self, kind: str) -> list[dict]:
        # Counts recomputed from the scoped book rows (each book counts
        # once per linked entity, matching the real link-row semantics);
        # sort/link and entity ids merge back from the real rows by name.
        recount: dict[str, int] = {}
        for b in self.get_all_books():
            if kind == "authors":
                names = b["authors"] or []
            elif kind == "series":
                names = [b["series"]] if b["series"] else []
            elif kind == "publishers":
                names = [b["publisher"]] if b["publisher"] else []
            elif kind == "tags":
                names = b["tags"] or []
            elif kind == "languages":
                names = b["languages"] or []
            elif kind == "ratings":
                names = [str(b["rating"])] if b["rating"] is not None else []
            else:
                # Unknown kinds raise through the real method, unchanged.
                return CalibreDB.get_entities(self, kind)
            for name in names:
                recount[name] = recount.get(name, 0) + 1
        # Merge with the real rows by name so sort/link and the entity id
        # survive; an entity absent from the real rows (link-table drift)
        # still reports its scoped count with empty secondary columns.
        by_name = {real["name"]: real for real in CalibreDB.get_entities(self, kind)}
        rows = []
        for name, count in recount.items():
            real = by_name.get(name) or {}
            rows.append(
                {
                    "id": real.get("id"),
                    "name": name,
                    "sort": real.get("sort", ""),
                    "link": real.get("link", ""),
                    "count": count,
                }
            )
        rows.sort(key=lambda r: r["name"] or "")
        return rows

    def get_format_stats(self) -> dict[str, dict[str, int]]:
        # Per-format counts and bytes over the scoped books, via the
        # per-book format rows (which carry size_bytes).
        stats: dict[str, dict[str, int]] = {}
        for b in self.get_all_books():
            for fmt, meta in CalibreDB.get_formats(self, b["id"]).items():
                slot = stats.setdefault(fmt.upper(), {"count": 0, "bytes": 0})
                slot["count"] += 1
                slot["bytes"] += meta.get("size_bytes") or 0
        return dict(sorted(stats.items()))
