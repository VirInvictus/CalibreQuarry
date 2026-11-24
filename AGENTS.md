# AGENTS.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

**CLI surface contract:** this workspace's CLI machine-surface contract lives in `~/.gitrepos/CLAUDE.md` (Throughlines). The 2026-11-13 live audit's two named open follow-ups are closed as of 3.89.0: issue #57 (the compact overview wraps instead of truncating, one "also:" block, the exit legend on the surface) and issue #58 (`topic_details` in `--help json` carries the exit contracts and gate annotations; additive-only, `topics`/`groups` untouched).

## Standing contracts (the load-bearing digest)

The release-by-release contract notes live in `docs/contract-notes.md`
(3.93.x back to 3.24.0, verbatim, newest first). This section carries the
invariants that still bind; the archive carries the full narrative, the
recorded trades, and the issue-by-issue archaeology behind each line.

- **Frontend-only split.** cquarry owns the database, search, helpers, and
  config; vir-tui owns the curses/UI primitives. Never re-derive library
  behavior inline: promote it to cquarry. The one recorded exception is
  --audit's inline duplicate grouping (the CSV joins ids in scan order;
  cquarry's find_duplicate_books sorts numerically).
- **Version sync is test-guarded** (tests/test_version.py): VERSION ==
  pyproject == src/cquarry_cli/__init__.py == spec.md's Version header ==
  spec.md's cquarry floor == spec.md 2.1's floor sentence == roadmap.md's
  "Updated as of" stamp == the newest patchnotes.md heading. Bump all
  carriers in the same pass.
- **Dependency policy.** cquarry and vir-tui ride PyPI floors (bump a floor
  deliberately when adopting a library feature; relock uv.lock in the same
  pass), never git deps; uv.lock stays untracked; CI pre-installs cquarry
  from git @main so main is tested against the library's head.
- **The NON-NEGOTIABLES hold at every door** (single-book, set, and run
  verbs; plugins ride the same seams): `#reading_status`, `status`, and
  `date_read` are refused by label (writeops.FORBIDDEN_COLUMNS).
- **The write rails:** closed-Calibre guard (anchored `pgrep ^calibre`,
  fail-closed on probe trouble), mandatory out-of-tree timestamped backups,
  dry-run default, exit 0/1/2 (argparse misuse is 2 before validation), one
  `batch()` per pass, HMAC-sealed acquisition manifests.
- **Read/write separation.** Read modes never import `cquarry.write` or
  `writeops`; the read surface's output guard lives in output.py; set-mode
  and write plumbing live in setwrite.py/writeops.py.
- **The decode blanket.** Every text-mode subprocess call carries
  errors="replace"; tests/test_scripts.py's AST tripwire
  (test_every_text_spawn_carries_errors_replace) refuses any new seam
  without it. Bare-name subprocess imports are outside the walk's shape and
  against tree convention.
- **Engine facts (cquarry >= 1.7).** Book rows expose authors/tags/
  languages/formats as native lists (never .split(",")); search() raises
  ParseException for unknown virtual libraries/saved searches while
  resolve_vl()/resolve_saved_search() raise ValueError; raw comments
  payloads are HTML (strip_html before terminal output).
- **Tests.** ./run_tests.sh; test the CLI, not just functions; floor-pin
  tests are version-gated (skipped below the floor); sibling test imports
  load by path via importlib; after any verb wave, run the live-seam drill
  (real calibre binaries over a scratch calibredb library in a
  CALIBRE_CONFIG_DIRECTORY sandbox) for what mocks cannot cover.
- **Docs discipline (2026-11-24 role split).** README is user-facing;
  spec.md is the contract and must never be stale; roadmap.md is collapsed
  into its ledger every pass; patchnotes.md is the versioned record tied to
  the tags. New releases append contract notes at the TOP of this file;
  when the live notes stack past ~10 sections, fold everything older than
  the current minor era into docs/contract-notes.md (verbatim, order
  preserved). The README's help paste moved to docs/cli-reference.md (the
  regen duty is unchanged) and the companion-script reference to
  docs/companion-scripts.md.

## Programmer-facing contract notes (3.94.0 onward, the plugin system host)

