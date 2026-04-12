# CalibreQuarry — Application Specification

**Version:** 2.0  
**Language:** Python 3.9+  
**Dependencies:** None (stdlib only: sqlite3, json, csv, argparse)  
**License:** MIT

---

## 1. Mission Statement

CalibreQuarry is a CLI toolkit for Calibre users who treat their libraries as
curated collections. It reads `metadata.db` directly in read-only mode —
no `calibredb` dependency, no JSON intermediaries, no external libraries.
Pure Python stdlib.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline
with a single command.** The script resolves Calibre's virtual library
search expressions natively, so existing wing definitions work without
re-encoding.

---

## 2. Architecture

### 2.1 Package Design

The toolkit is structured as a Python package in `src/cquarry/`:

```
src/cquarry/
├── __init__.py      # Version export
├── __main__.py      # python -m cquarry entry point
├── config.py        # Constants, persistent config (~/.config/cquarry/config.json)
├── db.py            # CalibreDB read-only database interface
├── helpers.py       # Rating conversion, author formatting, series gap detection
├── cli.py           # Argument parsing, CLI dispatch
├── tui.py           # Curses TUI, output capture, interactive menu
└── modes/
    ├── catalog.py   # Text catalog generation (single + all-wings)
    ├── stats.py     # Library statistics
    ├── audit.py     # Issue detection and CSV reporting
    ├── display.py   # Recent, series, wings display modes
    └── export.py    # JSON/CSV full library export
```

Zero external dependencies. Reads Calibre's standard SQLite tables
(`books`, `authors`, `tags`, `series`, `ratings`, `data`, `publishers`,
`languages`, `preferences`) in read-only mode (`?mode=ro`).

Install via `pip install .` for the `cquarry` console script, or run
directly with `python -m cquarry`.

### 2.2 Virtual Library Resolution

The script parses Calibre's virtual library definitions from the
`preferences` table. These are the same search expressions Calibre uses
internally:

```
Fantasy Wing:    tags:"Fic.Fantasy" or tags:"Fic.Speculative.Fantasy"
The Tabletop:    tags:"Gaming.TTRPG"
Unsorted:        not (vl:"The Tabletop" or vl:"Fantasy Wing" or ...)
```

Supported operators: `tags:Pattern`, `tags:"=Exact"`, `vl:Name` (cross-
reference), `or`, `and`, `not`, parentheses. Tag matching follows
Calibre's hierarchical convention — `tags:Fic.Fantasy` matches
`Fic.Fantasy`, `Fic.Fantasy.Epic`, `Fic.Fantasy.Grimdark`, etc.

### 2.3 Database Access

Read-only. Never writes. Opens with `?mode=ro` URI. All data comes from
standard Calibre tables — no custom columns required. Ratings are stored
0–10 internally (10 = 5 stars); converted to 0–5 for display.

If the database is locked by a running Calibre instance, CalibreQuarry
copies it (plus WAL/SHM journals) to a temporary snapshot and reads
from there. The temp files are cleaned up on exit.

### 2.4 Database Resolution

If `--db` is omitted, the database is resolved in order:
1. Saved config (`~/.config/cquarry/config.json`)
2. Default paths (`./metadata.db`, `~/Calibre Library/metadata.db`)
3. Interactive prompt (if running in a TTY)

The path is saved to config on first successful resolution.

---

## 3. Modes

| Mode | Flag | Description |
|------|------|-------------|
| Catalog | `--catalog` | Formatted text grouped by author with ratings and series |
| All wings | `--all-wings` | Separate catalog per virtual library |
| Statistics | `--stats` | Format breakdown, ratings, tags, publishers |
| Audit | `--audit` | Untagged, unrated, coverless books; series gaps |
| Recent | `--recent N` | N most recently added books |
| Series | `--series` | All series with completeness and gap detection |
| Export | `--export` | Full library to JSON or CSV |
| Wings | `--wings` | List virtual libraries with book counts |
| Interactive | (no args) | Launch the Curses TUI with scrollable output pager |

### 3.1 Modifiers

| Flag | Effect |
|------|--------|
| `--show-tags` | Show tags instead of ratings in catalogs |
| `--show-id` | Prefix books with Calibre ID (for scripting) |
| `--primary-only` | Collapse multi-author entries to first author |
| `--quiet` | Suppress decorative output |

---

## 4. What CalibreQuarry Is Not

- **Not a Calibre replacement.** It reads the database — it does not manage it.
- **Not an editor.** It never writes to `metadata.db`.
- **Not a converter.** It does not touch book files themselves.
- **Not a server.** It has no web interface and no network access.
