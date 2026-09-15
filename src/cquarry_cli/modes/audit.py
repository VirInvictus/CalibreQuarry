import csv
from collections import Counter, defaultdict

from cquarry.db import CalibreDB
from cquarry.helpers import (
    C_ERR,
    C_HEADER,
    C_TITLE,
    C_WARN,
    color,
    normalize_author_display,
)
from cquarry.integrity import (
    find_authorless,
    find_bad_language_codes,
    find_coverless,
    find_deprecated_formats,
    find_formatless,
    find_invalid_uuids,
    find_low_res_covers,
    find_missing_cover_files,
    find_sentinel_pubdates,
    find_series_gaps,
    find_untagged,
    find_unrated,
)

from cquarry_cli.modes.fts import fts_staleness
from cquarry_cli.modes.treeaudit import print_tree_summary, tree_audit
from cquarry_cli.output import open_output


def collect_issues(db: CalibreDB) -> tuple[list[dict[str, str]], dict]:
    """Derive every audit row once for both renderers (the CSV audit
    and the --health digest). Returns (issues, extras); extras carries
    the FTS staleness report and the pending OPF queue, which are
    summarized rather than rendered as rows."""
    books = db.get_all_books()
    all_series = db.get_all_series()
    issues: list[dict[str, str]] = []

    # The per-book predicates live in cquarry.integrity now — one shared
    # definition of "incomplete" across the ecosystem; this frontend renders.
    # Flag names, per-book problem order, and the CSV shape are unchanged.
    DEPRECATED_FORMATS = {"MOBI", "LIT", "LRF", "DJVU", "PDB", "AZW"}
    untagged = set(find_untagged(db))
    unrated = set(find_unrated(db))
    authorless = set(find_authorless(db))
    formatless = set(find_formatless(db))
    deprecated = set(find_deprecated_formats(db, DEPRECATED_FORMATS))
    coverless = set(find_coverless(db))
    missing_covers = set(find_missing_cover_files(db))
    low_res = find_low_res_covers(db)

    title_author_groups = defaultdict(list)

    for b in books:
        problems: list[str] = []

        if b["id"] in untagged:
            problems.append("no_tags")
        if b["id"] in unrated:
            problems.append("unrated")
        if b["id"] in authorless:
            problems.append("no_author")
        if b["id"] in formatless:
            problems.append("no_file")
        elif b["id"] in deprecated:
            problems.append("deprecated_format_only")

        if b["id"] in coverless:
            problems.append("no_cover")
        elif b["id"] in missing_covers:
            problems.append("cover_file_missing")
        elif b["id"] in low_res:
            w, h = low_res[b["id"]]
            problems.append(f"low_res_cover({w}x{h})")

        if problems:
            issues.append(
                {
                    "id": str(b["id"]),
                    "title": b["title"] or "",
                    "author": b["author_sort"] or "",
                    "issue_type": "book",
                    "issues": ", ".join(problems),
                }
            )

        # Group for duplicate detection
        if b["title"] and b["authors"]:
            primary_author = normalize_author_display(b["authors"], primary_only=True)
            key = (b["title"].strip().lower(), primary_author.strip().lower())
            title_author_groups[key].append(str(b["id"]))

    for key, ids in title_author_groups.items():
        if len(ids) > 1:
            title, author = key
            issues.append(
                {
                    "id": ", ".join(ids),
                    "title": title,
                    "author": author,
                    "issue_type": "duplicate",
                    "issues": "duplicate_books",
                }
            )

    series_gaps = find_series_gaps(db)
    for s in all_series:
        gaps = series_gaps.get(s["name"])
        if gaps:
            issues.append(
                {
                    "id": "",
                    "title": s["name"],
                    "author": "",
                    "issue_type": "series_gap",
                    "issues": f"missing indices: {', '.join(str(g) for g in gaps)}",
                }
            )

    # Manual conversion overrides (cquarry's get_conversion_profiles; the
    # frontend renders, never re-derives). The standalone
    # scripts/audit_conversion_overrides.py keeps its pipeable ids surface;
    # this is the audit's own view of the same drift.
    override_issues: list[dict[str, str]] = []
    book_names = {b["id"]: (b["title"] or "", b["author_sort"] or "") for b in books}
    for row in db.get_conversion_profiles():
        title, author = book_names.get(row["book"], ("", ""))
        override_issues.append(
            {
                "id": str(row["book"]),
                "title": title,
                "author": author,
                "issue_type": "conversion_override",
                "issues": f"[{row['format']}] recipe blob {row['data_size']} bytes",
            }
        )
    issues.extend(override_issues)

    # Metadata-quality rows (the routed bindery item, recorded 2026-09-12:
    # bindery counted 51 OPF-085 invalid-UUID warnings across this
    # library -- its view of the class cquarry owns). cquarry 1.21's
    # predicate, rendered the audit's way: one advisory row per book,
    # the stored value shown in brackets.
    # False positives: none by construction. A parseable UUID never
    # flags; an empty uuid (the pre-uuid-column reader degrade) and
    # hand-built garbage are reported as seen, not assumed away.
    uuid_values = {b["id"]: (b.get("uuid") or "") for b in books}
    uuid_rows: list[dict[str, str]] = []
    for bid in find_invalid_uuids(db):
        title, author = book_names.get(bid, ("", ""))
        value = uuid_values.get(bid, "")
        uuid_rows.append(
            {
                "id": str(bid),
                "title": title,
                "author": author,
                "issue_type": "invalid_uuid",
                "issues": f"invalid_uuid [{value or '(empty)'}]",
            }
        )
    issues.extend(uuid_rows)

    # Sentinel pubdates (cquarry 1.21's find_sentinel_pubdates): books
    # whose pubdate is Calibre's undefined-date sentinel (0101-01-01 or
    # its 0100-01-01 ancestor, the same pair the search engine and the
    # listing sort treat as dateless).
    # False positive: a genuine year-1/101 publication date would be
    # indistinguishable; nobody has one.
    pubdate_values = {b["id"]: (b.get("pubdate") or "") for b in books}
    sentinel_rows: list[dict[str, str]] = []
    for bid in find_sentinel_pubdates(db):
        title, author = book_names.get(bid, ("", ""))
        value = (pubdate_values.get(bid) or "")[:10]
        sentinel_rows.append(
            {
                "id": str(bid),
                "title": title,
                "author": author,
                "issue_type": "sentinel_pubdate",
                "issues": f"sentinel_pubdate [{value}]",
            }
        )
    issues.extend(sentinel_rows)

    # Bad language codes (cquarry 1.21's find_bad_language_codes): books
    # linked to a value that is not an ISO 639-2 code shape.
    # False positive: none from the shape check itself -- it demands
    # exactly three ASCII lowercase letters, so a valid but rare code
    # never flags; bare names ("English") and two-letter codes are the
    # smell the OPF linters flag.
    language_values = {b["id"]: (b.get("languages") or []) for b in books}
    language_rows: list[dict[str, str]] = []
    for bid in find_bad_language_codes(db):
        title, author = book_names.get(bid, ("", ""))
        bad = [
            code
            for code in language_values.get(bid, [])
            if not (
                isinstance(code, str)
                and len(code) == 3
                and code.isascii()
                and code.isalpha()
                and code.islower()
            )
        ]
        language_rows.append(
            {
                "id": str(bid),
                "title": title,
                "author": author,
                "issue_type": "bad_language",
                "issues": f"bad_language [{', '.join(bad)}]",
            }
        )
    issues.extend(language_rows)

    # Filesystem-vs-database tree audit (Phase 19 A.3, CQ-native route):
    # book-level classes follow the active --restrict view, library-shape
    # classes (orphans, malformed dirs, root strays) are global.
    issues.extend(tree_audit(db))

    # FTS coverage audit (Phase 19 B.6): one row per (book, format) in a
    # staleness class. With no sidecar the per-pair rows would be the
    # whole library, so the CSV stays silent and the prose summary
    # carries the never-indexed count instead.
    staleness = fts_staleness(db)
    if staleness["sidecar_present"]:
        titles = {b["id"]: (b["title"] or "", b["author_sort"] or "") for b in books}
        for bid, fmt in staleness["never_indexed"]:
            title, author = titles.get(bid, ("", ""))
            issues.append(
                {
                    "id": str(bid),
                    "title": title,
                    "author": author,
                    "issue_type": "fts_coverage",
                    "issues": f"fts_never_indexed [{fmt}]",
                }
            )
        for bid, fmt in staleness["indexed_empty"]:
            title, author = titles.get(bid, ("", ""))
            issues.append(
                {
                    "id": str(bid),
                    "title": title,
                    "author": author,
                    "issue_type": "fts_coverage",
                    "issues": f"fts_indexed_empty [{fmt}]",
                }
            )
        for bid, fmts in staleness["extraction_errors"].items():
            title, author = titles.get(bid, ("", ""))
            issues.append(
                {
                    "id": str(bid),
                    "title": title,
                    "author": author,
                    "issue_type": "fts_coverage",
                    "issues": f"fts_extraction_error [{', '.join(sorted(fmts))}]",
                }
            )
        for bid, fmt in staleness["stale_queued"]:
            title, author = titles.get(bid, ("", ""))
            issues.append(
                {
                    "id": str(bid),
                    "title": title,
                    "author": author,
                    "issue_type": "fts_coverage",
                    "issues": f"fts_stale_queued [{fmt}]",
                }
            )

    return issues, {
        "staleness": staleness,
        "dirtied": db.get_dirtied_books(),
        "override_issues": override_issues,
        "uuid_rows": uuid_rows,
        "sentinel_rows": sentinel_rows,
        "language_rows": language_rows,
    }


