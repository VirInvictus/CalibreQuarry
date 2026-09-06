# CLAUDE.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

## Programmer-facing contract notes (cquarry >= 1.7)
- `db.get_all_books()` rows expose `authors`, `tags`, `languages`, and `formats` as native `list[str]`. Never `.split(",")` them; comma-containing author/tag names are preserved by the link-table hydration. `normalize_author_display()` accepts both the legacy joined string and the list form.
- Every book row also carries `size` (total `data.uncompressed_size` bytes, may be None) and, since cquarry >= 1.3/1.4, `pages` (native `books_pages_link`), `author_sorts`, and `author_links`.
- `search()` raises `ParseException` for unknown virtual libraries or saved searches; only `resolve_vl()` / `resolve_saved_search()` raise `ValueError` (with an available-names message).
- Raw comments payloads are HTML; run them through `cquarry.helpers.strip_html()` before terminal output.
- **Write verbs** (`--set-*`, `--add-tag`, `--remove-tag`, `--clear-*`, `--remove-book`) are opt-in and funnel through `run_write()` in `src/cquarry_cli/writeops.py`, dispatched by `cli.py` for flags and called directly by `tui.py` for menu flows; it owns the WritableCalibreDB lifecycle and the error-to-exit-code mapping (argument problems exit 2, lock/write errors exit 1). Action builders return `(exit_code, status)` tuples, status `"applied"` or `"already-so"` from cquarry's `changed` returns, and the batch summary reports real outcomes instead of a blanket ok. Read modes never import `cquarry.write` or `writeops`; keep it that way.
- **Dependency policy.** `cquarry` and `vir-tui` ride PyPI floors (see `pyproject.toml`; bump a floor deliberately when adopting new features of that library), never git deps, and `uv.lock` stays out of the repo so installs resolve the floors fresh. CI pre-installs cquarry from git `@main` so main is tested against the library's head.

## Programmer-facing contract notes (3.24.0 onward)

- **Detail/audit/analytics modes render; cquarry derives.** `--book` is a renderer over cquarry 1.8's `get_book_dossier()` (batch forms compose it in a loop; `--book --untagged` sources ids from `cquarry.integrity.find_untagged`); `--audit`'s per-book predicates come from `cquarry.integrity`; `--analytics`/`--stats` consume `cquarry.analytics`. Do not re-derive a predicate or a stat inline in this repo: promote it to cquarry (the frontend-only split, now enforced by usage). The one deliberate exception: `--audit`'s duplicate grouping stays inline because the CSV joins ids in scan order and `find_duplicate_books()` sorts numerically.
- **`--analytics genres` is a pure renderer over cquarry >= 1.12's `analytics.genre_distribution()`.** That function owns the rollup semantics (genre = first dot-path segment; a book counts once per node even when its tags share an ancestor; shares are fractions of the whole library, so multi-root books push the sum over 1.0; `"untagged"` last). The renderer slices to `--genre-depth N` (default 1 = roots only; deeper levels indent under their parents with the last path segment as the label) and does formatting only: %, bars, the sums-over-100% caveat. Every rendered level stays a share of the whole library, not of its parent.
- **Set mode (Phase 16, `src/cquarry_cli/setwrite.py`)**: one target source (`--ids`, `--from-search`, `--from-untagged`, `--from-manifest`; hand-supplied ids are validated read-only and unknown ids abort exit 2 before anything opens writable) feeds id-less `--batch-*` verbs. `dispatch_set_write` runs BEFORE `dispatch_write` in `cli.py` so a single-book/set combination is refused before anything executes. Dry-run by default; `--apply` demands a closed Calibre (`pgrep ^calibre` guard, the `fetch_library_codes.py` precedent) and a `--backup-dir` outside the library directory (the `stamp_pdf.py` precedent), then ONE `batch()` transaction; any per-(book, verb) failure rolls the whole pass back (exit 1, `committed: false`). `--batch-clear-rating` is manifest-only, mechanically enforced; column verbs refuse `#reading_status`/`status`/`date_read`; there is deliberately no `--batch-remove-book` and no set-mode rating SET. Verb actions reuse the writeops action builders quieted; new set verbs should do the same rather than opening connections inline.

## Hard constraints
- **Frontend Only.** The core database logic and search evaluation are delegated to the external `cquarry` shared library. Do not add database reads or search parsing logic here; contribute them to `cquarry` instead.
- **Minimal Dependencies.** Only `cquarry`, `vir-tui`, and `tqdm`. No `calibredb` required.
- **Immersive Output.** All commands that dump extensive output must be wrapped in `_run_with_capture()` so they display in the curses pager, unless redirected.

## Layout
- `src/cquarry_cli/cli.py` & `tui.py`: Core CLI arguments and the Curses UI menu.
- `src/cquarry_cli/modes/`: Implementations for each CLI flag (`catalog.py`, `export.py`, `stats.py`, `detail.py`, `info.py`, etc).
- `src/cquarry_cli/writeops.py`: Shared write-verb plumbing: `run_write()` owns the WritableCalibreDB lifecycle; `dispatch_write()` maps CLI flags onto per-verb executors the TUI also calls.
- `src/cquarry_cli/setwrite.py`: Set-oriented writes (Phase 16): target-set resolution, `--batch-*` verbs over the writeops action builders, the dry-run/apply lifecycle, and the set-mode report (text + `--format json`). Write-path code; read modes never import it.
- `tests/`: End-to-end integration tests using `cquarry_cli` directly against the database (the unit tests for `cquarry.db` and `cquarry.search` were moved to the `cquarry` library).

> **Important:** The core database logic (`db.py`, `search.py`, `helpers.py`, `config.py`) was extracted into the `cquarry` shared library, and the generic UI formatting and curses menu primitives were extracted to the `vir-tui` shared repository. CalibreQuarry is a frontend over both; do not re-derive database logic inline that the library provides.

## Conventions
- Single source of truth for version is `src/cquarry_cli/__init__.py`; `tests/test_version.py` pins it equal to the root `VERSION` file, `pyproject.toml`, and the newest `patchnotes.md` heading.
- Run tests with `./run_tests.sh`. Test the CLI, not just the functions.
