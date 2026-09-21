# CLAUDE.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

## Programmer-facing contract notes (3.50.0 onward, the calibre-touched PDF fix)

- **stamp_pdf's author_sort erase rides calibre's own writer.** exiftool
  cannot write the calibre XMP namespace (not in its tables; a
  user-defined `-config` table registers but never associates with the
  packet parse, so deletes find nothing). When the stamp sets authors
  and `exiftool -s3 -Author_sort` shows the property,
  `_erase_ebook_meta_args` builds a value-preserving `ebook-meta` pass
  whose `--author-sort ""` (empty is null, so calibre writes no sort of
  its own) makes calibre's PDF rewrite drop EVERY calibre-namespaced
  XMP element from the old packet; docinfo Keywords (the ISBN carrier)
  survive the rewrite. Detection reads the raw property, not the
  rendering, because an author NAME can legitimately contain a bracket.
  `_verify` remains strict equality: the file is cleaned, the comparison
  is not loosened. The calibre facts behind it, verified on 9.15:
  `create_book_entry` honors an embedded author_sort verbatim (the
  import-poison half), and `ebook-meta --authors` auto-computes a sort,
  which is why the null override is load-bearing.

## Programmer-facing contract notes (3.49.0 onward, the roadmap findings batch)

- **audit_drm's N/A rows reach the CSV.** The directory- and library-scan
  loops used to `continue` past N/A verdicts (DJVU) before the CSV
  append, so `_drm_verdicts` missed them and the manifest recorded
  `unscanned` for a format that was judged. N/A rows are now written
  (still excluded from the counters/summary), and the instrument test
  pins `"N/A"` in the verdicts dict. N/A remains no quarantine reason
  (only DRM/ERROR are).
