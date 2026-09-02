# CLAUDE.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

## Programmer-facing contract notes (cquarry >= 1.7)
- `db.get_all_books()` rows expose `authors`, `tags`, `languages`, and `formats` as native `list[str]`. Never `.split(",")` them; comma-containing author/tag names are preserved by the link-table hydration. `normalize_author_display()` accepts both the legacy joined string and the list form.
- Every book row also carries `size` (total `data.uncompressed_size` bytes, may be None) and, since cquarry >= 1.3/1.4, `pages` (native `books_pages_link`), `author_sorts`, and `author_links`.
- `search()` raises `ParseException` for unknown virtual libraries or saved searches; only `resolve_vl()` / `resolve_saved_search()` raise `ValueError` (with an available-names message).
- Raw comments payloads are HTML; run them through `cquarry.helpers.strip_html()` before terminal output.
- **Write verbs** (`--set-*`, `--add-tag`, `--remove-tag`, `--clear-*`, `--remove-book`) are opt-in and funnel through `run_write()` in `src/cquarry_cli/writeops.py`, dispatched by `cli.py` for flags and called directly by `tui.py` for menu flows; it owns the WritableCalibreDB lifecycle and the error-to-exit-code mapping (argument problems exit 2, lock/write errors exit 1). Read modes never import `cquarry.write` or `writeops`; keep it that way.
- **Dependency policy.** `cquarry` and `vir-tui` are tracked at `@main` and must never be pinned to a tag or commit, and `uv.lock` stays out of the repo: installs always pull the latest.

## Programmer-facing contract notes (3.24.0 onward)

- **Detail/audit/analytics modes render; cquarry derives.** `--book` is a renderer over cquarry 1.8's `get_book_dossier()` (batch forms compose it in a loop; `--book --untagged` sources ids from `cquarry.integrity.find_untagged`); `--audit`'s per-book predicates come from `cquarry.integrity`; `--analytics`/`--stats` consume `cquarry.analytics`. Do not re-derive a predicate or a stat inline in this repo: promote it to cquarry (the frontend-only split, now enforced by usage). The one deliberate exception: `--audit`'s duplicate grouping stays inline because the CSV joins ids in scan order and `find_duplicate_books()` sorts numerically.

## Hard constraints
- **Frontend Only.** The core database logic and search evaluation are delegated to the external `cquarry` shared library. Do not add database reads or search parsing logic here; contribute them to `cquarry` instead.
- **Minimal Dependencies.** Only `cquarry`, `vir-tui`, and `tqdm`. No `calibredb` required.
- **Immersive Output.** All commands that dump extensive output must be wrapped in `_run_with_capture()` so they display in the curses pager, unless redirected.

## Layout
- `src/cquarry_cli/cli.py` & `tui.py`: Core CLI arguments and the Curses UI menu.
- `src/cquarry_cli/modes/`: Implementations for each CLI flag (`catalog.py`, `export.py`, `stats.py`, `detail.py`, `info.py`, etc).
- `src/cquarry_cli/writeops.py`: Shared write-verb plumbing: `run_write()` owns the WritableCalibreDB lifecycle; `dispatch_write()` maps CLI flags onto per-verb executors the TUI also calls.
- `tests/`: End-to-end integration tests using `cquarry_cli` directly against the database (the unit tests for `cquarry.db` and `cquarry.search` were moved to the `cquarry` library).

> **Important:** The core database logic (`db.py`, `search.py`, `helpers.py`, `config.py`) was extracted into the `cquarry` shared library. Additionally, the generic UI formatting and Curses menu primitives have been extracted to the `vir-tui` shared repository. CalibreQuarry relies on both of these external dependencies. (`db.py`, `search.py`, `helpers.py`, `config.py`) was extracted into the `cquarry` shared library package. CalibreQuarry relies on this external dependency.

## Conventions
- Single source of truth for version is `src/cquarry_cli/__init__.py`.
- Run tests with `./run_tests.sh`. Test the CLI, not just the functions.
