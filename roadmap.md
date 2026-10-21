# CalibreQuarry Roadmap

What's done, what's next. Updated as of v3.70.0.

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

- [x] Feature candidates logged (FINAL-REPORT L4, ranked): retired 2026-10-02,
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
      the TUI set-write batch session keeps its GATED Phase 20 box below, and
      the dirtied_formats promotion fired with the fts-index tick.
- [x] **Per-mode --format corners are silently ignored** (`--book --format md`, `--fts --format csv|ai`): either honor them per mode or refuse them (exit 2) like --export does now; a behavior call, not a mechanical fix. *(Shipped 3.46.0 with the refuse answer (exit 2 naming the one format the mode supports); confirmed in the tree at cli.py's --book gate and modes/fts.py's json-only check. Ticked 2026-10-02; the box was left open when the release shipped.)*
- [ ] **The pypi-environment tag policy is UI-only**: Settings -> Environments -> pypi -> Deployment branches and tags -> allow `v*.*.*` (REST policies are branch-type only and reject tag deployments outright; see the 1.22.0/3.44.0 errata and the cquarry erratum).
- [x] **reconcile: verify-after-embed for EPUB pubdate (the exiftool `-m` lesson again)** (observed 2026-09-16, #9177 Discourses and Selected Writings): `calibredb embed_metadata` reported success but the file kept its original EPUB3 `dc:date` (2010-10-25): the book has dual date elements and the embed does not move the one ebook-meta reads back. Reconcile reported this one book as eternally drifted. (The residual itself closed in 3.56.0, issue #3: --apply now normalizes the EPUB OPF to one canonical dc:date after every embed, so dual-dated packages converge on the next pass.) A one-line post-embed read-back per written field would convert silent no-ops into reported residuals. (Adopted into Phase 20 by the parity program.) *(Shipped 2026-10-02: `verify_embedded` re-diffs every file a writer claimed after the apply pass; residuals are reported and fail the run, and `run flush` names its two empty states apart and reports the queue it leaves behind.)*
- [x] **reconcile: EPUB3 packages report permanent pubdate drift when the DB pubdate lands on a month boundary** (observed 2026-10-09, issue #4, the five-book live case): after the 3.56.0 dc:date normalization, five EPUB3 books reported a pubdate residual on every reconcile pass, and a DB pubdate forced to exact UTC midnight (the #9740 experiment) did not converge them. All five carry day-1 pubdates (CBT 2020-10-01, the Byzantium trilogy 1988/1991/1995-01-01, Merkel 2024-01-01) while the batch's 11 in-sync EPUB3s are mid-month: the file's date-only canonical dc:date is correct as text, but calibre's EPUB3 reader nudges date-only values off month boundaries (fix_only_date: day 1 becomes day 2, a month's last day pulls back one), so ebook-meta read the file one day off forever. The EPUB2 reader (the general date parser) never nudges, which is why the 3.56.0 EPUB2 class (#9177/#9635) converged. *(Shipped 3.61.0: `canonical_date_target()` in epubdates.py builds the canonical element as a full ISO instant, the DB pubdate's date at UTC midnight, the shape calibre's own EPUB3 writer emits; both embed paths (reconcile --apply, run flush) take the target from it, surgery unchanged. Live-verified on a scratch copy of #9740: reads back exactly 2020-10-01T00:00:00+00:00. Mid-month date-only files stay in-sync; day-1/month-end ones converge on the next --apply/flush.)*
- [x] **fetch_library_codes: title/author SRU fallback for ISBN misses** (observed 2026-09-16): the ISBN-driven pass hit 6 of 23 nonfiction books; hand-written LoC SRU `bath.title=` + `bath.author=` queries recovered 15 of the 17 misses at work level (only the German Analysis 3 and the Chinese Kodaira have no LoC record at all). The tool could do this fallback itself and tag work-level hits as such. *(Shipped 2026-10-02 as the opt-in `--sru-fallback`: one paced title/author query after a clean ISBN miss, hits tagged work-level in the per-book lines and the report, work lookups cached under their own key.)*
- [x] **Reviewer verdict flips have no sanctioned propagation into approved_for_import** (observed 2026-09-19): the runner builds the approved list from per-file verdicts at manifest-creation time (run.py manifest.approve), and the Moral Letters / Arcana review flows both require flipping `verdict` AND rebuilding `approved_for_import` by hand: two places, no warning on divergence, and sign seals whatever the list says. Either sign should re-derive the list from the per-file verdicts (single source of truth) or refuse to sign on divergence; a reviewer-facing `cquarry run approve --manifest FILE` wrapping manifest.approve() would also close it. *(Shipped 2026-10-02, both halves: `run approve` re-derives the list from the verdicts (REPLACE, so a flip back to a refusal also propagates), and sign refuses the derived-vs-listed divergence in BOTH directions naming approve as the fix. The manifest validator's pairing cross-check gains the opt-out flag the repair door needs.)*
- [x] **validate_library.py: EVERY_BOOK_COVER has no allowlist mechanism** (observed 2026-09-19, mixed wave): #9268 Deadfall arrived with no embedded cover (D&DBeyond digital files carry none), and the validator reports it as an error with no suppression path. Every other by-design finding has one (READ_NO_DATE, FORMAT_FICTION_PDF, etc.). An `every_book_cover_allowed` key in taxonomy.yaml (or the standard `rule_options.EVERY_BOOK_COVER.allowed_ids`) would close it. (The cover was set from PDF page 1 for this batch, but a digital-native file with genuinely no cover art will recur.) *(Closed 2026-10-02 by the tree, not by new code: validator v2 shipped the generic `rule_options.<RULE>.allowed_ids` mechanism, and EVERY_BOOK_COVER's findings carry book ids, so the suppression path exists. The live case resolved itself: #9268's cover was set and the library currently holds zero has_cover = 0 books. docs/taxonomy.example.yaml now documents the block (commented; a null rule_options crashes the validator) with the Deadfall entry as the worked example.)*
- [x] **screen_duplicate: same work, swapped author order, stripped metadata evades the within-batch screen** (observed 2026-10-10, issue #5, the Stroustrup pair): two z-lib rips of one book (`The Design and Evolution of C++ (Bjarne Stroustrup) (...)` vs `The design and evolution of C++ (Stroustrup, Bjarne) (...)`, a 6.4 MB image-only scan vs a 26.4 MB OCR copy, no embedded metadata either side) passed the within-batch screen silently and were caught only by hand in the edition-research pass. ebook-meta's fallback reports the whole filename stem as the title and Author(s) "Unknown", so the compared stems carried the bracketed author segments in different orders, and the files differed in every physical dimension, so nothing downstream would have caught the pair either. *(Shipped 3.62.0: `_filename_seed()` parses the fallback shape when BOTH records look metadata-less, the first paren group compared as an order-free author token set plus a whole-stem token gate so a site-first marker group cannot over-match two different works; the pair now surfaces as batch_duplicates both ways, everything else falls through to the raw stem comparison unchanged.)*
- [x] **reconcile --apply: COMPLETED WITH ERRORS with no error detail printed** (observed 2026-10-11, issue #6, a 268-book pass): the failure lines ride stderr inline as they happen, so a capture that lost stderr saw "some operations failed (see above)" over an empty above; the follow-up read-only verify showed 268/268 in sync but could not reconstruct what had failed. *(Shipped 3.62.0: every failed operation is collected, calibredb chunk failures naming their id span and the exiftool/djvused/dc:date failures carrying book id and format, and a FAILURES block prints on stdout before a summary that carries the count; a residual-only failure no longer prints a dangling "(listed above)".)*
- [x] **fetch_library_codes writes the misses worklist to CWD** (observed 2026-10-09 and 2026-10-11, issue #7): the documented invocation runs from the library root, so the default `fetch_library_codes_misses.txt` landed INSIDE the library, flagged by the tree audit as a root stray and one rm from being forgotten forever. *(Shipped 3.62.0: the default is beside the script via `default_misses_file()`, absolute and library-proof; `--misses-file PATH` already existed and still overrides.)*
- [x] **check_pdf: non-UTF-8 tool output or a slow `qpdf --check` kills the whole battery** (observed 2026-10-11, issue #8, the 733-page Building Git under contention): `_run` decoded tool output strictly (`text=True`, no `errors=`), so qpdf echoing a PDF's raw non-UTF-8 bytes into its warning context raised `UnicodeDecodeError` and the battery exited 1 with the `--json` report unwritten, which the phase-1 verb's rc-in-(0,1) seam misread as "findings, report written" and died on the empty file; meanwhile a blown 120 s budget was swallowed as "unavailable", hiding the cause. *(Shipped 3.62.1: `_run` decodes lossily with `errors='replace'`, and a `qpdf --check` timeout names itself as the `qpdf_check` verdict "timeout" with an advisory `qpdf_timeout` finding outside the structural count (environmental trigger, the exit contract is unchanged); the other tool seams read the `_TimedOut` sentinel as unavailable, unchanged.)*
- [x] **Every text-mode spawn decodes strictly (the issue-#8 class beyond check_pdf)** (raised 2026-10-11 in the #8 close-out, issue #9): 40 more spawn sites across run.py, integrate.py, news.py, and seven scripts read piped tool output with `text=True` and no `errors=`, one non-UTF-8 byte from the same unhandled UnicodeDecodeError. *(Shipped 3.62.2: all 41 text-mode sites in the tree carry `errors='replace'`, decode only; a new AST tripwire pins that every subprocess call in text mode (text=, universal_newlines=, encoding=) carries errors="replace", value-checked, so the blanket cannot rot; the mocked seam fakes absorb the kwarg and real-subprocess decode tests drive run.py's _run and the stamp helpers.)*
- [x] **fetch_library_codes stores non-classification 050 values (MLCM/CPB control numbers, IN PROCESS placeholders) as lcc** (observed 2026-10-11, issue #10, 80 live values): the SRU parser took the 050 verbatim, so LoC ebook-preview records stored control numbers and acquisition placeholders (`MLCM 2024/43047 (B)`, `CPB Box no. 305 vol. 19`, `ACQUISITION IN PROCESS`, `Ephemera collection [item]`) as lcc, and the children's-record `[Fic]` shelf mark landed in ddc; each poisons the composite column and rides reconcile into every embed. *(Shipped 3.63.0: `valid_lcc`/`valid_ddc` (the issue's classification-shape regexes plus the MLCM/MLCS/CPB/IN PROCESS placeholder gate) run in `parse_mods` per element, at both cache-read sites (pre-guard cache entries covered; a sanitized junk-only entry fires `--sru-fallback` like a fresh miss), and as one catch-all at the single consumption point; junk reads as a miss everywhere: nothing written, worklisted.)*
- [x] **reconcile: identifier deletions read in-sync (file-side extra identifiers are not drift)** (observed 2026-10-11, issue #11, the same sweep): after `wdb.clear_identifier` removed an identifier DB-side, the EPUB OPF still carried the deleted `dc:identifier` (the deletion only queues OPF resync; reconcile is the tool that embeds), and the one-directional identifier diff read the book as in sync with `--apply` embedding nothing, so the stale value survived until Calibre's next launch; 72 EPUBs read in-sync with stale values, 1 PDF drifted and embedded. *(Shipped 3.63.0: the identifier diff is two-directional, so a file-side identifier type the DB lacks is drift (`embed_metadata` regenerates the OPF wholesale, so --apply removes it) and the verify read-back judges it by the same shared `diff_fields`; `FILE_ONLY_IDENTIFIERS` (`uri`, `ean`) stays exempt as the recorded legitimate extras, so strict equality is still not the rule.)* *(Superseded 3.63.3, the REOPEN (same day, the evening wave, 5 books): the read side was right but the writer never converged; `embed_metadata` does NOT regenerate the OPF wholesale. Verified live on calibre 9.15/9.16, its writer preserves the package unique-identifier element verbatim and never removes scheme-less elements, so a deleted type in either shape survived every re-embed and repeated `--apply` passes oscillated. The fix is the new post-embed `normalize_epub_identifiers` pass (shared by reconcile `--apply` and `run flush`): the OPF identifier set is forced to the DB's by the same rule the diff applies, a stale package slot is rewritten in place to `calibre:<uuid>`, scheme-less elements never satisfy an entry (the reader check_isbn-normalizes them), and the read-back re-diff stays the arbiter. Live-drilled on a scratch library: first `--apply` converges, second reads in-sync.)*
- [x] **Adopt cquarry 1.26.3: set_authors/add_book no longer crash on comma names over piped author rows** (filed 2026-10-11, issue #12): cquarry VirInvictus/cquarry#5 (fixed 1.26.3) left this repo's three `set_authors`/`add_book` seams (run phase2's import over verbatim creator strings, the set-write fixes path, add_book's author creation) dying on `UNIQUE(authors.name)` whenever a comma-bearing byline met a piped artifact row; the live teacher was a 143-book batch aborting at book 10083, rolled back cleanly. *(Shipped 3.63.1: the floor bump (`cquarry>=1.26.3`, spec line in step) and one version-gated seam test, `TestSetAuthorsPipedRowParity`, pinning that the `--set-authors` verb over a pre-seeded piped row with the comma display name links the existing row with its stored spelling and sort kept.)*
- [x] **Adopt cquarry 1.27.0: the author display-form parity wave** (raised 2026-10-11 by Brandon an hour after #12 closed, no GitHub issue): cquarry 1.27.0 shipped the three issue-#5 follow-ups as one wave (resolution, layout, inputs), which retires the 3.63.1-era caveat that a reused piped row's sort repair must pass the piped spelling. *(Shipped 3.63.2: the floor bump (pyproject + spec Dependencies line + spec 2.1's prose sentence and per-bump list, the unpinned carriers the release-auditor taught this repo to check on every floor bump) and one version-gated seam test, `TestRenameEntityDisplayFormParity`, pinning that `--rename-entity authors` -- the frontend's one consumer of the wave -- resolves a piped-stored row by its display comma name while the stored piped spelling keeps working.)*
- [x] **normalize_epub_identifiers refuses default-namespace EPUB3 OPFs** (filed 2026-10-13, issue #16, the 223-book morning wave): EPUB 3's alternate package form (the OPF namespace bound as the DEFAULT on `<package>`, no `xmlns:opf` prefix anywhere, `xmlns:dc` on `<package>` outside the metadata block) tripped both 3.63.3 surgery preconditions, so 13 of 223 books (12 held Bloomsbury/Baen-row rips and one Standard Ebooks file) reported `identifier normalization failed` and stayed drifted across repeated `--apply` passes; the interim workaround was hand-adding redundant-but-legal namespace declarations to all 13. *(Shipped 3.63.5: the OPF prefix is synthesized as a declaration on the `<metadata>` opening tag whenever the surgery must name one (a scheme-less slot rewrite or any insertion), byte-stable on the next pass and absent from removal-only passes; the dc binding check reads document scope (block or any ancestor) in BOTH passes, with the nowhere-bound refusal retained for genuinely malformed shapes; the same scope hoist covers `canonical_dc_dates`'s insertion; live round-trip verified on calibre 9.16 and the real-ebook-meta read-back class gained the default-namespace variant.)*
- [x] **phase-1 runner flattens audit_drm's unreadable verdict into `DRM: ERROR`** (filed 2026-10-13, issue #15, the 2026-10-12 129-file run): a dead 0-byte EPUB download (The Truthful Art) surfaced as a `manual_repair` decision reading `DRM: ERROR` because run.py built the quarantine reason unconditionally as `DRM: {status}` while dropping the kind/detail columns audit_drm's CSV carries, so triage went down the DRM path on a file that is not a zip at all; the phase-1 skill gained a 0-byte inventory sweep the same run to catch the class before the verb. *(Shipped 3.63.6: `_drm_verdicts` carries the audit's parse failure on ERROR rows (`ERROR: bad zip: ...`; every other status stays the bare class, so the instrument pins and mocks are untouched), the quarantine gate parses the class off the first token, and the reason is its own class (`UNREADABLE: bad zip: File is not a zip file` in the quarantines list and the decision detail, `DRM: DRM` unchanged for real encryption; quarantine behavior identical); the skill's sweep paragraph now names the new shape.)*
- [x] **screen_duplicate misses a same-edition duplicate on an ISBN-less, imprint-suffixed, translator-authored rip (the ASIN match unused)** (filed 2026-10-12, issue #13, the 2026-10-11 116-file run): a Mabinogion rip byte-comparable to held #10113 passed the screen with zero candidates because three factors stacked (no ISBN on the rip, only the mobi-asin the held book also carries, and the screen matched only ISBNs; the imprint-suffixed embedded title left classify_titles with no opinion; the translator-form author against the held `Anonymous` blocked the title path). *(Shipped 3.63.7: `_embedded_fields` collects `asin`/`mobi-asin` values into the record's `asins`, `_library_hits` matches each through the exact-value keypair `identifiers:=VALUE` so a file-side asin meets a library-side mobi-asin across type spellings, the within-batch cross-reference binds a case-folded ASIN intersection like a shared ISBN, and `normalize_title` strips a curated paren-scoped imprint-tag set so the live pair classifies duplicate and imprint variants collide into the hand-check class; the translator-vs-Anonymous author gate is deliberately left to the hand-check rules, and the phase-1 skill's paragraph records the shipped fix with the residue.)*
- [x] **Duplicate screen: edition-tagged held titles defeat the title judge** (filed 2026-10-13, issue #17, the evening wave's 117-file run): a second copy of The Rust Programming Language 3rd ed passed with zero candidates because the stamp correctly strips the edition tag from new arrivals, so the new title read bare while the held book carries ", 3rd Edition" and the judge had no opinion (no shared ISBN/ASIN either); The Linux Command Line 3rd passed the same way and was caught by hand, while The Ghidra Book 2nd ed flagged only because both its titles matched exactly. *(Shipped 3.64.0: the judge strips the ordinal edition forms (", Nth Edition", ": Nth Edition", "Nth ed.") from BOTH titles at every comparison site like the imprint set, anchored to where an annotation can end so prose ("the 2nd edition of...") and non-edition shapes ("Code: Version 2.0") never strip, and the volume annotations keep reading the raw title; the judge still surfaces rather than deciding edition-vs-duplicate. Live-drilled on the issue's exact Rust shape against a scratch calibre 9.16 library; the skill's edition-arrival paragraph rewritten.)*
- [x] **Runner labels an in-batch duplicate "matches an existing library book"** (filed 2026-10-13, issue #18, the same wave): two same-edition files shared only a mobi-asin; the screen's raw JSON was exactly right (library_hits empty, batch_duplicates naming the sibling), but the duplicate decision's single hard-coded detail sent triage querying the library by title and ASIN for a book that was never there. *(Shipped 3.64.0: `_screen_duplicates` grew an optional `refusals` dict beside `notes` carrying the per-path refusal evidence, the refused-set contract unchanged, and the detail reads "matches an existing library book: #id title", "matches another file in this batch: file", or both when both fire; a refusal without the breakdown reads a neutral class-unavailable string instead of guessing.)*
- [x] **Step-3 `--apply --all` applies the lossy strips before the consent gate; the mint reads lossy:false and the decision never fires** (filed 2026-10-13, issue #19, the same wave): the vetting pass flagged a lossy set (The Black Man's Burden, pagination x22) into the batch manifest, the skill's step-3 sweep ran `bindery library --sweep --only all --apply --all` whose `--all` includes the lossy strips, and the completed mint's fresh battery read the file clean, so the lossy_consent decision silently vanished from the signed manifest; consent was order-dependent on which skill-sanctioned step-3 variant ran. *(Shipped 3.64.0, the issue's option 1: the mint compares its fresh battery against the earlier SAME-DAY manifests, and a file recorded lossy-pending that now reads clean gets a `repairs` note naming the earlier manifest and the applied strips, plus a preempted-consent count in the phase-1 summary; informational by design, and silent when the fresh mint still flags the file, when no battery ran, or when a same-day manifest already records the strips applied (the consent path's own record stands). The phase-1 skill's step-6 paragraph points at the note.)*
- [x] **Scripts import the pip-installed cquarry_cli silently; version skew against the uv tool is invisible until behavior differs** (filed 2026-10-14, issue #20, the evening wave's phase-3 run, 114 books): the step-0 upgrade ran `uv tool upgrade calibrequarry` (binary -> 3.64.0) and `pip install --upgrade cquarry bindery-cli vir-tui`, which does not name calibrequarry, so the module the companion scripts import from user-site stayed at 3.63.4; reconcile's identifier normalization refused 8 default-namespace EPUBs the current module handles silently, and the run chased the ghost until a hand-run `pip show calibrequarry` vs `cquarry --version` exposed the split. The same run's secondary nit: reconcile's apply log reported 8 "identifier normalization failed" lines where the follow-up dry run found 1 drifted file, because several refusals rode files the embed had already brought in sync. *(Shipped 3.65.0: every scripts/ entry point prints one `toolchain:` stderr line naming the three workspace versions it sits on (imported-module `__version__` preferred, dist metadata fallback, `absent` for a missing package), from shared `scripts/version_guard.py`; reconcile floor-checks the module it actually imported at 3.63.5 and refuses exit 2 naming both versions and the pip fix -- the strong half of the issue's fix, and the honest version source is the module object, never the dist metadata that can lag a checkout; and the refused normalization is arbitrated by the read-back re-diff, demoted to a `(already in sync)` note on files the read-back finds in sync so the failure count names work that remains.)*
- [x] **stamp_pdf: extend the 3.54.0 qpdf-rebuild self-heal to exiftool WRITE failures naming a bad xref** (filed 2026-10-14, issue #21, the oct-14 wave): Language Implementation Patterns (367 pp) passed the vetting battery (qpdf exits 3 = warnings, and the battery reads the exit code alone, so a reconstructable-xref file lands benign), then stamp_pdf's write refused with "Error: Invalid xref table" (qpdf --check: "file is damaged, xref not found at offset 9737496"); the manual fix that worked was `qpdf --replace-input`, page count verified, remove the `.~qpdf-orig`, re-stamp. *(Shipped 3.66.0: the recovery block fires on the parse-crash class OR an output line naming a bad xref ("Invalid xref", "xref [table] not found", case-insensitive, stderr and stdout), sharing the exact 3.54.0 shape -- one `--replace-input` rebuild, page count preserved or the backup restored, one retry, never a second rebuild; the issue's secondary nit (check_pdf surfacing the reconstruction lines) stays closed as the issue itself called it: cosmetic once the stamp-side heal exists, and check_pdf's exit-code-only reading is recorded contract.)*
- [x] **stamp_pdf: arbitrate an ebook-meta read-back disagreement with an exiftool read-back before declaring STAMP_FAILED** (filed 2026-10-14, issue #22, the fourth blind-verifier recurrence): The Return of Martin Guerre -1 and The Sweet Science both STAMP_FAILED on "read-back disagrees on: isbn" while exiftool held every stamped value correctly (Title, Author, Publisher, Keywords isbn:...); the writes had landed and only ebook-meta's read was blind, the same disposition as Learning Blender (2026-10-10) and the Love unto crypt variant (2026-10-11), each costing a debug cycle that ended in "nothing to fix". *(Shipped 3.66.0, the 3.65.0 reconcile-arbitration shape applied to the stamper: on a verify mismatch the disputed fields are re-read through one `exiftool -j -a -G1` pass over the tags the stamp writes (any group's copy passes; list tags element-wise and joined); a field exiftool holds verifies with the blind reader on the record, a field it also fails holds fails naming both readers, and a dead/unparseable arbitration read confirms every disputed field (fail-closed). EPUB verification arbitrates nothing -- ebook-meta reads EPUBs reliably, no recurrence in the class.)*
- [x] **phase1 runner: seed provenance Z-Lib for markerless bare-name files per the standing ruling** (filed 2026-10-14, issue #23): the 2026-10-12 morning-wave ruling ("3 bare-name PDFs ... seed Z-Lib") left no None case, but the seeder still emitted None for markerless filenames, so every wave containing one needed the same hand correction in the mint pass (2026-10-14: the Jacques Pépin file). *(Shipped 3.66.0: `_provenance_from_filename` returns `Z-Lib` as the bare-name default (`_PROVENANCE_BARE_DEFAULT`), marker matches unchanged; the manifest seal, phase 2's cc6 stamp, and the review step take the seed unchanged, the agent-side rule becomes a verify, and pre-3.66 manifests carrying None stay signed history. Supersedes the 3.35.0 "absence of evidence is not a source" rule.)*
- [x] **companion script: extract-stamp-evidence (the research-evidence extractor has been hand-rolled per wave since 2026-10-11)** (filed 2026-10-14, issue #24): four waves (oct11, oct12-morning, oct13-evening, oct14) each re-derived the same JSON evidence extractor in the wave working dir, and the copies diverged -- the 2026-10-14 copy's id-first `<item>` regex silently emptied front matter for 87 of 111 EPUBs (this wave's OPFs put href before id), caught only by a thin-front census. *(Shipped 3.66.0 as `scripts/extract_stamp_evidence.py`: the stabilized field set one row per file (file/path/fmt, the raw ebook-meta display dump as seed -- the research pass wants the ISBN lines phase 1 refuses --, opf_ids, front from the first 4 spine docs or pdftotext pages 1-4, isbn_snips, provenance via run.py's seeder with a vendored fallback, per-file error never fatal), ElementTree manifest/spine parsing so attribute order cannot matter by construction, the thin-front census built in as the quality gate (listed by name, exit 1 under `--thin-chars`, default 200), top-level-only directory listing so `_quarantine/` never sweeps in, `--out` required and refused inside a scanned directory or on a scanned file, and the report kept a bare list so the wave consumers read it unchanged.)*
- [x] **audit_isbns.py: `--format json` is not machine-consumable and there is no `--json FILE` output** (filed 2026-10-14, issue #27): the human-report banner rode the same stdout stream as the JSON payload, so wave consumers parsed from the first `[` offset instead of `json.load`-ing, and the redirect-plus-offset hack was the only machine path while check_pdf.py and bindery's verbs take `--json FILE`. *(Shipped 3.67.0: `--format json`/`--format tsv` keep stdout to the payload alone -- the vir_tui banner and the setup-error prose ride stderr, text mode is unchanged, and the progress bar was already stderr -- and `--json FILE` writes the same payload `--format json` prints beside any format's human output, the check_pdf.py shape; the exit contract is untouched, and TestMachineOutput drives main() end to end over a fixture library pinning every stream.)*
- [x] **run phase2 metadata fetch injects bare genre tags and average ratings onto imported books** (filed 2026-10-14, issue #26): two documented arrivals (2026-10-04 #9596, a Google-keyed 4.0 average, the only rated import in 45 books, on a To Read title; 2026-10-15 #10564, a goodreads-keyed fetch adding 'Science Fiction' + 'Feminism' plus an 8/10 rating) moved books out of the untagged entry-set query and surfaced RATED_NOT_READ at lint. *(Shipped 3.67.0 as a pin, not a fix: both repo fetch-apply seams are field-limited and structurally cannot write tags or ratings -- `_apply_opf` writes six descriptive fields, `_apply_backfill` only plan-requested fields, and the import batch clears tags and rating first -- so the injector was the manual import passes' own metadata download, whose source plugins write tags and average rating by default; TestApplyOpfWritesNoReaderCuratedFields pins the seam (`dc:subject` elements and rating meta never land, curated rows survive, nothing added from zero) so a future fetch path cannot start writing reader-curated fields silently.)*
- [x] **run phase2: the cc6 `#source` stamp does not survive to the DB -- six consecutive wholesale-'Anna's Archive' arrivals against correct manifests** (filed 2026-10-14, issue #25): the signed 2026-10-14-batch-2 manifest carried Z-Lib x143 + Anna's Archive x1 and the DB read 'Anna's Archive' on all 144, with the issue inferring the in-batch changed=True guard was verifying the wrong thing across the 2026-10-11 x2, 2026-10-13 x2, and 2026-10-14 waves. *(Shipped 3.67.0 as a disposition, not a code change: all 17 manifests of the six waves carry zero `imported_id`s, the field phase 2 itself writes and saves, so the verb never ran and the arrivals were the manual import path's own stamping; a live drill on a scratch sandbox library (real ebook-meta, calibredb, bindery, cquarry 1.28.0) minted, signed, and imported three fixture EPUBs (an `-- Anna's Archive` trailer, a `(z-library.sk)` marker, a bare name) in one batch with per-book provenance landing exactly (Anna's Archive, Z-Lib, Z-Lib) and `#audience` stamped alongside; the in-batch guard was already verifying the thing it claims, no code changed.)*
- [x] **duplicate screen binds unrelated works on shared junk identifiers (template URL/uuid ids)** (filed 2026-10-15, issue #28, the oct-15 wave's 378-file vetting): Poem of the Cid (Blackburn trans., Oklahoma UP) and Return to Nevèrÿon (Delany, Wesleyan UP) refused as duplicates of each other because both amateur conversions carry the SAME junk dc:identifier (the tutorial template's URL, typed isbn by the converter; verified live on calibre 9.15 that a `opf:scheme="ISBN"` URL reports through ebook-meta as `isbn:http://...` while a scheme-less URL reports nothing). The same wave's mint refused seven Heaven Official's Blessing volumes as mutual duplicates on identical stamped titles with distinct valid ISBNs (a slice agent stripped the volume tags; fixed on the stamp side). *(Shipped 3.68.0: every identifier binding is shape-gated at both sites (`_isbn_shaped` for the isbn slot, `_bindable_asin` = B+9 alnum or ISBN-shaped for asin slots; checksum not required, so a same-typo pair still binds), the library keypair searches refuse non-shaped values, and two VALID but different ISBNs are evidence AGAINST a within-batch title+author duplicate: the refusal no longer fires; a valid-vs-junk difference is not evidence and still binds.)*
- [x] **companion script: drive-stamps (the wave stamp driver's fifth hand-rolled copy, and the hand-off bug class has a live incident)** (filed 2026-10-15, issue #29): every large wave since oct11 re-derived the same stamp driver in its working dir (load stamps_final.json, coverage-assert, dispatch stamp_epub/stamp_pdf by format, collect failures into stamp_results.json), and the oct-15 copy adapted from the oct-14 one by string-replace silently matched nothing twice (the format-on-edit hook had reflowed the source) and ran twice against the WRONG WAVE's stamp table; only the hand-written coverage assert kept both runs harmless with zero writes. *(Shipped 3.68.0 as `scripts/drive_stamps.py`: the stabilized contract as flags and asserts -- stamps file + target dir + backup dir + `--format` for the pdf/epub split, `--discard` repeatable, the coverage assert against the ACTUAL target directory before any spawn with the diff named both directions and the loaded size printed, NONE/empty stamps OMIT the flag, per-extension dispatch continuing past failures with the remainder named, stamp_results.json beside the stamps file by default, plan-time row validation (no stampable field, non-list authors) and the backup/results path guards at exit 2, MOBI/AZW3/DJVU entries named skipped since the skill routes them by hand, dry-run default.)*
- [x] **duplicate refusal detail names the matched FILES but not the match BASIS** (filed 2026-10-15, issue #30): CQ#18's fix named the other file(s), but the oct-15 wave's two refusal shapes needed opposite dispositions and the detail did not say WHY a pair matched; diagnosis required hand-dumping both files' dc:identifiers (the junk-identifier pair) and reading the stamps back (the seven-volume pair). *(Shipped 3.68.0: library hit descriptors carry `basis` (the field that matched, in search order: `isbn <value>`, `asin <value>`, `title+author`), within-batch records carry a `batch_duplicate_basis` map keyed by the sibling path with the title+author route naming the ISBN state (`isbn absent on one side`, `isbn differs: <a> vs <b>`), `_screen_duplicates` hands the map out as the refusals dict's `basis` key, and `_duplicate_detail` suffixes it onto both name classes so the decisions_needed entry is self-diagnosing; a refusal without the evidence (a mocked seam) renders byte-identical to before.)*
- [x] **reconcile identifier residual: a stored identifier value with an internal whitespace run never converges, and the package-slot rewrite skips uuid-typed slots** (filed 2026-10-15, issue #31, book #10842 The Chrysalids): two `--apply` passes left the read-back `still differs: identifiers` with a byte-identical OPF. The flagged pair was never the stale uuid slot the issue names (calibre's OPF reader drops uuid/calibre-typed identifiers before ebook-meta prints them, so the slot is invisible to the diff); it was the stored LC call number `PR6045.Y64 C48  1992`, whose double space survives the embed and ebook-meta verbatim while parse_identifiers collapses runs over the whole Identifiers line and the DB side compared raw. *(Shipped 3.69.0: db_record reduces every identifier value through the same norm_text+lower the file side applies -- the comparison form the normalization pass builds its targets with, so diff and writer judge the pair identically -- and the package-slot rewrite now precedes the writer-scheme keep, so a slot typed `uuid`/`calibre` carrying a foreign value rewrites in place to `calibre:<books.uuid>` like every other stale shape; only a slot already in the canonical calibre form keeps, a pass over such a file is byte-identical, and the empty-uuid keep/refusal split is unchanged.)*
- [x] **run phase1 inventories a populated completed/ staging dir** (filed 2026-10-21, issue #32, the oct-21 re-source round): a vetting pass over `~/Downloads` after staging mixed the 184 signed-mint files with the 8 new arrivals into one manifest (192 files) whose signing would double-import 184 books in one phase-2 run with nothing catching it (the duplicate screen reads the library, which does not have them yet); the import-agent handoff's belief that the walk skips completed/ was never code, only an empty staging dir at observation time. *(Shipped 3.70.0: `_inventory` skips any `completed/` under the vetted root, consistent with the `_quarantine` skip, so a post-staging vetting manifest covers the top-level arrivals alone and staged files stay owned by the signed completed/ mint; the vetted root itself is exempt, so the step-5c mint over `~/Downloads/completed` inventories the staging tree unchanged, trailing slash included. The phase-1 skill's inventory note and the issue's workaround paragraph are swept in the same release.)*

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
      Phase 14) so --fts-status retires its raw sidecar read (the one recorded
      exception, formerly modes/fts.py) and the L4 dirtied_formats promotion fires
      with it. Supersedes the L4 "run fts-index verb" candidate. *(Shipped 2026-10-02: the raw
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
- [x] **run device** (USBMS subset): ebook-device ls/df/books/mkdir/cp/cat/rm/touch
      (src/calibre/devices/cli.py:247-390). MTP and the wireless Calibre-Companion
      stack are process-bound and stay excluded from parity. *(Shipped 2026-10-02:
      reads execute immediately, writes are dry-run by default with --apply, and no
      closed-Calibre guard exists because the verb never opens a database.)*
- [x] **--saved-search add/delete/rename**: the calibredb saved_searches CRUD parity
      item; needs cquarry Phase 15's typed `set_preference` writer first. *(Shipped
      2026-10-02 over cquarry 1.24's writer (floor already at 1.24.0): the
      schemawrite dispatcher, one write per invocation, refused company with book
      verbs, and the full rails. The rename refuses to overwrite an existing name,
      where upstream silently does.)*
- [x] **--add-custom-column / --remove-custom-column**: the calibredb schema-CRUD
      parity item; cquarry.write has shipped create/delete_custom_column since 1.20,
      so only the frontend verb is missing (set_custom is already covered by
      --set-column). *(Shipped 2026-10-02 in the schemawrite dispatcher beside the
      saved-search verbs: the full rails, the create side's
      update_all_last_mod_dates_on_start side effect named in the dry run, the
      mark_for_delete purge semantics named, and the NON-NEGOTIABLES labels refused
      at both doors.)*

### Open unowned surface, owners-wanted (not committed; recorded in cquarry's ledger)

- [x] **run news** (candidate): wrap `ebook-convert <recipe> out.epub` for the 1,094
      upstream recipes (recipes/; web/feeds/news.py; the 2026 anti-bot infra rides
      upstream). Becomes committed work only when Brandon wants recipe fetching in the
      automation surface. *(Committed by Brandon's 2026-10-07 green light; shipped
      2026-10-07 in 3.60.0: title-keyed addressing through the documented
      `"<Title>.recipe"` seam with the colliding titles (Deutsche Welle x7, ...) refused
      on explicit asks and skipped+warned in the curated/--all flows; a curated free
      DEFAULT_RECIPES subset as the default batch because all 1,099 is hours of fetches
      against sites that block automation; per-recipe failure isolation with partial
      outputs deleted; resume = existing file unless --force over a date-scoped default
      dest; sequential fetches and the anti-bot caveats on the verb's own help page.
      The count in the original box is already stale: calibre 9.15 enumerates 1,099.)*

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
- [x] ci.yml hardening (found 2026-10-02 in the four-lane verification): the
      merged dependabot PRs had left ci.yml on movable tags while publish.yml
      carried the ledger's SHA pins; ci.yml now pins the same commits, with a
      concurrency block and a job timeout. *(Shipped 2026-10-02 in 3.55.0's
      "Docs and CI truth" bullet; this line exists because the Final-audit
      ledger row's hardening covers publish.yml only.)*
- [ ] The L4 TUI set-write batch session stays GATED (risk surface); the program does
      not ungate it. The per-mode --format corners, the fetch_library_codes SRU fallback, the
      run approve design, and the validate_library allowlist shipped or closed
      on 2026-10-02 (ticked above); the pypi-environment tag policy keeps its
      open box (Brandon's hands), and the TUI set-write batch session stays
      GATED.

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
