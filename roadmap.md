# CalibreQuarry Roadmap

What's done, what's next. Updated as of v3.54.0.

**Minimized 2026-09-29 (the cquarry precedent).** Phases 1-19, the maintenance
sweeps, and every audit wave are shipped; their full findings-ledger text is
preserved in git at the tree of commit 0d9b45f (the last full ledger) and the
release-by-release record is `patchnotes.md`. This file keeps the ledger, the
open work, the records that live nowhere else, and Phase 20.

Standing rules, unchanged:

- **Version sync is test-guarded** (`tests/test_version.py`): VERSION ==
  pyproject == `__init__`, the spec's cquarry floor line, the newest patchnotes
  heading, and this file's "Updated as of" stamp all move together.
- **Frontend-only split:** no new read APIs here, they belong to cquarry. Write
  verbs ride `cquarry.write`; the one non-cquarry mutation in the tree is
  `scripts/compress_pdf.py`'s size resync (a recorded script-side exception,
  outside the package contract).
- **The NON-NEGOTIABLES hold at every door:** `#reading_status`, `status`, and
  `date_read` are refused by label in single-book, set, and run verbs
  (`writeops.FORBIDDEN_COLUMNS`).
- **The write rails:** closed-Calibre guard (anchored `pgrep ^calibre`,
  fail-closed on probe trouble), mandatory out-of-tree timestamped backups,
  dry-run default, exit 0/1/2 (argparse misuse is 2 before validation), one
  `batch()` per pass, HMAC-sealed acquisition manifests.
- **Skill sync is floor-not-ceiling:** the phase-1/phase-3 skills (in
  `~/docs/Calibre Library/.claude/skills/`) are synced in the same release as
  any behavior change they touch, predicted or not.

## Completed phases (ledger)

| Phase | Scope | Closed | Releases |
|---|---|---|---|
| 1 | Core engine: read-only `?mode=ro` access, db auto-detection, hierarchical tags, VL search parser, cached reads | early | pre-v3.0.0 |
| 2 | Display and export: author catalogs, all-wings batch, stats, audit, recent, series, JSON/CSV export, VL listing | early | pre-v3.0.0 |
| 3 | Interactive TUI: curses menu and pager, display modifiers, persistent config, package extraction (`src/cquarry_cli/`, `cquarry` console script) | early | pre-v3.0.0 |
| 4 | Extended reads: search export, AI export, tag tree, pace stats, duplicates, custom columns, cover quality, author stats, wing overlap, genre share (+`--genre-depth`, 3.28.0), format migration, color, tag dump | early (+3.28.0) | pre-v3.0.0 |
| 5 | Search-parity engine rewrite + first companion scripts (`compress_pdf.py`, `audit_epub_content.py`); stdlib-only contract; floor 3.14 | 2026-06/07 | v3.0.0 |
| 6 | Companion scripts: validate_metadata, reconcile_file_metadata (+`--repair-pdf`), audit_epub pagenumbers, audit_drm, audit_epub ocr | 2026-06..07 | v3.0-v3.6 |
| 7 | Judgement and external catalogue: spot_check correctness + `--review`, fetch_library_codes (LoC SRU), reconcile identifier-space fix | 2026-07 | v3.7.0-v3.8.0 |
| Sweeps | Full-repo maintenance passes (2026-06-09, 2026-08-07, 2026-08-09): URI encoding, `--tag` scoping, snapshot fallbacks, timeouts, exit codes, doc truth | 2026-06..08 | v3.8.1, v3.9.2 |
| Lattice port | The shared curses skeleton's audit findings ported from Lattice (H6/H7/T2/T4/T6/T7 + generated fallback menu) | 2026-07-02 | pre-v3.2 |
| 12 | Codebase sweep: raw-SQL decoupling, lock fallbacks, crash fixes, thread pools, import cleanup, docs sync | 2026-08-23 | v3.13.0 era |
| 13 | Extraction: vir-tui standalone + its Phase-3 primitives adopted, scaffolding deleted | 2026-08-23 | v3.22.0 |
| 14 | Pre-import screen: `screen_duplicate.py`, `stamp_pdf.py`, the version/docs re-sync + patchnotes heading pin, skill sync | 2026-08-27..09-08 | v3.23.1-v3.25.0 |
| 15 | Phase-3 dossier: `--book` batch forms, pubdate in the dossier, `--set-pubdate`/`--clear-pubdate` (+ multi-verb single-`batch()` writes), fetch_library_codes guard + `--all-codes` + `--misses-file`, dossier consumption through cquarry Phase 9 | 2026-08-28..09-02 | v3.23.0-v3.26.0 |
| 16 | Set-oriented writes: target-set sources (`--ids`/`--from-search`/`--from-untagged`/`--from-manifest`), 21 `--batch-*` verbs over shared builders, dry-run/apply rails, the rating carve-out, changed-return threading, cquarry 1.13 floor | 2026-09-06 | v3.29.0 |
| 17 | The acquisition run: `acquisition-manifest/1`, `run phase1/phase2/phase3`, `--book --format json`, `comments_census.py`, `check_pdf.py`; the pathway amendment landed verbatim in the library `CLAUDE.md` | 2026-09-06 | v3.30.0 |
| 18 | Hardening from the 2026-09-08 five-agent sweep: the phase-1 seams, the HMAC manifest seal, quarantine consent, phase-2 accounting, phase-3 rails, the output-path guard, TUI degradation, real instrument tests, run_tests.sh, README/spec truth | 2026-09-09..10 | v3.32.0-v3.36.0 |
| 19 | Upstream comparison + FTS + scoping + tree audit + integrations: `--fts`/`--fts-status`, `--restrict` (RestrictedView), the CQ-native tree audit, `--analytics reading`, `--all-saved-searches`, simhash content duplicates, truncation/author-sort/ISBN/cover-aspect/FTS-coverage/PDF-depth/copyright audits, `run convert/polish/cover/export/merge/flush/backfill` | 2026-09-12..13 | v3.37.0-v3.39.0 |
| Wave 14 | Six-lens full-audit fixes: flush id-ranges, `_fetch_metadata -o`, convert overwrite skip, integration-verb hardening, the routed metadata-quality rows, `--health`, the reading-analytics era cut, the Markdown emitter, TUI adoption | 2026-09-13 | v3.41.0-v3.42.0 |
| Final audit | NEW findings batch: manifest uniquification, restricted ratings recount (with the misdiagnosis correction), TUI notice survival, exit-code discipline, precedent_tags promotion to cquarry 1.22, comment/header truth, publish hardening (SHA pins, concurrency, pinned ruff; the pypi REST policy probed and REVERTED), docs truth, housekeeping, the code tail, the prose pass | 2026-09-13..16 | v3.43.0-v3.48.0 |
| Field waves | Provenance seeder `Z-Lib`, the lossy mirror, screen_duplicate volume-token collapse + `classify_titles`, the pubdate truncation, `stamp_epub.py`, the multi-author seed split, stamp_pdf's parse-crash recovery, the cc6 in-batch verification | 2026-09-14..26 | v3.49.0-v3.54.0 |

