"""`run news`: fetch news editions through Calibre's builtin recipes
(``ebook-convert "<Recipe Title>.recipe" out.epub``, the seam upstream's
own help documents). The Phase 20 recipe-fetch parity verb: needs no
library, opens no database, writes nothing but the EPUBs it fetches.

The design decisions (recorded in spec.md's run news row):

- **Addressing is recipe TITLE**, the exact strings ``--list-recipes``
  prints, because that is the only CLI-documented addressing. Upstream
  keys builtin recipes by id internally (``builtin:<slug>``, resolved
  through the ``CALIBRE_RECIPE_URN`` environment variable calibre's GUI
  sets), but no public seam enumerates those ids, so a title-keyed verb
  would silently misfetch where titles collide. Calibre 9.15 ships 13
  colliding titles (Deutsche Welle x7, The Economist x2, The Wall
  Street Journal x2, ...): an explicit ``--recipe`` naming one is a
  usage refusal naming the collision (the GUI scheduler or a custom
  ``.recipe`` file is the escape hatch for those); a curated default
  that went stale or ambiguous across a calibre upgrade is skipped
  with a warning, never fatal.
- **The default batch is a curated subset, not --all.** 1,099 recipes
  would be many hours of sequential fetches against sites that
  actively block scripted fetchers. DEFAULT_RECIPES is the free,
  subscription-free, automation-tolerant subset; ``--recipe`` overrides
  and ``--all`` deliberately floods.
- **Failure isolation**: one dead recipe is one report row (failed,
  timeout), never a dead batch. A failed or timed-out fetch deletes its
  partial output so the next resume cannot skip on a half-written file
  standing in for a real edition (the catalog sweeps' stale-file rule).
- **Resume/skip**: the output filename is derived from the recipe title
  under one directory; an existing non-empty file means ``already``
  unless ``--force``. The default destination is date-scoped
  (``./news/<YYYY-MM-DD>/``) so yesterday's editions never shadow
  today's.
- **Anti-bot reality** (the verb's help page says this too): news
  sites treat scripted fetchers as bots; upstream fights that war for
  us (recipes carry browser user-agents and calibre refreshes recipe
  sources from its own server at fetch time), but some recipes will
  fail anyway and that is expected. Fetches run strictly sequentially:
  no hammering. Failures are exit 1, reported per recipe, and a later
  re-run retries only what is still missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

#: The curated default batch: free, subscription-free, automation-tolerant
#: sources. A recipe the installed calibre no longer lists (renames happen
#: between upstream releases) is skipped with a warning; this tuple is
#: convenience, never a contract. Paywalled sources stay out: a recipe
#: that cannot legally fetch full content fails noisily and teaches
#: nothing.
DEFAULT_RECIPES: tuple[str, ...] = (
    "Associated Press",
    "BBC News",
    "CNN",
    "Al Jazeera in English",
    "The Guardian and The Observer",
    "National Public Radio",
    "The Verge",
    "Ars Technica",
    "NASA",
)

#: The per-recipe ceiling. The live drill (calibre 9.15, 2026-10-07) fetched
#: a full BBC News edition at 128 MB in ~4.5 minutes; image-heavy editions
#: are the norm, so the default is generous. --timeout overrides.
_DEFAULT_TIMEOUT = 1200

_ENUM_TIMEOUT = 120


def list_recipe_titles() -> list[str]:
    """``ebook-convert --list-recipes`` as the exact title multiset.

    Upstream prints a header, one tab-indented line per recipe (colliding
    titles once each, so a duplicated title means ambiguity), and a
    trailing count line; only the tab-indented lines are titles. The raw
    post-tab bytes are kept because recipe resolution compares the input
    string exactly (a title with leading/trailing spaces must survive)."""
    exe = shutil.which("ebook-convert")
    if exe is None:
        # The enumeration IS a spawn, so the missing-binary refusal sits
        # here even for the dry run (the fts-index --fts-status precedent:
        # refuse at the spawn when the action is the spawn).
        raise RuntimeError("ebook-convert is not on PATH.")
    proc = subprocess.run(
        [exe, "--list-recipes"],
        capture_output=True,
        text=True,
        timeout=_ENUM_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"ebook-convert --list-recipes failed: {proc.stderr.strip()[:300]}"
        )
    return [line[1:] for line in proc.stdout.splitlines() if line.startswith("\t")]


def _title_for(titles: list[str], wanted: str) -> str | None:
    """The exact enumerated title a --recipe input names: exact match
    first, then a stripped fallback (trailing spaces in titles like
    " fluter. " are invisible when --list prints them). None when the
    title is not in this calibre's list at all."""
    if wanted in titles:
        return wanted
    stripped = wanted.strip()
    for title in titles:
        if title.strip() == stripped:
            return title
    return None


