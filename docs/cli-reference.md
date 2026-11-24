# CalibreQuarry CLI reference

The deep CLI reference, split out of README.md on 2026-11-24. The README links
here instead of carrying the blocks inline.

The "Full help output" block below is a paste of the bare `cquarry --help`
overview: regenerate it whenever the overview rendering changes, and verify
byte-identity against live `--help` after any helpcli.py change (the doc-drift
audit on 2026-11-24 verified the paste byte-identical at 3.96.1).

## Full help output

`--help` is two-level by design: the bare flag prints the compact
overview below, `--help read|write|set|run|plugin|examples|all|json` prints the
focused deep dives (flag tables generated from the live parser, so they
cannot drift from the real flags), and `run --help VERB` prints a
per-verb page for each of the 24 run verbs. `--help json` dumps the
complete parser surface (every group and flag, every run verb with its
claimed flags and prose) as machine-readable JSON, generated from the
live parser and never colored. On a terminal the output is
ANSI-colored with the same theme Python 3.14's argparse uses (plain when
piped; `NO_COLOR` and `FORCE_COLOR` are honored).

```
cquarry - Calibre library toolkit: catalog, stats, audit, export,
the acquisition run pathway, and headless Calibre verbs.

usage: cquarry [--db DB] [--restrict SEARCH] MODE [mode flags]
       cquarry run VERB [verb flags]

modes (pick exactly one):
  --catalog                 Build a text catalog
  --all-wings               Generate catalogs for all virtual libraries
  --all-saved-searches      Generate a catalog per saved search (the --all-wings analog;
                            files land in --outdir, one per search, scoped by --restrict
                            when given)
  --stats                   Show library statistics
  --analytics {author,pace,tags,genres,overlap,reading}
      Extended analytics and visualizations (reading: status funnel and finish dates
      from #reading_status/#date_read; read-only)
  --audit                   Report issues (untagged, unrated, series gaps, conversion
                            overrides)
  --health                  One-shot health digest: the audit's finding counts in a
                            short form (composes with --restrict and --format json;
                            always exit 0 unless --fail-on-findings)
  --recent [RECENT]         Show N most recently added books (default: 20; --format json
                            emits the slim rows)
  --series                  List all series with completeness and gap detection
  --export                  Export library to JSON, CSV, or AI format
  --search QUERY            Show/export books matching a Calibre search expression
                            (prints to stdout unless --output is given; empty query =
                            whole library). Supports custom grouped-search terms
                            (GroupName:query) and annotations: full-text over e-reader
                            highlights
  --fts QUERY               Full-text content search over Calibre's full-text-search.db
                            sidecar (what the books' text actually says, not metadata).
                            Case- and accent-folded; composes with --restrict. Prints an
                            index-staleness summary after the matches unless --quiet
  --fts-status              Report FTS index staleness only: never-indexed formats,
                            indexed-empty documents, stale entries queued for re-index,
                            and extraction errors
  --wings                   List all virtual library wings
  --tags                    Dump every tag with its book count
  --identifierless          List books with no identifiers (id and title; the curation
                            queue for Calibre-Companion style lookups)
  --trash                   List the library's .caltrash entries (what run merge moved
                            aside): category, book id, age, files; library-shape, so
                            --restrict does not scope it
  --book [BOOK_ID[,BOOK_ID...]]
      Show the full record for one book or a comma-separated list: identifiers, format
      files, cover, comments, custom columns, annotations, reading progress. --format
      json emits the machine-readable dossier. With --untagged, give no ids to select
      every untagged book (also a standalone mode)
  --entities KIND           List an entity class with book counts
                            (authors/series/publishers include sort and link columns)
  --reading-progress        Show per-device reading positions with progress bars, newest
                            first
  --columns                 List custom columns: type, editability, enum values
  --info                    Library dossier: identity, wings + expressions, saved
                            searches, @Name user categories, grouped search terms,
                            feeds, sync queues
  --exportlt                Export to LibraryThing CSV format (can be used alone or with
                            --search)
  --export-annotations      Dump e-reader highlights/bookmarks/notes as JSON (optionally
                            scoped with --id)
  --format-stats            Show per-format book counts and total bytes

scoping:
  --db DB                   Path to Calibre metadata.db, or the library directory
                            containing it (auto-detected if omitted)
  --restrict SEARCH         Scope every read mode to books matching this search
                            expression (or `vl:Name` for a virtual library): stats,
                            audit, analytics, exports, catalogs, and the rest compute
                            over the restricted set only. Refused with write verbs, the
                            run verbs, and --book/--id
  --wing WING               Filter to a specific virtual library wing

also:
  write verbs   --set-title, --add-tag, ... per-book edits            (see: --help write)
  set writes    --from-search/--ids + --batch-* edits          (see: --help set)
  run VERB      the acquisition pathway + headless calibre verbs   (see: --help run)
  cquarry (no arguments)        the interactive TUI

exit codes: 0 clean, 1 findings (mode-dependent) or a failed apply/live-Calibre refusal, 2 usage.

deep dives:
  cquarry --help read|write|set|run|examples|all|json  (-h works too)
  cquarry --version
  cquarry run --help VERB        e.g. run --help phase2

example:
  cquarry --catalog --wing "The Tabletop" --show-tags --db ~/Calibre/metadata.db
```
