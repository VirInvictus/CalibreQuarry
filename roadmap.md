# CalibreQuarry — Roadmap

What's done, what's next. Updated as of v1.0.4.

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
- [ ] **TUI upgrades similar to /home/bdkl/.gitrepos/Lattice:** Check the patchnotes.md and look through the code in that repo to figure out how to improve the current TUI.
- [ ] **Move from script to full Python Project:** Split the script into sensible modules, set up a build and install setup, and make the repo layout more professional. Update the README to use the application over the script.

## Phase 4: Extended Capabilities (Future)
*Expanding on the analytics without altering the database.*

- [ ] **Search Query Export** — Run Calibre-style search expressions directly from the CLI to generate a text file of matching results. The tool will notify the user and avoid creating an empty file if the query yields no results.
- [ ] **AI-readable export** — token-efficient flat format for LLM recommendation prompts
- [ ] **Tag tree visualization** — display the full hierarchical tag taxonomy as a tree
- [ ] **Reading pace stats** — books added per month/year trend from `timestamp` column
- [ ] **Duplicate detection** — same title+author appearing in multiple formats or editions
- [ ] **Custom column support** — read user-defined Calibre columns for display and filtering
- [ ] **Cover quality audit** — flag books with covers below a resolution threshold
- [ ] **Author statistics** — per-author breakdowns (book count, ratings, formats, series)
- [ ] **Wing overlap analysis** — show which books appear in multiple virtual libraries
- [ ] **Format migration report** — identify books only available in deprecated formats (MOBI, LIT)
- [ ] **Color CLI output** — ANSI color for terminal output in non-interactive mode
