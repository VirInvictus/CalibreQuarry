# CalibreQuarry — Application Specification

**Version:** 2.0  
**Language:** Python 3.9+  
**Dependencies:** None (stdlib only: sqlite3, json, csv, argparse)  
**License:** MIT

---

## 1. Mission Statement

CalibreQuarry is a CLI toolkit for Calibre users who treat their libraries as curated collections. It reads `metadata.db` directly in read-only mode — bypassing the overhead of `calibredb`, JSON intermediaries, or external library dependencies.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline with a single command.** The script resolves Calibre's **Virtual Library** (Wing) search expressions natively, ensuring existing library definitions work without re-encoding.

---

## 2. Architecture

### 2.1 Modular Package Design
The toolkit is structured as a Python package in `src/cquarry/`, ensuring separation of concerns:

| Module | Responsibility |
|--------|----------------|
| `db.py` | Read-only SQLite interface to Calibre's internal schema. |
| `tui.py` | Curses-based terminal interface and interactive pager. |
| `modes/` | Discrete logic for catalogs, stats, audits, and exports. |
| `config.py` | Path resolution and persistent settings management. |

### 2.2 Virtual Library (Wing) Resolution
CalibreQuarry parses search expressions directly from the `preferences` table. It supports hierarchical tag matching (`tags:Fic.Fantasy`), boolean operators, and `vl:` cross-references.

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