- **The plugin family dispatches BEFORE find_db, and `plugin run` binds its
  own library.** cli.py's main() routes `plugin <cmd>` right after the run
  dispatch; add/list/install/remove/update/verify are library-free, and
  `plugin run` resolves `--db` itself (cquarry.helpers.find_db when the
  flag is absent). The `plugin run` subparser's `--db` carries
  `default=argparse.SUPPRESS` so it can never clobber the top-level --db
  value (the argparse subparser-default clobber class).
- **Three version gates, fail-closed, honest sources.** manifest.py's
  check_floors refuses an api_version above the host contract (3), a
  cquarry floor above the IMPORTED engine's version (the module's own
  `__version__` / config.VERSION; dist metadata last because it lags
  checkouts), and a calibrequarry floor above this package's own version.
  Every refusal names found, floor, and fix.
- **The approval gate is the payload hash.** Approvals.approves is true
  only when the ACTIVE ledger entry's payload sha256 equals the live
  payload's hash (harness.payload_hash: sorted rel paths + bytes;
  `__pycache__` and `*.pyc` excluded so a load cannot change the hash).
  install, update's write path, and `plugin run --apply` all consult it; no
  flag bypasses.
- **git sync is fetch + reset --hard FETCH_HEAD.** A bare fetch never
  touches the working tree, so update's hash compare would read a stale
  clone; registry clones hold no local work of their own, so gitops.sync
  hard-resets to FETCH_HEAD and returns the new HEAD. All git ops spawn the
  binary through the errors="replace" seam (the AST tripwire's blanket).
- **Plugin module loads are per-plugin unique.** harness.load_plugin uses
  spec_from_file_location under the name
  `cquarry_plugin_<plugin>_<module>` -- never find_spec, which consults
  sys.modules and would bind one plugin's load to another's file (the
  cross-test ENOENT class). Entry modules are single files resolved under
  the plugin dir.
- **The WriteGate carries the merge-field seams (3.95.3).**
  plan_set_authors/plan_add_tags/plan_set_languages/plan_set_rating/
  plan_set_pubdate/plan_set_publisher/plan_set_comments -- the
  fill/union half of a plugin merge. LANE RULE RE-PROVEN: the gates
  were written but left uncommitted when the dependent plugin tagged;
  commit the host BEFORE the dependent's tag.
- **The loader loads multi-file plugins (3.96.0).** The entry module
  gets submodule_search_locations = the plugin dir, so
  `from . import sibling` resolves under the unique per-plugin package
  name; single-file plugins unchanged, sys.modules protections intact.
- **The gate has two faces: plan and write (contract 3, 3.95.2).**
  `context.plan.X(...)` builds label/action pairs and is legal in dry
  runs (planning is label construction); `context.write.apply(...)`
  executes and demands the capability + a non-dry context. Plugins
  using `plan` declare api_version 3 / floor >=3.95.2.
- **The WriteGate plans action pairs too (3.95.1).** plan_add_format
  (the FILE copy stays the plugin's), plan_remove_format,
  plan_rename_entity, and plan_remove_book (confirm forced -- the
  approval + dry-run/apply layers ARE the confirmation). The scratch
  and canonical fixture schemas carry books_delete_trg; without it a
  removed book's data row strands.
- **`plugin run`'s tail is REMAINDER.** The verb tail passes verbatim
  (argparse never hands unknown option-likes to a `nargs="*"`); the
  host's `--apply`/`--library` precede the plugin NAME (3.94.2, the drill
  find). The library is the subparser's OWN `--library` flag, never the
  top-level `--db` re-declared with SUPPRESS: CI caught that clobbering
  the parent namespace value on Python 3.14; `args.library or args.db`
  is version-proof.
- **The config seam is contract 2.** PluginContext.load_config/
  save_config give each plugin an isolated config file under
  `CQUARRY_PLUGIN_HOME/config/<name>/` (atomic, unreadable-reads-empty
  with the path named); the plugin-API contract is 2, api_version 1
  plugins still welcome (3.95.0, the find_duplicates exemptions seam).
- **The WriteGate plans label pairs.** plan_identifier returns the
  `(label, action)` tuple run_write_batch iterates (3.94.1: the gate
  owns the label because the batch results print it; the bare-closure
  shape was never consumable and the port's suite caught it).
- **The scratch library is the verify fixture.** plugins/scratch.py builds
  the canonical schema (the tests/_fixtures shape, kept in step with it and
  documented as such; two seeded books) with no calibre binaries; verify
  rebuilds it every run.

## Decisions

Moved verbatim from roadmap.md's "Records kept" on 2026-11-24 (the role
split: decisions and constraints live in this file). roadmap.md keeps a
one-line pointer.

- **The plugin-lane rulings (Brandon, 2026-11-21):** the MIT host takes a plugin
  system as the next x.0's headline; the kiwidude68 calibre plugins port as GPL-3
  plugins in their own monorepo (VirInvictus/cquarry-plugins), derived from upstream
  with attribution, one-way imports, no GPL bytes in the MIT repos; plugins declare
  capabilities, the engine runs a custom-written test run at install, and a writing
  plugin is added only on recorded approval; extract-isbn ships first; and the
  charter's "no book deletion in set mode" non-goal governs the core set verbs only
  (a plugin's write actions ride the plugin approval gate plus the standing rails
  instead), which is what puts the find-duplicates merge inside the lane's law.
- **The plugin-lane rulings, amended 2026-11-21 (same day, the CLI-registry
  refinement):** plugins are git-sourced: the host keeps a registry of sources and
  plugins (a small inspectable store beside the persistent config, not SQLite; git
  rides the binary through the standard spawn seam, the closed dependency set
  holds); `plugin add` auto-detects single-plugin and multi-plugin repos from the
  manifests it finds, never from configuration; `plugin update --all` is the
  update surface, with write-capable changes re-gated through the approval
  harness and newly-appearing plugins reported as available, never auto-installed;
  and the POCs are 1:1 parity ports: every crosswalk row ports, GUI idioms map to
  named CLI twins, row deletion and the saved-regex search joined scope behind the
  gates, and deviations are recorded, never silent.
- **The plugin-lane rulings, amended 2026-11-21 (the version-floor ruling):** a
  plugin manifest declares its minimum supported cquarry AND calibrequarry
  versions explicitly beside api_version, never inherited or defaulted; all
  three gates check fail-closed at install, update, and load naming found,
  floor, and fix (the floor_refusal idiom), the cquarry floor judged against
  the engine the process actually imported (the version_guard.py honest-source
  rule, never dist metadata), updates that raise floors past the running stack
  refusing that plugin alone with the upgrade path named, and plugin list
  rendering floor-blocked plugins as blocked-with-reason.

- **The "Author - Title" stamp convention** (decided 2026-09-10; cited by
  tests/test_run.py): the stamp parser reads "Author - Title", the direction the real
  corpus uses (libgen.li names, verified in the 09-10 Redwall run). Calibre's import
  fallback guesses the opposite on purpose; stamp_pdf's preview mirrors Calibre, this
  parser does not. A corpus regression test pins the direction.
- **Acquisition policy answers (Brandon, 2026-09-06).** Phase 16, all four: flag
  naming as specced; `--batch-clear-rating` manifest-only; `--backup-dir` mandatory;
  `--set-rating ID 0` remaps to a true clear. Phase 17, all six: `#source` stamped
  from manifest provenance; `#audience` unconditional `Brandon`; a signed phase-1
  report IS standing consent for `--apply-lossy`, scoped to the files the report
  listed; duplicate-refusal files are refused and flagged `decisions_needed` while
  the batch continues; failed/ambiguous metadata downloads push to phase 3 (phase 2
  stays non-interactive); manifests are KEPT in `.claude/manifests/` as the durable
  machine-readable record.
- **The lossy classification record** (cited by tests/test_run.py): bindery's
  gate-accepted repairs are classed lossy via five marker keys
  (`stripped_pagination`, `stripped_broken_tags`, `stripped_watermarks`,
  `dropped_marker`, `stub_docs_dropped`); `_mirror_lossy` records the class per
  repair and only lossy-marker repairs fire consent decisions (3.49.0; in practice
  superseded by bindery 0.45.0's structured `fixes` records, which 3.51.0 consumes
  with the 0.45.0 floor gate).
- **The lossy-pending in-manifest resolution** (cited by tests/test_run.py): the dry
  phase-1 emits `lossy_consent` per flagged file; the reviewer sets
  `"resolution": "apply"` and re-signs; phase 2 drives the strips itself, flips the
  lossy records, and consumes the decisions, all-or-nothing (3.48.0). The
  `--apply-lossy` re-run path remains as the alternative.
- **The screener classification record** (cited by tests/test_scripts.py):
  within-batch, same-normalized-title + same-first-author pairs classify via
  `classify_titles` (3.52.0): identical-after-scrub stays `duplicate_refused`;
  colon-boundary containment is the `related` advisory (exit 1, never a refusal,
  recorded as `checks.related_works`); differing real subtitles over one shared base
  are `distinct`; arabic volume pairs separate via the 3.25.0 signature gate, roman
  declarations via declared-annotation comparison, bare ordinals via the
  trailing-ordinal check; surviving same-base volume pairs surface as the
  informational `batch_volumes` advisory (never a refusal, never exit-1). Named
  shapes: Moral Letters vols 1-3, Monster Vault 2, Arcana Unleashed: Deadfall,
  Mothership: Wages of Sin, the Wandering Inn flood guard.
- **Route decisions:** the tree audit shipped CQ-native (3.37.0), so calibredb
  check_library subprocess parity was SKIPPED by decision; @Name user-category
  resolution was SKIPPED by its own gate (the library has zero user categories and
  one saved search; if categories ever appear, the right home is cquarry's engine,
  not this frontend); the pypi-environment deployment policy was probed and REVERTED
  (REST policies are branch-type only and reject tag deployments outright; the
  release-tags-protected ruleset covers tags; a UI-applied policy is the recorded
  reopen piece, see the open box).
- **Phase non-goals (charter):** no EPUB pre-stamping was OVERTURNED by Brandon's
  2026-09-22 ruling (`stamp_epub.py` shipped 3.53.0, mandatory for all filetypes);
  no in-`~/Downloads` backups or writes beyond the stamped file; no library-copy
  deletion on duplicate hits (duplicates route to recoverable trash; the deletion
  question stays Brandon's); no `--get-id` alias (the verb is `--book`); no new read
  APIs (standing rule); no book deletion in set mode; no set-mode comment/description
  writes; no implicit library-wide predicates; no single-verb behavior changes; no
  dependency pinning; no GUI automation; no Goodreads/Amazon API work (downloads ride
  Calibre's own source plugins); no taxonomy or genre decisions in phase 2; no bulk
  anything (every write manifest-scoped); no replacement of the manual path (the
  skills' command lists remain the fallback and the documentation of record).
- **Do not "fix" as bugs:** the B023 hits in `tui.py` are false positives; every
  flagged lambda is invoked within the same loop iteration by `_run_with_capture`,
  so late binding never bites. Bind defaults only if the lint should be quiet.
- **Deferred with dated notes:** the two CI skips (`/usr/share/dict/words`,
  ghostscript (host tools, not code)); the db_util consolidation (the private
  connect_ro copies have genuinely drifted: reconcile needs Row rows and its own tmp
  layout). The shared test `_SCHEMA` builder deferred here on 2026-09-29 SHIPPED
  in 3.60.1 (tests/_fixtures.py: one canonical schema over what had grown to 19
  drifted DDL copies, plus build_library()/seed_book()); the CI skips and the
  db_util consolidation stay deferred.
- **By-author's-call records:** the "X, not Y" prose rhythm stays by author's call;
  the pre-v3.37.0 GitHub Release cutoff is deliberately scoped, never decided.
- **Manager-pass records (2026-09-12):** `ids=` scoping on `get_entities`/analytics
  is a recorded future cquarry promotion candidate with no consumer yet; a drill
  plan-line trailing space was the one cosmetic finding.
- **Historical:** the audit_epub companions were extracted to bindery-cli on
  2026-08-21 (commit 7fce60c; they ship there as `bindery audit`); every Phase 18
  upstream finding closed in cquarry 1.15.0/1.17.0; the Phase 18 "leave Phase 17's
  boxes ticked" note is honored by this ledger (the P0s were correctness debt on top
  of shipped surface, all since fixed).
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

## Release flow
Commit and push verified work (the standing push grant in the global file; force-pushes still need approval), wait for CI to go green, then tag: annotated `vX.Y.Z` at the release commit, message = that release's `# N.N.N` patchnotes entry verbatim (`--cleanup=verbatim`; the full tag procedure lives in `~/.zcode/AGENTS.md`). The tag drives `publish.yml` (PyPI), and the `release-tags-protected` ruleset means a bad pushed tag needs the documented force path, so verify the tag message (`git cat-file tag`) before pushing. Version carriers are pinned by `tests/test_version.py` per the Conventions above.