- **Phase-1 stamps seed from embedded metadata first.**
  `_stamps_from_embedded` reads the `ebook-meta` display output
  (padded labels, `" : "` separator, authors joined `" & "`); the five
  fields title/authors/publisher/pubdate/language merge per-field over
  the filename parse, and ISBN is deliberately never seeded. A pubdate
  that fails `datetime.fromisoformat` is dropped (a bare year would fail
  cquarry's `add_book`); a read with `Traceback` on stderr returns no
  data (calibre exits 0 on unparseable files and prints its own
  reversed-order filename guess, which must never beat the decided
  "Author - Title" parse); `Unknown` placeholders are dropped; a missing
  binary degrades to filename-only with one warning; a per-file failure
  lands in the entry's `repairs`. `_drive_stamp` is unchanged.
- **The phase-2 `#source` stamp is verified in-batch.** A fresh book's
  first `set_custom_column` must return `changed=True`; `False` raises
  inside the one `batch()`, rolls the import back, and exits 1 with the
  library unwritten. This is the effect flag, not a read-back
  (WritableCalibreDB has no custom-column reader; a true read-back would
  need a cquarry API and is a recorded future option). The 2026-09-18/19
  "all Anna's Archive" observations were an out-of-verb door: both signed
  manifests carry zero `imported_id`s, and the DB showed the reviewed
  provenance landed on 77 of 79 books (the mismatches were the
  manifest-None files).
- **Only lossy-marker bindery repairs consent-gate.** `_mirror_lossy`
  records every gate-accepted repair with a `"lossy"` class judged by
  `_LOSSY_REPAIR_MARKERS` (the five fix keys bindery's own gate treats as
  lossy strips), and only those set `flagged`; structural-only files stop
  firing `lossy_consent` decisions. The decision detail carries the lossy
  summaries. Phase 2's consent re-drive flips and refuses over every
  recorded repair (bindery applies all of them), not just the flagged
  ones. The marker tuple is a string contract with bindery's summary
  renderer; the structured-fixes box on bindery-cli's roadmap is the
  long-term replacement.

## Programmer-facing contract notes (3.48.0 onward, the wave-2 refactor + consent batch)

- **The write-verb dest lists have one source: `dests.py`.**
  `SINGLE_BOOK_DESTS`, set mode's target sources (`SET_MODE_SOURCES`),
  and the `--batch-*` verbs (`BATCH_VALUE_DESTS` counted by presence,
  `BATCH_BOOL_DESTS` by truthiness) live there; `WRITE_FLAG_DESTS` is
  the restrict-refusal aggregate over all four. writeops re-exports the
  single-book list (setwrite's combination guard and the tests read it
  as `writeops.SINGLE_BOOK_DESTS`); tests/test_dests.py pins every
  member against build_parser(), so a dest that exists only in a list
  (or only in the parser) cannot rot.
- **One backup helper: `backups.make_backup`** (run phase 2, the
  integrate verbs, and set mode's `--apply` all call it). The recorded
  error-mapping decision: a `--backup-dir` inside the library is a
  USAGE problem (exit 2), so the shared helper raises the
  stdlib-neutral `ValueError` and each dispatcher keeps its own
  usage-path mapping; unwritable destinations and sqlite failures raise
  `ValueError` too (setwrite already wrapped them into its usage path;
  run/integrate previously propagated a traceback).
- **bindery's `manual_watermark_repair` decisions mirror into
  `decisions_needed` as `manual_repair`** (`_mirror_bindery_decisions`,
  the sibling of 3.43.0's `_mirror_lossy`): books bindery refuses to
  auto-strip used to vanish from the durable record entirely.
- **A dry phase 1 emits a `lossy_consent` decision per lossy-flagged
  file, and the resolution lives in the manifest.** The reviewer sets
  the decision's `"resolution"` to `"apply"` and re-signs; phase 2 then
  drives `bindery run phase1 --apply-lossy` itself (before the import
  batch opens), flips the lossy records to applied, and consumes the
  decisions. Consent is all-or-nothing: a partial resolution refuses
  before anything runs, a failed strip fails the verb with the library
  unwritten, and an unresolved lossy_consent still blocks like any open
  decision. The old path (re-run phase 1 with `--apply-lossy`) still
  works; it just no longer orphans the dry manifest.

## Programmer-facing contract notes (3.45.0 onward, the curation + trash batch)

- **The write verbs keep step with three new dests**: `--rename-entity
  KIND OLD NEW` (kinds: authors/series/publishers/tags, refused at
  builder time otherwise; a rename into an existing row MERGES and the
  moved-book count is the changed signal; a no-match old name is
  cquarry's ValueError -> exit 1) and `--set-author-sort` /
  `--set-title-sort BOOK SORT` (verbatim passthroughs). restrict's
  `_WRITE_FLAG_DESTS` and writeops' `SINGLE_BOOK_DESTS` both carry
  them; a test pins the restrict refusal so the list cannot rot.
- **The trash surface is two-sided.** `--trash` is a read mode over
  pure filesystem inventory (`modes/trash.py:collect_trash_entries`,
  upstream's `b/<id>/`+`f/<id>/` layout); it opens no write handle and
  is library-shape, so `--restrict` does not scope it (tree-audit
  precedent). `run trash --empty|--expire DAYS` owns the lifecycle
  through cquarry 1.20's verbs: dry-run listing by default, `--apply`
  executes, `--format json` carries `{plan, results}`. The apply half
  opens metadata.db writable to reach the upstream methods, so the
  closed-Calibre guard applies in dispatch_integrate; NO backup is
  required (the database never changes). `run trash` sits in
  `INTEGRATE_PHASES`, outside `verbs_need_targets` and `needs_backup`.

## Programmer-facing contract notes (3.44.0 onward, the truth-and-hardening batch)

- **The frontend tier has exactly one raw-SQL read left, the recorded
  one.** `_precedent_tags` delegates to cquarry 1.22's
  `CalibreDB.precedent_tags` (the 4-table JOIN lives upstream; results
  come back alphabetized), and `_remove_book_dry_run` reads through
  CalibreDB (`get_book` + `get_formats`). `modes/fts.py`'s
  dirtied-formats sidecar read remains the only recorded exception.
  Floor: cquarry >= 1.22.0.
- **The publish path is hardened** (SHA-pinned actions, top-level
  contents: read, concurrency no-cancel, pinned ruff + suite before
  build, twine --strict + wheel smoke, and a create-release job minting
  the Release from the tag): the same shape shipped in cquarry 1.22.0
  and vir-tui. The pypi-environment deployment-policy idea is RECORDED
  AS REVERTED: REST-created policies are branch-type only and reject
  tag deployments outright (cquarry's v1.22.0 publish proved it);
  tag policies are UI-only today.
- **The version-sync set is wider than the pin test used to check.**
  tests/test_version.py now also guards spec.md's `**Version:**`
  header, the spec Dependencies line's cquarry floor against
  pyproject's, and roadmap.md's `Updated as of` stamp. A release bump
  touches: `src/cquarry_cli/__init__.py`, `VERSION`, `pyproject.toml`,
  the newest patchnotes heading, `spec.md`'s Version header, and
  `roadmap.md`'s stamp.
- **GitHub repo surfaces**: an actions-only dependabot keeps the new
  SHA pins current; a `release-tags-protected` ruleset blocks deletion
  of `refs/tags/v*`.

## Programmer-facing contract notes (3.43.0 onward, the record-integrity batch)

- **The phase-1 manifest filename is unique per batch.**
  `{date}-batch.json` collides only on the date, so a second same-day
  batch takes `{date}-batch-2.json` (the `_backup_db` exists()-loop);
  the fixed name let two batches silently destroy the durable record
  phase 2 resume and phase 3 consume.
- **Provenance seeds `Z-Lib`, the renamed enum value.** The 2026-09-13
  ruling renamed the 3.40-era `Z-Library` #source value to Brandon's
  spelling; the library's enum carries `Z-Lib` only, so the seeder,
  the fixture, and the skills all track `Z-Lib` now (3.40-3.42
  manifests say `Z-Library` and need the hand-correction before
  signing).
- **Bindery's gate-accepted EPUB repairs are mirrored into the per-file
  `lossy` records** (`_mirror_lossy`: repair status accept/partial ->
  `flagged: true` plus the named repairs and an `applied` flag), which
  is what the seal binds; before 3.43.0 sign consented to repairs the
  manifest never carried.
- **The Calibre-running refusal is lock-class exit 1 everywhere** (set
  mode, phase 2, phase 3, dispatch_integrate): usage problems exit 2,
  an open Calibre is not a usage problem. dispatch_integrate runs its
  usage guards BEFORE `find_db`, so a missing `--ids` is exit 2 however
  resolvable the library is.
- **`--export` propagates its refusals** (unknown `--format` 2, bad
  `--show-custom` 1; run_export returns them like run_search_export),
  and the implicit `--wing` fallback passes `fmt=args.format`.
- **The TUI's `_restricted` parse-failure notice goes through
  `_notify`**, which blocks on Enter: every caller resets the terminal
  right after the prompt, and a bare print was erased before it could
  be read (a typo'd scope ran UNRESTRICTED silently).
- **RestrictedView scopes `get_tag_counts`** (the last read mode
  reading a global aggregation): recounted from the scoped rows like
  `get_entities`, uncarried tags absent. NOTE the ratings recount key
  is `str(b["rating"])` and CORRECT: get_all_books rows carry the raw
  0-10 int, matching `CAST(rating AS TEXT)` entity names. THE FINAL
  AUDIT's L2.2 "4.0 key" finding was a misdiagnosis (it assumed star
  floats); the `int(round(stars*2))` formula it suggested would key
  "16" for a rating of 8, and test_restrict pins the correct key.

## Programmer-facing contract notes (3.42.0 onward, the blitz candidates)

- **`--audit` and `--health` share one derivation.**
  `modes/audit.py:collect_issues` returns every row plus the non-row
  extras (staleness, dirtied queue, the named metadata rows the prose
  blocks use); `run_audit` renders CSV + prose, `show_health` renders
  counts. Exit 0 always for --health (a dashboard, not the audit's
  CSV contract). Never derive an audit row in either renderer.
- **The catalog Markdown shape is `fmt="md"`** on write_catalog and
  both sweeps: `#` header (same provenance content), `##` per author,
  `- ` bullets with bold titles, hr + bold total. `--format` accepts
  `md`; the sweeps name files `.md`; json/csv/ai on a catalog stay
  silently-ignored text (pre-existing). Text form is unchanged.
- **Reading analytics' era split fires only when negative spans
  exist**: library-era spans (finished >= added) and pre-library
  reads report separate medians; a clean library keeps the old
  single-median line byte-for-byte.
- **The TUI's scope prompt is `_restricted(db)`**: blank = whole
  library, an expression resolves once through RestrictedView, a
  parse failure notifies and stays unrestricted (the CLI would exit
  1; the session has nowhere to exit to). Menu structure lives in
  `_menu_sections()` and Settings must stay the last section with
  Change Database/Quit first/second: the s/q aliases pin those
  coordinates.

## Programmer-facing contract notes (3.41.0 onward, the six-lens batch)

- **calibredb id lists are space-separated, never ranges.**
  `run flush` joins each chunk's ids individually: calibredb reads
  `5-900` as EVERY book between the endpoints, and the dirtied queue
  is generally non-contiguous (the range form embedded metadata into
  books the queue never named). `run flush --ids/--search` resolution
  failures are usage errors (exit 2), matching the other verbs.
- **fetch-ebook-metadata's `-o/--opf` is a store flag.** The OPF
  arrives on stdout; both call sites (`run._fetch_metadata` in phase
  2 and integrate's `run_backfill`) stage stdout to a temp file. The
  phase-2 ambiguity sniff reads "multiple" only: the no-result log
  says "No matches found", so bare "matches" classified every empty
  lookup as ambiguous. The seam tests mock subprocess, not the verb,
  so a regression cannot hide behind a mocked `_fetch_metadata`.
- **`run convert` skips already-has-target at plan time** (the merge
  verb's shape); apply counts those already-so, not failed. A
  registration failure after a successful conversion is a failed
  report row, never a traceback.
- **`dispatch_run` refuses `--restrict` (exit 2).** main() dispatches
  the run subcommand BEFORE the read-surface refusal gate, so the
  check lives at the top of dispatch_run; new run-verb plumbing must
  keep it there.
- **integrate's pgrep guard is fail-closed**: timeout or OSError
  means assumed-RUNNING (refuse --apply), matching run.py's recorded
  semantics. The old cut inverted it and proceeded against live
  Calibre.
- **Backfill apply failures are counted and fail the verb (exit 1)**;
  `_apply_backfill` wraps parse and write like run.py's `_apply_opf`
  (a malformed OPF is a failed row). ISBN selection prefers
  `opf:scheme=ISBN` and falls back to an ISBN shape through cquarry's
  `to_isbn13` (recomputed check digit; the first `dc:identifier` may
  be a Goodreads id).
- **The catalog sweeps propagate per-file failures (exit 1)**:
  `--all-wings`/`--all-saved-searches` count only files that exist,
  drop a failed entry's stale file (it must not stand in for the
  fresh write), warn regardless of `--quiet`, and report "N of M
  written". A saved search that no longer parses stays a
  warning-and-skip (the A.5 contract); a WRITE failure is not.
- **Set mode's backup takes the sqlite-API route** (run.py and
  integrate.py share the shape): timestamped, outside the library,
  `_UsageError` on failure. No copy2 anywhere on a write door.
- **`--audit` carries the metadata-quality rows** (the bindery-routed
  item): `find_invalid_uuids`, `find_sentinel_pubdates`,
  `find_bad_language_codes` as advisory `issue_type` rows with value
  brackets and summary blocks. False-positive notes live in the
  renderer comments (none by construction for uuids; year-1/101
  pubdates indistinguishable by design; the 3-letter shape check
  never flags a rare valid code). Real-library probe 2026-09-13: all
  three classes CLEAN at the DB level; bindery's 51 OPF-085s were
  file-side (stale sidecar OPFs), not DB drift.
- **Test guards stay below the last class.** Three files had suites
  appended after `if __name__ == "__main__"` (invisible to direct
  file runs, caught by the same repair as 0dc4789); appended classes
  go at file end.

## Programmer-facing contract notes (3.39.0 onward, Phase 19 C)

- **The integration verbs live in `src/cquarry_cli/integrate.py`** and
  dispatch through `run`'s subparser (`INTEGRATE_PHASES` in run.py).
  They are write-path code: read modes never import them. The shared
  lifecycle is `dispatch_integrate`: usage guards (exit 2 before the
  library opens), read-only target resolution (`--search`/`--ids`,
  unknown ids abort), then dry-run plans or the guarded `--apply`
  (anchored pgrep, timestamped `_backup_db` outside the library for
  convert/polish/cover/merge/flush; export touches nothing and needs
  no backup).
- **External programs are subprocess seams**: ebook-convert,
  ebook-polish, calibredb, fetch-ebook-metadata. Every seam tolerates
  the binary being missing as a setup refusal (exit 2), never a
  traceback. `run backfill` is the only verb that touches the network,
  and only at `--apply` (fetch-ebook-metadata).
- **run merge sends the duplicate to the trash**
  (`remove_book(..., delete_files="trash")`, cquarry 1.20's
  `.caltrash/b/<id>/`); `run flush` is the only verb that consumes a
  queue rather than a target list, and an empty queue is exit 0.
- **`run` accepts `--db` after the subcommand** (argparse SUPPRESS so
  the pre-`run` form still wins); the facility-run doc has been
  suggesting that shape all along.

## Programmer-facing contract notes (3.38.0 onward, Phase 19 B)

- **The audit-depth classes host their logic deliberately.** B.3
  (author-sort sanity), B.5 (cover aspect bands), and B.6 (FTS
  coverage rows) were routed "to cquarry" by the deep dive, but
  cquarry never boxed them; per the lane decision they are implemented
  locally (validate_metadata check, scripts/audit_cover_aspect.py,
  modes/fts.py staleness helper reused by modes/audit.py) and the
  cquarry-predicate promotion remains a recorded future option. Do not
  assume a `find_bad_author_sorts`/`find_distorted_covers` predicate
  exists.
- **audit_isbns' year_mismatch is advisory and additive:** it rides
  the per-result records and the exit-1 findings contract but never
  alters the ISBN verdicts. The acceptable multi-author author_sort
  shape is the `" & "` join of per-author sorts in book order
  (Calibre's own form).
- **check_pdf's new findings (text_layer_partial, low_dpi) are
  advisory:** the structural total (header, qpdf_errors) and the
  exit contract run.py's seam honors are unchanged, so phase-1
  manifests keep their shape.

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
  matching `--search`); write verbs, the run verbs, and `--book`/`--id`
  refuse the combination (exit 2; the run verbs in `dispatch_run`,
  see the 3.41.0 notes).
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
  order. NOTE for searching enum columns: prefer the exact form
  (`#reading_status:=Read`) -- the contains form honestly substring-
  matches every value containing the word (in this library "To Read",
  "Reading", and "Read" all contain "read", so the contains form
  matches the whole library). The 3.37-era note calling this a cquarry
  engine issue was a MISDIAGNOSIS, retracted 2026-09-12 after
  instrumented comparison with upstream's CONTAINS_MATCH
  (calibre/db/search.py: `query in t`): cquarry is faithful.
- **`@Name` user-category resolution was skipped by its own gate:**
  the real library's preferences carry zero user categories (read-only
  peek, 2026-09-12). If categories ever appear, the right home is the
  cquarry search engine, not this frontend.

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
  "Z-Lib", the renamed enum value of the 2026-09-12/13 ruling; libgen.* ->
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

## Programmer-facing contract notes (3.24.0 onward)

- **Detail/audit/analytics modes render; cquarry derives.** `--book` is a renderer over cquarry 1.8's `get_book_dossier()` (batch forms compose it in a loop; `--book --untagged` sources ids from `cquarry.integrity.find_untagged`); `--audit`'s per-book predicates come from `cquarry.integrity`; `--analytics`/`--stats` consume `cquarry.analytics`. Do not re-derive a predicate or a stat inline in this repo: promote it to cquarry (the frontend-only split, now enforced by usage). The one deliberate exception: `--audit`'s duplicate grouping stays inline because the CSV joins ids in scan order and `find_duplicate_books()` sorts numerically.
- **`--analytics genres` is a pure renderer over cquarry >= 1.12's `analytics.genre_distribution()`.** That function owns the rollup semantics (genre = first dot-path segment; a book counts once per node even when its tags share an ancestor; shares are fractions of the whole library, so multi-root books push the sum over 1.0; `"untagged"` last). The renderer slices to `--genre-depth N` (default 1 = roots only; deeper levels indent under their parents with the last path segment as the label) and does formatting only: %, bars, the sums-over-100% caveat. Every rendered level stays a share of the whole library, not of its parent.
- **Set mode (Phase 16, `src/cquarry_cli/setwrite.py`)**: one target source (`--ids`, `--from-search`, `--from-untagged`, `--from-manifest`; hand-supplied ids are validated read-only and unknown ids abort exit 2 before anything opens writable) feeds id-less `--batch-*` verbs. `dispatch_set_write` runs BEFORE `dispatch_write` in `cli.py` so a single-book/set combination is refused before anything executes. Dry-run by default; `--apply` demands a closed Calibre (`pgrep ^calibre` guard, the `fetch_library_codes.py` precedent) and a `--backup-dir` outside the library directory (the `stamp_pdf.py` precedent), then ONE `batch()` transaction; any per-(book, verb) failure rolls the whole pass back (exit 1, `committed: false`). `--batch-clear-rating` is manifest-only, mechanically enforced; column verbs refuse `#reading_status`/`status`/`date_read`; there is deliberately no `--batch-remove-book` and no set-mode rating SET. Verb actions reuse the writeops action builders quieted; new set verbs should do the same rather than opening connections inline.

## Programmer-facing contract notes (cquarry >= 1.7)
- `db.get_all_books()` rows expose `authors`, `tags`, `languages`, and `formats` as native `list[str]`. Never `.split(",")` them; comma-containing author/tag names are preserved by the link-table hydration. `normalize_author_display()` accepts both the legacy joined string and the list form.
- Every book row also carries `size` (total `data.uncompressed_size` bytes, may be None) and, since cquarry >= 1.3/1.4, `pages` (native `books_pages_link`), `author_sorts`, and `author_links`.
- `search()` raises `ParseException` for unknown virtual libraries or saved searches; only `resolve_vl()` / `resolve_saved_search()` raise `ValueError` (with an available-names message).
- Raw comments payloads are HTML; run them through `cquarry.helpers.strip_html()` before terminal output.
- **Write verbs** (`--set-*`, `--add-tag`, `--remove-tag`, `--clear-*`, `--remove-book`) are opt-in and funnel through `run_write()` in `src/cquarry_cli/writeops.py`, dispatched by `cli.py` for flags and called directly by `tui.py` for menu flows; it owns the WritableCalibreDB lifecycle and the error-to-exit-code mapping (argument problems exit 2, lock/write errors exit 1). Action builders return `(exit_code, status)` tuples, status `"applied"` or `"already-so"` from cquarry's `changed` returns, and the batch summary reports real outcomes instead of a blanket ok. Read modes never import `cquarry.write` or `writeops`; keep it that way.
- **Dependency policy.** `cquarry` and `vir-tui` ride PyPI floors (see `pyproject.toml`; bump a floor deliberately when adopting new features of that library), never git deps, and `uv.lock` stays out of the repo so installs resolve the floors fresh. CI pre-installs cquarry from git `@main` so main is tested against the library's head.

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
