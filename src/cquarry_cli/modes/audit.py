import csv
import os
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
    find_coverless,
    find_deprecated_formats,
    find_formatless,
    find_low_res_covers,
    find_missing_cover_files,
    find_series_gaps,
    find_untagged,
    find_unrated,
)


def run_audit(db: CalibreDB, output: str, *, quiet: bool = False) -> None:
    """Report library issues to CSV."""
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

    fieldnames = ["id", "title", "author", "issue_type", "issues"]
    out_path = os.path.abspath(output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
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

        # Books whose sidecar .opf Calibre will regenerate at next startup
        # (its metadata_dirtied queue — external writes land here).
        dirtied = db.get_dirtied_books()
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

        print(f"\nFull report: {color(out_path, C_TITLE)}")
