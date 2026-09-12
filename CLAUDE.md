# CLAUDE.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

## Programmer-facing contract notes (3.37.0 onward, Phase 19 A)

- **`--restrict` is a scoping view, not per-mode filters.**
  `src/cquarry_cli/restrict.py` holds `RestrictedView`, a `CalibreDB`
  subclass built over an already-open connection (`__dict__` copy) whose
  collection methods filter to the resolved id set. Analytics,
  integrity predicates, and the series rollup run unchanged over the
  view, so cquarry still derives everything; the frontend only scopes
  inputs. The two SQL-level aggregations (`get_entities`,
  `get_format_stats`) are recounted from the scoped rows and merged
  with the real rows' secondary columns; `get_all_tags` derives from
  scoped rows (distinct on-book tags, a different semantic than the
  global tags table). `view.origin` is the unrestricted database: the
  tree audit's orphan classes need it because orphans are library
  shape, not restriction shape. The refusal gate (`restrict_refusal`)
  checks an explicit write-flag dest tuple that must keep step with
  `build_parser()`'s write/set groups; new write verbs must be added
  there. Resolution happens once in `cli.main` (parse failure exits 1,
  matching `--search`); write verbs and `--book`/`--id` refuse the
  combination (exit 2).
- **`--fts` route decision (recorded).** Content search goes through
  cquarry's `search_book_text`; coverage flows through cquarry's
  `get_text_extractions`; the sidecar's `dirtied_formats` queue is the
  one table cquarry 1.20 does not expose, so `modes/fts.py` reads it
  directly with a `db_uri_ro` connection, confined to that module.
  Promoting a staleness predicate to cquarry remains a future option.
  `TEXT_INDEX_FORMATS` is deliberately conservative (a format outside
  it is never "never indexed"); the real library has never built the
  sidecar, so the degrade path is the common case here.
- **The tree audit is CQ-native by decision.** `modes/treeaudit.py`
  walks the library tree read-only (upstream check_library's CHECKS is
  the class checklist, not a subprocess to run; this package carries no
  calibredb). Whitelists: `metadata.opf`, any `*.opf` (legacy
  per-book metadata), `cover.jpg/jpeg/png` (extra only when
  `has_cover` is false), `data/`; name comparison is case-insensitive;
  the known-extension set mirrors upstream `BOOK_EXTENSIONS`. Tolerate
  `failed_folder` rows in unreadable dirs instead of letting the walk
  die.
- **`--analytics reading` is read-only by charter** (the
  NON-NEGOTIABLES `#reading_status` ban is untouched; an mtime-pinned
  test proves it). The funnel renders in the column's configured enum
  order. NOTE for searching enum columns: the exact form
  `#reading_status:=Read` is correct; the contains form
  (`#reading_status:Read`, quoted or not) currently matches the whole
  library over normalized enum columns. That is a cquarry search
  engine issue (found 2026-09-12, recorded here and in the audit
  sheet); fixing it belongs upstream, not in this frontend.
- **`@Name` user-category resolution was skipped by its own gate:**
  the real library's preferences carry zero user categories (read-only
  peek, 2026-09-12). If categories ever appear, the right home is the
  cquarry search engine, not this frontend.

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

### Programmer-facing contract notes (3.33.0 onward)

- **The single-verb path batches.** `run_write` wraps every action in
  `wdb.batch()`, so an interrupt mid-verb cannot commit a torn edit (the
  guarantee used to differ between single and batched paths).
- **`--commit-per-book` is real.** Each book is its own outermost batch;
  a failing book rolls back alone, its result entries read
  `status: "rolled_back"` with `book_committed: false`, and the pass
  continues. Exit 0 only when every book committed.
- **Empty-string flag values are refused (exit 2)** by
  `setwrite._reject_empty`; `_has_verbs` counts value-bearing flags by
  presence (never truthiness), so the refusal message is what the user
  sees. The store_true `--batch-clear-*` flags still count by truthiness.
- **`writeops.FORBIDDEN_COLUMNS` is the one banned-columns tuple.** The
  builders (`action_set_column`/`action_clear_column`/
  `action_add_column_value`) refuse it at builder time; set mode imports
  the same tuple; run.py's answer-file gate checks it too.
