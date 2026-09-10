# CalibreQuarry — Application Specification

**Version:** 3.36.0  
**Language:** Python 3.14+  
**Dependencies:** `cquarry`, `vir-tui`, `tqdm` (stdlib sqlite3, json, csv, argparse, re, unicodedata, datetime)  
**License:** MIT

---

## 1. Mission Statement

CalibreQuarry is a CLI toolkit for Calibre users who treat their libraries as curated collections. It reads `metadata.db` directly in read-only mode — bypassing the overhead of `calibredb`, JSON intermediaries, or external library dependencies.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline with a single command.** The script resolves Calibre's **Virtual Library** (Wing) search expressions natively, ensuring existing library definitions work without re-encoding.

---

## 2. Architecture

### 2.1 Decoupled Shared Library Architecture
The CalibreQuarry architecture relies on a strict separation of concerns, decoupling the CLI/TUI frontend from the database and search logic. 

**`cquarry` (External Dependency)**: The core database connection, schema mapping, Calibre lock handling (snapshots), and the search grammar AST parser are provided by the `cquarry` standalone package. This ensures parity across the ecosystem. Requires cquarry >= 1.14.0: `get_all_books()` hydrates `authors`/`tags`/`languages`/`formats` as native lists (never comma-split them), rows carry a computed `size`, saved searches interpolate via `search:"Name"`, multi-valued count operators (`tags:#>2`) and language canonicalization are engine-level, unknown virtual libraries raise instead of matching nothing, the `--set-*`/`--remove-book` write verbs run on `WritableCalibreDB` (`set_pubdate`, `batch()`), `analytics.genre_distribution()` powers `--analytics genres`, the set-mode verbs consume the 1.13 write helpers (`clear_tags`, `add_custom_column_values`, `clear_rating`), and the `run phase2` import consumes 1.14's `add_book` creation path.

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
a different file. All data comes from standard Calibre tables — no custom
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
| Catalog | `--catalog` | Formatted text grouped by author with ratings and series |
| All wings | `--all-wings` | Separate catalog per virtual library |
| Statistics | `--stats` | Format breakdown, ratings, tags, publishers |
| Audit | `--audit` | Untagged, unrated, coverless/low-res books, and covers the DB claims but the disk lacks; deprecated formats; duplicates; series gaps; manual conversion overrides (per-book `conversion_options`, surfaced by size and format, never unpickled) |
| Recent | `--recent N` | N most recently added books |
| Series | `--series` | All series with completeness and gap detection |
| Analytics | `--analytics {author,pace,tags,genres,overlap}` | Per-author stats, reading-pace trend, tag tree, genre share breakdown (`--genre-depth N` for deeper hierarchy levels), Wing overlap |
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
| Run: phase1 | `run phase1 DIR` | Vet a downloads directory into an `acquisition-manifest/1` batch (duplicate screen, DRM audit, PDF/DJVU battery, bindery's EPUB slice); filename-derived stamps and provenance seeds land in the manifest for the review step to correct; read-only against `metadata.db`; the emitted manifest is unsigned until `run sign` seals it |
| Run: sign | `run sign --manifest FILE` | Seal the reviewed manifest for phase 2: structure checks (no seal check, so re-signing after a deliberate edit works), then an HMAC seal over the approved set, the per-file stamps, provenance, and lossy flags, and the decisions list |
| Run: phase2 | `run phase2 --manifest FILE` | Import the SIGNED, SEALED manifest as ONE `batch()` through `add_book` (the seal is recomputed at load; a post-sign edit refuses to load until re-signed); `#source`/`#audience` stamped, tags+rating cleared on the imported ids only, downloads after the commit (failures become decisions), resumable |
| Run: phase3 | `run phase3 --manifest FILE` | Curate via TTY prompts or `--answer-file` in ONE `batch()`, then bindery phase3 + file reconciliation + re-validation to 0 errors and the prose batch record |

### 3.1 Modifiers

| Flag | Effect |
|------|--------|
| `--show-tags` | Show tags instead of ratings in catalogs |
| `--show-id` | Prefix books with Calibre ID (for scripting) |
| `--show-custom COL` | Load and display a Calibre custom column |
| `--primary-only` | Collapse multi-author entries to first author |
| `--format {json,csv,ai}` | Output format for `--export` (default json) and `--search` (default: text listing) |
| `--plugin-data NAME` | Append a `books_plugin_data` value (e.g. `goodreads_id`, `wordcount`) to catalog/search book lines |
| `--output PATH` | Write to a file instead of stdout |
| `--quiet` | Suppress decorative output |

### 3.2 Writes (opt-in)

Single-book verbs: `--set-title`, `--set-authors`, `--set-rating` (0 remaps
to a true clear since 3.29.0), `--set-pubdate`/`--clear-pubdate`,
`--set-comments`/`--clear-comments`,
`--set-column`/`--clear-column`, `--add-tag`/`--remove-tag`,
`--set-identifier`/`--clear-identifier`, `--set-series`
(+`--series-index`)/`--clear-series`, `--set-publisher`/`--clear-publisher`,
`--set-languages`/`--clear-languages`, `--add-format`/`--remove-format`,
`--set-cover`, and guarded `--remove-book` (dry-run until
`--confirm-remove`). Several verbs in one invocation share one `batch()`
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

---

## 4. What CalibreQuarry Is Not

- **Not a Calibre replacement.** It reads the database — it does not manage it.
- **Read-only by default; writes are explicit, opt-in verbs only.** Every read mode (`--catalog`, `--stats`, `--search`, `--export`, …) opens `metadata.db` strictly `mode=ro`. The only write paths are the explicit `--set-*` / `--remove-book` verbs and the set-mode `--batch-*` verbs (§3.2), which route through cquarry's separate `WritableCalibreDB` module and require Calibre to be closed. Nothing in the read path can ever mutate the database.
- **Not a converter.** It does not touch book files themselves.
- **Not a server.** It has no web interface and no network access.

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
| `reconcile_file_metadata.py` | Diffs the curated `metadata.db` against each file's embedded metadata; `--apply` embeds the DB values back into drifted files, and `--repair-pdf` rebuilds a broken PDF xref table so the embed can succeed | **Yes** with `--apply` (rewrites book files; never `metadata.db`) | `calibredb`, `exiftool`, `djvused`, `qpdf` (`--repair-pdf`) |
| `fetch_library_codes.py` | Queries the LoC SRU catalogue (`bath.isbn`) for Library of Congress Classification codes and stores them as `lcc` and optionally `ddc` identifiers (`--write-ddc`, or `--all-codes` for one pass over books missing either); emits a misses worklist file for the manual-research pass; dry-run by default, disk-cached and resumable, rate-limited with backoff | **Yes** with `--apply` (writes through `cquarry.write.WritableCalibreDB` with OPF-resync queueing and locked-database retry; backs up `metadata.db` first, refuses while Calibre is open) | none (plain-HTTP SRU endpoint) |
| `audit_isbns.py` | Checks each stored ISBN against the ISBN the book prints on its own copyright page, catching identifiers that point at a different book (usually a same-publisher sibling). Reads body text only, never embedded metadata, since `reconcile_file_metadata.py` writes the DB's values there and comparing against them would be circular. Classifies apart the three benign look-alikes: bibliographies, bundles/series, and format variants | No (`metadata.db` opened `mode=ro`; deliberately has no `--apply`) | `pdftotext`/`pdfinfo` (poppler), `djvutxt` (both optional) |
| `audit_conversion_overrides.py` | Lists books carrying manual per-book conversion overrides (`conversion_options`), so pipeline drift is visible instead of surprising | No (`metadata.db` opened `mode=ro`) | none |
| `screen_duplicate.py` | Screens loose downloads against the library (and within the batch) for duplicates: exact ISBN first, then normalized title + first author via the search engine; reads embedded metadata with `ebook-meta`; report-only | No (`metadata.db` opened `mode=ro`) | `ebook-meta` |
| `stamp_pdf.py` | Pre-stamps PDF metadata (title/author/publisher, ISBN via keywords) so imports land with real titles; verifies via `ebook-meta`; dry-run by default, `--apply` with a mandatory out-of-tree `--backup-dir` | **Yes** with `--apply` (rewrites the PDF; never `metadata.db`) | `exiftool`, `ebook-meta` |

Write capability is the reason these live outside the package: `compress_pdf.py`, `stamp_pdf.py --apply`, `reconcile_file_metadata.py --apply`, and `fetch_library_codes.py --apply` mutate things, which the `cquarry` core forbids. Keeping them adjacent but separate preserves the toolkit's read-only promise.
