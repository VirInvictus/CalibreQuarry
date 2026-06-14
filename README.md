<p align="center">
  <img src="logo.svg" alt="CalibreQuarry" width="680">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python 3.14+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

A CLI toolkit for Calibre users who treat their libraries as curated collections. Reads `metadata.db` directly — no `calibredb` dependency, no JSON intermediaries, no external libraries. Pure Python stdlib.

> **Note:** This is considered completed software. It is effectively feature complete; bug fixes will be addressed as they come, but no new features are planned. It has been thoroughly tested and is known to be fully functional on the primary development environment: **Fedora Linux 44 (Workstation Edition)**, kernel `7.0.9-205.fc44.x86_64`, using **Calibre 9.8** on **Python 3.14**. While it is pure Python and should be cross-platform, this specific setup is the only officially tested environment.

## Contents

- [Why this exists](#why-this-exists)
- [Features](#features)
- [Installation](#installation) · [Requirements](#requirements)
- [Usage](#usage) · [Recipes](#recipes)
- [Sample output](#sample-output)
- [Search syntax & virtual library resolution](#search-syntax--virtual-library-resolution)
- [Troubleshooting](#troubleshooting)
- [How it reads the database](#how-it-reads-the-database)
- [Full help output](#full-help-output)
- [Companion scripts](#companion-scripts)

## Why this exists

Calibre is a good database. It is not a good reporting tool. If you maintain a large library (3000+ books) organized with virtual libraries, hierarchical tags, and series tracking, you eventually want answers to questions Calibre's UI doesn't surface well: which series have gaps, how many books are unrated, what does a given wing actually contain, and can I get a machine-readable export without running `calibredb list` through a parser script.

This tool reads the SQLite database directly in read-only mode. It ships a near-complete port of Calibre's own search engine (field prefixes like `tags:`, `author:`, `series:`, `rating:`, `pubdate:`; `vl:` cross-references; boolean and hierarchical matching), so your existing wing definitions and search habits work without being re-encoded anywhere.

## Features

| Mode | Flag | Description |
|------|------|-------------|
| **Catalog** | `--catalog` | Formatted text catalog grouped by author, with ratings and series info |
| **All wings** | `--all-wings` | Generate a separate catalog file for every virtual library |
| **Statistics** | `--stats` | Format breakdown, rating distribution, tag taxonomy, publisher counts |
| **Audit** | `--audit` | Report untagged, unrated, coverless, and low-resolution-cover books; deprecated-format-only and duplicate books; detect series gaps |
| **Recent** | `--recent N` | Show the N most recently added books (default: 20) |
| **Series** | `--series` | List all series with completeness status and gap detection |
| **Analytics** | `--analytics {author,pace,tags,overlap}` | Per-author breakdowns, reading-pace trend, tag-taxonomy tree, Wing-overlap analysis |
| **Export** | `--export` | Full library export to JSON, CSV, or an AI-readable flat format |
| **Search** | `--search QUERY` | Books matching a Calibre search expression; prints to stdout, or to a file with `--output` |
| **Wings** | `--wings` | List all virtual libraries with book counts |
| **Tags** | `--tags` | Flat dump of every tag with its book count |
| **Version** | `--version` | Show version and exit |

Modifiers: `--show-tags` swaps ratings for tag display in catalogs, `--show-id` prefixes each book with its Calibre ID (useful for scripting against `calibredb set_metadata`), `--show-custom COL` loads a Calibre custom column, `--primary-only` collapses multi-author entries to the first author, `--format {json,csv,ai}` selects the output shape for `--export` and `--search`, `--output PATH` writes to a file instead of stdout, `--quiet` suppresses decorative output.

Running with no arguments launches a full-screen interactive TUI (arrow-key navigable) with a built-in scrollable output pager, or a text-based menu if `curses` is unavailable. The TUI remembers your database path between sessions.

## Installation

```bash
pip install .
# or
pipx install .
```

This gives you the `cquarry` command:

```bash
cquarry --catalog --db ~/Calibre/metadata.db
cquarry --stats
cquarry   # launches interactive TUI
```

Or run without installing:

```bash
PYTHONPATH=src python -m cquarry --stats
```

## Requirements

Python 3.14+. Zero external dependencies — uses only stdlib modules (`sqlite3`, `json`, `csv`, `argparse`, `curses`, `re`, `unicodedata`, `datetime`).

(3.14 is the tested floor, matching the development environment. The code does not lean on bleeding-edge language features, so it is likely fine on somewhat older interpreters, but only 3.14+ is supported.)

## Usage

```bash
# Build a catalog for a specific wing
cquarry --catalog --wing "The Tabletop" --primary-only --db ~/Calibre/metadata.db

# Same catalog, but showing tags instead of star ratings
cquarry --catalog --wing "The Tabletop" --show-tags --db ~/Calibre/metadata.db

# Catalog with Calibre IDs (for piping into calibredb set_metadata scripts)
cquarry --catalog --show-id --db ~/Calibre/metadata.db

# Generate catalogs for all virtual libraries at once
cquarry --all-wings --db ~/Calibre/metadata.db --outdir ~/docs/catalogs

# Library statistics
cquarry --stats --db ~/Calibre/metadata.db

# Audit: find unrated books, missing tags, series gaps
cquarry --audit --db ~/Calibre/metadata.db --output audit.csv

# Recently added books
cquarry --recent 10 --db ~/Calibre/metadata.db

# Series completeness and gap detection
cquarry --series --db ~/Calibre/metadata.db

# Extended analytics: per-author stats, reading pace, tag tree, wing overlap
cquarry --analytics author --db ~/Calibre/metadata.db
cquarry --analytics pace --db ~/Calibre/metadata.db

# Export full library to JSON (or CSV, or an AI-readable flat format)
cquarry --export --db ~/Calibre/metadata.db --format json --output library.json

# Search with a Calibre expression — prints to the terminal by default
cquarry --search 'series:Mistborn and rating:>=4' --db ~/Calibre/metadata.db

# Same search as JSON, written to a file
cquarry --search 'tags:Fic.SciFi and pubdate:>2015' --format json --output recent_scifi.json

# Display a custom column alongside catalog/export output
cquarry --catalog --show-custom "Status" --db ~/Calibre/metadata.db

# List all virtual library wings with counts
cquarry --wings --db ~/Calibre/metadata.db

# Dump every tag with its book count (replaces `calibredb list_categories -r tags`)
cquarry --tags > ~/docs/catalogs/tags.txt

# Check version
cquarry --version
```

If `metadata.db` is in the current directory or at `~/Calibre Library/metadata.db`, the `--db` flag can be omitted. On first run you'll be prompted for the path, which is saved to `~/.config/cquarry/config.json` for future sessions. If Calibre is running and has the database locked, CalibreQuarry will automatically read from a temporary snapshot.

## Recipes

Common questions mapped to a single command. These assume `--db` is configured (omit it after the first run). `--search` prints to the terminal; add `--output FILE` to save, or `--format json|csv|ai` to change the shape.

**Curation and triage**

```bash
# What haven't I rated yet?
cquarry --search 'rating:false'

# Unrated books in a specific genre
cquarry --search 'tags:Fic.Fantasy and rating:false'

# Books with no cover, or a cover so small it should be replaced
cquarry --search 'cover:false'
cquarry --audit                       # the low_res_cover rows in the report

# Books I have only as PDF (conversion / re-acquisition candidates)
cquarry --search 'formats:PDF and not formats:EPUB'

# Books with no ISBN recorded
cquarry --search 'not identifiers:isbn:true'

# Everything still in a deprecated-only format, plus duplicates and series gaps
cquarry --audit --output audit.csv
```

**Discovery and reading planning**

```bash
# Top-rated science fiction
cquarry --search 'tags:Fic.SciFi and rating:5'

# Added in the last month / since a date
cquarry --search 'date:30daysago'
cquarry --search 'date:>=2026-01-01'

# Everything by an author (substring; quote names with spaces)
cquarry --search 'author:"Brandon Sanderson"'

# Which series are incomplete, and what's missing
cquarry --series

# What's actually inside a wing
cquarry --catalog --wing "Sci-Fi Wing" --output scifi.txt
```

**Exporting and feeding other tools**

```bash
# A whole wing as a compact, token-efficient list for an LLM prompt
cquarry --search 'tags:Fic.Fantasy' --format ai --output fantasy.ai.txt

# Full library as JSON / CSV for a spreadsheet or script
cquarry --export --format csv --output library.csv

# Calibre IDs for a batch calibredb operation
cquarry --catalog --show-id --wing "Cooking" | grep '^\s*\*'
```

## Sample output

### Catalog (`--catalog`)

```
Calibre Library Export — 2026-03-27 19:38 [The Tabletop]
========================================================

[Avery Alder]
-------------
  * The Quiet Year [PDF]

[Emmy Allen]
------------
  * The Gardens of Ynn [PDF]
  * The Stygian Library [PDF]

[Aaron Allston]
---------------
  * Dungeons and Dragons Rules Cyclopedia [PDF] [★★★★☆ 4.0/5]
```

### Statistics (`--stats`)

```
=== Library Statistics (3853 books) ===

Formats:
  EPUB    2571  ██████████████████████████
  PDF     1208  ████████████
  DJVU      65
  MOBI       8
  AZW3       3

Ratings:
  ★★★   (3.0)     81  █
  ★★★★  (4.0)   2031  ████████████████████████████████████████
  ★★★★★ (5.0)    135  ██
  Unrated:        1579  (41.0%)

Tag taxonomy (392 tags):
  NonFic: 276 tags
  Fic: 98 tags
  Gaming: 17 tags
```

### Series (`--series`)

```
  A Song of Ice and Fire: 5 of 5 (complete)
  Asian Saga: Chronological Order: 4 of 6 (incomplete)  ⚠ missing: 2, 3
  Aubrey-Maturin: 20 of 20 (complete)
  Discworld: 41 of 41 (complete)
  Parker: 10 of 18 (incomplete)  ⚠ missing: 8, 9, 10, 11, 12, 13, 14, 15
```

## Search Syntax & Virtual Library Resolution

CalibreQuarry ships a pure-Python search engine (`src/cquarry/search.py`) that ports Calibre's grammar and matching semantics as closely as the standard library allows. The same engine resolves Virtual Libraries (Wings) directly from the `preferences` table and powers the `--search` CLI mode, so your existing wing definitions work unchanged.

```
# Virtual Library Definitions
Fantasy Wing:    tags:"Fic.Fantasy" or tags:"Fic.Speculative.Fantasy"
The Tabletop:    tags:"Gaming.TTRPG"
Unsorted:        not (vl:"The Tabletop" or vl:"Fantasy Wing" or ...)

# CLI Search Queries
cquarry --search 'NOT(tags:Fic.Romance OR tags:Fic.Contemporary)'
cquarry --search 'tags:"Fic.Fantasy.Grimdark" AND author:"Phil Tucker"'
```

### Supported Search Features

* **Field locations**: `title`, `authors`/`author`, `author_sort`, `series`, `publisher`, `tags`/`tag`, `rating`, `formats`/`format`, `languages`/`language`, `pubdate`, `timestamp`/`date`, `last_modified`, `identifiers`/`identifier`/`isbn`, `comments`/`comment`, `cover`, `id`, `uuid`, `#custom` columns, plus `all` and `vl:`.
* **General Text Search**: An un-prefixed term (e.g., `Rice`) is matched across title, authors, series, publisher, tags, and comments.
* **Hierarchical tags**: `tags:Fic.Fantasy` matches `Fic.Fantasy` and everything below it (`Fic.Fantasy.Epic`, `Fic.Fantasy.Grimdark`, ...). Prepend `=` for an exact match: `tags:"=Fic.Fantasy"`.
* **Match kinds**: contains (default; case- and accent-insensitive), `=` exact, `~` regex, `^` accent.
* **Numbers and dates**: relational operators on numeric fields (`rating:>=4`, `id:<100`) and dates (`pubdate:>2015`, `date:>=2024-01-01`, `timestamp:30daysago`); `field:true`/`field:false` test presence/absence.
* **Boolean logic**: `AND`, `OR`, `NOT`, with implicit `AND` between space-separated terms (`tags:Fic tags:SciFi` == `tags:Fic AND tags:SciFi`), and parentheses for grouping (`(tags:Fic OR tags:NonFic) AND NOT tags:Gaming`).
* **Virtual Library Referencing**: `vl:"Wing Name"` cross-references an existing Wing (recursion is detected and reported).
* **Empty query**: an empty `--search ''` returns the whole library, matching Calibre.

#### Parity scope (stdlib-only deviations)

Matching is near-complete but not bit-for-bit identical to Calibre, by design: CalibreQuarry has zero dependencies, while a few of Calibre's behaviors are tied to third-party libraries.

* `~` regex uses Python's stdlib `re`, not Calibre's `regex` module (`\X`, `VERSION1` semantics differ).
* Accent/contains folding uses `unicodedata` (NFKD), not ICU, so it is accent- and case-insensitive but not punctuation-insensitive.
* GPM templates (`@...:`) and saved-search references (`search:`) are not evaluated.
* `tags:` is **anchored-hierarchical** (matches `Foo` and `Foo.*`), where Calibre's raw default is an unanchored substring. This is intentional and is what curated dotted taxonomies want; use `=` for strict exact.

### Custom columns

Custom columns are referred to by **two different names**, which is easy to trip over:

| Where | Which name | Example |
|-------|-----------|---------|
| `--show-custom` | the column's **display name** (what you see in Calibre) | `--show-custom "Status"` |
| `--search` (the `#` prefix) | the column's **lookup name** (label), prefixed with `#` | `--search '#reading_status:Read'` |

These two names are often different (display "Status", lookup `reading_status`). In Calibre, the lookup name is the one shown in *Preferences → Add your own columns* under "Lookup name"; the `#` search prefix always uses that one. If `--show-custom` reports "not found", the error lists the valid display names.

**Watch the contains-vs-exact trap on enumerations.** A custom search is a substring match by default, so `#reading_status:Read` also matches `Reading` and `To Read` (both contain "read"). For the exact value, use `=`: `#reading_status:=Read`. Quote values with spaces: `#reading_status:"=To Read"`.

### Quote Handling (`"` and `'`)

When running searches via the command line with `--search`, you must navigate your shell's quote-escaping rules. Items can be explicitly `""`'d or written unquoted (if they do not contain spaces).

1. **Wrap the entire query in single quotes (`'`)**: This prevents your bash/zsh shell from trying to interpret spaces or special characters.
2. **Use double quotes (`"`) inside the query**: Use double quotes around tag names, author names, or virtual library names if they contain spaces.

**Good Examples:**
```bash
cquarry --search 'NOT(tags:Fic.Romance OR tags:Fic.Contemporary)'
cquarry --search 'tags:"Fic.Fantasy.Grimdark" AND author:"Phil Tucker"'
cquarry --search "author:Anne Rice"  # Handled natively as author:Anne AND Rice
```

**What to Avoid:**
* Unquoted spaces will break your shell command: `cquarry --search tags:Fic OR tags:SciFi` (Your shell thinks `OR` is a separate argument; instead use `--search 'tags:Fic OR tags:SciFi'`).
* Mismatched quotes will cause parsing errors: `cquarry --search "tags:'Fic.SciFi'"` (Calibre expects double quotes `"` internally, not single quotes).

### Automated Test Suite

`tests/test_search.py` and `tests/test_helpers.py` run without a Calibre library (stdlib `unittest`):

- **Grammar** (`tests/test_search.py`): parser AST cases adapted from Calibre's own `search_query_parser_test.py`, covering quotes, escapes, colon handling in values, implicit `AND`, `OR`/`NOT`, and grouping.
- **Matching**: a battery against an in-memory provider, covering hierarchical tags, `=` exact, numeric/date relational, booleans, identifiers, `vl:` recursion, accent folding, and empty-query-is-all.
- **Integration**: a temporary SQLite fixture shaped like a Calibre `metadata.db`, exercising the full `CalibreDB` stack (search, `resolve_vl`, the Python-side series rollup).
- **Helpers** (`tests/test_helpers.py`): rating-to-stars and the half-star glyph, series-gap detection, and the JPEG/PNG cover sizers (including a JPEG whose SOF sits past the first 1 KB).

Run them with `PYTHONPATH=src python -m unittest tests.test_search tests.test_helpers`. The shell scripts `run_tests.sh` (every CLI mode) and `test_queries.sh` (representative `--search` queries) smoke-test against a real library.

## Troubleshooting

**A search or wing returns nothing.**
- Tags are anchored-hierarchical: `tags:Fic` matches `Fic` and `Fic.*`, but not a tag that merely contains "fic" in the middle. Use the full dotted path, or `=` for an exact leaf (`tags:"=Fic.SciFi.Cyberpunk"`).
- Check the wing name with `cquarry --wings`; names are case-sensitive and must match Calibre exactly. Quote names with spaces: `--wing "Sci-Fi Wing"`.
- A field prefix that Calibre supports but cquarry does not (templates `@...:`, saved searches `search:`) matches nothing. See [Parity scope](#parity-scope-stdlib-only-deviations).

**"Database not found" or it points at the wrong library.**
- Pass `--db /path/to/metadata.db` (or a directory containing it). The resolved path is saved to `~/.config/cquarry/config.json`; delete that file or pass `--db` to reset it.

**The shell mangles my query.** Wrap the whole expression in single quotes and use double quotes inside: `cquarry --search 'tags:"Fic.Fantasy.Grimdark" AND author:"Phil Tucker"'`. Without single quotes, your shell treats `OR`/`AND`/parentheses as separate arguments.

**"Custom column not found" (`--show-custom`).** Use the column's *display* name (e.g. `Status`); the error lists the available names. Note the asymmetry: `--show-custom` wants the display name, but a `#` search wants the *lookup* name (`#reading_status`). See [Custom columns](#custom-columns).

**A `#custom` search matches too many rows.** Custom searches are substring matches, so `#reading_status:Read` also catches `Reading` and `To Read`. Use `=` for an exact value: `#reading_status:=Read`.

**Calibre is open / the database is locked.** Expected. cquarry prints a notice to stderr, reads from a temporary snapshot, and cleans it up on exit. Results reflect the last saved state.

**Boxes or stars look like garbage in the TUI.** The interface uses Unicode box-drawing and star glyphs and a 256-color terminal. If `curses` is unavailable, cquarry falls back to a plain text menu automatically; piping or redirecting output disables color.

## How it reads the database

CalibreQuarry opens `metadata.db` in read-only mode (`?mode=ro`). It never writes to the database. All data comes from standard Calibre tables: `books`, `authors`, `tags`, `series`, `ratings`, `data`, `publishers`, `languages`, `identifiers`, `comments`, and `preferences`. Custom columns are not required, but are read on demand for `--show-custom` and `#column` searches.

If Calibre is running and holds a lock on the database, CalibreQuarry copies it (along with any WAL/SHM journal files) to a temporary snapshot and reads from that. A notice is printed to stderr; the temp files are cleaned up on exit.

Calibre stores ratings on a 0–10 scale internally (where 10 = 5 stars). CalibreQuarry converts to the standard 0-5 star display automatically.

## Replacing shell-based catalog pipelines

If you previously generated catalogs through a `calibredb list → JSON → parser` pipeline, `--all-wings` replaces that entire workflow with a single command. No temp files, no intermediate JSON, no shell glue functions.

The `--show-id` flag outputs Calibre book IDs, making it straightforward to pipe results into `calibredb set_metadata` for batch operations.

## Full help output

```
usage: cquarry [-h] [--version] [--catalog | --all-wings | --stats |
               --analytics {author,pace,tags,overlap} | --audit |
               --recent [RECENT] | --series | --export | --search QUERY |
               --wings | --tags] [--db DB] [--wing WING] [--output OUTPUT]
               [--outdir OUTDIR] [--format {json,csv,ai}] [--primary-only]
               [--show-tags] [--show-id] [--show-custom COL_NAME] [--quiet]

Calibre library toolkit: catalog, stats, audit, export

options:
  -h, --help            show this help message and exit
  --version             show program's version number and exit
  --catalog             Build a text catalog
  --all-wings           Generate catalogs for all virtual libraries
  --stats               Show library statistics
  --analytics {author,pace,tags,overlap}
                        Extended analytics and visualizations
  --audit               Report issues (untagged, unrated, series gaps)
  --recent [RECENT]     Show N most recently added books (default: 20)
  --series              List all series with completeness and gap detection
  --export              Export library to JSON, CSV, or AI format
  --search QUERY        Show/export books matching a Calibre search expression
                        (prints to stdout unless --output is given; empty
                        query = whole library)
  --wings               List all virtual library wings
  --tags                Dump every tag with its book count
  --db DB               Path to Calibre metadata.db (auto-detected if omitted)
  --wing WING           Filter to a specific virtual library wing
  --output OUTPUT       Output file path
  --outdir OUTDIR       Output directory for --all-wings (default: current dir)
  --format {json,csv,ai}
                        Output format. --export defaults to json; --search
                        defaults to a plain-text listing unless a format is
                        given here
  --primary-only        Use only the first author (useful for TTRPG
                        collections)
  --show-tags           Show tags instead of ratings in catalog output
  --show-id             Prefix each book with its Calibre ID for scripting
  --show-custom COL_NAME
                        Load and display a specific custom column
  --quiet               Minimize output
```

## Companion scripts

The `scripts/` directory holds standalone maintenance tools. They are **not** part of the `cquarry` package and deliberately sit **outside its read-only contract**: they are run directly with `python3`, and one of them writes. They are stdlib-only Python; some shell out to external command-line tools. Each is designed to run from inside a Calibre library directory (they locate `metadata.db` relative to themselves), so deploy a copy into your library root or pass paths explicitly.

### `compress_pdf.py` — shrink oversize PDFs (writes)

Re-encodes a bloated PDF (think 1 GB TTRPG sourcebooks) through Ghostscript with a quality preset, but only after verifying the result: it aborts if the page count changes or the output isn't smaller, and it keeps the original as `<name>.pre-compress.pdf`. If the file lives in a Calibre library, it syncs the new size back to the database (core `data.uncompressed_size`, plus the Count Pages plugin's `books_pages_link.format_size` if present) so Calibre doesn't see a stale size. A busy or locked database is handled gracefully: the PDF is still replaced and you are told to re-run with Calibre closed.

> **This script modifies files and `metadata.db`.** It is the reason the companion scripts live outside the read-only `cquarry` package. Back up before a bulk run; close Calibre first.

Requires `gs` (Ghostscript); optionally uses `pdfinfo` / `pdfimages` / `pdfdetach` (poppler) for page-count verification and the `--inspect` report.

```bash
python3 scripts/compress_pdf.py book.pdf                 # /ebook (150 dpi), in place + rollback copy
python3 scripts/compress_pdf.py book.pdf --preset screen # smaller, lower quality
python3 scripts/compress_pdf.py book.pdf --dry-run       # compress to a temp file, replace nothing
python3 scripts/compress_pdf.py ./Library --inspect      # per-file recommendation, no changes
python3 scripts/compress_pdf.py book.pdf --out-dir ~/out # write a copy elsewhere; original untouched
```

Exit codes: `0` compressed/verified (or clean inspect), `1` aborted (no shrink, page-count mismatch), `2` setup error (Ghostscript missing, unreadable file).

### `audit_epub_content.py` — flag non-English / tampered EPUBs (read-only)

Reads the actual text of EPUBs to catch problems metadata can't: editions whose body is in the wrong language (declared `eng` but actually Portuguese, Russian, etc.) and an injected foreign-language ad-notice signature. It votes a language across stopword sets and counts non-Latin script, and opens `metadata.db` strictly `mode=ro`.

```bash
python3 audit_epub_content.py              # audit the whole library (run from the library dir)
python3 audit_epub_content.py ~/Downloads  # vet loose .epub files before importing them
```

Exit codes: `0` clean, `1` foreign-language content or injection signature found, `2` setup error.

### `audit_epub_pagenumbers.py` — flag baked-in page numbers (read-only)

Reads EPUB body text to find print page numbers (and running headers) that a bad PDF/OCR conversion captured as paragraphs instead of real pagination, so they reflow into the middle of a sentence. It flags a number only when it actually interrupts prose (a lowercase continuation, a word split across it, or a repeated running header beside it), leaving legitimate chapter/section numbers and endnote markers alone. It opens `metadata.db` strictly `mode=ro`. As a side effect it also surfaces piracy watermarks and bad OCR scans.

```bash
python3 audit_epub_pagenumbers.py              # audit the whole library (run from the library dir)
python3 audit_epub_pagenumbers.py ~/Downloads  # vet loose .epub files before importing them
```

Exit codes: `0` clean, `1` baked-in page numbers found, `2` setup error.

### `validate_metadata.py` — lint database integrity (read-only)

A linter for `metadata.db` with two layers. It is the database-side companion to `audit_epub_content.py` (which checks book *content*), and it is strictly `mode=ro`.

**Integrity layer (always on, zero config).** Taxonomy-agnostic, schema-level problems the UI and `--audit` leave alone: books with no language, one ISBN attached to two books, placeholder (`0101-01-01`) or unparseable publication dates, junk identifier types (`url`, `uri`, `guid`, `isbn13`, ...), an ISBN-10 misfiled under `amazon`/`mobi-asin` (checksum-verified, so genuine ASINs are left alone), and custom-column link rows orphaned by deleted books. Safe to point at any library; needs no configuration.

**Opinionated layer (on when a taxonomy is loaded).** A `taxonomy.json` describes your tag tree, publisher consolidations, and identifier vocabulary, and these checks enforce it: every tag in use must be declared (`TAG_IN_SPEC`), alias publishers must be merged into their canonical (`PUBLISHER_NOT_CONSOLIDATED`), and fiction should not be PDF-only (`FORMAT_FICTION_PDF`). Loading a taxonomy also makes the identifier-type vocabulary authoritative (the `--strict` behavior turns on automatically). A comprehensive, ready-to-adapt template ships as **`scripts/taxonomy.example.json`** (three roots — `Fic` / `NonFic` / `Gaming` — with a deep, single-tag-per-book hierarchy; a branch is a valid tag on its own only when its `bare_allowed` is `true`). A fuller real-world reference in YAML, **`scripts/taxonomy.example.yaml`**, is also included; it is the richer schema used by a separate library-side linter and is provided for reference (the stdlib tools here read the JSON form).

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

### `reconcile_file_metadata.py` — sync DB metadata into book files (writes with `--apply`)

Calibre's `metadata.db` is where you curate titles, authors, series, tags, publishers, dates, identifiers, and blurbs; the copy embedded *inside* each EPUB/MOBI/AZW3/PDF/DJVU is what travels with the book when it leaves the library. Those drift apart whenever you edit metadata in Calibre without re-exporting the file. This script finds that drift and, with `--apply`, closes it. The flow is always database to file; it never reads file metadata back into the database.

It reads the database `mode=ro`, reads each file's embedded metadata with `ebook-meta` (EPUB/MOBI/AZW3/PDF) or `djvused` (DJVU), and diffs a per-format set of fields so a format is never faulted for something it cannot carry (a PDF holds title/author/publisher/date, a DJVU only title/author, an EPUB the full record). Default is a dry-run report. `--apply` touches only the drifted files, with a writer chosen per format: `calibredb embed_metadata` for EPUB/MOBI/AZW3 (full record plus cover), `exiftool` for PDF (Info dict + XMP; calibredb is skipped for PDF because it silently leaves some PDFs unchanged, whereas exiftool wrote every PDF tested), and `djvused` for DJVU. It refuses `--apply` while Calibre is running unless you pass `--force`. `--apply` needs `calibredb` and `exiftool` on PATH (the dry run does not). A few PDFs carry a damaged cross-reference table that exiftool refuses to write; pass `--repair-pdf` to rebuild it in place with `qpdf --replace-input` (page count preserved) and retry the embed. It is opt-in because it structurally rewrites the file.

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

Exit codes: `0` no drift (or `--apply` finished cleanly), `1` drift found (dry run) or an apply/embed step failed, `2` setup error (no `metadata.db`, or a missing external tool).

## Support

If CalibreQuarry's useful to you and you'd like to chip in:

```
bc1qkge6zr45tzqfwfmvma2ylumt6mg7wlwmhr05yv
```