- **The `--batch-clear-rating` manifest gate validates.** The
  `--from-manifest` file must load through `cquarry_cli.manifest`
  (structure + seal) and the targets are exactly its imported ids; a
  plain id file is refused.
- **Read-only means read-only for dry runs.** `--remove-book`'s dry run
  (CLI and TUI) goes through a `mode=ro` connection, never
  WritableCalibreDB. Set-write backups are timestamped, so a second run
  never destroys the first restore point.
- **Run-verb rails:** phase 1's quarantine moves only audit_drm's
  `is_problem` set (DRM, ERROR) and only under `--quarantine`; the
  PDF battery runs the recursive inventory and honors check_pdf's exit
  codes; phase 2 saves the resume record before the download segment,
  takes timestamped sqlite-API backups, defaults `#audience` to
  `DEFAULT_AUDIENCE` when the flag is absent, and defers downloads if
  Calibre opens after the commit (no calibredb anywhere: downloaded OPFs
  apply via `run._apply_opf`); phase 3 enforces the closed-Calibre guard
  and the banned answer-file fields, and mechanical-pass trouble
  (bindery/reconcile rc 2) fails the verb and lands in the batch record.

### Programmer-facing contract notes (3.35.0 onward)

- **The filename-stamp convention is "Author - Title" (decided, not
  open).** `_FILENAME_STAMP` is the seed parser and `_drive_stamp` the
  writer; the observed corpus (libgen.li names) confirms the direction.
  Calibre's filename fallback reads the OPPOSITE order and stamp_pdf's
  `_derive_from_filename` preview deliberately mirrors Calibre (it
  previews what an unstamped import would guess); do not "align" either
  to run.py's reading.
- **`run phase1` seeds `provenance`.** `run._provenance_from_filename`
  maps the filename's site markers onto the #source vocabulary
  (Anna's Archive trailer -> "Anna's Archive"; z-library.sk/1lib.sk ->
  "Other", pending Brandon's Z-Library enum decision; libgen.* ->
  "Library Genesis"; no marker -> None). The value is sealed:
  `manifest._seal_payload` binds provenance alongside stamps and lossy
  flags, so a post-sign provenance edit fails every load until
  re-signing. Phase 2's cc6 stamping is unchanged (it always consumed
  `entry["provenance"]`; it was the phase-1 half that was dead).
- **check_pdf's qpdf class reads the exit code alone** (qpdf's
  documented contract: 0 clean, 3 warnings, 2 errors). qpdf writes
  warnings to stderr; never reintroduce a stdout marker gate, and never
  count `qpdf_warnings` findings in the structural total.
- **`--audit` renders conversion overrides (3.36.0).** The
  `conversion_override` rows and the summary block consume cquarry's
  `get_conversion_profiles` (the predicate's library home; never
  re-derive it inline), while `scripts/audit_conversion_overrides.py`
  remains the standalone, pipeable form (exit 1 on findings). The mode
  keeps `--audit`'s exit-0 reporting contract.

### Programmer-facing contract notes (3.32.0 onward)

- **The manifest signature is an HMAC seal, not a boolean.** `manifest.sign()`
  seals the approved set, the per-file stamps, provenance, and lossy
  flags, and the decisions list (HMAC-SHA256 over canonical JSON; the key
  is a schema
  constant, so the seal is tamper-EVIDENCE, not secret authentication).
  `validate()` checks the seal by default; `check_seal=False` exists for
  exactly one caller, the `run sign` verb, so a deliberate post-sign edit
  can be re-approved instead of dead-ending. `save()` re-seals signed
  manifests (the writer owns its state; phase 2's appends stay verifiable
  for phase 3). `approve()` refuses any file whose verdict is not
  `approved_for_import`. Do not hand-set `signed`; sign via
  `cquarry run sign --manifest FILE`.
- **All read-mode file output goes through `src/cquarry_cli/output.py`.**
  `open_output()` refuses the database path and its `-wal`/`-shm`/`-journal`
  sidecars and stages through temp + `os.replace`;
  `ensure_output_dir()` additionally refuses the library root for directory
  exporters (exportlt sweeps stale csv files, and none of that may land
  inside the library). Never `open(path, "w")` a user-supplied output path
  in a mode; the refusal surfaces as `OutputRefusedError`, mapped to exit 2
  in `cli.main()`.
