# Companion scripts

The per-script reference for everything under `scripts/`, split out of README.md
on 2026-11-24 (the README keeps a compact table and links here). The scripts are
stdlib-only Python (plus tqdm for progress bars); several write, deliberately
outside the package read-only contract; each announces its toolchain on stderr.


The `scripts/` directory holds standalone maintenance tools. They are **not** part of the `cquarry` package and deliberately sit **outside its read-only contract**: they are run directly with `python3`, and several of them write. They are stdlib-only Python (plus tqdm for progress bars); some shell out to external command-line tools. Each is designed to run from inside a Calibre library directory (they locate `metadata.db` relative to themselves), so deploy a copy into your library root or pass paths explicitly. Every entry point announces its toolchain (one `toolchain:` line on stderr naming the cquarry / vir_tui / cquarry_cli versions the run sits on; the scripts import the pip-installed module, which can lag the `cquarry` binary a uv tool upgrade brings), and `reconcile_file_metadata.py` refuses an imported `cquarry_cli` older than the floor its identifier pass assumes; a lone deployed copy without `version_guard.py` beside it runs unchanged, just without the announcement (reconcile notes the skipped floor check on stderr).

### `compress_pdf.py`: shrink oversize PDFs (writes)

Re-encodes a bloated PDF (think 1 GB TTRPG sourcebooks) through Ghostscript with a quality preset, but only after verifying the result: it aborts if the page count changes or the output isn't smaller, and it keeps the original as `<name>.pre-compress.pdf`. If the file lives in a Calibre library, it syncs the new size back to the database (core `data.uncompressed_size`, plus the Count Pages plugin's `books_pages_link.format_size` if present) so Calibre doesn't see a stale size. A busy or locked database is handled gracefully: the PDF is still replaced and you are told to re-run with Calibre closed.

> **This script modifies files and `metadata.db`.** It is the reason the companion scripts live outside the `calibrequarry` package. Back up before a bulk run; close Calibre first.

Requires `gs` (Ghostscript); optionally uses `pdfinfo` / `pdfimages` / `pdfdetach` (poppler) for page-count verification and the `--inspect` report.

```bash
python3 scripts/compress_pdf.py book.pdf                 # /ebook (150 dpi), in place + rollback copy
python3 scripts/compress_pdf.py book.pdf --preset screen # smaller, lower quality
python3 scripts/compress_pdf.py book.pdf --dry-run       # compress to a temp file, replace nothing
python3 scripts/compress_pdf.py ./Library --inspect      # per-file recommendation, no changes
python3 scripts/compress_pdf.py book.pdf --out-dir ~/out # write a copy elsewhere; original untouched
```

Exit codes: `0` compressed/verified (or clean inspect), `1` aborted (no shrink, page-count mismatch), `2` setup error (Ghostscript missing, unreadable file).

### `audit_drm.py`: flag DRM-locked files across every format (read-only)

Scans ebook files for DRM, which the metadata and structural audits never inspect. A DRM-locked file can pass `epubcheck`, report its page count, and even import, yet silently refuse to let its embedded metadata be rewritten (the case that prompted this tool was a PDF carrying a residual Adobe ADEPT `EBX_HANDLER` dictionary that `qpdf` and `pdfinfo` both called "not encrypted" while `exiftool` choked on it).

Detection is deliberately conservative: two benign things look like DRM to a crude check and are explicitly cleared.

- **font obfuscation**: an EPUB may carry `META-INF/encryption.xml` that scrambles only its embedded fonts (the IDPF or Adobe font-mangling algorithms). That is not DRM. Obfuscated fonts are sometimes named `fonts/00001.dat` with no font extension, so an entry is cleared when it uses a font-scrambling algorithm *or* targets a font resource.
- **permission flags**: a PDF may be "encrypted" with the Standard handler and an empty user password: it opens with no password and is only flagged against printing/copying. That is not a lock.

