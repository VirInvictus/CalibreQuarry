# Quarry.py — Patch Notes

## v1.0.4 (2026-04-08)

---

### New Features

**Curses TUI.** Running the script with no arguments now launches a full-screen, arrow-key navigable terminal UI utilizing the `curses` library (matching the interface of `getMusic`). Non-TTY environments or systems without `curses` will gracefully fall back to a styled text-based box menu. The TUI features a custom scrollable pager that intercepts standard output, allowing you to comfortably read and navigate command outputs directly within the interface.

### Bug Fixes

**Implicit AND in VL expressions ignored subsequent tags.** Calibre's parser evaluates adjacent tags like `tags:Fic.Fantasy tags:Magic` as an implicit `AND`. The `_parse_and` method previously discarded tags after the first one unless the `AND` keyword was explicitly written out. It now correctly intersects all implicit constraints.

**Exact tag matching (`=`) was case-sensitive.** Calibre's tag matches are always case-insensitive. The exact match SQL query (`WHERE t.name = ?`) was missing `COLLATE NOCASE`, causing `tags:"=Fic.Fantasy"` to fail if capitalization varied.

**Duplicate author headers in `--primary-only` mode.** Generating a catalog with `--primary-only` caused highly fragmented author groups. The script relied solely on the SQL `ORDER BY b.author_sort` (which sorts by the *full* multi-author string). Books are now presorted in Python natively by their derived primary-only display key.

**Non-deterministic `GROUP_CONCAT` output.** The metadata fields built via `GROUP_CONCAT(DISTINCT ...)` (like `authors` and `tags`) returned unpredictably ordered results depending on SQLite's internal row execution. This occasionally resulted in the wrong primary author being selected. The SQL query has been rewritten to use correlated subqueries with explicit `ORDER BY` clauses for deterministic structure.

**Double `NOT` cascading crashes.** Expressions combining consecutive exclusions (e.g., `NOT NOT vl:Name`) failed because `_parse_not` routed directly to `_parse_atom` for the inner operand. This has been updated to recursively call `_parse_not` to handle complex nested negations gracefully.

**Fractional series indices ignored in recent display.** `show_recent` dropped series identifiers completely if the index contained a decimal (e.g., `1.5`) due to a missing fallback formatting block.

---

## v1.0.3 (2026-04-04)

---

### Bug Fixes

**`_read_value` crashed on unmatched quotes in VL expressions.**
`expr.index('"')` raised `ValueError` if a virtual library search expression
had an opening quote with no closing quote. A single malformed VL definition
took down the entire tool. Now consumes the rest of the string as the value.

**Series index `0.0` silently dropped.** `if idx and idx == int(idx)` treated
`0.0` as falsy — any book at series position zero lost its series display. Hit
in both `write_catalog` and `show_recent`. Changed to explicit `is not None`
checks.

**Division by zero in `show_stats`.** Empty library crashed on three separate
lines: format bar chart (`count * 40 // total`), rating bar chart
(`max(rating_counts.values())`), and unrated percentage
(`unrated * 100 / total`). All three guarded.

**`max_index` None dereference in `show_series`.**
`s['max_index'] == int(s['max_index'])` threw `TypeError` when `max_index` was
None. Same fix propagated into `detect_series_gaps`.

**`format_stars` produced garbage on corrupt ratings.** A DB value of 12 (6.0
stars) yielded a negative `empty` count. Python silently returns `""` for
`"☆" * -1`, so no crash — but the display was meaningless. Rating now clamped
to 0–5.

**CSV export blanked zero-valued fields.** `stars or ''` and
`series_index or ''` used the `or` pattern, which treats `0.0` as falsy.
Changed to explicit `is not None` checks.

**JSON export had leading whitespace in split fields.**
`b['authors'].split(',')` on `GROUP_CONCAT` output produced
`["Author1", " Author2"]`. All split fields now strip.

**Tokenizer keyword boundary missed underscores.** `isalnum()` doesn't match
`_`, so a hypothetical tag starting with `or_` or `not_` would misparse as a
boolean operator. Boundary check now includes underscore.

**`_prompt_str` displayed `[None]` in interactive prompts.** When called with
`default=None`, the user saw the literal text `[None]`. Now shows empty
brackets.

### Performance

**`get_all_books()` results cached.** The 8-JOIN metadata query was called once
per wing in `--all-wings` mode — 18 times against a 3,800-book library. Now
fires once and returns the cached list.

**`_get_all_book_ids()` cached for NOT operations.** The VL parser queried
`SELECT id FROM books` on every `NOT` clause. Multiple NOT expressions in a
single VL definition hammered the DB. Cached on first call.

**`count_books()` uses warm caches.** If the books or IDs cache is already
populated, returns `len()` instead of hitting SQLite.

**Parser built once in `main()`.** Was constructing `build_parser()` to parse
args, then building it again on the help-output fallthrough path. Stored the
reference.

### New Features

**`--version` flag.** Uses `action="version"` so argparse handles it during
`parse_args()` — works even when no database is present. Version also shown in
the interactive menu banner.

### Code Hygiene

**Unused imports removed.** `defaultdict` and `Path` — imported, never
referenced.

**f-string with no placeholders.** `f"\nLanguages:"` → `"\nLanguages:"`.

**`show_wings` caught bare `Exception`.** Narrowed to `ValueError`, which is
what `resolve_vl` actually raises.

**`quiet` parameter wired up everywhere.** `show_recent`, `show_series`, and
`show_stats` all accepted `quiet` but ignored it. Now suppresses headers and
decorative output when passed.

**`main()` catches `PermissionError`.** A read-only DB with wrong filesystem
permissions previously produced an unhandled traceback.

**`_match_tags` docstring corrected.** Said "containing" for the non-exact case
but the SQL does prefix match, not substring. Added a note that regex patterns
(`tags:~regex`) are unsupported.

**`prog="Quarry.py"` added to `ArgumentParser`.** Version and help output now
show the script name consistently regardless of invocation path.
