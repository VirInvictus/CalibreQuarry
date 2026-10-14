#!/usr/bin/env python3
"""extract_stamp_evidence: the stamp-research pass's evidence feeder.

One JSON row per wave file (field set fixed 2026-10-14, issue #24, after
four waves hand-rolled the same extractor and the copies diverged):

  file, path, fmt        -- what was scanned
  seed                   -- the embedded-metadata seed via `ebook-meta`
                            (title, author(s), publisher, published,
                            languages, isbn, identifiers; a read calibre
                            itself refuses is seed=None + an error)
  opf_ids                -- the EPUB OPF's dc:identifier values (<= 8)
  front                  -- the first pages' text: the first 4 spine
                            documents for an EPUB, `pdftotext` pages 1-4
                            for a PDF; other formats carry no front
  isbn_snips             -- ISBN-shaped snippets from the front (<= 6)
  provenance             -- the filename marker's #source seed (the same
                            seeder phase 1 uses; since the standing
                            2026-10-12 ruling a bare name seeds Z-Lib)
  error                  -- everything that went wrong for THIS file,
                            None when nothing did

The rows feed the research briefs; choosing the stamp VALUES stays the
research pass's job (the phase-1 skill). Reads only: directory inputs are
listed non-recursively (top-level files only), nothing is ever written
into an input directory, and `--out` is refused when it would land inside
one -- a wave's evidence belongs in the wave's working dir, never the
library root.

OPF parsing is attribute-order-independent BY CONSTRUCTION (ElementTree,
id and href read as separate attributes of each <item>): the 2026-10-14
wave's hand-rolled copy used an id-first regex, this wave's OPFs put href
first, and the front field went silently empty for 87 of 111 EPUBs --
caught only by a thin-front census. That census is built in here as the
extraction's own quality gate: rows whose front came back shorter than
--thin-chars (default 200) are listed in a THIN FRONT block and the run
exits 1, because evidence this thin must be looked at, never trusted.

Exit codes: 0 = rows written, every extracted front above threshold,
1 = thin-front rows found (the census names them), 2 = usage/setup error.
"""

import argparse
import json
import posixpath
import re
import subprocess
import sys
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

try:
    from version_guard import announce
except ImportError:  # a lone deployed copy: no siblings beside it

    def announce(stream=None):
        pass


try:
    from version_guard import imported_version, version_tuple

    from cquarry_cli.run import _provenance_from_filename

    # The bare-name Z-Lib default landed in 3.66.0: a stale user-site
    # module (the issue-#20 skew) still seeds None and would silently
    # hollow the provenance field out, so anything below the floor runs
    # the vendored fallback below instead.
    if (version_tuple(imported_version("cquarry_cli")) or ()) < (3, 66, 0):
        raise ImportError
except Exception:
    _PROVENANCE_PATTERNS = (
        (re.compile(r"anna[’']s\s+archive", re.IGNORECASE), "Anna's Archive"),
        (re.compile(r"\b(?:z[\s_-]?lib(?:rary)?|1lib)\b", re.IGNORECASE), "Z-Lib"),
        (re.compile(r"\blibgen\b", re.IGNORECASE), "Library Genesis"),
    )

    def _provenance_from_filename(path: str) -> str:
        stem = posixpath.splitext(posixpath.basename(path))[0]
        for pattern, source in _PROVENANCE_PATTERNS:
            if pattern.search(stem):
                return source
        return "Z-Lib"


#: The scanned extensions: run.py's _EBOOK_EXTS. Keep the two in step.
EVIDENCE_EXTENSIONS = (".epub", ".pdf", ".mobi", ".azw3", ".djvu")

#: Formats with a front extractor; the thin-front census judges only these
#: (a mobi row with empty front is "no extractor", not "extraction broke").
FRONT_FORMATS = (".epub", ".pdf")

_FRONT_DOC_COUNT = 4
_FRONT_DOC_CHARS = 1500
_EPUB_FRONT_CHARS = 4000
_PDF_FRONT_CHARS = 3000

#: The seed labels this script lifts from ebook-meta's display output
#: (lowercased). Deliberately the raw display fields: the research pass
#: wants the ISBN/identifiers lines phase 1's stamp seeding refuses.
_SEED_LABELS = (
    "title",
    "author(s)",
    "publisher",
    "published",
    "languages",
    "isbn",
    "identifiers",
)

