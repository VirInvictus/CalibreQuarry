# 3.22.0 (2026-08-28)
- **Upgrade**: The TUI now delegates to vir-tui 2.2.0's Phase-3 primitives — `interactive_session()` owns the curses session lifecycle (open, degrade-to-text, close, KeyboardInterrupt → exit 130), `prompt_float()` replaces the hand-rolled rating loop (blank now means 0/clear rather than cancel), `prompt_path()` powers the first-run and change-database flows (existence loop with a notice built in; the change-database cancel path keeps the "database unchanged" behavior), `confirm(danger=True)` gates the Remove Book flow, and report footers use the public `out_note()`.
- **Chore**: Dropped the private `_USE_CURSES`/`_SCREEN` module globals in favor of vir-tui's public `text_mode()`.
- **Dependency**: `vir-tui` tracks `@main`; requires 2.2.0.

# 3.21.0 (2026-08-27)
- **Feature**: `--book BOOK_ID` — full single-book dossier composing cquarry's read APIs: identifiers, per-format files with catalogued sizes and on-disk paths (missing files flagged), cover resolution, comments rendered through `strip_html`, custom columns (via the search-engine `field()` hook), e-reader annotations, per-device reading positions, plugin data, and conversion overrides. Unknown ids exit 1 cleanly.
- **Feature**: `--entities {authors,series,publishers,tags,languages,ratings}` — cquarry's `get_entities()` listing with per-entity book counts and the sort/link secondary columns for authors/series/publishers (ratings render as stars).
- **Feature**: `--reading-progress` — every `last_read_positions` row across devices with progress-fraction bars, newest first.
- **Feature**: `--columns` — the custom-column schema (label, datatype, editability, normalized flag, enum values, composite templates) via `get_custom_columns()`.
- **Feature**: `--info` — library dossier: identity UUID, virtual libraries with their defining expressions, saved searches, `@Name` user categories, grouped search terms, news feeds, conversion overrides, `metadata_dirtied`/`annotations_dirtied` queue depth, and tag-browser layout state.
- **Feature**: Write-verb expansion completing cquarry ≥1.5's write module — `--add-tag` / `--remove-tag` (repeatable; orphaned entity rows pruned), `--set-identifier` / `--clear-identifier` (EAV upsert, empty VALUE deletes), `--set-series` + `--series-index` / `--clear-series` (clear nulls `books.series_index` and prunes), `--set-publisher` / `--clear-publisher`, `--set-languages` / `--clear-languages` (canonicalized through Calibre's language map), `--add-format` / `--remove-format` (metadata-only `data` rows), and `--set-cover` (has-cover flag). All funnel through the shared dispatcher: argument problems exit 2 before the DB opens, lock/validation errors exit 1.
- **Upgrade**: All write plumbing (previously inline in `cli.py`) moved to `cquarry_cli/writeops.py` so the TUI reuses the exact same executors; the TUI gains Display/Info entries, Annotations + LibraryThing exports, an *Edit Book* submenu covering every write verb, and a dry-run-first *Remove Book* flow.
- **Upgrade**: Dependency policy — cquarry (and vir-tui) now tracked explicitly at `@main`; the committed `uv.lock` (which pinned cquarry 1.0.0 at an old commit) is removed and gitignored so every `pip`/`uv`/CI install pulls the latest cquarry.
- **Upgrade**: Requires the latest cquarry (1.6.x).

# 3.20.0 (2026-08-26)
- **Upgrade**: `--search` inherits cquarry 1.6's `@Name:query` user-category location — Calibre's
  `get_user_category_matches` parity (exact member matching per member location, `@Name:.query`
  for subcategories, `false` inversion, upstream's `@...:` lexer word rule so spaced category
  names work). No flags needed; unknown `@Names` match nothing instead of degrading to an
  `all:` text sweep.
