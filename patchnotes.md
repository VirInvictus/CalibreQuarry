# CalibreQuarry — Patch Notes

## v2.3.0 (2026-04-16)

---

### New Features

**Extended Audit Checks.** The `--audit` mode has been significantly expanded to include three new checks:
- **Duplicate Detection**: Identifies books with identical titles and primary authors across the library.
- **Cover Quality Audit**: Scans the actual JPEG cover files on disk (without external library dependencies) and flags covers with low resolution (below 500px on their longest edge).
- **Format Migration Report**: Flags books that are only available in deprecated legacy formats (MOBI, LIT, LRF, DJVU, PDB, AZW).

---

## v2.2.0 (2026-04-16)

---

### New Features

**Extended Analytics.** Added a new `--analytics` argument with four detailed reporting modes: `author` (per-author breakdowns of formats, ratings, and series), `pace` (books added per month/year trend), `tags` (hierarchical taxonomy tree visualization), and `overlap` (virtual library wing overlap analysis). These are also accessible via a new `ANALYTICS` section in the interactive TUI.

---

## v2.1.0 (2026-04-16)

---

### New Features

**Search Query Export.** You can now pass arbitrary Calibre search expressions directly to the CLI via `--search "query"` to export matching books. The results are written to a plain text file. This feature is also accessible via the interactive TUI under the `OUTPUT` menu.

**AI-Readable Export.** Added a new `ai` format to the `--export` option. This format outputs the library data as a highly token-efficient, flat text list designed specifically for LLM ingestion and recommendation prompts (e.g., `Title by Author [Tags] - Rating/5`).

---

## v2.0.1 (2026-04-12)

---

### New Features

**Top Authors and Top Tags in `--stats`.** The statistics output now includes a "Top authors" section (10 most prolific by book count) and a "Top tags" section (15 most-used tags), inserted between the ratings distribution and the tag taxonomy breakdown.

---

## v2.0.0 (2026-04-12)

---

### Major Overhaul: Package Restructure & TUI Upgrades

CalibreQuarry has been refactored from a single ~1450-line monolithic script (`cquarry.py`) into a proper Python package architecture, with TUI improvements modeled after the Lattice project.

**Layer-Based Package Design.** The codebase now lives in `src/cquarry/` and is split by logical functionality: `config.py`, `db.py`, `helpers.py`, `cli.py`, `tui.py`, and a `modes/` directory for individual feature operations (`catalog.py`, `stats.py`, `audit.py`, `display.py`, `export.py`). The monolithic script is gone.

**Modern Build System (Hatch).** CalibreQuarry now uses `pyproject.toml` managed by Hatch. Install via `pip install .` or `pipx install .` and the `cquarry` command is available globally. Also runnable via `python -m cquarry`.

**Persistent Database Configuration.** Both CLI and TUI now share a unified database resolution chain: explicit `--db` flag, saved config (`~/.config/cquarry/config.json`), default search paths, then an interactive prompt if running in a TTY. The path is saved on first successful resolution, eliminating the need to pass `--db` in future sessions. A "Change database path" option under the SETTINGS section in the TUI main menu allows updating the stored path.

**Calibre Lock Handling.** When `metadata.db` is locked by a running Calibre instance, CalibreQuarry now automatically copies the database (including WAL/SHM journal files) to a temporary snapshot and reads from that instead. A notice is printed to stderr, and the temp files are cleaned up on exit. Previously, a locked database would produce an unhandled `sqlite3.OperationalError`.

**Fully Immersive TUI.** All operations now run through a `_run_with_capture()` wrapper that intercepts stdout and stderr via `io.StringIO` buffers. Output is displayed within the scrollable curses pager rather than dropping the user back to raw terminal output. This matches the immersive TUI pattern established in Lattice v4.1.2.

**Styled Curses Pause.** The post-operation "Press Enter to continue" prompt now renders inside a styled Unicode box within the curses session (`_tui_pause`), instead of falling through to a raw `input()` call. Accepts Enter, q, or Esc to dismiss.

**Null Byte Sanitization.** The scrollable pager now strips null bytes from captured output before rendering, preventing `ValueError: embedded null character` crashes on corrupted data.

---

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

**`prog="cquarry.py"` added to `ArgumentParser`.** Version and help output now
show the script name consistently regardless of invocation path.
