# CalibreQuarry — Roadmap

What's done, what's next. Updated as of v3.12.0.

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

- [ ] **`scripts/screen_duplicate.py` — loose-file vs library duplicate screen
      (read-only).** Phase 1 § 3 screens every download against the library AND
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
- [ ] **`scripts/stamp_pdf.py` — pre-stamp PDF metadata (writes FILES, never the
      DB).** Phase 1 § 6 pre-stamps bare-metadata PDFs (TTRPG modules, scans, indie
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
- [ ] **Version/docs re-sync.** *State as of 2026-08-27 (post 3.21.0 work):* the
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
- [ ] **Skill sync**: phase-1-import (Brandon's library,
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

- [ ] **`--book` batch forms**: accept comma-separated ids (`--book
  8884,8885,8886`) and an `--book --untagged` selector (the phase-3 entry state
  is "all untagged books"). Curating a batch today means a hand-rolled
  `get_book()` loop; the dossier renderer (`modes/detail.py show_book`) is
  already per-book and composes in a loop unchanged.
- [ ] **`show_book`: print `pubdate`.** The dossier prints added/modified dates
  but not the publication date — a field phase 3 explicitly checks (Jan-01
  placeholder dates are one of its standard catches). One-line fix in
  `modes/detail.py`.
- [ ] **`--set-pubdate ID DATE` write verb** once cquarry ships `set_pubdate`
  (cquarry roadmap Phase 8): store the canonical TEXT form
  (`'YYYY-MM-DD 00:00:00+00:00'`). The 2026-08-27 batch's raw-integer pubdate
  writes tripped 8 linter errors (sentinel + unparseable) before being caught.
- [ ] **`scripts/fetch_library_codes.py` Calibre-detection guard**: its
  "Calibre is running" refusal matches concurrent process ARGS, so a parallel
  Bindery sweep whose command line contains "Calibre Library" false-positives
  it (bit twice in one batch, 2026-08-27). Detect the GUI by exact process name
  (`pgrep -x calibre`) or by attempting the DB write lock — not `pgrep -f` over
  the whole process table.
- [ ] **Skill sync**: the phase-3-import skill in Brandon's library
  (`~/docs/Calibre Library/.claude/skills/`) should name the `--book` batch
  form in its "read EVERY field" step, and soften its LoC-sequencing warning
  once the guard fix above stops the false positives. **This item is a floor,
  not a ceiling**: any behavior-affecting discovery made while building — a
  flag that landed differently, a default that changed, a new failure mode the
  tests surfaced — gets documented in the affected skills in the same release,
  even when this phase didn't predict it.
- [ ] **`fetch_library_codes.py` misses worklist**: when the hit rate is under
  100%, emit a worklist file (id, title, identifiers) of the misses so the
  skill's mandatory manual-research pass starts from a file instead of terminal
  scrollback (2026-08-27: 3 misses were tracked by hand).

- [ ] **Consume cquarry Phase 9's `get_book_dossier()`** once it lands: `show_book`
  becomes a thin renderer over the composed dossier dict instead of hand-calling
  ten read APIs (cquarry roadmap Phase 9 is the mine this comes from).

Non-goals: no `--get-id` alias (the verb is `--book` and it shipped in 3.22.0);
no new read APIs (they belong to cquarry per the frontend-only split).