Detection per format: EPUB (an `encryption.xml` that encrypts content documents is DRM; a standalone `rights.xml` or `sinf.xml` with no content encryption is a residual marker from a freed book and is reported benign, since the content reads and embeds fine), PDF (a non-Standard security handler found by a streaming byte scan, so a residual/inactive dictionary is still caught because for PDF it still breaks metadata embedding; active Standard encryption is classed with `qpdf` into password-locked vs permissions-only), Kindle MOBI/AZW3 (the record-0 encryption-type field), DJVU (no DRM scheme, reported N/A). It opens `metadata.db` strictly `mode=ro`.

```bash
python3 audit_drm.py                  # scan every file in the library (from the library dir)
python3 audit_drm.py ~/Downloads      # vet loose files before importing them
python3 audit_drm.py --csv drm.csv    # also write a CSV audit (id,status,kind,detail,path)
```

Exit codes: `0` clean (no DRM; font obfuscation and permission flags are not DRM), `1` DRM found or a scan error, `2` setup error.

### `audit_isbns.py`: check stored ISBNs against the books themselves (read-only)

Every other audit here asks whether the catalogue is internally consistent. This one asks what nothing in the Calibre ecosystem asks: does the ISBN recorded against a book actually identify *that* book? Calibre downloads metadata but never re-examines what it stored, so a wrong ISBN is invisible forever, and it matters because an ISBN is what other systems key on. Hand a catalogue to a library service and the ISBN, not the title, decides which book you get.

The failure is real and quiet. A sweep of a 6,786-ISBN library that was already validator-clean found 51 identifiers pointing at something else, most often a **sibling** of the right book: the same publisher's next title, so the number looks plausible and the checksum passes. *Programming Clojure* carried *tmux 2*'s ISBN; *Spelunky* carried *Super Mario Bros. 3*'s; *A Book on C* carried `9782147483649`, which is the 2147483649 integer-overflow constant wearing an ISBN's clothes.

For books no bibliographic database has heard of (small-press RPGs, indie ebooks, print-on-demand reprints) the publisher's own copyright page is the best authority there is, and it is already on disk.

**It reads body text only, never a file's embedded metadata.** `reconcile_file_metadata.py` exists to write the database's values *into* those metadata blocks, so comparing against them would be comparing the database with itself and would confirm every error this tool was built to find.

The hard part is not finding printed ISBNs; it is not crying wolf. A crude check would cry wolf at all four shapes below: the first is a gate on what counts as the book's own number, and the remaining three are classified apart:

- **citations**: books quote other books' ISBNs constantly, and one citation is indistinguishable from a self-identification if you only count numbers. *The Atrocity Archives* names *The New Hacker's Dictionary*'s ISBN in a glossary entry; *Metamagical Themas* lists one among Hofstadter's self-referential joke titles; *C++ Primer Plus* advertises six other Sams books. So a number counts as the book's own only when copyright-page furniture sits near it (a copyright line, a rights reservation, a binding, a printing statement, a CIP block): positive evidence, rather than an attempt to enumerate every way a citation can look. This gates only the *negative* direction: a book printing the same ISBN you store is conclusive regardless of context, since a citation coinciding with your own stored value does not happen.
- **bibliographies**: a book that cites other books prints their ISBNs (*The Art of UNIX Programming* prints 49). Above `--max-printed` distinct ISBNs a file is read as a citing work and its numbers are not treated as evidence about itself.
- **bundles and series**: a boxed set prints each component's ISBN and a series volume may print its siblings'. Several printed ISBNs with no match is reported `AMBIGUOUS`; a human picks, the tool does not guess.
- **format variants**: print and ebook editions differ only in the last digits. When the printed and stored numbers share a registrant prefix the finding is `VARIANT` (same publisher, probably another binding) rather than `MISMATCH` (a different publisher block, where a genuinely wrong ISBN sits).

