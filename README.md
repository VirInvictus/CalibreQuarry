<p align="center">
  <img src="logo.svg" alt="CalibreQuarry" width="680">
</p>

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.14%2B-blue" alt="Python 3.14+"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"></a>
</p>

A CLI toolkit for Calibre users who treat their libraries as curated collections. Reads `metadata.db` directly — no `calibredb` dependency, no JSON intermediaries, no external libraries. Pure Python stdlib.

> **Note:** This is considered completed software. It has been thoroughly tested and is known to be fully functional on the primary development environment: **Fedora Linux 43 (Workstation Edition)**, kernel `6.19.12-200.fc43.x86_64`, using **Calibre 9.7**. While it is pure Python and should be cross-platform, this specific setup is the only officially tested environment.

## Why this exists

Calibre is a good database. It is not a good reporting tool. If you maintain a large library (3000+ books) organized with virtual libraries, hierarchical tags, and series tracking, you eventually want answers to questions Calibre's UI doesn't surface well: which series have gaps, how many books are unrated, what does a given wing actually contain, and can I get a machine-readable export without running `calibredb list` through a parser script.

This tool reads the SQLite database directly in read-only mode. It resolves Calibre's virtual library search expressions (including `tags:`, `vl:` cross-references, and boolean operators), so your existing wing definitions work without being re-encoded anywhere.

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

The `scripts/` directory holds standalone maintenance tools. They are **not** part of the `cquarry` package and deliberately sit **outside its read-only contract**: they are run directly with `python3`, and one of them writes. They are stdlib-only Python, but shell out to external command-line tools. Both are designed to run from inside a Calibre library directory (they locate `metadata.db` relative to themselves), so deploy a copy into your library root or pass paths explicitly.

### `compress_pdf.py` — shrink oversize PDFs (writes)

Re-encodes a bloated PDF (think 1 GB TTRPG sourcebooks) through Ghostscript with a quality preset, but only after verifying the result: it aborts if the page count changes or the output isn't smaller, and it keeps the original as `<name>.pre-compress.pdf`. If the file lives in a Calibre library, it also updates `books_pages_link.format_size` so Calibre doesn't treat its cache as stale.

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

## Support

If CalibreQuarry's useful to you and you'd like to chip in:

```
bc1qkge6zr45tzqfwfmvma2ylumt6mg7wlwmhr05yv
```
