#!/usr/bin/env python3
"""screen_duplicate: screen loose downloads against the library (and each other).

Phase 1 § 3's duplicate screen as a tool. Reads each candidate file's embedded
title/authors/ISBN with `ebook-meta` (Calibre's reader, read-only invocation)
and matches against the library by exact ISBN first, then by normalized title
+ first author via cquarry's search engine. The filename appears only as a
display hint: AA/z-library filenames lie ("...Volume 1..." held Volume 3;
titles arrive word-scrambled), so the embedded metadata decides.

Matching is deliberately two-stage. An exact-ISBN hit is conclusive. The
title/author path uses cquarry's tight `=` matches and then compares
NORMALIZED titles in Python — case/accent fold, strip the leading article,
and scrub subtitles (the segment before a colon) plus standalone
volume/part/book N tokens — so "Capital: Volume I" screens against
"Capital: A Critique of Political Economy" where raw equality would not.

Report-only: nothing is deleted or moved here. Removing a library duplicate
needs Brandon's explicit OK in phase 3; loose-file removal is his too.

Exit codes: 0 = no candidates, 1 = duplicate candidates found, 2 = setup error.
"""

import argparse
import json
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

EBOOK_EXTENSIONS = {".epub", ".pdf", ".mobi", ".azw3"}

RESET = "\033[0m"
YELLOW = "\033[1;33m"
RED = "\033[1;31m"
DIM = "\033[2m"


def _ebook_meta(path: Path) -> dict[str, str]:
    """Run Calibre's `ebook-meta` on one file and parse its report.

    The output is a flat `Key : value` listing (keys padded, values may
    themselves contain colons, so the split happens on the FIRST colon).
    Returns lowercase keys -> raw values, e.g. {"title": ..., "author(s)": ...,
    "identifiers": ...}. Missing keys simply stay absent.
    """
    proc = subprocess.run(
        ["ebook-meta", str(path)], capture_output=True, text=True, timeout=120
    )
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key and val:
            out[key] = val
    return out


def _embedded_fields(path: Path) -> dict[str, object]:
    """The embedded title / authors / isbn of one candidate file."""
    meta = _ebook_meta(path)
    title = meta.get("title") or ""
    authors = [
        a.strip()
        for a in (meta.get("author(s)") or meta.get("authors") or "").split("&")
        if a.strip()
    ]
    isbn = ""
    for pair in re.split(r"[,;]", meta.get("identifiers") or ""):
        kind, _, val = pair.strip().partition(":")
        if kind.strip().lower() == "isbn" and val.strip():
            isbn = val.strip()
            break
    return {"file": str(path), "title": title, "authors": authors, "isbn": isbn}


def _fold(text: str) -> str:
    """Case- and accent-fold (NFKD, combining characters stripped)."""
    lowered = unicodedata.normalize("NFKD", text)
    return "".join(c for c in lowered if not unicodedata.combining(c)).casefold()


_EDITION_TOKEN = re.compile(r"\b(vol|volume|part|pt|book|bd)\.?\s*\d+\b", re.IGNORECASE)
_VOLUME_SIG = re.compile(r"\b(vol|volume|part|pt|book|bd)\.?\s*(\d+)\b", re.IGNORECASE)


def _volume_signature(title: str) -> frozenset:
    """The volume tokens of a title, e.g. "Book 8" -> {("book", "8")}.

    Serialized fiction lives and dies by this number: scrubbing it (as the
    subtitle scrub does) collapses every Wandering Inn volume into one title.
    A match therefore requires equal volume signatures whenever BOTH sides
    carry one; roman numerals ("Volume I") leave no signature and stay in the
    scrub bucket, which is the edition-annotation case the scrub exists for.
    """
    return frozenset(
        (token.casefold(), number) for token, number in _VOLUME_SIG.findall(title or "")
    )


