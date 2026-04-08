# getBooks — Application Specification

**Version:** 1.0  
**Language:** Python 3.9+  
**Dependencies:** None (stdlib only: sqlite3, json, csv, argparse)  
**License:** MIT

---

## 1. Mission Statement

getBooks is a CLI toolkit for Calibre users who treat their libraries as
curated collections. It reads `metadata.db` directly in read-only mode —
no `calibredb` dependency, no JSON intermediaries, no external libraries.
Pure Python stdlib.

Design philosophy: **replace every `calibredb list | jq | awk` pipeline
with a single command.** The script resolves Calibre's virtual library
search expressions natively, so existing wing definitions work without
re-encoding.

---

## 2. Architecture

### 2.1 Single-File Design

The entire toolkit lives in `getBooks.py`. Zero external dependencies.
Reads Calibre's standard SQLite tables (`books`, `authors`, `tags`,
`series`, `ratings`, `data`, `publishers`, `languages`, `preferences`)
in read-only mode (`?mode=ro`).

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

### 2.4 Auto-Detection

If `--db` is omitted, the script searches for `metadata.db` in the current
directory and at `~/Calibre Library/metadata.db`.

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

## 4. What getBooks Is Not

- **Not a Calibre replacement.** It reads the database — it does not manage it.
- **Not an editor.** It never writes to `metadata.db`.
- **Not a converter.** It does not touch book files themselves.
- **Not a server.** It has no web interface and no network access.
