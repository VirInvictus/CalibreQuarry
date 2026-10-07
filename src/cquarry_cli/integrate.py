"""The integration verbs: `cquarry run convert/polish/cover/export/merge/
flush/backfill/trash` (Phase 19 C, plus the 3.45 trash lifecycle).

Most verbs drive external programs (convert/polish: ebook-convert and
ebook-polish; export/flush: calibredb; backfill: fetch-ebook-metadata)
and register the outcome through cquarry's write module; cover, merge,
and trash work purely through cquarry's write APIs and the filesystem.
No new Python dependencies, by design. The house write discipline holds
across all of them:

- dry-run by default: the plan (per-book actions) prints, nothing runs;
- `--apply` demands a closed Calibre (anchored pgrep guard) and, for
  every verb that mutates metadata.db, a `--backup-dir` OUTSIDE the
  library directory (a timestamped sqlite-API backup, a second run
  never destroys an earlier restore point);
- targets resolve read-only first (`--search EXPR` or `--ids`);
  unknown ids abort before anything opens writable;
- one report shape: per-book plan/results with applied/failed counts,
  `--format json` for the machine form.

C.8 (calibredb check_library parity) is deliberately absent: the A.3
tree audit shipped CQ-native, so there is nothing for a subprocess to
add.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from cquarry.helpers import to_isbn13
from cquarry.write import WritableCalibreDB
from cquarry_cli.backups import make_backup
from cquarry_cli.epubdates import norm_date, normalize_epub_dates

_PGREP_TIMEOUT = 10

# convert: formats ebook-convert can produce; polish/cover: EPUB only.
POLISH_OPS = {
    "smarten": "--smarten-punctuation",
    "unused-css": "--remove-unused-css",
    "compress-images": "--compress-images",
    "subset-fonts": "--subset-fonts",
    "jacket": "--add-jacket",
    "kepubify": "--kepubify",
}


def _calibre_running() -> bool:
    """The anchored-name pgrep guard (fetch_library_codes.py precedent).

    Fail-closed on anything that is not a clean "not running" answer: a
    pgrep timeout (or a pgrep that cannot run at all) is assumed-RUNNING,
    because refusing costs a re-run while guessing wrong writes to a
    live database (run.py's recorded semantics; the old cut returned
    False on a timeout and proceeded to --apply against live Calibre)."""
    try:
        proc = subprocess.run(
            ["pgrep", "^calibre"],
            capture_output=True,
            timeout=_PGREP_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return True
    except OSError:
        return True
    return proc.returncode == 0


def resolve_targets(db, args) -> list[int]:
    """Exactly one of --search/--ids (merge uses --keeper/--duplicate
    instead); giving both is refused, because silently preferring one
    would leave the other's ids unvalidated. Unknown hand-supplied ids
    abort here, read-only."""
    search = getattr(args, "search", None)
    raw = getattr(args, "ids", None)
    if search and raw:
        raise ValueError("--search EXPR and --ids are exclusive")
    if search:
        from cquarry.search import ParseException

        try:
            return sorted(set(db.search(search)))
        except ParseException as e:
            raise ValueError(f"could not parse the search expression: {e}") from None
    if not raw:
        return []
    known = db.all_ids()
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            bid = int(part)
        except ValueError:
            raise ValueError(f"invalid book id {part!r}") from None
        if bid not in known:
            raise ValueError(f"unknown book id {bid}")
        out.append(bid)
    return sorted(set(out))


def _dest_path(db, book_id: int, ext: str) -> Path:
    """The book-dir file path a new format lands at (Calibre naming)."""
    row = db.get_book(book_id)
    if row is None:
        raise ValueError(f"unknown book id {book_id}")
    libdir = Path(db.db_path).resolve().parent
    safe = row["title"].replace("/", "_") or "book"
    return libdir / Path(row["path"]) / f"{safe} - {row['id']}.{ext.lower()}"


def plan_convert(db, args) -> list[dict]:
    """One planned conversion per book: source format -> --to. A book
    that already has the target skips at plan time (the merge verb's
    shape): running ebook-convert anyway used to overwrite the existing
    target file and then die on the registration."""
    to_fmt = (args.to or "").upper()
    if not to_fmt:
        raise ValueError("run convert needs --to FORMAT")
    plans = []
    for bid in resolve_targets(db, args):
        formats = db.get_formats(bid) or {}
        if any(f.upper() == to_fmt for f in formats):
            plans.append(
                {
                    "book": bid,
                    "action": "skip",
                    "detail": f"already has {to_fmt}",
                }
            )
            continue
        source = None
        if getattr(args, "from_format", None):
            want = args.from_format.upper()
            if want in formats:
                source = want
        else:
            candidates = [
                (meta.get("size_bytes") or 0, fmt)
                for fmt, meta in formats.items()
                if fmt.upper() != to_fmt
            ]
            if candidates:
                source = max(candidates)[1]
        if source is None:
            plans.append(
                {
                    "book": bid,
                    "action": "skip",
                    "detail": "no source format to convert from",
                }
            )
            continue
        src_path = formats[source]["path"]
        dst = _dest_path(db, bid, to_fmt)
        plans.append(
            {
                "book": bid,
                "action": "convert",
                "source": source,
                "target": to_fmt,
                "src": src_path,
                "dst": str(dst),
            }
        )
    return plans


def run_convert(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        plans = plan_convert(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "convert plan")
    if take_backup and (rc := take_backup()):
        return rc
    applied = failed = 0
    for p in plans:
        if p["action"] != "convert":
            # Plan-time skips ride two shapes: "already has TARGET"
            # (expected idempotence, never a failure -- the file the book
            # already has is untouched) and "no source format to convert
            # from" (an unconvertible book: it can never convert, so it
            # is not counted applied either). Both land here as
            # already-so; a run that needs them separated in the report
            # is a roadmap item, not this pass.
            p["result"] = "already-so"
            continue
        proc = subprocess.run(
            ["ebook-convert", p["src"], p["dst"]],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0 or not Path(p["dst"]).exists():
            p["result"] = "failed"
            p["detail"] = (proc.stderr or "ebook-convert failed")[:200]
            failed += 1
            continue
        try:
            with WritableCalibreDB(db.db_path) as wdb:
                with wdb.batch():
                    wdb.add_format(
                        p["book"],
                        p["target"],
                        Path(p["dst"]).stem,
                        Path(p["dst"]).stat().st_size,
                    )
        except Exception as e:
            # A registration failure is a failed row in the report, not
            # a traceback (the conversion itself succeeded; the file is
            # there for a re-run).
            p["result"] = "failed"
            p["detail"] = f"registration failed: {e}"[:200]
            failed += 1
            continue
        p["result"] = "applied"
        applied += 1
    return _print_results(plans, args, applied, failed)


def plan_polish(db, args) -> list[dict]:
    ops = [o for o in (args.polish_ops or "").split(",") if o]
    flags = []
    for o in ops:
        if o not in POLISH_OPS:
            raise ValueError(
                f"unknown polish op {o!r}; available: {', '.join(sorted(POLISH_OPS))}"
            )
        flags.append(POLISH_OPS[o])
    if not flags:
        raise ValueError("run polish needs --polish-ops (smarten,unused-css,...)")
    plans = []
    for bid in resolve_targets(db, args):
        formats = db.get_formats(bid) or {}
        epub = formats.get("EPUB") or formats.get("KEPUB")
        if epub is None:
            plans.append({"book": bid, "action": "skip", "detail": "no EPUB to polish"})
            continue
        plans.append(
            {
                "book": bid,
                "action": "polish",
                "src": epub["path"],
                "flags": flags,
                "fmt": "EPUB" if "EPUB" in formats else "KEPUB",
            }
        )
    return plans


def run_polish(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        plans = plan_polish(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "polish plan")
    if take_backup and (rc := take_backup()):
        return rc
    applied = failed = 0
    for p in plans:
        if p["action"] != "polish":
            failed += 1
            p["result"] = "skipped"
            continue
        before = os.path.getsize(p["src"]) if os.path.exists(p["src"]) else 0
        proc = subprocess.run(
            ["ebook-polish", *p["flags"], p["src"], p["src"]],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        after = os.path.getsize(p["src"]) if os.path.exists(p["src"]) else 0
        if proc.returncode != 0:
            p["result"] = "failed"
            p["detail"] = (proc.stderr or "ebook-polish failed")[:200]
            failed += 1
            continue
        # Post-verify through cquarry: sync the size the file now has.
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                wdb.set_format(p["book"], p["fmt"], Path(p["src"]).stem, after)
        p["result"] = "applied"
        p["detail"] = f"size {before} -> {after}"
        applied += 1
    return _print_results(plans, args, applied, failed)


def plan_cover(db, args) -> list[dict]:
    if not args.cover and not args.remove_cover:
        raise ValueError("run cover needs --cover FILE or --remove-cover")
    if args.cover and args.remove_cover:
        raise ValueError("--cover FILE and --remove-cover are exclusive")
    cover = os.path.abspath(args.cover) if args.cover else None
    if cover and not os.path.exists(cover):
        raise ValueError(f"cover file not found: {cover}")
    plans = []
    for bid in resolve_targets(db, args):
        plans.append(
            {
                "book": bid,
                "action": "set_cover" if cover else "remove_cover",
                "cover": cover,
            }
        )
    return plans


def run_cover(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        plans = plan_cover(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "cover plan")
    if take_backup and (rc := take_backup()):
        return rc
    applied = failed = 0
    for p in plans:
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                if p["action"] == "set_cover":
                    changed = wdb.set_cover(p["book"], p["cover"])
                else:
                    changed = wdb.remove_cover(p["book"])
        p["result"] = "applied" if changed else "already-so"
        # An already-so row is not an application (run_convert's
        # counting, not the old blanket +1).
        applied += 1 if changed else 0
    return _print_results(plans, args, applied, failed)


def plan_export(db, args) -> list[dict]:
    if not getattr(args, "dest", None):
        raise ValueError("run export needs --dest DIR")
    ids = resolve_targets(db, args)
    if not ids:
        raise ValueError("no books matched the selection")
    template = args.template or "{author_sort}/{title} {id}"
    return [
        {"book": bid, "action": "export", "dest": args.dest, "template": template}
        for bid in ids
    ]


def run_export(db, args, *, apply: bool, take_backup=None) -> int:
    """Export touches nothing in the library: no backup, ever."""
    try:
        plans = plan_export(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    ids = ",".join(str(p["book"]) for p in plans)
    library = str(Path(db.db_path).resolve().parent)
    cmd = [
        "calibredb",
        "export",
        "--library",
        library,
        "--to-dir",
        args.dest,
        "--template",
        plans[0]["template"],
        "--dont-write-opf",
        ids,
    ]
    if not apply:
        print(
            f"export plan: {len(plans)} book(s) -> {args.dest} "
            f"(template {plans[0]['template']!r})"
        )
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        print(f"ERROR: calibredb export failed: {proc.stderr[:300]}", file=sys.stderr)
        return 1
    print(f"Exported {len(plans)} book(s) to {args.dest}.")
    return 0


def plan_merge(db, args) -> list[dict]:
    keeper, duplicate = args.keeper, args.duplicate
    if not keeper or not duplicate:
        raise ValueError("run merge needs --keeper ID and --duplicate ID")
    known = db.all_ids()
    if int(keeper) not in known:
        raise ValueError(f"unknown keeper id {keeper}")
    if int(duplicate) not in known:
        raise ValueError(f"unknown duplicate id {duplicate}")
    if int(keeper) == int(duplicate):
        raise ValueError("keeper and duplicate are the same book")
    keeper_fmts = db.get_formats(int(keeper)) or {}
    dup_fmts = db.get_formats(int(duplicate)) or {}
    moves = [
        {"fmt": fmt, "src": meta["path"]}
        for fmt, meta in sorted(dup_fmts.items())
        if fmt.upper() not in {k.upper() for k in keeper_fmts}
    ]
    return [
        {
            "book": int(keeper),
            "action": "merge",
            "duplicate": int(duplicate),
            "moves": moves,
        }
    ]


def run_merge(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        plans = plan_merge(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "merge plan")
    if take_backup and (rc := take_backup()):
        return rc
    applied = failed = 0
    for p in plans:
        p["results"] = []
        ok = True
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                keeper_row = db.get_book(p["book"])
                keeper_dir = Path(db.db_path).resolve().parent / Path(
                    keeper_row["path"]
                )
                keeper_dir.mkdir(parents=True, exist_ok=True)
                for move in p["moves"]:
                    src = Path(move["src"])
                    if not src.exists():
                        p["results"].append(
                            {
                                **move,
                                "result": "failed",
                                "detail": "source file missing",
                            }
                        )
                        ok = False
                        continue
                    target = keeper_dir / src.name
                    shutil.copy2(src, target)
                    changed = wdb.add_format(
                        p["book"],
                        move["fmt"],
                        src.stem,
                        target.stat().st_size,
                    )
                    p["results"].append(
                        {**move, "result": "applied" if changed else "already-so"}
                    )
                # The duplicate's removal lands in cquarry 1.20's trash
                # (.caltrash/b/<id>/, recoverable by hand until expired).
                wdb.remove_book(p["duplicate"], delete_files="trash")
        p["result"] = "applied" if ok else "failed"
        applied += 1 if ok else 0
        failed += 0 if ok else 1
    return _print_results(plans, args, applied, failed)


def run_flush(db, args, *, apply: bool, take_backup=None) -> int:
    """Headless OPF-queue flush: calibredb embed_metadata for the ids in
    metadata_dirtied (chunked; the reconcile precedent)."""
    ids = db.get_dirtied_books()
    if getattr(args, "ids", None) or getattr(args, "search", None):
        try:
            selected = set(resolve_targets(db, args))
        except ValueError as e:
            # A bad --ids list or search expression is a usage error,
            # not a traceback (every other verb wraps resolve_targets).
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        ids = [i for i in ids if i in selected]
    if not ids:
        # Two different empty states, named apart (the flush-honesty fix):
        # an empty queue and an empty intersection with a target set read
        # as the same old line, which lied about the queue's state.
        if getattr(args, "ids", None) or getattr(args, "search", None):
            print("No targeted book is in the OPF queue: nothing to flush.")
        else:
            print("The OPF queue is empty: nothing to flush.")
        return 0
    chunk = max(1, args.chunk or 50)
    chunks = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
    as_json = getattr(args, "format", None) == "json"
    if not apply:
        plan = {
            "plan": {
                "verb": "flush",
                "ids": ids,
                "chunks": len(chunks),
                "chunk_size": chunk,
            }
        }
        if as_json:
            print(json.dumps(plan, indent=1))
        else:
            print(
                f"flush plan: {len(ids)} dirtied book(s) in {len(chunks)} "
                f"chunk(s) of up to {chunk}: embed_metadata each"
            )
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    if take_backup and (rc := take_backup()):
        return rc
    done = 0
    # embed_metadata takes space-separated ids (its hyphen range is a
    # COUNT of every book between the endpoints, and the dirtied set is
    # generally non-contiguous: "5-900" once wrote 896 books the queue
    # never named); --library is a DIRECTORY.
    library = str(Path(db.db_path).resolve().parent)
    for c in chunks:
        targets = [str(i) for i in c]
        proc = subprocess.run(
            ["calibredb", "embed_metadata", "--library", library, *targets],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            print(
                f"ERROR: embed_metadata {targets} failed: {proc.stderr[:200]}",
                file=sys.stderr,
            )
            return 1
        done += len(c)
    # The dc:date cleanup rides every EPUB embed (the 3.56.0 pass, issue
    # #3): calibredb's EPUB2 writer rewrites only the earliest dc:date and
    # leaves the rest standing, so a flushed multi-dated EPUB2 kept the
    # junk dates forever. The same normalization
    # scripts/reconcile_file_metadata.py runs, from the shared module.
    normalized = 0
    for bid in ids:
        entry = db.get_formats(bid).get("EPUB")
        if entry is None:
            continue
        fpath = Path(entry["path"])
        if not fpath.exists():
            continue
        if normalize_epub_dates(fpath, norm_date(db.get_book(bid)["pubdate"])):
            normalized += 1
        else:
            print(
                f"ERROR: dc:date normalization failed on {fpath}",
                file=sys.stderr,
            )
            return 1
    # Post-apply honesty: re-read the queue so a calibredb run that exited 0
    # without draining it (or dirtied work that raced the run) is reported,
    # not silent.
    remaining = db.get_dirtied_books()
    if as_json:
        print(
            json.dumps(
                {
                    "results": {
                        "flushed": done,
                        "epub_dates_normalized": normalized,
                        "queue_remaining": len(remaining),
                    }
                },
                indent=1,
            )
        )
    else:
        line = f"Flushed {done} book(s): embedded metadata regenerated."
        if normalized:
            line += f" dc:date normalized in {normalized} EPUB file(s)."
        print(line)
        if remaining:
            print(
                f"Queue after flush: {len(remaining)} book(s) still queued "
                "(the queue drains on Calibre's next start too)."
            )
    return 0


def plan_backfill(db, args) -> list[dict]:
    fields = [f for f in (args.fields or "").split(",") if f]
    # comments is deliberately NOT offered: the OPF round-trip carries
    # publisher HTML and overwriting a curated description from it is a
    # curation decision, not a backfill.
    allowed = {"title", "authors", "publisher", "isbn"}
    bad = [f for f in fields if f not in allowed]
    if bad:
        raise ValueError(
            f"unknown field(s): {', '.join(bad)}; available: "
            + ", ".join(sorted(allowed))
        )
    if not fields:
        raise ValueError("run backfill needs --fields (title,authors,...)")
    plans = []
    for bid in resolve_targets(db, args):
        book = db.get_book(bid)
        plans.append(
            {
                "book": bid,
                "action": "backfill",
                "fields": fields,
                "title": book["title"],
                "authors": book["authors"],
                "isbn": book["identifiers"].get("isbn", ""),
            }
        )
    return plans


def run_backfill(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        plans = plan_backfill(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "backfill plan")
    if take_backup and (rc := take_backup()):
        return rc
    if not shutil.which("fetch-ebook-metadata"):
        print("ERROR: fetch-ebook-metadata is not on PATH.", file=sys.stderr)
        return 2
    applied = failed = 0
    import tempfile

    for p in plans:
        # No --allowed-plugin: by default every enabled metadata source
        # runs, which is what a backfill wants ("Google Images" here was
        # the cover plugin and silently allowed no metadata source at all
        # -- caught by the authorized live drill, 3.39.2-era).
        # -o alone: the OPF arrives on stdout (the stray "--opf <file>"
        # this used to append was a positional the binary ignores).
        query = ["fetch-ebook-metadata", "-o"]
        if p["isbn"]:
            query += ["--isbn", p["isbn"]]
        else:
            query += ["--title", p["title"] or ""]
            if p["authors"]:
                query += ["--authors", p["authors"][0]]
        opf = None
        try:
            fd, opf = tempfile.mkstemp(suffix=".opf")
            os.close(fd)
            proc = subprocess.run(
                query,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                p["result"] = "failed"
                p["detail"] = "metadata source returned nothing"
                failed += 1
                continue
            with open(opf, "w", encoding="utf-8") as f:
                f.write(proc.stdout)
            if _apply_backfill(db, p, opf, args):
                applied += 1
            else:
                failed += 1
        except subprocess.TimeoutExpired:
            p["result"] = "failed"
            p["detail"] = "metadata source timed out"
            failed += 1
        finally:
            if opf and os.path.exists(opf):
                os.unlink(opf)
    return _print_results(plans, args, applied, failed)


def _opf_isbn(meta, ns) -> str | None:
    """The fetched record's ISBN: an identifier tagged opf:scheme=ISBN
    wins; otherwise the first identifier whose value is ISBN-shaped
    (to_isbn13 accepts 10- and 13-digit forms). The old cut stored the
    FIRST dc:identifier whatever its scheme, so a Goodreads id could
    become the isbn."""
    fallback = None
    for el in meta.findall("dc:identifier", ns):
        value = (el.text or "").strip()
        if not value:
            continue
        scheme = (el.get(f"{{{ns['o']}}}scheme") or el.get("scheme") or "").strip()
        if scheme.lower() == "isbn":
            return to_isbn13(value)
        if fallback is None and to_isbn13(value) is not None:
            fallback = value
    return to_isbn13(fallback) if fallback else None


def _apply_backfill(db, plan: dict, opf_path: str, args) -> bool:
    """Apply the requested fields from an OPF through cquarry writes.
    False on any failure: a malformed OPF or a refused write is a failed
    row in the report, never a traceback (the shape mirrors run.py's
    _apply_opf)."""
    import xml.etree.ElementTree as ET

    # Real fetch-ebook-metadata OPFs declare the dc namespace and use
    # dc:-prefixed elements (dc:title, dc:creator, ...); the opf prefix
    # only wraps them.
    ns = {
        "o": "http://www.idpf.org/2007/opf",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    try:
        meta = ET.parse(opf_path).getroot().find("o:metadata", ns)
    except OSError, ET.ParseError:
        plan["result"] = "failed"
        plan["detail"] = "OPF unreadable"
        return False
    if meta is None:
        plan["result"] = "failed"
        plan["detail"] = "OPF without metadata"
        return False

    def _text(tag):
        el = meta.find(f"dc:{tag}", ns)
        return el.text.strip() if el is not None and el.text else None

    try:
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                if "title" in plan["fields"] and _text("title"):
                    wdb.update_title(plan["book"], _text("title"))
                if "authors" in plan["fields"]:
                    names = [
                        c.text.strip()
                        for c in meta.findall("dc:creator", ns)
                        if c.text and c.text.strip()
                    ]
                    if names:
                        wdb.set_authors(plan["book"], names)
                if "publisher" in plan["fields"] and _text("publisher"):
                    wdb.set_publisher(plan["book"], _text("publisher"))
                if "isbn" in plan["fields"]:
                    isbn = _opf_isbn(meta, ns)
                    if isbn:
                        wdb.set_identifier(plan["book"], "isbn", isbn)
    except Exception as e:
        plan["result"] = "failed"
        plan["detail"] = str(e)[:200]
        return False
    plan["result"] = "applied"
    return True


def plan_backup_metadata(db, args) -> list[dict]:
    """One sidecar-OPF regeneration per queued book; --all widens to every
    book (upstream marks the whole library dirty first, so a full pass is a
    deliberate flood, not a refresh)."""
    if getattr(args, "all", False):
        return [
            {"book": bid, "action": "backup_opf", "detail": "--all"}
            for bid in sorted(db.all_ids())
        ]
    return [{"book": bid, "action": "backup_opf"} for bid in db.get_dirtied_books()]


def run_backup_metadata(db, args, *, apply: bool, take_backup=None) -> int:
    """`run backup-metadata`: calibredb backup_metadata, the headless form of
    the daemon job cquarry's metadata_dirtied feed exists for. Pairs with
    run flush: flush embeds into the format files, this refreshes the
    sidecar OPFs. Dry run lists the queue; --apply demands the same rails
    as flush (Calibre closed, backup outside the library) because calibre
    owns the queue rows it consumes."""
    try:
        plans = plan_backup_metadata(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not plans and not getattr(args, "all", False):
        print("The OPF queue is empty: nothing to regenerate.")
        return 0
    if not apply:
        return _print_plan(plans, args, "backup-metadata plan")
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    if take_backup and (rc := take_backup()):
        return rc
    library = str(Path(db.db_path).resolve().parent)
    cmd = ["calibredb", "backup_metadata", "--library", library]
    if getattr(args, "all", False):
        cmd.append("--all")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        print(f"ERROR: backup_metadata failed: {proc.stderr[:300]}", file=sys.stderr)
        return 1
    print(
        f"Regenerated sidecar OPFs for {len(plans)} book(s)"
        + (" (--all)." if getattr(args, "all", False) else ".")
    )
    return 0


#: The restore plan's walk is bounded so a huge (or cyclic-through-symlink)
#: tree cannot hang the dry run; a real library's book dirs number in the
#: thousands, not tens of thousands.
_RESTORE_WALK_CAP = 20000


def count_opf_book_dirs(target: Path) -> tuple[int, int]:
    """(book dirs holding a metadata.opf, dirs walked) under target."""
    seen = 0
    found = 0
    for dirpath, _dirnames, filenames in os.walk(target):
        seen += 1
        if seen > _RESTORE_WALK_CAP:
            break
        if "metadata.opf" in {name.lower() for name in filenames}:
            found += 1
    return found, seen


def plan_restore_database(db, args) -> dict:
    """The restore plan and its guards. This verb CREATES a database, so
    destination confusion is refused at the plan door: an explicit --target,
    an existing metadata.db only over --force, and at least one stored OPF
    to rebuild from (an empty rebuild would silently mint an empty
    library)."""
    raw = getattr(args, "target", None)
    if not raw:
        raise ValueError("run restore-database needs --target DIR")
    target = Path(raw).expanduser()
    if not target.is_dir():
        raise ValueError(f"--target is not a directory: {target}")
    db_present = (target / "metadata.db").exists()
    if db_present and not getattr(args, "force", False):
        raise ValueError(
            f"--target already holds a metadata.db ({target}); restore would "
            "replace it (upstream keeps the old one as "
            "metadata_pre_restore.db). Re-run with --force if that is the "
            "point."
        )
    found, _walked = count_opf_book_dirs(target)
    if not found:
        raise ValueError(
            f"no metadata.opf book folders under {target}: there is nothing "
            "to restore from"
        )
    return {"target": str(target), "opf_dirs": found, "db_present": db_present}


def run_restore_database(db, args, *, apply: bool, take_backup=None) -> int:
    """`run restore-database`: calibredb restore_database over --target, the
    rebuild-a-corrupt-database door. The rebuilt library loses saved
    searches, user categories, plugboards, per-book conversion settings,
    and custom recipes; restored rows are only as good as the stored OPFs,
    so the dry run says so before --apply does it. No --backup-dir: the
    verb's --force gate covers the existing-database case, and upstream
    keeps its own metadata_pre_restore.db copy."""
    try:
        plan = plan_restore_database(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    note = (
        f"restore plan: {plan['opf_dirs']} OPF-bearing book folder(s) under "
        f"{plan['target']}; existing metadata.db: "
        f"{'YES (replaced under --force)' if plan['db_present'] else 'none (fresh build)'}"
    )
    if not apply:
        print(note)
        print(
            "Dry run: nothing executed. --apply runs calibredb "
            "restore_database (Calibre closed). WARNING: the rebuild loses "
            "saved searches, user categories, plugboards, per-book "
            "conversion settings, and custom recipes."
        )
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    try:
        proc = subprocess.run(
            [
                "calibredb",
                "restore_database",
                "--library",
                plan["target"],
                "--really-do-it",
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )
    except subprocess.TimeoutExpired:
        print(
            "ERROR: restore_database timed out after 3600s; a "
            "whole-library rebuild can be slow, but a hung calibredb is a "
            "failure, not a wait.",
            file=sys.stderr,
        )
        return 1
    if proc.returncode != 0:
        print(
            f"ERROR: restore_database failed: {proc.stderr[:300]}",
            file=sys.stderr,
        )
        return 1
    print(
        f"Restored a database under {plan['target']} from {plan['opf_dirs']} book folder(s)."
    )
    if proc.stdout.strip():
        print(proc.stdout.strip())
    return 0


def plan_clone(db, args) -> dict:
    """The clone plan and its guards. calibredb clone creates a FRESH-SCHEMA
    EMPTY library (same custom columns, virtual libraries, saved searches,
    and other settings; no books), so the target must not exist or must be
    empty, and must never collide with the source library."""
    raw = getattr(args, "target", None)
    if not raw:
        raise ValueError("run clone needs --target DIR")
    target = Path(raw).expanduser()
    library = Path(db.db_path).resolve().parent
    resolved = target.resolve()
    if resolved == library:
        raise ValueError(f"--target is the current library ({library})")
    if resolved.is_relative_to(library):
        raise ValueError(
            f"--target ({resolved}) sits inside the library ({library}): a "
            "clone nested in its source would be scanned as library content"
        )
    if target.exists() and not target.is_dir():
        raise ValueError(f"--target is not a directory: {target}")
    if target.exists() and any(target.iterdir()):
        raise ValueError(
            f"--target is not empty: {target}. calibredb clone refuses to "
            "clone over content (an empty folder or a new path only)"
        )
    return {"target": str(target), "source": str(library)}


def run_clone(db, args, *, apply: bool, take_backup=None) -> int:
    """`run clone`: calibredb clone, the schema-fresh half of a library copy
    (cquarry's backup_to() is the consistent-with-books half). The clone
    carries the column schema and settings but NO books; the dry run says
    so loudly, because the verb's name promises more than it does. No
    backup: the source database is never opened writable."""
    try:
        plan = plan_clone(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        print(
            f"clone plan: {plan['source']} -> {plan['target']} "
            "(fresh schema: custom columns, virtual libraries, saved "
            "searches, and settings; NO books)"
        )
        print(
            "Dry run: nothing executed. --apply runs calibredb clone "
            "(Calibre closed). For a full duplicate including books, copy "
            "the library folder with filesystem tools."
        )
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["calibredb", "clone", "--library", plan["source"], plan["target"]],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        print(f"ERROR: calibredb clone failed: {proc.stderr[:300]}", file=sys.stderr)
        return 1
    print(f"Cloned the schema of {plan['source']} into {plan['target']} (no books).")
    return 0


def run_fts_index(db, args, *, apply: bool, take_backup=None) -> int:
    """`run fts-index`: calibredb fts_index, the extraction half of the FTS
    story (cquarry contractually never touches the FTS5 tables; Calibre's
    own tokenizer does). The default action re-indexes the sidecar's
    dirtied_formats queue (read through cquarry's get_dirtied_formats(),
    the same feed --fts-status reports); --status reports Calibre's own
    progress view; --enable turns indexing on. No metadata.db backup: the
    default action touches only the sidecar, and --enable's one preference
    row is backed up inside the verb (the trash precedent for verb-owned
    backup rules)."""
    status = bool(getattr(args, "fts_status", False))
    enable = bool(getattr(args, "enable", False))
    if status and enable:
        print(
            "ERROR: run fts-index takes --fts-status or --enable, not both "
            "(no flags means: reindex the dirtied queue).",
            file=sys.stderr,
        )
        return 2
    library = str(Path(db.db_path).resolve().parent)

    if status:
        # The missing-binary refusal sits with the spawns, never ahead of
        # the dry runs (the run_export shape): a runner without calibredb
        # still gets plans and honest no-ops.
        if not shutil.which("calibredb"):
            print("ERROR: calibredb is not on PATH.", file=sys.stderr)
            return 2
        proc = subprocess.run(
            ["calibredb", "fts_index", "--library", library, "status"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        # Upstream exits 2 when indexing is disabled: that is a report, not
        # a usage error, so it reads as an honest answer here.
        if proc.returncode == 2:
            print("FTS indexing is disabled for this library.")
            return 0
        if proc.returncode != 0:
            print(
                f"ERROR: fts_index status failed: {proc.stderr[:300]}",
                file=sys.stderr,
            )
            return 1
        print(proc.stdout.strip() or "FTS indexing is enabled.")
        return 0

    if enable:
        note = "enable plan: turn FTS indexing on for this library (Calibre "
        "builds the sidecar and drains the queue on its next start)"
        if not apply:
            print(note)
            print("Dry run: nothing executed. --apply with --backup-dir runs it.")
            return 0
        if not getattr(args, "backup_dir", None):
            print(
                "ERROR: run fts-index --enable --apply requires --backup-dir "
                "(outside the library): enabling writes the fts_enabled "
                "preference row.",
                file=sys.stderr,
            )
            return 2
        try:
            make_backup(db.db_path, args.backup_dir)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        if not shutil.which("calibredb"):
            print("ERROR: calibredb is not on PATH.", file=sys.stderr)
            return 2
        proc = subprocess.run(
            ["calibredb", "fts_index", "--library", library, "enable"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:
            print(
                f"ERROR: fts_index enable failed: {proc.stderr[:300]}",
                file=sys.stderr,
            )
            return 1
        print("FTS indexing enabled.")
        return 0

    queue = db.get_dirtied_formats()
    if not queue:
        print("The extraction queue is empty: nothing to index.")
        return 0
    items = [f"{book}:{fmt}" for book, fmt in queue]
    if not apply:
        print(
            f"fts-index plan: {len(queue)} queued (book:format) pair(s) "
            "for Calibre's extractor:"
        )
        for item in items[:30]:
            print(f"  {item}")
        if len(items) > 30:
            print(f"  ... and {len(items) - 30} more")
        print(
            "Dry run: nothing executed. --apply runs calibredb fts_index "
            "reindex over exactly these pairs (Calibre closed)."
        )
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["calibredb", "fts_index", "--library", library, "reindex", *items],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode != 0:
        # The usual shape: "Full text indexing is not enabled on this
        # library" -- calibre's own message rides out.
        print(
            f"ERROR: fts_index reindex failed: {proc.stderr.strip()[:300]}",
            file=sys.stderr,
        )
        return 1
    print(f"Queued {len(items)} (book:format) pair(s) for extraction.")
    return 0


#: calibredb catalog picks its plugin from the output file's extension and
#: SILENTLY falls back to the EPUB plugin on anything it does not recognize,
#: so each verb enforces its own extension: a .bib request must never
#: quietly produce an EPUB.
_CATALOG_EXTS = {"catalog-epub": ".epub", "catalog-bibtex": ".bib"}


# --- headless Calibre-automation verbs (no library; dispatch_run routes
# them before any database resolution) -------------------------------


_CUSTOMIZE_ACTIONS = (
    "list-plugins",
    "add-plugin",
    "remove-plugin",
    "enable-plugin",
    "disable-plugin",
)


def run_customize(args) -> int:
    """`run customize`: calibre-customize, the headless plugin surface (the
    motivating case: installing the Bindery Repair plugin zip without the
    GUI). --list-plugins reads; the four mutators are dry-run by default
    and take the closed-Calibre guard at --apply, because a live GUI keeps
    its plugin state in memory and writes its config on exit."""
    chosen = [
        action
        for action, value in (
            ("list-plugins", getattr(args, "list_plugins", False)),
            ("add-plugin", getattr(args, "add_plugin", None)),
            ("remove-plugin", getattr(args, "remove_plugin", None)),
            ("enable-plugin", getattr(args, "enable_plugin", None)),
            ("disable-plugin", getattr(args, "disable_plugin", None)),
        )
        if value
    ]
    if not chosen:
        print(
            "ERROR: run customize needs one of --list-plugins, --add-plugin "
            "ZIP, --remove-plugin NAME, --enable-plugin NAME, "
            "--disable-plugin NAME.",
            file=sys.stderr,
        )
        return 2
    if len(chosen) > 1:
        print(
            f"ERROR: run customize takes exactly one action, got: "
            f"{', '.join('--' + c for c in chosen)}.",
            file=sys.stderr,
        )
        return 2
    action = chosen[0]
    apply = bool(getattr(args, "apply", False))

    if action == "list-plugins":
        if not shutil.which("calibre-customize"):
            print("ERROR: calibre-customize is not on PATH.", file=sys.stderr)
            return 2
        proc = subprocess.run(
            ["calibre-customize", "-l"], capture_output=True, text=True, timeout=300
        )
        if proc.returncode != 0:
            print(
                f"ERROR: calibre-customize -l failed: {proc.stderr[:300]}",
                file=sys.stderr,
            )
            return 1
        print(proc.stdout.strip())
        return 0

    value = {
        "add-plugin": getattr(args, "add_plugin", None),
        "remove-plugin": getattr(args, "remove_plugin", None),
        "enable-plugin": getattr(args, "enable_plugin", None),
        "disable-plugin": getattr(args, "disable_plugin", None),
    }[action]
    flag = {
        "add-plugin": "-a",
        "remove-plugin": "-r",
        "enable-plugin": "--enable-plugin",
        "disable-plugin": "--disable-plugin",
    }[action]
    if action == "add-plugin":
        zip_path = Path(value).expanduser()
        if not zip_path.is_file():
            print(f"ERROR: --add-plugin zip not found: {zip_path}", file=sys.stderr)
            return 2
    verb = {
        "add-plugin": "install",
        "remove-plugin": "remove",
        "enable-plugin": "enable",
        "disable-plugin": "disable",
    }[action]
    if not apply:
        print(f"customize plan: {verb} {value} (calibre-customize {flag})")
        print(
            "Dry run: nothing executed. --apply runs it (Calibre closed: a "
            "live GUI keeps its plugin state in memory and writes its "
            "config on exit)."
        )
        return 0
    if _calibre_running():
        # Lock-class refusal (exit 1), matching the other write doors.
        print("ERROR: Calibre is running; close it before --apply.", file=sys.stderr)
        return 1
    if not shutil.which("calibre-customize"):
        print("ERROR: calibre-customize is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["calibre-customize", flag, str(value)],
        capture_output=True,
        text=True,
        timeout=600,
    )
    if proc.returncode != 0:
        print(
            f"ERROR: calibre-customize {flag} failed: {proc.stderr[:300]}",
            file=sys.stderr,
        )
        return 1
    past = {"install": "installed"}.get(verb, f"{verb}d")
    print(proc.stdout.strip() or f"{past.capitalize()}: {value}")
    return 0


_DEBUG_TOOLS = (
    "explode",
    "implode",
    "diff",
    "kepubify",
    "un-kepubify",
    "inspect-mobi",
)


def run_debug_tools(args) -> int:
    """`run debug-tools`: the curated calibre-debug subset (explode, implode,
    diff, kepubify, un-kepubify, inspect-mobi). The -e/--exec-file surface
    stays out on purpose: arbitrary code execution is not a verb. The file
    mutators are dry-run by default with the closed-Calibre guard at
    --apply; explode/kepubify/un-kepubify create new files (originals
    untouched) and implode's source dir is the content of record, so no
    --backup-dir is demanded (the trash precedent for verb-owned backup
    rules). diff and inspect-mobi are reads and run immediately."""
    actions = {
        "explode": getattr(args, "explode", None),
        "implode": getattr(args, "implode", None),
        "diff": getattr(args, "diff", None),
        "kepubify": getattr(args, "kepubify", None),
        "un-kepubify": getattr(args, "un_kepubify", None),
        "inspect-mobi": getattr(args, "inspect_mobi", None),
    }
    chosen = [name for name, value in actions.items() if value]
    if not chosen:
        print(
            "ERROR: run debug-tools needs exactly one of --explode FILE DIR, "
            "--implode DIR FILE, --diff OLD NEW, --kepubify FILE, "
            "--un-kepubify FILE, --inspect-mobi FILE.",
            file=sys.stderr,
        )
        return 2
    if len(chosen) > 1:
        print(
            "ERROR: run debug-tools takes exactly one action, got: "
            f"{', '.join('--' + c for c in chosen)}.",
            file=sys.stderr,
        )
        return 2
    action = chosen[0]
    targets = list(actions[action])
    apply = bool(getattr(args, "apply", False))
    mutating = action in ("explode", "implode", "kepubify", "un-kepubify")

    # Existence checks at the plan door: a typo'd INPUT path is a usage
    # error however the verb is run. explode's DIR and implode's FILE are
    # OUTPUTS upstream creates on demand (calibre's tweak.explode runs
    # os.makedirs; implode writes the new file), so only the input side of
    # each is checked: the canonical first run ("--explode book.epub
    # ./parts") must not refuse on the folder it is about to create.
    checked = targets[:1] if action in ("explode", "implode") else targets
    for path in checked:
        if not Path(path).exists():
            print(f"ERROR: {action}: no such path: {path}", file=sys.stderr)
            return 2
    cmd = ["calibre-debug"]
    if action == "explode":
        cmd += ["--explode-book", targets[0], targets[1]]
    elif action == "implode":
        cmd += ["--implode-book", targets[0], targets[1]]
    elif action == "diff":
        cmd += ["--diff", targets[0], targets[1]]
    elif action == "kepubify":
        cmd += ["--kepubify", *targets]
    elif action == "un-kepubify":
        cmd += ["--un-kepubify", *targets]
    else:
        cmd += ["--inspect-mobi", *targets]

    if mutating and not apply:
        print(f"debug-tools plan: {' '.join(cmd)}")
        print(
            "Dry run: nothing executed. --apply runs it (Calibre closed "
            "when the targets live in the library)."
        )
        return 0
    if mutating and _calibre_running():
        print("ERROR: Calibre is running; close it before --apply.", file=sys.stderr)
        return 1
    if not shutil.which("calibre-debug"):
        print("ERROR: calibre-debug is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0:
        print(
            f"ERROR: calibre-debug {action} failed: {proc.stderr[:300]}",
            file=sys.stderr,
        )
        return 1
    if proc.stdout.strip():
        print(proc.stdout.strip())
    else:
        print(f"calibre-debug {action} completed.")
    return 0


#: The ebook-device USBMS subset: (flag, write?) — reads execute
#: immediately, writes are dry-run by default. MTP and the wireless
#: Calibre-Companion stack stay out of parity by roadmap decision.
_DEVICE_ACTIONS = (
    ("device_ls", False),
    ("device_df", False),
    ("device_books", False),
    ("device_cat", False),
    ("device_mkdir", True),
    ("device_cp", True),
    ("device_rm", True),
    ("device_touch", True),
)


def run_device(args) -> int:
    """`run device`: the ebook-device USBMS subset (ls/df/books/cat/mkdir/
    cp/rm/touch). No library, no database, and therefore no closed-Calibre
    guard: the guard protects metadata.db and this verb never opens it.
    Reads execute immediately; the writes are dry-run by default. MTP and
    the wireless Calibre-Companion stack stay excluded from parity."""
    # df/books are store_true reads (False when absent); the rest carry a
    # positional list (None when absent). Both shapes detect with the same
    # "present" rule.
    chosen = []
    for dest, writes in _DEVICE_ACTIONS:
        value = getattr(args, dest, None)
        if value is not None and value is not False:
            chosen.append((dest, writes))
    if not chosen:
        print(
            "ERROR: run device needs exactly one of --device-ls PATH, "
            "--device-df, --device-books, --device-cat PATH, --device-mkdir "
            "PATH, --device-cp SRC DST, --device-rm PATH, --device-touch PATH.",
            file=sys.stderr,
        )
        return 2
    if len(chosen) > 1:
        print(
            f"ERROR: run device takes exactly one action, got {len(chosen)}.",
            file=sys.stderr,
        )
        return 2
    dest, writes = chosen[0]
    value = getattr(args, dest)
    targets = list(value) if isinstance(value, (list, tuple)) else []
    command = dest.removeprefix("device_")
    apply = bool(getattr(args, "apply", False))

    if writes and not apply:
        print(f"device plan: ebook-device {command} {' '.join(targets)}")
        print(
            "Dry run: nothing executed. --apply runs it against the connected device."
        )
        return 0
    if not shutil.which("ebook-device"):
        print("ERROR: ebook-device is not on PATH.", file=sys.stderr)
        return 2
    proc = subprocess.run(
        ["ebook-device", command, *targets],
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if proc.returncode != 0:
        # Upstream's own "Unable to find a connected ebook reader." rides out.
        print(
            f"ERROR: ebook-device {command} failed: "
            f"{(proc.stderr or proc.stdout).strip()[:300]}",
            file=sys.stderr,
        )
        return 1
    if proc.stdout.strip():
        print(proc.stdout.strip())
    elif proc.stderr.strip():
        # Upstream's no-device answer ("Unable to find a connected ebook
        # reader.") rides stderr with rc 0: surface it, never paper over it
        # with a bare "completed." (live-drill finding, calibre 9.15).
        print(proc.stderr.strip())
    else:
        print(f"ebook-device {command} completed.")
    return 0


def dispatch_headless(args) -> int:
    """The no-library run verbs: routed by dispatch_run before any database
    resolution, because none of them opens one. customize, debug-tools,
    device, and news."""
    if args.phase == "customize":
        return run_customize(args)
    if args.phase == "debug-tools":
        return run_debug_tools(args)
    if args.phase == "news":
        from cquarry_cli.news import run_news

        return run_news(args)
    return run_device(args)


def run_catalog(db, args, *, apply: bool, take_backup=None) -> int:
    """`run catalog-epub` / `run catalog-bibtex`: calibredb catalog through
    the EPUB_MOBI and BIBTEX catalog plugins (the CSV/XML half of catalog
    output is already native, `--catalog --format csv`). Targets resolve
    read-only first (no selection means the whole library, upstream's
    default). The verb touches nothing in the library: no backup, ever."""
    phase = args.phase
    ext = _CATALOG_EXTS[phase]
    dest_raw = getattr(args, "dest", None)
    if not dest_raw:
        print(f"ERROR: run {phase} needs --dest FILE (a {ext} path).", file=sys.stderr)
        return 2
    dest = Path(dest_raw).expanduser()
    if dest.suffix.lower() != ext:
        print(
            f"ERROR: run {phase} writes a {ext} file; got {dest.name}. calibredb "
            "catalog picks its plugin from the extension and silently falls "
            "back to EPUB on anything else, so the extension is enforced.",
            file=sys.stderr,
        )
        return 2
    try:
        ids = resolve_targets(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not ids:
        ids = sorted(db.all_ids())
    if not ids:
        print("The library has no books: nothing to catalog.")
        return 0
    library = str(Path(db.db_path).resolve().parent)
    if not apply:
        print(
            f"{phase} plan: {len(ids)} book(s) -> {dest} (calibredb catalog "
            "through the plugin; the library is only read)"
        )
        print("Dry run: nothing executed. --apply runs calibredb catalog.")
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    # calibredb catalog's hand-written parser enforces a shape no other
    # command uses (verified live against calibre 9.15): the output
    # filename must be the FIRST token after the subcommand and every
    # option -- the library's own --library-path included -- comes after
    # it. Any option before the filename dies with "Must specify the
    # catalog output filename before any options", and the placement every
    # other verb uses (--library after the subcommand) dies with it.
    proc = subprocess.run(
        [
            "calibredb",
            "catalog",
            str(dest),
            "--library-path",
            library,
            "--ids",
            ",".join(str(i) for i in ids),
        ],
        capture_output=True,
        text=True,
        timeout=3600,
    )
    if proc.returncode != 0:
        print(f"ERROR: calibredb catalog failed: {proc.stderr[:300]}", file=sys.stderr)
        return 1
    print(f"Wrote {dest} ({len(ids)} book(s)).")
    return 0


def _print_plan(plans: list[dict], args, title: str) -> int:
    if getattr(args, "format", None) == "json":
        print(json.dumps({"plan": plans}, indent=2, ensure_ascii=False))
        return 0
    print(f"{title} ({len(plans)}):")
    for p in plans:
        detail = p.get("detail") or p.get("source") or ""
        if p.get("moves"):
            detail += " moves: " + ", ".join(m["fmt"] for m in p["moves"])
        line = f"  #{p['book']} {p['action']}"
        if detail:
            line += f" {detail}"
        print(line)
    print(
        "\nDry run: nothing executed. Pass --apply to run it "
        "(Calibre closed, --backup-dir outside the library)."
    )
    return 0


def _print_results(plans: list[dict], args, applied: int, failed: int) -> int:
    if getattr(args, "format", None) == "json":
        print(
            json.dumps(
                {"results": plans, "applied": applied, "failed": failed},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(f"Applied {applied}, failed/skipped {failed}.")
        for p in plans:
            if p.get("result") not in ("applied", "already-so"):
                print(f"  #{p['book']}: {p.get('result')} {p.get('detail', '')}")
    return 1 if failed else 0


def run_trash(db_path: str, args, *, apply: bool) -> int:
    """`run trash`: the .caltrash lifecycle over cquarry 1.20's verbs.

    Dry run (the default) lists every entry with its age and file
    count; `--empty --apply` deletes the whole trash, `--expire DAYS
    --apply` deletes entries older than DAYS (no flag: upstream's
    14-day rule). The apply half opens metadata.db writable to reach
    the upstream verbs, which is why the closed-Calibre guard applies
    in dispatch -- but no backup is required, because the verbs are
    pure filesystem and the database itself never changes."""
    from cquarry_cli.modes.trash import collect_trash_entries

    empty = bool(getattr(args, "empty", False))
    raw_days = getattr(args, "expire", None)
    if empty and raw_days is not None:
        print(
            "ERROR: run trash takes --empty or --expire DAYS, not both.",
            file=sys.stderr,
        )
        return 2
    days: float | None = None
    if raw_days is not None:
        try:
            days = float(raw_days)
        except ValueError:
            print(
                f"ERROR: --expire wants a number of days, got {raw_days!r}.",
                file=sys.stderr,
            )
            return 2
        if days < 0:
            print("ERROR: --expire DAYS must not be negative.", file=sys.stderr)
            return 2

    mode = "empty" if empty else ("expire" if days is not None else "list")
    library_dir = os.path.dirname(os.path.abspath(db_path))
    entries = collect_trash_entries(library_dir)
    if mode == "empty":
        planned = entries
    elif mode == "expire":
        # Upstream's mtime rule, in days: an entry goes when its age is
        # DAYS or more (0 or less expires everything, honored for parity).
        planned = [e for e in entries if days <= 0 or e["age_days"] >= days]
    else:
        planned = []

    executing = apply and mode != "list"
    if not executing:
        if not entries:
            print(f"No trash under {library_dir} (nothing merged away yet).")
            return 0
        planned_ids = {id(e) for e in planned}
        print(
            f"Trash: {len(entries)} entries under {library_dir}"
            + ("." if not planned else f"; {len(planned)} would be deleted:")
        )
        for e in entries:
            marker = " (would delete)" if id(e) in planned_ids else ""
            print(
                f"  [{e['category']}] book {e['book_id']}: "
                f"{len(e['files'])} file(s), {e['age_days']:.1f} days old"
                f"{marker}"
            )
        if not apply and mode != "list":
            print(
                f"Dry run: re-run with --apply to delete these {len(planned)} entries."
            )
        elif mode == "list":
            print(
                "Re-run with --empty or --expire DAYS (and --apply) to "
                "delete; the dry run is the listing."
            )
        return 0

    from cquarry.write import WritableCalibreDB

    with WritableCalibreDB(db_path) as wdb:
        if mode == "empty":
            removed = wdb.empty_trash()
        else:
            removed = wdb.expire_trash(days * 86400)
    if getattr(args, "format", None) == "json":
        print(
            json.dumps(
                {
                    "plan": {
                        "mode": mode,
                        "entries": len(entries),
                        "would_remove": len(planned),
                    },
                    "results": {"removed": removed},
                },
                indent=1,
            )
        )
    else:
        print(f"Removed {removed} trash entry(ies).")
    return 0


def dispatch_integrate(args) -> int:
    """The Phase 19 C verb dispatch: usage guards first (exit 2 before
    anything opens), then the shared dry-run/apply lifecycle with the
    house guards (Calibre closed, timestamped backup outside the
    library) for every verb that mutates state."""
    from cquarry_cli.cli import find_db
    from cquarry.db import CalibreDB

    apply = bool(getattr(args, "apply", False))
    verbs_need_targets = {"convert", "polish", "cover", "export", "backfill"}
    # backfill mutates metadata.db at --apply too (matrix-C finding:
    # it was the one write verb escaping the backup requirement).
    needs_backup = {
        "convert",
        "polish",
        "cover",
        "merge",
        "flush",
        "backfill",
        "backup-metadata",
    }

    # Usage guards BEFORE find_db (dispatch_run's rule): a missing --ids
    # is a usage error (exit 2) however resolvable the library is, and a
    # missing library must not turn it into an environment exit 1.
    if args.phase in verbs_need_targets and not (
        getattr(args, "search", None) or getattr(args, "ids", None)
    ):
        print(f"ERROR: run {args.phase} needs --search EXPR or --ids.", file=sys.stderr)
        return 2
    if args.phase == "merge" and not (args.keeper and args.duplicate):
        print("ERROR: run merge needs --keeper ID and --duplicate ID.", file=sys.stderr)
        return 2
    if args.phase == "clone" and not getattr(args, "target", None):
        print(
            "ERROR: run clone needs --target DIR (the empty folder that "
            "receives the fresh schema).",
            file=sys.stderr,
        )
        return 2
    if args.phase == "restore-database" and not getattr(args, "target", None):
        print(
            "ERROR: run restore-database needs --target DIR (this verb "
            "creates a database; the destination is never guessed).",
            file=sys.stderr,
        )
        return 2

    db_path = find_db(getattr(args, "db", None))
    if apply:
        if _calibre_running():
            # Lock-class refusal (exit 1), the shape setwrite and the
            # phase-2/3 doors follow: usage problems exit 2, an open
            # Calibre is an environment condition, not a usage problem.
            print(
                "ERROR: Calibre is running; close it before --apply.", file=sys.stderr
            )
            return 1
        if args.phase in needs_backup and not getattr(args, "backup_dir", None):
            print(
                f"ERROR: run {args.phase} --apply requires --backup-dir "
                "(outside the library).",
                file=sys.stderr,
            )
            return 2

    def take_backup() -> int:
        """Called by the verbs AFTER plan validation, so an invocation
        that aborts on usage never writes a backup nobody needs."""
        try:
            make_backup(db_path, args.backup_dir)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        return 0

    if args.phase == "trash":
        return run_trash(db_path, args, apply=apply)

    with CalibreDB(db_path) as db:
        if args.phase == "merge":
            return run_merge(db, args, apply=apply, take_backup=take_backup)
        verb = {
            "convert": run_convert,
            "polish": run_polish,
            "cover": run_cover,
            "export": run_export,
            "flush": run_flush,
            "backfill": run_backfill,
            "backup-metadata": run_backup_metadata,
            "restore-database": run_restore_database,
            "clone": run_clone,
            "fts-index": run_fts_index,
            "catalog-epub": run_catalog,
            "catalog-bibtex": run_catalog,
        }[args.phase]
        backup = take_backup if (apply and args.phase in needs_backup) else None
        return verb(db, args, apply=apply, take_backup=backup)
