#!/usr/bin/env python3
"""
fetch_library_codes.py: derive Library of Congress Classification (LCC) and
Dewey (DDC) codes for books that have an ISBN, from the Library of Congress
SRU catalogue, and store them as Calibre identifiers.

This replaces the "Library Codes - SRU" Calibre plugin, which cannot work
against a composite custom column and whose ISBN lookup is broken upstream:
it queries the LCDB index `dc.identifier`, which the server rejects with SRU
diagnostic 1/16 "Unsupported index" (Bib-1 114, Unsupported Use attribute).
The index that actually resolves an ISBN there is `bath.isbn`. Verified live
2026-08-02.

Storing the code as an identifier rather than a column value is deliberate:
identifiers are the catalogue's canonical home for external keys, a composite
column (`{identifiers:select(lcc)}`) projects the value for display without a
second copy, and reconcile_file_metadata.py carries identifiers into embedded
file metadata. A plain text column would be a second, unsynced home.

LoC rate-limits aggressively. At 0.6s between requests the server begins
resetting connections after roughly twenty queries, so the default pacing here
is 2.0s with exponential backoff, and every result (including a miss) is cached
to disk so an interrupted run resumes without re-querying. A full pass over a
few thousand ISBNs is measured in hours, not minutes; that is the service's
constraint, not this tool's.

Coverage is partial by nature. LoC catalogues what it catalogues: academic,
canonical and mainstream trade titles resolve well, while genre fiction, indie
and small-press releases, translations and TTRPG material often have no record.
Run in the default dry-run mode first and read the per-branch hit rate before
committing to a full pass.

Run from the library directory:
    python3 fetch_library_codes.py                    # dry run, whole library
    python3 fetch_library_codes.py --sample 200       # dry run, random sample
    python3 fetch_library_codes.py --id 8541,8542     # dry run, specific books
    python3 fetch_library_codes.py --apply            # write identifiers
    python3 fetch_library_codes.py --apply --write-ddc  # also store ddc

Exit codes:
    0 = completed (with or without hits)
    1 = aborted (repeated network failure) or a write error
    2 = setup error (missing DB, Calibre running while --apply, bad arguments)
"""

import argparse
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter

SRU_BASE = "http://lx2.loc.gov:210/LCDB"
MODS_NS = {"mods": "http://www.loc.gov/mods/v3"}
SRW_NS = "{http://www.loc.gov/zing/srw/}"

DEFAULT_CACHE = os.path.expanduser("~/.cache/cquarry/library_codes.json")

# Consecutive network failures after which we stop rather than keep hammering
# a service that has clearly stopped answering us.
ABORT_AFTER_CONSECUTIVE_FAILURES = 8

# ElementTree does not resolve external entities (so no XXE), but expat will
# happily expand nested internal entities: a hostile or MITM'd response could
# billion-laughs us. defusedxml is the usual answer and is not available here
# (stdlib-only project), so we cap the bytes we are willing to parse instead.
# A five-record MODS response is a few tens of KB; 8 MB is far above any real
# answer and far below anything that hurts.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
def load_cache(path: str) -> dict[str, dict]:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError, json.JSONDecodeError:
        return {}


def save_cache(path: str, cache: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, indent=1, sort_keys=True)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# LoC SRU
# --------------------------------------------------------------------------
def sru_url(isbn: str, max_records: int = 5) -> str:
    query = urllib.parse.quote(f"bath.isbn={isbn}")
    return (
        f"{SRU_BASE}?version=1.1&operation=searchRetrieve&recordSchema=mods"
        f"&maximumRecords={max_records}&query={query}"
    )


def parse_mods(raw: bytes) -> dict[str, str]:
    """Return {'lcc': ..., 'ddc': ...} from the first record carrying each."""
    out: dict[str, str] = {}
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return out

    diag = root.find(f".//{SRW_NS}diagnostics")
    if diag is not None:
        msg = root.find(".//{http://www.loc.gov/zing/srw/diagnostic/}message")
        raise SRUDiagnostic(msg.text if msg is not None else "unknown diagnostic")

    # Walk record by record so a code is never taken from a sibling record
    # that happens to sit later in the same response.
    for mods in root.findall(f".//{SRW_NS}recordData/mods:mods", MODS_NS):
        for authority in ("lcc", "ddc"):
            if authority in out:
                continue
            el = mods.find(f"./mods:classification[@authority='{authority}']", MODS_NS)
            if el is not None and el.text and el.text.strip():
                out[authority] = el.text.strip()
    return out


