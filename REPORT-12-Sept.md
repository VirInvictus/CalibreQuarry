# REPORT-12-Sept: the upstream comparison (CalibreQuarry vs Calibre's management surface)

Research date: 2026-09-10/11. Method: two read-only research passes
against the upstream clone — one on Calibre's full library-management
surface (calibredb's 24 commands, the GUI's library views, the view
layer, FTS, annotations, devices, conversion, plugins), one on the
audit/quality problem space — compared against this repo's shipped CLI
surface, scripts, and Phases 16-18. Full evidence lives in the agents'
findings. Nothing below re-opens shipped work; Phase 18's audit items
all shipped in 3.32-3.36.

## Verdict

The surface is nearly complete against everything upstream exposes
that is headless-reachable, and this repo **leads upstream in three
places**: series gap detection (upstream has none), standing duplicate
audits (upstream's duplicate tooling is add-time only), and
content-quality audits (DRM/ISBN-vs-copyright-page/comments/overrides —
Calibre has nothing comparable). The stats/analytics layer is mature
but **global-only**: no scoping, no reading-status dimension. The
sharpest missing edge is **full-text content search**: upstream ships
it headless (`calibredb fts_index` / `fts_search`) against a sidecar
SQLite that this ecosystem never reads, so a CQ user cannot ask "which
of my books discuss X". Second hole: `--wing` reaches only catalog and
search — stats, audits, analytics, and exports have no per-wing answer.
Third: the file-vs-database tree is audited only for missing covers.

## A. Surface gaps (ranked)

1. **FTS-driven content search** — read the `full-text-search.db`
   sidecar (fts5 `books_text` + text tables; upstream
   `calibre/db/fts/connect.py:35-48`) read-only, with a
   `--restrict-to` modifier. Plus the audit angle: stale or unindexed
   formats via `dirtied_formats` (upstream `db/fts/connect.py:63-110`).
   Effort M. Highest user value; keeps the no-calibredb identity
   honest (the plain `books_text` table needs no FTS5 machinery —
   cquarry REPORT-12-Sept A.1 is the read half of exactly this).
2. **`--restrict SEARCH` scoping every read mode** — upstream precedent
   both GUI (virtual libraries as view restrictions) and headless
   (`--restrict-to` on fts_search, `cmd_fts_search.py`); upstream
   already supports restricted-id category counts (`db/categories.py:
   212`). Turns stats, audits, analytics, exports, and catalogs into
   per-wing instruments. Effort S/M. Cheapest large win.
3. **Filesystem-vs-database tree audit** — `calibredb check_library`
   parity (`calibre/library/check_library.py:34-47`): orphan book dirs,
   extra/unknown files, missing/extra formats on disk, extra covers,
   malformed paths. CQ --audit covers cover_file_missing only. Effort M.
4. **Reading analytics (read-only)** — status funnel from
   `#reading_status`, days-to-read and recent-finishes from
   `#date_read` (columns the library itself maintains; the
   NON-NEGOTIABLES write ban is untouched — nothing here writes).
   Upstream surfaces the data via `srv/last_read.py`. Effort S/M.
5. **Saved-search-driven catalogs** — `--all-saved-searches` as the
   `--all-wings` analog; saved searches live in the preferences table
   and resolve via `search:"Name"` today. Effort S.
6. **Conversion plan report** — per-book suggested `ebook-convert`
   commands for deprecated/PDF-only books (detection already ships as
   `find_deprecated_formats`), override-aware, dry-run only. This is a
   report, not a pipeline. Effort M.
7. **`@Name` user-category resolution** in search and analytics
   (read-only parse of the preferences; upstream
   `db/categories.py:114`). Worth doing only if the library's user
   categories are in active use. Effort M.

Declined as GUI-bound or out of identity: cover grid/quickview/similar
books/mark-books, search history, news scheduler, save-to-disk file
export (see C.4 for the subprocess form), the content server (Carrel's
charter), device drivers, and the plugins ecosystem's read side (the
`--plugin-data` surface already covers it).

## B. Audit-depth gaps (the cross-signal layer)

The audit story is deep per-signal and shallow across signals. Ranked
new audits, each routed (cquarry predicates vs CQ renders vs bindery
analyzers):

1. **Content-duplicate fingerprinting across books** — different
   title+author rows sharing >80% body text (re-downloads under wrong
   titles, edition swaps, omnibus overlap). Invisible to the
   title+author grouping and to screen_duplicate. Simhash over 3-word
   shingles of spine text; clusters reported. Route: CQ (new
   `audit_duplicates_content.py`). Effort M.
