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
constraint, not this tool's. Only a well-formed SRU answer is cached: a genuine
zero-hit is a miss, but a throttle page, a proxy body or any other non-answer
retries and never caches (issue #36: one throttled evening once poisoned the
cache with hundreds of {} permanent misses). `--refresh` is the escape hatch
from a poisoned cache: it re-queries past the stored answers entirely.

Coverage is partial by nature. LoC catalogues what it catalogues: academic,
canonical and mainstream trade titles resolve well, while genre fiction, indie
and small-press releases, translations and TTRPG material often have no record.
Run in the default dry-run mode first and read the per-branch hit rate before
committing to a full pass.

Title-fragment fallback (`--sru-fallback`): the ISBN index is the narrowest
door into the catalogue. The September 2026 hand pass showed most "no LoC
record" ISBN misses were nothing of the kind: 15 of 17 misses resolved at work
level through a `bath.title=` query (only a German Analysis 3 and a Chinese
Kodaira title have no LoC record at all). Issue #36 sharpened the query shape:
the DB's FULL title kills the LoC title-index match (the subtitle is part of
the indexed string), and `bath.author` clauses intermittently draw the
unsupported-attribute diagnostic (1/16, Bib-1 114), so the fallback queries a
pre-colon fragment of the title (capped at 45 characters) with up to 20
records back and filters those records by the book's author surname
client-side before trusting a classification. Hits are tagged work-level in
the report because the classification belongs to the matched work, not
necessarily this edition, and deserve a glance before they are trusted.