class SRUDiagnostic(Exception):
    """The SRU server answered with a diagnostic instead of records."""


def fetch_codes(
    isbn: str, timeout: int, retries: int = 3, *, strict: bool = False
) -> dict[str, str] | None:
    """Query LoC for one ISBN. Returns a dict (possibly empty) or None on failure.

    SRU diagnostics are retried like network errors rather than treated as fatal.
    LoC intermittently answers a perfectly good query with "Query feature
    unsupported" when it is under load: an ISBN that failed that way mid-run
    returned a record on the very next try. Aborting the whole pass on one of
    those throws away hours of work for a server hiccup.

    `strict` is set for the very first query of a run. There a diagnostic is far
    more likely to mean the query form itself is wrong (the plugin's dead
    `dc.identifier` index answers this way every single time), which is worth
    failing loudly and immediately instead of retrying 2,900 times. Later
    queries are never strict, even if nothing has succeeded yet; the
    consecutive-failure abort catches a systemically broken run instead.
    """
    delay = 5.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(sru_url(isbn), timeout=timeout) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SRUDiagnostic(
                    f"response exceeded {MAX_RESPONSE_BYTES} bytes; refusing to parse"
                )
            return parse_mods(raw)
        except SRUDiagnostic:
            if strict:
                raise
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
        except urllib.error.URLError, OSError, TimeoutError:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
    return None


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
def find_db(explicit: str | None) -> str:
    if explicit:
        return explicit
    local = os.path.join(os.getcwd(), "metadata.db")
    if os.path.exists(local):
        return local
    print(
        "setup error: no metadata.db here; run from the library dir or pass --db",
        file=sys.stderr,
    )
    sys.exit(2)