## Open work

### Feature candidates and behavior calls (each fires on its own green light)

- [x] Feature candidates logged (FINAL-REPORT L4, ranked) — retired 2026-10-02,
      every candidate resolved. Shipped: --search QUERY --format md, the
      --health json shape + the annotations-dirtied line + --fail-on-findings
      (3.46.0); --rename-entity + the sort setters and the trash surface
      (3.45.0); --identifierless and restrict-scoped get_tag_counts (3.43.0);
      the tag-tree rolled-up subtree counts (cquarry.helpers.tag_rollup,
      consumed by --analytics tags) and addition_timeline year granularity
      (--pace-granularity year). Declined by recorded decision: the audit's
      inline duplicate grouping stays inline (the CSV joins ids in scan order;
      cquarry's find_duplicate_books sorts numerically). Re-homed: the
      fts-index candidate is superseded by Phase 20's `run fts-index` box, and
      the two gated residues (the TUI set-write batch session, the
      dirtied_formats promotion) live in their Phase 20 boxes above.
- [x] **Per-mode --format corners are silently ignored** (`--book --format md`, `--fts --format csv|ai`): either honor them per mode or refuse them (exit 2) like --export does now; a behavior call, not a mechanical fix. *(Shipped 3.46.0 with the refuse answer (exit 2 naming the one format the mode supports); confirmed in the tree at cli.py's --book gate and modes/fts.py's json-only check. Ticked 2026-10-02; the box was left open when the release shipped.)*
- [ ] **The pypi-environment tag policy is UI-only**: Settings -> Environments -> pypi -> Deployment branches and tags -> allow `v*.*.*` (REST policies are branch-type only and reject tag deployments outright; see the 1.22.0/3.44.0 errata and the cquarry erratum).
- [x] **reconcile: verify-after-embed for EPUB pubdate (the exiftool `-m` lesson again)** (observed 2026-09-16, #9177 Discourses and Selected Writings): `calibredb embed_metadata` reported success but the file kept its original EPUB3 `dc:date` (2010-10-25) — the book has dual date elements and the embed does not move the one ebook-meta reads back. Reconcile now reports this one book as eternally drifted. A one-line post-embed read-back per written field would convert silent no-ops into reported residuals. (Adopted into Phase 20 by the parity program.) *(Shipped 2026-10-02: `verify_embedded` re-diffs every file a writer claimed after the apply pass; residuals are reported and fail the run, and `run flush` names its two empty states apart and reports the queue it leaves behind.)*
- [x] **fetch_library_codes: title/author SRU fallback for ISBN misses** (observed 2026-09-16): the ISBN-driven pass hit 6 of 23 nonfiction books; hand-written LoC SRU `bath.title=` + `bath.author=` queries recovered 15 of the 17 misses at work level (only the German Analysis 3 and the Chinese Kodaira have no LoC record at all). The tool could do this fallback itself and tag work-level hits as such. *(Shipped 2026-10-02 as the opt-in `--sru-fallback`: one paced title/author query after a clean ISBN miss, hits tagged work-level in the per-book lines and the report, work lookups cached under their own key.)*
- [x] **Reviewer verdict flips have no sanctioned propagation into approved_for_import** (observed 2026-09-19): the runner builds the approved list from per-file verdicts at manifest-creation time (run.py manifest.approve), and the Moral Letters / Arcana review flows both require flipping `verdict` AND rebuilding `approved_for_import` by hand — two places, no warning on divergence, and sign seals whatever the list says. Either sign should re-derive the list from the per-file verdicts (single source of truth) or refuse to sign on divergence; a reviewer-facing `cquarry run approve --manifest FILE` wrapping manifest.approve() would also close it. *(Shipped 2026-10-02, both halves: `run approve` re-derives the list from the verdicts (REPLACE, so a flip back to a refusal also propagates), and sign refuses the derived-vs-listed divergence in BOTH directions naming approve as the fix. The manifest validator's pairing cross-check gains the opt-out flag the repair door needs.)*
- [x] **validate_library.py: EVERY_BOOK_COVER has no allowlist mechanism** (observed 2026-09-19, mixed wave): #9268 Deadfall arrived with no embedded cover (D&DBeyond digital files carry none), and the validator reports it as an error with no suppression path. Every other by-design finding has one (READ_NO_DATE, FORMAT_FICTION_PDF, etc.). A `every_book_cover_allowed` key in taxonomy.yaml — or the standard `rule_options.EVERY_BOOK_COVER.allowed_ids` — would close it. (The cover was set from PDF page 1 for this batch, but a digital-native file with genuinely no cover art will recur.) *(Closed 2026-10-02 by the tree, not by new code: validator v2 shipped the generic `rule_options.<RULE>.allowed_ids` mechanism, and EVERY_BOOK_COVER's findings carry book ids, so the suppression path exists. The live case resolved itself — #9268's cover was set and the library currently holds zero has_cover = 0 books. docs/taxonomy.example.yaml now documents the block (commented; a null rule_options crashes the validator) with the Deadfall entry as the worked example.)*

## Phase 20: the Calibre-automation parity lane (opened 2026-09-29)

Brandon opened the ecosystem parity program on 2026-09-29 (cquarry roadmap.md, "The
parity program"): every Calibre capability covered natively or by orchestration of
Calibre's own headless tools, so Calibre work can be fully automated. CalibreQuarry is
the ecosystem's orchestration lane, and this phase commits the headless upstream verbs
that have no ecosystem wiring. Parity claims count native + owned-gap + orchestrated
coverage; process-bound and declined surface is excluded with recorded reasons (the
ledger lives in cquarry's roadmap). Sequenced, undated; ship order within the phase is
free; every verb ships behind the standing rails (closed-Calibre pgrep guard,
out-of-tree backups where rows change, dry-run default, the exit 0/1/2 contract).

### The unwired headless verbs (each a `run` verb around a Calibre binary; all headless upstream today, src/calibre/linux.py:23-42)

- [x] **run backup-metadata**: calibredb backup_metadata regenerates per-book OPF
      sidecars for the dirtied queue; the headless form of the daemon job cquarry's
      `metadata_dirtied` feed exists for. Pairs with run flush: flush embeds into the
      format files, this refreshes the sidecars. *(Shipped 2026-10-02: dry run lists
      the queue, --apply behind the flush rails (Calibre closed, out-of-tree backup),
      an honest empty-queue no-op, and `--all` for the deliberate whole-library
      flood.)*
- [x] **run restore-database**: calibredb restore_database rebuilds a metadata.db from
      stored OPFs (upstream src/calibre/db/restore.py). Refuses to write an existing
      metadata.db without an explicit --target/--force; the NATIVE rebuild stays
      declined in cquarry, the O-lane covers it. *(Shipped 2026-10-02: the destination
      is never guessed (required --target), an existing metadata.db is replaced only
      under --force, an OPF-less target refuses, and the dry run names what the
      rebuild loses.)*
- [x] **run clone**: calibredb clone (fresh-schema library copy); cquarry's
      `backup_to()` is the consistent-copy half, this is the schema-fresh half.
      *(Shipped 2026-10-02: the dry run says NO BOOKS copy, loudly; the target
      must not exist or must be empty and never collides with the source; no
      backup, the source database is never opened writable.)*
- [x] **run fts-index**: calibredb fts_index (extraction and Calibre's own tokenizer
      into the sidecar; cquarry contractually never touches the FTS5 tables). Consumes
      the dirtied_formats queue; adopts cquarry's `get_dirtied_formats()` (cquarry
      Phase 14) so --fts-status retires its raw sidecar read (modes/fts.py:85-97, the
      one recorded exception) and the L4 dirtied_formats promotion fires with it.
      Supersedes the L4 "run fts-index verb" candidate. *(Shipped 2026-10-02: the raw
      sidecar read is retired (the tier is clean of raw SQL again), the default
      action reindexes exactly the queued book:FORMAT pairs, --fts-status reads
      upstream's disabled answer as a report, --enable carries its own backup rule
      for the preference row it writes, and the cquarry floor moves to 1.24.0.)*
- [x] **run catalog-epub / run catalog-bibtex**: calibredb catalog through the EPUB_MOBI
      and BIBTEX catalog plugins (customize/builtins.py:704); the CSV/XML half is
      already native (--catalog over export_rows). *(Shipped 2026-10-02: the output
      extension decides the plugin and is enforced, because calibredb silently falls
      back to EPUB on an unrecognized one; targets resolve read-only, no selection
      meaning the whole library; the verb never writes the library.)*
- [x] **run customize**: calibre-customize install/enable/disable/list
      (src/calibre/customize/ui.py); the missing automation for installing the Bindery
      Repair plugin (and any other plugin) headlessly. *(Shipped 2026-10-02: needs no
      library (routed before any database resolution), exactly one action per
      invocation, mutators dry-run by default with --apply behind the closed-Calibre
      guard.)*
- [x] **run debug-tools** (curated subset, not a passthrough): calibre-debug
      explode/implode/diff/kepubify/un-kepubify/inspect-mobi (src/calibre/debug.py:60-206).
      The -e/--exec-file surface stays out: arbitrary code execution is not a verb.
      *(Shipped 2026-10-02: the parser never offers the exec surface, the mutators
      are dry-run by default with --apply behind the closed-Calibre guard, and the
      reads (diff, inspect-mobi) run immediately.)*
- [ ] **run device** (USBMS subset): ebook-device ls/df/books/mkdir/cp/cat/rm/touch
      (src/calibre/devices/cli.py:247-390). MTP and the wireless Calibre-Companion
      stack are process-bound and stay excluded from parity.
- [ ] **--saved-search add/delete/rename**: the calibredb saved_searches CRUD parity
      item; needs cquarry Phase 15's typed `set_preference` writer first.
- [ ] **--add-custom-column / --remove-custom-column**: the calibredb schema-CRUD
      parity item; cquarry.write has shipped create/delete_custom_column since 1.20,
      so only the frontend verb is missing (set_custom is already covered by
      --set-column).

### Open unowned surface, owners-wanted (not committed; recorded in cquarry's ledger)

- [ ] **run news** (candidate): wrap `ebook-convert <recipe> out.epub` for the 1,094
      upstream recipes (recipes/; web/feeds/news.py; the 2026 anti-bot infra rides
      upstream). Becomes committed work only when Brandon wants recipe fetching in the
      automation surface.

### Adopted from the existing queue (committed by the program; the boxes stay in place above)

- [x] reconcile verify-after-embed for EPUB pubdate (the 2026-09-16 box): a one-line
      post-embed read-back per written field, so `run flush` cannot silently no-op;
      parity-relevant flush honesty. *(Landed 2026-10-02 with the flush empty-state
      and queue-remaining honesty.)*
- [x] docs truth: spec.md:232 still says "Not a converter. It does not touch book files
      themselves" while §3 ships run convert/polish/cover and the §5 scripts rewrite
      files (found 2026-09-29 during the parity scoping). Fix the sentence to name the
      orchestration posture (the verb shells Calibre's own tools; the native surface
      stays read-only). *(Fixed 2026-10-02: §4 now draws the line as package
      guarantees versus orchestration and script capabilities.)*
- [ ] The L4 TUI set-write batch session stays GATED (risk surface); the program does
      not ungate it. The per-mode --format corners, the pypi tag policy, the
      fetch_library_codes SRU fallback, the run approve design, and the
      validate_library allowlist also keep their existing open boxes and gates.

## Records kept (decisions that live only here)

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
- **Deferred with dated notes:** the shared test `_SCHEMA` builder (six fixtures,
  each tuned to its suite) and the two CI skips (`/usr/share/dict/words`,
  ghostscript — host tools, not code); the db_util consolidation (the private
  connect_ro copies have genuinely drifted: reconcile needs Row rows and its own tmp
  layout).
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
