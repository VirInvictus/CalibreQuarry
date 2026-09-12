# CalibreQuarry — Roadmap

What's done, what's next. Updated as of v3.26.0.

---

## Phase 1: Core Engine & Single-File Design
*Pure Python stdlib, zero external dependencies. Reading `metadata.db` natively.*

- [x] Read-only database access (`?mode=ro`)
- [x] Auto-detection of `metadata.db` location
- [x] Hierarchical tag matching (Calibre convention)
- [x] Virtual library search expression parser (tags, vl, boolean, parens)
- [x] Cached `get_all_books()` for performance in batch modes

## Phase 2: Display & Export Modes
*Replacing complex shell pipelines with native outputs.*

- [x] Text catalog grouped by author with ratings and series info
- [x] All-wings batch catalog generation (one file per virtual library)
- [x] Library statistics (formats, ratings, tag taxonomy, publishers)
- [x] Audit mode (untagged, unrated, coverless, series gaps)
- [x] Recent additions display (`--recent N`)
- [x] Series listing with completeness status and gap detection
- [x] Full library export to JSON or CSV
- [x] Virtual library listing with book counts

## Phase 3: Interactive TUI & Modifiers
*Navigating the data efficiently.*

- [x] Interactive menu (curses TUI with scrollable pager)
- [x] `--show-tags` modifier for tag display in catalogs
- [x] `--show-id` modifier for Calibre ID output (scripting)
- [x] `--primary-only` modifier for single-author display
- [x] `--quiet` modifier for minimal output
- [x] **TUI upgrades (Lattice-style):** Persistent DB config, immersive output capture, styled curses pause, settings menu
- [x] **Full Python package:** `src/cquarry/` with hatchling build, `pip install .`, `cquarry` console script

## Phase 4: Extended Capabilities (Future)
*Expanding on the analytics without altering the database.*

