# CalibreQuarry Application Specification

**Version:** 3.58.0  
**Language:** Python 3.14+  
**Dependencies:** `cquarry` (>= 1.24.0), `vir-tui`, `tqdm` (stdlib sqlite3, json, csv, argparse, re, unicodedata, datetime)  
**License:** MIT

---

## 1. Mission Statement

CalibreQuarry is a CLI toolkit for Calibre users who treat their libraries as curated collections. It reads `metadata.db` directly in read-only mode, bypassing the overhead of `calibredb`, JSON intermediaries, or external library dependencies.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline with a single command.** The script resolves Calibre's **Virtual Library** (Wing) search expressions natively, ensuring existing library definitions work without re-encoding.

---

## 2. Architecture

### 2.1 Decoupled Shared Library Architecture
The CLI/TUI frontend is decoupled from the database and search logic: 

**`cquarry` (External Dependency)**: The core database connection, schema mapping, Calibre lock handling (snapshots), and the search grammar AST parser are provided by the `cquarry` standalone package.

The floor is `cquarry >= 1.24.0`; this section names the floor plus a short per-bump list, so it stops accreting a sentence that rots (the floor line itself went stale at 1.7, 1.14, and 1.21). A test pins the floor against `pyproject.toml`.

- **1.24.0**: `CalibreDB.get_dirtied_formats()`, the FTS sidecar's extraction queue read (retires modes/fts.py's raw sidecar read, the frontend tier's one recorded raw-SQL exception, and feeds `run fts-index`); the typed `set_preference` writer plus `saved_search_add`/`saved_search_delete`/`saved_search_rename` behind `--saved-search-*`; the FTS queue verbs and `maintain()` ride the floor without frontend consumers yet.
- **1.22.0**: `CalibreDB.precedent_tags()`, the phase-3 prompt's tag-by-precedent read (promoted from run.py).
- **1.21.0**: the metadata-quality predicates `--audit` renders (`find_invalid_uuids`, `find_sentinel_pubdates`, `find_bad_language_codes`), plus the write-path fixes the 3.41 run-verb batch rides on.
- **1.12-1.14**: the foundations every mode rides: native list hydration for `authors`/`tags`/`languages`/`formats` (never comma-split them), computed row `size`, `analytics.genre_distribution()` behind `--analytics genres`, the set-mode write helpers (`clear_tags`, `add_custom_column_values`, `clear_rating`), and `add_book` as `run phase2`'s creation path. The search-engine features here are engine-level: saved-search interpolation (`search:"Name"`), multi-valued count operators (`tags:#>2`), language canonicalization, and unknown virtual libraries raising instead of matching nothing.
- **Older floors**: see `patchnotes.md`, which records what each cquarry bump adopted at release time.

**`cquarry_cli` (Internal Package)**: The frontend modules live in `src/cquarry_cli/`:

| Module | Responsibility |
|--------|----------------|
| `cli.py` | Argument parsing and dispatch. |
| `tui.py` | Curses-based interactive terminal UI. |
| `modes/*.py` | Feature-specific implementations (e.g., `catalog.py`, `export.py`, `stats.py`). |
### 2.2 Virtual Library (Wing) Resolution
CalibreQuarry parses search expressions directly from the `preferences` table using the same engine that backs `--search` (`search.py`). It supports hierarchical tag matching (`tags:Fic.Fantasy`), boolean operators, and `vl:` cross-references.

### 2.3 Search Engine

The search engine provided by `cquarry` ports Calibre's grammar and matching semantics as closely as the standard library allows. It is the single source of truth for both `--search` and Wing resolution.

**Supported:**

- Full grammar: quotes, `\\` / `\"` / `\(` / `\)` escapes, parentheses, `or` / `and` / `not`, implicit AND, and `location:query` tokens, evaluated with Calibre's candidate-set boolean semantics.
- Match kinds: contains (default, case- and accent-folded), `=` exact, `~` regex, `^` accent.
- Field locations: `title`, `authors`/`author`, `author_sort`, `series`, `publisher`, `tags`/`tag` (hierarchical), `rating`, `formats`/`format`, `languages`/`language`, `pubdate`, `timestamp`/`date`, `last_modified`, `identifiers`/`identifier`/`isbn`, `comments`/`comment`, `cover`, `id`, `uuid`, `#custom` columns, `all`, and `vl:`.
- Numeric relational (`= > < >= <= !=`, plus `true`/`false` for presence) and date relational (incl. `today`, `yesterday`, `thismonth`, `N daysago`).