def _ambiguous(titles: list[str], name: str) -> bool:
    """True when the enumerated list carries this title more than once
    (title-keyed CLI resolution silently picks the first match, so a
    collision is a wrong-fetch the verb refuses to guess on)."""
    return titles.count(name) > 1


def _dest_name(title: str) -> str:
    """The output filename for one edition: the recipe title with the
    house path-scrub (`_dest_path`'s `/` -> `_`) and the filesystem's
    null byte refused the same way. Unicode is kept: editions are for
    humans first."""
    return title.replace("/", "_").replace("\x00", "_").strip() + ".epub"


def resolve_plan(args, titles: list[str]) -> tuple[list[dict], list[str]]:
    """The per-recipe plan rows and the warnings. Explicit --recipe
    selections refuse what they cannot name exactly (unknown, ambiguous);
    the curated default degrades to skip rows + warnings instead, because
    a calibre upgrade renaming one default recipe must not kill the
    batch. Same title twice in one invocation is one row."""
    explicit = list(dict.fromkeys(getattr(args, "recipe", None) or []))
    if explicit:
        wanted = explicit
        curated = False
    elif getattr(args, "all", False):
        wanted = list(dict.fromkeys(titles))
        curated = False
    else:
        wanted = list(DEFAULT_RECIPES)
        curated = True

    rows: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for name in wanted:
        title = _title_for(titles, name)
        if title is None:
            if curated:
                warnings.append(
                    f"{name!r} is not in this calibre's recipe list; skipped "
                    "(run news --list shows what is available)"
                )
                continue
            raise ValueError(
                f"unknown recipe {name!r}; `run news --list` shows the "
                f"{len(titles)} titles this calibre ships"
            )
        if _ambiguous(titles, title):
            if not explicit:
                # A stale curated default or a colliding title inside an
                # --all flood degrades to a skip: title addressing cannot
                # reach it, and neither case may kill the batch.
                warnings.append(
                    f"{title!r} names {titles.count(title)} recipes in this "
                    "calibre; skipped (title addressing cannot choose between "
                    "them; use Calibre's GUI scheduler or a custom .recipe file)"
                )
                continue
            raise ValueError(
                f"{title!r} names {titles.count(title)} different builtin "
                "recipes and title addressing cannot choose between them; "
                "use Calibre's GUI scheduler or a custom .recipe file"
            )
        if title in seen:
            continue
        seen.add(title)
        rows.append({"recipe": title, "action": "fetch", "title": _dest_name(title)})
    return rows, warnings


def _print_plan(rows: list[dict], dest: Path, warnings: list[str], args) -> None:
    if getattr(args, "format", None) == "json":
        print(
            json.dumps(
                {
                    "plan": {
                        "dest": str(dest),
                        "recipes": rows,
                        "warnings": warnings,
                    }
                },
                indent=1,
            )
        )
        return
    for line in warnings:
        print(f"WARNING: {line}", file=sys.stderr)
    print(f"news plan: {len(rows)} edition(s) -> {dest}")
    for row in rows:
        print(f"  {row['recipe']} -> {row['title']}")
    print(
        "Dry run: nothing fetched. Pass --apply to fetch (network; the "
        "anti-bot caveats are on `run --help news`)."
    )


