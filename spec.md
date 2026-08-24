# CalibreQuarry — Application Specification

**Version:** 3.13.0  
**Language:** Python 3.14+  
**Dependencies:** `cquarry`, `vir-tui`, `tqdm` (minimal-dependency (uses tqdm): sqlite3, json, csv, argparse, re, unicodedata, datetime)  
**License:** MIT

---

## 1. Mission Statement

CalibreQuarry is a CLI toolkit for Calibre users who treat their libraries as curated collections. It reads `metadata.db` directly in read-only mode — bypassing the overhead of `calibredb`, JSON intermediaries, or external library dependencies.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline with a single command.** The script resolves Calibre's **Virtual Library** (Wing) search expressions natively, ensuring existing library definitions work without re-encoding.

---

## 2. Architecture

### 2.1 Decoupled Shared Library Architecture
The CalibreQuarry architecture relies on a strict separation of concerns, decoupling the CLI/TUI frontend from the database and search logic. 

**`cquarry` (External Dependency)**: The core database connection, schema mapping, Calibre lock handling (snapshots), and the search grammar AST parser are provided by the `cquarry` standalone package. This ensures parity across the ecosystem.

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

**Deliberate, dependency-bound deviations from Calibre** (it is minimal-dependency (uses tqdm)):

- `~` regex uses the stdlib `re` engine, not Calibre's third-party `regex` module.
- Accent/contains folding uses `unicodedata` (NFKD), not ICU collation, so punctuation-insensitivity is not reproduced.
- GPM templates (`@...:`) and saved-search references (`search:`) are not evaluated.
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
| Audit | `--audit` | Untagged, unrated, coverless/low-res books, and covers the DB claims but the disk lacks; deprecated formats; duplicates; series gaps |
| Recent | `--recent N` | N most recently added books |
| Series | `--series` | All series with completeness and gap detection |
| Analytics | `--analytics {author,pace,tags,overlap}` | Per-author stats, reading-pace trend, tag tree, Wing overlap |
| Export | `--export` | Full library to JSON, CSV, or AI-readable format |
| Search | `--search QUERY` | Books matching a search expression; prints to stdout, or a file with `--output` |
| Wings | `--wings` | List virtual libraries with book counts |
| Tags | `--tags` | Flat dump of every tag in the library with its book count |
| Interactive | (no args) | Launch the Curses TUI with scrollable output pager |

### 3.1 Modifiers

| Flag | Effect |
|------|--------|
| `--show-tags` | Show tags instead of ratings in catalogs |
| `--show-id` | Prefix books with Calibre ID (for scripting) |
| `--show-custom COL` | Load and display a Calibre custom column |
| `--primary-only` | Collapse multi-author entries to first author |
| `--format {json,csv,ai}` | Output format for `--export` (default json) and `--search` (default: text listing) |
| `--output PATH` | Write to a file instead of stdout |
| `--quiet` | Suppress decorative output |

---

## 4. What CalibreQuarry Is Not

- **Not a Calibre replacement.** It reads the database — it does not manage it.
- **Not an editor.** It never writes to `metadata.db`.
- **Not a converter.** It does not touch book files themselves.
- **Not a server.** It has no web interface and no network access.

These guarantees apply to the `cquarry_cli` package only. The companion scripts in §5 are explicitly outside this contract.

---

## 5. Companion Scripts

The `scripts/` directory holds standalone maintenance tools that are **not** part of the `cquarry_cli` package and do **not** share its read-only or import guarantees. They are minimal-dependency (uses tqdm) Python but shell out to external tools, and three of them write. Each is run directly (`python3 scripts/<name>.py`), not via the `cquarry` command.

| Script | What it does | Writes? | External tools |
|--------|--------------|---------|----------------|
| `compress_pdf.py` | Shrinks an oversize PDF via Ghostscript with verify-or-rollback; syncs the new size to `data.uncompressed_size` (and the Count Pages `books_pages_link.format_size` if present) so Calibre isn't stale | **Yes** (replaces the PDF; updates `metadata.db`) | `gs`, `pdfinfo`/`pdfimages`/`pdfdetach` (poppler) |
| `audit_drm.py` | Cross-format DRM scanner (EPUB/PDF/MOBI/AZW3; DJVU N/A) that clears benign look-alikes (font obfuscation, PDF permission flags) and catches residual handler dictionaries by streaming byte scan | No (`metadata.db` opened `mode=ro`) | `qpdf` (optional, to class Standard-encrypted PDFs) |
| `validate_metadata.py` | Integrity linter for `metadata.db` (missing language, duplicate ISBNs, junk identifiers, orphan links) plus an optional taxonomy-driven opinionated layer | No (`metadata.db` opened `mode=ro`) | none |
| `spot_check.py` | Randomized metadata + file-integrity audit: samples N random books and checks field quality (title corruption, junk authors, mojibake, stub comments) and file contents (EPUB archive/spine/text, PDF and DJVU page counts); `--review` emits judgement bundles for the checks no pattern can make (right title? right author? right description?), records verdicts to a ledger with exact id reconciliation, and excludes reviewed books from later samples | No (`metadata.db` opened `mode=ro`; the review ledger lives beside the reports, never in the DB) | `exiftool`, `djvused` (both optional) |
| `reconcile_file_metadata.py` | Diffs the curated `metadata.db` against each file's embedded metadata; `--apply` embeds the DB values back into drifted files, and `--repair-pdf` rebuilds a broken PDF xref table so the embed can succeed | **Yes** with `--apply` (rewrites book files; never `metadata.db`) | `calibredb`, `exiftool`, `djvused`, `qpdf` (`--repair-pdf`) |
| `fetch_library_codes.py` | Queries the LoC SRU catalogue (`bath.isbn`) for Library of Congress Classification codes and stores them as `lcc` (optionally `ddc`) identifiers; dry-run by default, disk-cached and resumable, rate-limited with backoff | **Yes** with `--apply` (inserts identifiers; backs up `metadata.db` first, refuses while Calibre is open) | none (plain-HTTP SRU endpoint) |
| `audit_isbns.py` | Checks each stored ISBN against the ISBN the book prints on its own copyright page, catching identifiers that point at a different book (usually a same-publisher sibling). Reads body text only, never embedded metadata, since `reconcile_file_metadata.py` writes the DB's values there and comparing against them would be circular. Classifies apart the three benign look-alikes: bibliographies, bundles/series, and format variants | No (`metadata.db` opened `mode=ro`; deliberately has no `--apply`) | `pdftotext`/`pdfinfo` (poppler), `djvutxt` (both optional) |

Write capability is the reason these live outside the package: `compress_pdf.py`, `reconcile_file_metadata.py --apply`, and `fetch_library_codes.py --apply` mutate things, which the `cquarry` core forbids. Keeping them adjacent but separate preserves the toolkit's read-only promise.