def run_audit(db: CalibreDB, output: str, *, quiet: bool = False) -> None:
    """Report library issues to CSV."""
    issues, extras = collect_issues(db)
    staleness = extras["staleness"]
    override_issues = extras["override_issues"]
    uuid_rows = extras["uuid_rows"]
    sentinel_rows = extras["sentinel_rows"]
    language_rows = extras["language_rows"]
    books = db.get_all_books()
    all_series = db.get_all_series()

    fieldnames = ["id", "title", "author", "issue_type", "issues"]
    with open_output(output, db.db_path) as (f, out_path):
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in issues:
            w.writerow(row)

    if not quiet:
        book_issues = [i for i in issues if i["issue_type"] == "book"]
        series_issues = [i for i in issues if i["issue_type"] == "series_gap"]
        duplicate_issues = [i for i in issues if i["issue_type"] == "duplicate"]

        issue_counts: Counter = Counter()
        for i in book_issues:
            for problem in i["issues"].split(", "):
                issue_counts[problem] += 1

        lib_uuid = db.get_library_uuid()
        provenance = f" (library {lib_uuid})" if lib_uuid else ""
        print(f"Audited {len(books)} books, {len(all_series)} series.{provenance}")

        issue_str = f"{len(issues)} issues"
        if len(issues) > 0:
            issue_str = color(issue_str, C_ERR)
        print(f"Found {issue_str} total.\n")

        if issue_counts:
            print(color("Book issues:", C_HEADER))
            for problem, count in issue_counts.most_common():
                print(f"  {problem}: {count}")

        if duplicate_issues:
            print("\n" + color(f"Duplicates found: {len(duplicate_issues)}", C_WARN))
            for i in duplicate_issues[:10]:
                print(f"  {i['title']} by {i['author']} (IDs: {i['id']})")

        if series_issues:
            print("\n" + color(f"Series with gaps: {len(series_issues)}", C_WARN))
            for i in series_issues[:10]:
                print(f"  {i['title']}: {i['issues']}")

        if override_issues:
            print(
                "\n"
                + color(
                    f"Manual conversion overrides: {len(override_issues)} book(s)",
                    C_WARN,
                )
            )
            for i in override_issues[:10]:
                print(f"  #{i['id']} {i['title']}: {i['issues']}")
            if len(override_issues) > 10:
                print(f"  ... and {len(override_issues) - 10} more")
            print(
                "  The recipe blobs are Calibre pickles; open the book's "
                "conversion dialog in Calibre to inspect or clear them."
            )

        if uuid_rows:
            print(
                "\n"
                + color(
                    f"Metadata quality: {len(uuid_rows)} invalid uuid(s)",
                    C_WARN,
                )
            )
            for i in uuid_rows[:10]:
                print(f"  #{i['id']} {i['title']}: {i['issues']}")
            if len(uuid_rows) > 10:
                print(f"  ... and {len(uuid_rows) - 10} more")

        if sentinel_rows:
            print(
                "\n"
                + color(
                    f"Metadata quality: {len(sentinel_rows)} sentinel "
                    "pubdate(s) (Calibre's undefined-date value)",
                    C_WARN,
                )
            )
            for i in sentinel_rows[:10]:
                print(f"  #{i['id']} {i['title']}: {i['issues']}")
            if len(sentinel_rows) > 10:
                print(f"  ... and {len(sentinel_rows) - 10} more")

        if language_rows:
            print(
                "\n"
                + color(
                    f"Metadata quality: {len(language_rows)} book(s) with a "
                    "non-ISO-639-2 language value",
                    C_WARN,
                )
            )
            for i in language_rows[:10]:
                print(f"  #{i['id']} {i['title']}: {i['issues']}")
            if len(language_rows) > 10:
                print(f"  ... and {len(language_rows) - 10} more")

        # Books whose sidecar .opf Calibre will regenerate at next startup
        # (its metadata_dirtied queue — external writes land here).
        dirtied = extras["dirtied"]
        if dirtied:
            print(
                "\n"
                + color(f"Pending OPF sync: {len(dirtied)} book(s)", C_WARN)
                + " (regenerated by Calibre at its next startup)"
            )
            titles = {b["id"]: b["title"] for b in books}
            for bid in dirtied[:10]:
                print(f"  #{bid} {titles.get(bid, '?')}")
            if len(dirtied) > 10:
                print(f"  ... and {len(dirtied) - 10} more")

        print_tree_summary(
            [i for i in issues if i["issue_type"] == "tree"], quiet=quiet
        )

        fts_rows = [i for i in issues if i["issue_type"] == "fts_coverage"]
        if staleness["sidecar_present"]:
            counts = Counter(r["issues"].split(" [")[0] for r in fts_rows)
            if counts:
                print(
                    "\n"
                    + color(
                        f"FTS coverage: {len(fts_rows)} finding(s)",
                        C_WARN,
                    )
                )
                for cls, count in counts.most_common():
                    print(f"  {cls}: {count}")
        else:
            never = len(staleness["never_indexed"])
            print(
                "\n"
                + color(
                    f"FTS sidecar absent ({never} text-capable "
                    "format(s) never indexed; run --fts-status)",
                    C_WARN,
                )
            )

        print(f"\nFull report: {color(out_path, C_TITLE)}")