def load_targets(
    db_path: str, ids: list[int] | None, prefixes: list[str] | None
) -> list[dict]:
    con = sqlite3.connect(f"file:{urllib.parse.quote(db_path)}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """
            SELECT b.id, b.title, i.val,
                   (SELECT t.name FROM tags t
                      JOIN books_tags_link l ON l.tag = t.id
                     WHERE l.book = b.id LIMIT 1),
                   EXISTS(SELECT 1 FROM identifiers x
                           WHERE x.book = b.id AND x.type = 'lcc')
              FROM books b
              JOIN identifiers i ON i.book = b.id AND i.type = 'isbn'
             ORDER BY b.id
            """
        ).fetchall()
    finally:
        con.close()

    wanted = set(ids) if ids else None
    out = [
        {
            "id": r[0],
            "title": r[1],
            "isbn": r[2],
            "tag": r[3] or "",
            "has_lcc": bool(r[4]),
        }
        for r in rows
        if wanted is None or r[0] in wanted
    ]
    if prefixes:
        out = [t for t in out if tag_matches(t["tag"], prefixes)]
    return out


def tag_matches(tag: str, prefixes: list[str]) -> bool:
    """Anchored-hierarchical match, the same rule cquarry's `tags:` search uses:
    'NonFic' matches 'NonFic' and anything under 'NonFic.', but never 'NonFiction'."""
    return any(tag == p or tag.startswith(p + ".") for p in prefixes)


def calibre_running() -> bool:
    return (
        subprocess.run(["pgrep", "-x", "calibre"], capture_output=True).returncode == 0
    )


def backup_db(db_path: str) -> str:
    """Copy metadata.db to the sibling .backups dir before writing."""
    lib = os.path.dirname(os.path.abspath(db_path))
    backups = os.path.join(os.path.dirname(lib), os.path.basename(lib) + ".backups")
    os.makedirs(backups, exist_ok=True)
    # Seconds in the stamp plus a collision counter so a second --apply run
    # gets its own file instead of clobbering the earlier restore point.
    stamp = time.strftime("%Y-%m-%d-%H%M%S")
    dest = os.path.join(backups, f".bak-{stamp}-library-codes-metadata.db")
    n = 1
    while os.path.exists(dest):
        n += 1
        dest = os.path.join(backups, f".bak-{stamp}-{n}-library-codes-metadata.db")
    shutil.copy2(db_path, dest)
    return dest


def write_identifiers(db_path: str, writes: list[tuple[int, str, str]]) -> int:
    """INSERT OR REPLACE identifiers. UNIQUE(book,type) makes this an upsert."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.executemany(
            "INSERT OR REPLACE INTO identifiers(book, type, val) VALUES (?, ?, ?)",
            writes,
        )
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return len(writes)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def branch_of(tag: str) -> str:
    """Collapse a tag to its two-level branch, e.g. Fic.Fantasy.Epic -> Fic.Fantasy."""
    if not tag:
        return "(untagged)"
    parts = tag.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def report(results: list[dict], quiet: bool) -> None:
    hits = Counter()
    total = Counter()
    for r in results:
        b = branch_of(r["tag"])
        total[b] += 1
        if r.get("lcc"):
            hits[b] += 1

    n = sum(total.values())
    got = sum(hits.values())
    print()
    print(
        f"queried {n} book(s); LCC found for {got} ({got / n:.0%})"
        if n
        else "nothing queried"
    )
    if quiet or not n:
        return

    print("\nhit rate by tag branch:")
    for branch in sorted(total, key=lambda b: (-total[b], b)):
        bar = "#" * round(20 * hits[branch] / total[branch])
        print(f"  {branch:<30} {hits[branch]:>4}/{total[branch]:<4} {bar}")


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description="Derive LCC/DDC codes from the Library of Congress SRU catalogue.",
        epilog="Default is a dry run; nothing is written without --apply.",
    )
    ap.add_argument("--db", help="path to metadata.db (default: ./metadata.db)")
    ap.add_argument(
        "--apply", action="store_true", help="write identifiers (needs Calibre closed)"
    )
    ap.add_argument(
        "--write-ddc", action="store_true", help="also store the ddc identifier"
    )
    ap.add_argument("--sample", type=int, metavar="N", help="random sample of N books")
    ap.add_argument(
        "--seed", type=int, default=None, help="seed for --sample (reproducible)"
    )
    ap.add_argument("--id", help="comma-separated book ids to restrict to")
    ap.add_argument(
        "--tag",
        metavar="PREFIX",
        help="comma-separated tag prefixes, anchored-hierarchical "
        "(e.g. 'NonFic' covers NonFic and every NonFic.* leaf)",
    )
    ap.add_argument("--limit", type=int, help="stop after N queries")
    ap.add_argument(
        "--refresh", action="store_true", help="re-query books that already have lcc"
    )
    ap.add_argument(
        "--delay",
        type=float,
        default=2.0,
        help="seconds between requests (default 2.0)",
    )
    ap.add_argument(
        "--timeout", type=int, default=25, help="per-request timeout (default 25)"
    )
    ap.add_argument(
        "--cache", default=DEFAULT_CACHE, help=f"cache file (default {DEFAULT_CACHE})"
    )
    ap.add_argument(
        "--no-cache", action="store_true", help="ignore and do not update the cache"
    )
    ap.add_argument("--quiet", action="store_true", help="suppress per-book lines")
    args = ap.parse_args()

    if args.delay < 1.0:
        print(
            "warning: a delay below 1.0s gets you rate-limited by LoC", file=sys.stderr
        )

    ids = None
    if args.id:
        try:
            ids = [int(x) for x in args.id.split(",") if x.strip()]
        except ValueError:
            print("setup error: --id takes comma-separated integers", file=sys.stderr)
            return 2

    db_path = find_db(args.db)
    if not os.path.exists(db_path):
        print(f"setup error: no such database: {db_path}", file=sys.stderr)
        return 2

    if args.apply and calibre_running():
        print(
            "setup error: Calibre is running; close it before --apply", file=sys.stderr
        )
        return 2

    prefixes = (
        [p.strip() for p in args.tag.split(",") if p.strip()] if args.tag else None
    )
    targets = load_targets(db_path, ids, prefixes)
    if not targets:
        print("nothing to do: no book with an ISBN matched --id/--tag")
        return 0
    selected = len(targets)
    if not args.refresh:
        targets = [t for t in targets if not t["has_lcc"]]
    if args.sample and args.sample < len(targets):
        random.seed(args.seed)
        targets = random.sample(targets, args.sample)
        targets.sort(key=lambda t: t["id"])
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print(
            f"nothing to do: all {selected} selected book(s) already have an lcc identifier"
        )
        return 0

    cache = {} if args.no_cache else load_cache(args.cache)
    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"{mode}: {len(targets)} book(s) with an ISBN and no LCC")
    print(f"pacing {args.delay}s between requests; cached results reused")
    if not args.apply:
        print("(nothing will be written; re-run with --apply to store)")
    print()

    results: list[dict] = []
    writes: list[tuple[int, str, str]] = []
    consecutive_failures = 0
    queried = 0
    aborted = False

    try:
        for i, t in enumerate(targets, 1):
            isbn = t["isbn"]
            cached = cache.get(isbn)
            if cached is not None:
                codes = cached
                src = "cache"
            else:
                try:
                    codes = fetch_codes(isbn, args.timeout, strict=(queried == 0))
                except SRUDiagnostic as e:
                    print(
                        f"  ABORT: the very first query was refused: {e}\n"
                        "  This means the query form is wrong, not that the book is missing.",
                        file=sys.stderr,
                    )
                    return 1
                queried += 1
                if codes is None:
                    consecutive_failures += 1
                    if not args.quiet:
                        print(
                            f"  [{i}/{len(targets)}] ERR  #{t['id']} {t['title'][:44]}"
                        )
                    if consecutive_failures >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                        # Break rather than return: everything found before LoC
                        # stopped answering is still good and still gets written.
                        print(
                            f"\nSTOPPING: {consecutive_failures} consecutive failures. "
                            "LoC is refusing us; writing what we have, resume later "
                            "(lookups are cached, so a re-run replays them instantly).",
                            file=sys.stderr,
                        )
                        aborted = True
                        break
                    time.sleep(args.delay)
                    continue
                consecutive_failures = 0
                src = "loc"
                if not args.no_cache:
                    cache[isbn] = codes
                    if queried % 25 == 0:
                        save_cache(args.cache, cache)

            lcc, ddc = codes.get("lcc"), codes.get("ddc")
            results.append({**t, "lcc": lcc, "ddc": ddc})

            if lcc:
                writes.append((t["id"], "lcc", lcc))
                if args.write_ddc and ddc:
                    writes.append((t["id"], "ddc", ddc))
                if not args.quiet:
                    print(
                        f"  [{i}/{len(targets)}] HIT  {lcc:<24} #{t['id']} {t['title'][:40]}"
                    )
            elif not args.quiet:
                print(
                    f"  [{i}/{len(targets)}] --   {'':<24} #{t['id']} {t['title'][:40]}"
                )

            if src == "loc":
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\ninterrupted; progress cached, re-run to resume", file=sys.stderr)
    finally:
        if not args.no_cache:
            save_cache(args.cache, cache)

    report(results, args.quiet)

    if not writes:
        print("\nno codes found; nothing to write")
        return 0

    if not args.apply:
        print(
            f"\ndry run: {len(writes)} identifier(s) would be written. Re-run with --apply."
        )
        return 0

    dest = backup_db(db_path)
    print(f"\nbacked up metadata.db to {dest}")
    try:
        n = write_identifiers(db_path, writes)
    except sqlite3.Error as e:
        print(f"write error: {e}", file=sys.stderr)
        return 1
    print(f"wrote {n} identifier(s).")
    print("Next: run validate_library.py, then reconcile_file_metadata.py --apply")
    if aborted:
        print("NOTE: the pass stopped early; re-run to cover the remaining books.")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