def run_news(args) -> int:
    """`run news`: dry run by default; --apply fetches each planned
    edition sequentially through its own ebook-convert spawn. A missing
    binary refuses at the enumeration (the dry run plans from that same
    list); a failing or hung fetch is one report row, and its partial
    output is deleted so a resume cannot skip on it."""
    if getattr(args, "list", False) and (
        getattr(args, "recipe", None) or getattr(args, "all", False)
    ):
        print(
            "ERROR: run news --list takes no selection flags (--recipe/--all).",
            file=sys.stderr,
        )
        return 2
    try:
        titles = list_recipe_titles()
    except RuntimeError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("ERROR: ebook-convert --list-recipes timed out.", file=sys.stderr)
        return 1

    if getattr(args, "list", False):
        if getattr(args, "format", None) == "json":
            print(json.dumps({"recipes": titles, "count": len(titles)}, indent=1))
        else:
            for title in titles:
                print(title)
            print(f"{len(titles)} recipes available.", file=sys.stderr)
        return 0

    try:
        rows, warnings = resolve_plan(args, titles)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    dest_raw = getattr(args, "dest", None)
    dest = (
        Path(dest_raw).expanduser()
        if dest_raw
        else Path.cwd() / "news" / datetime.now().strftime("%Y-%m-%d")
    )
    if dest.exists() and not dest.is_dir():
        print(f"ERROR: --dest is not a directory: {dest}", file=sys.stderr)
        return 2
    apply = bool(getattr(args, "apply", False))
    if not apply:
        _print_plan(rows, dest, warnings, args)
        return 0
    if not rows:
        for line in warnings:
            print(f"WARNING: {line}", file=sys.stderr)
        print("No recipes to fetch.")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("ebook-convert")
    if exe is None:
        # Vanished between the enumeration and the fetch; a uniform
        # refusal, never a TypeError from the spawn.
        print("ERROR: ebook-convert is not on PATH.", file=sys.stderr)
        return 2
    timeout = getattr(args, "timeout", None) or _DEFAULT_TIMEOUT
    force = bool(getattr(args, "force", False))
    quiet = bool(getattr(args, "quiet", False))
    as_json = getattr(args, "format", None) == "json"

    # The skip warnings surface before the first fetch: a curated recipe
    # this calibre no longer ships is a plan fact, not a tail note.
    for line in warnings:
        print(f"WARNING: {line}", file=sys.stderr)

    fetched = already = failed = 0
    skipped = len(warnings)
    results: list[dict] = []
    try:
        for row in rows:
            target = dest / row["title"]
            entry = {"recipe": row["recipe"], "path": str(target)}
            if target.exists() and target.stat().st_size > 0 and not force:
                entry["status"] = "already"
                already += 1
                results.append(entry)
                if not quiet and not as_json:
                    print(f"  already: {row['recipe']}", flush=True)
                continue
            started = datetime.now().timestamp()
            try:
                proc = subprocess.run(
                    [exe, f"{row['recipe']}.recipe", str(target)],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                entry["status"] = "timeout"
                entry["detail"] = f"no exit within {timeout}s"
                target.unlink(missing_ok=True)
                failed += 1
                results.append(entry)
                if not quiet and not as_json:
                    print(f"  TIMEOUT: {row['recipe']}", flush=True)
                continue
            except OSError as e:
                entry["status"] = "failed"
                entry["detail"] = str(e)[:200]
                target.unlink(missing_ok=True)
                failed += 1
                results.append(entry)
                continue
            seconds = round(datetime.now().timestamp() - started, 1)
            if (
                proc.returncode != 0
                or not target.exists()
                or target.stat().st_size == 0
            ):
                entry["status"] = "failed"
                entry["detail"] = (proc.stderr or "ebook-convert failed").strip()[-200:]
                # A failed fetch must not leave a partial file the next
                # resume would treat as a fetched edition (the catalog
                # sweeps' stale-file rule).
                target.unlink(missing_ok=True)
                failed += 1
                results.append(entry)
                if not quiet and not as_json:
                    print(
                        f"  FAILED: {row['recipe']}: {entry['detail'][:120]}",
                        flush=True,
                    )
                continue
            entry["status"] = "fetched"
            entry["seconds"] = seconds
            entry["bytes"] = target.stat().st_size
            fetched += 1
            results.append(entry)
            if not quiet and not as_json:
                print(
                    f"  fetched: {row['recipe']} "
                    f"({entry['bytes'] / (1 << 20):.0f} MB in {seconds:.0f}s)",
                    flush=True,
                )
    except KeyboardInterrupt:
        # The editions already fetched stay on disk; a re-run skips them.
        # Report the partial batch instead of a bare traceback.
        if as_json:
            print(
                json.dumps(
                    {
                        "results": results,
                        "dest": str(dest),
                        "fetched": fetched,
                        "already": already,
                        "skipped": skipped,
                        "failed": failed,
                        "interrupted": True,
                    },
                    indent=1,
                )
            )
        else:
            print(
                f"Interrupted: {fetched} fetched, {already} already present, "
                f"{failed} failed before the interrupt; re-run to resume.",
                file=sys.stderr,
            )
        return 1

    if as_json:
        print(
            json.dumps(
                {
                    "results": results,
                    "dest": str(dest),
                    "fetched": fetched,
                    "already": already,
                    "skipped": skipped,
                    "failed": failed,
                },
                indent=1,
            )
        )
    else:
        print(
            f"News: {fetched} fetched, {already} already present, "
            f"{skipped} skipped, {failed} failed -> {dest}"
        )
        if failed:
            print(
                "Re-run the same command later: the failures are retried, "
                "the fetched editions are skipped."
            )
    return 1 if failed else 0
