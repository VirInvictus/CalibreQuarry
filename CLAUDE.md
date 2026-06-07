# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CalibreQuarry (`cquarry`) — a CLI toolkit that reads Calibre's `metadata.db` directly. Pure Python stdlib, zero runtime dependencies. Considered complete software; the bar for new code is "match what's there."

Authoritative project docs: `README.md`, `spec.md`, `roadmap.md`, `patchnotes.md`. `spec.md` is the contract — read it before changing semantics.

## Hard invariants

- **Stdlib only.** No `requirements.txt`, no `pip install <thirdparty>`. Allowed: `sqlite3`, `json`, `csv`, `argparse`, `curses`, `os`, `sys`, `shutil`, `tempfile`, `struct`, `re`, `unicodedata`, `datetime`, `typing`, `subprocess`, `io`, `contextlib`. If a task seems to require a third-party library, stop and ask. (The `scripts/` companion tools are also stdlib-only, but may shell out to external binaries like Ghostscript.)
- **Read-only DB access.** `CalibreDB` opens `metadata.db` with `?mode=ro` URI. Never add `INSERT`, `UPDATE`, `DELETE`, `CREATE`, or `ATTACH` — and never widen `mode=ro` to anything else. (This applies to the `cquarry` package only; `scripts/compress_pdf.py` deliberately writes, which is exactly why it lives outside the package.)
- **Calibre parity for search.** The search engine lives in `search.py` (`_Parser` for the grammar; `SearchEngine` for candidate-set evaluation and the per-datatype matchers). `CalibreDB.search`/`resolve_vl` delegate to it and `CalibreDB` is its `MetadataProvider`. It targets faithful-but-stdlib parity with Calibre; the deliberate deviations (stdlib `re` not `regex`, `unicodedata` not ICU, no templates/saved-searches, anchored `tags:`) are documented in `spec.md` §2.3 and the README. If you change the engine, run `tests/test_search.py` and `./test_queries.sh` against a real library.
- **Hierarchical tag matching.** `tags:Fic.Fantasy` matches `Fic.Fantasy` AND any tag prefixed `Fic.Fantasy.`. The `=` prefix (`tags:"=Fic"`) opts into exact match. This is cquarry's anchored default (`_match_hier` in `search.py`), intentionally narrower than Calibre's raw substring. Don't "simplify" this away.
- **Locked-DB snapshot.** If Calibre holds the DB lock, `CalibreDB._open` copies `metadata.db` (+ `-wal`, `-shm`) to a tempfile and reads from there; the temp files are cleaned in `close()`. Preserve this behavior.
- **Calibre rating scale.** Stored 0–10 internally (10 = 5 stars). Convert via `calibre_rating_to_stars` in `helpers.py` for any 0–5 display. Don't divide by 2 ad-hoc.

## Architecture

```
src/cquarry/
  cli.py        argparse → mode dispatch (single mutually-exclusive group of modes)
  __main__.py   `python -m cquarry` entry; wraps main() in finally→_reset_terminal
  tui.py        curses TUI + fallback text menu (run when argv is empty)
  db.py         CalibreDB: all SQL + caching; also the search MetadataProvider
  search.py     Calibre-parity search engine (_Parser grammar + SearchEngine)
  config.py     ~/.config/cquarry/config.json (db_path persistence), VERSION
  helpers.py    find_db(), ANSI color, JPEG/PNG sizing, rating/series helpers
  modes/        one file per mode; each exposes a top-level function called from cli.py
    catalog.py    write_catalog, write_all_wings
    stats.py      show_stats
    analytics.py  show_author_stats, show_pace_stats, show_tag_tree, show_wing_overlap
    audit.py      run_audit
    display.py    show_recent, show_series, show_wings
    export.py     run_export (json/csv/ai), run_search_export (file or stdout)
tests/
  test_search.py   unittest: parser AST, matcher (in-memory), DB integration (temp sqlite)
  test_helpers.py  unittest: rating/star, series gaps, JPEG/PNG sizing
scripts/            standalone tools OUTSIDE the package contract (run with python3)
  compress_pdf.py        write-capable PDF shrinker (Ghostscript); updates metadata.db
  audit_epub_content.py  read-only EPUB content/language auditor
  validate_metadata.py   read-only metadata.db integrity + optional taxonomy linter
  reconcile_file_metadata.py  DB-vs-embedded metadata diff; --apply embeds via calibredb (epub/mobi/azw3), exiftool (pdf), djvused (djvu)
run_tests.sh      smoke-runs every CLI mode against the live library at $DB_PATH
test_queries.sh   smoke-runs --search with representative queries
```

Big picture: `cli.py` is a thin dispatcher. `db.py` owns ALL SQL and the search engine (`search.py`) owns ALL expression parsing/matching; modes consume `db.get_all_books()`, `db.resolve_vl(name)`, `db.search(query)`, `db.get_virtual_libraries()`, etc., and never touch `self.conn` directly. `search.py` is DB-agnostic: it talks to a `MetadataProvider` (which `CalibreDB` implements via `all_ids`/`field`/`vl_expression`/`custom_locations`), so it is tested with an in-memory provider and no DB. Caches on `CalibreDB` (`_books_cache`, `_vl_cache`, `_all_ids_cache`, `_search_view`, custom-column caches) are populated lazily; if you add a new bulk query, cache it on the instance the same way.

Adding a new mode: drop a file in `src/cquarry/modes/`, add a flag to `cli.py`'s mutually-exclusive group, dispatch to it in `main()`, and (if it has a sensible interactive form) wire it into `tui.py`. Mirror the existing `(db, *, ..., quiet: bool = False)` signature.

## Common commands

```bash
# Install (creates the `cquarry` console script)
pip install .
# or, ephemerally
pipx install .

# Run without installing (Brandon's local pattern)
PYTHONPATH=src python -m cquarry --stats

# Unit tests (fast, no live library needed)
PYTHONPATH=src python -m unittest tests.test_search tests.test_helpers -v

# Run a single test method
PYTHONPATH=src python -m unittest tests.test_search.TestParser.test_grouping -v

# End-to-end smoke (every CLI mode; needs a real Calibre library)
./run_tests.sh

# Search-syntax smoke (representative queries through --search)
./test_queries.sh
```

Both `run_tests.sh` and `test_queries.sh` hardcode `DB_PATH="/home/bdkl/docs/Calibre Library/metadata.db"`. If the library moves, edit the variable; don't add CLI flags to the scripts.

## Versioning & releases

- Single source of truth: `VERSION` in `src/cquarry/config.py`. `pyproject.toml`'s `version` must match. Bumping a version means updating both.
- Each release gets an entry in `patchnotes.md` (newest at top — match the existing format).
- `roadmap.md` tracks completed/planned phases; tick boxes when shipping a roadmap item.

## Style notes specific to this repo

- Modern typing only (Python 3.14 floor): builtin generics (`list[str]`, `dict[int, Any]`) and `X | None`, never `typing.List`/`Optional`. Import from `typing` only what has no builtin form (`Any`, `Protocol`). Do **not** add `from __future__ import annotations` — PEP 649 deferred annotations are the default in 3.14, so it is unnecessary boilerplate.
- ANSI color via `helpers.color(text, code)` — it auto-disables when stdout isn't a TTY. Don't hand-roll `\033[` escapes elsewhere.
- Mode functions print user-facing status to stdout and errors to stderr; they don't raise for "no results" — they print and return.
- `--quiet` suppresses decorative output but not errors. Honor it in any new mode.
- `cli.py` returns int exit codes; `FileNotFoundError`/`PermissionError` → 1, `KeyboardInterrupt` → 130, unmatched args → 2.
