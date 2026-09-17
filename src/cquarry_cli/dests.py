"""The write-verb dest lists, in one place.

Three modules used to keep three parallel lists in step by comment and
test alone (writeops' SINGLE_BOOK_DESTS, restrict's _WRITE_FLAG_DESTS,
setwrite's _SOURCES and _has_verbs tuples); a new write dest had to be
typed in every one of them. This module is the single source: each
consumer imports its slice, and tests/test_dests.py pins every member
against build_parser() so a dest that exists only here cannot rot.

Partition (disjoint by construction, tested):
- SINGLE_BOOK_DESTS   the single-book write verbs, in dispatch priority order
- SET_MODE_SOURCES    set mode's exactly-one target sources
- BATCH_VALUE_DESTS   --batch-* verbs carrying a value (counted by presence)
- BATCH_BOOL_DESTS    store_true --batch-clear-* verbs (counted by truthiness)
- WRITE_FLAG_DESTS    the restrict-refusal aggregate over all four
"""

from __future__ import annotations

# The single-book verb dests, in dispatch priority order. add_tag and
# remove_tag are append-list dests: dispatch_write counts their extra
# occurrences separately (three tag adds are three mutations).
SINGLE_BOOK_DESTS: list[str] = [
    "set_title",
    "set_authors",
    "set_rating",
    "set_pubdate",
    "clear_pubdate",
    "set_comments",
    "clear_comments",
    "add_tag",
    "remove_tag",
    "set_column",
    "clear_column",
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
    "rename_entity",
    "set_author_sort",
    "set_title_sort",
]

# Set mode's target sources: exactly one per invocation.
SET_MODE_SOURCES: tuple[str, ...] = (
    "set_ids",
    "from_search",
    "from_untagged",
    "from_manifest",
)

# Value-bearing --batch-* verbs: an empty string is a real argument whose
# refusal must reach the user, so _has_verbs counts these by PRESENCE,
# never truthiness.
BATCH_VALUE_DESTS: tuple[str, ...] = (
    "batch_add_tag",
    "batch_remove_tag",
    "batch_set_column",
    "batch_clear_column",
    "batch_add_column_value",
    "batch_set_title",
    "batch_set_authors",
    "batch_set_pubdate",
    "batch_set_publisher",
    "batch_set_languages",
    "batch_set_series",
    "batch_set_identifier",
    "batch_clear_identifier",
    "batch_set_cover",
    "batch_remove_format",
)

# The store_true --batch-clear-* flags: their False default would read as
# present, so _has_verbs counts these by truthiness.
BATCH_BOOL_DESTS: tuple[str, ...] = (
    "batch_clear_tags",
    "batch_clear_rating",
    "batch_clear_pubdate",
    "batch_clear_publisher",
    "batch_clear_languages",
    "batch_clear_series",
)

# --restrict is a read-surface scoping modifier: write targets are chosen
# by --ids/--from-search, not by scoping, so the combination is refused
# rather than silently ignored. Every dest a write could ride.
WRITE_FLAG_DESTS: tuple[str, ...] = (
    tuple(SINGLE_BOOK_DESTS) + SET_MODE_SOURCES + BATCH_VALUE_DESTS + BATCH_BOOL_DESTS
)
