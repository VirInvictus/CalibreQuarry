# CalibreQuarry — Patch Notes

## v3.0.3 (2026-06-20)

### New Features

**`scripts/audit_epub_emptytext.py`: find empty / no-body-text EPUBs.** Catches the failure mode every other audit misses: a content-less stub that still validates. The canonical case is the "Bookmate" export, where the archive holds only cover and promo images plus a tiny HTML placeholder, the OPF spine points only at that placeholder, and the book itself is absent. Such a file passes `epubcheck`, "repairs" clean in a structural repairer (its one referenced document is well-formed), and shows no foreign text to `audit_epub_content.py` because there is no text at all; the metadata looks perfect. The detector resolves the spine, drops `<script>`/`<style>`, strips tags, decodes entities, and counts the rendered characters: EMPTY (`<=2000`, `--min-chars`) is a real defect to re-source, THIN (`<20000`, `--thin-chars`) is advisory because a genuine short story or a publisher sample can also land there. A Bookmate origin is not itself a defect; most Bookmate exports carry their full text, so the flag is on empty content, not provenance. Library mode (DB-driven, `mode=ro`) and directory mode (vet downloads before import), mirroring the other two EPUB audits. First full-library run flagged four real defects (a Bujold stub, two image-only scans with no text layer, and a Draft2Digital sample of a full novel hiding in the THIN tier) against three genuinely short stories left alone.

### Fixes

**`spot_check.py` no longer flags OCaml and NCurses as case garble.** The intercaps allowlist (`_CASE_OK`) now includes `OCaml` and `NCurses` alongside `SQLite`, `QBasic`, and the rest, so legitimate library titles stop tripping the advisory case-garble heuristic.

## v3.0.2 (2026-06-14)

### New Features

**`scripts/audit_epub_pagenumbers.py`: find print page numbers baked into EPUB body text.** Bad PDF/OCR-to-EPUB conversions capture the print page number (and often the running header) as a literal paragraph in the flow instead of real EPUB pagination, so it reflows into the middle of a sentence ("where the hay cart 16 was taking him"). The detector reads each book's blocks in spine order and flags a number only when it genuinely interrupts prose: a lowercase continuation after it, a word split across it (the previous block ends in a hyphen), or it abuts a repeated running header/footer. Section and chapter numbers (which open the next block with a capital) and endnote/footnote numbers and chronology years are left alone. Library mode (DB-driven, `mode=ro`) and directory mode (vet downloads before import), mirroring `audit_epub_content.py`. Validated by hand against the full reference library: 21 flagged, every one a true positive; the false-positive tail (an experimental footnote-poem, a scraped web-serial's vote counts, placeholder section labels) all fell under the hit-count or book-span floors. It also surfaces piracy watermarks and bad OCR scans that ride along with the page-number cruft.

## v3.0.1 (2026-06-12)

### New Features

**`scripts/spot_check.py`: randomized metadata + file-integrity audit.** Samples N random books (reproducible with `--seed`) and checks what pattern-based sweeps miss: title corruption and mojibake, junk author entries, missing or stub descriptions, EPUB archive integrity (CRC, container/OPF sanity, spine completeness, text volume), PDF header/page count, and DJVU page count. Emits a machine-readable flag report plus a review bundle (title/author/tag/series/blurb per sampled book) for a human or LLM judgment pass. Read-only against `metadata.db`; validator-owned checks are not duplicated. First 600-book run against the live library caught a wrong-book description, a truncated description, three mojibake descriptions, and an EPUB with ten dangling spine references.

### Fixes

**`reconcile_file_metadata.py` PDF embeds no longer fail silently.** Two related defects: exiftool refuses to rewrite XMP packets containing duplicate properties (seen in the wild: a doubled `prism:doi`) unless `-m` is passed, and it exits 0 with "files unchanged", which the tool read as success; The embed now passes `-m`. (An interim attempt to also write a bare `-Publisher` tag was reverted: on PDFs exiftool maps it to the same XMP dc:publisher bag, so double assignment appended duplicate entries and broke the round-trip.)

**`write_catalog` no longer corrupts the shared book cache.** It sorted the list returned by `get_all_books()` in place, silently reordering the session cache for every later consumer. It now sorts a copy; a regression test pins the behavior (`tests/test_modes.py`).

**`compress_pdf.py` cannot clobber a rollback original.** If a `.pre-compress` rollback file already exists, an in-place run now aborts instead of overwriting the only copy of the true original with an already-compressed file.

**`audit_epub_content.py` finds the library from either home.** The library root resolves to wherever `metadata.db` sits: next to the script (the copy living inside the library) or the current working directory (running the repo copy from a library), in that order.

**`reconcile --id` rejects malformed id lists cleanly** via a dedicated parser instead of an unhandled `ValueError`.

### Internals

Exception chaining (`raise ... from`) throughout `search.py` and `helpers.py`; unused loop variable removed in wing-overlap analytics; `re.Scanner` access satisfied for type checkers; new test coverage for the backup guard, library-root resolution, and cache isolation (suite: 87 tests).

## v3.0.0 (2026-05-26)

---

### Breaking

**Python 3.14+.** The supported floor moved from 3.9 to 3.14 to match the development environment. The code does not depend on bleeding-edge syntax, but only 3.14+ is tested and supported.

### New Features

