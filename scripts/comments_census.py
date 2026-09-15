#!/usr/bin/env python3
"""
comments_census.py: the description mechanical sweep, as a standing tool.

Phase 3 rewrites every downloaded description into the house voice; before
any human reading happens, this sweep runs the field's MECHANICAL defects
across the batch — the same checks the phase-3 skill used to re-derive as
an inline three-liner every run (and which caught between 90 and 227
records per wing on the description-rewrite project, including a third of
everything a reader would have passed):

  double_hyphen      a literal `--` (renders as a double hyphen in Carrel)
  spaced_hyphen_dash a spaced hyphen used as a dash (`a - b`, `a -b`, `a- b`)
  markdown_bold      stray `**` from a markdown-sourced description
  tag_debris         `<br>` or `<div>` debris inside the field
  body_shape         the field does not start `<p>` and end `</p>`
  soft_hyphen        U+00AD soft hyphens from bad copy sources
  zero_width         zero-width spaces/joiners and BOM characters
  mojibake           high-confidence UTF-8-read-as-Latin1 telltales
  lost_ligature      a bare `?` inside a word (fi/fl and friends, lost)
  duplicate_body     exact-duplicate bodies across the scanned set

Read-only: opens metadata.db strictly mode=ro. It never writes; the fix is
phase 3's curated rewrite (or `--set-comments`).

Exit codes: 0 clean, 1 findings, 2 setup error. `--json FILE` emits the
machine report for scripting.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from urllib.parse import quote

# The classic UTF-8-read-as-Latin1/CP1252 double-encode fragments. Only the
# high-confidence lead-ins are flagged (an accented character alone is
# legitimate text; `Ã`, `â€`, and a bare `Â` before another byte are not).
_MOJIBAKE = re.compile(r"Ã.|â€.|Â[^\sa-zA-Z0-9]|�")

_SPACED_HYPHEN_DASH = re.compile(r"\w - \w|\w -\w|\w- \w")
_LOST_LIGATURE = re.compile(r"\w\?\w|\s\?\w")
_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\ufeff]")
_SOFT_HYPHEN = "\u00ad"


def _samples(text: str, pattern: re.Pattern, limit: int = 2) -> list[str]:
    hits = []
    for m in pattern.finditer(text):
        start = max(0, m.start() - 20)
        hits.append(text[start : m.end() + 20].replace("\n", " "))
        if len(hits) >= limit:
            break
    return hits


def sweep_text(text: str) -> list[dict]:
    """The per-book census: one finding dict per defect kind present."""
    findings: list[dict] = []

    def add(kind: str, count: int, samples: list[str] | None = None):
        findings.append({"kind": kind, "count": count, "samples": samples or []})

    if "--" in text:
        add("double_hyphen", text.count("--"), _samples(text, re.compile(r"--")))
    spaced = _SPACED_HYPHEN_DASH.findall(text)
    if spaced:
        add("spaced_hyphen_dash", len(spaced), spaced[:2])
    stars = text.count("**")
    if stars:
        add("markdown_bold", stars)
    debris = re.findall(r"<br\b[^>]*>|<div\b[^>]*>", text, re.IGNORECASE)
    if debris:
        add("tag_debris", len(debris), debris[:2])
    stripped = text.strip()
    if stripped and not (stripped.startswith("<p>") and stripped.endswith("</p>")):
        add("body_shape", 1, [stripped[:60]])
    for kind, pattern in (
        ("soft_hyphen", re.compile(_SOFT_HYPHEN)),
        ("zero_width", _ZERO_WIDTH),
        ("mojibake", _MOJIBAKE),
        ("lost_ligature", _LOST_LIGATURE),
    ):
        hits = pattern.findall(text)
        if hits:
            add(kind, len(hits), _samples(text, pattern))
    return findings


def find_duplicates(bodies: dict[int, str]) -> list[list[int]]:
    """Group book ids whose comment bodies are byte-exact identical.

    Singletons are dropped: only real duplicate groups come back, ids in
    scan order within each group.
    """
    by_body: dict[str, list[int]] = defaultdict(list)
    for book_id, text in bodies.items():
        by_body[text].append(book_id)
    return sorted(
        (ids for ids in by_body.values() if len(ids) > 1),
        key=lambda group: group[0],
    )


def db_uri_ro(path: str) -> str:

    return f"file:{quote(path)}?mode=ro"


def load_bodies(db_path: str, ids: list[int] | None) -> dict[int, str]:
    """book id -> raw comments HTML for every book that has one."""
    con = sqlite3.connect(db_uri_ro(db_path), uri=True)
    con.row_factory = sqlite3.Row
    try:
        if ids:
            marks = ",".join("?" * len(ids))
            rows = con.execute(
                f"SELECT book, text FROM comments WHERE book IN ({marks})",
                ids,
            ).fetchall()
        else:
            rows = con.execute("SELECT book, text FROM comments").fetchall()
        return {r["book"]: r["text"] for r in rows if r["text"]}
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mechanical defect census over Calibre description "
        "(comments) fields: the phase-3 sweep as a standing tool. Read-only."
    )
    ap.add_argument("--db", help="path to metadata.db (default: ./metadata.db)")
    ap.add_argument("--id", help="comma-separated book ids to restrict the census to")
    ap.add_argument("--json", metavar="FILE", help="also write the machine report")
    ap.add_argument(
        "--quiet", action="store_true", help="summary only, no per-book lines"
    )
    args = ap.parse_args()

    db_path = args.db or "metadata.db"
    try:
        ids = [int(x.strip()) for x in args.id.split(",")] if args.id else None
    except ValueError:
        print(f"ERROR: --id must be integers: {args.id!r}", file=sys.stderr)
        return 2
    try:
        bodies = load_bodies(db_path, ids)
    except sqlite3.OperationalError as e:
        print(f"ERROR: cannot open {db_path!r}: {e}", file=sys.stderr)
        return 2

    report = {"scanned": len(bodies), "books": [], "duplicate_groups": []}
    books_with_findings = 0
    for book_id in sorted(bodies):
        findings = sweep_text(bodies[book_id])
        if findings:
            books_with_findings += 1
            report["books"].append({"id": book_id, "findings": findings})
            if not args.quiet:
                for f in findings:
                    detail = ""
                    if f["samples"]:
                        shown = " | ".join(repr(s)[:60] for s in f["samples"])
                        detail = f" ({shown})"
                    print(f"#{book_id}: {f['kind']} x{f['count']}{detail}")

    groups = find_duplicates(bodies)
    for group in groups:
        report["duplicate_groups"].append(group)
        if not args.quiet:
            ids_str = ", ".join(f"#{i}" for i in group)
            print(f"duplicate_body: identical description on {ids_str}")

    print(
        f"\n{len(bodies)} description(s) scanned: "
        f"{books_with_findings} with mechanical defects, "
        f"{len(groups)} exact-duplicate group(s)."
    )
    if args.json:
        report["books_with_findings"] = books_with_findings
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=1)
            f.write("\n")
    return 1 if (report["books"] or groups) else 0


if __name__ == "__main__":
    sys.exit(main())
