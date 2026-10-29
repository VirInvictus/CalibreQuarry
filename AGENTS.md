# AGENTS.md (CalibreQuarry)

Per-project guidance. Overrides the global file where they conflict.

## What this is
A CLI and TUI toolkit for Calibre users who treat their libraries as curated collections. It provides a purely terminal-driven interface for analyzing and exporting from Calibre databases.

## Programmer-facing contract notes (3.77.0 onward, the cquarry 1.28.2 floor, issue #42)

- **The floor is `>=1.28.2`, and the format-row extension guard is
  upstream behavior this repo only pins.** cquarry issue #9: a
  `name` ending case-insensitively with `.<fmt.lower()>` raises
  `ValueError` before any SQL in both `add_format` and `set_format`,
  naming the stem to pass; legitimate dotted stems pass, and an
  `ORIGINAL_<FMT>` row refuses its own `.original_<ext>` spelling.
  This frontend's only paths that can feed such a name are the
  free-text ones (`--add-format`, the TUI Add Format prompt), and
  `run_write` maps the raise to `ERROR: name must be the filename
  stem, not the filename: ...`, exit 1, nothing written -- desired,
  no code change. `run_polish`/`run_merge` take data-row-derived
  paths and stay legal on healthy libraries; on a library still
  carrying a pre-1.28.2 double-extended row they now raise where
  they previously re-registered the corrupt spelling (surfacing it;
  the remedy is fixing the row stem). `TestAddFormatExtensionRefusal`
  (version-gated, skipped below 1.28.2) pins both seams: the API
  refusal + dotted-stem pass, and the verb's ERROR/exit-1/nothing-
  written surface. The local `uv.lock` relock rode this same pass
  (the lock is untracked by the 3.21.0 always-latest policy, but it
  is the state `uv sync` resolves from): it had pinned cquarry 1.23.2
  against a leftover `>=1.22.0` specifier while pyproject said
  `>=1.28.1`, so the floor line and the lock had drifted two bumps
  apart -- after any future floor bump, `uv lock` runs in the same
  pass.

## Programmer-facing contract notes (3.76.0 onward, the screening-and-evidence batch, issues #40-#41)

- **The title judge strips ONE trailing parenthetical, and a
  disagreement over it is work identity.** `_judge_form()` (imprint
  tags, edition tags, then `_strip_trailing_paren`: one
  `(...)`/`[...]` group anchored at the end of the title, content free
  of nested brackets) is the string every compared form starts from
  (`normalize_title`, `_full_title`, `_subtitle`, and so `_core_title`).
  `classify_titles` judges the content: both sides' trailing-paren
  content reads from the NOISE-STRIPPED title (so a known imprint or
  edition tag's bracket never counts as content), a one-sided or equal
  qualifier strips silently and the pair binds (the issue-#40 ACOTAR
  duplicate), while both present, differing, over an equal base is
  `distinct` -- the check sits after the annotation/signature gates,
  which still read the RAW title, so a paren-wrapped "(Book 8)" keeps
  its volume rules (disagreement -> volume_sibling, one-sided arabic ->
  the None gap-fill). A non-trailing bracket is title text; differing
  qualifiers over DIFFERING bases fall through to the containment
  check. Retired pin: "The Mabinogion (Translated by Sioned Davies)"
  vs the bare held title was None, now duplicate; the strip's one
  widening beyond the issue is pinned too ("Foo (Bar)" vs "Foo: Bar" is
  now bare-base related, was None).
- **The evidence row's snips scan the FULL front text, and
  `copyright_snips` joins the field set.** `_epub_front` returns a
  third element: the same first-4-spine-docs text uncapped (`front`
  keeps its exact capped-sample shape: per-doc 1500, total 4000), and
  `_pdf_front` returns its pages-1-4 text uncapped with the row taking
  the 3000-char sample from its head. Both snip scans run over that
  full text (`scan = full_front`; an extraction failure leaves it
  empty, so the failed row snips empty as before). `isbn_snips` gains
  the label-free shape `_ISBN_BARE_RE` (`97[89](?:[ -]?[0-9]){10}`:
  exactly 13 digits on a 978/979 prefix, single-character separators,
  ends at the check digit -- no trailing-hyphen eat) beside the labeled
  shape, deduped through `_snip_digits` (digits plus an uppercased X;
  the lexicographically first spelling of each number wins the
  listing), cap still 6. `_copyright_snips` harvests the "First
  Edition" lines (`_EDITION_SNIP_RE`, optional month-word + year tail,
  edge punctuation stripped) and the LCCN in both printed spellings
  (`_LCCN_SNIP_RE`: the `LCCN` abbreviation OR the spelled-out "Library
  of Congress Control Number" the trade pages actually print), sorted,
  cap 6. Both stay snippet classes: the research pass checksum-validates
  before anything becomes a stamp. The row-shape pin test carries the
  new field.

## Programmer-facing contract notes (3.75.0 onward, the embed-scar batch, issue #37)

- **The embed's NCX half is healed by a third post-embed pass.**
  `normalize_epub_ncx` (epubdates.py) reads the freshly normalized OPF's
  unique-identifier element (`_opf_unique_identifier_value`: the element
  the `unique-identifier` attribute names, entity-decoded, stripped) and
  rewrites every `dtb:uid` meta's content in the NCX the OPF's manifest
  declares (`_ncx_entry_name`: media-type `application/x-dtbncx+xml`,
  the spine's `toc` attribute naming THE one when several, href
  resolved against the OPF's directory, fragment-stripped and
  percent-decoded). Regex surgery like the two OPF passes, precisely
  because a scarred OPF (an unbound ns0 attribute prefix) must stay
  rewriteable. Already-synced files are byte-identical no-ops, and the
  no-sync states (no unique-identifier, the dangling OPF-030 slot, no
  NCX declared or present, no `dtb:uid` meta) are True no-ops because
  none of them can mismatch epubcheck NCX-001. Refusals (unreadable
  zip, no container, no OPF entry) share the two OPF passes' surface:
  reconcile's normalization arbitration (note on in-sync, failure on
  residual) and flush's fail-the-verb arm. Both embed consumers run it
  after the identifier pass.