Placeholder classifications are refused, not stored (issue #10): LoC
ebook-preview records carry 050 control numbers (`MLCM 2024/43047 (B)`,
`CPB Box no. 305 vol. 19`), `ACQUISITION IN PROCESS` placeholders, and
children's records put `[Fic]` where a Dewey number belongs. A value that
fails the classification guard reads as a miss: it is never written, and
the book lands in the misses worklist like any other gap.

Run from the library directory:
    python3 fetch_library_codes.py                    # dry run, whole library
    python3 fetch_library_codes.py --sample 200       # dry run, random sample
    python3 fetch_library_codes.py --id 8541,8542     # dry run, specific books
    python3 fetch_library_codes.py --apply            # write identifiers
    python3 fetch_library_codes.py --apply --write-ddc  # also store ddc
    python3 fetch_library_codes.py --apply --all-codes  # one pass, both codes

Exit codes:
    0 = completed (with or without hits)
    1 = aborted (repeated network failure) or a write error
    2 = setup error (missing DB, Calibre running while --apply, bad arguments)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import json
import os
import random
import re
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

from cquarry.write import WritableCalibreDB

try:
    from version_guard import announce
except ImportError:  # a lone deployed copy: no siblings beside it

    def announce(stream=None):
        pass


from vir_tui import core as ui

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

# Seconds the "is Calibre running" probe gets before it is treated as hung.
PGREP_TIMEOUT = 10


# --------------------------------------------------------------------------
# classification guards (issue #10)
# --------------------------------------------------------------------------
# LoC's LCDB answers mix real classifications with placeholder 050s: the
# ebook-preview program's records carry MLCM/MLCS control numbers, CPB box
# numbers, and "ACQUISITION IN PROCESS" placeholders, and children's records
# put "[Fic]" where a Dewey number belongs. Stored verbatim (80 such values
# found in the live library, 2026-10-11), each poisons the composite column
# and rides reconcile into every embed. Both guards treat junk as a miss: the
# work genuinely has no classification yet, so it lands in the misses
# worklist like any other gap.
_LCC_SHAPE = re.compile(r"^[A-Z]{1,3} ?\d{1,4}(?:\.\d+)?")
_LCC_PLACEHOLDER_PREFIXES = ("MLCM", "MLCS", "CPB")
_DDC_SHAPE = re.compile(r"^\d{1,3}(?:\.\d+)?")


def valid_lcc(value: str) -> bool:
    """Does this 050 look like a classification and not a control number or
    acquisition placeholder? The shape gate is the issue's suggested one; the
    prefix/keyword gate names the families that can still wear a
    classification shape ("MLC 2024/..." would pass the regex)."""
    v = (value or "").strip()
    if not v:
        return False
    head = v.upper()
    if head.startswith(_LCC_PLACEHOLDER_PREFIXES) or "IN PROCESS" in head:
        return False
    return bool(_LCC_SHAPE.match(v))


def valid_ddc(value: str) -> bool:
    """Dewey is numeric; bracketed shelf marks ([Fic]) and word values are
    not Dewey numbers, whatever the record's authority tag claims."""
    v = (value or "").strip()
    return bool(v) and bool(_DDC_SHAPE.match(v))


_CODE_GUARDS = {"lcc": valid_lcc, "ddc": valid_ddc}


def sanitize_codes(codes: dict[str, str]) -> dict[str, str]:
    """Drop junk classifications from a codes dict. Applied in parse_mods and
    again wherever codes are read back, because the on-disk cache carries
    entries written before the guard existed."""
    out = dict(codes)
    for key, guard in _CODE_GUARDS.items():
        if key in out and not guard(out[key]):
            del out[key]
    return out


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
def sru_url(cql: str, max_records: int = 5) -> str:
    query = urllib.parse.quote(cql)
    return (
        f"{SRU_BASE}?version=1.1&operation=searchRetrieve&recordSchema=mods"
        f"&maximumRecords={max_records}&query={query}"
    )


def isbn_query(isbn: str) -> str:
    return f"bath.isbn={isbn}"


def title_fragment(title: str, cap: int = 45) -> str:
    """The distinctive-fragment query shape the library CLAUDE.md coverage
    rule documents (issue #36): LoC's title index matches the whole indexed
    string, so the DB's full title-with-subtitle returns nothing. Cut at the
    first colon, then cap on a word boundary. A title that leaves nothing
    (a leading colon) comes back empty; the caller falls back to the full
    title, no worse than the old query."""
    frag = (title or "").split(":", 1)[0].strip()
    if len(frag) > cap:
        frag = frag[:cap].rsplit(" ", 1)[0]
    return frag


def author_surname(name: str) -> str:
    """The compact surname key a work-level lookup caches under: the
    pre-comma heading of a 'Last, First' stored form, else the last token of
    a plain display name. Empty when unusable (no name, or the Calibre
    'Unknown' placeholder): the caller skips the fallback then, because the
    client-side filter could never certify a record for it."""
    s = " ".join((name or "").split())
    if not s or s.lower() == "unknown":
        return ""
    if "," in s:
        return s.split(",", 1)[0].strip().lower()
    return s.rsplit(" ", 1)[-1].lower()


def work_query(fragment: str) -> str:
    """The work-level CQL, one title-fragment clause. The September 2026
    hand pass paired the title with `bath.author=`; issue #36 retired both
    halves of that shape: the full DB title dies on its subtitle in the
    title index, and lx2 intermittently answers `bath.author`/`bath.name`
    clauses with the unsupported-attribute diagnostic (1/16, Bib-1 114)
    while accepting the same forms at other times, so the author filters
    client-side instead (parse_mods_for_author). Double quotes are
    stripped, never escaped: they cannot appear inside a quoted CQL string,
    and a malformed query burns three retries plus a slot in the
    consecutive-failure abort counter."""
    return f'bath.title="{(fragment or "").replace(chr(34), "")}"'


def work_cache_key(fragment: str, surname: str) -> str:
    """Cache key for a work-level lookup: the queried fragment plus the
    author-surname key the records were filtered by. Distinct from any ISBN
    key, and normalized so the same lookup asked twice shares one entry.
    The surname half is what keeps two books that share a title fragment
    from sharing an author's verdict."""

    def norm(s: str) -> str:
        return " ".join((s or "").split()).lower()

    return f"work:{norm(fragment)}|{norm(surname)}"


class SRUDiagnostic(Exception):
    """The SRU server answered with a diagnostic instead of records."""


class SRUResponseError(SRUDiagnostic):
    """The response body is not an SRU records answer at all: unparseable
    XML, a wrong root element, an oversize body. This is the issue-#36
    throttle gate: a rate-limit or proxy page must never fall through the
    parser as {}, because the caller caches a miss. Retried like a network
    error and never strict-fatal: the query form cannot produce a non-SRU
    body, so the first-query abort does not apply."""


def _parse_srw(raw: bytes) -> ET.Element:
    """The shared SRU answer gate: parse, prove the body IS a
    searchRetrieveResponse, and surface a server diagnostic. A genuine
    zero-hit answer is always well-formed SRU carrying no records, so an
    unparseable body reads as a failed request (SRUResponseError), never as
    a cacheable miss."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SRUResponseError(
            "response is not well-formed XML (a throttle or proxy page?)"
        )
    if root.tag != f"{SRW_NS}searchRetrieveResponse":
        raise SRUResponseError(
            f"response is not an SRU answer (root element {root.tag})"
        )
    diag = root.find(f".//{SRW_NS}diagnostics")
    if diag is not None:
        msg = root.find(".//{http://www.loc.gov/zing/srw/diagnostic/}message")
        raise SRUDiagnostic(msg.text if msg is not None else "unknown diagnostic")
    return root


def _record_codes(mods: ET.Element) -> dict[str, str]:
    """The validated {'lcc': ..., 'ddc': ...} one MODS record carries; junk
    values (issue #10) are absent, never stored."""
    out: dict[str, str] = {}
    for authority, guard in _CODE_GUARDS.items():
        el = mods.find(f"./mods:classification[@authority='{authority}']", MODS_NS)
        if el is not None and el.text and el.text.strip():
            value = el.text.strip()
            if guard(value):
                out[authority] = value
    return out


def _record_authors(mods: ET.Element) -> list[str]:
    """Every namePart the record carries (LoC headings, 'Surname, Forename',
    dates after a second comma)."""
    return [
        el.text.strip()
        for el in mods.findall("./mods:name/mods:namePart", MODS_NS)
        if el.text and el.text.strip()
    ]


def author_matches(name_parts: list[str], author: str) -> bool:
    """Does any LoC name heading carry this stored author? The heading is a
    namePart's pre-comma form ('Gamow' out of 'Gamow, George, 1904-1968').
    It matches the folded stored name at whole-token width: equality, the
    stored name's own pre-comma heading (a 'Last, First' stored form), or
    the heading as a tail of the stored name ('le guin' ends 'ursula k. le
    guin'). Same-surname different-forename collisions pass by design: the
    work-level caveat in the report is the honest boundary of a surname
    filter. Unusable authors (empty, the 'Unknown' placeholder) match
    nothing."""
    folded = " ".join((author or "").split()).lower()
    if not folded or folded == "unknown":
        return False
    head = folded.split(",", 1)[0].strip() if "," in folded else ""
    for part in name_parts:
        heading = " ".join(part.split(",", 1)[0].split()).lower()
        if heading and (
            heading == folded or heading == head or folded.endswith(" " + heading)
        ):
            return True
    return False


def parse_mods(raw: bytes) -> dict[str, str]:
    """Return {'lcc': ..., 'ddc': ...} from the first record carrying each,
    validated by the classification guards (issue #10): a junk 050 on the
    first record is skipped, not stored, and a later record's real
    classification still wins."""
    root = _parse_srw(raw)
    out: dict[str, str] = {}
    # Walk record by record so a code is never taken from a sibling record
    # that happens to sit later in the same response.
    for mods in root.findall(f".//{SRW_NS}recordData/mods:mods", MODS_NS):
        for authority, value in _record_codes(mods).items():
            out.setdefault(authority, value)
    return out


def parse_mods_for_author(raw: bytes, author: str) -> dict[str, str]:
    """parse_mods over a title-fragment answer, scored against the
    requesting book's author (issue #36): only records whose name headings
    match the stored author may donate a classification, so a 20-record
    fragment response cannot hand over a different author's work. No
    matching record is the genuine miss it reads as: the fragment was
    asked, this author did not answer."""
    root = _parse_srw(raw)
    out: dict[str, str] = {}
    for mods in root.findall(f".//{SRW_NS}recordData/mods:mods", MODS_NS):
        if not author_matches(_record_authors(mods), author):
            continue
        for authority, value in _record_codes(mods).items():
            out.setdefault(authority, value)
    return out


def fetch_codes(
    cql: str,
    timeout: int,
    retries: int = 3,
    *,
    strict: bool = False,
    max_records: int = 5,
    author: str | None = None,
) -> dict[str, str] | None:
    """Query LoC with one CQL string. Returns a dict (possibly empty) or None
    on failure.

    SRU diagnostics are retried like network errors rather than treated as fatal.
    LoC intermittently answers a perfectly good query with "Query feature
    unsupported" when it is under load: an ISBN that failed that way mid-run
    returned a record on the very next try. Aborting the whole pass on one of
    those throws away hours of work for a server hiccup.

    A body that is not an SRU answer at all (SRUResponseError: the throttle
    page, a proxy body, truncated XML) retries the same way and comes back
    None, so nothing is ever cached from it (issue #36's cache poisoning:
    hundreds of throttled requests once landed as {} permanent misses). A
    genuine zero-hit parses to {} and stays cacheable.

    `strict` is set for the very first query of a run. There a diagnostic is far
    more likely to mean the query form itself is wrong (the plugin's dead
    `dc.identifier` index answers this way every single time), which is worth
    failing loudly and immediately instead of retrying 2,900 times. Later
    queries are never strict, even if nothing has succeeded yet; the
    consecutive-failure abort catches a systemically broken run instead.

    `author` switches the parse to the surname-gated form
    (parse_mods_for_author); the title-fragment fallback is its only caller,
    with `max_records` raised to 20 for the same reason.
    """
    delay = 5.0
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(
                sru_url(cql, max_records), timeout=timeout
            ) as resp:
                raw = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SRUResponseError(
                    f"response exceeded {MAX_RESPONSE_BYTES} bytes; refusing to parse"
                )
            if author is None:
                return parse_mods(raw)
            return parse_mods_for_author(raw, author)
        except SRUResponseError:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
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
                   EXISTS(SELECT 1 FROM identifiers x
                           WHERE x.book = b.id AND x.type = 'lcc'),
                   EXISTS(SELECT 1 FROM identifiers x
                           WHERE x.book = b.id AND x.type = 'ddc')
              FROM books b
              JOIN identifiers i ON i.book = b.id AND i.type = 'isbn'
             ORDER BY b.id
            """
        ).fetchall()
        booktags: dict[int, list[str]] = {}
        for bid, tname in con.execute(
            "SELECT bt.book, t.name FROM books_tags_link bt JOIN tags t ON t.id = bt.tag"
        ):
            booktags.setdefault(bid, []).append(tname)
        # First author (link order) drives the work-level SRU fallback.
        bookauthors: dict[int, list[str]] = {}
        for bid, aname in con.execute(
            "SELECT bal.book, a.name FROM books_authors_link bal "
            "JOIN authors a ON a.id = bal.author"
        ):
            bookauthors.setdefault(bid, []).append(aname)
    finally:
        con.close()

    wanted = set(ids) if ids else None
    out = []
    for r in rows:
        if wanted is not None and r[0] not in wanted:
            continue
        tags = sorted(booktags.get(r[0], []))
        if prefixes and not tag_matches(tags, prefixes):
            continue
        out.append(
            {
                "id": r[0],
                "title": r[1],
                "isbn": r[2],
                "authors": bookauthors.get(r[0], []),
                "tag": display_tag(tags, prefixes),
                "tags": tags,
                "has_lcc": bool(r[3]),
                "has_ddc": bool(r[4]),
            }
        )
    return out


def tag_matches(tags: list[str], prefixes: list[str]) -> bool:
    """Anchored-hierarchical match over ALL of a book's tags, the same rule
    cquarry's `tags:` search uses: 'NonFic' matches 'NonFic' and anything under
    'NonFic.', but never 'NonFiction'.

    Matching every tag is the point. This used to read a single tag pulled by a
    `LIMIT 1` subquery with no ORDER BY, so a book carrying two tags was scoped
    (and reported under a branch) by whichever one SQLite happened to return.
    """
    return any(t == p or t.startswith(p + ".") for t in tags for p in prefixes)


def display_tag(tags: list[str], prefixes: list[str] | None) -> str:
    """The tag a book is reported under. Under a --tag filter, prefer one the
    filter actually selected on, so the per-branch hit rate agrees with the
    scoping."""
    if prefixes:
        for t in tags:
            if tag_matches([t], prefixes):
                return t
    return tags[0] if tags else ""


def calibre_running() -> bool:
    # Anchored NAME match, never -f: command lines must not be able to trip
    # this (a concurrent Bindery sweep mentions "Calibre Library" in its args).
    # The prefix covers the GUI, calibre-debug, and the calibre-parallel job
    # workers, whose comm truncates to "calibre-paralle" — a full-name -x match
    # cannot ever see them, so it under-refused while a worker touched the DB.
    # (The 2026-08-27 "args false-positive" record was a misdiagnosis: -x had
    # been in place since 2026-08-09, and a name-only probe cannot match args.)
    try:
        return (
            subprocess.run(
                ["pgrep", "^calibre"], capture_output=True, timeout=PGREP_TIMEOUT
            ).returncode
            == 0
        )
    except subprocess.TimeoutExpired:
        # Can't tell. This gates --apply, so the safe answer is "assume yes":
        # refusing costs a re-run, guessing wrong writes to a live database.
        return True
    except OSError:
        # An unrunnable pgrep is "can't tell" too: fail closed.
        return True


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
    """Store identifiers through cquarry's write module; returns how many
    actually changed.

    This used to be a raw INSERT OR REPLACE: it worked, but it bypassed the
    ecosystem's only sanctioned write path, so the touched books never landed
    in `metadata_dirtied` (Calibre never regenerated their sidecar .opfs) and
    `last_modified` never moved. `set_identifier` inside one `batch()`
    transaction fixes both. Concurrent writers are absorbed by the module's
    30s busy timeout plus the retry/backoff below (the 2026-09-02 incident:
    two background --apply passes contending on the write lock). Identical
    values are no-ops and are not counted, so a `--refresh` re-run reports
    honestly.
    """
    delay = 2.0
    for attempt in range(4):
        try:
            with WritableCalibreDB(db_path) as wdb:
                with wdb.batch():
                    return sum(wdb.set_identifier(b, t, v) for b, t, v in writes)
        except sqlite3.OperationalError as e:
            if "locked" not in str(e) and "busy" not in str(e):
                raise
            if attempt == 3:
                raise
            time.sleep(delay)
            delay *= 2
    raise AssertionError("unreachable")


def _needs_query(t: dict, want_ddc: bool) -> bool:
    """A book needs querying when the pass wants a code it does not have."""
    if want_ddc:
        return not (t["has_lcc"] and t["has_ddc"])
    return not t["has_lcc"]


def default_misses_file() -> str:
    """The misses worklist default: beside this script, never the working
    directory (issue #7). The documented invocation runs from the library
    root, so the old CWD default wrote the worklist INSIDE the library,
    where the tree audit flags it as a stray. The script directory is
    stable across runs, outside any library, and *.txt is gitignored in
    the checkout; --misses-file overrides it."""
    return str(Path(__file__).resolve().parent / "fetch_library_codes_misses.txt")


# Run sections the worklist file keeps. The file is append-per-run now
# (issue #36): the old truncate-on-write destroyed the accumulated worklist
# whenever any later run fired, however small, which is how the October 2026
# residue grew invisible. The cap keeps a full-library run from stacking
# hundreds-of-line sections forever; the newest sections are the live list.
MISSES_RUN_SECTIONS_KEPT = 8
_MISSES_RUN_MARKER = "# --- run "


def write_misses_worklist(results: list[dict], path: str, mode: str = "DRY RUN") -> int:
    """Append this run's manual-research worklist as a timestamped section:
    every book the SRU pass could not code, one per line as
    `id<TAB>isbn<TAB>ddc<TAB>title`, under a `# --- run <UTC stamp>
    (<mode>): N miss(es) of M queried` header with the id list beside it.
    Append, never truncate (issue #36: any later run, however small, used
    to clobber the accumulated list); the file keeps the most recent
    MISSES_RUN_SECTIONS_KEPT sections, with pre-marker (single-run-era)
    content counting as one legacy section until pruned. A report artifact,
    not a database write: it is produced in dry runs too."""
    misses = [r for r in results if not r.get("lcc")]
    if not misses:
        return 0

    ids = [str(r["id"]) for r in misses]
    section = [
        f"{_MISSES_RUN_MARKER}"
        f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} ({mode}): "
        f"{len(misses)} miss(es) of {len(results)} queried\n",
    ]
    for i in range(0, len(ids), 16):
        section.append("# ids: " + " ".join(ids[i : i + 16]) + "\n")
    section.append("# Manual research pass: find the code, then\n")
    section.append("#   wdb.set_identifier(<id>, 'lcc', '<code>')\n")
    section.append("# id\tisbn\tddc\ttitle\n")
    for r in misses:
        section.append(
            f"{r['id']}\t{r.get('isbn', '')}\t{r.get('ddc') or ''}\t{r['title']}\n"
        )

    sections: list[list[str]] = []
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.startswith(_MISSES_RUN_MARKER) or not sections:
                    sections.append([line])
                else:
                    sections[-1].append(line)
    sections.append(section)
    with open(path, "w", encoding="utf-8") as fh:
        for kept in sections[-MISSES_RUN_SECTIONS_KEPT:]:
            # A hand-edited file may end without a newline; gluing the next
            # run's marker onto its last line would fuse the sections
            # permanently (the marker would never start a line again).
            if kept and not kept[-1].endswith("\n"):
                kept[-1] += "\n"
            fh.writelines(kept)
    return len(misses)


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def branch_of(tag: str) -> str:
    """Collapse a tag to its two-level branch, e.g. Fic.Fantasy.Epic -> Fic.Fantasy."""
    if not tag:
        return "(untagged)"
    parts = tag.split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else parts[0]


def report(results: list[dict], quiet: bool, want_ddc: bool = False) -> None:
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
    work_hits = sum(1 for r in results if r.get("lcc") and r.get("via") == "work")
    if work_hits:
        print(
            f"  of the {got} LCC hit(s), {work_hits} came via the title-fragment "
            "fallback (work-level: the matched work's class; verify against "
            "this edition)"
        )
    if want_ddc and n:
        ddc_got = sum(1 for r in results if r.get("ddc"))
        print(f"DDC found for {ddc_got} ({ddc_got / n:.0%})")
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
        epilog="Default is a dry run; nothing is written to the database without --apply. (The misses worklist file is a report artifact and is written in dry runs too; runs append timestamped sections and the file keeps the most recent ones.)",
    )
    ap.add_argument("--db", help="path to metadata.db (default: ./metadata.db)")
    ap.add_argument(
        "--apply", action="store_true", help="write identifiers (needs Calibre closed)"
    )
    ap.add_argument(
        "--write-ddc", action="store_true", help="also store the ddc identifier"
    )
    ap.add_argument(
        "--all-codes",
        dest="all_codes",
        action="store_true",
        help="one pass storing both lcc and ddc (they arrive in the same "
        "SRU query; selects every book missing either code)",
    )
    ap.add_argument(
        "--misses-file",
        default=default_misses_file(),
        help="worklist file for the manual-research pass; each run appends a "
        "timestamped section listing every book that ended the pass with no "
        f"LCC (default: {default_misses_file()}, beside this script; the "
        "documented invocation runs from the library root and the worklist "
        "must not land inside the library)",
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
        "--refresh",
        action="store_true",
        help="re-query books that already have lcc, bypassing the response "
        "cache (stored answers, misses included, are fetched again and "
        "replaced: the escape from a poisoned cache; writes still land, so "
        "the rest of the cache file survives)",
    )
    ap.add_argument(
        "--sru-fallback",
        dest="sru_fallback",
        action="store_true",
        help="after a clean ISBN miss, try one title-fragment SRU query "
        "(20 records, filtered client-side by the book's author surname; "
        "work-level: the matched work's classification, not necessarily "
        "this edition's; hit lines and the report say so)",
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
    ui.print_header("fetch_library_codes.py - Execution")
    announce()

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
    want_ddc = args.write_ddc or args.all_codes
    if not args.refresh:
        targets = [t for t in targets if _needs_query(t, want_ddc)]
    if args.sample and args.sample < len(targets):
        random.seed(args.seed)
        targets = random.sample(targets, args.sample)
        targets.sort(key=lambda t: t["id"])
    if args.limit:
        targets = targets[: args.limit]

    if not targets:
        print(
            f"nothing to do: all {selected} selected book(s) already have the "
            f"requested code(s) ({'lcc+ddc' if want_ddc else 'lcc'})"
        )
        return 0

    cache = {} if args.no_cache else load_cache(args.cache)
    # --refresh reads past the response cache (issue #36): its contract is
    # "re-query", and a stored {} read back as a miss would defeat it. Cache
    # WRITES still land on `cache` (and overwrite), so the accumulated cache
    # file survives a refresh run intact instead of being rewritten to just
    # this run's entries.
    read_cache = {} if args.refresh else cache
    mode = "APPLY" if args.apply else "DRY RUN"
    print(
        f"{mode}: {len(targets)} book(s) with an ISBN and no "
        f"{'LCC/DDC' if want_ddc else 'LCC'}"
    )
    if args.refresh:
        print(
            f"pacing {args.delay}s between requests; response cache bypassed (--refresh)"
        )
    else:
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
        for i, t in enumerate(ui.tqdm(targets, desc=ui.info("Fetching codes")), 1):
            isbn = t["isbn"]
            cached = read_cache.get(isbn)
            if cached is not None:
                codes = sanitize_codes(cached)
                src = "cache"
            else:
                try:
                    codes = fetch_codes(
                        isbn_query(isbn), args.timeout, strict=(queried == 0)
                    )
                except SRUDiagnostic as e:
                    ui.tqdm.write(
                        ui.error(
                            f"  ABORT: the very first query was refused: {e}\n"
                            "  This means the query form is wrong, not that the book is missing."
                        ),
                        file=sys.stderr,
                    )
                    return 1
                queried += 1
                if codes is None:
                    consecutive_failures += 1
                    if not args.quiet:
                        ui.tqdm.write(
                            ui.error(
                                f"  [{i}/{len(targets)}] ERR  #{t['id']} {t['title'][:44]}"
                            )
                        )
                    if consecutive_failures >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                        # Break rather than return: everything found before LoC
                        # stopped answering is still good and still gets written.
                        ui.tqdm.write(
                            ui.error(
                                f"\nSTOPPING: {consecutive_failures} consecutive failures. "
                                "LoC is refusing us; writing what we have, resume later "
                                "(lookups are cached, so a re-run replays them instantly)."
                            ),
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
            if not codes and args.sru_fallback and t.get("authors"):
                # A clean ISBN miss, fresh OR cached-out (a resume re-run
                # must get the work-level retry too): one paced
                # title-fragment query with 20 records back, filtered
                # client-side by the book's author surname (issue #36: the
                # full DB title dies on its subtitle in LoC's title index,
                # and the bath.author clause intermittently draws the
                # unsupported-attribute diagnostic; the September 2026 hand
                # pass first proved the shape). A usable surname gates the
                # query: without one the filter could never certify a
                # record. A fallback failure counts toward the abort
                # counter, but the book itself stays a miss, never an ERR.
                frag = title_fragment(t["title"]) or t["title"]
                surname = author_surname(t["authors"][0])
                wkey = work_cache_key(frag, surname)
                wcached = None if args.refresh else read_cache.get(wkey)
                if wcached is not None:
                    if wcached:
                        codes = sanitize_codes(wcached)
                        src = "cache-work"
                elif surname:
                    time.sleep(args.delay)
                    wcodes = fetch_codes(
                        work_query(frag),
                        args.timeout,
                        max_records=20,
                        author=t["authors"][0],
                    )
                    queried += 1
                    if wcodes is None:
                        consecutive_failures += 1
                        if consecutive_failures >= ABORT_AFTER_CONSECUTIVE_FAILURES:
                            ui.tqdm.write(
                                ui.error(
                                    f"\nSTOPPING: {consecutive_failures} "
                                    "consecutive failures. LoC is refusing "
                                    "us; writing what we have, resume later."
                                ),
                                file=sys.stderr,
                            )
                            aborted = True
                            break
                    else:
                        if not args.no_cache:
                            cache[wkey] = wcodes
                        if wcodes:
                            codes = wcodes
                            src = "loc-work"

            # The catch-all guard (issue #10): whatever path produced codes
            # (fresh parse, old cache entry, a future seam), nothing junk
            # reaches the writes, the report, or the worklist from here.
            codes = sanitize_codes(codes)
            lcc, ddc = codes.get("lcc"), codes.get("ddc")
            via = "work" if src in ("loc-work", "cache-work") else "isbn"
            results.append({**t, "lcc": lcc, "ddc": ddc, "via": via})

            if lcc:
                writes.append((t["id"], "lcc", lcc))
            if want_ddc and ddc:
                # DDC rides the same SRU response; store it whenever it was
                # asked for and found, even for a book the catalogue has no
                # LCC for (the old nesting silently dropped exactly those).
                writes.append((t["id"], "ddc", ddc))

            shown = lcc or (ddc if want_ddc else None)
            if shown:
                if not args.quiet:
                    marker = " (work-level)" if via == "work" else ""
                    ui.tqdm.write(
                        ui.success(
                            f"  [{i}/{len(targets)}] HIT  {shown:<24}{marker} "
                            f"#{t['id']} {t['title'][:40]}"
                        )
                    )
            elif not args.quiet:
                ui.tqdm.write(
                    ui.info(
                        f"  [{i}/{len(targets)}] --   {'':<24} #{t['id']} {t['title'][:40]}"
                    )
                )

            if src in ("loc", "loc-work"):
                time.sleep(args.delay)
    except KeyboardInterrupt:
        ui.tqdm.write(
            ui.warn("\ninterrupted; progress cached, re-run to resume"), file=sys.stderr
        )
    finally:
        if not args.no_cache:
            save_cache(args.cache, cache)

    report(results, args.quiet, want_ddc=want_ddc)
    misses = write_misses_worklist(results, args.misses_file, mode)
    if misses:
        print(f"appended {misses} miss(es) to {args.misses_file} for the manual pass")

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
    print(f"stored {n} of {len(writes)} identifier(s).")
    print("Next: run validate_metadata.py, then reconcile_file_metadata.py --apply")
    if aborted:
        print("NOTE: the pass stopped early; re-run to cover the remaining books.")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