def show_health(db: CalibreDB, *, quiet: bool = False) -> int:
    """The one-shot health digest: every audit class's row count in a
    short form, always exit 0 (a dashboard, not --audit's CSV). Book-
    level classes follow the active --restrict view; library-shape
    classes stay global, exactly as in --audit, because both renderers
    consume the same collect_issues derivation."""
    books = db.get_all_books()
    issues, extras = collect_issues(db)
    staleness = extras["staleness"]
    dirtied = extras["dirtied"]

    if quiet:
        return 0

    by_type: Counter = Counter(i["issue_type"] for i in issues)
    book_rows = [i for i in issues if i["issue_type"] == "book"]
    problem_counts: Counter = Counter()
    for row in book_rows:
        for problem in row["issues"].split(", "):
            problem_counts[problem] += 1

    print(f"=== Library health ({len(books)} books) ===")
    lib_uuid = db.get_library_uuid()
    if lib_uuid:
        print(f"library {lib_uuid}")

    if book_rows:
        top = ", ".join(f"{p} {n}" for p, n in problem_counts.most_common(5))
        print(f"  book issues          : {len(book_rows)} book(s) ({top})")
    else:
        print("  book issues          : none")
    print(f"  duplicate groups     : {by_type['duplicate']}")
    print(f"  series gaps          : {by_type['series_gap']}")
    print(f"  conversion overrides : {by_type['conversion_override']}")
    print(
        f"  metadata quality     : {len(extras['uuid_rows'])}"
        f" invalid uuid(s), {len(extras['sentinel_rows'])} sentinel pubdate(s), "
        f"{len(extras['language_rows'])} bad language value(s)"
    )
    tree_count = by_type["tree"]
    print(
        f"  filesystem tree      : {tree_count} finding(s)"
        if tree_count
        else "  filesystem tree      : no discrepancies"
    )
    if staleness["sidecar_present"]:
        print(f"  FTS coverage         : {by_type['fts_coverage']} finding(s)")
    else:
        never = len(staleness["never_indexed"])
        print(
            f"  FTS coverage         : sidecar absent ({never} text-capable "
            "format(s) never indexed)"
        )
    print(f"  pending OPF sync     : {len(dirtied)} book(s)")
    print("  Full detail: --audit (CSV); tree/metadata rows carry their own views.")
    return 0