**Deliberate, dependency-bound deviations from Calibre** (the engine is pure stdlib):

- `~` regex uses the stdlib `re` engine, not Calibre's third-party `regex` module.
- Accent/contains folding uses `unicodedata` (NFKD), not ICU collation, so punctuation-insensitivity is not reproduced.
- GPM templates (`@...:`) are tokenized but not evaluated. Saved-search references (`search:"Name"`) ARE evaluated, interpolated from the `preferences` table (cquarry 1.1).
- `tags:` uses cquarry's anchored hierarchical match (`Foo` matches `Foo` and `Foo.*`) rather than Calibre's raw substring default. This is a long-standing invariant; `=` opts into strict exact match.

### 2.4 Database Access

Read-only. Never writes. Opens with a `?mode=ro` URI, built by
`cquarry.helpers.db_uri_ro`, which percent-encodes the path: `?` and `#` are URI
syntax, so a library directory containing either would otherwise resolve to
a different file. All data comes from standard Calibre tables; no custom
columns required. Ratings are stored 0–10 internally (10 = 5 stars);
converted to 0–5 for display.

If the database is locked by a running Calibre instance, CalibreQuarry
copies it (plus WAL/SHM journals) to a temporary snapshot and reads
from there. The temp files are cleaned up on exit.

### 2.5 Database Resolution

If `--db` is omitted, the database is resolved in order:
1. Saved config (`~/.config/cquarry/config.json`)
2. Default paths (`./metadata.db`, `~/Calibre Library/metadata.db`, `~/calibre/metadata.db`)
3. Interactive prompt (if running in a TTY)

The path is saved to config on first successful resolution.

---

## 3. Modes