- **Upgrade**: Inherited v1.6 read-side completeness — `get_book()` rows are now
  shape-identical to `get_all_books()` rows (both carry `uuid`, `identifiers`, `size`),
  `languages` follow `books_languages_link.item_order` like Calibre, `get_entities()` gained a
  `ratings` kind, and `get_feeds()` / `get_annotations_dirtied_books()` /
  `get_tag_browser_counts()` (Calibre's own `tag_browser_*` sidebar rollups with `avg_rating`)
  are available for future verbs.
- **Upgrade**: Requires `cquarry>=1.6`.

# 3.19.0 (2026-08-26)
- **Feature**: Write-verb surface completed on cquarry ≥1.5's expanded module — `--set-authors ID "A; B"` (semicolon-separated; author_sort recomputed), `--set-rating ID STARS` (0–5), `--set-comments` / `--clear-comments`, `--set-column ID #LABEL VALUE` / `--clear-column` (layout auto-detected, enumerations validated against configured values, non-editable columns refused), and `--remove-book ID [--confirm-remove]` (dry-run by default printing title+formats; irreversible with the flag). All verbs queue OPF regeneration via `metadata_dirtied`.
- **Feature**: `--format-stats` prints per-format book counts and total bytes (`get_format_stats()`).
- **Upgrade**: All write verbs funnel through a shared `_run_write` dispatcher with uniform lock/validation error handling (exit 1) and argument validation (exit 2).
- **Upgrade**: Requires `cquarry>=1.5`.

# 3.18.0 (2026-08-26)
- **Feature**: `--show-author-details` — opt-in enrichment for `--catalog`, `--all-wings`, `--export`, and structured `--search` output. Catalog lines gain a `{sort; link}` segment and JSON/CSV exports gain `author_sorts`/`author_links` fields, sourced from cquarry ≥1.4's entity secondary columns (each author's true sort key and author-page URL).
- **Upgrade**: `--search` now resolves custom grouped-search terms (`GroupName:query`, from Calibre's `grouped_search_terms` preference, with upstream union/false-inversion semantics) and the new `annotations:` location (full-text over e-reader highlights) — both inherited automatically via cquarry 1.4's engine; no flags needed.
- **Upgrade**: Requires `cquarry>=1.4`.

# 3.17.0 (2026-08-26)
- **Feature**: Page counts flow through exports — `--export` JSON gains a `pages` key per book, CSV a `pages` column, and the AI format a `<N>p` segment — sourced from Calibre's native `books_pages_link` table via cquarry ≥1.3.
- **Feature**: Library provenance stamping — text catalogs (`--catalog`, `--all-wings`) carry the library's identity UUID in their header line, and `--audit`'s summary names it, so any output can be traced back to its source library after moves/restores (cquarry `get_library_uuid()`).
- **Upgrade**: The audit's cover checks now resolve through cquarry's `get_cover_path()` (canonical `cover.jpg`/`cover.png` layout logic) instead of hand-built paths.
- **Upgrade**: Requires `cquarry>=1.3`.

# 3.16.0 (2026-08-26)
- **Feature**: First write flow — `--set-title BOOK_ID TITLE` renames a book through cquarry's separate `WritableCalibreDB` module (trigger-safe: registers Calibre's `title_sort`/`uuid4` SQL functions, refreshes the sort key, bumps `last_modified`). Every mutation also records the book id in Calibre's `metadata_dirtied` queue (requires cquarry 1.2), which is what upstream consumes to regenerate the book's sidecar `.opf` and re-push metadata to wireless readers on its next startup — external edits finally propagate. Requires closing Calibre first; lock contention fails cleanly with exit code 1.
- **Feature**: `--audit` output now includes a "Pending OPF sync" section listing the books queued in `metadata_dirtied` (via cquarry's read-only `get_dirtied_books()`), so you can see exactly what Calibre will resync next time it starts. Absent when the queue is empty.
- **Upgrade**: Requires `cquarry>=1.2`.

# 3.15.0 (2026-08-25)
- **Feature**: New `--export-annotations` command dumps e-reader highlights, bookmarks, and notes as JSON (via cquarry's `annotations` reader), optionally scoped to one book with `--id <BOOK_ID>`.
- **Feature**: New `--plugin-data NAME` flag (works with `--catalog`, `--all-wings`, and `--search`) appends third-party plugin values — e.g. `goodreads_id` or `wordcount` from `books_plugin_data` — as `<name: value>` segments on each book line.
- **Upgrade**: Migrated every mode off the retired string-typed `get_all_books()` fields. `authors`, `tags`, `formats`, and `languages` are consumed as the native `list[str]` arrays cquarry 1.1+ exposes; author names containing literal commas no longer risk splitting.
- **Upgrade**: Requires `cquarry>=1.1`. Search gains saved-search interpolation (`--search 'search:"Name"'`), multi-valued count operators (`tags:#>2`), language canonicalization (`languages:English`), slash date separators, tristate boolean keywords (`checked`/`blank`/...), strict errors on unknown virtual libraries, and new `size:`/`pages:` locations — all available through the existing `--search` flag with no CLI changes.
- **Note**: `scripts/reconcile_file_metadata.py` already fetches per-book records without a full-library scan, so the roadmap's single-record fast path was satisfied by design; no change was needed.
- **Feature**: New `scripts/audit_conversion_overrides.py` lists books carrying manual conversion recipes — cquarry’s extractor, which never unpickles the blobs.

# 3.14.1 (2026-08-24)
- **Fix**: Replaced hardcoded custom columns logic in `librarything.py` with dynamic ID resolution from the database schema.
- **Fix**: Restored functionality in `test_queries.sh` by targeting `cquarry_cli` instead of the extracted `cquarry` module.
- **Fix**: Migrated `spot_check.py` and `audit_isbns.py` to use `connect_ro()` with WAL/SHM fallback for safe read-only locking against active Calibre DBs.
- **Fix**: Corrected NULL title bug in `spot_check.py` by coalescing absent titles.
- **Fix**: Fixed logical gap check in `display.py` series output.
- **Fix**: Reordered argument validation in `export.py` to prevent 0-byte file truncations on invalid format flags.
- **Fix**: Removed `.title()` enforcement during duplication checks in `audit.py` to preserve original casing.

# 3.14.0 (2026-08-24)
- **Refactor**: Adapted to `vir-tui` v2.0.0 public API and decoupled menu fallbacks.
- **Fix**: The non-curses fallback text menu now functions properly for CalibreQuarry by passing custom `letter_keys` and `aliases` during initialization.

# CalibreQuarry — Patch Notes

## v3.13.0 (2026-08-23)

### Changed
- **TUI Extraction (`vir-tui`):** Extracted the generic CLI formatting (`core.py`) and curses menu primitives (`tui.py`) into the `vir-tui` shared repository. CalibreQuarry now depends on `vir-tui` for all UI logic, ensuring perfect parity and centralized updates for all interactive prompts across the workspace.

## v3.12.0 (2026-08-23)

### Changed
- **Shared Library Extraction (`cquarry`):** Extracted the core Calibre database reading layer and search expression grammar into a new, standalone Python library (`cquarry`). CalibreQuarry now depends on this shared library for all data access and search resolution, ensuring 100% parity across all tools in the workspace (like Hermitage and Wings).
- **Project Renamed to `calibrequarry`:** To prevent pip namespace collisions with the newly extracted `cquarry` shared library, the CLI project has been formally renamed to `calibrequarry` in `pyproject.toml`, and its internal modules have been moved to `cquarry_cli`. The terminal command remains `cquarry`.


**UI Upgrade:** CLI scripts now feature rich output (ANSI formatting, `tqdm` progress bars, and a clear summary block). The project is no longer strictly stdlib-only and now depends on `tqdm`.

## v3.10.1 (2026-08-14)

### Fixes

**CI Configuration & Code Closures.** The GitHub Actions pipeline (`ruff check`) failed because of several B023 late-binding closures inside `tui.py` which were unnoticed by the global configuration. Fixed those closures and added a test (`test_version.py`) to prevent version drift between `pyproject.toml`, `config.py`, and `VERSION` files.

## v3.10.0 (2026-08-11)

### Features

**LibraryThing Export Integration.** Ported the standalone `export_librarything.py` script natively into `cquarry`. You can now use the `--exportlt` flag to generate LT-formatted CSVs directly. Crucially, this can be combined with `--search` to export only specific subsets of your library (e.g., `--search "date:>2026-08-05" --exportlt`), making targeted updates significantly easier. The export fully handles LibraryThing data quirks, such as folding ISBN-10 to ISBN-13, expanding translators into individual tags, clearing sentinel dates, and breaking output into manageable 500-book chunks split by "Read" vs "Unread" statuses.
## v3.9.2 (2026-08-09)

A bug, maintenance and improvement sweep across the package and all seven companion scripts. Eight fixes, three additions, four cleanups, every one pinned by a regression test. The suite grows from 243 to 273 tests.

### Fixes

**Every database opener built its `file:` URI by raw interpolation.** `?` and `#` are URI syntax, so a library at `Books #2/metadata.db` resolved to a different path entirely and failed with the thoroughly unhelpful "no such table: books". This affected `db.py` and six of the seven scripts; `fetch_library_codes.py` already percent-encoded its path, which is the form the rest have adopted via a shared `db_uri_ro` helper. The package's helper is in `helpers.py`; the standalone scripts each carry their own copy, as they carry everything else.

**`--tag` scoping in `audit_isbns.py` and `fetch_library_codes.py` matched one arbitrary tag per book.** Both pulled a single tag through a `LIMIT 1` subquery with no `ORDER BY`, so a book carrying two tags was filtered on whichever one SQLite happened to return: a book tagged both `Fic.Fantasy` and `NonFic.Tech` was invisible to `--tag NonFic.Tech` roughly half the time, and `fetch_library_codes.py`'s per-branch hit-rate report was attributing books to a branch at random. Scoping now considers every tag, and a book is reported under a tag the filter actually selected on. **This is latent on the reference library**, where all 7,439 books carry exactly one tag; it was found by reading the query, reproduced with a two-tag fixture, and fixed as insurance rather than to change any result observed so far.

**`compress_pdf.py --out-dir` pointed at the PDF's own directory destroyed the original.** The output lands at `out_dir/<same name>`, which in that case *is* the source path, so the mode documented as leaving the original untouched moved the compressed temp over it, left no `.pre-compress.pdf` rollback, and then printed "Original untouched at:" followed by the path of the file it had just replaced. It is now refused (exit 2), with the comparison resolving both sides so a symlinked directory cannot slip past.

**`write_all_wings` silently overwrote colliding wing catalogs.** Sanitizing a wing name to a filename is lossy: "Tabletop: RPG" and "Tabletop RPG" both reduce to `Tabletop_RPG`, and a punctuation-only name reduces to nothing, yielding `_Library.txt`. One wing's catalog replaced another's with no warning, leaving a file that claimed to be a wing it was not. Names are now de-duplicated with a numeric suffix, and a name that sanitizes away gets a positional fallback.

**`write_catalog` could not write into a directory that did not exist yet.** `run_audit` and `run_export` both create the parent first; `write_catalog` opened directly, so `--catalog --output reports/catalog.txt` exited 1 on a `FileNotFoundError`. It now matches its siblings.

**`spot_check.py --review` crashed on an empty sample.** With no books selected (`--n 0`, or a `--limit` that selects nothing) no chunk files were written, and the closing instructions indexed `written[0]`. It now reports an empty sample.

**`audit_drm.py` and `audit_epub.py` had no locked-database fallback.** The package, `validate_metadata.py` and `reconcile_file_metadata.py` all read from a temporary snapshot when Calibre holds the lock; these two let the `OperationalError` escape as a traceback, so a library-mode run while Calibre was open simply crashed. Both now degrade the same way, pinned by tests that take a real `BEGIN EXCLUSIVE` lock rather than faking the error.

**Two writers used the locale's encoding instead of UTF-8.** `spot_check.py`'s report and bundle, and `audit_drm.py`'s CSV, were the only file writes in the repository without an explicit `encoding=`; a title outside the locale's character set raises `UnicodeEncodeError` partway through a long run. Reachable only under a genuinely non-UTF-8 locale (`en_US.ISO-8859-1` and the like) rather than the far commoner `LANG=C`, which auto-enables Python's UTF-8 mode: this is consistency with the rest of the repository more than a crash anyone was hitting.

### Additions

**`--audit` reports `cover_file_missing`.** A book whose database row says `has_cover` but whose cover file is gone from disk was silently clean: the audit flagged a missing cover record and a low-resolution cover, but not the case where the two sources of truth disagree. Every cover consumer hits that, Calibre's own grid included.

**The curses prompt accepts non-ASCII input.** It read one byte at a time and discarded anything outside 32-126, so `Brontë` could not be typed into a search query or an output path. It now reads whole characters (`get_wch`), which for a library full of translated fiction is the difference between the TUI being usable for search and not.

**Every external tool call is bounded by a timeout.** `spot_check.py` (exiftool, djvused), `reconcile_file_metadata.py` (calibredb, exiftool, qpdf, djvused, pgrep), `fetch_library_codes.py` (pgrep) and `compress_pdf.py`'s inspection probes previously had none, so a single wedged tool on a single damaged file hung a whole-library run with no indication of which book it stopped on. Each failure path is handled rather than merely raised: a hung reader flags that book and the run continues. **Ghostscript itself is deliberately left unbounded** in `compress_pdf.py`, because a legitimate 1 GB sourcebook takes many minutes and killing that mid-convert would be the wrong answer.

### Maintenance

`audit_epub.py` extracted each book's rendered text twice under `all`: `emptytext` built it per spine document and `ocr` rebuilt the same string, which is the expensive half of a pass whose entire purpose is touching each EPUB once. It is now computed once per book and shared. Two stale documentation references to `validate_library.py`, a script that is not in this repository, now point at `validate_metadata.py`; `audit_epub.py`'s usage line said "all three audits" when there have been four since v3.6.0. `audit_drm.py` imported `sqlite3` inside a function while importing everything else at module scope, and `stats.py` carried a conditional whose two branches computed the same value.

## v3.9.1 (2026-08-08)

`audit_isbns.py` counted any labelled ISBN as the book's own. Books quote other books' ISBNs constantly, and one citation is indistinguishable from a self-identification if you only count numbers, so a handful of famous false accusations followed: *The Atrocity Archives* names *The New Hacker's Dictionary*'s ISBN in a glossary entry, *Metamagical Themas* lists one among Hofstadter's self-referential joke titles, and *C++ Primer Plus* advertises six other Sams books in its back matter.

The fix is to require corroboration rather than to enumerate the ways a citation can look. A copyright page never carries a bare number: it sits beside a copyright line, a rights reservation, a binding, a printing statement, or a CIP block. A citation carries none of that, so an ISBN now counts as the book's own only when such a marker appears within 260 characters.

**Applying that symmetrically was itself a bug, caught by measuring before committing.** The first version demanded self-identification in both directions and cost **639 confirmations** across a real library while removing only 25 false findings. The two directions need different evidence: a book printing the *same* number the catalogue holds is conclusive whatever the surrounding prose says, because a citation coinciding with your own stored value does not happen. Only a *different* number needs to have been claimed. Restricting the test to the negative direction gives 46 fewer false findings with confirmations slightly *up* (2,974 to 2,979).

Both directions are now pinned by tests carrying the real passages. 243 tests.

## v3.9.0 (2026-08-08)

A new companion script, `audit_isbns.py`, and the first new capability since the v3.8 sweep. It answers a question nothing else in the Calibre ecosystem asks: does the ISBN stored against a book actually identify that book? Calibre downloads metadata but never re-examines what it stored, so a wrong ISBN stays invisible, and an ISBN is what other systems key on when you hand them a catalogue.

The motivating evidence: a four-source sweep of a 6,786-ISBN library that was already validator-clean found 51 identifiers pointing at a different book. The dominant shape is a **same-publisher sibling**, which is why the defect survives every existing check: the number is well-formed, the checksum passes, and only the book it names is wrong. *Programming Clojure* carried *tmux 2*'s ISBN, *Spelunky* carried *Super Mario Bros. 3*'s, and *A Book on C* carried `9782147483649`, the 2147483649 integer-overflow constant dressed as an ISBN.

This release ships the offline half: verification against the ISBN each book prints on its own copyright page. That is the best authority available for exactly the books no bibliographic database has heard of (small-press RPGs, indie ebooks, print-on-demand reprints), and it needs no network, no credentials, and no new dependencies.

**It reads body text only, never embedded metadata.** `reconcile_file_metadata.py` writes the database's values into those metadata blocks, so comparing against them would be comparing the database with itself and would cheerfully confirm every error the tool exists to find. A test pins this: an OPF carrying a matching identifier must not produce a confirmation.

Not crying wolf is most of the work. Three benign things resemble a mismatch and are classified apart. A **bibliography** prints other books' ISBNs (*The Art of UNIX Programming* prints 49), so above `--max-printed` distinct numbers a file is read as a citing work. A **bundle or series volume** legitimately prints several ISBNs, reported `AMBIGUOUS` for a human to resolve rather than guessed at. A **format variant** (print versus ebook) differs only in its final digits, so a printed number sharing the stored one's registrant prefix is reported `VARIANT` rather than `MISMATCH`; a genuinely wrong ISBN almost always comes from a different publisher block entirely.

There is deliberately **no `--apply`**, and there will not be one. Single-source verdicts proved wrong often enough during the motivating sweep that an auto-fixer would have "corrected" *Curse of Strahd*, *Cold Mountain*, *Kitchen* and *The Master and Margarita*, every one of which was already right.

Scoping reuses `fetch_library_codes.py`'s anchored-hierarchical `--tag` rule rather than inventing a virtual-library flag: a comma-separated prefix list covers a multi-root wing without coupling a standalone script to the package's VL resolver. Text extraction is stdlib `zipfile` for EPUB and optional `pdftotext`/`djvutxt` for PDF and DJVU, whose absence is reported rather than fatal. MOBI/AZW3 are skipped.

Three things were corrected before merge, all found by running it against a whole 6,783-ISBN library rather than a single wing.

**The severe verdict was renamed `SUSPECT` to `MISMATCH`, because the old name overclaimed.** On a real library the commonest reason a book prints a different ISBN than the catalogue holds is that the *file is a different edition*, which is worth knowing but is not a wrong book. An ISBN alone cannot separate the two, and a report that says "suspect" 100 times about mostly-benign edition drift trains its reader to ignore it.

**The publisher-prefix comparison was too long.** Registrant lengths run 2 to 7 digits and are *inversely* proportional to publisher size, so a nine-digit prefix (right for a one-book press) split HarperCollins from itself: *Sabriel*'s stored `978-0-06-447183-1` and printed `978-0-06-000548-1` are one publisher and were being reported as rivals. Six digits reclassified 28 findings from the severe bucket to `VARIANT`.

**`UNREADABLE` conflated two opposite things.** Half of those books were MOBI/AZW3, which this tool skips by design; the rest were supported formats that yielded nothing, which may be damaged files. They are now `SKIPPED` and `UNREADABLE` respectively.

Also documented: **the printed ISBN can itself be wrong**, which is the limit of the whole premise. The TSR *Forgotten Realms Campaign Setting* prints a number belonging to *The Jungles of Chult* (a permanent typo), and *Night Witches* prints *Durance*'s because a small press reused its previous copyright page. Both surfaced as `VARIANT` and both resolved in favour of the database, which is exactly why nothing is ever written automatically.

Runs: 121 books in 15s on one wing and 468 in 63s on another, both with zero severe findings; 6,783 in about 15 minutes across the library. The suite grows from 208 to 237 tests, with the real-world classifications pinned so a future refactor that silently reclassifies them fails loudly.

## v3.8.1 (2026-08-07)

A full-repository bug, maintenance, and documentation sweep: the package, all seven companion scripts, the tests, and every doc. The package core came out clean (one micro-refactor: `_num_predicate` in `search.py` returned a two-tuple whose second element nothing read; it now returns just the predicate). The scripts yielded nine real fixes, every one now pinned by a regression test. The suite grows from 195 to 208 tests, and `fetch_library_codes.py` and `validate_metadata.py` gain their first tests.

### Fixes

**`audit_drm.py` reported an unparseable PDF as CLEAN.** `qpdf --is-encrypted` exits 0 for encrypted, 2 for not encrypted, and 3 for a file it cannot parse; the code collapsed 2 and 3 into "clean". A corrupted or truncated PDF (exactly the kind of loose file the tool exists to vet) therefore skipped the trailer `/Encrypt` fallback that was built for the couldn't-classify case, and a Standard-encrypted broken file read as definitively DRM-free. Exit 3 now defers to the fallback and reports `BENIGN encrypted-unclassified` when an `/Encrypt` dictionary is present.

**`audit_epub.py content` contradicted itself on an injected signature in a declared-foreign book.** The expected-foreign flag (declared language / `NonFic.Language.*` tags) was applied to injection-signature hits too, so a piracy notice in a legitimately-French book printed under a green "(expected-foreign)" label, reported "0 file(s) need review", and still exited 1. A signature is a defect regardless of language; it is now always counted and listed as needing review.

**`compress_pdf.py` skipped verification when it mattered most.** `page_count()` returns None both when pdfinfo is missing and when pdfinfo cannot parse the file, and the page-count comparison only ran when both counts were known. A Ghostscript output so broken pdfinfo could not read it (the strongest possible bad-conversion signal) therefore passed "verification" and replaced the original. When the original's page count is known and the output's is not, the run now aborts with the original untouched.

**`reconcile_file_metadata.py` split author names on commas.** The file-side author string was split on `&`, `;`, and `,`, but `ebook-meta` only ever joins authors with `&`; a comma belongs to the name itself. "Martin Luther King, Jr." parsed as two bogus authors, never matched the database, and the book reported as drifted forever, surviving every re-embed. Same shape as the v3.8.0 identifier-space bug, one field over. The split now uses `&` and `;` only.

**`spot_check.py --record` silently truncated comma-delimited notes.** A verdict line's note field was `parts[4]` of an unbounded split, so a comma-mode note of "wrong author, should be Jane Doe" recorded as "wrong author" with the rest dropped and no error. The split now caps at five fields, keeping free-text notes intact.

**`spot_check.py --record` no longer runs without `--against`.** The id reconciliation is documented as the load-bearing part of review mode ("nothing is written unless the ids reconcile"), but `--against` was optional, and omitting it skipped the check entirely, accepting exactly the short-but-plausible verdict lists it exists to refuse. Recording without an ids file is now a setup error (exit 2).

**`fetch_library_codes.py` clobbered its own backup.** The pre-`--apply` backup was stamped with the date only, so a second run the same day overwrote the first run's restore point, the copy that actually holds the pre-change database. The stamp now carries seconds plus a collision counter; no backup is ever overwritten. Also fixed: a missing `metadata.db` exited 1 via `sys.exit(str)` while the documented contract (and every other setup-error path) says 2.

**`validate_metadata.py` inflated FORMAT_FICTION_PDF counts.** The check joined through the tags table before filtering, producing one warning per (book, tag) pair; a crossover book tagged `Fic.Fantasy` and `Fic.Horror` was reported twice. Now one warning per book, listing every matching tag.

**`audit_epub.py`'s latin-1 decode fallback was dead code.** `bytes.decode("utf-8", "replace")` can never raise, so the documented fallback was unreachable and the except path re-read the same corrupt entry just to fail again. A corrupted spine entry now reads as empty text through one honest path, and the docstring says so.

### Maintenance

- `test_queries.sh` exercised `vl:"The Tabletop"`, a wing that no longer exists (split into the two "Tabletop:" wings in the live library), so the VL-resolution smoke was silently testing the unknown-VL path. It now targets `Fantasy Wing`.
- All seven scripts are executable now (five carried a shebang without the bit; the documented `python3 scripts/...` invocation is unchanged).
- Docs drift closed across the board: `spec.md` §5 gains the missing `fetch_library_codes.py` row and the `--review` half of `spot_check.py` (and now says three scripts write, not two); `roadmap.md` gains Phase 7 recording the v3.7.0–v3.8.0 companion work; the README's test-suite section describes all seven test files instead of the original two; `CLAUDE.md`'s architecture tree adds `audit_drm.py`, `spot_check.py`, `modes/tags.py`, and the five test files it didn't list, and renames the long-gone `audit_epub_content.py` to `audit_epub.py`.

## v3.8.0 (2026-08-02)

**New companion script `fetch_library_codes.py`: derive Library of Congress Classification codes from the LoC SRU catalogue and store them as identifiers.** Written after the existing Calibre plugin for this job, "Library Codes - SRU", was diagnosed as unable to do it at all.

The plugin fails two independent ways. It refuses composite custom columns outright (`library_codes_dialog.py` validates `cc_datatype != "text"` and clears the active flag), which is fatal if you store the code as an identifier and project it with `{identifiers:select(lcc)}`. And its ISBN lookup queries the LCDB index `dc.identifier`, which the server has never supported: it answers with SRU diagnostic 1/16, `1007 (Bib-1 114 Unsupported Use attribute)`, "Unsupported index". The index that resolves an ISBN there is `bath.isbn`, confirmed live against `lx2.loc.gov:210` alongside `dc.title` and `dc.creator`, which do work and are what the plugin's author/title fallback happens to use. So the plugin's primary path, the one its own description advertises, has been dead while the fallback quietly carried it.

Storing to `identifiers` rather than to a column value is the deliberate difference. Identifiers are the catalogue's canonical home for external keys, a composite column displays the value with no second copy to drift, and `reconcile_file_metadata.py` already carries identifiers into embedded file metadata. Books that already hold an `lcc` are skipped unless `--refresh` is passed, so the tool is naturally incremental against a growing library.

Two operational facts shape the design. The Library of Congress rate-limits harder than the plugin's 1.6s pacing suggests: at 0.6s between requests it began resetting connections after roughly twenty queries, and a clean 60-query run at 2.0s posted zero errors. Pacing therefore defaults to 2.0s with exponential backoff, the run aborts after eight consecutive failures rather than hammering a service that has stopped answering, and every result including a miss is cached to `~/.cache/cquarry/library_codes.json`. That cache is what makes a multi-hour pass survivable: an interrupted run resumes for free, and a repeat of the same 12 books fell from 30s to 0.09s.

Coverage is partial and worth knowing before committing hours to it. Measured on a 7,362-book reference library, a clean 60-book random sample of ISBN-bearing books resolved **18, or 30%**. The average is misleading, because the distribution is steep: `NonFic.Tech` hit 5/7, while `Fic.Fantasy` managed 3/16 and `Fic.Contemporary`, `Fic.Horror`, `Fic.Translated` and `Gaming.TTRPG` returned nothing at all across the sample. LoC catalogues academic, technical and canonical trade titles well and genre fiction, indie and small-press releases, translations and tabletop material poorly. The dry run is therefore the default and reports a per-branch hit rate, and `--tag` scopes a pass by anchored-hierarchical tag prefix so the dense half can be run without paying for the sparse half.

`--apply` backs `metadata.db` up to the sibling `.backups` directory before writing and refuses to run while Calibre is open. `INSERT OR REPLACE` is correct here specifically because `identifiers` is `UNIQUE(book, type)`, unlike the enum custom-column link tables where the same statement would append a second row.

**New: `spot_check.py --review`, a judgement mode for the checks no pattern can make.** The mechanical lints in that script decide whether a field has the wrong *shape*. They cannot decide whether a title is the right title, whether an author field holds the person who wrote the book, or whether a description describes this book rather than another one. Review mode puts those three questions in front of a reader in a form that can be answered in bulk: full title, authors, context line and complete description, emitted in numbered chunks with a matching `.ids` file, answered with a verdict file, and accumulated in a ledger.

The ledger is what makes it worth doing more than once. Reviewed books drop out of later samples, so repeated runs converge on full coverage while the random sampling that makes a partial pass statistically meaningful is preserved. `--worklist` prints the BAD entries as a punch list; nothing is ever written back to `metadata.db`.

**Recording refuses unless the verdict ids reconcile exactly with the ids emitted**, in both directions, and refuses again on any verdict word that is not `OK` or `BAD`. This is the load-bearing part rather than a nicety: a reviewer working through hundreds of records drops some, and a short list looks identical to a complete one. On the reference library the same failure has now been recorded five times against agent output, once during the run that motivated this mode. Six tests cover the drop, invent, malformed, roundtrip, worklist, and chunking paths; the suite grows to 70.

**Also fixed: `reconcile_file_metadata.py` truncated any identifier value containing a space, and reported the book as drifted forever.** `parse_identifiers` split the `ebook-meta` output on `[,\s]+`, commas or whitespace, so `lcc:BF637.S4 G63 2007` parsed as `lcc = "bf637.s4"` with the rest silently dropped. The parsed value never matched the database, so the book was reported as drifted no matter how many times it was successfully re-embedded. The split is now on commas alone, which is the format `ebook-meta` actually emits.

This is a latent bug rather than a new one, and it went unfound because nothing could reach it: every identifier type in use until now (isbn, goodreads, storygraph, google, amazon, oclc) is space-free. Library of Congress call numbers are the first that are not, so writing `lcc` identifiers is what exposed it. Measured on the reference library, a reconcile pass over 655 freshly embedded books reported 469 of them as still drifted before the fix and 1 after, that one being an AZW3, which genuinely cannot carry arbitrary identifier types. Tests grow to 69.

One deviation worth recording: the XML comes from a plain-HTTP endpoint and is parsed with `xml.etree.ElementTree`. `defusedxml` is the conventional hardening and is not an option in a stdlib-only project, so the response is instead capped at 8 MB before parsing, which bounds the entity-expansion exposure without a dependency. ElementTree does not resolve external entities, so there is no XXE path.

## v3.7.1 (2026-07-31)

**`spot_check.py` reported a complete book as `EPUB_EMPTY_SPINE` when the package used the legacy OEB 1.0 namespace.** `check_epub` resolved the manifest and spine with a hardcoded `{"o": "http://www.idpf.org/2007/opf"}`, so any package declaring `http://openebook.org/namespaces/oeb-package/1.0/` instead (OverDrive-era conversions) matched nothing at all: no manifest, no spine, and therefore a HARD failure and a nonzero exit code on a book that opens perfectly. Found by the first full-library pass, which flagged exactly one hard failure across 7,339 books, #8048 *Dying Inside*: 31 content documents, 446,083 characters of body text, a spine listing every one of them, and a checker that could not see any of it.

Manifest items and spine itemrefs are now matched by local element name through a `_by_local_name` helper, so the package's declared namespace stops mattering. This is the same root cause as the known `audit_epub.py emptytext` false positive on legacy OEB files; that analyzer is untouched here and still carries it.

Tests grow to 64 (an OEB 1.0 package resolves its manifest and spine).

## v3.7.0 (2026-07-31)

Three `spot_check.py` correctness fixes and one new advisory flag, all found by running the checker against the 7,339-book reference library and then auditing what it did not catch.

### Fixes

**`_MOJIBAKE` missed the commonest lead byte of all, `Ã¢`.** The pattern enumerated individual accented characters (`Ã[©¨¤¶¼£±]`), which covers `Ã©`/`Ã£` but not `Ã¢`, the double-encoded form of a curly apostrophe and by far the most frequent mojibake in scraped blurbs. One live case was missed on the reference library (#2658 *Observability Engineering*, `youÃ¢??re doing`). Replaced with `[ÃÂ]` followed by anything in U+0080-U+00BF: that band is never valid text, because a real Portuguese `Ã` is followed by an ASCII vowel (`Ãvila`) and never by latin-1 supplement punctuation. Verified against ten cases including `São Paulo`, `café society` and `Ãvila`, which must stay clean.

**The comment, title, and author lints read raw markup instead of text.** `lint_comment` stripped tags but never decoded HTML entities, so a description of `&amp;` repeated forty times measured 200 characters and cleared the 120-character stub gate on 40 characters of real content (one live case). The same blindness hid any mojibake stored in entity form. A new `plain_text()` helper strips tags, drops `script`/`style` bodies, decodes entities, and normalizes non-breaking spaces; the lints and the review bundle's blurb excerpt all route through it, so every check now sees what a reader sees.

**OPF hrefs were resolved without URL-decoding.** Manifest hrefs are percent-encoded per the EPUB spec, so a content document whose filename contains a space arrives as `%20` and never matches the zip namelist, which reads as a missing spine item and therefore a HARD failure and a nonzero exit. No book in the reference library trips it (checked all 4,890 EPUBs: zero false positives cleared by decoding), so this is a latent correctness fix rather than an observed one, but the failure mode is silent and severe enough to close. Fragments are stripped before resolution as well.

### New

**Advisory `COMMENT_TRUNCATED`: a description that stops mid-word.** The motivating defect, found 2026-07-30: a metadata download returned four of six Jacqueline Carey blurbs cut off mid-sentence (`Blessed Elua founded Terre d'A`, `Raphael de Mereliot, her manipul`). Nothing caught them, because every existing check passes: the text exists, is well-formed, and is long (977 to 2,265 characters). Only the final word gives it away. A whole-library sweep then found roughly fifteen more.

The check is deliberately gated and deliberately advisory. It needs a wordlist and reads `/usr/share/dict/words` on exactly the terms the PDF and DJVU checks already use for exiftool and djvused: used when the system has it, silently skipped when it does not, never a Python dependency. Descriptions legitimately end without punctuation all the time (blurb attributions, series lists, contents dumps), so the heuristic requires the final word to be lowercase, absent from the wordlist, ASCII, preceded by whitespace, in prose of at least fifteen tokens, and not part of a URL or a numbered contents line.

Measured honestly on the reference library: **22 flagged out of 7,339, of which 6 are confirmed truncations (27% precision, 43% recall against a hand-built set of 14)**. The residual false positives are systematic and worth knowing before trusting a flag: modern technical vocabulary absent from an old wordlist (`microservices`, `autoscaling`, `lifecycle`, `asyncio`), and truncations whose final fragment happens to be a real word (`the co`, `on the st`) are missed entirely. It is a lead generator for the judgment pass, not a verdict, which is why it is not in `HARD` and does not affect the exit code.

Tests grow to 63 in `tests/test_scripts.py` (mojibake lead-byte coverage with the Portuguese and French negatives, entity decoding and the stub-gate skew it caused, and truncation detection with its proper-noun, URL, and complete-prose negatives).

## v3.6.0 (2026-07-03)

### New Features

**`audit_epub.py` gains a fourth analyzer: `ocr`, flagging OCR/conversion-damaged prose.** The defect class none of the existing three catches: a bad conversion splits paragraphs mid-sentence at line-wrap or page-break positions, so the text reflows broken ("could just make out the shape" / "of another boat"). The motivating case was a damaged Jingo EPUB that measured 80 such splits where a clean edition of the same text measured 0. The primary signal is exactly that split shape (a paragraph ending without terminal punctuation, the next starting lowercase, paired only within one spine document), reported as a per-book rate normalized by paragraph count. `all` runs it inside the same single decompression pass as the other three.

The hard problem was separating damage from intentional style, and rate alone cannot do it: deliberately unpunctuated literary prose (Fosse's Septology, Evaristo's *Girl, Woman, Other*, Kingsnorth's *The Wake*, Faulkner) posts split rates far above genuinely damaged books. The discriminator that works is where the fragment ends. Style breaks at clause boundaries ("...she started out in theatre"); damage breaks at line-wrap positions, which land on function words ("sat the disembodied" / "heads who were..."). On the reference library every style book measured at most 11% of splits ending on a function word and every hand-confirmed damage case at least 26%, so the flag gate requires 25%, alongside minimum-splits, minimum-paragraphs, and rate floors. The function-word set is a small closed list, the same spirit as the content analyzer's stopword votes, not a dictionary.

Five false-positive idioms found during validation are guarded explicitly: paragraphs interrupted by a rendered figure (an inline formula or card-diagram image reads as a split otherwise; `_Blocks` now records when an image falls between two blocks' text), display math set as text (mostly non-alphabetic fragments), back-of-book indexes rendered as paragraph blocks (the "See also" signature), epistolary sign-offs (a dangling short unterminated fragment), and the block-quotation idiom of academic prose (pairs into or out of a `<blockquote>`, plus attribution fragments ending on "that"). Secondary signals are reported but never gate: en-dashes embedded inside words (`bottom–feedin'`), doubled opening quotes (`' 'Course`, with the first quote required to follow whitespace so British single-quote dialogue's close-then-open sequences stay invisible), and space-stripped proper nouns recurring alongside their hyphenated form (`AnkhMorpork` vs `Ankh-Morpork`).

Hand-validated against the full 4,605-EPUB reference library, every flagged book inspected: 105 flagged, 104 confirmed damage, one borderline residue (display quotes publisher-styled as plain paragraphs, indistinguishable without CSS). Documented out of scope: character-substitution errors ("sonic" for "some") need a wordlist the stdlib-only contract rules out, and damage whose signature is word truncation or whitespace corruption rather than paragraph splitting. Tests grow to 60 in `tests/test_scripts.py` (split detection, dialogue-fragment and scene-break non-splits, image-interrupted pairs, style-vs-damage discrimination, threshold boundaries, and an `all` run including the new analyzer).

## v3.5.0 (2026-07-02)

The persistent-curses-screen rework, closing the last item in the roadmap's "Port from the Lattice TUI audit" section (Lattice T7, shipped there as v4.10.0). Purely a lifecycle change; no menu, prompt, or mode behavior differs.

### Changes

**One curses screen per session.** Every menu, prompt, pause, and pager used to be its own `curses.wrapper` init/teardown, so multi-prompt flows visibly flashed to the shell between widgets. `interactive_menu` now opens the screen once and every widget draws into it (`_with_screen`); a widget invoked outside a session still gets its own one-shot wrapper, so nothing changes for direct callers. `_reset_terminal` becomes a no-op while the session screen lives (running `stty sane` under curses would undo `cbreak`/`noecho` beneath it). Measured under a pty: a full menu, prompt, Esc-cancel, mode, pager, quit session enters the terminal's alternate screen exactly once, where the same flow used to enter it once per widget.

**One degradation path.** A curses failure at session startup or mid-session (unknown terminal, capability lost) funnels through `_degrade_to_text`: the screen is suspended once and the rest of the session runs the text fallback. All the v3.3.2 guarantees hold (no stuck terminal, no silent exit 0); a pager that dies mid-display now also degrades and prints the mode's output as plain text instead of eating it. Ctrl-C at the menu (curses or text fallback alike) ends the session cleanly with exit code 130 and the screen restored, while EOF at the text menu stays a quiet Quit.

`tests/test_tui.py` grows to 29 cases; the session lifecycle was additionally verified end-to-end under a pty (alternate-screen count, degraded startup on an unknown `TERM`, and a full cancel-then-run flow against the live library).

## v3.4.0 (2026-07-02)

The rest of the Lattice TUI audit ports (roadmap section "Port from the Lattice TUI audit": T2, T4, T6, and the fallback-menu generation). Lattice shipped all of these in its v4.9.0; this release keeps the two shared curses skeletons aligned.

### Changes

**Esc in a prompt now cancels back to the menu instead of accepting the default (behavior change, port of Lattice T2).** In menus Esc has always meant back; in prompts it meant "accept the default", so mis-selecting a mode and mashing Esc launched it with all defaults. A cancelled prompt now unwinds the whole prompt chain back to the menu with nothing launched; bare Enter still accepts the default, and Ctrl-C or EOF at a prompt cancels the same way (the text-fallback prompts used to exit the whole program on Ctrl-C). The hint bar now reads "Enter Accept, Esc Cancel, Ctrl-U Clear". Cancelling the first-run prompt exits without persisting anything (exit code 1, the CLI's no-database signal, so unattended runs never read as success), and cancelling "Change database path" leaves the saved path untouched.

### Fixes

**Output-file prompts expand `~` (port of Lattice T4).** `~/reports/x.txt` used to create a literal `./~/reports/` directory, since the TUI has no shell to expand it. Every output path the TUI collects now goes through a shared `_prompt_out()` that expands the tilde but does not absolutize, so relative paths keep meaning the current directory. The results pager also gains a footer naming the resolved absolute path, so "where did my report go" answers itself; the footer is suppressed when the mode errored or was cancelled, so it never claims a file that was not written.

**The no-curses fallback menu is generated from the same sections the arrow-key menu renders (port of Lattice's v4.8.1 fix).** The numbered listing and its key map were hand-maintained twins of `_MAIN_SECTIONS`, exactly the pattern that silently desynced in Lattice (fallback keys dispatching the wrong modes). `_build_fallback` now derives both from the sections, the word aliases ("catalog", "stats", ...) stay as an explicit supplemental dict, and tests pin that every entry is reachable with its number matching its label.

**Widget paper cuts (port of Lattice T6).** A bad answer at a number prompt re-asks with a "not a number, try again" note instead of silently using the default. On terminals shorter than the menu, the box shifts so the selected row stays visible instead of clipping blind below the bottom edge. The pager gains horizontal panning (arrow keys or h/l) with ellipsis markers on lines that continue off-screen, computes its width once instead of on every keypress, and keeps the scroll position valid across resizes. Ctrl-U clears a prompt field, so editing a long pre-filled path no longer means backspacing through all of it. And the export format prompt validates its answer against json/csv/ai, re-asking instead of accepting any string.

`tests/test_tui.py` grows from 7 to 24 cases, pinning all of the above.

## v3.3.2 (2026-07-01)

### Fixes

**TUI hardening ported from the Lattice TUI audit (2026-07-01).** `cquarry/tui.py` shares its curses skeleton with Lattice's; the two carry-overs from that audit's high-severity findings land here (roadmap section "Port from the Lattice TUI audit", items H7 and H6's exception-boundary half). First: a curses init failure no longer reads as Quit. On capability-poor terminals (`TERM=vt100`, dumb terminals) the color setup or `curs_set` raised `curses.error`, which the menu loop treated as the user quitting, so the TUI silently exited 0 even though the text fallback menu works. Cosmetic capabilities are now non-fatal (a monochrome TUI beats a dead one), and a real `curses.wrapper` failure flips the session to the text fallback menu instead of exiting. Second: `_run_with_capture` now has an exception boundary. A mode error used to escape as a raw traceback and lose the captured output; it is now paged under an `[Error]` heading with the traceback plus whatever was captured, and Ctrl-C pages a `[Cancelled]` notice the same way. New `tests/test_tui.py` (7 cases) pins both behaviors. The remaining Lattice carry-overs (Esc-cancels-prompt, `~` expansion in output prompts, generated fallback menu) stay on the roadmap until their Lattice counterparts land.

## v3.3.1 (2026-06-30)

### Fixes

**`audit_drm.py` no longer flags a freed EPUB on a leftover marker file.** v3.3.0 treated the mere presence of `META-INF/rights.xml` (Adobe ADEPT) or `sinf.xml` (Apple FairPlay) as DRM. But those are token/voucher files, not the lock itself: the actual lock is content encryption, which a DRM'd EPUB records in `encryption.xml` against its XHTML. When a book is freed, the content is decrypted but the marker can stay behind, so a bare marker with no content encryption is a residual artifact, not a locked book; it reads and embeds fine. The first whole-library sweep surfaced exactly one such case (Warhammer *Helsreach*: a `sinf.xml`, no `encryption.xml`, 37 plain-XHTML chapters), which is the same residual-artifact shape as the PDF that motivated the tool. EPUB classification now keys on actual content encryption (`encryption.xml` with non-font entries) and names the scheme from whichever marker is present; a standalone marker is reported BENIGN as a "residual DRM marker". PDFs are unchanged: a residual handler dictionary there still breaks metadata embedding, so it is still flagged. With this fix the library's real DRM count is 48 (all recoverable PDF ADEPT dictionaries), with the lone FairPlay EPUB correctly cleared. Tests extended to 21 cases (residual markers benign; markers plus encrypted content still DRM).

## v3.3.0 (2026-06-30)

### New Features

**`audit_drm.py`: a cross-format DRM scanner (read-only).** The metadata and structural audits never look at encryption, so a DRM-locked file can pass `epubcheck`, report its page count, and even import, yet silently refuse to let its embedded metadata be rewritten. The case that prompted this was a z-library PDF carrying a residual Adobe ADEPT `EBX_HANDLER` dictionary that `qpdf --check` and `pdfinfo` both reported as "not encrypted" while `exiftool` choked on it, failing the reconcile embed. The new script classifies EPUB, PDF, and Kindle (MOBI/AZW3) files; DJVU has no DRM scheme and is reported N/A. It runs in library mode (formats and paths from `metadata.db`, opened strictly `mode=ro`) or directory mode (a recursive scan of loose files before import, for the pre-import battery), with the usual exit codes (0 clean, 1 DRM found or scan error, 2 setup error).

The design priority was not detecting encryption; it was not crying wolf. Two benign things look like DRM to a crude check and are explicitly cleared. Font obfuscation: an EPUB `META-INF/encryption.xml` that scrambles only the embedded fonts (the IDPF or Adobe `#RC` algorithms) is not a content lock; because publishers often name obfuscated fonts `fonts/00001.dat` with no font extension, an entry is cleared when it uses a font-scrambling algorithm OR targets a font resource (extension or a `fonts/` path), which an algorithm-only or extension-only check gets wrong. Permission flags: a PDF "encrypted" with the Standard handler and an empty user password opens with no password and is only flagged against printing or copying, so it is classed with `qpdf` as PERMISSIONS, not a lock. Real DRM is `rights.xml` (Adobe ADEPT) or `sinf.xml` (Apple FairPlay) in an EPUB, a content-encrypting `encryption.xml`, a non-Standard PDF security handler found by a streaming byte scan (so a residual or inactive dictionary is still caught, which is exactly the Irodov case), a password-locked PDF, or a non-zero Mobipocket encryption-type field.

The first whole-library sweep (6,651 files) found 50 DRM-locked files (49 Adobe ADEPT, 1 Apple FairPlay), with 135 benign font-obfuscation/permission cases correctly cleared and one early false positive fixed before release: five EPUBs whose Adobe `#RC` font obfuscation targets `fonts/*.dat` were initially misread as encrypted content, which is what drove the algorithm-or-target rule above. Ships with a unittest suite (`tests/test_audit_drm.py`, 19 cases) building zip, PDF-byte, and PalmDB fixtures for each format and verdict.

## v3.2.1 (2026-06-25)

### Fixes

**`reconcile_file_metadata.py --repair-pdf` now deletes the `.~qpdf-orig` backup qpdf leaves behind.** `qpdf --replace-input`, used to rebuild a broken cross-reference table before re-embedding, writes the pre-repair original to `<name>.~qpdf-orig` beside the file and never removes it. Across many reconcile passes these full-size copies accumulated inside the library tree, which is the worst place for them: Calibre scans that tree, and each one is a complete duplicate PDF. A sweep of one library turned up 21 such files totalling 403 MB. `embed_pdf` now unlinks the backup as soon as qpdf reports success (return code 0 or 3), before retrying the embed; a missing backup is a no-op, so the change is safe whether or not qpdf wrote one. Regression tests mock the exiftool/qpdf boundary to assert the backup is removed after a successful repair and that an absent backup does not raise. Pre-existing strays from older runs are not cleaned by the tool; remove them once with `fd -H '\.~qpdf-orig$' "<library>" -X rm`.

## v3.2.0 (2026-06-23)

### New Features

**`audit_epub.py emptytext` now flags partial / placeholder exports (new PARTIAL verdict).** The whole-book character count missed a failure mode: a DRM-locked or sample export where most chapters are an identical "content unavailable" placeholder while one or two real chapters carry enough text to clear the THIN floor, so the book validates, repairs clean, and reads as full-length to the old total-char check. The canonical case was a BookShout export of *Johannes Cabal: The Fear Institute*, where 15 of 17 chapters were the same 138-char "something went wrong loading... bookshout.com" stub; even after structural repair to zero epubcheck fatals it stayed a 2-chapter sample. The analyzer now flags PARTIAL when a known DRM-placeholder signature appears anywhere in the spine, or when the same short stub (12 to 600 chars) repeats across at least 3 spine documents and at least 30% of the spine. PARTIAL is a real defect (counts as FOUND, exit 1, needs re-sourcing), distinct from the advisory THIN. The false-positive guard is the per-document distribution: a well-made book full of small but DISTINCT section dividers does not trip it (only repeated-identical stubs do), so the three full novels in the batch that surfaced this stayed OK. Library and directory modes both report it.

## v3.1.1 (2026-06-23)

### Fixes

**`audit_epub.py` now percent-decodes spine hrefs, fixing false EMPTY verdicts.** OPF manifest hrefs are IRIs, so a content document whose archive filename contains a reserved character (commonly `!`, written `%21`; Sigil and calibre emit these routinely) was matched against the raw zip namelist undecoded, failed to resolve, and dropped out of the spine. A text-full book whose every chapter file had such a name resolved to zero readable spine documents and was reported EMPTY: the exact false positive hit on Martha Wells's *The Serpent Sea* (every `split_NNN.html` was named `CR!RT...`). The resolver now decodes the percent-encoding (UTF-8, with multi-byte runs decoded together) and strips any `#fragment` before matching the namelist. Stdlib-only via a small `re`-based decoder (`_pct_decode`); no urllib dependency added. The fix lands in the shared spine resolver, so all three analyzers (`content`, `pagenumbers`, `emptytext`) benefit. Regression tests cover the decoder (reserved char, multi-byte UTF-8, invalid escape) and an end-to-end encoded-spine EPUB.

## v3.1.0 (2026-06-20)

### Changes

**The three EPUB-content audits are merged into one `scripts/audit_epub.py`.** `audit_epub_content.py`, `audit_epub_pagenumbers.py`, and `audit_epub_emptytext.py` shared the same spine resolution, library/directory dual-mode, read-only contract, and exit codes, and differed only in the per-book verdict; they are now three analyzers behind one tool, selected by subcommand: `audit_epub.py content|pagenumbers|emptytext|all [directory]`. The detection logic of each is unchanged (same thresholds, same results). Two wins beyond removing the duplicated scaffolding: `all` opens each EPUB once and runs all three analyzers in a single decompression pass (the expensive part is decompression, so this is much faster than three separate full-library runs), and there is now one spine resolver to maintain instead of three slightly-diverging copies. The old script names are removed; update any caller to `audit_epub.py <mode>`. The `--min-chars` / `--thin-chars` knobs (emptytext) carry over.

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

**f-string with no placeholders.** `f"
Languages:"` → `"
Languages:"`.

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