#: Copyright-page ISBN shapes (the stabilized evidence shape). Snippets,
#: not verdicts: every candidate is checksum-validated by the research
#: pass before it becomes a stamp.
_ISBN_SNIP_RE = re.compile(
    r"(?:isbn(?:[^0-9]{0,3}1[03])?[^0-9]{0,12})"
    r"((?:97[89][ -]?)?(?:[0-9][0-9 -]{8,14}[0-9Xx]))",
    re.IGNORECASE,
)


def _run_ebook_meta(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ebook-meta", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )


def _run_pdftotext(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["pdftotext", "-f", "1", "-l", "4", str(path), "-"],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )


def _local_name(el: ET.Element) -> str:
    return el.tag.split("}")[-1]


def _epub_front(path: Path) -> tuple[str, list[str]]:
    """(front text, opf identifier values). The OPF manifest and spine are
    parsed with ElementTree: id and href are read as separate attributes
    of each <item> element, so attribute ORDER never matters (the
    2026-10-14 bug this script retires was an id-first regex that emptied
    the manifest for href-first OPFs)."""
    with zipfile.ZipFile(path) as z:
        container = z.read("META-INF/container.xml").decode("utf-8", "replace")
        m = re.search(r'full-path="([^"]+)"', container)
        if m:
            opf_name = m.group(1)
        else:
            opfs = [n for n in z.namelist() if n.endswith(".opf")]
            if not opfs:
                raise ValueError("no rootfile in container.xml and no .opf member")
            opf_name = opfs[0]
        opf = ET.fromstring(z.read(opf_name))
        manifest: dict[str, str] = {}
        spine: list[str] = []
        opf_ids: list[str] = []
        for el in opf.iter():
            local = _local_name(el)
            if local == "item":
                if el.get("id") and el.get("href"):
                    manifest[el.get("id")] = el.get("href")
            elif local == "itemref":
                if el.get("idref"):
                    spine.append(el.get("idref"))
            elif local == "identifier" and el.text and el.text.strip():
                opf_ids.append(el.text.strip())
        base = posixpath.dirname(opf_name)
        texts: list[str] = []
        for ref in spine[:_FRONT_DOC_COUNT]:
            href = manifest.get(ref)
            if not href:
                continue
            member = posixpath.normpath(
                posixpath.join(base, urllib.parse.unquote(href))
            )
            try:
                doc = z.read(member).decode("utf-8", "replace")
            except KeyError:
                continue
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", doc))
            texts.append(text[:_FRONT_DOC_CHARS])
    return " | ".join(texts)[:_EPUB_FRONT_CHARS], opf_ids[:8]


def _pdf_front(path: Path) -> str:
    proc = _run_pdftotext(path)
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftotext exited {proc.returncode}: {(proc.stderr or '').strip()[:120]}"
        )
    return re.sub(r"\s+", " ", proc.stdout)[:_PDF_FRONT_CHARS]


def _seed(path: Path) -> tuple[dict | None, str | None]:
    """The ebook-meta display fields as the research seed. A read calibre
    itself refuses (a nonzero exit, or its own filename guess plus a
    stderr traceback) is seed=None with the error on the record -- the
    same guard phase 1's embedded-metadata seeding uses."""
    try:
        proc = _run_ebook_meta(path)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"ebook-meta: {e}"
    if proc.returncode != 0:
        return None, f"ebook-meta: exited {proc.returncode}"
    if "traceback" in (proc.stderr or "").lower():
        return None, "ebook-meta: unparseable file (calibre printed its own guess)"
    fields: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if " : " not in line:
            continue
        label, value = line.split(" : ", 1)
        fields[label.strip().lower()] = value.strip()
    seed = {k: fields[k] for k in _SEED_LABELS if fields.get(k)}
    return seed, None


def _isbn_snips(front: str) -> list[str]:
    # The whole front (it is size-capped at extraction time), so an EPUB
    # row's fourth spine document is snip-scanned like the first.
    snips = {m.group(1).strip() for m in _ISBN_SNIP_RE.finditer(front)}
    return sorted(snips)[:6]


