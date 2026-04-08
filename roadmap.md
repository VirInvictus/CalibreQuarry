# getBooks — Roadmap

What's done, what's next. Updated as of v1.0.3.

---

## Done

- [x] Text catalog grouped by author with ratings and series info
- [x] All-wings batch catalog generation (one file per virtual library)
- [x] Library statistics (formats, ratings, tag taxonomy, publishers)
- [x] Audit mode (untagged, unrated, coverless, series gaps)
- [x] Recent additions display (`--recent N`)
- [x] Series listing with completeness status and gap detection
- [x] Full library export to JSON or CSV
- [x] Virtual library listing with book counts
- [x] Virtual library search expression parser (tags, vl, boolean, parens)
- [x] Hierarchical tag matching (Calibre convention)
- [x] `--show-tags` modifier for tag display in catalogs
- [x] `--show-id` modifier for Calibre ID output (scripting)
- [x] `--primary-only` modifier for single-author display
- [x] `--quiet` modifier for minimal output
- [x] Auto-detection of `metadata.db` location
- [x] Read-only database access (`?mode=ro`)
- [x] Interactive menu (no-argument launch)
- [x] `--version` flag
- [x] Cached `get_all_books()` for performance in batch modes

---

## Future

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
t** — identify books only available in deprecated formats (MOBI, LIT)
- [ ] **Color CLI output** — ANSI color for terminal output in non-interactive mode
