# CalibreQuarry — Roadmap

What's done, what's next. Updated as of v3.3.1.

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

## Maintenance (workspace sweep, 2026-06-09)
*Small behaviour-neutral pass; everything else was clean (42 tests green, default ruff rule set clean).*

- [ ] **5x B904, `raise ... from` missing inside `except` clauses**: `helpers.py:207`, `search.py:301`, `search.py:327`, `search.py:393`, `search.py:433`. Re-raising without `from err` (or `from None`) loses the causal traceback chain; fixing it improves debugging of bad search queries.
- [ ] Minor: 2x B007 unused loop variables, 1x B009 `getattr` with constant attribute.
- **Do not "fix" as bugs: the 17x B023 hits in `tui.py` (lines 691-842) are false positives.** Every flagged lambda is passed to `_run_with_capture(...)`, which invokes it immediately within the same loop iteration, so the late-binding capture never bites. If the lint should be quiet, bind defaults (`lambda output=output: ...`); purely cosmetic.

## Port from the Lattice TUI audit (2026-07-01)
*`cquarry/tui.py` shares its curses skeleton with Lattice's `tui.py`. The 2026-07-01 Lattice audit (Lattice `roadmap.md`, section "Audit 2026-07-01", items H6/H7/T2/T4 plus the v4.8.1 fallback-menu fix) found bugs in that shared skeleton; the ones below carry over and should be ported when the Lattice fixes land, keeping the two TUIs' behavior aligned. All are bug fixes, so they fit the "complete, bug fixes only" contract. Line numbers are as of v3.3.1. What does NOT carry over: Lattice H6's terminal-corruption half (cquarry has no `IN_TUI`/`_TUIPbar` progress machinery and never calls `initscr()` outside `curses.wrapper`), Lattice T1 (multi-root), and Lattice T3: cquarry's change-database flow already validates before persisting (`tui.py:722-734`) and its first-run resolver re-prompts on bad paths (`tui.py:680-693`); that pattern is the model Lattice's T3 fix copies, not the other way around.*

- [ ] **Port Lattice H7: curses init failure in `_tui_select` reads as Quit; TUI silently exits 0 on capability-poor terminals.** Identical shape to Lattice: unguarded `curses.curs_set(0)` (`tui.py:173`), `except curses.error: return None` around `curses.wrapper` (`tui.py:189-192`), and `interactive_menu` treats `None` as Quit (`tui.py:718`). On `TERM=vt100` or a dumb terminal the menu "opens" and the program instantly exits 0 despite the working `_box_menu` text fallback. **Fix (same as Lattice):** wrap `curs_set(0)` and the color init in individual `try/except curses.error: pass` (cosmetic failures must not kill the widget); catch `curses.error` from `curses.wrapper` itself, flip the module-level `_USE_CURSES` to False, and return a sentinel the menu loop re-enters on, so the next iteration renders the text fallback. **Test:** monkeypatch `curses.wrapper` to raise `curses.error`; assert `interactive_menu` falls back to the text menu instead of returning 0.
- [ ] **Port Lattice H6 (exception-boundary half only): `_run_with_capture` runs modes bare.** `tui.py:438-441` calls `func(...)` with no try/except, so a mode exception escapes as a traceback and the captured output is lost. No terminal corruption here (no stray `initscr()`), so this is the crash-and-lose-results half only. **Fix:** wrap the call; `except Exception` pages `traceback.format_exc()` under an `[Error]` title through the existing `_tui_scroll_text`/print paths; `except KeyboardInterrupt` pages a "[Cancelled]" notice plus whatever output was captured. **Test:** `_run_with_capture` with a raising func pages the traceback instead of propagating (monkeypatch the pager to record).
- [ ] **Port Lattice T2: Esc in a prompt accepts the default instead of cancelling.** Same semantics as Lattice (`tui.py:201` docstring "Esc returns the default", hint at `tui.py:249`), while Esc in menus means back/quit. Stakes are lower here (every mode is a read-only report; an accidental confirm just runs one), but the two TUIs should agree once Lattice flips Esc to a cancel sentinel. **Fix:** mirror Lattice's change: `_tui_prompt_str` returns None on Esc, `_prompt_str` propagates it, prompt chains abort back to the menu, bare Enter keeps meaning "accept default", hint bar becomes "Enter Accept · Esc Cancel". Land together with (or immediately after) the Lattice change, and note the behavior change in the patchnotes.
- [ ] **Port Lattice T4: output-file prompts never expand `~`.** Outputs go through plain `_prompt_str` (`tui.py:752`, `:799`, `:840`; audit the other `"Output file"` prompts in the dispatch block while there), so `~/reports/x.txt` creates a literal `./~/` directory. The DB-path prompts already expand (`_prompt_path`, `tui.py:527`; explicit `expanduser` at `:682`, `:723`); only the output prompts are exposed. **Fix:** run `os.path.expanduser` on every output path the TUI collects (one small `_prompt_out()` wrapper used at all output sites; do not abspath, so relative paths keep meaning CWD). Echo the resolved absolute path in the confirmation output so "where did my report go" answers itself.
- [ ] **Adopt Lattice's v4.8.1 fallback-menu generation (preventive; currently in sync).** The no-curses menu and its key map are hand-maintained (`_MAIN_FALLBACK_MAP` at `tui.py:578-614`, the hardcoded listing in `_select_main` at `tui.py:620-656`) while the curses menu renders from `_MAIN_SECTIONS` (`tui.py:534`). That is exactly the pattern that silently desynced in Lattice (its FOUND BUG 2026-06-10: fallback keys dispatching the wrong modes, newer modes unreachable) and was fixed by generating both the fallback listing and key map from the same sections the arrow-key menu uses (`_build_fallback` in Lattice `tui.py`), pinned by a test. Verified in sync today (keys 1-14/s/q all match the section tuples), but every future mode addition re-rolls the dice. **Fix:** port `_build_fallback`: derive the numbered listing and the key map from `_MAIN_SECTIONS`, keep the word aliases ("catalog", "stats", ...) as an explicit supplemental dict, and delete the hand-written listing. **Test:** a `tests/` case asserting the generated map's targets equal the section tuple space and that every section item is reachable, mirroring Lattice's `test_tui.py` pin.