2. **Truncation beyond gross thinness** — last spine document ends
   mid-sentence (bindery analyzer), and PDF real page count vs
   Count Pages data disagreeing >20% (CQ's PDF battery already reads
   the count). Route: bindery + CQ. Effort M.
3. **Author-sort sanity** — author_sort identical to display (never
   inverted), or not matching any author on the book. cquarry
   predicate `find_bad_author_sorts`; --audit renders `bad_author_sort`.
   Effort S/M.
4. **DB-level ISBN checksum/shape** — the integer-constant ISBN
   audit_isbns found needs no file open; validate_metadata gains
   INVALID_ISBN via cquarry's existing checksum helper. Effort S.
5. **Cover aspect distortion** — w/h ratio outside ~0.55-0.80;
   cquarry already has header-only image sizing. Effort S.
6. **FTS coverage audit** — books absent from `full-text-search.db` or
   indexed near-empty ("never indexed" vs "indexed empty" reported
   separately). Effort S.
7. **PDF battery depth** — text layer sampled at pages 1/middle/last
   (page-1-only OCR passes today), and image DPI parsed from the
   existing `pdfimages -list` output. Effort S.
8. **Mid-book empty chapters** — spine docs with near-zero text in an
   otherwise full book; weighted by count and consecutive runs. Route:
   bindery audit. Effort S.
9. **Body-text mojibake** — the proven regex telltales applied to spine
   text (existing checks cover only DB fields). Route: bindery audit.
   Effort S.
10. **Copyright-year vs pubdate** — capture (c) years in audit_isbns'
    existing front-matter pass; disagreement is VARIANT-class advisory.
    Effort M.

Deliberately not recommended: `calibredb check_library`
re-implementation (the docs can call it out; C.3 above is the CQ-native
narrow form), ebook-edit's Check Book class (bindery's epubcheck gate
owns it), and conversion-time heuristics (upstream runs them at
convert).

## C. Utility integrations (upstream tools the duo should drive)

The blind spot is the book-file plane and the cross-library plane:
upstream ships whole CLI programs operating on EPUB/PDF bytes or moving
books between libraries, and the duo drives none of them. All
subprocess-driven; no new Python dependencies.

1. **Format-conversion batching** — `run convert` for a search set:
   drive `ebook-convert` as a subprocess, register output via cquarry
   `add_format`/`remove_format`. Detection already ships. Effort M.
2. **Batch quality-polish** — drive `ebook-polish` (smarten punctuation,
   unused CSS removal, image compression, font subset/embed, jacket,
   kepubify) per book; cquarry supplies id sets and post-verify.
   Effort M.
3. **Cover remediation** — the audits flag coverless/low-res; nothing
   places an image. cquarry `set_cover` (see cquarry's Phase 13
   promotion candidate) + CQ verb driving it. Effort M.
4. **Save-to-disk bulk export** — drive `calibredb export --template`
   for real file/cover/OPF tree exports (backups, SD cards). Effort S.
5. **Duplicate-record merge verb** — combine a duplicate's formats into
   the keeper, then remove; detection exists, remediation does not.
   Route: cquarry compose + CQ verb. Effort M.
6. **Headless flush of the dirtied/OPF queue** — drive
   `calibredb embed_metadata`/`backup_metadata` for dirtied ids;
   closes the write loop without opening Calibre. Effort S.
7. **Metadata-source backfill** — drive fetch-ebook-metadata for a
   search set into OPF, then cquarry writes (the pattern is proven in
   run.py). Effort S-M.
8. **check_library parity** — drive `calibredb check_library` as a
   subprocess, or the CQ-native narrow form in A.3. Effort S.
9. **Library merge/split** — drive `calibredb add --with-library` per
   cquarry-resolved id sets. One-off migrations, not daily. Effort M.

Deliberately not recommended: notes (declined by recorded decision),
reading-list writes (NON-NEGOTIABLES), news/device flows (GUI-bound),
re-implementing conversion or Calibre's template language (L and
off-contract).

## D. Phase 19 routing

Everything above is boxed in roadmap.md **Phase 19** with fresh-agent
context, grouped: A1-A5 (the read-surface batch), B1-B10 (the audit
batch, with cquarry/bindery routing marked), C1-C9 (the integration
batch, all subprocess). The B items that route to cquarry and bindery
are cross-repo findings recorded here and in those repos' research
reports.