- **The verify grew a structural half, and a scar never arbitrates
  away.** After the passes, reconcile parses each embedded EPUB's OPF
  and NCX strictly (`epub_parse_errors`; expat's "unbound prefix" is
  exactly the embed-scar fatal class) and rows every refusal as a SCARS
  entry. Block order is RESIDUAL, SCARS, FAILURES, NOTES, DONE. A scar
  is structural, not drift, so the read-back's in-sync arbitration does
  not apply: scars fail the run unconditionally and join the summary as
  "N file(s) structurally scarred (unparseable OPF/NCX, follow-up
  above)". Beneath the block prints the exact scoped remediation, dry
  run as printed: `bindery library <root> --id <scarred ids>
  --fix-unbound-prefix`. A strict parser stops at a document's first
  well-formedness error, so a multiply-scarred document reports one row;
  the bindery probe enumerates the rest. A file that is not a readable
  zip scans EMPTY (the read-back's `<unreadable after embed>` residual
  owns that class; never double-reported). The `dc:date`
  element-prefix class has no bindery repair yet and the printed note
  says so (bindery-cli#22).
- **`run flush` runs the NCX pass too** (same seam, same order; refusal
  fails the verb like the other two) and its report carries the count
  (`epub_ncx_normalized` in JSON, an "NCX dtb:uid normalized in N EPUB
  file(s)." text clause). The scar scan stays reconcile-only: flush
  heals, reconcile verifies.
- **The reconcile floor is (3, 75, 0)** (the two new imported names,
  `normalize_epub_ncx` and `epub_parse_errors`; the 3.63.5
  identifier-synthesis floor is superseded). The import fallback EVICTS
  any cached `cquarry_cli*` from `sys.modules` before its retry: a
  stale installed copy satisfies the first import yet lacks the new
  names, and the failed from-import leaves it cached where a bare
  retry would find it again (a raw traceback instead of the polite
  floor refusal). A second failure (a lone deployed script over a
  stale package, no checkout beside it) leaves the names unbound;
  `imported_version()` then reads None and the floor refusal fires
  fail-closed. `run flush` needs no floor: integrate.py ships inside
  the package it imports.


## Programmer-facing contract notes (3.74.0 onward, the LoC-fallback batch, issue #36)

- **The work-level fallback queries a title fragment and filters records
  by author surname client-side.** `title_fragment()` (cut at the first
  colon, 45-char cap on a word boundary; an empty result falls back to
  the full title) feeds a single-clause `work_query()`, sent with
  `max_records=20`; `parse_mods_for_author()` donates classifications
  only from records whose `mods:name` headings match the book's author
  via `author_matches()` (heading == folded name, == a comma-form
  stored name's pre-comma heading, or a whole-token tail of the folded
  name, so "Le Guin, Ursula K." answers for "Ursula K. Le Guin").
  `author_surname()` is the cache-key half (pre-comma heading of a
  comma form, else last token; "" for empty/`Unknown`), and a usable
  surname GATES the query: unusable-author books skip the fallback
  entirely (the filter could never certify a record). Same-surname
  different-forename collisions pass by design; the work-level caveat
  is the boundary. Cache consequence: `work:` keys are now
  fragment|surname, so pre-3.74 work keys are inert dead weight, and
  two books sharing a fragment never share a verdict (the surname half
  separates them).
- **Only a well-formed `searchRetrieveResponse` can cache.** The shared
  `_parse_srw()` gate raises `SRUResponseError` (a `SRUDiagnostic`
  subclass) for an unparseable body or a wrong root element, and
  `fetch_codes()` retries that class like a network error to None: a
  throttle or proxy page can never fall through the parser as `{}` and
  cache as a permanent miss (the issue's 5,835-poisoned-entries
  mechanism). Genuine zero-hits are well-formed SRU with no records
  and stay cacheable misses. `SRUResponseError` is never
  strict-fatal (the query form cannot produce a non-SRU body; the
  oversize refusal moved to it), so the first-query abort fires only
  on real diagnostics.
- **`--refresh` reads past the response cache.** `read_cache` is `{}`
  under `--refresh` (both ISBN and work lookups) while cache WRITES
  still land on the loaded dict, so a refresh overwrites the poisoned
  entries and the accumulated cache file survives the run (it is NOT
  rewritten to just that run's entries). The DB-side `_needs_query`
  gate stays separate: `--refresh` still widens the target set.
- **The misses worklist appends timestamped sections and keeps the
  newest 8.** `write_misses_worklist(results, path, mode)` appends a
  `# --- run <UTC stamp> (<mode>): N miss(es) of M queried` section
  (`# ids:` comment lines, 16 ids per line, then the
  `id<TAB>isbn<TAB>ddc<TAB>title` rows); pre-marker content from the
  single-run era counts as one legacy section until pruned;
  `MISSES_RUN_SECTIONS_KEPT` bounds the file. Zero misses still
  touches nothing (the return-0/no-file contract). main() passes the
  mode and prints "appended". The append heals a missing trailing
  newline on prior content (a hand-edited file; an unhealed marker
  glues onto the last line and fuses every later read into ONE legacy
  section) and the legacy read carries errors="replace" (a stray byte
  must never kill a finished --apply pass at its tail).

## Programmer-facing contract notes (3.73.0 onward, the cquarry 1.28.1 floor, issue #35)

- **The floor is `>=1.28.1`, and the text-family custom-column writes
  preserve the three non-breaking spaces.** Calibre 9.17's
  `normalize_whitespace` is what 1.28.1's adapters normalize through:
  interior breaking-whitespace runs still collapse to one space and ends
  still strip (a trailing NBSP strips away with them), but U+00A0,
  U+2007, and U+202F survive interior runs in single-valued text,
  series, enumeration, multi-valued items, and appends. This frontend
  passes values verbatim (`--set-column` -> `set_custom_column`, the
  set-mode appends -> `add_custom_column_values`), so the fix is the
  floor bump alone (the issue-#12/#14 precedent): no frontend code
  changed. `TestSetColumnNbspParity` (test_write_flow.py, skipped below
  1.28.1) pins the round-trip through the CLI: an NBSP-bearing value
  stores with the three spaces intact, runs collapsed, ends stripped.
  Enumeration values are adapted before validation, so an NBSP spelling
  still matches no listed value and raises. Previously corrupted rows
  stay as stored; the fix changes new writes only. Also in 1.28.1,
  `add_data_file` writes through a temp file + atomic replace; this CLI
  has no consumer of that verb and it does not drive the floor.

## Programmer-facing contract notes (3.72.0 onward, the cover-import batch, issue #34)

- **`run phase2` extracts the source EPUB's declared cover and passes
  it to `add_book` in the same batch.** The extractor is
  `declared_epub_cover(path)` in `src/cquarry_cli/coverfind.py` (the
  epubdates.py sibling), tried in the issue's preference order: the
  EPUB2 declaration (`<meta name="cover" content="id">` resolved
  through the OPF manifest item that id names), then the EPUB3
  declaration (the manifest item whose space-separated `properties`
  carry `cover-image`), then the largest archive entry whose basename
  contains "cover" (case-insensitive) and whose extension is
  .jpg/.jpeg/.png. Preference is TIER-ordered, never size-ordered
  across tiers: a resolving declaration beats any named-cover image at
  any size; only the fallback tier compares sizes, and its name gate is
  what keeps a distributor logo (never cover-named) from being
  catalogued as the art (the issue's observed wrong-art trap). The
  fallback scans archive NAMES, not the OPF manifest, so it also finds
  covers the OPF never listed. The wiring gates on the entry's format:
  EPUB only, and a mocked seam test pins that non-EPUB imports never
  open the extractor.
- **The extraction is fail-open by contract, never a refusal.** Every
  candidate must resolve to real archive bytes AND sniff as JPEG/PNG
  (`cquarry.helpers.sniff_image_format`, the same gate `add_book`
  applies sniff-or-raise); a declared SVG, a declared path absent from
  the archive, a corrupt zip, a missing rootfile, or a malformed OPF
  falls through to the next tier or returns None, and a file with no
  usable cover anywhere imports uncovered EXACTLY as before
  (has_cover falsy in the DB; the library linter's EVERY_BOOK_COVER
  check is the surfacing mechanism, per the issue). Hrefs resolve
  against the OPF's directory inside the zip, percent-decoded,
  fragment-stripped. The manifest shape is unchanged: a cover that
  lands appends `declared EPUB cover applied` to the entry's existing
  `import.fixes` list; an absent cover records nothing (byte-identical
  to 3.71.0's manifest). Fixture note pinned by tests: `add_book`
  inserts without the has_cover column and the test schema carries no
  DEFAULT 0, so the uncovered assertion is falsy (NULL), not literal 0.

## Programmer-facing contract notes (3.71.0 onward, the notation-and-initials batch, issue #33)

- **ISBNs compare through their 13-form, and the conversion is
  shape-gated.** `_isbn13_form(value)` (screen_duplicate.py) returns
  the canonical comparison value: a shaped 13-form passes through, a
  shaped 10-form converts through the 978 Bookland prefix with a
  recomputed check digit, and ANY unshaped reduction is None (the
  `_ISBN_SHAPE_RE` gate runs BEFORE the arithmetic; a reduction like
  `X123456789` used to crash `int()`). No checksum requirement,
  matching the issue-#28 gate: a same-typo 10-form pair still converts
  to the same (wrong) 13-form and binds, exactly as it bound before;
  a 979-prefixed 13-form has no cross form. `_isbn_cross_form()` is
  the opposite-notation search spelling (10 -> 978-13, 978-13 -> 10
  with the X check digit possible). `_isbn_valid` now carries the same
  shape gate (a non-shaped reduction is simply not valid, never a
  crash).
- **The within-batch bind compares canonical forms; the evidence rule
  survives.** `isbn_bound` replaces the old `same_isbn`/`junk_isbn`
  pair: both isbns present, both shaped, canonical 13-forms equal.
  Raw-equal pairs quote `isbn <value>`; digit-differing cross-notation
  pairs quote `isbn <13-form raw>, 13-form of <10-form raw>`
  (`_isbn_pair_basis`, order-free, 13-form side always first);
  digit-IDENTICAL punctuation variants (`978-0-14-044568-8` vs
  `9780140445688`, which 3.70.0 read as a conflict) bind with the
  plain single-value basis. `isbn_conflict` still requires raw
  difference plus both checksums passing, so genuinely different
  editions stay suppressed; identical junk pairs (both canonical None)
  keep the plain `title+author` basis.
- **The library keypair search runs the cross notation second.** After
  the direct `isbn:VALUE` search, `_library_hits` searches
  `isbn:<cross form>` when one exists; the hit's basis comes from
  `_isbn_pair_basis` (the direct hit keeps `isbn <value>`), and `seen`
  dedup stays first-route-wins, so a book held in both notations
  reports the direct basis.
- **The first-author leg compares middle-initial-stripped token sets.**
  `_same_author_tokens(a, b)`: identical folded names match outright
  (the non-Latin names whose token form is empty still bind their own
  spelling, the 3.70.0 semantics), otherwise the `_author_tokens()`
  frozensets (folded, split on non-ASCII-alphanumerics, single-letter
  tokens DROPPED) must be equal and non-empty. "Christopher W.
  Alexander" == "Christopher Alexander"; "Ursula K. Le Guin" ==
  "Ursula Le Guin"; "Karl Marx" != "Groucho Marx"; initials-only names
  match only themselves. `_screen`'s within-batch `same_author` uses
  it on FIRST authors; the filename-seed branch is unchanged.
- **The library door's candidate supply is surname-wide, then gated.**
  `_author_surname()` returns the first author's last folded token when
  it is a real word (more than one letter; never a one-letter
  substring scan over the library), `_library_hits` searches
  `author:<surname>` with it, and every candidate passes
  `_same_author_tokens(first, any of the book's authors)` BEFORE
  classify_titles judges (the any-author check keeps the engine's
  author-search semantics, where a book matches on any author value).
  No usable surname (initials-only, non-Latin) falls back to the exact
  `author:=first` search, the 3.70.0 supply. Duplicate verdicts bind
  with basis `title+author`; related verdicts still surface as
  related candidates, now also gate-checked.

## Programmer-facing contract notes (3.70.0 onward, the staging-dir batch, issue #32)

- **`_inventory` skips the staging directory name `completed/` under the
  vetted root, and the root itself is exempt.** Staged files are the
  signed completed/ mint's job (step 5c mints over
  `~/Downloads/completed`), never the vetting pass's: the skip uses the
  same slash-anchored substring semantics as the `_quarantine`/
  `_stamp_backups` skips (`"/completed" in rel`, so a nested
  `completed/` is staging-shaped too) but only below the root, judged
  on abspath-normalized paths, so `cquarry run phase1
  ~/Downloads/completed` and its trailing-slash form still inventory
  the staging tree itself. The skip is silent (the `_quarantine`
  precedent). The inventory's consumers shrink with it (`_pdf_battery`,
  the duplicate screen); audit_drm's directory scan still walks the
  whole tree, and its extra verdict rows are inert because verdicts are
  consumed only per inventoried entry. A vetting manifest minted after
  staging now covers the top-level arrivals alone; the union of staged
  + new remains the completed/ mint's shape, minted over the staging
  root after the winners are staged.

## Programmer-facing contract notes (3.69.0 onward, the identifier-residual batch, issue #31)

- **Identifier values compare whitespace-collapsed on BOTH sides of the
  reconcile diff, and `db_record` is where the DB side collapses.** The
  stored `lcc` `PR6045.Y64 C48  1992` (double space) rides the embed and
  ebook-meta verbatim (calibre's OPF reader strips only element edges),
  but `parse_identifiers` collapses runs over the whole Identifiers line;
  the raw `.strip().lower()` DB compare could never match it, so the book
  reported `identifiers` drift on every pass and no writer could close it.
  `db_record` now returns `norm_text(val).lower()` per value, the same
  reduction `parse_identifiers` applies file-side and the one
  `canonical_dc_identifiers` builds its targets with (`_norm_ident`), so
  one pair has one comparison form. Consumers of `rec["identifiers"]`:
  `diff_fields` (both directions) and the normalize call, both of which
  want exactly this form; inserts into files were already collapsed by
  the writer and stay collapsed.
- **The package-slot rewrite precedes the writer-scheme keep.**
  `canonical_dc_identifiers` judges the package unique-identifier slot
  before `_WRITER_SCHEMES`: any slot content that is not already
  `<scheme="calibre"><books.uuid>` rewrites in place, so a slot typed
  `uuid` (or `calibre`) carrying a FOREIGN books-uuid converges like
  every other stale shape (the issue's live element; the keep used to
  shield it forever). The keep still covers every NON-package
  calibre/uuid element, and the empty-uuid edge is unchanged: with no
  books.uuid a writer-scheme slot keeps standing (the read seam never
  surfaces it) while any other stale slot still reaches
  `_rewrite_package_identifier` and refuses the pass. A pass over a file
  whose slot is already canonical is byte-identical without a rebuild
  (the keep fires before `changed` is set), so convergence is one
  one-time scheme flip at most.

## Programmer-facing contract notes (3.68.0 onward, the duplicate-triage batch, issues #28-#30)

- **Identifier bindings are shape-gated, and the gates live in
  screen_duplicate.py.** `_isbn_shaped` (10 or 13 ISBN characters once
  everything outside [0-9Xx] strips -- `_isbn_digits`, the house
  normalization, so dashed/spaced/dotted producer spellings compare
  equal; checksum NOT required: a same-typo pair out of one rip's
  source is still binding evidence), `_isbn_valid` (both lengths,
  checksum strict, the validate_metadata rule), and `_bindable_asin`
  (B + 9 alphanumerics case-insensitive, OR ISBN-shaped: the pre-2001
  ASINs are ISBN-10s and rips file ISBN-10 values under asin) gate
  EVERY identifier binding at BOTH sites: `_screen`'s within-batch
  isbn/asin intersection and `_library_hits`' two keypair searches (a
  junk URL in the isbn or asin slot searches nothing, so a held
  tutorial-conversion twin cannot be matched by its own junk). The
  issue-#28 mechanism, verified live on calibre 9.15: an amateur
  conversion's `<dc:identifier opf:scheme="ISBN">http://...`
  (the tutorial template's URL as the package unique-identifier)
  reports through ebook-meta as `Identifiers : isbn:http://...` and
  `_embedded_fields` collects it verbatim into the `isbn` slot; a
  scheme-less URL identifier reports nothing (calibre drops
  scheme-less values), so the junk only ever arrives TYPED.
  `_embedded_fields`' record shape is unchanged (raw values, display
  and evidence); only the binding is gated.
- **Two VALID but different ISBNs suppress the within-batch title
  refusal.** `_screen`'s title+author duplicate path refuses only when
  NOT `isbn_conflict` (both isbns present, different, BOTH
  `_isbn_valid`; the 2026-10-15 seven-volume stamp collapse). A
  valid-vs-junk difference is not evidence and still binds. THE
  ACCEPTED TRADE: a same-work, same-title pair that is genuinely two
  EDITIONS (paperback vs hardcover re-rip) also stops refusing -- it
  was a refusal in 3.67.0 and is now a silent pass; the backstops are
  the library screen (a held copy still hits) and phase 3's
  byte-identity floor (identical bytes refuse at add_book). The
  library-side title+author path is unchanged (the library hit
  descriptor carries no ISBN to compare), and the `related`/
  `volume_sibling` advisory classes are untouched: only the duplicate
  refusal reads ISBN evidence.
- **Every binding carries its evidence, and the refusals dict has a
  third key.** `_library_hits` descriptors gain `basis` (the first
  route that found the book: `isbn <value>`, `asin <value>`, or
  `title+author`; `_describe` passes it through). Within-batch records
  gain `batch_duplicate_basis` (sibling path -> basis string; the
  exact strings are `isbn <value>`, `asin <value>`, `title+author` (plain,
  the shared-identifier-equality state the shape gate refused to bind
  on -- identical junk values are NOT "differs"),
  `title+author, isbn absent on one side`, `title+author, isbn
  differs: <a> vs <b>` -- comma form, no nested parens). run.py's
  `_screen_duplicates` hands the map out as `refusal["basis"]` beside
  the issue-#18 `library`/`batch` split, and `_duplicate_detail`
  suffixes `(matched on <basis>)` onto library hits and `(<basis>)`
  onto batch siblings; a refusal without the basis (a mocked seam)
  renders byte-identical to 3.67.0, and the class-breakdown-unavailable
  neutral is unchanged. The screen's text report shows the evidence
  too (`on <basis>` after the library id, `(<basis>)` after the batch
  sibling).
- **`scripts/drive_stamps.py` is the wave stamp driver; its coverage
  assert is the incident protection.** Map shape: filename basename ->
  {title, authors (list), publisher, isbn, cut, notes}; None/""/"NONE"
  OMITS the flag (never the literal string); cut/notes ride for the
  record and never stamp (stamp --author REPLACES the list, so the
  cut is already applied in authors). The coverage assert runs after
  plan-time row validation and before any spawn: (map keys minus
  `--discard`) == the target dir's TOP-LEVEL ebook files
  (.epub/.pdf/.mobi/.azw3/.djvu), both diff directions named, exit 2;
  a discarded name still on disk is an extra. Rows with no stampable
  field (or a non-list authors) refuse at exit 2 before coverage;
  unknown fields warn and ride. Dispatch is stamp_epub.py (`.epub`)
  and stamp_pdf.py (`.pdf`) ONLY, sorted order, 300s per file,
  continuing past failures with the remainder named (exit 1);
  everything else in the map is a named skip (the phase-1 skill
  routes MOBI/DJVU by hand) and `--format epub|pdf` skips the other
  format named, coverage still whole. Defaults: dry-run (the tools
  run without `--apply`), results beside the stamps file
  (`stamp_results.json`; the association the oct-15 hand-rolled copy
  got wrong), `--results`/backup-dir refused inside the target, the
  results path refused equal to the stamps map. The spawn seam is
  `_spawn(cmd)` (text-mode, errors="replace", the AST tripwire's
  blanket); tests mock it and drive `main()` over a fake wave dir
  (`TestDriveStamps`).

## Programmer-facing contract notes (3.67.0 onward, the triage batch, issues #25-#27)

- **audit_isbns.py's machine formats own stdout exclusively.** Under
  `--format json` and `--format tsv` the script's stdout carries ONLY
  the payload: the vir_tui banner and the four setup-error prints ride
  stderr (`contextlib.redirect_stdout` over `print_header`, whose
  tqdm.write resolves sys.stdout at call time; a `prose` stream for the
  error prints), so `json.load` on captured stdout and a `tsv` redirect
  work without offset hacks. Text mode is byte-identical to before
  (banner and prose on stdout). `--json FILE` writes the same payload
  `--format json` prints (trailing newline included) beside ANY
  format's human output, the check_pdf.py `--json FILE` shape, with no
  path guard (matching that sibling deliberately); the exit contract is
  untouched and `--json` never changes what stdout carries. Pinned by
  `TestMachineOutput` driving `main()` over a fixture library whose
  files print their own stored ISBNs.
- **The fetched-record apply seams are field-limited by contract, and
  a test now refuses to let that rot.** `_apply_opf` writes
  title/authors/publisher/pubdate (date-only)/comments/identifiers and
  structurally ignores everything else a fetched OPF carries;
  `_apply_backfill` writes only its plan's requested fields; phase 2's
  import batch clears tags and rating BEFORE any fetch exists to
  re-add them. `TestApplyOpfWritesNoReaderCuratedFields` pins the
  seam: `dc:subject` elements and rating meta in a contaminated OPF
  never land on the book, pre-existing curated rows survive untouched,
  and nothing is added from zero. Issue #26's injections came from the
  manual import passes' own metadata download (calibre's source plugins
  write tags and average rating by default), never from this repo.
- **The phase-2 cc6 stamp is proven live; the wholesale-'Anna's
  Archive' waves never ran the verb.** All 17 manifests of the
  2026-10-10..14 waves carry zero `imported_id`s (the field phase 2
  itself writes and saves), so the six-wave symptom was the manual
  import path's own stamping, not this verb. The live drill (scratch
  sandbox library, real ebook-meta/calibredb/bindery, cquarry 1.28.0):
  phase-1 mint -> `run sign` -> `run phase2 --backup-dir` over three
  fixture EPUBs (an `-- Anna's Archive` trailer, a `(z-library.sk)`
  marker, a bare name) landed per-book provenance exactly
  (Anna's Archive, Z-Lib, Z-Lib) with `#audience` stamped. When a
  future wave reports wrong cc6 on imported books, check the manifest's
  `import.imported_id` fields FIRST: absent means the verb was not in
  the loop.

## Programmer-facing contract notes (3.66.0 onward, the wave-lessons batch, issues #21-#24)

- **`scripts/extract_stamp_evidence.py` is the research pass's evidence
  feeder, and its OPF parsing is attribute-order-independent by
  construction.** One JSON row per file, bare list (the wave consumers'
  shape): `file`/`path`/`fmt`, `seed` (the ebook-meta display dump
  restricted to title/author(s)/publisher/published/languages/isbn/
  identifiers -- raw display fields, NOT phase 1's filtered stamps: the
  research pass wants the ISBN/identifiers lines the stamp seeding
  refuses; a calibre-refused read is seed=None + error, the run.py
  traceback guard), `opf_ids` (first 8 dc:identifier values),
  `front` (first 4 spine docs joined " | ", or pdftotext pages 1-4;
  other formats carry no front and are NOT census-judged), `isbn_snips`
  (front-scanned shapes, <= 6; the label gap crosses the
  "ISBN-13:"/"ISBN 13:" spellings), `provenance` (run.py's
  `_provenance_from_filename`, imported at module load behind a
  3.66.0 floor check -- a stale user-site module still seeds None,
  so below the floor the vendored fallback runs; the fallback's
  default MUST track the bare-name ruling), `error` (also set when
  ebook-meta exits nonzero, the run.py guard).
  The manifest/spine parse through ElementTree local names (`id` and
  `href` are separate attributes of each `<item>`) -- the oct-14
  hand-rolled copy's id-first regex emptied front for 87 of 111 EPUBs
  and is the bug this script retires. The thin-front census (extracted
  rows with front under `--thin-chars`, default 200) is the
  extraction's quality gate: listed by name, exit 1. Directory inputs
  list TOP-LEVEL files only (a recursive walk would sweep a prior
  phase's `_quarantine/` in); `--out` is required and refused inside
  any scanned directory or on any scanned file (reads only). Exit
  codes: 0 clean extraction, 1 thin rows, 2 usage.
- **stamp_pdf's one-shot qpdf recovery now covers two write-failure
  classes.** The recovery block fires on `_is_parse_crash(proc) or
  _is_xref_failure(proc)`: a Perl-space parse error (3.54.0, 2026-09-26)
  or an output line matching `_XREF_FAIL_RE` ("Invalid xref", "xref
  [table] not found", case-insensitive, stderr and stdout; issue #21,
  the Language Implementation Patterns live shape). Shared shape:
  exactly one `qpdf --replace-input` rebuild, page count verified
  preserved (a changed count restores the backup and keeps the
  failure), one retry, and the sort-erase detection re-reads after the
  rebuild. Any other rc != 0 shape is an immediate STAMP_FAILED, and a
  failed retry is never rebuilt again. This supersedes the 3.54.0
  clause "any other nonzero exit (no Perl-space signature) never
  recovers". check_pdf's exit-code-only reading is unchanged (the
  issue named the battery gap cosmetic once the stamp-side heal
  landed).
- **A stamp_pdf verify mismatch is arbitrated by exiftool before it is
  a failure.** `_verify`'s failed fields go through
  `_arbitrate_with_exiftool`: one `exiftool -j -a -G1` read of
  `_ARBITER_TAGS` (author, creator, keywords, publisher, title --
  flattened by bare tag name, so any group's copy passes; list values
  count element-wise and as their " & " join) against the stamped
  values. Fields exiftool holds were a blind ebook-meta read (the
  2026-10-10..14 class) and the stamp verifies with a DIM note naming
  the blind fields; fields exiftool also fails to hold print
  "read-back and exiftool both disagree on: ..." and fail. An empty
  arbitration readback (exiftool died, unparseable JSON) confirms every
  disputed field -- fail-closed, a blind arbiter never certifies a
  stamp. The refused-set return contract of the mocked seam is
  unchanged; the arbitration rides the same `_run_exiftool` seam and
  its probes carry no `=`, so write-call counters in tests never see
  them. EPUB verification (stamp_epub) arbitrates nothing, by the
  issue's own evidence.
- **`_provenance_from_filename` has no None case.** The standing
  2026-10-12 ruling: marker files seed their site, bare names seed
  `Z-Lib` (`_PROVENANCE_BARE_DEFAULT`; issue #23). Supersedes the
  3.35.0 "absence of evidence is not a source" rule. Manifests minted
  before 3.66.0 may still carry None (signed history, untouched);
  extract_stamp_evidence's vendored fallback carries the same default
  and must keep step.

## Programmer-facing contract notes (3.65.0 onward, the version-skew batch, issue #20)

- **Every `scripts/` entry point announces its toolchain, and
  reconcile_file_metadata.py floor-checks the cquarry_cli module it
  actually imported.** The shared helper is `scripts/version_guard.py`
  (stdlib-only, no main; db_util.py's sibling, not an entry point).
  `announce()` prints ONE stderr line, `toolchain: cquarry X, vir_tui Y,
  cquarry_cli Z`, called right after `parse_args()` in every script's
  main() (check_pdf and comments_census included: the line is
  a uniform surface an agent can rely on). The version source prefers
  `sys.modules[...].__version__` (what the process actually imported; a
  checkout fallback's src/ copy reports itself) and falls back to
  importlib.metadata over `WORKSPACE_PACKAGES` (module -> dist names:
  cquarry->cquarry, vir_tui->vir-tui, cquarry_cli->calibrequarry);
  `absent` for a package neither imported nor installed; it never
  imports anything and never writes stdout, so machine-report contracts
  (check_pdf's JSON) stay clean. Every script imports the helper
  through a try/except ImportError whose fallback is a no-op
  `announce` (a lone deployed copy with no siblings -- the documented
  deploy-a-copy pattern -- runs exactly as 3.64.0 did, announcing
  nothing); reconcile's fallback additionally stubs
  `imported_version` (None) and `floor_refusal`, and that stub prints
  "NOTE: version_guard.py is not beside this script; the cquarry_cli
  floor check was skipped." once at the gate, so the strong half of
  the fix degrades visibly, never silently.
- **The floor is `(3, 63, 5)` and the honest version is the imported
  module's own `__version__`.** reconcile captures
  `_CQUARRY_CLI_VERSION = imported_version("cquarry_cli")` at its
  epubdates import seam (the try/except decided WHICH copy runs; the
  dist metadata can lag a checkout, so it is never the answer), and
  main() refuses through `floor_refusal()` before the tool probes and
  the library resolution: exit 2, naming the version found, the floor,
  the why (the identifier pass's default-namespace synthesis moved in
  3.63.5; an older module refuses files the current code handles
  silently -- the issue-#20 skew symptom), and the fix command
  (`pip install --user --upgrade calibrequarry`). An unparseable or
  missing version refuses too (fail-closed, the pgrep-guard shape).
  `version_tuple` compare is plain tuple ordering. New script-side
  dependence on moved module internals means bumping that floor in the
  same commit; the other fifteen scripts announce but floor nothing
  (none of them imports cquarry_cli).
- **reconcile's apply tail arbitrates a normalization refusal by the
  read-back re-diff.** Both normalization loops now append
  `(rec, fpath, line)` to a held `normalization_failed` list (no inline
  stderr print, no immediate `ok = False`); after `verify_embedded`
  returns, refusals on files in the residual set (keyed `(book id,
  "EPUB")`; unreadable read-backs are residuals) print to stderr and
  join `failures` with `ok = False` as before, and refusals on in-sync
  files become `normalization_notes` lines with the literal suffix
  " (already in sync)" -- printed as a `NOTES (N)` block after
  FAILURES and before the summary, never counted as failures, never
  blocking exit 0; the DONE line appends the demoted count when notes
  exist. Block order is fixed: RESIDUAL, FAILURES, NOTES, then
  DONE/COMPLETED WITH ERRORS (the summary's "(listed above)" and
  "(noted above)" both point backwards). The issue-#6 durable-record
  contract is unchanged for real failures; what changed, pinned by the
  rewritten tests: a refused normalization over an in-sync read-back
  used to fail the run, and is now a note at exit 0.

## Programmer-facing contract notes (3.64.0 onward, the evening-wave batch, issues #17/#18/#19)

- **The title judge strips ordinal edition tags, and the refusal detail
  tells the two refusal classes apart.** `_EDITION_TAG`
  (screen_duplicate.py) removes `, Nth Edition` / `: Nth Edition` /
  `Nth ed.` forms (ordinal digit + `edition`/`ed`, anchored to where an
  annotation can end: segment end, colon, comma, semicolon, closing
  bracket) at every comparison site via `_strip_noise` (imprint strip,
  then edition strip, so a tag the imprint removal exposes still
  fires); prose ("the 2nd edition of...") and non-edition shapes
  (`Version 2.0`) never strip. `_annotations` and `_volume_signature`
  still read the RAW title, so the series rules are untouched, and both
  curated sets (`_IMPRINT_TAG`, `_EDITION_TAG`) grow as rips surface new
  shapes. `_screen_duplicates` grew an optional `refusals` dict beside
  `notes` (path -> `{"library": [hit descriptors], "batch": [paths]}`);
  `_duplicate_detail` renders it as "matches an existing library book:
  #id title", "matches another file in this batch: file", or both. A
  refusal missing the breakdown (a mocked seam) reads the neutral
  "class breakdown unavailable": the old hard-coded "matches an
  existing library book" was the lie triage chased (issue #18). The
  refused-set return contract is unchanged.
- **The mint records a preempted lossy consent instead of letting the
  decision vanish.** `_note_preempted_lossy` (run.py, after the bindery
  mirrors in `run_phase1`) scans the earlier SAME-DAY manifests in the
  library's manifests dir (the `{today}-batch*.json` names only): a file
  recorded there with `lossy.flagged` and unapplied lossy repairs that
  THIS mint reads not-lossy-flagged gets a `repairs` note naming the
  earlier manifest, the pending strip summaries, and the cause (a
  repair sweep's `--all` applies the lossy strips with the structural
  set, so the consent gate was preempted; issue #19). Gates: a fresh
  battery must have run (`bindery_shape` non-empty; without one there
  is no evidence), the fresh read must actually be clean (a
  still-flagged file keeps its standing decision), and no same-day
  manifest may already record the path's lossy repairs as APPLIED
  (there the consent path ran and its own record is the history; the
  preemption claim would be a false WHY). Informational by
  design (the strips already happened), so it is a note in `repairs`,
  never a blocking decision; the phase-1 summary prints the preempted
  count. Same-day means the `{today}-batch*` names against
  `_now_stamp()` at scan time: a mint straddling midnight can miss a
  note for a manifest recorded minutes earlier (an absent note, never a
  wrong one).

## Programmer-facing contract notes (3.63.7 onward, the duplicate-screen keypair and imprint batch)

- **The duplicate screen matches ASINs and strips imprint tags.**
  `_embedded_fields` records carry `asins` (values of `asin`/
  `mobi-asin`-typed identifiers; both spellings, other types out), and
  `_library_hits` searches each via the exact-value keypair
  `identifiers:=VALUE` (cquarry's search grammar; matches ANY identifier
  type case-insensitively, so file-side `asin` meets library-side
  `mobi-asin`). Within-batch, a case-folded ASIN intersection binds
  `batch_duplicates` like a shared ISBN. `normalize_title` strips a
  curated paren-scoped imprint-tag set before judging (the list lives in
  `_IMPRINT_TAG` and grows as rips surface new tags); `_annotations` and
  `_volume_signature` still read the UNstripped title, so volume logic
  is untouched. The translator-form-author-vs-Anonymous gate is
  deliberately unsolved: the keypair path (ISBN, ASIN) is what surfaces
  those, and the phase-1 skill's hand-check covers the residue. Issue
  #13's live Mabinogion shape is pinned end to end through the real
  search engine.

## Programmer-facing contract notes (3.63.6 onward, the unreadable classification)

- **Unreadable phase-1 quarantines are `UNREADABLE`, not `DRM: ERROR`,
  and `_drm_verdicts` carries the audit's parse failure.** ERROR rows
  come back as `ERROR: {detail}` (`ERROR: bad zip: File is not a zip
  file`); every other status stays the bare class. The quarantine gate
  parses the class as the first token before the colon, so bare
  `ERROR`/`DRM` values (the mocked-seam shape) gate unchanged. The
  reason for an ERROR row is `UNREADABLE` + whatever followed the class
  (the audit's detail rides verbatim into the quarantines list, the
  `manual_repair` decision detail, and `entry["checks"]["drm"]`); DRM
  rows keep `DRM: DRM`. Quarantine behavior is unchanged: same
  is_problem set, same dry-vs-move rails. Issue #15's live class (a
  0-byte download read as "DRM: ERROR") is what this retires.

## Programmer-facing contract notes (3.63.5 onward, the default-namespace EPUB3 fix)

- **normalize_epub_identifiers no longer refuses the default-namespace
  EPUB3 OPF form, and its binding rules split in two.** The OPF
  namespace prefix is SYNTHESIZED when the surgery must name it (a
  scheme-less package slot to rewrite, or any insertion) and none is
  bound anywhere in the document: `_declare_namespaces` writes
  `xmlns:opf="http://www.idpf.org/2007/opf"` on the `<metadata>`
  opening tag (inside the block the surgery already owns, in scope for
  everything it emits, and found by `_opf_prefix_of` on the next pass,
  so converged files stay byte-identical). The dc binding check is
  document-scope: the literal binding may sit in the block OR anywhere
  before it (an ancestor; in an OPF, the `<package>` tag); a dc
  binding nowhere in scope still refuses (dc-namespaced content
  without the binding is malformed XML no embed could have produced).
  Removal-only passes synthesize nothing (`needs_opf_prefix` gates on
  the surgery actually naming an OPF-namespaced attribute), so the
  byte-exact no-churn contract holds there. The same scope hoist is in
  `canonical_dc_dates`'s insertion precondition. The 3.63.3 note's
  "insertions need both bindings" refusal is superseded for the OPF
  prefix and widened for dc.

## Programmer-facing contract notes (3.63.4 onward, the cquarry 1.28.0 floor)

- **The floor is 1.28.0, and case-variant entity writes consolidate.**
  A name matching an existing entity row only by CASE is a real write
  now (upstream `change_case`, the `allow_case_change=True` default):
  the shared row re-cases, every linked book is touched + queued for
  OPF resync, and the setter returns changed=True. Consequences: batch
  summaries count a re-case as "applied" where 1.27 read "already-so",
  and a re-case dirties MORE books than the calling one (the spelling
  propagating). Covered: `set_publisher`, `set_series`, `set_authors`,
  `add_tag`, and the text-family custom-column writes; authors keep
  hand-tuned `authors.sort` and apply upstream's strcmp rule to each
  linked book's `author_sort`. `add_tag` stops INSERTing duplicate
  case-variant rows; `remove_tag` gained a NOCASE fallback.
  `--rename-entity` remains the tool ONLY for PRE-EXISTING
  case-duplicate rows: cquarry no longer creates them and deliberately
  does not heal them. `TestSetPublisherCaseChangeParity`
  (version-gated) pins the seam at the verb.

## Programmer-facing contract notes (3.63.3 onward, the identifier-set convergence)

- **The embed writer does NOT regenerate the OPF's identifier set, and
  the post-embed pass is what converges deletions.** The 3.63.0 note
  below says "embed_metadata regenerates the OPF wholesale"; that was
  wrong and is superseded (issue #11's reopen, verified live on
  calibre 9.15/9.16 against `opf2.py`'s `set_identifiers`): the writer
  preserves the package's unique-identifier `dc:identifier` element
  verbatim (reusing its text only when the element's own scheme names
  a DB type) and never removes scheme-less elements, so a deleted type
  in either shape survived every re-embed and `--apply` oscillated.
  `normalize_epub_identifiers` (in `epubdates.py`, the dc:date pass's
  module; the zip rewrite is now the shared `_rewrite_opf`) runs after
  every EPUB embed in reconcile `--apply` AND `run flush`, and forces
  the file's identifier set to the DB's by the same rule `diff_fields`
  applies: matches kept (first wins), `FILE_ONLY_IDENTIFIERS` and the
  writer's `calibre`/`uuid` schemes kept (a DB row typed `calibre`/
  `uuid` is satisfied like any other entry, never re-inserted),
  everything else removed, unsatisfied DB entries inserted after the
  last identifier, and a stale package slot rewritten in place to
  `calibre:<books.uuid>` (id attribute and position kept; the
  reference never dangles). Scheme-less elements NEVER satisfy an
  entry: the reader check_isbn-normalizes scheme-less `urn:isbn:`
  values, so they are removed and the entry inserted scheme'd (raw
  round-trip). Refusals are scoped to what the surgery must name
  (insertions need both bindings; removals/rewrites proceed without),
  already-canonical files are byte-identical no-ops, and the read-back
  re-diff stays the arbiter. `db_record` selects `books.uuid`; flush
  JSON carries `epub_identifiers_normalized`. A DB row typed outside
  the reader's surface (`calibre`, `uuid`) reads as permanent drift:
  no writer can emit a value the reader never reports; the residual
  names it.

## Programmer-facing contract notes (3.63.2 onward, the cquarry 1.27.0 floor)

- **The floor is 1.27.0, and the 3.63.1 byline-repair caveat is
  retired.** The 3.63.1 note below says a reused piped row's sort
  repair takes the piped form, never the display comma name; that was
  true of 1.26.3 exactly and is WRONG on 1.27.0 (the display-form
  parity wave): `set_author_sort_name`, `rename_entity` (old-name
  lookup and merge detection), `set_link_map(kind="authors")`, and
  `remove_entity_everywhere` all resolve the display comma form now.
  The frontend's one consumer of the wave is `--rename-entity
  authors`, and `TestRenameEntityDisplayFormParity` (version-gated)
  pins both addressing directions at the verb: the display comma form
  reaches a piped-stored row, and the raw stored spelling still wins
  first, deliberately. Lane details with no frontend seam:
  `set_authors`/`add_book` display-normalize pipe-bearing inputs, and
  piped-stored authors' on-disk layout takes the display form on each
  book's next write (stored paths match until then, so the tree audit
  and reconcile are unaffected).

## Programmer-facing contract notes (3.63.1 onward, the cquarry 1.26.3 floor)

- **The floor is 1.26.3 for the piped-author resolution.** Every author
  write seam (`run phase2`'s import over verbatim creator strings, the
  set-write fixes path, `add_book`, the `--set-authors` verb) relies on
  cquarry resolving incoming author names through BOTH spellings: raw
  first, then the piped form the create path stores. Below 1.26.3 a
  comma-bearing byline over a piped artifact row dies on
  `UNIQUE(authors.name)`. `set_author_sort_name` still resolves STORED
  spellings only: repairing a reused piped row's sort takes the piped
  form (`'Name| Piped'`), never the display comma name.
  `TestSetAuthorsPipedRowParity` (version-gated) pins the seam.

## Programmer-facing contract notes (3.63.0 onward, the issue-#10/#11 fixes)

- **fetch_library_codes refuses placeholder classifications at every
  seam.** `valid_lcc`/`valid_ddc` (the issue's classification-shape
  regexes plus the MLCM/MLCS/CPB/`IN PROCESS` placeholder gate, which
  catches shapes the regex alone admits like `MLC 2024/...`) run in
  `parse_mods` per element (a junk 050 on the first record is skipped;
  a later record's real classification still wins), at both cache-read
  sites, and once as a catch-all at the single consumption point
  before values become writes/report/worklist (the point a mocked or
  future seam cannot bypass). Junk reads as a miss everywhere: nothing
  written, worklisted, and a sanitized junk-only entry fires
  `--sru-fallback` exactly like a fresh miss. Pre-guard cache entries
  need no bump; the read-side sanitize covers them.
- **reconcile's identifier diff is two-directional.** A file-side
  identifier type the DB lacks is drift (issue #11: after
  `clear_identifier` the OPF value stood and reconcile read in-sync),
  because `calibredb embed_metadata` regenerates the OPF wholesale and
  `--apply` removes it. `FILE_ONLY_IDENTIFIERS` (`uri`, `ean`) stays
  exempt: every embed re-adds the book uuid and publisher `ean`s were
  already recorded as legitimate, so strict equality is still not the
  rule and a freshly embedded book still reads in sync.
  `verify_embedded` shares `diff_fields`, so the post-embed read-back
  judges the removal direction by the same rule. PDFs are unchanged:
  their read-back never compares identifiers.

## Programmer-facing contract notes (3.62.2 onward, the decode blanket)

- **Every text-mode subprocess call carries errors="replace", and an
  AST tripwire enforces it.** `tests/test_scripts.py`'s
  `test_every_text_spawn_carries_errors_replace` walks every
  `subprocess.*` call under `src/` and `scripts/` and fails on any
  text-mode call (`text=`, `universal_newlines=`, `encoding=`) whose
  `errors=` is missing or is not the literal `"replace"`. New seams
  get the flag for free or the suite refuses; bare-name subprocess
  imports (`from subprocess import run`) and `import subprocess as
  sp` are outside the walk's shape and against tree convention. The
  3.62.1 note's "raised, not fixed" tail is hereby resolved: the
  sibling holes closed in this release (issue #9), decode only.

## Programmer-facing contract notes (3.62.1 onward, the battery-degrade fix)

- **check_pdf's `_run` never raises and never hides a timeout.** It
  decodes with `errors="replace"` (the crash that actually killed the
  2026-10-11 battery: `text=True` + strict decode turned qpdf's
  non-UTF-8 warning echo into an unhandled UnicodeDecodeError, exit 1,
  no JSON report, and `_pdf_battery`'s rc-in-(0,1) seam then died on
  the empty temp file) and returns a `_TimedOut` sentinel (returncode
  None, empty outputs) when a subprocess blows its budget: None still
  means no-binary/OSError, `_TimedOut` means out of time. Only
  check_file's `qpdf --check` caller names the cause (verdict
  "timeout" + advisory `qpdf_timeout` finding); every other caller
  treats the sentinel exactly like None. `qpdf_timeout` sits outside
  `_STRUCTURAL_KINDS` on purpose (environmental trigger, not file
  damage), so the exit contract is unchanged. The sibling `text=True`
  seams (run.py's `_run`, integrate.py, the other scripts) still carry
  the strict-decode hole; raised, not fixed in 3.62.1.

## Programmer-facing contract notes (3.62.0 onward, the three-issue fixes)

- **The screen's filename fallback feeds classify_titles parsed seeds.**
  For a file with no embedded metadata, ebook-meta reports the whole
  filename stem as the title and Author(s) "Unknown" (verified live,
  calibre 9.15). `_screen`'s within-batch loop now compares
  `_filename_seed()` output when BOTH records look metadata-less (no
  authors, or the placeholder): first parenthesized group = author
  segment (order-free token set, "Last, First" == "First Last"), text
  before it = title. A second gate requires the whole stems' token sets
  to agree, so a site-first `(z-library.sk)` group cannot over-match
  two different works. classify_titles stays the one title-pair judge;
  the seed only chooses WHICH strings it judges. Any real author
  (non-placeholder) on either side never takes the path, and a
  token-set disagreement falls through to the raw stem comparison,
  unchanged.
- **reconcile's apply failures collect into a `failures` list** printed
  as a FAILURES block on stdout immediately before the summary, and the
  summary carries the count (`COMPLETED WITH ERRORS: N failed
  operation(s) (listed above); M file(s) still drifted on read-back.`,
  issue #6). `embed_calibredb(ids, library_root, failures)` takes the
  list as a required third param and names the failed chunk's id span;
  `embed_pdf`/`embed_djvu` take an optional `failures=` kwarg and carry
  book id and format. The inline stderr lines stay. A residual-only
  failure (every embed rc 0, the #9177 class) prints no dangling
  "(listed above)".
- **fetch_library_codes' misses default is beside the script**
  (`default_misses_file()`), never CWD-relative: the documented
  invocation runs from the library root, and the worklist must not land
  inside the library (issue #7). `--misses-file PATH` overrides.

## Programmer-facing contract notes (3.61.0 onward, the EPUB3 dc:date round-trip fix)

- **The canonical dc:date is a full ISO instant, not date-only.**
  `canonical_date_target(pubdate)` in `src/cquarry_cli/epubdates.py`
  (`norm_date` + `T00:00:00Z`; '' stays '' -> zero dates) builds the
  target, and BOTH embed paths take it from there (reconcile's apply
  pass and `run flush`). Why: calibre's EPUB3 reader nudges a date-only
  dc:date off month boundaries (`fix_only_date`, calibre utils/date:
  day 1 becomes day 2, a month's last day pulls back one), so the
  3.56.0 date-only canonical read back one day off through ebook-meta
  for every day-1 pubdate and the read-back re-diff never converged
  (issue #4: five EPUB3 residuals, all day-1 pubdates; CBT 2020-10-01
  read back 2020-10-02T00:00:00+00:00). The EPUB2 reader uses the
  general date parser, which never nudges; that is why the EPUB2
  multi-date class converged and only EPUB3 residuals remained. A full
  ISO instant round-trips exactly under both readers (calibre's own
  EPUB3 writer emits full ISO via `isoformat`). The 3.56.0 surgery
  (`canonical_dc_dates`) is unchanged and still generic; books already
  carrying a date-only canonical stay in-sync when mid-month and keep
  reporting DRIFT when day-1/month-end until the next `--apply`/`flush`
  rewrites them.

## Programmer-facing contract notes (3.60.0 onward, run news)

- **`run news` addresses recipes by exact TITLE, and the seam cannot
  do better.** The verb spawns `ebook-convert "<Title>.recipe"
  out.epub` (what upstream's own help documents); upstream's internal
  id addressing (`builtin:<slug>` via the `CALIBRE_RECIPE_URN` env var
  its GUI sets) has no public enumeration, so title is the only honest
  key. Consequence pinned by tests: a title `--list-recipes` ships
  MORE THAN ONCE (calibre 9.15 carries 13 collisions; Deutsche Welle
  x7) is a usage refusal on an explicit `--recipe` (title resolution
  would silently fetch the first match), and inside the curated
  default or `--all` it degrades to a skip warning instead. The
  enumeration parse keeps the RAW post-tab bytes (titles with
  leading/trailing spaces resolve exactly); `_title_for` accepts
  stripped input and maps it back to the raw string.
- **The batch rails are the verb's own** (`news.py`, routed headless
  via dispatch_headless: no library, no db, no backup, no
  closed-Calibre guard): curated DEFAULT_RECIPES default (a stale or
  ambiguous entry warns and skips, never fatal), dry run by default,
  network at `--apply` only, strictly sequential spawns (no
  hammering), one report row per dead recipe with its PARTIAL output
  deleted (the catalog sweeps' stale-file rule: a resume must not
  skip on a half-written edition), resume = existing non-empty file
  unless `--force`, default dest `./news/<date>/` (date-scoped so
  yesterday's editions never shadow today's), default timeout 1200s
  (the live drill: BBC News = 128 MB in ~4.5 min). Progress lines
  print only in text mode; `--format json` output stays parseable
  (plan: `{plan:{dest,recipes,warnings}}`, apply:
  `{results,fetched,already,skipped,failed}`).
- **`run news` is the third network verb** (backfill --apply, phase2's
  post-commit downloads, news --apply) and the only one whose upstream
  seam ALSO phones home by design: ebook-convert refreshes the recipe
  source from calibre's server at fetch time ("Trying to get latest
  version of recipe"); that is upstream's default, kept deliberately
  (fresher recipes fail less), not a hidden fetch.

## Programmer-facing contract notes (3.59.0 onward, the truthed-up + json help)

- **Verb pages are claim-map truth.** `VERB_FLAGS` claims `backup_dir`
  for every verb whose `--apply` demands it (phase2 + the
  dispatch_integrate needs_backup set), and the per-verb shared footer
  is verb-aware via `shared_flags_for` + `APPLY_VERBS`: the
  acquisition-pathway verbs (phase1/sign/approve/phase2/phase3) have NO
  --apply and their pages must not advertise one. `takes_apply` in the
  json surface is the same table.
- **`--help json` is the machine surface**: the complete parser
  (groups, flags, run verbs with claimed flags + prose + shared) as
  JSON generated from the live parser, `sort_keys` deterministic, and
  NEVER painted — the intercept exempts the json topic from `paint()`
  so the output stays valid JSON under FORCE_COLOR. An agent's
  single-artifact CLI reference; the AI-grokability probe (2026-10-06)
  is what drove the truth-up.

## Programmer-facing contract notes (3.58.0 onward, the colored help)

- **Help pages paint at print time, never at layout time.**
  `helpcli.paint(text)` applies the CPython 3.14 argparse theme
  (lattice-music's shipped contract: FORCE_COLOR forces, NO_COLOR and
  PYTHON_COLORS=0 suppress, piped stays plain) to FINISHED layout lines;
  the renderers stay plain-text so column math never sees codes.
  `stripped(paint(x)) == x` is a pinned invariant. New help text needs
  nothing color-specific unless it must NOT be painted (avoid ALL-CAPS
  words you do not want yellow; the label regex paints them).

## Programmer-facing contract notes (3.57.0 onward, the two-level help)

- **The help surface lives in `src/cquarry_cli/helpcli.py` and answers
  before argparse.** `handle_help(argv)` runs first in `cli.main()`:
  `--help TOPIC` / `run --help VERB` / `run VERB --help` return
  `(exit_code, text)`, bare `--help`/`-h` returns None and lands on the
  parser's `_OverviewHelp` action, which prints `overview(parser)`.
  Flag tables are generated from the parser's argument groups (found by
  the title constants helpcli owns: MODES/SCOPING/OUTPUT/DISPLAY/
  MODIFIERS/WRITE/SET), so group assignment IS topic coverage. The
  curated layer is `VERB_FLAGS` (the verb claim map) + `VERB_PROSE`;
  tests/test_help.py pins every run flag claimed or shared, every mode
  in the overview, every choice paginated. New run flags must join the
  claim map or the pin fails.
- **The modes group is a titled regular group, not an argparse
  mutually exclusive group** (argparse gives exclusivity groups no
  title): the pick-one enforcement is the explicit post-parse check at
  the top of `cli.main()` (exit 2, names the picked flags). Do not
  re-add a second exclusive group for modes. The set-mode target
  sources keep their real exclusive group (untitled, inside the set
  section).

## Programmer-facing contract notes (3.56.0 onward, the dc:date convergence batch)

- **reconcile's EPUB embeds end with an OPF normalization pass**
  (`normalize_epub_dates`, after the calibredb chunks, before the
  read-back): every embedded EPUB's metadata block is forced to exactly
  one `dc:date` carrying `norm_date(db pubdate)`, or zero dates when the
  DB pubdate is the sentinel (matching diff_fields' comparison; the
  date-only target superseded 3.61.0 by `canonical_date_target`'s full
  ISO instant, see the issue-#4 note). The
  live class it closes (#9177/#9635): calibredb's EPUB2 writer rewrites
  only the EARLIEST dc:date in place and leaves the rest standing,
  while calibre's EPUB2 reader reports the MINIMUM, so multi-dated
  packages never converged. The surgery is text-scoped to the
  `<metadata>` block (`canonical_dc_dates`): dcterms:modified and
  everything outside the block are untouched; an already-canonical file
  is byte-identical (no zip rewrite); insertion goes after the last
  DTD-predecessor element (title/creator/subject/description/
  publisher/contributor). A file that cannot be safely rewritten (no
  metadata block, no `xmlns:dc` binding for insertion, unreadable zip)
  is a named failure (`dc:date normalization failed`, stderr) that
  fails the run; the read-back re-diff stays the arbiter. The zip
  rewrite is whole-archive (entry order and per-entry metadata
  preserved) through a temp file + os.replace in the file's directory.
- **The pass lives in `src/cquarry_cli/epubdates.py` and `run flush`
  runs it too** (the follow-up to 3.56.0, sharing the module; ships
  with the next release): after a green embed pass, every flushed
  book's EPUB is normalized the same way, a normalization failure
  fails the verb, and the report carries the count (text line;
  `epub_dates_normalized` in JSON). The script imports norm_date and
  normalize_epub_dates from the module (a checkout run without the
  package importable inserts the repo's src/), and a test pins the
  re-exports as the module objects, so a fork cannot rot flush.

## Programmer-facing contract notes (3.55.2 onward, the live-seam drill)

- **calibredb catalog's argv contract is unique and live-verified**: the
  output filename is the FIRST token after the subcommand and every
  option follows it, the library via `--library-path` (calibre 9.15).
  The placement every other calibredb command accepts (`--library` after
  the command) dies with "Must specify the catalog output filename
  before any options". tests/test_integrate.py asserts the full command.
- **Live-drill facts, calibre 9.15**: ebook-device's no-device answer
  ("Unable to find a connected ebook reader.") rides stderr with rc 0 and
  run device surfaces it; the BIBTEX catalog plugin omits any book
  missing title/authors/publisher/pubdate (README caveat); `calibredb
  fts_index reindex` only QUEUES extraction -- headless completion needs
  upstream's --wait-for-completion (a `--wait` passthrough is the
  recorded improvement candidate) or Calibre's next start. The drill ran
  every other Phase 20 verb against real binaries (scratch libraries,
  CALIBRE_CONFIG_DIRECTORY sandbox): clone, backup-metadata,
  restore-database, fts-index, catalog-epub, customize, debug-tools all
  verified end to end; run device without hardware is the one uncovered
  seam.

## Programmer-facing contract notes (3.55.1 onward, the audit batch)

- **Spawn-point refusals are now uniform**: run flush and run export moved
  their shutil.which checks after the dry-run return, so every verb's
  missing-binary refusal sits at its spawn (fts-index's --fts-status still
  refuses immediately because that action IS a spawn).
- **debug-tools' existence rules are per action**: explode checks its FILE
  and implode checks its DIR only (the other operand is upstream's output,
  created on demand); the other four tools check every path.
- **The schemawrite company guard consults all five dest lists** (book
  verbs, set sources, and both batch tuples); a batch flag beside a schema
  verb is a usage refusal, never a silent drop.
- **The SRU fallback fires on cached-out misses too**: the block sits at
  the top level of the target loop, not inside the network branch, so a
  resume re-run gets the work-level retry. work_query strips double quotes
  (a quoted CQL string cannot contain them, and a malformed query burns
  three retries plus abort-counter headroom).

## Programmer-facing contract notes (3.55.0 onward, the Phase 20 parity lane)

- **The headless verbs are subprocess seams with spawn-point refusals.**
  `integrate.py` hosts backup-metadata, restore-database, clone, fts-index,
  catalog-epub/bibtex; customize, debug-tools, and device need no library and
  are routed by `dispatch_run` before any database resolution
  (`dispatch_headless`). The missing-binary refusal (`shutil.which`) sits
  with the spawns, never ahead of a dry run: CI has no calibredb, and a
  verb-top check refused even the plans (339cb06's fix). restore-database
  CREATES a database: `--target` required, existing metadata.db replaced
  only under `--force`, an OPF-less target refused, no `--backup-dir`
  (upstream keeps its own `metadata_pre_restore.db`). clone's dry run says
  NO BOOKS copy; the target must not exist or must be empty and never
  collides with the source. catalog's output extension is enforced per verb
  (`_CATALOG_EXTS`): calibredb catalog silently falls back to the EPUB
  plugin on an unrecognized extension. fts-index's `--enable` writes a
  preference row and carries its OWN backup rule in-verb (the trash
  precedent); the reindex touches only the sidecar and needs none.
- **The library-schema writes are one dispatcher: `schemawrite.py`.**
  `SCHEMA_WRITE_DESTS` (dests.py) holds the five dests; they refuse the
  company of any book verb or set source, run one write per invocation, and
  take the full rails. The custom-column doors refuse the NON-NEGOTIABLES
  labels at the argument layer (`_check_column_label`); add sets
  update_all_last_mod_dates_on_start (named in the dry run), remove only
  flags mark_for_delete. `--column-is-multiple` is refused as an orphan
  modifier. The flags with no nargs (delete, remove) store plain strings,
  not tuples. The dry-run peek opens one read-only CalibreDB and must do
  BOTH reads inside its open block (the column read used to run after
  close). Test-fixture trap, now pinned: the preferences table needs
  `UNIQUE (key)` (Calibre's real shape) or INSERT OR REPLACE inserts second
  rows and every fetchone() sees the stale first one.
- **The sign/approve pair.** `manifest.approve_from_verdicts` REPLACEs the
  approved list from the per-file verdicts; `run approve` wraps it (loads
  with `check_approval_pairing=False`, the validate opt-out, because the
  repair door must load the broken pairing it fixes); `sign_manifest`
  replaces the pairing cross-check with the derived-vs-listed comparison,
  which covers BOTH divergence directions and names approve as the fix.
  Phase 2/3 loads keep the full validator.
- **Supersedes two stale notes below**: the 3.37.0 `--fts` route decision
  ("the one table cquarry 1.20 does not expose... read directly") and the
  3.44.0 "exactly one raw-SQL read left" note both describe the retired
  modes/fts.py sidecar read; the tier has zero raw-SQL reads since the
  get_dirtied_formats() adoption, and the floor line in the 3.44.0 section
  (cquarry >= 1.22.0) is superseded by pyproject's.
- **reconcile's verify-after-embed**: `verify_embedded` re-diffs every file
  a writer claimed; residuals fail the run (exit 1, the class phase 3's
  seam already tolerates). `run flush` reports `queue_remaining` in JSON.
  fetch_library_codes' `--sru-fallback` caches work-level lookups under
  `work:` keys that cannot collide with ISBN keys; fallback failures count
  toward the consecutive-failure abort.

## Programmer-facing contract notes (3.54.0 onward, the seed-and-recover batch)

- **The phase-1 author seed cuts the bracket BEFORE the split.**
  `_stamps_from_embedded` partitions the whole `Author(s)` display line at
  the FIRST ` [` and only then splits on `" & "`: calibre renders N authors
  as `A & B & C & D [SortA & SortB & ...]` with the bracket wrapping the
  LIST, and the old split-first order seeded the sort forms as phantom
  authors (live case: a correct 4-author stamp seeded 7). The single-author
  `Name [Sort, Form]` form and the Unknown drop are unchanged. A display
  name carrying a literal ` [` loses its tail by design, the same recorded
  trade as stamp_epub's `_display_author`; the manifest is the review
  step's to correct.
- **stamp_pdf's parse-crash class gets exactly one mechanical recovery.**
  When the exiftool write exits nonzero AND the output carries a Perl-space
  parse error (`_PARSE_CRASH_RE`; the live shape: "Can't find Root object"
  off a malformed catalog Names array, on a file `qpdf --check` passes),
  `_rebuild_in_place` runs one `qpdf --replace-input` rebuild gated on the
  page count (qpdf missing, unreadable either side, or a changed count:
  the already-made backup is restored and None comes back), and only a
  verified rebuild retries the stamp ONCE, re-reading
  `_has_calibre_authorsort` afterward because the rebuild rewrote the
  packet. The stubborn-XMP class (write rc 0, read-back disagrees) is
  untouched and never retries; any other nonzero exit (no Perl-space
  signature) never recovers either. `_run_qpdf` is a seam like the other
  subprocess helpers.

## Programmer-facing contract notes (3.53.0 onward, the EPUB stamp tool)

- **`scripts/stamp_epub.py` is stamp_pdf's EPUB sibling, CLI-shaped identically**
  (dry-run default; `--apply` demands a `--backup-dir` refused inside any
  target's directory; the `_check_isbn` checksum gate is exit 2, dry-run and
  apply alike; multi-author joins " & "). The write is ONE `ebook-meta`
  invocation over the fixed field set (title/authors/publisher/isbn); a
  field the caller leaves off is not passed at all. Two guards stamp_pdf
  does not have: a target named twice is refused (exit 2, one file one
  stamp), and a failed stamp STOPS the list with the remainder named AND
  recorded `left_unstamped` in the `--json FILE` report (statuses:
  `dry_run`/`stamped_verified`/`stamp_failed`/`left_unstamped`), the
  bijection record.
- **The EPUB verify contract is two-sourced on purpose.** Title/authors/
  publisher verify from the `ebook-meta` read-back with the author compared
  as the display segment before the ` [` bracket (`_display_author`; live
  files render `Name [Sort, Form]`). The ISBN NEVER verifies from the
  read-back: `_opf_identifier_candidates` reads the OPF via the container's
  rootfile entry (never the entry name) and collects every `dc:identifier`'s
  text AND `id` attribute; `_normalize_identifier` drops an optional leading
  `urn:`/`isbn:` (also the `isbn_` id spelling) then all non-alphanumerics,
  case-insensitive; the stamp verifies when ANY candidate equals it. This
  makes an already-right ISBN an honest no-op (the writer keeps the
  producer's spelling, sometimes adding its own canonical identifier
  alongside) and makes the wrong-ISBN replace verifiable; no `--isbn`
  requested skips the check entirely. Tests pin all of it against the REAL
  ebook-meta (`TestStampEpubLive`, skipUnless the binary is on PATH) plus a
  mocked-seam class, so the fixture zips are the verify inputs.

## Programmer-facing contract notes (3.52.0 onward, the screen-semantics batch)

- **`classify_titles` is the one title-pair judge.** Everything the
  screener (library path, within-batch path) decides about two titles
  goes through it; do not re-derive normalized comparisons at a call
  site. Verdicts: `duplicate` (equal base, equal core subtitle), `related`
  (colon-boundary containment: one full title equals the other's
  pre-colon base or post-colon subtitle; a masked duplicate as often as
  two products of one series), `volume_sibling` (both sides declare
  volume annotations and they disagree, or the cores differ by one
  trailing bare ordinal), `distinct` (shared base, differing real
  subtitles), None (no opinion). The arabic-signature gate
  (`_volume_signature`, the 3.25.0 shape, raw token keys) is unchanged
  and deliberately stricter than the annotation comparison
  (`_annotations`, canonical kinds, roman-aware): a one-sided arabic
  declaration stays a silent no-match because surfacing every sibling is
  the recorded 19-candidate Wandering Inn flood. `_annotations` values
  are ints (roman parsed), so "Vol. 1" and "Volume I" agree; the
  signature keeps string values and its `{("book", "8")}` shape is
  pinned.
- **The screen's report has two refusal classes and three advisory
  classes.** Refusals: `library_hits`, `batch_duplicates` (run.py's
  `_screen_duplicates` refused-set contract, unchanged). Advisories:
  `related_hits`/`batch_related` (candidate pairs, count toward exit 1,
  land in the manifest's `checks.related_works`) and `batch_volumes`
  (multi-volume set members, informational, never exit 1, land in
  `checks.volume_siblings`). NO advisory sets a file's verdict; phase 1
  passes a notes dict into the seam to receive them. A test pins the
  masked-duplicate pair (library "Mothership: Wages of Sin" vs the bare
  "Wages of Sin" file) as `related`, not a pass.
- **stamp_pdf's `--isbn` is checksum-gated at the boundary**
  (`_check_isbn`, exit 2, dry-run and apply alike, separators tolerated).
  The roadmap box's Hewitt "valid form" 1-59863-503-5 also fails its
  check digit; the guard refuses it, so that number needs re-sourcing.
- **`_apply_opf` writes pubdate date-only** (`_date_only`): a downloaded
  OPF has no trustworthy time-of-day, so `YYYY-MM-DDThh:mm...` truncates
  to `YYYY-MM-DD` before `set_pubdate` (which canonicalizes to midnight
  UTC). Bare years and unparseable forms ride through and fail
  `set_pubdate` exactly as before.

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
  ones. Since 2026-09-21 the class is judged against the repair record's
  structured `fixes` dict (bindery-cli v0.45.0's structured-fix records),
  never against the rendered summary string; the summary rides along as
  the display line. `_BINDERY_MIN_VERSION` probes the PATH bindery and
  hard-errors below 0.45.0 naming the upgrade: an older binary would
  class every strip as structural (the vacuous-consent hole again), so
  too old is never a silent downgrade.

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
  traceback. **[SUPERSEDED 3.59.1: `run phase2`'s post-commit download
  segment also touches the network; backfill is no longer the only one.
  SUPERSEDED 3.60.0: `run news` --apply is the third network verb.]**
  `run backfill` is the only verb that touches the network,
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

## Programmer-facing contract notes (3.35.0 onward)

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

## Programmer-facing contract notes (3.33.0 onward)

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

## Programmer-facing contract notes (3.32.0 onward)

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

## Release flow
Commit and push verified work (the standing push grant in the global file; force-pushes still need approval), wait for CI to go green, then tag: annotated `vX.Y.Z` at the release commit, message = that release's `# N.N.N` patchnotes entry verbatim (`--cleanup=verbatim`; the full tag procedure lives in `~/.zcode/AGENTS.md`). The tag drives `publish.yml` (PyPI), and the `release-tags-protected` ruleset means a bad pushed tag needs the documented force path, so verify the tag message (`git cat-file tag`) before pushing. Version carriers are pinned by `tests/test_version.py` per the Conventions above.