**The printed ISBN can itself be wrong.** That is the limit of this tool's premise, and the reason it only ever reports. Two real cases, both flagged `VARIANT` and both resolved in favour of the database: the TSR *Forgotten Realms Campaign Setting* boxed set prints `1-56076-605-0`, which actually belongs to *The Jungles of Chult* (a documented typo, in the book, permanently); and *Night Witches* (Bully Pulpit, 2014) prints *Durance*'s ISBN, because a small press reused its previous title's copyright page without updating it. Both are same-publisher cases, which is precisely why `VARIANT` exists: that shape covers innocent format variants *and* publisher mistakes, and no rule separates them without a human.

There is deliberately **no `--apply`**. Across the sweep that motivated this tool, single-source verdicts were wrong often enough to matter: an auto-fixer would have "corrected" *Curse of Strahd*, *Cold Mountain*, *Kitchen* and *The Master and Margarita*, all of which were right. Findings are for a human to judge.

```bash
python3 audit_isbns.py                        # every book with an ISBN
python3 audit_isbns.py --tag NonFic.Tech      # one branch of the taxonomy
python3 audit_isbns.py --id 1969,3189         # specific books
python3 audit_isbns.py --format tsv > out.tsv # machine-readable
python3 audit_isbns.py --format json          # stdout json.loads, no offset
python3 audit_isbns.py --json out.json        # human report + machine file
```

Scoping uses the same anchored-hierarchical `--tag` rule as `fetch_library_codes.py` and cquarry's `tags:` search, and takes a comma-separated list, so a virtual library that spans several roots is covered without a separate flag. Machine formats (`--format tsv`, `--format json`) keep stdout to the payload alone (the banner and setup prose ride stderr), and `--json FILE` writes the same payload `--format json` prints beside any format's human output.

Exit codes: `0` no disagreement, `1` at least one `MISMATCH`/`VARIANT`/`AMBIGUOUS` finding or an unreadable file, `2` setup error.

### `audit_duplicates_content.py`: find books whose text duplicates another book's (read-only)

Metadata grouping only sees rows; this audit fingerprints what is inside the files. A 64-bit simhash over 3-word shingles of each book's spine text finds re-downloads filed under different metadata; a bottom-32 sketch pass catches omnibus containment (a superset document shares its whole sketch, which Hamming distance can never see), and candidates are classified exactly: `near_duplicate` (re-download, or a public-domain reissue: that IS the finding here) vs `omnibus_overlap` (anthology vs standalone). Front-matter-only files and formats without a text extractor are skipped, not guessed. Roughly a second per book, so scope with `--search`/`--ids` for interactive runs.

```bash
python3 scripts/audit_duplicates_content.py .                    # whole library (a long pass)
python3 scripts/audit_duplicates_content.py . --search 'tags:Fic.SciFi'
python3 scripts/audit_duplicates_content.py . --ids 42,7 --format json
```

Exit codes: `0` no clusters, `1` clusters found, `2` setup error.

### `audit_truncation.py`: cross-check Count Pages against the real PDFs (read-only)

