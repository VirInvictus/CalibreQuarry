from __future__ import annotations

import csv
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List

from cquarry.db import CalibreDB
from cquarry.helpers import detect_series_gaps, get_jpeg_size, normalize_author_display


def run_audit(db: CalibreDB, output: str, *, quiet: bool = False) -> None:
    """Report library issues to CSV."""
    books = db.get_all_books()
    all_series = db.get_all_series()
    issues: List[Dict[str, str]] = []
    
    DEPRECATED_FORMATS = {"MOBI", "LIT", "LRF", "DJVU", "PDB", "AZW"}
    db_dir = os.path.dirname(db.db_path)
    
    title_author_groups = defaultdict(list)

    for b in books:
        problems: List[str] = []

        if not b['tags']:
            problems.append("no_tags")
        if b['rating'] is None or b['rating'] == 0:
            problems.append("unrated")
        if not b['authors'] or b['authors'] == 'Unknown':
            problems.append("no_author")
        if not b['formats']:
            problems.append("no_file")
        else:
            formats = set(f.strip().upper() for f in b['formats'].split(','))
            if formats and formats.issubset(DEPRECATED_FORMATS):
                problems.append("deprecated_format_only")
                
        if not b['has_cover']:
            problems.append("no_cover")
        elif b['path']:
            cover_path = os.path.join(db_dir, b['path'], 'cover.jpg')
            if os.path.exists(cover_path):
                size = get_jpeg_size(cover_path)
                if size:
                    w, h = size
                    if max(w, h) < 500:
                        problems.append(f"low_res_cover({w}x{h})")

        if problems:
            issues.append({
                "id": str(b['id']),
                "title": b['title'] or '',
                "author": b['author_sort'] or '',
                "issue_type": "book",
                "issues": ", ".join(problems),
            })
            
        # Group for duplicate detection
        if b['title'] and b['authors']:
            primary_author = normalize_author_display(b['authors'], primary_only=True)
            key = (b['title'].strip().lower(), primary_author.strip().lower())
            title_author_groups[key].append(str(b['id']))

    for key, ids in title_author_groups.items():
        if len(ids) > 1:
            title, author = key
            issues.append({
                "id": ", ".join(ids),
                "title": title.title(),
                "author": author.title(),
                "issue_type": "duplicate",
                "issues": "duplicate_books",
            })

    for s in all_series:
        gaps = detect_series_gaps(s['indices'], s['max_index'])
        if gaps:
            issues.append({
                "id": "",
                "title": s['name'],
                "author": "",
                "issue_type": "series_gap",
                "issues": f"missing indices: {', '.join(str(g) for g in gaps)}",
            })

    fieldnames = ["id", "title", "author", "issue_type", "issues"]
    out_path = os.path.abspath(output)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in issues:
            w.writerow(row)

    if not quiet:
        book_issues = [i for i in issues if i['issue_type'] == 'book']
        series_issues = [i for i in issues if i['issue_type'] == 'series_gap']
        duplicate_issues = [i for i in issues if i['issue_type'] == 'duplicate']

        issue_counts: Counter = Counter()
        for i in book_issues:
            for problem in i['issues'].split(', '):
                issue_counts[problem] += 1

        print(f"Audited {len(books)} books, {len(all_series)} series.")
        print(f"Found {len(issues)} issues total.\n")

        if issue_counts:
            print("Book issues:")
            for problem, count in issue_counts.most_common():
                print(f"  {problem}: {count}")

        if duplicate_issues:
            print(f"\nDuplicates found: {len(duplicate_issues)}")
            for i in duplicate_issues[:10]:
                print(f"  {i['title']} by {i['author']} (IDs: {i['id']})")

        if series_issues:
            print(f"\nSeries with gaps: {len(series_issues)}")
            for i in series_issues[:10]:
                print(f"  {i['title']}: {i['issues']}")

        print(f"\nFull report: {out_path}")