- [x] **Search Query Export** — Run Calibre-style search expressions directly from the CLI to generate a text file of matching results. The tool will notify the user and avoid creating an empty file if the query yields no results.
- [x] **AI-readable export** — token-efficient flat format for LLM recommendation prompts
- [x] **Tag tree visualization** — display the full hierarchical tag taxonomy as a tree
- [x] **Reading pace stats** — books added per month/year trend from `timestamp` column
- [x] **Duplicate detection** — same title+author appearing in multiple formats or editions
- [x] **Custom column support** — read user-defined Calibre columns for display and filtering
- [x] **Cover quality audit** — flag books with covers below a resolution threshold
- [x] **Author statistics** — per-author breakdowns (book count, ratings, formats, series)
- [x] **Wing overlap analysis** — show which books appear in multiple virtual libraries
- [x] **Genre share breakdown** — every top-level genre as a % of the whole library (`--analytics genres`, rendered over cquarry >= 1.12's `genre_distribution()`), with `--genre-depth N` descending the hierarchy (v3.28.0)
- [x] **Format migration report** — identify books only available in deprecated formats (MOBI, LIT)
- [x] **Color CLI output** — ANSI color for terminal output in non-interactive mode
- [x] **Tag dump** — flat list of every tag with book counts, replacing `calibredb list_categories -r tags`

## Phase 5: Comprehensive Search Parity & Companion Tools (v3.0.0)
*A faithful, stdlib-only port of Calibre's search engine, plus the maintenance scripts that live alongside the read-only core.*

- [x] **Dedicated search engine** (`search.py`): ported grammar (quotes, escapes, parens, implicit AND) and candidate-set boolean evaluation
- [x] **Full field-location support**: title, authors, series, publisher, tags (hierarchical), rating, formats, languages, dates, identifiers, comments, cover, id, uuid, `#custom`, `all`, `vl:`
- [x] **Match kinds**: contains (accent/case-folded), `=` exact, `~` regex, `^` accent; numeric and date relational operators; boolean columns
- [x] **Documented parity deviations** (regex engine, ICU folding, templates, anchored tags) recorded in `spec.md` and `README.md`
- [x] **`--search` to stdout** and structured (`--format json/csv/ai`) output; empty query returns the whole library
- [x] **Deeper cover audit**: seek-based JPEG SOF scan (no 1 KB cap) plus PNG dimension reading
- [x] **Half-star glyph** (½) and a corrected series "complete" definition
- [x] **Companion `scripts/`**: `compress_pdf.py` (write-capable) and `audit_epub_content.py` (read-only), documented as outside the package contract
- [x] **Portable test suite**: parser/matcher/integration tests with no live-library dependency; Python floor raised to 3.14
- [x] **Fixed the TUI analytics crash** (missing imports) and cleared all linter findings

## Phase 6: Metadata Companion Scripts (post-v3.0.0)
*More `scripts/` tools that read the curated database and act on it; outside the read-only package contract.*

- [x] **`validate_metadata.py`** — integrity linter (no language, duplicate ISBN, junk identifiers, orphan cc-links) plus an optional taxonomy-driven opinionated layer
- [x] **`reconcile_file_metadata.py`** — diff the curated `metadata.db` against each file's embedded metadata and embed the DB values back (calibredb for EPUB/MOBI/AZW3, exiftool for PDF, djvused for DJVU); dry-run by default, `--apply` only touches drifted files
- [x] **`--repair-pdf` for `reconcile_file_metadata.py`** — opt-in flag that, when an exiftool write fails on a broken cross-reference table, rebuilds it in place with `qpdf --replace-input` and retries the embed (page count preserved). Default off because it structurally rewrites the file. Automates the by-hand fix done during the 2026-06-07 full-library run, where 20 PDFs hit "Invalid xref table".
- [x] **`audit_epub_pagenumbers.py`** — reads EPUB body text to flag print page numbers (and running headers) baked into the flow by bad PDF/OCR conversions, which reflow mid-sentence. Flags only genuine prose interruptions (lowercase continuation, word split, running-header abutment); leaves chapter/section numbers and endnotes alone. Hand-validated against the full reference library: 21 true positives, no false positives.
- [x] **`audit_drm.py`** (v3.3.0): cross-format DRM scanner (EPUB/PDF/MOBI/AZW3; DJVU is N/A), library or loose-directory mode, read-only. Clears the two benign cases a crude check trips on (font obfuscation, including `fonts/*.dat` named fonts; PDF permission flags) and catches residual/inactive handler dictionaries by streaming byte scan. Built after a residual Adobe ADEPT dictionary in a z-library PDF (#7893) slipped the pre-import battery and broke its reconcile embed. First whole-library sweep flagged 48 live DRM files (all recoverable Adobe ADEPT PDF dictionaries; v3.3.1 reclassified a lone residual FairPlay EPUB marker as benign once it was clear the marker, not content encryption, was all that remained).
- [x] **`audit_epub.py ocr` analyzer** (v3.6.0) — a fourth body-text analyzer (`content|pagenumbers|emptytext|ocr|all`) flagging OCR/conversion-damaged prose, the defect class none of the existing three catches. Primary signal: mid-sentence paragraph splits, where a paragraph ends without terminal punctuation (lowercase letter or comma) and the next paragraph starts lowercase. Motivating case (2026-07-03): a damaged Jingo EPUB measured 80 such splits ("could just make out the shape" / "of another boat"); a clean edition of the same text measured 0. The planned rate-only threshold turned out not to separate style from damage (deliberately unpunctuated literary prose — Fosse, Evaristo, Kingsnorth, Faulkner — posts higher split rates than damaged books); the shipped gate adds a function-word-fraction discriminator (damage splits at line-wrap positions, so fragments end on function words; style splits at clause boundaries) plus guards for five legitimate idioms found during validation (figure-interrupted paragraphs, display math as text, rendered indexes, epistolary sign-offs, block quotations). Secondary signals, all dictionary-free to keep the stdlib-only contract, are reported but never gate: en-dashes embedded inside words (`bottom–feedin'`), doubled opening quotes (`' 'Course`), and space-stripped proper nouns recurring alongside their hyphenated form (`AnkhMorpork` vs `Ankh-Morpork` in the same book). Hand-validated against the full 4,605-EPUB reference library, every flagged book inspected: 105 flagged, 104 confirmed damage, 1 borderline residue (publisher-styled display quotes, indistinguishable without CSS). Out of scope, documented in the script header: character-substitution errors ("sonic" for "some") need a wordlist; word-truncation and whitespace-corruption damage carry different signatures. `all` gained the fourth analyzer inside the same single decompression pass. Tests grew to 60: split detection (true mid-sentence split; dialogue fragment and scene break as non-splits), image-interrupted pairs, style-vs-damage discrimination, threshold boundaries, and an `all` run that includes the new analyzer.

> **Historical note (added 2026-09-05):** the `audit_epub*.py` companions the rows
> above shipped were extracted to their own repository on 2026-08-21 (commit
> `7fce60c`) and no longer live in `scripts/`; the analyzers ship today inside
> bindery-cli as `bindery audit` (its v0.15.0). The rows stay as the release
> record; the scripts themselves are gone from this repo.

## Phase 7: Judgement & External-Catalogue Companions (v3.7.0–v3.8.0)
*Companion evolution continues: quality checks that need a human verdict, and the first external-catalogue integration. All in `scripts/`, outside the package contract.*

- [x] **`spot_check.py` correctness pass** (v3.7.0/v3.7.1): mojibake lead-byte coverage (`Ã¢`, the commonest form, via a `[ÃÂ]` + U+0080–U+00BF band match), entity decoding through a shared `plain_text()` so lints see what a reader sees, URL-decoded OPF href resolution, OPF manifest/spine matching by local element name (fixes the OEB 1.0 false `EPUB_EMPTY_SPINE`), and the advisory `COMMENT_TRUNCATED` flag (wordlist-gated, never affects exit code).
- [x] **`spot_check.py --review`** (v3.8.0): judgement mode for the checks no pattern can make (right title? right author? right description?): numbered review bundles with matching `.ids` files, verdict recording that refuses unless the ids reconcile exactly in both directions, a ledger that drops reviewed books from later samples, and `--worklist` as the BAD punch list. Nothing is ever written to `metadata.db`.
- [x] **`fetch_library_codes.py`** (v3.8.0): derive LoC Classification codes from the LoC SRU catalogue (`bath.isbn`, the index that actually works, unlike the existing Calibre plugin's dead `dc.identifier` path) and store them as `lcc`/`ddc` identifiers. Dry-run default with per-branch hit rates, disk-cached and resumable, 2.0s pacing with backoff and an eight-failure abort; `--apply` backs up `metadata.db` and refuses while Calibre runs.
- [x] **`reconcile_file_metadata.py` identifier-space fix** (v3.8.0): `parse_identifiers` split on commas *or whitespace*, so any identifier value containing a space (`lcc:BF637.S4 G63 2007`) was truncated and the book reported drifted forever; now splits on commas alone, the format `ebook-meta` actually emits.

## Maintenance (full-repo sweep, 2026-08-09, shipped as v3.9.2)
*Package and all seven companion scripts. Eight fixes, three additions, four cleanups, each pinned by a regression test (suite: 243 to 273). Full detail in `patchnotes.md` v3.9.2.*

- [x] Every `file:` URI is percent-encoded (`db_uri_ro`): a library path containing `?` or `#` opened a different file and failed with "no such table: books". Package plus six scripts; `fetch_library_codes.py` already did it right
- [x] `audit_isbns.py` / `fetch_library_codes.py`: `--tag` scoping considers every tag, not one arbitrary `LIMIT 1` pick. **Latent on the reference library** (all 7,439 books carry exactly one tag); reproduced with a two-tag fixture
- [x] `compress_pdf.py`: `--out-dir` aimed at the PDF's own directory is refused instead of destroying the original with no rollback
- [x] `catalog.py`: colliding wing filenames get distinct files; `write_catalog` creates a missing output directory like `run_audit`/`run_export` do
- [x] `spot_check.py`: `--review` on an empty sample reports instead of raising IndexError
- [x] `audit_drm.py` / `audit_epub.py`: locked-database snapshot fallback, matching the package and the other readers (tests take a real `BEGIN EXCLUSIVE` lock)
- [x] `spot_check.py` / `audit_drm.py`: explicit `encoding="utf-8"` on the last two writers lacking it
- [x] `--audit` flags `cover_file_missing` (has_cover set, file gone from disk)
- [x] TUI prompt reads whole characters (`get_wch`), so non-ASCII can be typed into a query or path
- [x] Every external tool call bounded by a timeout, each failure path handled; ghostscript deliberately left unbounded
- [x] `audit_epub.py` extracts each book's rendered text once, shared by `emptytext` and `ocr`, instead of twice under `all`
- [x] Stale docs closed: two `validate_library.py` references (not a script in this repo), "all three audits" for four, function-scoped `sqlite3` import, no-op branch in `stats.py`

## Maintenance (full-repo bug sweep, 2026-08-07, shipped as v3.8.1)
*Package, all seven companion scripts, tests, and docs. The package core was clean; the scripts yielded nine fixes, each pinned by a regression test (suite: 195 to 208). Full detail in `patchnotes.md` v3.8.1.*

- [x] `audit_drm.py`: qpdf exit 3 (unparseable) no longer reads as CLEAN; falls back to the trailer `/Encrypt` scan
- [x] `audit_epub.py`: injection signatures never count as expected-foreign; dead latin-1 decode fallback removed
- [x] `compress_pdf.py`: an output pdfinfo cannot read fails verification instead of skipping it
- [x] `reconcile_file_metadata.py`: commas are part of an author's name, not a separator (the "drifted forever" class, again)
- [x] `spot_check.py`: notes keep their commas; `--record` requires `--against`
- [x] `fetch_library_codes.py`: backups never clobbered; missing-DB exit code is 2 as documented; first tests
- [x] `validate_metadata.py`: one FORMAT_FICTION_PDF warning per book; first tests
- [x] Docs drift closed (spec §5, roadmap Phase 7, README test section, CLAUDE.md architecture tree); `test_queries.sh` VL query points at a live wing

## Maintenance (workspace sweep, 2026-06-09)
*Small behaviour-neutral pass; everything else was clean (42 tests green, default ruff rule set clean).*

- [x] **5x B904, `raise ... from` missing inside `except` clauses** (verified fixed 2026-07-02; `ruff check --select B904` is clean, the `from e` chains are in place): `helpers.py:207`, `search.py:301`, `search.py:327`, `search.py:393`, `search.py:433`. Re-raising without `from err` (or `from None`) loses the causal traceback chain; fixing it improves debugging of bad search queries.
- [x] Minor: 2x B007 unused loop variables, 1x B009 `getattr` with constant attribute. (verified fixed 2026-07-02; `ruff check --select B007,B009` is clean)
- **Do not "fix" as bugs: the B023 hits in `tui.py` (15 as of v3.5.0; line numbers drift) are false positives.** Every flagged lambda is passed to `_run_with_capture(...)`, which invokes it immediately within the same loop iteration, so the late-binding capture never bites. If the lint should be quiet, bind defaults (`lambda output=output: ...`); purely cosmetic.

## Port from the Lattice TUI audit (2026-07-01)
*`cquarry/tui.py` shares its curses skeleton with Lattice's `tui.py`. The 2026-07-01 Lattice audit (Lattice `roadmap.md`, section "Audit 2026-07-01", items H6/H7/T2/T4/T6/T7 plus the v4.8.1 fallback-menu fix) found bugs in that shared skeleton; the ones below carry over, keeping the two TUIs' behavior aligned. Lattice has since shipped its entire audit (T1-T6 in v4.9.0, T7 in v4.10.0, both 2026-07-02), so every remaining port here is unblocked. All are bug fixes, so they fit the "complete, bug fixes only" contract. Line numbers are as of v3.3.1 for the first five items and as of v3.3.2 for the T6/T7 items added 2026-07-02. What does NOT carry over: Lattice H6's terminal-corruption half (cquarry has no `IN_TUI`/`_TUIPbar` progress machinery and never calls `initscr()` outside `curses.wrapper`), Lattice T1 (multi-root), and Lattice T3: cquarry's change-database flow already validates before persisting (`tui.py:722-734`) and its first-run resolver re-prompts on bad paths (`tui.py:680-693`); that pattern is the model Lattice's T3 fix copies, not the other way around.*

- [x] **Port Lattice H7: curses init failure in `_tui_select` reads as Quit; TUI silently exits 0 on capability-poor terminals.** Identical shape to Lattice: unguarded `curses.curs_set(0)` (`tui.py:173`), `except curses.error: return None` around `curses.wrapper` (`tui.py:189-192`), and `interactive_menu` treats `None` as Quit (`tui.py:718`). On `TERM=vt100` or a dumb terminal the menu "opens" and the program instantly exits 0 despite the working `_box_menu` text fallback. **Fix (same as Lattice):** wrap `curs_set(0)` and the color init in individual `try/except curses.error: pass` (cosmetic failures must not kill the widget); catch `curses.error` from `curses.wrapper` itself, flip the module-level `_USE_CURSES` to False, and return a sentinel the menu loop re-enters on, so the next iteration renders the text fallback. **Test:** monkeypatch `curses.wrapper` to raise `curses.error`; assert `interactive_menu` falls back to the text menu instead of returning 0.
- [x] **Port Lattice H6 (exception-boundary half only): `_run_with_capture` runs modes bare.** `tui.py:438-441` calls `func(...)` with no try/except, so a mode exception escapes as a traceback and the captured output is lost. No terminal corruption here (no stray `initscr()`), so this is the crash-and-lose-results half only. **Fix:** wrap the call; `except Exception` pages `traceback.format_exc()` under an `[Error]` title through the existing `_tui_scroll_text`/print paths; `except KeyboardInterrupt` pages a "[Cancelled]" notice plus whatever output was captured. **Test:** `_run_with_capture` with a raising func pages the traceback instead of propagating (monkeypatch the pager to record).
- [x] **Port Lattice T2: Esc in a prompt accepts the default instead of cancelling.** Same semantics as Lattice (`tui.py:201` docstring "Esc returns the default", hint at `tui.py:249`), while Esc in menus means back/quit. Stakes are lower here (every mode is a read-only report; an accidental confirm just runs one), but the two TUIs should agree once Lattice flips Esc to a cancel sentinel. **Fix:** mirror Lattice's change: `_tui_prompt_str` returns None on Esc, `_prompt_str` propagates it, prompt chains abort back to the menu, bare Enter keeps meaning "accept default", hint bar becomes "Enter Accept · Esc Cancel". Land together with (or immediately after) the Lattice change, and note the behavior change in the patchnotes.
- [x] **Port Lattice T4: output-file prompts never expand `~`.** Outputs go through plain `_prompt_str` (`tui.py:752`, `:799`, `:840`; audit the other `"Output file"` prompts in the dispatch block while there), so `~/reports/x.txt` creates a literal `./~/` directory. The DB-path prompts already expand (`_prompt_path`, `tui.py:527`; explicit `expanduser` at `:682`, `:723`); only the output prompts are exposed. **Fix:** run `os.path.expanduser` on every output path the TUI collects (one small `_prompt_out()` wrapper used at all output sites; do not abspath, so relative paths keep meaning CWD). Echo the resolved absolute path in the confirmation output so "where did my report go" answers itself.
- [x] **Adopt Lattice's v4.8.1 fallback-menu generation (preventive; currently in sync).** The no-curses menu and its key map are hand-maintained (`_MAIN_FALLBACK_MAP` at `tui.py:578-614`, the hardcoded listing in `_select_main` at `tui.py:620-656`) while the curses menu renders from `_MAIN_SECTIONS` (`tui.py:534`). That is exactly the pattern that silently desynced in Lattice (its FOUND BUG 2026-06-10: fallback keys dispatching the wrong modes, newer modes unreachable) and was fixed by generating both the fallback listing and key map from the same sections the arrow-key menu uses (`_build_fallback` in Lattice `tui.py`), pinned by a test. Verified in sync today (keys 1-14/s/q all match the section tuples), but every future mode addition re-rolls the dice. **Fix:** port `_build_fallback`: derive the numbered listing and the key map from `_MAIN_SECTIONS`, keep the word aliases ("catalog", "stats", ...) as an explicit supplemental dict, and delete the hand-written listing. **Test:** a `tests/` case asserting the generated map's targets equal the section tuple space and that every section item is reachable, mirroring Lattice's `test_tui.py` pin.
- [x] **Port Lattice T6 (widget-UX batch; shipped in Lattice v4.9.0): the shared widgets carry the same paper cuts.** The sub-items that live in cquarry's copy of the skeleton: (a) `_prompt_int` silently swallows bad input and returns the default (`tui.py:548-553`); echo "invalid, using N". (b) `_tui_select` clips blind below the box on short terminals (`_safe_addstr` hides the crash but off-screen items are simply invisible); show "terminal too small" or scroll with the selection, mirroring Lattice's scroll-follows-selection choice. (c) `_tui_scroll_text` recomputes `max_line_len` over all lines on every keypress (`tui.py:375`) and chops lines at `content_w - 4` with no truncation indicator (`tui.py:407`); precompute once, add an ellipsis marker. (d) Prompt editing is append/backspace only (`tui.py:286-295`); support Ctrl-U (clear field) at minimum. Plus the local analog of Lattice T5(d): the export format prompt is unvalidated free text (`Format (json/csv/ai)`, `tui.py:872`) where `cli.py` has `choices`; re-prompt unless the answer is one of the three. What does NOT carry over: T6(e) (`_TUIPbar` throttle; no progress machinery here), T6(g) (playlists; no such mode), and the rest of T5 (ffmpeg/layout/prefer prompts are Lattice modes). **Test:** `_prompt_int` invalid-input echo; format prompt rejects a bogus value; pager truncation marker on an over-wide line.
- [x] **Port Lattice T7 (persistent curses screen; shipped in Lattice v4.10.0): one screen per session instead of one per widget.** Identical architecture here: menu, prompt, pause, and pager each run their own `curses.wrapper` (`tui.py:205`, `:297`, `:351`, `:437`), so a menu, prompt, mode, pager flow enters and leaves the terminal's alternate screen once per widget, visibly flashing to the shell in between. Lattice's fix: `interactive_menu` opens the screen once, widgets draw into it via `_with_screen`, a widget invoked outside a session keeps its own one-shot wrapper (nothing changes for direct callers), and a mid-session curses failure funnels through a single `_degrade_to_text` path that preserves the H7 no-silent-exit guarantee. Strictly simpler here than in Lattice (no `_TUIPbar` to re-home onto the shared screen). Purely a lifecycle change, no menu/prompt/mode behavior differs; port it as its own deliberate pass, after the behavior items above land. **Test:** under a pty, a full menu-to-quit session enters the alternate screen exactly once (Lattice measured seven entries before, one after).
- [x] **Phase 13:** Extract cquarry shared library

## Phase 13: Extraction (2026-08-23)
- [x] Extract `vir-tui` core into a standalone repository and replace local primitives with the shared dependency.
- [x] Adopt vir-tui 2.2.0's Phase-3 primitives — `interactive_session()`, `prompt_float`/`prompt_path`, `confirm`, `out_note`, `text_mode()` — deleting the duplicated session/prompt scaffolding (v3.22.0).

## Phase 12: Codebase Sweep & Robustness Hardening (2026-08-23)
*Context: Based on a full-repo sweep, addressing edge-case crashes, documentation desyncs, and expanding multi-threaded capabilities.*

### Bugs to Fix
- [x] **Custom Column Raw SQL:** Refactor `librarything.py` to use dynamic column mapping instead of hardcoding `books_custom_column_3_link`, preventing crashes on standard DBs.
- [x] **Test Script Invocation:** Update `test_queries.sh` to call `python -m cquarry_cli` instead of the extracted `cquarry` package.
- [x] **DB Lock Fallback in `audit_isbns.py`:** Implement `connect_ro()` with WAL/SHM snapshot fallback to prevent crashes when Calibre holds a lock.
- [x] **NULL Title Crash in `spot_check.py`:** Coalesce `None` titles to prevent `AttributeError` during linting.
- [x] **Series "of None":** Check for `max_idx is None` in `show_series` to fix formatting for unindexed series.
- [x] **Export Truncation:** Validate export format and custom columns *before* opening the output file to prevent 0-byte truncations.
- [x] **Lossy Title-Casing:** Stop using `str.title()` on duplicate book detection keys to prevent mangling proper nouns and apostrophes.
- [x] **Narrow Terminal Crash:** Enforce `visible_w = max(1, content_w - 4)` in the curses pager.

### Refactoring & Growth
- [x] **Clean Up Imports:** Remove duplicate `sys` and `Path` imports across companion scripts.
- [x] **Unify DB Snapshot Helper:** Move the WAL/SHM fallback logic from individual scripts into a shared `scripts/db_util.py`.
- [x] **Expand CC Orphan Audit:** Extend `check_orphan_cc_links` to audit single-value tables.
- [x] **Multi-Threaded Audits:** Wrap file inspection in `reconcile_file_metadata.py`, `audit_isbns.py`, and `spot_check.py` with a `ThreadPoolExecutor` for a 5-10x speedup.
- [x] **Docs Sync:** Bump versions in `spec.md` and `roadmap.md` to 3.13.0 to match the code.

## Phase 14: Pre-import screen & pre-stamp support (proposed 2026-08-27)

*Context: the acquisition pathway is a fixed three-phase pipeline — agent-run
pre-import vetting ("phase-1-import" skill), Brandon's manual import, agent-run
post-import curation ("phase-3-import" skill); both skills live in
`~/docs/Calibre Library/.claude/skills/`. Phase 1 already reaches into
`CalibreQuarry/scripts/` for `audit_drm.py` and `compress_pdf.py`. Two of its
remaining steps are still ad-hoc inline command sequences re-typed every batch, and
both are exactly this repo's companion-script shape: thin, stdlib + cquarry, external
CLIs for file work, read-only-or-explicit-write. This phase gives them a home. It also
closes a version/docs desync the repo's own tests currently cannot see.*

- [x] **`scripts/screen_duplicate.py` — loose-file vs library duplicate screen
      (read-only).** *(Shipped in 3.25.0: ebook-meta reads, ISBN-exact then
      scrub-normalized title+author via the search engine, within-batch
      screening, json/text reports, exit 0/1/2.)* Phase 1 § 3 screens every download against the library AND
      within the batch, matching on normalized title AND same-first-author AND ISBN
      (title alone misses worded-differently editions: "Capital: Volume I" vs
      "Capital: A Critique..."; loose author LIKE patterns flood results — a
      `%Lawrence%` match pulls in Mark Lawrence for a T. E. Lawrence book). Today
      this is a hand-written SQL/search session per batch. The script:
  - Input: a directory or file list (positional; EPUB/PDF/MOBI/AZW3). Read each
    file's embedded title/authors/isbn with `ebook-meta` (read-only invocation).
    Show the filename parse as a display hint only — the skill's rule is that
    AA/z-library filenames lie ("...Volume 1..." held Volume 3; titles arrive
    word-scrambled).
  - Match: exact-ISBN lookup first; otherwise normalized-title + first-author via
    cquarry's search (`db.search_books('title:"=..." AND author:"=..."')`), with
    normalization = case/accent fold + leading-article strip + edition/subtitle
    scrub. Reuse `cquarry.helpers.normalize_author_display` for the author side; do
    not `.split(",")` hydrated list fields (cquarry contract).
  - Output per candidate pair: existing id, title, authors, format, size (row
    `size`), pages (row `pages`, native `books_pages_link`), plus the same fields for
    the new file — the comparison columns the skill says to report before
    recommending keep/upgrade/re-source. `--format json` for the batch report. Exit
    codes mirror `audit_conversion_overrides.py`: 0 clean, 1 candidates found, 2
    setup error.
  - Tests in `tests/test_scripts.py` style: synthetic DB + stubbed `ebook-meta`.
- [x] **`scripts/stamp_pdf.py` — pre-stamp PDF metadata (writes FILES, never the
      DB).** *(Shipped in 3.25.0: fixed field set, ' & ' author join documented
      against --set-authors' ';', ebook-meta verification, STAMP_FAILED on
      read-back disagreement, mandatory out-of-tree --backup-dir, dry-run
      filename-derivation preview, mechanics-only docstring.)* Phase 1 § 6 pre-stamps bare-metadata PDFs (TTRPG modules, scans, indie
      releases) so phase 2 imports real titles instead of filename fragments
      (`5E - Wonderland.pdf` imports as Title "5E", Author "Wonderland"). The
      exiftool incantation is precise and its traps are already paid for; encode
      them once:
  - Flags `--title/--author/--publisher/--isbn`; dry-run by default; `--apply` to
    write. The field set is FIXED per the skill: `-Title` + `-XMP-dc:Title`,
    `-Author` + `-XMP-dc:Creator`, `-XMP-dc:Publisher` for publisher, and ISBN via
    `-Keywords="isbn:..."` — NEVER `-XMP-dc:Identifier`, which Calibre maps to a
    bogus `doi`. Multi-author joins with " & " (Calibre's separator — note this is
    the OPPOSITE of the `cquarry --set-authors` CLI, which splits on `;`; keep both
    documented in the help text).
  - Verification uses `ebook-meta` (Calibre's own reader), not an exiftool
    round-trip — exiftool reading back what exiftool wrote proves nothing.
  - On write-reports-success-but-readback-disagrees (the stubborn-XMP class, where
    even a qpdf rebuild does not help): print STAMP_FAILED with the field, exit
    nonzero, stop. The skill's rule: do not keep fighting; phase 3 fixes the field
    in SQL instead.
  - `--backup-dir` REQUIRED for `--apply`, and REFUSED if it resolves inside the
    directory holding the target files (a stray backup beside the file gets
    imported — the phase-1 cardinal sin).
  - Dry-run preview prints what Calibre would derive from the filename alone (the
    dash-split Title/Author) next to the requested stamp, so the before/after is
    visible without writing anything.
  - The script is mechanics only. Choosing the VALUES — researching the book online
    or reading its credits/copyright/back-cover pages (`pdftotext -f 1 -l 4`,
    `-layout` for column-formatted credits) — stays the agent's informed-judgment
    step per the skill. Say so in the docstring so a future editor does not bolt on
    web lookups.
- [x] **Version/docs re-sync.** *State as of 2026-08-27 (post 3.21.0 work):* the
      full 3.21.0 release (`--book`, `--entities`, `--reading-progress`,
      `--columns`, `--info`, the write-verb expansion, `writeops.py`, new tests)
      sits in the working tree UNCOMMITTED with VERSION/pyproject/`__init__.py`
      and the patchnotes entry all at 3.21.0 — commit it first. The desync this
      item exists to prevent already happened once at HEAD: the "Patchnotes:
      3.20.0" commit landed with VERSION/pyproject/`__init__` still at 3.19.0.
      Still stale after that release: spec.md header reads 3.15.0; this roadmap's
      header reads "as of v3.12.0"; spec § 5's companion table is missing
      `audit_conversion_overrides.py` (shipped between releases, documented only
      in `.clinerules`); `scripts/fetch_library_codes.py.bak` is scratch litter
      by this repo's own cleanup standard. Extend `tests/test_version.py` to
      parse the top `# X.Y.Z` heading of patchnotes.md and pin it equal to
      `cquarry_cli.VERSION`, so the patchnotes-vs-code desync class is caught by
      CI instead of by the next agent to notice.
      *(Done in 3.23.1: both headers current, the companion-table row added,
      the `.bak` and orphaned `audit_epub*.pyc` deleted, and `test_version.py`
      now pins the newest patchnotes heading to `cquarry_cli.VERSION`. The
      README's "completed software" note was rewritten the same pass; its "no
      new features are planned" claim contradicted this roadmap's open phases.)*
- [x] **Skill sync**: phase-1-import (Brandon's library,
      `~/docs/Calibre Library/.claude/skills/`) should name `screen_duplicate.py`
      in its duplicate-screen step and `stamp_pdf.py` in its pre-stamp section
      once shipped. **Floor, not ceiling**: any behavior-affecting discovery made
      while building these scripts — a flag that landed differently, a failure
      mode the tests surfaced — gets documented in the affected skill in the same
      release, even when this phase didn't predict it.

Non-goals: no EPUB pre-stamping (Calibre reads EPUB OPF natively; the skill forbids
it); no in-`~/Downloads` backups or writes beyond the stamped file itself; no
deletion of library copies on duplicate hits (that stays a Brandon-decision, phase 3).

## Phase 15: Phase-3 batch dossier & write-verb completeness (proposed 2026-08-28)

*Context: the 2026-08-27 acquisition batch was curated end to end against this
CLI, so the friction points are known precisely. 3.22.0's `--book BOOK_ID`
dossier already answers "show me everything about one book" — this phase makes
it batch-shaped and closes the two write gaps the batch exposed.*

> **Status (2026-08-28, after 3.23.0):** the `--set-pubdate` box below is
> shipped, and the multi-verb batch mode it required landed in the same
> release (see its ship note). Everything else in this phase is open; the
> dossier-consumption box at the bottom is BLOCKED on cquarry Phase 9, whose
> full-mine scope (dossier, integrity + analytics modules, ISBN family, path
> index, API.md split) is approved and written out in cquarry's roadmap — the
> next session resumes there and this repo's remaining boxes are its
> downstream sync (as release 3.24.0).

- [x] **`--book` batch forms**: accept comma-separated ids (`--book
  8884,8885,8886`) and an `--book --untagged` selector (the phase-3 entry state
  is "all untagged books"). Curating a batch today means a hand-rolled
  `get_book()` loop; the dossier renderer (`modes/detail.py show_book`) is
  already per-book and composes in a loop unchanged.
  *(Shipped in 3.26.0: `--book` takes a comma list; `--book --untagged` (no
  ids) sources them from `cquarry.integrity.find_untagged`. A bare `--book`
  or `--book 1 --untagged` are usage errors (exit 2); an unknown id inside a
  list renders the rest and exits 1, matching the single-id behavior.)*
- [x] **`show_book`: print `pubdate`.** The dossier prints added/modified dates
  but not the publication date — a field phase 3 explicitly checks (Jan-01
  placeholder dates are one of its standard catches). One-line fix in
  `modes/detail.py`. *(Shipped in 3.24.0: `published YYYY-MM-DD` in the facts
  line, suppressed while the `0101` sentinel stands.)*
- [x] **`--set-pubdate ID DATE` write verb** once cquarry ships `set_pubdate`
  (cquarry roadmap Phase 8): store the canonical TEXT form
  (`'YYYY-MM-DD 00:00:00+00:00'`). The 2026-08-27 batch's raw-integer pubdate
  writes tripped 8 linter errors (sentinel + unparseable) before being caught.
  *(Shipped in 3.23.0: `--set-pubdate` / `--clear-pubdate` plus a TUI
  Set/Clear Pubdate pair, all through cquarry 1.7's `set_pubdate`. Bonus that
  cquarry's batch context unlocked: several write flags in ONE invocation now
  run in a single `WritableCalibreDB` inside one `cquarry.batch()`
  transaction (all-or-nothing, per-verb summary after the commit), which is
  the actual "fix it all in one transaction" shape the phase-3 skill wanted;
  `--remove-book` refuses batch combinations. The TUI edit session keeps one
  transaction per op on purpose: wrapping the whole interactive menu in a
  batch would hold the write lock while the user thinks.)*
- [x] **`scripts/fetch_library_codes.py` Calibre-detection guard**: its
  "Calibre is running" refusal matches concurrent process ARGS, so a parallel
  Bindery sweep whose command line contains "Calibre Library" false-positives
  it (bit twice in one batch, 2026-08-27). Detect the GUI by exact process name
  (`pgrep -x calibre`) or by attempting the DB write lock — not `pgrep -f` over
  the whole process table.
  *(Done in 3.23.1, with a correction: the args-matching mechanism was a
  misdiagnosis. The probe has been name-only since 2026-08-09 and could only
  refuse on a real calibre-named process; on 2026-08-30 a live check confirmed
  the refusals' likely source was Calibre itself. The real defect was the
  opposite of the record: `-x "calibre"` full-name matching can never see the
  calibre-parallel job workers, whose comm truncates to "calibre-paralle", so
  the guard under-refused while a worker touched the DB. The probe is now an
  anchored name-only `pgrep ^calibre` (GUI + debug + parallel workers, still
  never args), verified live against a running Calibre with four workers, and
  the phase-3 skill's wrong "matches process args" warning was rewritten in the
  same pass.)*
- [x] **Skill sync**: the phase-3-import skill in Brandon's library
  (`~/docs/Calibre Library/.claude/skills/`) should name the `--book` batch
  form in its "read EVERY field" step, and soften its LoC-sequencing warning
  once the guard fix above stops the false positives. **This item is a floor,
  not a ceiling**: any behavior-affecting discovery made while building — a
  flag that landed differently, a default that changed, a new failure mode the
  tests surfaced — gets documented in the affected skills in the same release,
  even when this phase didn't predict it.
  *(Done in 3.26.0: the skill's dossier step names the batch forms and
  `--book --untagged`; the LoC step teaches the one-pass `--all-codes`
  invocation and drops the strict manual-serialization warning (the writer
  now retries over contention); the manual-insert SQL there had already moved
  to `wdb.set_identifier` in the cquarry 1.9 sync.)*
- [x] **`fetch_library_codes.py` misses worklist**: when the hit rate is under
  100%, emit a worklist file (id, title, identifiers) of the misses so the
  skill's mandatory manual-research pass starts from a file instead of terminal
  scrollback (2026-08-27: 3 misses were tracked by hand).
  *(Shipped in 3.26.0: `--misses-file` (default
  `fetch_library_codes_misses.txt` in the CWD) gets every book ending the pass
  with no LCC as `id<TAB>isbn<TAB>ddc<TAB>title`; written in dry runs too — it
  is a report artifact, not a database write.)*

- [x] **Consume cquarry Phase 9's `get_book_dossier()`** once it lands: `show_book`
  becomes a thin renderer over the composed dossier dict instead of hand-calling
  ten read APIs (cquarry roadmap Phase 9 is the mine this comes from).
  *(Shipped in 3.24.0: show_book renders the dossier; `--audit` predicates run
  through `cquarry.integrity` and `--analytics`/`--stats` through
  `cquarry.analytics`, with old-vs-new output verified byte-identical on the
  real library for the audit CSV, stats, and analytics.)*

Non-goals: no `--get-id` alias (the verb is `--book` and it shipped in 3.22.0);
no new read APIs (they belong to cquarry per the frontend-only split).
- [x] **`fetch_library_codes.py` SQLite locking & concurrency protection**:
  - **Context**: During a Phase 3 import (2026-09-02), dispatching `--apply` (for LCC) to the background and immediately dispatching `--apply --write-ddc` (for DDC) created concurrent DB writers, risking SQLite lock contention on `metadata.db`.
  - **Required Fix**: Enhance `fetch_library_codes.py` to handle both LCC and DDC code fetches efficiently in a single pass (e.g., via `--all-codes` or if `--write-ddc` is passed, check both at once without locking each other), or implement robust retry/backoff logic for SQLite `database is locked` errors during `WritableCalibreDB` transactions.
  - **Skill sync**: Upon completion, update the `phase-3-import` skill in Brandon's library to teach the new unified invocation or remove the strict manual-serialization warning if the locking is fully hardened.
  - *(Shipped in 3.26.0, both halves. `--all-codes` does one pass for both
  codes — they arrive in the same SRU response, so the second pass only ever
  re-read the cache; its selection covers books missing either code, and the
  DDC write left the LCC branch (a DDC-only book used to be silently dropped
  even under `--write-ddc`). The writer moved from raw `INSERT OR REPLACE` to
  `cquarry.write.WritableCalibreDB.set_identifier` in one `batch()` — touched
  books finally land in `metadata_dirtied` and `last_modified` moves — with
  retry/backoff over `database is locked` on top of the module's 30s busy
  timeout; identical values are honest no-ops, so `--refresh` re-runs report
  the real change count. `--clear-identifier` (already present since 3.19.0)
  now routes through cquarry 1.9's explicit helper.)*

## Phase 16: Set-oriented write verbs & the phase-2 write mechanics (proposed 2026-09-05)

*Context: approved by Brandon 2026-09-05. The read side became batch-shaped in
Phase 15 (`--book 1,2,3`, `--book --untagged`); the write side never did. Every
write verb carries exactly one inline id today, multi-verb mode composes verbs
over one invocation's flags with no shared target set, and the planned automated
phase-2 import (Phase 17) needs scoped set writes: clear the downloaded tags and
rating on the ids it just imported, set `#audience`, apply mechanical fixes.
Two safety facts shape the design: cquarry's `set_rating(id, None)` is a true
clear (deletes the link, prunes the orphan) while `--set-rating ID 0` writes a
phantom 0-rating row that reads as unrated; and the library NON-NEGOTIABLES ban
bulk edits of ratings, which the design honors by making the rating clear legal
ONLY against a manifest of ids that same run imported.*

- [x] **Target-set mechanism** — exactly one source per invocation, mutually
      exclusive (else exit 2): `--ids ID[,ID...]`, `--from-search 'EXPR'`
      (resolved read-only via the search engine before anything opens
      writable), `--from-untagged` (reuses `find_untagged`), `--from-manifest
      FILE` (ids one per line or comma-separated; unknown ids reported and
      aborted before `--apply`). All existing single-book verbs stay
      byte-for-byte unchanged; combining a target source with inline ids is a
      usage error.
      *(Shipped in 3.29.0. Mutual exclusion is enforced at the argparse
      level and re-checked in `setwrite`; `--ids` and `--from-manifest`
      ids are validated against the library read-only and unknown ids
      abort exit 2 before anything opens writable; duplicate ids collapse
      preserving order. Set writes dispatch before single-book verbs so a
      combination is refused before anything executes.)*
- [x] **Set-mode verbs** (id-less `--batch-*` forms reusing the existing action
      builders and `run_write_batch`): add/remove tag, clear tags, clear
      rating, set/clear column, add-column-value (append for `is_multiple`
      columns), set/clear pubdate, set title/authors/publisher/languages/
      series (+`--series-index`), set/clear identifier, set cover, remove
      format. `--remove-book` is NOT available in set mode: deletion stays
      per-book, explicit, and recoverable.
      *(Shipped in 3.29.0: 21 verb flags over the shared writeops action
      builders quieted, with the cquarry 1.13 helpers landing as three new
      builders (`action_clear_tags`, `action_clear_rating`,
      `action_add_column_value`); the set runner lives in
      `src/cquarry_cli/setwrite.py`.)*
- [x] **Dry-run by default.** Prints the target source verbatim, the resolved
      id count and list, and a per-verb preview; `--apply` executes and
      nothing opens `WritableCalibreDB` without it. On apply: Calibre-closed
      guard (anchored `pgrep ^calibre`), mandatory `--backup-dir` (the
      `stamp_pdf.py` precedent), then ONE `batch()` transaction;
      `--commit-per-book` as the documented non-default escape hatch for very
      large sets.
      *(Shipped in 3.29.0, all as written. The backup dir must sit outside
      the library directory, not merely exist; the pgrep guard is the
      `fetch_library_codes.py` one, timeout meaning assume-running.)*
- [x] **The rating carve-out, mechanically encoded:** `--batch-clear-rating` is
      accepted only when the target source is `--from-manifest`;
      `--from-search`, `--from-untagged`, and `--ids` are refused with exit 2.
      The NON-NEGOTIABLES' bulk-ratings ban stays enforced for every set
      Brandon could point at; the automated phase-2 reset stays legal because
      the manifest proves which ids that run imported. Set-mode verbs also
      refuse `#reading_status`, `status`, and `date_read` by label with exit 2
      (belt-and-braces on the absolute ban).
      *(Shipped in 3.29.0; the label refusal is case-insensitive and strips
      the `#`.)*
- [x] **Reporting:** per-verb `applied / already-so / failed` counts plus a
      per-id failure list, and `--format json`
      (`{target, verbs, results[{id, verb, status, detail}], committed}`) so
      an AI caller consumes the run. Exit 0/1/2. Requires threading the
      setters' `changed` returns through the actions (today the batch summary
      prints "ok" unconditionally, so applied vs already-so is not
      distinguishable).
      *(Shipped in 3.29.0, in two halves: the changed-return threading landed
      on main early (honest applied/already-so batch summaries, single-verb
      output untouched), and set mode consumes it for per-verb counts, the
      stderr failure list, and the JSON report (which adds a `dry_run`
      flag). Any failed row rolls the whole pass back and reports
      `committed: false`, exit 1.)*
- [x] **Depends on cquarry** (its roadmap Phase 11): `clear_tags(book_id)`,
      `add_custom_column_values(book_id, label, values)` (Pattern-A append
      with dedupe against the `UNIQUE(book, value)` link table), optional
      `clear_rating` alias. Everything else already exists in
      `cquarry.write`.
      *(cquarry 1.13.0 shipped all three; the pyproject floor moves to
      `>=1.13.0` in this release.)*
- [x] **Skill sync**: phase-3-import names the set forms where it teaches
      per-id loops, if any land in its workflow. Floor, not ceiling, per the
      standing rule.
      *(Done in 3.29.0: the skill's CLI-verbs paragraph names the sources,
      the `--batch-*` forms, the dry-run/apply lifecycle, and calls out the
      rating carve-out so phase-3 rating changes stay per-book.)*

Open questions (Brandon): flag naming (shared sources + `--batch-*` verbs, as
 specced, vs per-verb `@file` in the existing id slot); whether
`--batch-clear-rating` should also accept an explicit `--ids` list; backup
location (mandatory `--backup-dir` vs automatic `.bak-*` beside the DB); and
whether `--set-rating ID 0` should be remapped to a clear or rejected in the
single-book path (it currently writes the phantom row).
 *(Answered 2026-09-06, all four: as specced on naming, manifest-only, and
 `--backup-dir`; `--set-rating ID 0` remaps to a true clear.)*

Non-goals: no `add_book` (cquarry Phase 10); no book deletion in set mode; no
set-mode comments/description writes (description curation is phase 3's
curated house-voice step; bulk comment overwrite is exactly the regression
class it guards against); no implicit library-wide predicates; no change to
single-verb behavior; no dependency pinning.

## Phase 17: The acquisition run commands — `run phase1/2/3` + the manifest (proposed 2026-09-05)

*Context: Brandon's 2026-09-05 decision promotes the three-phase acquisition
pathway (today: the `phase-1-import` skill vetting `~/Downloads`, his manual
import + metadata download, the `phase-3-import` skill curating `metadata.db`)
into first-class CLI subcommands. `run phase1` and `run phase3` turn each
skill's mechanical steps into one orchestrated pass that a user or an AI agent
can run, surfacing judgment calls as structured decisions instead of re-reading
prose. `run phase2` is the NEW automated middle phase, and it AMENDS the
pathway's written-in-stone division of labour (library `CLAUDE.md` "The
acquisition pathway"; the phase-1 skill's "Never import on Brandon's behalf").
Feasibility was researched 2026-09-05 against the skills, the tooling, and the
calibre 9.14 reference clone: import via cquarry's `add_book` (cquarry Phase
10) with `calibredb add` as the documented fallback; headless metadata download
is real — `calibredb` has no download command, but Calibre ships
`fetch-ebook-metadata`, which drives the same source plugins the GUI uses, and
`calibredb set_metadata` applies the result; downloaded junk tags/ratings are
cleared through Phase 16's set-writes, manifest-scoped. Dependencies: bindery
Phase 13 (`audit --json`, `run phase1`/`run phase3` slices), cquarry Phase 10
+ Phase 11, this repo's Phase 16.*

**The pathway amendment, to land verbatim in the library `CLAUDE.md` and both
skills in the same release as `run phase2`:** Phase 2 becomes tool-driven.
`cquarry run phase2` imports ONLY the files on the phase-1 manifest's
`approved_for_import` list, and that list is final only after Brandon answers
the manifest's `decisions_needed` — his answer is the sign-off. The
NON-NEGOTIABLES write shape binds phase 2 exactly as it binds phase 3
(audit first, show findings, sign-off, `.bak` backup, one transaction for the
DB-side pass, verify counts, docs updated). The "bulk edits of ratings" ban is
interpreted as a ban on library-wide predicates: phase 2's clear is scoped to
the ids THAT RUN imported, from its own manifest, never a search expression.

- [x] **The manifest** (`acquisition-manifest/1`): JSON, one per batch, in the
      library-local `.claude/manifests/`. Carries per-file verdicts, checks,
      repairs + backup paths, stamps, duplicates + recommendations,
      quarantines, `decisions_needed`, and `approved_for_import`; phase 2
      appends imported ids, per-book download outcomes, clears, and fixes;
      phase 3 consumes it and emits the batch record. Machine-readable
      hand-off between the phases and the calling agent; the prose
      `.claude/project_preimport_*.md` record stays as the human summary.
- [x] **`cquarry run phase1 DIR`**: orchestrates the inventory, `audit_drm.py`,
      `screen_duplicate.py --format json`, `stamp_pdf.py` driving, quarantine
      moves, and the final report, plus a NEW `scripts/check_pdf.py` standing
      wrapper for the per-file PDF/DJVU battery (header, page count,
      `qpdf --check` with the real-vs-benign warning classes, `pdffonts`,
      `pdftotext` sample, `pdfimages -list`, `djvused -e n`) — the last
      hand-assembled battery in the skill. Invokes `bindery run phase1 --json`
      as a subprocess for the EPUB slice and also accepts
      `--bindery-report FILE` so the slices can be run peer-style by hand.
      Read-only against `metadata.db`.
- [x] **`--book --format json`**: machine-readable dossier output (cquarry's
      `get_book_dossier` already composes the dict; `show_book` renders text
      only today). Phase 3's structured input.
- [x] **`scripts/comments_census.py`**: the description mechanical sweep as a
      standing tool (`--`, spaced-hyphen dashes, `**`, `<br>`/`<div>` tags,
      non-`<p>` body shape, soft hyphens, zero-width characters, mojibake,
      ligature `?`, exact-duplicate bodies), retiring the inline three-liner
      the skill re-derives every run.
- [x] **`cquarry run phase2 --manifest FILE [--audience ...] [--yes]`**: guard
      Calibre closed + `.bak`; import each approved file through cquarry's
      `add_book` (seed title/authors/identifiers/language/pubdate/publisher
      from the manifest stamps; `calibredb add` as the documented fallback if
      cquarry Phase 10 slips); write new ids back to the manifest and
      cross-check them through `format_path_index()`; metadata download per id
      via `fetch-ebook-metadata --identifier isbn:... -o book.opf` +
      `calibredb set_metadata` (the one Calibre-open-safe segment; per-book
      outcome recorded; no-result or ambiguous becomes a `decisions_needed`
      entry, never a guess); clear tags + rating on the imported ids ONLY
      (Phase 16, manifest-scoped); set cc9 `#audience` (default `Brandon`,
      multi-value append via cquarry's `add_custom_column_values`); mechanical
      title/author fixes only (filename-derived titles, pipe artifacts) —
      real curation stays phase 3; and a clobber watch comparing phase-1 stamp
      authors against post-download authors, recorded in the manifest for
      phase 3 to restore from.
- [x] **`cquarry run phase3 --manifest FILE [--answer-file FILE]`**: validate →
      batch set = manifest ids cross-checked against `find_untagged` → dossier
      fetch (`--book --format json`) → decision gates (tag-by-precedent,
      description curation, field fixes) rendered as prompts on a TTY or
      consumed from `--answer-file` → ONE `WritableCalibreDB` `batch()`
      transaction → taxonomy-declaration reminder → subprocess
      `bindery run phase3` + `reconcile_file_metadata.py --apply --repair-pdf
      --id` → re-validate to 0 errors → emit the `.claude/project_import_*.md`
      batch record from the manifest.
- [x] **Skill sync (same release, both skills + the library `CLAUDE.md`)**: the
      pathway amendment above; the phase-1 skill names `run phase1` as the
      orchestrated form with its manual command list kept as the appendix; the
      phase-3 skill names `run phase3`; the phase-2 section records the cc6
      `source` decision below.
      *(All seven boxes shipped in 3.30.0, 2026-09-06. The manifest module
      carries the six decisions structurally; the verbs drive the companion
      scripts, bindery, and calibredb through mocked-subprocess tests (294
      suite total) against fixture libraries. The pathway amendment landed
      verbatim in the library `CLAUDE.md`; both skills name the verbs.
      Remaining before real use: Brandon's seeded facility run of the full
      phase1 -> phase2 -> phase3 pass against the acceptance criteria
      below: the suite proves the contracts, the facility run proves the
      pathway.)*

Acceptance criteria: a seeded test-Downloads run produces a schema-valid
manifest whose `approved_for_import` matches the hand-derived list; phase 2
with Calibre open, or with non-empty `decisions_needed`, exits 2 without
touching the DB; a 3-book test import yields exactly 3 new ids, zero tags,
zero ratings, audience set, and manifest ids equal to `find_untagged`; killing
phase 2 mid-batch leaves a resumable manifest and a `.bak` that diffs clean; a
phase-3 `--answer-file` run lands validator-clean with the batch record
written.

Open questions (Brandon): cc6 `source` in phase 2 (set from manifest provenance
vs leave for phase 3's `SOURCE_MISSING` loop); audience default (unconditional
`Brandon` vs a per-file manifest column); lossy-strip consent (does signing the
phase-1 report constitute standing consent for `--apply-lossy`); duplicate-net
skips (when the import refuses a file as already-in-library: stop for a
decision, or import anyway under `--duplicates` and flag the pair for phase 3);
download residue (GUI one-keystroke fallback vs push to phase 3); manifest
retention (archive after phase 3 vs keep as the durable record).
 *(Answered 2026-09-06, all six: stamp `#source` from manifest
 provenance; `#audience` unconditional `Brandon`; a signed phase-1
 report IS standing consent for `--apply-lossy`, scoped to the files
 the report listed; duplicate-refusal files are refused and flagged as
 `decisions_needed` while the batch continues; failed/ambiguous
 metadata downloads push to phase 3 (phase 2 stays non-interactive);
 manifests are KEPT in `.claude/manifests/` as the durable
 machine-readable record. Scheduling: build starts now, 2026-09-06.)*

Non-goals: no GUI automation; no Goodreads/Amazon API work (downloads ride
Calibre's own source plugins); no taxonomy or genre decisions in phase 2; no
bulk anything (every write is manifest-scoped); no replacement of the manual
path (the skills' command lists remain the fallback and the documentation of
record).

## Phase 18: hardening backlog from the 2026-09-08 audit sweep (proposed 2026-09-08, digging only)

*Context: a five-agent adversarial sweep of the whole repo (write path, run
verbs, read surface, tests and scripts, docs), prompted by the bindery-cli
sweep earlier the same day. No code was changed; findings were demonstrated
against synthetic /tmp fixtures, never the real library. The headline: the
suite is hermetic and genuinely good (298 tests, 1.6 s, all fixture-based),
Phase 16's set-write rollback held under everything thrown at it, but the
Phase 17 run verbs carry three P0-class findings and have plausibly never run
end to end against real files, and the read surface has one proven
data-destroyer. Three upstream (cquarry) fix candidates surfaced and are
noted for that repo's own sweep.*

*Verification postscript (2026-09-08, an independent batch re-derived the
five sharpest claims; all five CONFIRMED, with sharpening): the run.py
bindery seam has a THIRD break even after `--json` gains its FILE argument.
bindery's phase1 signals "trouble found" with exit 2, its normal outcome for
a downloads dir with any problem book, and run.py treats rc not in (0,1) as
failure, so the seam raises on exactly the case phase 1 exists to surface;
the installed bindery is 0.30.0, so the old-verb half is hypothetical today.
"Crashes on every non-empty directory" means "any directory holding an
ebook-extension file"; a djvu-only directory crashes at the
screen_duplicate exit-2 seam instead. The `--output` hole is shared by
`--search --output` and the annotations export (same `_open_out`), and the
overwrite truncates the inode rather than renaming. The Ctrl-C torn-write
window is the single-verb `run_write` path only (all batched paths roll
back) and needs the interrupt to land between a setter's write and its
`_mark_dirty`; the CLI then prints "Interrupted by user." with no hint a
write landed. `tests/test_run.py` mocks both broken seams (`_screen_duplicates`
and `_bindery_phase1`), which is how a green suite coexists with a phase1
that crashes on real input.*

### Run verbs (Phase 17): the flagship pathway is draft-quality

- [x] **Fix the two broken phase-1 seams (P0).** `run.py:282-286` calls
      `.get` on screen_duplicate's JSON, which is a bare list, so
      `run phase1` dies with AttributeError on any non-empty directory; the
      same comprehension would flag every screened file as a duplicate
      because the filter on actual hits is missing. And `run.py:208` invokes
      `bindery run phase1 DIR --json` with no value for `--json` (bindery
      requires a FILE), while the `bindery` on PATH also predates the run
      verb; either way the seam raises. Both slipped through because every
      test mocks these seams. Fix: consume the list and keep only hit
      records; pass a temp file to `--json`; pin the bindery entry point. *
      *(Shipped in 3.32.0, 2026-09-09, bb3f3a3: the list report is consumed with the hit filter on; the screener gets only files its own extension set covers, so a djvu-only tree is a clean screen; bindery gets `--json FILE` via a temp report with its real exit contract honored (2 = trouble found, report written); the seam tests pin the instruments' actual shapes, including one run against the real screen_duplicate.py.)*
- [x] **Make the manifest signature a real seal (P0).** `sign()` sets a bare
      `signed = true` boolean in the same editable JSON file
      (`manifest.py:185-191`); nothing binds it to contents, and
      `validate()` never cross-checks `approved_for_import` against the
      per-file verdict, so a manifest whose rejected file is listed as
      approved passes and phase 2 imports it (proven end to end). Fix:
      stdlib HMAC over the canonical approved-set + stamps + lossy list,
      recomputed by phase 2, plus the verdict cross-check. *
      *(Shipped in 3.32.0, 2026-09-09, d780f75: HMAC-SHA256 over the approved set, stamps, lossy flags, and decisions; every load recomputes it; the re-sign path is the new `cquarry run sign` verb; approve() and validate() refuse any approved-list/verdict disagreement, so the forged-approval attack is caught twice. Spec 3.3 documents the seal; the phase-1-import skill synced same release.)*
- [x] **Stop phase 1 from moving files without consent (P1).** The docs
      promise phase 1 is dry against book files without `--stamp`/
      `--apply-lossy`, but `_quarantine()` runs unconditionally for any
      verdict that is not clean/unscanned, which includes audit_drm's BENIGN
      (font obfuscation) and N/A (DJVU), i.e. every DJVU in the batch gets
      moved and a manual_repair decision recorded (`run.py:304-311`).
      Quarantine only true DRM hits, gate the move behind a flag. *
      *(Shipped in 3.33.0, 2026-09-09, b96efa1: audit_drm's own is_problem set only (DRM, ERROR); the move gated behind the new --quarantine consent flag; moved_to honest.)*
- [x] **Make quarantine and stamping non-destructive (P1).** `_quarantine`
      moves onto `basename` collisions, destroying the earlier file, and
      records the wrong `moved_to` directory (`run.py:218-223`, `309-311`).
      `--stamp` is a silent no-op: run.py points stamp_pdf's backup dir
      inside the vetted tree, which stamp_pdf itself refuses, and the
      failure is dropped without a message; `_stamp_backups` is also never
      excluded from the inventory, so a second run sweeps the backups
      (`run.py:276`, `106`). Collision-checked destinations, backups
      outside the tree, warn on nonzero exits. *
      *(Shipped in 3.33.0, 2026-09-09, afd9c34: numbered quarantine siblings on basename collision, stamp backups in a dated temp dir outside the tree (which is what makes --stamp actually work), WARNING on stamp failures, both instrument dirs excluded from the inventory.)*
- [x] **Close phase 2's accounting holes (P1).** The "never imported twice"
      docstring invariant is unimplemented (only the manifest's own
      `imported_id` is checked; add_book copies unconditionally), and the
      resume record is saved only after the unguarded download segment, so
      a crash there duplicates the import on rerun (`run.py:444-482`,
      `544`). `--audience` with no flag stamps the literal string `'None'`
      into `#audience` (`cli.py:708-712` + `run.py:481` + cquarry's
      `str(v).strip()`). The `--backup-dir` copy is a fixed-name file, so
      the second run to the same dir destroys the only restore point, and a
      raw copy2 can snapshot a hot journal (`run.py:372-374`). The rollback
      message claims "library restored" when nothing was restored, and
      add_book's file copies for books that succeeded before the failure
      stay behind as orphan directories (`run.py:486-490`). `--yes` is a
      dead flag; `--apply-lossy` on phase 1 is accepted and never used. *
      *(Shipped in 3.33.0, 2026-09-09, 84e7e20: resume record saves before the download segment; --audience defaults to the documented value; timestamped sqlite-API backups; honest rollback message; --yes deleted; --apply-lossy wired to the bindery slice. add_book orphans and the byte-identity floor closed upstream in cquarry 1.15.0.)*
- [x] **Give phase 3 the same rails as every other write path (P1).** It
      opens WritableCalibreDB with no closed-Calibre check (phase 2 and set
      mode both enforce `pgrep ^calibre`), and its fixes fallback
      `set_custom_column` accepts `#reading_status` from the answer file,
      the exact column the library NON-NEGOTIABLES ban
      (`run.py:602-691`, `687`; contrast `setwrite.py:131`, `571`). Also:
      bindery/reconcile exit 2 is a suppressed warning (fully invisible
      under `--quiet`) while validator-clean alone still exits 0
      (`run.py:702`, `720`); the post-commit calibredb download segment
      runs outside both the batch and the guard window. *
      *(Shipped in 3.33.0, 2026-09-09, 2b1095e: closed-Calibre guard before the answer gates; banned answer-file fields refused; mechanical trouble always prints, is recorded, and fails the verb; the download segment re-checks the guard and defers.)*
- [x] **Run-verb papercuts:** `_pdf_battery` ignores check_pdf's exit code
      and only scans the top level while `_inventory` walks recursively
      (`run.py:193-201`); answer-file loading raises raw tracebacks and
      silently ignores unknown ids (`run.py:646-649`); `_fetch_metadata`
      maps a timeout to "ambiguous"; quarantined files never appear in
      `files[]`, so the schema's `quarantined` verdict is writer-dead;
      `_existing_book_ids` is dead code; phase 2 shells `calibredb
      set_metadata`, contradicting the repo's own "No calibredb required"
      constraint. *
      *(Shipped in 3.33.0, 2026-09-09, 9330946: recursive battery with honored exit codes; readable answer-file errors with unknown-id warnings; timeout maps to failed; quarantined files in files[]; dead helper deleted; the calibredb shell-out replaced by _apply_opf over cquarry's write module.)*

### Write path (Phase 16): solid core, real edges

- [x] **Ctrl-C mid-single-verb-write commits the half-done mutation (P1,
      upstream component).** `run_write` runs the action bare
      (`writeops.py:54-57`); cquarry's setters roll back on `Exception`
      only, and `WritableCalibreDB.__exit__` commits unconditionally, so a
      KeyboardInterrupt commits the row change without the
      `metadata_dirtied` row, making the edit invisible to Calibre's OPF
      sync forever (reproduced). The batched paths are immune. Fix:
      `wdb.batch()` around the single action in run_write, and/or
      `__exit__` should skip commit when an exception is in flight (that
      half belongs to cquarry). *
      *(Shipped in 3.33.0, 2026-09-09, 686b180 locally; the __exit__ half shipped upstream in cquarry 1.15.0; an interrupt test proves nothing lands.)*
- [x] **`--commit-per-book` is mechanically inert and the report lies about
      it (P1).** The outer batch wraps the per-book inner batches, nested
      batches join the outer transaction, so the flag changes nothing while
      the output claims "Committed per book (3 transactions)"
      (`setwrite.py:594-600`). Implement it for real (with per-book
      committed/failed reporting) or delete it. *
      *(Shipped in 3.33.0, 2026-09-09, 5e6be70, implemented for real: per-book outermost batches, a failing book rolls back alone with rolled_back/book_committed-false entries, honest partial-rollback reporting, exit 0 only when every book committed.)*
- [x] **Empty-string flag values: silently dropped or silently destructive
      (P1).** `--batch-set-title ""` vanishes (truthiness gates) while
      `--batch-set-column audience ""` is collected and cquarry treats the
      empty string as clear, wiping the column on every targeted book with
      rc 0 (`setwrite.py:216-289`; `write.py:1116`). Validate `is not
      None`, reject empties with exit 2. *
      *(Shipped in 3.33.0, 2026-09-09, 0b1ee3d: the value sweep refuses empties at exit 2; _has_verbs counts value flags by presence so the refusal names the real problem.)*
- [x] **Promote the banned-column refusal to a shared chokepoint (P2).**
      `#reading_status`/`status`/`date_read` are refused only at set mode's
      entrance; single-book `--set-column` and the TUI both write them
      today (spec scopes the refusal to set mode, so arguably deliberate,
      but the same column is protected at one door and open at the other
      two). Fix in the action builders or cquarry's `set_custom_column`. *
      *(Shipped in 3.33.0, 2026-09-09, 1ddf073: writeops.FORBIDDEN_COLUMNS enforced in the builders -- every door closed by one check; cquarry 1.17 deferred the policy upstream.)*
- [x] **Set/write papercuts:** `--batch-set-cover maybe` crashes with a
      traceback and exit 1 instead of the contracted exit 2
      (`setwrite.py:330` vs `writeops._ArgError`); the `--batch-clear-rating`
      manifest gate is honor-system (any id file unlocks a bulk rating
      clear; the repo's own manifest validator is never consulted,
      `setwrite.py:397-402`); `--quiet` suppresses the failure detail the
      module docstring promises survives it (`setwrite.py:462-464`);
      `--remove-book`'s dry run opens the DB through the read-write handle
      (`writeops.py:429-447`); backups overwrite by fixed name (both here
      and in run.py); pgrep guard checked before the backup-dir usage
      error, TOCTOU window noted; `parse_book_id` accepts `int("5_0")`;
      dry-run JSON omits the resolved target ids. *
      *(Shipped in 3.33.0, 2026-09-09, d7ba622: all eight -- cover typo exits 2; the rating gate validates a real sealed manifest; failure detail survives --quiet; the dry run is read-only; timestamped backups; '5_0' rejected; ids in the JSON; usage check before the pgrep probe.)*

### Read surface

- [x] **A read mode can overwrite metadata.db itself (P0).** No output
      writer compares its path to the database path:
      `--export --output <lib>/metadata.db` replaced a fixture database
      with a JSON report, exit 0 (`export.py:39-51`; same hole in
      `catalog.py:83`, `audit.py:117`, annotations, exportlt). The
      read-only guarantee holds at the SQL layer only. Fix: one shared
      output helper that refuses the db path (and temp+os.replace for
      no-partial-file). *
      *(Shipped in 3.32.0, 2026-09-09, 1a3e139: cquarry_cli/output.py is that helper for every read-mode file output; db + sidecars refused, directory exporters also refuse the library root, temp + os.replace staging, refusal exits 2. Spec 3.3; test_read_modes replays the sweep's attack verbatim.)*
- [x] **The TUI dies wholesale on a malformed database (P0).** `CalibreDB`
      is constructed outside every exception boundary
      (`tui.py:426`), so a corrupt or foreign sqlite file at the chosen
      path ends the session in a raw traceback, including via
      "Change Database" (which validates only the filename suffix,
      `tui.py:56-66`). *
      *(Shipped in 3.32.0, 2026-09-09, f7729e3: the menu loop probe-opens the db every iteration, Change Database demands a real open before rebinding, and a mid-session sqlite3.Error degrades to the re-prompt; tests/test_tui_degrades.py gives tui.py its first coverage.)*
- [x] **The TUI ignores the saved config, then silently rebinds it (P1).**
      `_resolve_db_for_tui` consults a hard-coded default list that starts
      with CWD-relative `metadata.db` and never calls `get_db_path()`, so
      launching the TUI from any directory containing a stray metadata.db
      overwrites the shared config and the next CLI run reads the wrong
      library (`tui.py:139-150`). *
      *(Shipped in 3.34.0, 2026-09-10, 0d96450: the saved config wins whenever it exists; discovery binds only when nothing is saved; a test pins no-rebind.)*
- [x] **`--exportlt` breaks two contracts (P1).** It is the one frontend-only
      violation in the read surface (raw hand-built SQL over link tables,
      no schema degradation: crashes on a pre-`books_pages_link` schema,
      `librarything.py:112-139`), and its self-check verdict is discarded
      by the CLI: "do not upload" exits 0 (`cli.py:819-822`). *
      *(Shipped in 3.34.0, 2026-09-10, 0d96450 for the verdict half: run_librarything_export's exit code reaches the CLI; the raw-SQL half was already retired by the cquarry 1.17 export_rows adoption (3.31.0).)*
- [x] **Search/catalog failures exit 0 (P1).** `--search '((('` prints the
      parse error and exits 0 while the same failure under `--exportlt
      --search` exits 1; `--catalog --wing NoSuchWing` exits 0 leaving a
      stale catalog file (`export.py:205-209`, `cli.py:911`,
      `catalog.py:45-46`). Normalize: modes return exit codes; catch
      `ParseException` specifically. *
      *(Shipped in 3.34.0, 2026-09-10, 0d96450: --search parse failures exit 1; unknown wings exit 2 and never leave a stale catalog; write_catalog/run_search_export return codes wired through cli.)*
- [x] **Read-mode papercuts:** one corrupt epoch in `last_read_positions`
      tracebacks `--reading-progress` and `--book` (milliseconds-epoch
      pattern; `display.py:166`, `detail.py:142`); `--exportlt`,
      `--export-annotations`, `--untagged`, `--format-stats` sit outside
      the mutually-exclusive group and silently lose to whichever
      dispatches first (`--format-stats` is even declared in the write-verbs
      group); TUI Entity Browser pages a raw traceback for an invalid kind;
      negative `--recent N` prints a nonsense header; `--exportlt --output
      FILE` creates a directory named FILE; header-only `librarything_read.csv`
      when nothing is Read; the TUI footer claims "Report written to ..."
      when nothing was written; `--plugin-data` is silently dropped by
      `--format json`; `--exportlt` silently deletes matching csv files in
      the output dir. *
      *(Shipped in 3.34.0, 2026-09-10, 0d96450: --export-annotations/--exportlt/--format-stats joined the exclusive read-modes group (--format-stats out of the write group; --untagged stays a --book modifier by design); negative --recent refused; corrupt epochs render raw; the Entity Browser notifies instead of paging a traceback; plugin values ride in json/csv/ai; a directory target that exists as a file is refused. Remaining by design: the stale-csv sweep is the exporter's documented contract, now fenced behind the output guard's library-root refusal.)*

### Tests and scripts

- [x] **Make run_tests.sh actually run the tests (highest-value fix in this
      audit).** `CLAUDE.md:39` says "Run tests with ./run_tests.sh", but the
      script runs only the 27-command live-library smoke (read-only, exit
      codes only, outputs to /tmp) and zero of the 298 unittest tests;
      anyone following the doc gets smoke-only coverage. Prepend the
      unittest discover line (and merge test_queries.sh's overlapping
      queries in), or rename it smoke_library.sh and fix the doc. *
      *(Shipped in 3.34.0, 2026-09-10, d29414c: the script runs the unittest suite first, then the smoke; test_queries.sh left standing as the search-query deep-dive it is.)*
- [x] **Contract-test the run-verb instruments for real.** The whole P0
      class exists because screen_duplicate, bindery, and check_pdf seams
      are mocked in tests; `dispatch_run` (the CLI wiring for every run
      flag) has zero test references. Thin adapters plus tests that invoke
      the actual scripts against /tmp fixtures would have caught all three
      before shipping. Also untested: phase-2 rollback branch,
      `--stamp`/`_drive_stamp`, `_fetch_metadata` verdict parsing, the
      setwrite Calibre-running refusal branch, writeops lock-contention
      mapping, `--commit-per-book` failure semantics, validate_metadata
      (one test gates phase 3's exit code), and all of tui.py (zero
      coverage; it calls run_write directly, bypassing the pinned path). *
      *(Shipped in 3.34.0, 2026-09-10, d29414c: test_instruments.py drives the real scripts through the real seams (and caught check_pdf's undeclared --quiet); dispatch_run wiring, the phase-2 rollback deferral, stamp failures, fetch-verdict mapping, the setwrite Calibre refusal, the :722 per-book semantics, and the :690 rails all have named tests; tui.py's degrade paths covered in test_tui_degrades. The full-list ambitions (lock-contention mapping, every tui menu path) remain open depth.)*
- [x] **Suite hygiene:** mid-file `unittest.main()` guards silently
      truncate direct runs in three files (`test_write_flow.py:167`,
      `test_scripts.py:213`, `test_audit_drm.py:230`); six near-identical
      drifting `_SCHEMA` fixture strings want a shared builder;
      `test_manifest.py:115-119` uses the real library path as a throwaway
      literal; `fix_cq_lint.sh` is committed junk whose re-run would
      comment out every `try:` in export.py (delete); the two CI skips
      (`/usr/share/dict/words`, ghostscript) mean CI runs fewer assertions
      than this machine. *
      *(Shipped in 3.34.0, 2026-09-10, d29414c: the guards moved to true EOF (test_scripts had 1028 lines after its guard); the real-library literal de-realized; fix_cq_lint.sh deleted. Deferred with a dated note: the shared _SCHEMA builder (six fixtures, each tuned to its suite) and the CI-skip gap (host tools, not code).)*
- [x] **Scripts verdict: everything is alive; nothing to delete except
      fix_cq_lint.sh.** run.py drives six of them (screen_duplicate,
      audit_drm, check_pdf, stamp_pdf, reconcile_file_metadata,
      validate_metadata); fetch_library_codes, audit_isbns, spot_check,
      compress_pdf are standalone live tools; all ruff-clean and
      type-hinted. Upgrades: audit_conversion_overrides wants to become an
      `--audit` mode (per the promote-to-cquarry doctrine); db_util is
      half-consolidated (four scripts still carry private copies of
      connect_ro/calibre_running); comments_census claims `--json` is "for
      the phase-3 runner" but run.py never calls it (wire it or reword);
      taxonomy.example.yaml is reference material for another repo
      (docs/ would be tidier). *
      *(Shipped in 3.34.0, 2026-09-10, d29414c: moved to docs/ with the README pointer updated; comments_census's --json help no longer claims a runner that never consumed it. Deferred with dated notes: the db_util consolidation (the private connect_ro copies have genuinely drifted: reconcile needs Row rows and its own tmp layout) and the audit_conversion_overrides --audit promotion, its own box below (shipped 3.36.0, same day).)*

### Documentation

- [x] **Fix the two user-facing falsehoods in README:** the troubleshooting
      line claiming saved searches "match nothing" (they work; the same
      README says so 50 lines earlier, `README.md:391`), and the search
      engine living at the nonexistent `src/cquarry/search.py` plus "zero
      dependencies" (it is the cquarry dependency; three runtime deps,
      `README.md:312`, `:338`). Also clean the six botched
      "minimal-dependency (uses tqdm)" find-replace artifacts. *
      *(Shipped in 3.34.0, 2026-09-10: the saved-searches line corrected (they evaluate, cquarry 1.1+); the engine path named honestly (cquarry.search) and the zero-dependencies claim replaced with the real set; all six artifacts cleaned.)*
- [x] **Absorb phases 15-17 into spec.md:** seven shipped modes are absent
      from the Modes table (`--book` incl. `--format json`, `--entities`,
      `--reading-progress`, `--columns`, `--info`, `--exportlt`,
      `--format-stats`); `--set-pubdate`/`--clear-pubdate` are missing from
      the single-book verb list; spec §5 says "three of them write" while
      its own table lists four. *
      *(Shipped in 3.34.0, 2026-09-10: the seven mode rows added, --set-pubdate/--clear-pubdate in the verb list, §5's closing sentence names all four writers.)*
- [x] **Document the run verbs' flag surface in README** (`--stamp`,
      `--apply-lossy`, `--bindery-report`, `--audience`, `--yes` are
      file-side consents living only in patchnotes/help) and add companion
      script sections for stamp_pdf, screen_duplicate,
      audit_conversion_overrides, check_pdf, comments_census. *
      *(Shipped in 3.34.0, 2026-09-10: a run-verbs prose section names the consents (--quarantine added, --yes deleted since the sweep); the help dump regenerated mechanically; the five missing script sections written.)*
- [x] **Housekeeping:** all seven Phase 17 roadmap boxes carry a malformed
      `- [x] - [ ]` double checkbox; the patchnotes H1 title sits mid-file
      (entries are prepended above it) and the heading style shifted from
      `## v` to `# ` around 3.14.0; README's "213 tests" is stale (298). *
      *(Shipped in 3.34.0, 2026-09-10: the seven double checkboxes normalized; the H1 moved to the top and the `## vX.Y.Z` headings normalized to `# X.Y.Z`; the README count made current.)*
      *(Note on the checkboxes: leave Phase 17's boxes ticked, the features
      exist; the P0s above are correctness debt on top of shipped
      surface.)*

- [x] **Promote `audit_conversion_overrides` to a real `--audit` mode** (opened
      2026-09-10 from the sweep's scripts verdict, deferred from :829):
      the promote-to-cquarry doctrine applies once the predicate is worth
      a library home; until then the standalone script stands. *
      *(Shipped in 3.36.0, 2026-09-10, 949aaf6: `--audit` grows the
      `conversion_override` rows and a summary block, consuming cquarry's
      `get_conversion_profiles` (the predicate is already home; nothing
      upstream owed), while the standalone script keeps its pipeable
      --quiet/exit-1 surface and the mode keeps --audit's exit-0
      reporting contract. CSV row shape matches the audit's existing
      five columns.)*

- [x] **Filename-stamp parsers disagree on metadata-less files** (observed
      2026-09-09 while contract-testing :642): run.py's `_FILENAME_STAMP`
      reads `Author - Title`, but Calibre's `ebook-meta` filename
      fallback (which screen_duplicate.py leans on when a file carries no
      embedded metadata) guessed the opposite split in a probe. Both are
      seed data a human reviews before signing, so this is a small
      consistency question, not a data-destroyer; decide one convention
      and note it in both tools. *
      *(Shipped in 3.35.0, 2026-09-10, ffa30de: the writer decides; the
      stamping path emits run.py's reading and the observed corpus
      confirms it (libgen.li names are Author - Title, verified in the
      09-10 run), so "Author - Title" is THE convention; Calibre's
      opposite fallback (probed: "Brian Jacques - Mossflower.pdf" imports
      as Title "Brian Jacques") is noted at all three readers, with
      stamp_pdf's preview documented as deliberately mirroring Calibre
      and screen_duplicate naming the metadata-less screening gap. A
      corpus regression test pins the direction.)*

- [x] **check_pdf.py classifies qpdf exit 3 (warnings-only) as `errors`**
      (observed 2026-09-10 in the Redwall/Tech phase-1 run, the first
      real-file exercise since the 3.34 args.quiet fix): two PDFs whose
      only qpdf output was warning-class (unknown-token tolerance in one
      object; linearization `/E` and hint-table drift on the other, both
      files reporting "operation succeeded with warnings") were recorded
      as `qpdf_check: "errors"` with `qpdf_errors` findings, in the CLI
      summary and in the phase-1 manifest. The docstring already calls
      exit 3 benign ("warnings (benign; qpdf exits 3 on warning-only
      files all the time)"); the exit-to-class mapping does not honor
      it, so the benign class has no label and every warning-only scan
      reads as structural damage. Fix the mapping and re-triage the two
      warning kinds this run surfaced. *
      *(Shipped in 3.35.0, 2026-09-10, 6e075cf: root cause was the
      channel, not the threshold; qpdf writes its warnings and the
      summary line to stderr, so the stdout marker gate never matched.
      The class now reads the documented exit code alone (0 clean, 3
      warnings, 2 errors), warning findings carry the first warning line
      as triage evidence, and tests pin exit 3 as its own class.
      Re-triage: both warning kinds confirmed benign; the Multics PDF
      re-checks clean today because the phase-1 stamp rewrite healed the
      linearization drift.)*

- [x] **`provenance` is never populated, so phase 2's cc6 stamp is always
      the default** (observed 2026-09-10 after the Redwall/Tech batch):
      the manifest schema carries a per-file `provenance` field (the
      2026-09-06 decision binds phase 2's cc6 stamp to "the manifest's
      recorded provenance"), but `run phase1` leaves it None on every
      file even though the filename usually carries the evidence
      (`z-library.sk` / `1lib.sk` naming, the `-- Anna's Archive`
      suffix, `libgen.li`). Consequence observed twice (2026-09-08,
      2026-09-10): cc6 arrives as a blanket "Anna's Archive" regardless
      of true provenance, and phase 3 has to re-derive it from filenames
      every batch. Fix: derive `provenance` in the phase-1 runner from
      the same filename patterns `_FILENAME_STAMP` already works with,
      surface it in the manifest the review step corrects (same flow as
      stamps), and let phase 2 stamp cc6 from the corrected value. *
      *(Shipped in 3.35.0, 2026-09-10, 404537c: phase 1 seeds provenance
      from the observed site markers, mapped onto the #source enum's
      vocabulary: Anna's Archive trailer -> Anna's Archive,
      z-library.sk/1lib.sk -> Other (the recorded practice of both runs,
      pending Brandon's Z-Library enum decision), libgen.li -> Library
      Genesis, bare names -> None. The review corrects it like the
      stamps, phase 2 stamps cc6 from the reviewed value, and the seal
      now binds provenance so a post-sign source swap fails the load.)*

### Upstream findings (belong to cquarry's own sweep, noted here where found)

*(CLOSED UPSTREAM 2026-09-09: all three fixes shipped in cquarry 1.15.0
(commits 7c798a3, edf841e, 9a109b4) and the promotion candidates in
cquarry 1.17.0 (cee35c3). Recorded at this phase's ship notes and the
audit sheet's adoption block; nothing left to chase here.)*

- `WritableCalibreDB.__exit__` commits unconditionally, so BaseException
  (Ctrl-C) mid-write commits a torn edit; setters self-heal only on
  `Exception` (`cquarry/write.py:290-293`, `447-449`).
- `add_book` has no duplicate refusal (the "never imported twice" invariant
  needs a `data`-table check) and leaves orphan book directories when a
  later book in a shared batch fails.
- `add_custom_column_values` stringifies None into the literal `'None'`.
- Promotion candidates the frontend is waiting on: a flat book-row provider
  for exporters (kills --exportlt's raw SQL), a banned-column chokepoint in
  `set_custom_column`, and a shared write-session guard (closed-Calibre
  check + rotated backup + one-batch accounting + orphan compensation).

### Completeness verdict from the sweep

*Not almost complete, and the gap has a precise address. The foundation is
the strongest in the workspace trio: hermetic 298-test suite, honest
rollback in set mode, renderers that genuinely derive nothing, and version
sync enforced four ways. But Phase 17 shipped as scaffolding with contracts
attached: the pathway's own tests never exercise its two real seams, so the
verbs fail on first contact with real files, and the manifest's signature is
a boolean. The one proven data-destroyer (export overwriting metadata.db) is
a ten-line fix that should not wait. Suggested order when work resumes: the
two phase-1 seams and the output-path guard first, the manifest HMAC second,
phase 3's rails third; the facility run that Phase 17's own postscript calls
for should follow, not precede, those fixes.*

## Phase 19: the upstream comparison — FTS search, scoping, the tree audit, and the utility integrations (proposed 2026-09-12, from REPORT-12-Sept.md)

Two research passes compared this repo against everything upstream
Calibre exposes for library management (calibredb's 24 commands, the
GUI's library views and view layer, FTS, annotations, devices,
conversion, the polish tool) and against the full audit/quality problem
space. Full evidence and upstream pointers live in REPORT-12-Sept.md.
Cross-repo routing: B3/B5/B6 route to cquarry predicates and bindery
analyzers, recorded there as well. Nothing here re-opens Phases 16-18.

### A. The read-surface batch

- [ ] **`--fts` content search over `full-text-search.db`** (read-only;
  the plain `books_text` table needs no FTS5 machinery — the read half
  is cquarry Phase 13 A.1) with `--restrict-to`; plus index-staleness
  audit rows from `dirtied_formats`. M.
- [ ] **`--restrict SEARCH` scoping every read mode** (stats, audit,
  analytics, exports, catalog) by search or virtual library; upstream
  precedent `--restrict-to` (cmd_fts_search.py) and restricted-id
  category counts (db/categories.py:212). S/M.
- [ ] **Filesystem-vs-database tree audit** as --audit rows: orphan
  book dirs, extra/unknown files, missing/extra formats on disk,
  extra covers, malformed paths (upstream check_library parity,
  calibre/library/check_library.py:34-47; the CQ-native narrow form).
  M.
- [ ] **Reading analytics (read-only)**: status funnel from
  `#reading_status`; days-to-read and recent-finishes from `#date_read`.
  The NON-NEGOTIABLES write ban is untouched. S/M.
- [ ] **`--all-saved-searches` catalog sweep** (the --all-wings analog;
  saved searches live in the preferences table). S.
- [ ] **`@Name` user-category resolution** in search and analytics
  (upstream db/categories.py:114); only if the library's user
  categories are in active use. M.

### B. The audit-depth batch (routing marked)

- [ ] **Content-duplicate fingerprinting** (`audit_duplicates_content.py`):
  64-bit simhash over 3-word shingles of spine text; clusters with
  containment classification (re-download vs omnibus overlap). M.
- [ ] **Truncation cross-checks**: PDF real page count vs Count Pages
  data disagreeing >20% (the battery already reads the count), stale
  plugin data reported as its own class. M (EPUB-tail half routed to
  bindery).
- [ ] **Author-sort sanity**: cquarry predicate `find_bad_author_sorts`
  + --audit `bad_author_sort` render (advisory class; deliberate
  non-inverted sorts documented). S/M (predicate half routed to
  cquarry).
- [ ] **DB-level ISBN checksum/shape** in validate_metadata
  (INVALID_ISBN via cquarry's existing checksum helper). S (helper
  routed to cquarry).
- [ ] **Cover aspect distortion**: cquarry predicate
  `find_distorted_covers` + --audit render (ratio bands, advisory). S
  (predicate routed to cquarry).
- [ ] **FTS coverage audit**: books absent from `full-text-search.db`
  or indexed near-empty; "never indexed" vs "indexed empty" separate.
  S (predicate routed to cquarry).
- [ ] **PDF battery depth**: text layer sampled at pages 1/middle/last;
  image DPI parsed from the existing `pdfimages -list` output. S.
- [ ] **Copyright-year vs pubdate** as an audit_isbns extension
  (VARIANT-class advisory; (c)-year patterns only). M.

### C. The integration batch (all subprocess-driven; no new deps)

- [ ] **`run convert` format-conversion batching**: drive
  `ebook-convert` per search set; register output via cquarry
  add_format/remove_format. M.
- [ ] **Batch quality-polish**: drive `ebook-polish` (smarten, unused
  CSS, image compression, font subset/embed, jacket, kepubify);
  cquarry id sets + post-verify. M.
- [ ] **Cover remediation verb**: drives cquarry `set_cover` (its
  promotion candidate); closes the coverless/low-res audit loop. M.
- [ ] **Save-to-disk bulk export**: drive `calibredb export --template`
  per cquarry-resolved id sets. S.
- [ ] **Duplicate-record merge verb**: compose add_format file
  placement + remove_book; detection exists. M.
- [ ] **Headless OPF-queue flush**: drive `calibredb
  embed_metadata`/`backup_metadata` for dirtied ids (chunked embed
  machinery exists in reconcile). S.
- [ ] **Metadata-source backfill**: drive fetch-ebook-metadata for a
  search set into OPF, then cquarry writes (pattern proven in run.py).
  S-M.
- [ ] **check_library subprocess parity** if A.3's native form is not
  chosen. S.

Ship shape: A.2 (`--restrict`) first — it multiplies every other mode.
Then A.1, B.1-B.8 (one audit class per commit, each with its fixture),
then C. Every audit class ships with a fixture and a false-positive
note; every integration ships with a dry-run before any write.