The Count Pages plugin records a page count per book and nothing re-checks it. This audit runs `pdfinfo` over every PDF the plugin measured: more than 20% disagreement is `page_count_mismatch` (a truncated download, or a replaced edition nobody re-scanned), and `stale_plugin_data` is its own class (catalogued size drifted from the file, the plugin's own needs_scan flag, or a post-scan mtime). Only PDF rows are checked: the plugin's EPUB pages are word-count estimates by design.

```bash
python3 scripts/audit_truncation.py .                          # every PDF page row
python3 scripts/audit_truncation.py . --search 'tags:RPG' --tolerance 0.1
```

Exit codes: `0` clean, `1` findings, `2` setup error.

### `audit_cover_aspect.py`: flag unusual cover shapes (read-only)

Sizes every catalogued cover through cquarry's header-only image readers (no image library, no full decode) and reports two advisory bands: `cover_aspect_narrow` (w/h below 0.55: spine scans, bad crops, rotated images) and `cover_aspect_wide` (above 0.80: landscape or square art). Legitimate landscape art exists; the audit surfaces the distribution, you judge. Missing and unreadable cover files belong to the missing-covers audit, not this one.

```bash
python3 scripts/audit_cover_aspect.py .                     # default bands
python3 scripts/audit_cover_aspect.py . --low 0.45 --high 0.95 --quiet
```

Exit codes: `0` all inside the bands, `1` findings (advisory), `2` setup error.

### `validate_metadata.py`: lint database integrity (read-only)

A linter for `metadata.db` with two layers. It is the database-side companion to `audit_drm.py`, and it is strictly `mode=ro`.

**Integrity layer (always on, zero config).** Taxonomy-agnostic, schema-level problems the UI and `--audit` leave alone: books with no language, one ISBN attached to two books, placeholder (`0101-01-01`) or unparseable publication dates, junk identifier types (`url`, `uri`, `guid`, `isbn13`, ...), an ISBN-10 misfiled under `amazon`/`mobi-asin` (checksum-verified, so genuine ASINs are left alone), and custom-column link rows orphaned by deleted books. Safe to point at any library; needs no configuration.

**Opinionated layer (on when a taxonomy is loaded).** A `taxonomy.json` describes your tag tree, publisher consolidations, and identifier vocabulary, and these checks enforce it: every tag in use must be declared (`TAG_IN_SPEC`), alias publishers must be merged into their canonical (`PUBLISHER_NOT_CONSOLIDATED`), and fiction should not be PDF-only (`FORMAT_FICTION_PDF`). Loading a taxonomy also makes the identifier-type vocabulary authoritative (the `--strict` behavior turns on automatically). A ready-to-adapt template ships as **`scripts/taxonomy.example.json`** (three roots: `Fic` / `NonFic` / `Gaming`, with a deep, single-tag-per-book hierarchy; a branch is a valid tag on its own only when its `bare_allowed` is `true`). A fuller real-world reference in YAML, **`docs/taxonomy.example.yaml`**, is also included; it is the richer schema used by a separate library-side linter (the stdlib tools here read the JSON form).

Errors are bad data Calibre or tooling can trip on; warnings are hygiene.

```bash
python3 scripts/validate_metadata.py                   # integrity checks on ./metadata.db
python3 scripts/validate_metadata.py ~/Calibre         # a library directory
python3 scripts/validate_metadata.py library/metadata.db
python3 scripts/validate_metadata.py --strict          # also flag non-canonical identifier types
python3 scripts/validate_metadata.py --quiet           # only problems; truncate long lists

# Opinionated mode: copy the template, edit it to match your tree, drop it
# beside your library (it is auto-detected), or pass it explicitly.
cp scripts/taxonomy.example.json taxonomy.json
python3 scripts/validate_metadata.py --taxonomy taxonomy.json
python3 scripts/validate_metadata.py --no-taxonomy     # force integrity-only
```

Sample output (opinionated mode):

```
Validating /path/to/metadata.db
Taxonomy: /path/to/taxonomy.json

ERRORS (2)
  NO_DUPLICATE_ISBN (1)
    ISBN 9780026581509 appears on books: 6352,6355
  TAG_IN_SPEC (1)
    tag 'Fic.Fantasy.Wierd' is not declared in the taxonomy

WARNINGS (2)
  FORMAT_FICTION_PDF (1)
    #5145 'Vermis I' (tag 'Fic.Fantasy.Weird') is PDF-only; fiction prefers EPUB
  PUBLISHER_NOT_CONSOLIDATED (1)
    publisher 'Tor' should be merged into 'Tor Books'

FAIL: 2 error(s), 2 warning(s).
```

A `taxonomy.json` next to the library, the script, or the working directory is loaded automatically; `taxonomy.example.json` is a template and is never auto-loaded.

Exit codes: `0` clean (warnings do not fail), `1` one or more errors, `2` setup error (no `metadata.db`, or a bad taxonomy file).

### `stamp_pdf.py`: pre-stamp PDF metadata for clean imports (writes with `--apply`)

Obscure PDFs (TTRPG modules, scans, indie releases) often carry no embedded
metadata, so Calibre imports the FILENAME as the title. This tool stamps
Title/Author/Publisher (and ISBN via keywords, so phase 2's metadata
download can match) before import; an `--isbn` failing its check digit is
refused outright, in dry-run and apply alike. It verifies with Calibre's own
`ebook-meta` and prints `STAMP_FAILED` rather than fighting a stubborn XMP
store. Dry-run by default; `--apply` requires `--backup-dir` OUTSIDE the
library tree and backs up every original first. The phase-1 skill owns the
research rules: never stamp from the filename or from memory, and prefer
no ISBN over a wrong one.

### `stamp_epub.py`: pre-stamp EPUB metadata for clean imports (writes with `--apply`)

The EPUB sibling of `stamp_pdf.py`: one `ebook-meta` write puts
Title/Author/Publisher and the ISBN into the OPF (Calibre rewrites it in
place and REPLACES any existing isbn identifier, which is how a wrong
embedded ISBN gets corrected). The `--isbn` checksum gate, the out-of-tree
`--backup-dir` requirement, and the dry-run default all match stamp_pdf.
Verification differs because EPUB differs: title/authors/publisher are
read back through `ebook-meta`, while the ISBN is verified from the OPF's
`dc:identifier` elements under value equality, so every producer spelling
verifies and a file that already carried the right ISBN stays untouched.
A failed stamp stops the batch with the remainder named; `--json FILE`
records every target exactly once.

### `screen_duplicate.py`: screen downloads for duplicates (read-only)

The one-pass duplicate screen the acquisition pathway uses: each file's
embedded metadata is read with Calibre's `ebook-meta`, matched against the
library (exact ISBN first, with the 10-form and its 13-form read as the
one edition they are, then classified normalized title + first author,
where a middle initial on one side no longer splits one author in two)
and within the batch, and printed as comparison columns. Beyond true
duplicates, the classifier surfaces advisory classes it never auto-
refuses: differing declared volume annotations as one multi-volume set
(`batch_volumes`), and colon-boundary title containment as related
candidates (`batch_related`, `related_hits`): the shape a series/store
prefix makes when it can either mask a duplicate or join two distinct
products, so the human judges. Junk seeds refuse nothing either: a
title that parses as a print pipeline's filesystem path or an author
that is a print-tool string, and any identical title+author seed
shared by three or more identifier-less files (print-shop template
spill), surface as related advisories naming their basis instead of
refusing distinct files (since 3.92.0). `--format json` emits the bare list of
per-file records the `run phase1` seam consumes (only records with
`library_hits`/`batch_duplicates` are duplicates; advisories ride into the
manifest's `checks`). Exit codes: 0 clean, 1 candidates found, 2 setup
error. Report-only, always.

### `check_pdf.py`: the PDF/DJVU battery (read-only)

The per-file battery behind `run phase1`: header, page count, `qpdf --check`
with real-vs-benign classification (a check that exceeds its 120 s budget
reports `timeout` with an advisory finding instead of crashing the
battery: environmental, re-check the file without contention), fonts,
text layer, images. Per-file report plus `--json FILE` for the machine
shape. Exit codes: 0 clean, 1 structural findings (the normal trouble
outcome, report still written), 2 a file it could not read at all.

### `comments_census.py`: description-field defect census (read-only)

A standing census over the comments fields: length outliers, HTML junk,
stub descriptions, and the like, per book with a summary. `--id` restricts
to specific books; `--json FILE` writes the machine report for scripting.

### `audit_conversion_overrides.py`: list per-book conversion overrides (read-only)

Manual per-book conversion overrides (`conversion_options`) make pipeline
behavior drift book-by-book; this lists which books carry them so the
overrides are a decision, not a surprise. `--quiet` prints only the ids
(exit 1 when any are found, so the report pipes into a repair workflow).
The same check also renders inside `cquarry --audit` (the
`conversion_override` rows and the summary block), through cquarry's
shared `get_conversion_profiles`; this script remains the standalone,
pipeable form.

### `reconcile_file_metadata.py`: sync DB metadata into book files (writes with `--apply`)

Calibre's `metadata.db` is where you curate titles, authors, series, tags, publishers, dates, identifiers, and blurbs; the copy embedded *inside* each EPUB/MOBI/AZW3/PDF/DJVU is what travels with the book when it leaves the library. Those drift apart whenever you edit metadata in Calibre without re-exporting the file. This script finds that drift and, with `--apply`, closes it. The flow is always database to file; it never reads file metadata back into the database.

It reads the database `mode=ro`, reads each file's embedded metadata with `ebook-meta` (EPUB/MOBI/AZW3/PDF) or `djvused` (DJVU), and diffs a per-format set of fields so a format is never faulted for something it cannot carry (a PDF holds title/author/publisher/date, a DJVU only title/author, an EPUB the full record). Default is a dry-run report. `--apply` touches only the drifted files, with a writer chosen per format: `calibredb embed_metadata` for EPUB/MOBI/AZW3 (full record plus cover), `exiftool` for PDF (Info dict + XMP; calibredb is skipped for PDF because it silently leaves some PDFs unchanged, whereas exiftool wrote every PDF tested), and `djvused` for DJVU. It refuses `--apply` while Calibre is running unless you pass `--force`. `--apply` needs `calibredb` and `exiftool` on PATH (the dry run does not). After an EPUB embed the OPF is normalized to one canonical `dc:date` and identifier set, and the EPUB2 NCX's `dtb:uid` is synced to the freshly embedded OPF identifier (calibre's writer updates the OPF slot and never the NCX, which used to leave every embedded EPUB failing epubcheck NCX-001). A few PDFs carry a damaged cross-reference table that exiftool refuses to write; pass `--repair-pdf` to rebuild it in place with `qpdf --replace-input` (page count preserved) and retry the embed. It is opt-in because it structurally rewrites the file.

```bash
python3 scripts/reconcile_file_metadata.py                 # dry-run report, ./metadata.db
python3 scripts/reconcile_file_metadata.py ~/Calibre       # a library directory
python3 scripts/reconcile_file_metadata.py --sample 50     # a random 50 books (quick look)
python3 scripts/reconcile_file_metadata.py --id 6688,6690  # specific books
python3 scripts/reconcile_file_metadata.py --format epub   # only EPUB files
python3 scripts/reconcile_file_metadata.py --apply         # embed DB metadata into drifted files
```

Reading every file spawns a subprocess per file, so an unscoped run is slow (tens of minutes for thousands of books); scope it with `--sample` / `--id` / `--format` for a quick look. Sample output:

```
Reconciling /path/to/metadata.db  [5 book(s)]

DRIFT (5 file(s))
  #6688 [EPUB] Slumdog Deckbuilder
      differs: title, series, publisher, pubdate, tags, identifiers, comments
  #5061 [PDF] Rogue Trader: Core Rulebook
      differs: authors
  #2231 [DJVU] The B-Book: Assigning Programs to Meanings
      differs: title, authors

checked 5 file(s): 0 in sync, 5 drifted, 0 unreadable/missing.

Run again with --apply to embed the database metadata into the drifted files.
```

After the embed, every written EPUB's OPF and NCX are also parsed strictly: files the embed left unparseable (calibre's writer can emit unbound XML prefixes) are reported as SCARS rows and fail the run, with the exact scoped `bindery library ... --fix-unbound-prefix` follow-up command printed beneath the block, so the remediation never has to be remembered.

Exit codes: `0` no drift (or `--apply` finished cleanly), `1` drift found (dry run) or an apply/embed step failed, `2` setup error (no `metadata.db`, or a missing external tool).

### `fetch_library_codes.py`: derive LCC/DDC codes from the Library of Congress (writes with `--apply`)

Fills in Library of Congress Classification (and optionally Dewey) codes for books that already carry an ISBN, by querying the Library of Congress SRU catalogue at `lx2.loc.gov:210` and storing what comes back as Calibre identifiers (`lcc`, and with `--write-ddc` also `ddc`). Storing them as identifiers rather than as a column value keeps one canonical home for the data: a composite custom column with the template `{identifiers:select(lcc)}` displays the value without a second copy, and `reconcile_file_metadata.py` carries identifiers into embedded file metadata.

This exists because the "Library Codes - SRU" Calibre plugin cannot do the job. It rejects composite custom columns outright (`library_codes_dialog.py` requires datatype `text`), and its ISBN lookup queries the LCDB index `dc.identifier`, which the server refuses with SRU diagnostic 1/16 "Unsupported index". The index that actually resolves an ISBN is `bath.isbn`.

Two things govern how you run it. First, the Library of Congress rate-limits hard: at 0.6s between requests it starts resetting connections after roughly twenty queries, so the default pacing is 2.0s with exponential backoff, and a run over several thousand ISBNs takes hours. Every result including a miss is cached to `~/.cache/cquarry/library_codes.json`, so an interrupted run resumes for free and a repeat run is instant. Second, coverage is partial and very uneven by subject: academic, technical and canonical titles resolve well, while genre fiction, indie and small-press releases, translations and tabletop material frequently have no record at all. Default is a dry run that reports the hit rate per tag branch, which is the number to look at before committing to a full pass.

```bash
python3 scripts/fetch_library_codes.py                  # dry run over the whole library
python3 scripts/fetch_library_codes.py --sample 200     # dry run, random sample
python3 scripts/fetch_library_codes.py --tag NonFic     # scope by tag prefix (anchored)
python3 scripts/fetch_library_codes.py --id 8541,8542   # specific books
python3 scripts/fetch_library_codes.py --apply          # write the identifiers
python3 scripts/fetch_library_codes.py --apply --write-ddc   # also store ddc
python3 scripts/fetch_library_codes.py --apply --all-codes   # one pass, both codes
```

Books that already have the requested code are skipped unless you pass `--refresh`, so the tool is naturally incremental: run it again after an import and it only queries the new books (`--all-codes` selects books missing *either* code). `--refresh` also reads past the response cache entirely, so stored answers (misses included) are fetched again and replaced while the rest of the cache file survives; `--no-cache` is the leave-the-cache-alone variant. Only a well-formed SRU answer is cached: a genuine zero-hit is a miss, but a rate-limit page or any other non-answer retries and never caches. `--sru-fallback` answers a clean ISBN miss with one paced query against a fragment of the title (cut at the first colon, capped at 45 characters: LoC's title index matches the whole indexed string, so the DB's full title-with-subtitle finds nothing) asking for 20 records filtered client-side by the book's author surname, and caches work-level lookups under `work:` keys so ISBN-level retries never re-query. LCC and DDC arrive in the same SRU response, so `--all-codes` stores both in one pass and the old two-pass dance (an LCC `--apply`, then a `--apply --write-ddc` behind it) is retired: two concurrent writers on `metadata.db` were exactly the lock-contention incident that motivated it. `--apply` backs up `metadata.db` to the sibling `.backups` directory first and refuses to run while Calibre is open; the write itself goes through cquarry's `WritableCalibreDB` (so touched books land in the `metadata_dirtied` OPF-resync queue and `last_modified` moves) with retry/backoff over a busy database. Every book that ends the pass without an LCC is appended to the misses worklist (`--misses-file`, default `fetch_library_codes_misses.txt` beside the script; the documented invocation runs from the library root and the worklist must never land inside the library) as a timestamped `# --- run` section carrying the id list plus one `id<TAB>isbn<TAB>ddc<TAB>title` row per miss, so the manual-research pass starts from a file instead of terminal scrollback and an earlier run's list survives later, smaller runs (the file keeps the most recent eight sections). Sample output:

```
DRY RUN: 12 book(s) with an ISBN and no LCC
pacing 2.0s between requests; cached results reused

  [1/12] HIT  PS3561.I483 Y68 2024     #299 You Like It Darker
  [2/12] --                            #1009 Cat's Cradle
  [3/12] HIT  PL992.26.K36 C4313 2016  #1089 The Vegetarian

queried 12 book(s); LCC found for 8 (67%)

hit rate by tag branch:
  Fic.Fantasy                       2/4    ##########
  NonFic.Tech                       3/4    ###############
```

Exit codes: `0` completed, `1` aborted after repeated network failure or a write error, `2` setup error (no `metadata.db`, Calibre running under `--apply`, or bad arguments).

### `extract_stamp_evidence.py`: the stamp-research evidence feeder (read-only)

One JSON row per file for the phase-1 research pass (the feeder the
research briefs and slice files read): `file`/`path`/`fmt`, `seed` (the
raw `ebook-meta` display fields, ISBN/identifiers included), `opf_ids`,
`front` (first 4 EPUB spine documents or `pdftotext` pages 1-4; a PDF
window that carries no ISBN and no copyright evidence widens once to
pages 1-12, because print-scan copyright leaves sort at pages 5-10),
`isbn_snips` (copyright-page ISBN shapes), `provenance` (the same seeder
phase 1 uses), and a per-file `error` that never stops the run. The OPF
manifest parses with ElementTree, so `<item>` attribute order cannot
matter (the 2026-10-14 href-first wave broke a hand-rolled regex copy 87
EPUBs deep, which is why this is a tool and not a snippet). The
thin-front census is the extraction's quality gate: rows whose front came
back under `--thin-chars` (default 200) are listed by name and the run
exits 1. Reads only: directory inputs list top-level files (a prior
phase's `_quarantine/` never sweeps in) and `--out` (required) is refused
inside any scanned directory. Exit codes: `0` clean extraction, `1`
thin-front rows to inspect, `2` usage error.

### `spot_check.py`: randomized quality audit, with a judgement mode (read-only)

Samples N random books and checks what pattern sweeps miss: metadata field quality (title corruption, junk author entries, mojibake, stub descriptions) and the actual file contents (EPUB archive integrity, spine completeness, text volume; PDF header and page count; DJVU page count). Random sampling is the point. Every record has equal odds of inspection, so the result estimates whole-library quality instead of re-confirming whatever curation already looked at. The exit code is the number of books with hard failures.

```bash
python3 scripts/spot_check.py --n 600 --seed 101      # mechanical pass
```

**Review mode (`--review`) exists because the mechanical checks can only judge shape.** Whether a title is the *right* title, whether an author field holds the person who actually wrote the book, and whether a description describes *this* book are judgements no regular expression can make. Review mode emits full title, author, context, and complete description in numbered chunks, takes back a verdict file, and keeps the answers in a ledger so reviewed books drop out of later samples and the judging accumulates across sessions instead of being redone.

```bash
python3 scripts/spot_check.py --n 200 --review --batch 100    # emit chunks + .ids
python3 scripts/spot_check.py --record verdicts.tsv --against spot_review.001.ids
python3 scripts/spot_check.py --worklist                      # the BAD punch list
```

Verdicts are three per book, `OK` or `BAD` for title, author, and comment, plus an optional note. **Recording refuses to write unless the verdict ids reconcile exactly with the ids emitted**, in either direction:

```
REFUSED: 1 of 3 emitted id(s) have no verdict: [5315]
REFUSED: 1 id(s) not in r.001.ids: [9999]
REFUSED: 1 malformed line(s): line 1: author is 'YES', want OK or BAD
```

That check is the load-bearing part. A reviewer working through hundreds of records silently drops some, and a short list is indistinguishable from a complete one; this has bitten repeatedly on real work. Nothing enters the ledger unless the ids agree.

Exit codes: `0` clean, N = number of books with hard failures (capped at 99), `1` from `--record` if the verdicts are malformed or the ids do not reconcile (and nothing is written), `99` setup error.