| Mode | Flag | Description |
|------|------|-------------|
| Help | `--help`, `--help TOPIC` | Two-level: the bare flag prints the compact overview (mode table, scoping, topic pointers); `--help read|write|set|run|examples|all` prints the focused deep dives with flag tables generated from the live parser's groups; `run --help VERB` (or `run VERB --help`) prints a per-verb page. The pre-parse intercept answers before argparse; the grammar is untouched. Modes stay mutually exclusive (one per invocation, exit 2 otherwise). On a terminal the pages are ANSI-colored with the CPython 3.14 argparse theme, laid out on plain text and painted on the finished lines (plain when piped; `NO_COLOR`/`PYTHON_COLORS=0` suppress, `FORCE_COLOR` forces); stripped of codes the painted page is byte-identical to the plain one; `--help json` emits the whole surface (groups, flags, run verbs with claimed flags, apply-verb table) as valid-JSON-under-any-env machine output, generated from the live parser |
| Catalog | `--catalog` | Formatted text grouped by author with ratings and series; `--format md` renders the Markdown shape (headings per author, bulleted books, bold totals) |
| All wings | `--all-wings` | Separate catalog per virtual library (`--format md` names the files `.md`); a per-file failure drops the stale file and fails the sweep |
| All saved searches | `--all-saved-searches` | Separate catalog per saved search (`--outdir`), each headed with the search's expression |
| Statistics | `--stats` | Format breakdown, ratings, tags, publishers |
| Health digest | `--health` | The audit's finding counts in one short screen; same `collect_issues` derivation as `--audit` (the two renderers cannot drift); book-level classes follow the active `--restrict` view, library-shape classes stay global; exit 0 always |
| Audit | `--audit` | Untagged, unrated, coverless/low-res books, and covers the DB claims but the disk lacks; deprecated formats; duplicates; series gaps; manual conversion overrides (per-book `conversion_options`, surfaced by size and format, never unpickled); metadata-quality rows (invalid uuid, sentinel pubdate, bad language; advisory inventory from the cquarry 1.21 predicates, false-positive notes in the renderer); filesystem-vs-database tree rows (missing book dirs/format files, extra/unknown files, extra covers, orphan book/author dirs, malformed paths, root strays) |
| Full-text search | `--fts QUERY` | Content search over the `full-text-search.db` sidecar's plain `books_text` table (read-only; no FTS5 machinery), with an index-staleness summary after the matches; `--fts-status` reports the staleness classes on their own; `--format json` exports the matches |
| Recent | `--recent N` | N most recently added books |
| Series | `--series` | All series with completeness and gap detection |
| Analytics | `--analytics {author,pace,tags,genres,overlap,reading}` | Per-author stats, reading-pace trend, tag tree, genre share breakdown (`--genre-depth N` for deeper hierarchy levels), Wing overlap, reading analytics (`#reading_status` funnel in enum order, `#date_read` recent finishes, days-from-added-to-finished with the era split (pre-library reads, finished before their added date, report separately so they cannot dominate the median); read-only) |
| Export | `--export` | Full library to JSON, CSV, or AI-readable format |
| Search | `--search QUERY` | Books matching a search expression; prints to stdout, or a file with `--output` |
| Annotations | `--export-annotations` | E-reader highlights/bookmarks/notes as JSON; `--id N` scopes to one book |
| Wings | `--wings` | List virtual libraries with book counts |
| Tags | `--tags` | Flat dump of every tag in the library with its book count |
| Interactive | (no args) | Launch the Curses TUI with scrollable output pager |
| Book detail | `--book ID[,ID...]`, `--book --untagged` | Full dossier per book: identifiers, format files, cover, comments (HTML stripped), custom columns, annotations, per-device reading progress, plugin data, conversion overrides; `--format json` for the machine-readable shape; `--untagged` selects cquarry's `find_untagged()` |
| Entities | `--entities KIND` | `authors`/`series`/`publishers`/`tags`/`languages`/`ratings` with book counts; sort and link columns where they exist |
| Reading progress | `--reading-progress` | Every recorded position across devices, progress bars, newest first |
| Custom columns | `--columns` | Custom-column schema: label, search location, datatype, editability, enum values |
| Library info | `--info` | Library dossier: identity UUID, wings + expressions, saved searches, `@Name` categories, grouped search terms, feeds, sync queues, conversion overrides |
| LibraryThing | `--exportlt` | LibraryThing import CSVs (fixed eleven-column template), batched, self-checked; failures exit 1 ("do not upload") |
| Format stats | `--format-stats` | Per-format book counts and total catalogued bytes |
| Run: phase1 | `run phase1 DIR` | Vet a downloads directory into an `acquisition-manifest/1` batch (duplicate screen, DRM audit, PDF/DJVU battery, bindery's EPUB slice); embedded-metadata stamp seeds (via `ebook-meta`, filename parse as fallback; ISBN never seeded) and provenance seeds land in the manifest for the review step to correct; read-only against `metadata.db`; the emitted manifest is unsigned until `run sign` seals it |
| Run: sign | `run sign --manifest FILE` | Seal the reviewed manifest for phase 2: structure checks (no seal check, so re-signing after a deliberate edit works), then an HMAC seal over the approved set, the per-file stamps, provenance, and lossy flags, and the decisions list. The approved set must equal what the per-file verdicts imply (both divergence directions refused); `run approve --manifest FILE` re-derives the list from the verdicts, the sanctioned propagation for a reviewer flip |
| Run: phase2 | `run phase2 --manifest FILE` | Import the SIGNED, SEALED manifest as ONE `batch()` through `add_book` (the seal is recomputed at load; a post-sign edit refuses to load until re-signed); `#source`/`#audience` stamped (`#source` verified in-batch: a stamp that writes no state rolls the import back), tags+rating cleared on the imported ids only, downloads after the commit (failures become decisions), resumable |
| Run: phase3 | `run phase3 --manifest FILE` | Curate via TTY prompts or `--answer-file` in ONE `batch()`, then bindery phase3 + file reconciliation + re-validation to 0 errors and the prose batch record |
| Run: integration verbs | `run convert\|polish\|cover\|export\|merge\|flush\|backfill\|trash\|backup-metadata\|restore-database\|clone\|fts-index\|catalog-epub\|catalog-bibtex\|customize\|debug-tools\|device` | The Phase 19 C batch (dry-run by default; `--apply` requires a closed Calibre and, for metadata-mutating verbs, a `--backup-dir` outside the library). convert drives `ebook-convert` and registers the output through `add_format`; polish drives `ebook-polish` and re-syncs the size; cover drives cquarry's `set_cover`/`remove_cover`; export drives `calibredb export --template`; merge folds a duplicate's unique formats into the keeper and sends the duplicate to cquarry 1.20's trash; flush embeds the OPF queue via `calibredb embed_metadata` in chunks; backup-metadata regenerates the sidecar OPFs for the same queue through `calibredb backup_metadata` (`--all` widens to every book; backup required, Calibre closed); restore-database rebuilds a database from the stored OPFs under a required `--target` (an existing metadata.db there is replaced only under `--force`; the rebuild loses saved searches, user categories, plugboards, per-book conversion settings, and custom recipes, and the dry run says so); clone copies the SCHEMA into a fresh empty library under a required `--target` (custom columns, virtual libraries, saved searches, and settings; no books; the target must not exist or must be empty, and never collides with the source; no backup, the source is never opened writable); fts-index drives `calibredb fts_index` over the dirtied_formats queue read through cquarry's `get_dirtied_formats()` (the default action reindexes exactly the queued `book:FORMAT` pairs; `--fts-status` reports, upstream's disabled answer reads as a report; `--enable` writes the fts_enabled preference and carries its own backup rule); catalog-epub / catalog-bibtex drive `calibredb catalog` through the EPUB_MOBI and BIBTEX plugins over `--dest` (the extension decides the plugin and is enforced, because calibredb silently falls back to EPUB on anything else; targets resolve read-only, no selection meaning the whole library; the library is only ever read, no backup); customize drives `calibre-customize` (needs no library: `--list-plugins` reads, and `--add-plugin`/`--remove-plugin`/`--enable-plugin`/`--disable-plugin` are dry-run by default with `--apply` behind the closed-Calibre guard, because a live GUI keeps its plugin state in memory and writes its config on exit); debug-tools drives the curated calibre-debug subset (explode/implode/diff/kepubify/un-kepubify/inspect-mobi; the -e/--exec-file surface stays out: arbitrary code execution is not a verb; mutators dry-run by default with --apply behind the closed-Calibre guard; diff and inspect-mobi are reads); device drives the ebook-device USBMS subset (`--device-ls/--device-df/--device-books/--device-cat` read immediately; `--device-mkdir/--device-cp/--device-rm/--device-touch` are dry-run by default with `--apply`; no database involvement, so no closed-Calibre guard; MTP and the wireless Calibre-Companion stack stay excluded from parity); backfill drives `fetch-ebook-metadata` (network only at `--apply`) and applies the requested fields through cquarry writes; trash lists/empties/expires `.caltrash` through cquarry 1.20's verbs (a pure-filesystem lifecycle: no backup, dry-run listing by default). Targets resolve read-only from `--search`/`--ids`; unknown ids abort before anything opens writable |

### 3.1 Modifiers

| Flag | Effect |
|------|--------|
| `--show-tags` | Show tags instead of ratings in catalogs |
| `--show-id` | Prefix books with Calibre ID (for scripting) |
| `--show-custom COL` | Load and display a Calibre custom column |
| `--primary-only` | Collapse multi-author entries to first author |
| `--format {json,csv,ai,md}` | Output format for `--export` (json/csv/ai; default json) and `--search` (json/csv/ai; default: text listing); `md` renders the Markdown catalog shape for `--catalog`, `--wing`, and the sweeps |
| `--plugin-data NAME` | Append a `books_plugin_data` value (e.g. `goodreads_id`, `wordcount`) to catalog/search book lines |
| `--output PATH` | Write to a file instead of stdout |
| `--quiet` | Suppress decorative output |
| `--restrict SEARCH` | Scope every read mode to the books matching a search expression (see §3.4) |

### 3.2 Writes (opt-in)

Single-book verbs: `--set-title`, `--set-authors`, `--set-rating` (0 remaps
to a true clear since 3.29.0), `--set-pubdate`/`--clear-pubdate`,
`--set-comments`/`--clear-comments`,
`--set-column`/`--clear-column`, `--add-tag`/`--remove-tag`,
`--set-identifier`/`--clear-identifier`, `--set-series`
(+`--series-index`)/`--clear-series`, `--set-publisher`/`--clear-publisher`,
`--set-languages`/`--clear-languages`, `--add-format`/`--remove-format`,
`--set-cover`, guarded `--remove-book` (dry-run until
`--confirm-remove`), the curation verbs `--rename-entity KIND OLD NEW`
(everywhere, merging into an existing row) and `--set-author-sort` /
`--set-title-sort BOOK SORT` (verbatim passthroughs a later
`--set-authors`/`--set-title` recomputes over). Several verbs in one invocation share one `batch()`
transaction.

Set mode: exactly one target source per invocation (`--ids`,
`--from-search` resolved read-only, `--from-untagged`, `--from-manifest`;
hand-supplied ids are validated against the library and unknown ids abort
before anything opens writable) feeds any number of id-less `--batch-*`
verbs (`add`/`remove`/`clear` tags, `clear` rating, set/clear column,
add-column-value, set/clear pubdate, set title/authors/publisher/languages/
series, set/clear identifier, set cover, remove format). Deletion has no
set form. Dry-run by default; `--apply` requires a closed Calibre and a
`--backup-dir` outside the library directory, then runs as ONE
`batch()` transaction. `--commit-per-book` is the non-default escape
hatch and is real: each book is its own outermost transaction, a book
whose verbs failed rolls back alone (its entries report `rolled_back`,
`book_committed: false`), and the pass continues. Backups are
timestamped; a second run never destroys an earlier restore point.
Empty-string flag values are refused arguments (exit 2): clearing has
its own explicit `--batch-clear-*` verbs. `--batch-clear-rating` is
legal ONLY with `--from-manifest` naming a VALID, SEALED batch manifest,
and only for the ids that manifest records as imported (the library
NON-NEGOTIABLES bulk-ratings ban, mechanically encoded). The
`#reading_status`/`status`/`date_read` refusal is a shared chokepoint in
the writeops action builders, so the single-book verbs and the TUI are
closed by the same check as set mode. Reporting is per-verb
applied/already-so/failed plus a per-id failure list (it survives
`--quiet` on stderr); `--format json` emits `{target, ids, verbs,
results, committed, dry_run}`. Exit 0 committed/dry-run, 1 failures or
lock, 2 usage.

### 3.2.1 Library-schema writes (saved searches, custom-column CRUD)

`--saved-search-add NAME EXPR` / `--saved-search-delete NAME` /
`--saved-search-rename OLD NEW` manage the saved searches Calibre's GUI reads,
riding cquarry 1.24's typed `set_preference` writer (one `preferences` row,
payload validated by key before anything lands).
`--add-custom-column LABEL NAME DATATYPE` (with `--column-is-multiple` for
text and composite) and `--remove-custom-column LABEL` are the calibredb
schema-CRUD parity verbs over cquarry's column DDL: creation mirrors
upstream's `create_custom_column` DDL-for-DDL and sets Calibre's
`update_all_last_mod_dates_on_start` (the next GUI start refreshes every
book's last_modified), and deletion only FLAGS the column
(`mark_for_delete`) for the purge Calibre runs at its next start. All five
verbs change library-wide state, not books: any book verb or set-mode source
in the same invocation is refused, exactly one write runs per invocation, and
the rails hold in full (dry-run by default, `--apply` with the closed-Calibre
guard and an out-of-tree timestamped backup, exit 0/1/2). The rename resolves
the old name exactly then case-insensitively and refuses to overwrite an
existing name, where upstream's `saved_searches add` silently replaces; the
column doors refuse the NON-NEGOTIABLES labels (`#reading_status`, `status`,
`date_read`) at the argument layer, because deleting the column is the
biggest write to it there is.

### 3.3 Read-surface output guard

Every read-mode file output (`--export`, `--search --output`, `--catalog`,
`--audit`, `--export-annotations`, `--exportlt`) goes through
`cquarry_cli/output.py`. The database path and its sqlite sidecars
(`-wal`, `-shm`, `-journal`) are refused before anything opens, and the
directory-target exporters additionally refuse the library root itself
(their stale-file sweep would write and delete inside the library). A
refusal is an argument error: exit 2, nothing written. File outputs
stage through a temp file replaced into place only after the writer
closes clean, so a failed report never leaves a truncated file. This is
the last mile of the §4 read-only guarantee: the SQL layer opens
`mode=ro`, and the output layer can no longer clobber the database from
the write side of a report.

### 3.4 The restrict view (Phase 19)

`--restrict SEARCH` resolves once, read-only, through the search engine
(virtual libraries compose as `vl:Name`), and every read mode then
computes over that id set only. Mechanically it is a `RestrictedView`
(a read-only `CalibreDB` subclass sharing the open connection) whose
collection methods filter: analytics functions, integrity predicates,
and the series rollup run unchanged over the restricted universe, so
cquarry still derives every stat and the frontend only scopes inputs.
The two SQL-level aggregations (entity counts, format stats) are
recounted from the scoped rows and merged with the real rows'
secondary columns. Book-level audit classes and the FTS staleness
report follow the restriction; library-shape classes (orphan dirs,
malformed paths, root strays) always report globally against the
origin database, since they belong to no restriction. `--restrict`
with write verbs, the run verbs, `--book`, or `--id` is refused
(exit 2): write targets are chosen by `--ids`/`--from-search`, and
explicit ids are not a set to narrow. The refusal covers the run
subcommand specifically because its dispatch precedes the read-
surface refusal gate in `main()`. An unparseable expression exits 1, matching
`--search`; an empty result is a valid (empty) universe, not an error.

### 3.5 Full-text content search (Phase 19)

`--fts QUERY` searches the extracted plain text Calibre keeps in the
`full-text-search.db` sidecar (the plain `books_text` table; the FTS5
index tables are unqueryable outside Calibre). The match goes through
cquarry's `search_book_text` (case- and accent-folded substring
semantics, one hit per book naming its matching formats). `--fts-status`
and the tail of every `--fts` run report index staleness in separate
classes: never indexed (a text-capable format with no `books_text`
row), indexed empty (zero extracted text, no error), extraction errors
(`err_msg`), and stale-queued (the sidecar's `dirtied_formats` queue).
The extractor format set is deliberately conservative: formats outside
it are never reported as never indexed. A missing sidecar is the
"never indexed everything" case with a prose note, never an error.

---

## 4. What CalibreQuarry Is Not

- **Not a Calibre replacement.** It reads the database; it does not manage it.
- **Read-only by default; writes are explicit, opt-in verbs only.** Every read mode (`--catalog`, `--stats`, `--search`, `--export`, …) opens `metadata.db` strictly `mode=ro`. The only write paths are the explicit `--set-*` / `--remove-book` verbs and the set-mode `--batch-*` verbs (§3.2), which route through cquarry's separate `WritableCalibreDB` module and require Calibre to be closed. Nothing in the read path can ever mutate the database.
- **Not a converter in its own right.** The package's native surface never
  rewrites a book file: every read mode and every in-package write verb touches
  only `metadata.db` and its reports. What the toolkit does ship is
  orchestration of Calibre's own headless tools: `run convert` drives
  `ebook-convert` and registers the output through cquarry, `run polish` drives
  `ebook-polish`, and the §5 companion scripts rewrite book files directly.
- **Not a server.** It has no web interface, and the read surface has no network access. The one network-touching verb is `run backfill` at `--apply` (it drives `fetch-ebook-metadata`, an external calibre tool, per book); everything else runs entirely offline.

These guarantees apply to the `cquarry_cli` package only. The companion scripts in §5 are explicitly outside this contract.

---

## 5. Companion Scripts

The `scripts/` directory holds standalone maintenance tools that are **not** part of the `cquarry_cli` package and do **not** share its read-only or import guarantees. They are stdlib-only Python but shell out to external tools, and several of them write. Each is run directly (`python3 scripts/<name>.py`), not via the `cquarry` command.

| Script | What it does | Writes? | External tools |
|--------|--------------|---------|----------------|
| `compress_pdf.py` | Shrinks an oversize PDF via Ghostscript with verify-or-rollback; syncs the new size to `data.uncompressed_size` (and the Count Pages `books_pages_link.format_size` if present) so Calibre isn't stale | **Yes** (replaces the PDF; updates `metadata.db`) | `gs`, `pdfinfo`/`pdfimages`/`pdfdetach` (poppler) |
| `audit_drm.py` | Cross-format DRM scanner (EPUB/PDF/MOBI/AZW3; DJVU N/A) that clears benign look-alikes (font obfuscation, PDF permission flags) and catches residual handler dictionaries by streaming byte scan | No (`metadata.db` opened `mode=ro`) | `qpdf` (optional, to class Standard-encrypted PDFs) |
| `validate_metadata.py` | Integrity linter for `metadata.db` (missing language, duplicate ISBNs, junk identifiers, orphan links) plus an optional taxonomy-driven opinionated layer | No (`metadata.db` opened `mode=ro`) | none |
| `spot_check.py` | Randomized metadata + file-integrity audit: samples N random books and checks field quality (title corruption, junk authors, mojibake, stub comments) and file contents (EPUB archive/spine/text, PDF and DJVU page counts); `--review` emits judgement bundles for the checks no pattern can make (right title? right author? right description?), records verdicts to a ledger with exact id reconciliation, and excludes reviewed books from later samples | No (`metadata.db` opened `mode=ro`; the review ledger lives beside the reports, never in the DB) | `exiftool`, `djvused` (both optional) |
| `reconcile_file_metadata.py` | Diffs the curated `metadata.db` against each file's embedded metadata; `--apply` embeds the DB values back into drifted files and verifies the write by read-back (every claimed file is re-diffed after the pass; a file still drifted is a reported RESIDUAL and fails the run), and `--repair-pdf` rebuilds a broken PDF xref table so the embed can succeed | **Yes** with `--apply` (rewrites book files; never `metadata.db`) | `calibredb`, `exiftool`, `djvused`, `qpdf` (`--repair-pdf`) |
| `fetch_library_codes.py` | Queries the LoC SRU catalogue (`bath.isbn`) for Library of Congress Classification codes and stores them as `lcc` and optionally `ddc` identifiers (`--write-ddc`, or `--all-codes` for one pass over books missing either); an opt-in `--sru-fallback` answers a clean ISBN miss with one paced title/author query (the work-level hits the September 2026 hand pass proved out, tagged as such in the report); emits a misses worklist file for the manual-research pass; dry-run by default, disk-cached and resumable, rate-limited with backoff | **Yes** with `--apply` (writes through `cquarry.write.WritableCalibreDB` with OPF-resync queueing and locked-database retry; backs up `metadata.db` first, refuses while Calibre is open) | none (plain-HTTP SRU endpoint) |
| `audit_isbns.py` | Checks each stored ISBN against the ISBN the book prints on its own copyright page, catching identifiers that point at a different book (usually a same-publisher sibling). Reads body text only, never embedded metadata, since `reconcile_file_metadata.py` writes the DB's values there and comparing against them would be circular. Classifies apart the three benign look-alikes: bibliographies, bundles/series, and format variants | No (`metadata.db` opened `mode=ro`; deliberately has no `--apply`) | `pdftotext`/`pdfinfo` (poppler), `djvutxt` (both optional) |
| `audit_conversion_overrides.py` | Lists books carrying manual per-book conversion overrides (`conversion_options`), so pipeline drift is visible instead of surprising | No (`metadata.db` opened `mode=ro`) | none |
| `audit_duplicates_content.py` | Content-duplicate fingerprinting across books: 64-bit simhash over 3-word shingles of spine text (largest text-bearing format per book), near-duplicate candidates by Hamming distance plus a bottom-32 sketch pass for containment candidates; exact shingle-set classification into `near_duplicate` (re-download under wrong metadata) vs `omnibus_overlap` (the larger book contains the smaller's text). Short documents and formats without an extractor are excluded, not guessed | No (`metadata.db` opened `mode=ro`; files read in place) | `pdftotext` (optional, for PDF text) |
| `screen_duplicate.py` | Screens loose downloads against the library (and within the batch) for duplicates: exact ISBN first, then normalized title + first author via the search engine, with differing declared volume annotations surfaced as one multi-volume set (informational) and colon-boundary title containment surfaced as related candidates for human judgment (never auto-refused); reads embedded metadata with `ebook-meta`; report-only | No (`metadata.db` opened `mode=ro`) | `ebook-meta` |
| `audit_truncation.py` | Cross-checks the Count Pages plugin's `books_pages_link` rows against the real PDFs (poppler `pdfinfo`): `page_count_mismatch` beyond `--tolerance` (default 20%) and `stale_plugin_data` as its own class (format_size drift, the plugin's needs_scan flag, post-scan mtime). Only PDF rows are checked; the plugin's EPUB pages are estimates by design | No (`metadata.db` opened `mode=ro`) | `pdfinfo` (poppler) |
| `audit_cover_aspect.py` | Sizes every catalogued cover through cquarry's header-only image readers and reports advisory ratio bands: `cover_aspect_narrow` (w/h < 0.55, spine scans and bad crops) and `cover_aspect_wide` (> 0.80, landscape or square art; legitimate art exists, the operator judges). Missing/unreadable covers stay `find_missing_cover_files`' class | No (`metadata.db` opened `mode=ro`; cover files read header-only) | none |
| `stamp_pdf.py` | Pre-stamps PDF metadata (title/author/publisher, ISBN via keywords) so imports land with real titles; `--isbn` failing its check digit is refused (exit 2, dry-run and apply alike); verifies via `ebook-meta`; dry-run by default, `--apply` with a mandatory out-of-tree `--backup-dir` | **Yes** with `--apply` (rewrites the PDF; never `metadata.db`) | `exiftool`, `ebook-meta` |
| `stamp_epub.py` | The EPUB sibling of `stamp_pdf.py`: pre-stamps title/authors/publisher/ISBN through one `ebook-meta` write (Calibre rewrites the OPF in place and REPLACES any existing isbn identifier, the correction mechanism for wrong embedded ISBNs); `--isbn` failing its check digit is refused (exit 2, dry-run and apply alike); verification reads title/authors/publisher from the `ebook-meta` read-back (author compared as the display segment before the ` [` bracket) and the ISBN from a direct OPF read (zipfile -> `dc:identifier`) under VALUE EQUALITY, so every producer spelling verifies and an already-right ISBN is an honest no-op; a failed stamp STOPS the list with the remainder named; dry-run by default, `--apply` with a mandatory out-of-tree `--backup-dir` | **Yes** with `--apply` (rewrites the EPUB; never `metadata.db`) | `ebook-meta` |
| `check_pdf.py` | The per-file PDF/DJVU battery as a standing tool (the phase-1 prose check, retired from the skill): magic-byte header, page count, `qpdf --check` classified clean/warnings/errors, non-embedded fonts, text layer sampled at first/middle/last page, image count and area-weighted DPI; `--json FILE` is the machine report the phase-1 runner consumes | No | `qpdf`, `pdffonts`/`pdftotext`/`pdfimages` (poppler), `djvused`; every tool optional (a missing one marks its check unavailable) |
| `comments_census.py` | The description field's mechanical-defect sweep (double hyphens, spaced-hyphen dashes, stray markdown bold, tag debris, body shape, soft hyphens, zero-width characters, mojibake, lost ligatures, duplicate bodies); the fix is phase 3's curated rewrite or `--set-comments`, never this script | No (`metadata.db` opened `mode=ro`) | none |
| `db_util.py` | Shared import for the other scripts (not run directly): the read-only URI builder and the locked-database fallback that reads through a temp snapshot copy when Calibre holds the lock | No | none |

Write capability is the reason these live outside the package: `compress_pdf.py`, `stamp_pdf.py --apply`, `stamp_epub.py --apply`, `reconcile_file_metadata.py --apply`, and `fetch_library_codes.py --apply` mutate things, which the `cquarry` core forbids. Keeping them adjacent but separate preserves the toolkit's read-only promise.