- **Run-verb seam contracts are pinned by tests.** `_screen_duplicates`
  returns the set of hit paths: screen_duplicate's JSON report is a bare
  list holding every screened file, and only records with `library_hits` or
  `batch_duplicates` count; run.py pre-filters the inventory with
  `_SCREEN_EXTS` (keep in step with screen_duplicate's `EBOOK_EXTENSIONS`).
  `_bindery_phase1` passes `--json FILE` (a temp report read back) and
  honors bindery's exit contract (0 clean, 2 trouble found with report, 1
  invocation problem without; anything else, or a missing report on 0/2, is
  a hard error). If an instrument's shape changes, its seam test fails
  first; fix the seam and the test together.
- **The TUI probe-opens the database every menu iteration** (`_db_opens`,
  which relies on CalibreDB's constructor running `SELECT 1 FROM books`).
  Change Database requires a real open before `set_db_path`, and a
  `sqlite3.Error` mid-session degrades to the re-prompt. Keep the
  construction inside those boundaries.

## Hard constraints
- **Frontend Only.** The core database logic and search evaluation are delegated to the external `cquarry` shared library. Do not add database reads or search parsing logic here; contribute them to `cquarry` instead.
- **Minimal Dependencies.** Only `cquarry`, `vir-tui`, and `tqdm`. No `calibredb` required.
- **Immersive Output.** All commands that dump extensive output must be wrapped in `_run_with_capture()` so they display in the curses pager, unless redirected.

## Layout
- `src/cquarry_cli/cli.py` & `tui.py`: Core CLI arguments and the Curses UI menu.
- `src/cquarry_cli/modes/`: Implementations for each CLI flag (`catalog.py`, `export.py`, `stats.py`, `detail.py`, `info.py`, etc).
- `src/cquarry_cli/writeops.py`: Shared write-verb plumbing: `run_write()` owns the WritableCalibreDB lifecycle; `dispatch_write()` maps CLI flags onto per-verb executors the TUI also calls.
- `src/cquarry_cli/run.py`: The acquisition run verbs (Phase 17): `cquarry run phase1/sign/phase2/phase3` orchestrate the manifest (`manifest.py`, `acquisition-manifest/1`), the companion `scripts/` tools, bindery's run slices, and cquarry 1.14's `add_book` into the three-phase pathway. Phase 1 is read-only against `metadata.db`; `run sign` seals the reviewed manifest (structure checks only, so re-signing after a deliberate edit works); phase 2 requires a SIGNED, SEALED manifest, no blocking decisions, Calibre closed, and `--backup-dir`, then commits as ONE `batch()`; `metadata_download` decisions are phase 2's own product and never block a resume, every other kind blocks; phase 3 writes only what its answer sources decided, then re-validates to 0 errors or exits 1.
- `src/cquarry_cli/setwrite.py`: Set-oriented writes (Phase 16): target-set resolution, `--batch-*` verbs over the writeops action builders, the dry-run/apply lifecycle, and the set-mode report (text + `--format json`). Write-path code; read modes never import it.
- `src/cquarry_cli/output.py`: The read surface's output guard (`open_output`, `ensure_output_dir`, `OutputRefusedError`). Read-path code; see the 3.32.0 contract notes.
- `tests/`: End-to-end integration tests using `cquarry_cli` directly against the database (the unit tests for `cquarry.db` and `cquarry.search` were moved to the `cquarry` library).

> **Important:** The core database logic (`db.py`, `search.py`, `helpers.py`, `config.py`) was extracted into the `cquarry` shared library, and the generic UI formatting and curses menu primitives were extracted to the `vir-tui` shared repository. CalibreQuarry is a frontend over both; do not re-derive database logic inline that the library provides.

## Conventions
- Single source of truth for version is `src/cquarry_cli/__init__.py`; `tests/test_version.py` pins it equal to the root `VERSION` file, `pyproject.toml`, and the newest `patchnotes.md` heading.
- Run tests with `./run_tests.sh`. Test the CLI, not just the functions.
