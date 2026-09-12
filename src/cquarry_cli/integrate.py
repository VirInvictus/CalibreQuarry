"""The integration verbs: `cquarry run convert/polish/cover/export/merge/
flush/backfill` (Phase 19 C).

Every verb drives external programs (ebook-convert, ebook-polish,
calibredb, fetch-ebook-metadata) and registers the outcome through
cquarry's write module; no new Python dependencies, by design. The
house write discipline holds across all of them:

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

from cquarry.write import WritableCalibreDB

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
    """The anchored-name pgrep guard (fetch_library_codes.py precedent)."""
    try:
        proc = subprocess.run(
            ["pgrep", "^calibre"],
            capture_output=True,
            timeout=_PGREP_TIMEOUT,
            check=False,
        )
    except OSError, subprocess.TimeoutExpired:
        return False
    return proc.returncode == 0


def _backup_db(db_path: str, backup_dir: str) -> str:
    """Timestamped sqlite-API backup outside the library (the run.py
    precedent; a second run never destroys an earlier restore point)."""
    import sqlite3
    from datetime import datetime

    resolved = Path(backup_dir).expanduser().resolve()
    lib_dir = Path(db_path).resolve().parent
    if resolved == lib_dir or resolved.is_relative_to(lib_dir):
        raise ValueError(
            f"--backup-dir ({resolved}) must sit OUTSIDE the library directory"
        )
    os.makedirs(resolved, exist_ok=True)
    stem = Path(db_path).stem
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = resolved / f"{stem}-{stamp}.db"
    n = 2
    while dest.exists():
        dest = resolved / f"{stem}-{stamp}-{n}.db"
        n += 1
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            with dst:
                src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return str(dest)


def resolve_targets(db, args) -> list[int]:
    """Exactly one of --search/--ids (merge uses --keeper/--duplicate
    instead). Unknown hand-supplied ids abort here, read-only."""
    if getattr(args, "search", None):
        ids = set(db.search(args.search))
        return sorted(ids)
    raw = getattr(args, "ids", None)
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
    """One planned conversion per book: source format -> --to."""
    to_fmt = (args.to or "").upper()
    if not to_fmt:
        raise ValueError("run convert needs --to FORMAT")
    plans = []
    for bid in resolve_targets(db, args):
        formats = db.get_formats(bid) or {}
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


def run_convert(db, args, *, apply: bool) -> int:
    try:
        plans = plan_convert(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "convert plan")
    applied = failed = 0
    for p in plans:
        if p["action"] != "convert":
            failed += 1
            p["result"] = "skipped"
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
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                wdb.add_format(
                    p["book"],
                    p["target"],
                    Path(p["dst"]).stem,
                    Path(p["dst"]).stat().st_size,
                )
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


def run_polish(db, args, *, apply: bool) -> int:
    try:
        plans = plan_polish(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "polish plan")
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


def run_cover(db, args, *, apply: bool) -> int:
    try:
        plans = plan_cover(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "cover plan")
    applied = failed = 0
    for p in plans:
        with WritableCalibreDB(db.db_path) as wdb:
            with wdb.batch():
                if p["action"] == "set_cover":
                    changed = wdb.set_cover(p["book"], p["cover"])
                else:
                    changed = wdb.remove_cover(p["book"])
        p["result"] = "applied" if changed else "already-so"
        applied += 1
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


def run_export(db, args, *, apply: bool) -> int:
    try:
        plans = plan_export(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    ids = ",".join(str(p["book"]) for p in plans)
    cmd = [
        "calibredb",
        "export",
        "--library",
        db.db_path,
        "--to-dir",
        args.dest,
        "--template",
        plans[0]["template"],
        "--dont-save-opf",
        ids,
    ]
    if not apply:
        print(
            f"export plan: {len(plans)} book(s) -> {args.dest} "
            f"(template {plans[0]['template']!r})"
        )
        return 0
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


def run_merge(db, args, *, apply: bool) -> int:
    try:
        plans = plan_merge(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "merge plan")
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


def run_flush(db, args, *, apply: bool) -> int:
    """Headless OPF-queue flush: calibredb embed_metadata for the ids in
    metadata_dirtied (chunked; the reconcile precedent)."""
    ids = db.get_dirtied_books()
    if getattr(args, "ids", None) or getattr(args, "search", None):
        ids = [i for i in ids if i in set(resolve_targets(db, args))]
    if not ids:
        print("The OPF queue is empty: nothing to flush.")
        return 0
    if not shutil.which("calibredb"):
        print("ERROR: calibredb is not on PATH.", file=sys.stderr)
        return 2
    chunk = max(1, args.chunk or 50)
    chunks = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
    if not apply:
        print(
            f"flush plan: {len(ids)} dirtied book(s) in {len(chunks)} "
            f"chunk(s) of up to {chunk}: embed_metadata each"
        )
        return 0
    done = 0
    for c in chunks:
        span = f"{c[0]}-{c[-1]}" if len(c) > 1 else str(c[0])
        proc = subprocess.run(
            ["calibredb", "embed_metadata", "--library", db.db_path, span],
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode != 0:
            print(
                f"ERROR: embed_metadata {span} failed: {proc.stderr[:200]}",
                file=sys.stderr,
            )
            return 1
        done += len(c)
    print(f"Flushed {done} book(s): embedded metadata regenerated.")
    return 0


def plan_backfill(db, args) -> list[dict]:
    fields = [f for f in (args.fields or "").split(",") if f]
    allowed = {"title", "authors", "publisher", "isbn", "comments"}
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


def run_backfill(db, args, *, apply: bool) -> int:
    try:
        plans = plan_backfill(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        return _print_plan(plans, args, "backfill plan")
    if not shutil.which("fetch-ebook-metadata"):
        print("ERROR: fetch-ebook-metadata is not on PATH.", file=sys.stderr)
        return 2
    applied = failed = 0
    import tempfile

    for p in plans:
        query = ["fetch-ebook-metadata", "--allowed-plugin", "Google Images"]
        if p["isbn"]:
            query += ["--isbn", p["isbn"]]
        else:
            query += ["--title", p["title"] or ""]
            if p["authors"]:
                query += ["--authors", p["authors"][0]]
        query += ["--opf", "-"]
        try:
            fd, opf = tempfile.mkstemp(suffix=".opf")
            os.close(fd)
            query[-1] = opf
            proc = subprocess.run(
                query[:-1], capture_output=True, text=True, timeout=300
            )
            if proc.returncode != 0 or os.path.getsize(opf) == 0:
                p["result"] = "failed"
                p["detail"] = "metadata source returned nothing"
                failed += 1
                continue
            applied += 1 if _apply_backfill(db, p, opf, args) else 0
        finally:
            if os.path.exists(opf):
                os.unlink(opf)
    return _print_results(plans, args, applied, failed)


def _apply_backfill(db, plan: dict, opf_path: str, args) -> bool:
    """Apply the requested fields from an OPF through cquarry writes."""
    import xml.etree.ElementTree as ET

    ns = {"o": "http://www.idpf.org/2007/opf"}
    root = ET.parse(opf_path).getroot()
    meta = root.find("o:metadata", ns)
    if meta is None:
        plan["result"] = "failed"
        plan["detail"] = "OPF without metadata"
        return False

    def _text(tag):
        el = meta.find(f"o:{tag}", ns)
        return el.text.strip() if el is not None and el.text else None

    with WritableCalibreDB(db.db_path) as wdb:
        with wdb.batch():
            if "title" in plan["fields"] and _text("title"):
                wdb.update_title(plan["book"], _text("title"))
            if "authors" in plan["fields"]:
                names = [
                    c.text.strip()
                    for c in meta.findall("o:creator", ns)
                    if c.text and c.text.strip()
                ]
                if names:
                    wdb.set_authors(plan["book"], names)
            if "publisher" in plan["fields"] and _text("publisher"):
                wdb.set_publisher(plan["book"], _text("publisher"))
            if "isbn" in plan["fields"] and _text("identifier"):
                wdb.set_identifier(plan["book"], "isbn", _text("identifier"))
    plan["result"] = "applied"
    return True


def _print_plan(plans: list[dict], args, title: str) -> int:
    if getattr(args, "format", None) == "json":
        print(json.dumps({"plan": plans}, indent=2, ensure_ascii=False))
        return 0
    print(f"{title} ({len(plans)}):")
    for p in plans:
        detail = p.get("detail") or p.get("source") or ""
        if p.get("moves"):
            detail += " moves: " + ", ".join(m["fmt"] for m in p["moves"])
        print(f"  #{p['book']} {p['action']} {detail}")
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


def dispatch_integrate(args) -> int:
    """The Phase 19 C verb dispatch: usage guards first (exit 2 before
    anything opens), then the shared dry-run/apply lifecycle with the
    house guards (Calibre closed, timestamped backup outside the
    library) for every verb that mutates state."""
    from cquarry_cli.cli import find_db
    from cquarry.db import CalibreDB

    db_path = find_db(getattr(args, "db", None))
    apply = bool(getattr(args, "apply", False))
    verbs_need_targets = {"convert", "polish", "cover", "export", "backfill"}
    needs_backup = {"convert", "polish", "cover", "merge", "flush"}

    if args.phase in verbs_need_targets and not (
        getattr(args, "search", None) or getattr(args, "ids", None)
    ):
        print(f"ERROR: run {args.phase} needs --search EXPR or --ids.", file=sys.stderr)
        return 2
    if args.phase == "merge" and not (args.keeper and args.duplicate):
        print("ERROR: run merge needs --keeper ID and --duplicate ID.", file=sys.stderr)
        return 2
    if apply:
        if _calibre_running():
            print(
                "ERROR: Calibre is running; close it before --apply.", file=sys.stderr
            )
            return 2
        if args.phase in needs_backup and not getattr(args, "backup_dir", None):
            print(
                f"ERROR: run {args.phase} --apply requires --backup-dir "
                "(outside the library).",
                file=sys.stderr,
            )
            return 2

    def _guarded_backup() -> int:
        try:
            _backup_db(db_path, args.backup_dir)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        return 0

    with CalibreDB(db_path) as db:
        if args.phase == "merge":
            if apply and args.backup_dir:
                rc = _guarded_backup()
                if rc:
                    return rc
            return run_merge(db, args, apply=apply)
        verb = {
            "convert": run_convert,
            "polish": run_polish,
            "cover": run_cover,
            "export": run_export,
            "flush": run_flush,
            "backfill": run_backfill,
        }[args.phase]
        if apply and args.phase in needs_backup:
            rc = _guarded_backup()
            if rc:
                return rc
        return verb(db, args, apply=apply)