def normalize_title(title: str) -> str:
    """Fold, drop the subtitle (anything before a colon), edition tokens, article.

    "Capital: Volume I" and "Capital: A Critique of Political Economy" both
    normalize to "capital"; "The Left Hand of Darkness" loses its article.
    """
    text = _fold(title or "")
    text = text.split(":", 1)[0]
    text = _EDITION_TOKEN.sub(" ", text)
    text = re.sub(r"[^0-9a-z\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    for article in ("the ", "a ", "an "):
        if text.startswith(article):
            text = text[len(article) :].strip()
    return text


def normalize_author(author: str) -> str:
    """Fold, and drop bracket annotations ("Pirateaba [Pirateaba]" downloads)."""
    cleaned = re.sub(r"\s*\[[^\]]*\]\s*", " ", author or "")
    return _fold(cleaned).strip()


def normalize_author_brackets(author: str) -> str:
    """Strip bracket annotations but keep the original casing for searching."""
    return re.sub(r"\s*\[[^\]]*\]\s*", " ", author or "").strip()


def _search_escape(value: str) -> str:
    """Quote a value for Calibre's search grammar, escaping inner quotes."""
    return '"' + value.replace('"', r"\"") + '"'


def _library_hits(db, fields: dict[str, object]) -> list[dict[str, object]]:
    """Existing library copies of one candidate file.

    Exact ISBN first (the search engine's `isbn:X` is an exact keypair
    lookup), then normalized title + first author: the tight `=` search
    supplies candidates and the Python normalization catches the
    worded-differently editions raw equality misses. Hydrated list fields
    stay lists (never comma-split) per the cquarry contract.
    """
    hits: list[dict[str, object]] = []
    seen: set[int] = set()

    isbn = fields.get("isbn") or ""
    if isbn:
        for b in db.search_books(f"isbn:{_search_escape(isbn)}"):
            if b["id"] not in seen:
                seen.add(b["id"])
                hits.append(b)

    title = fields.get("title") or ""
    authors = fields.get("authors") or []
    if title and authors:
        first = normalize_author_brackets(authors[0])
        strict = db.search_books(
            f"title:{_search_escape('=' + title)} AND author:{_search_escape('=' + first)}"
        )
        author_exact = db.search_books(f"author:{_search_escape('=' + first)}")
        want = normalize_title(title)
        want_sig = _volume_signature(title)
        for b in author_exact:
            if b["id"] in seen:
                continue
            if normalize_title(b["title"] or "") != want:
                continue
            if _volume_signature(b["title"] or "") != want_sig:
                # Same series root, different volume: a gap-fill, not a dupe
                # (the phase-1 series census judges from here).
                continue
            seen.add(b["id"])
            hits.append(b)
        # The strict pair match joins the set even when the scrub rules would
        # disagree with the embedded title's exact spelling.
        for b in strict:
            if b["id"] not in seen:
                seen.add(b["id"])
                hits.append(b)
    return hits


def _scan_files(paths: list[str]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    skipped: list[str] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for child in sorted(p.iterdir()):
                if child.is_file() and child.suffix.lower() in EBOOK_EXTENSIONS:
                    files.append(child)
        elif p.is_file() and p.suffix.lower() in EBOOK_EXTENSIONS:
            files.append(p)
        else:
            skipped.append(raw)
    return files, skipped


def _screen(files: list[Path], db) -> list[dict[str, object]]:
    """Per-file candidate report, library hits plus within-batch duplicates."""
    records: list[dict[str, object]] = []
    for f in files:
        rec = _embedded_fields(f)
        rec["library_hits"] = [_describe(b) for b in _library_hits(db, rec)]
        records.append(rec)

    for i, rec in enumerate(records):
        for other in records[i + 1 :]:
            same_isbn = rec.get("isbn") and rec["isbn"] == other.get("isbn")
            same_title_author = (
                normalize_title(rec.get("title") or "") != ""
                and normalize_title(rec.get("title") or "")
                == normalize_title(other.get("title") or "")
                and _volume_signature(rec.get("title") or "")
                == _volume_signature(other.get("title") or "")
                and rec.get("authors")
                and other.get("authors")
                and normalize_author(rec["authors"][0])
                == normalize_author(other["authors"][0])
            )
            if same_isbn or same_title_author:
                rec.setdefault("batch_duplicates", []).append(other["file"])
                other.setdefault("batch_duplicates", []).append(rec["file"])
    return records


def _describe(b: dict) -> dict[str, object]:
    """The comparison columns the phase-1 skill says to report per copy."""
    return {
        "id": b["id"],
        "title": b["title"],
        "authors": b["authors"],
        "formats": b["formats"],
        "size": b.get("size"),
        "pages": b.get("pages"),
    }


def _print_report(records: list[dict[str, object]], skipped: list[str]) -> None:
    candidates = 0
    for rec in records:
        hits = rec["library_hits"]
        batch = rec.get("batch_duplicates") or []
        flag = YELLOW if (hits or batch) else ""
        status = "CANDIDATE" if (hits or batch) else "clean"
        print(f"{flag}[{status:9}] {rec['file']}{RESET}")
        if rec.get("title"):
            print(f"           embedded: {rec['title']}")
        if rec.get("authors"):
            print(f"           authors:  {' & '.join(rec['authors'])}")
        if rec.get("isbn"):
            print(f"           isbn:     {rec['isbn']}")
        for h in hits:
            candidates += 1
            fmts = ", ".join(h["formats"]) or "no formats"
            size = f", {h['size']:,} bytes" if h.get("size") else ""
            pages = f", {h['pages']} pages" if h.get("pages") else ""
            print(
                f"           {RED}vs library #{h['id']}{RESET}: {h['title']}"
                f" — {' & '.join(h['authors'])} ({fmts}{size}{pages})"
            )
            print(
                f"{DIM}             compare size/pages and quality before judging.{RESET}"
            )
        for other in batch:
            candidates += 1
            print(f"           {YELLOW}duplicate within batch{RESET}: {other}")
    if skipped:
        print(f"\n{DIM}Skipped (not ebook files): {', '.join(skipped)}{RESET}")
    print(f"\n{len(records)} file(s) screened, {candidates} candidate pair(s).")


def _resolve_db(path: str | None) -> Path | None:
    """Accept a library directory or a metadata.db path, like the sibling scripts."""
    p = Path(path).expanduser() if path else Path.cwd()
    if p.is_dir():
        candidate = p / "metadata.db"
        return candidate if candidate.exists() else None
    return p if p.exists() else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Screen loose ebook files against the Calibre library "
        "(and within the batch) for duplicates. Read-only; report-only."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="files or directories to screen (EPUB/PDF/MOBI/AZW3)",
    )
    parser.add_argument(
        "--db",
        help="library directory or metadata.db (default: saved config / discovery)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="report format (json is the batch-report shape)",
    )
    args = parser.parse_args()

    files, skipped = _scan_files(args.paths)
    if not files:
        print("ERROR: no ebook files to screen.", file=sys.stderr)
        return 2

    from cquarry.db import CalibreDB
    from cquarry.helpers import find_db

    try:
        db_path = _resolve_db(args.db) or find_db()
        db = CalibreDB(str(db_path))
    except Exception as e:
        print(f"ERROR: cannot open the library: {e}", file=sys.stderr)
        return 2

    try:
        records = _screen(files, db)
    finally:
        db.close()

    if args.format == "json":
        print(json.dumps(records, indent=2, ensure_ascii=False))
    else:
        _print_report(records, skipped)

    found = any(r["library_hits"] or r.get("batch_duplicates") for r in records)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