def _row(path: Path) -> dict:
    fmt = path.suffix[1:].lower()
    row: dict = {
        "file": path.name,
        "path": str(path),
        "fmt": fmt,
        "seed": None,
        "opf_ids": [],
        "front": "",
        "isbn_snips": [],
        "provenance": _provenance_from_filename(str(path)),
        "error": None,
    }
    seed, error = _seed(path)
    row["seed"] = seed
    row["error"] = error
    try:
        if fmt == "epub":
            row["front"], row["opf_ids"] = _epub_front(path)
        elif fmt == "pdf":
            row["front"] = _pdf_front(path)
    except Exception as e:  # one unreadable file never kills the run
        row["error"] = (row["error"] + "; " if row["error"] else "") + f"front: {e}"
    row["isbn_snips"] = _isbn_snips(row["front"])
    return row


def _collect_files(inputs: list[str]) -> tuple[list[Path], list[Path]]:
    """(files, dirs) from the positional inputs: a file joins the scan
    whatever its extension (the refusal below reports it), a directory
    contributes its TOP-LEVEL files only -- the waves' inputs are flat
    download dirs, and a recursive walk would sweep a prior phase's
    `_quarantine/` subtree into the evidence."""
    files: list[Path] = []
    dirs: list[Path] = []
    for raw in inputs:
        p = Path(raw).expanduser()
        if p.is_dir():
            dirs.append(p)
            files.extend(child for child in sorted(p.iterdir()) if child.is_file())
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(f"no such file or directory: {p}")
    seen: set[str] = set()
    unique: list[Path] = []
    for f in files:
        key = str(f.resolve())
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique, dirs


def _check_out_path(out: Path, files: list[Path], dirs: list[Path]) -> str | None:
    """The reads-only guard: the report never lands inside a scanned
    directory or on a scanned file."""
    try:
        resolved = out.expanduser().resolve()
    except OSError as e:
        return f"unusable --out: {e}"
    if resolved.is_dir():
        return f"--out is a directory: {resolved}"
    for d in dirs:
        r = d.resolve()
        if resolved == r or r in resolved.parents:
            return (
                f"--out ({resolved}) must be OUTSIDE the scanned directories "
                f"({r}); reads only, never write into an input tree"
            )
    for f in files:
        if resolved == f.resolve():
            return f"--out must not overwrite a scanned file ({f})"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract one JSON evidence row per file for the "
        "stamp-research pass (reads only; the thin-front census is the "
        "extraction's quality gate)."
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help="files and/or directories (top-level files only) to profile",
    )
    parser.add_argument(
        "--out",
        required=True,
        help="JSON report path (a bare list, one row per file); must sit "
        "OUTSIDE every scanned directory",
    )
    parser.add_argument(
        "--thin-chars",
        type=int,
        default=200,
        help="front shorter than this many characters is census-flagged "
        "(default: 200; exit 1)",
    )
    args = parser.parse_args()

    announce()

    try:
        files, dirs = _collect_files(args.inputs)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    scanned = [f for f in files if f.suffix.lower() in EVIDENCE_EXTENSIONS]
    skipped = len(files) - len(scanned)
    if not scanned:
        print(
            "ERROR: no ebook files found (extensions: "
            f"{', '.join(EVIDENCE_EXTENSIONS)}).",
            file=sys.stderr,
        )
        return 2

    out = Path(args.out).expanduser()
    if problem := _check_out_path(out, files, dirs):
        print(f"ERROR: {problem}", file=sys.stderr)
        return 2

    rows: list[dict] = []
    for index, f in enumerate(scanned, 1):
        rows.append(_row(f))
        print(f"{index}/{len(scanned)} {f.name[:60]}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)

    thin = [
        r
        for r in rows
        if ("." + r["fmt"]) in FRONT_FORMATS and len(r["front"]) < args.thin_chars
    ]
    print(f"saved {len(rows)} rows -> {out}")
    if skipped:
        print(f"(skipped {skipped} file(s) outside {', '.join(EVIDENCE_EXTENSIONS)})")
    print(f"THIN FRONT ({len(thin)} of {len(rows)}): threshold {args.thin_chars} chars")
    for r in thin:
        print(f"  thin: {r['file']}" + (f" ({r['error']})" if r["error"] else ""))
    if thin:
        print(
            "evidence this thin must be inspected, never trusted: check the "
            "rows (and the error fields) before dispatching the research wave."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