**Comprehensive search engine.** The search expression parser was rebuilt as a dedicated, stdlib-only engine (`src/cquarry/search.py`) that ports Calibre's grammar and matching semantics. It now resolves field locations beyond tags and authors: `series`, `publisher`, `rating`, `formats`, `languages`, `pubdate`/`date`/`last_modified`, `identifiers`/`isbn`, `comments`, `cover`, `id`, `uuid`, and `#custom` columns, in addition to `tags`, `authors`, `all`, and `vl:`. It supports contains/`=`exact/`~`regex/`^`accent match kinds, numeric and date relational operators (`rating:>=4`, `pubdate:>2015`, `date:30daysago`), and `field:true`/`field:false` presence tests. Boolean grouping, implicit AND, quotes, and escapes follow Calibre's grammar, evaluated with its candidate-set semantics. The previous build only handled `tags:`, `author(s):`, and `vl:`; other prefixes silently matched nothing.

**`--search` prints to stdout.** With no `--output`, results stream to the terminal instead of forcing a file. `--format json|csv|ai` emits the matching books in that structured shape; otherwise a plain-text listing is produced. An empty query (`--search ''`) returns the whole library, matching Calibre.

**Deeper cover audit.** Cover dimension reading no longer stops at the first 1 KB, so a JPEG whose SOF marker sits behind a large EXIF/ICC block is measured correctly; PNG covers are now read too.

### Fixes

**Interactive TUI analytics no longer crashes.** Selecting any item under the ANALYTICS menu (Author statistics, Reading pace, Tag tree, Wing overlap) raised a `NameError` because those functions were never imported into `tui.py`. They now work.

**Half-star ratings are visibly distinct.** A 2.5 rating rendered identically to 2.0 (both `★★☆☆☆`); it now shows `★★½☆☆` using the universally available `½` glyph.

**Series "complete" is computed correctly.** Completeness now means "no missing integer volumes" rather than "book count equals the top index", so a series with novellas (0.5) or duplicate editions is no longer wrongly marked incomplete.

**Portability of the series rollup.** `get_all_series` was rewritten to aggregate in Python instead of using `GROUP_CONCAT(... ORDER BY ...)`, which required SQLite 3.44+.

**Normalized custom columns now load.** Single-valued text and enumeration custom columns (e.g. a "Status" / reading-state column) are stored by Calibre in a value table plus a `books_custom_column_N_link` table, exactly like multi-valued columns. The loader keyed off `is_multiple` and tried to read a `book` column straight from the value table, which errored and silently returned nothing. It now detects the link table, so `--show-custom` and `#column` searches work for every custom-column type.

### Documentation & Tests

**Honest parity claims.** The README and spec no longer claim "100% parity"; they document exactly which locations and operators are supported and the deliberate, dependency-bound deviations (stdlib `re` instead of the `regex` module, `unicodedata` folding instead of ICU, no GPM templates or saved-search references, and cquarry's anchored hierarchical `tags:` match).

**Companion scripts.** `scripts/compress_pdf.py` (Ghostscript-based PDF shrinking with verify-or-rollback; writes files and `metadata.db`) and `scripts/audit_epub_content.py` (read-only EPUB content auditor) are now versioned alongside the toolkit and fully documented, explicitly outside the read-only `cquarry` package contract.

**Portable test suite.** `tests/test_search.py` and `tests/test_helpers.py` cover the parser grammar (adapted from Calibre's own tests), the matcher against an in-memory provider, a full-stack integration test on a temporary SQLite fixture, and the rating/series/image helpers, all without needing a live Calibre library.

## v2.6.0 (2026-05-03)

---

### New Features

**Tag Dump (`--tags`).** A flat, alphabetized list of every tag in the library with its book count, written to stdout. Drop-in replacement for the noisy `calibredb list_categories -r tags` shell pipeline — pipe it to a file with `cquarry --tags > tags.txt`. Also reachable as "Tag dump" under LISTS in the interactive TUI. Honors `--quiet` (suppresses header/footer, leaves the body intact for scripting). Distinct from `--analytics tags`, which renders the hierarchical tree.

---

## v2.5.0 (2026-04-21)

---

### New Features & Fixes

**Full Parity Search Engine.** Refactored the `--search` expression parser to achieve 100% parity with Calibre's native syntax.
- **Author Searching:** Added full support for `author:` and `authors:` prefix tokens.
- **Fallback Text Search:** Un-prefixed terms (e.g. `cquarry --search "author:Anne Rice"`) now correctly fall back to searching anywhere across book titles, authors, and tags. This accurately mimics Calibre's implicit boolean `AND` handling of unquoted spaces.
- **Complex Grouping:** Verified and documented support for nested boolean logic, parenthetical grouping, and negative lookaheads (e.g., `NOT(tags:Fic.Romance OR tags:Fic.Contemporary)` or `tags:"Fic.Fantasy.Grimdark" AND author:"Phil Tucker"`).
- **Test Suite.** Added an automated test suite (`tests/test_search.py`) mapped against Calibre's actual `SearchQueryParser` behavior to guarantee ongoing expression fidelity.

---

## v2.4.1 (2026-04-16)

---

### Completed Software & Bug Fixes

**Completed Software Status.** Phase 4 has been concluded, and CalibreQuarry is now considered feature-complete and stable. It has undergone rigorous end-to-end testing against real-world Calibre databases.

**Bug Fixes:**
- Fixed a `NameError` crash in `--audit` mode where the newly introduced `color` helper was not imported, preventing the final summary from printing when issues were found.

---

## v2.4.0 (2026-04-16)

---

### New Features

**Custom Column Support.** Added a `--show-custom "Column Name"` flag that extracts data from user-defined custom Calibre columns. The values are automatically appended to text catalogs and are natively included in JSON, CSV, and AI exports.
**Color CLI Output.** Introduced simple, lightweight ANSI color formatting for headers, warnings, and error highlights across CLI modes to improve readability when bypassing the interactive pager.

---

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
