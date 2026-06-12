#!/usr/bin/env python3
"""
compress_pdf.py: shrink an oversize PDF via ghostscript, with safety checks.

Use case: TTRPG sourcebooks and visual-layout PDFs in the library that ship
uncompressed (Knock! #4 was 1 GB; Degenesis pieces in the 250-400 MB range).
Ghostscript's /ebook preset re-encodes raster images at 150 DPI, which is
sweet-spot quality for screen reading and typically yields 40-80% reduction.

Workflow
    1. Verify ghostscript is available.
    2. Snapshot original PDF size + page count (via pdfinfo if present).
    3. Run ghostscript with the chosen preset to a temp file.
    4. Verify the output: page count matches, file is a valid PDF.
    5. Replace original. Original is preserved as <name>.pre-compress.pdf.
    6. If the PDF lives in a Calibre library, update books_pages_link.format_size
       so Calibre doesn't think the cache is stale.

Run from anywhere:
    python3 compress_pdf.py path/to/file.pdf
    python3 compress_pdf.py path/to/file.pdf --preset screen
    python3 compress_pdf.py path/to/file.pdf --dry-run

Exit codes
    0 = compressed, verified, replaced
    1 = compression aborted (no shrink, page-count mismatch, or verify failed)
    2 = setup error (ghostscript missing, file unreadable)
"""

import argparse
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

# Ghostscript pdfwrite quality presets:
#   screen   72 dpi  smallest, low quality (web preview)
#   ebook    150 dpi sweet spot for screen reading
#   printer  300 dpi laser printer quality
#   prepress 300 dpi commercial print quality
PRESETS = ("screen", "ebook", "printer", "prepress")

# ANSI; suppress when not a TTY
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""


def fmt_size(n: int) -> str:
    if n >= 1 << 30:
        return f"{n / (1 << 30):.2f} GB"
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MB"
    return f"{n / 1024:.1f} KB"


def require(cmd: str) -> str:
    p = shutil.which(cmd)
    if not p:
        print(f"{RED}ERROR{RESET}: required tool '{cmd}' not found on PATH.")
        sys.exit(2)
    return p


def page_count(path: Path) -> int | None:
    """Return PDF page count via pdfinfo if available; else None (skip the check)."""
    pdfinfo = shutil.which("pdfinfo")
    if not pdfinfo:
        return None
    try:
        out = subprocess.check_output(
            [pdfinfo, str(path)], stderr=subprocess.DEVNULL, text=True
        )
    except subprocess.CalledProcessError:
        return None
    for line in out.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    return None


def pdf_features(path: Path) -> dict:
    """Inspect a PDF's structural features via pdfinfo / pdfimages / pdfdetach.
    Returns a dict; values are None when the underlying tool isn't available or
    the field is absent. Never raises."""
    f: dict = {
        "size": path.stat().st_size,
        "pages": None,
        "optimized": None,
        "form": None,  # 'AcroForm', 'XFA', or 'none'
        "javascript": None,  # True / False
        "attachments": 0,
        "avg_dpi": None,
        "image_count": 0,
        "tagged": None,
    }
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        try:
            out = subprocess.check_output(
                [pdfinfo, str(path)], stderr=subprocess.DEVNULL, text=True
            )
            for line in out.splitlines():
                if ":" not in line:
                    continue
                k, v = (s.strip() for s in line.split(":", 1))
                if k == "Pages":
                    f["pages"] = int(v)
                elif k == "Optimized":
                    f["optimized"] = v.lower() == "yes"
                elif k == "Form":
                    f["form"] = v
                elif k == "JavaScript":
                    f["javascript"] = v.lower() == "yes"
                elif k == "Tagged":
                    f["tagged"] = v.lower() == "yes"
        except subprocess.CalledProcessError:
            pass

    pdfdetach = shutil.which("pdfdetach")
    if pdfdetach:
        try:
            out = subprocess.check_output(
                [pdfdetach, "-list", str(path)], stderr=subprocess.DEVNULL, text=True
            )
            # Count entries; pdfdetach prefixes "0 embedded files" or a numbered list
            for line in out.splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.split()[0].isdigit() and "embedded files" in s:
                    f["attachments"] = int(s.split()[0])
                    break
        except subprocess.CalledProcessError:
            pass

    pdfimages = shutil.which("pdfimages")
    if pdfimages:
        try:
            out = subprocess.check_output(
                [pdfimages, "-list", str(path)], stderr=subprocess.DEVNULL, text=True
            )
            dpis: list[int] = []
            for line in out.splitlines()[2:]:  # skip 2 header lines
                cols = line.split()
                if len(cols) < 14:
                    continue
                # x-ppi at column 12 (0-indexed) in poppler-utils
                try:
                    x_ppi = int(cols[12])
                    if x_ppi > 0:
                        dpis.append(x_ppi)
                except ValueError, IndexError:
                    continue
            f["image_count"] = len(dpis)
            if dpis:
                f["avg_dpi"] = sum(dpis) // len(dpis)
        except subprocess.CalledProcessError:
            pass

    return f


def recommend(features: dict) -> tuple[str, str]:
    """Return (verdict, rationale) based on the feature report.
    verdict in: 'ebook', 'printer', 'skip-small', 'skip-optimized',
                'skip-already-low-dpi', 'manual'."""
    size_mb = features["size"] / (1 << 20)
    if size_mb < 50:
        return "skip-small", f"only {size_mb:.0f} MB, not worth the round-trip"

    form = (features["form"] or "").lower()
    has_form = form not in ("", "none")
    has_js = bool(features["javascript"])
    has_attachments = features["attachments"] > 0
    optimized = features["optimized"]
    dpi = features["avg_dpi"]

    risks: list[str] = []
    if has_form:
        risks.append(f"form fields ({features['form']}) flatten on compression")
    if has_js:
        risks.append("JavaScript actions drop on compression")
    if has_attachments:
        risks.append(f"{features['attachments']} embedded file(s) may drop")

    if dpi is None:
        return (
            "manual",
            "no raster images detected; inspect manually before compressing",
        )

    if optimized is True and size_mb < 200 and dpi <= 200:
        return "skip-optimized", "already optimized at moderate DPI; little to gain"

    if dpi < 150:
        return (
            "skip-already-low-dpi",
            f"avg image DPI is {dpi}; already at /ebook level",
        )

    if risks:
        # Form/JS/attachments make /printer safer (still big savings, less aggressive)
        rationale = "; ".join(risks) + "; use /printer to keep more headroom"
        return "printer", rationale

    if dpi >= 250:
        est = int(100 * (1 - (150 / dpi) ** 2))
        return "ebook", f"avg image DPI {dpi}; /ebook should shrink raster ~{est}%"
    return "printer", f"avg image DPI {dpi}; modest savings with /printer is safer"


def inspect_one(path: Path, brief: bool = False) -> dict:
    f = pdf_features(path)
    verdict, why = recommend(f)
    f["verdict"] = verdict
    f["rationale"] = why
    if not brief:
        print(f"{BOLD}{path}{RESET}")
        print(f"  size:           {fmt_size(f['size'])}")
        print(f"  pages:          {f['pages']}")
        print(f"  optimized:      {f['optimized']}")
        print(f"  form:           {f['form']}")
        print(f"  javascript:     {f['javascript']}")
        print(f"  attachments:    {f['attachments']}")
        print(
            f"  images:         {f['image_count']}  (avg {f['avg_dpi']} DPI)"
            if f["avg_dpi"]
            else f"  images:         {f['image_count']}"
        )
        print(f"  verdict:        {verdict}")
        print(f"  rationale:      {why}")
        print()
    return f


def inspect_path(target: Path, min_size_mb: int) -> None:
    """Inspect a single PDF or every PDF in a directory tree above the size threshold."""
    if target.is_file():
        inspect_one(target)
        return
    min_bytes = min_size_mb * (1 << 20)
    pdfs: list[tuple[int, Path]] = []
    for p in target.rglob("*.pdf"):
        try:
            sz = p.stat().st_size
        except OSError:
            continue
        if sz >= min_bytes:
            pdfs.append((sz, p))
    pdfs.sort(reverse=True)

    print(
        f"{BOLD}Inspecting {len(pdfs)} PDF(s) over {min_size_mb} MB under {target}{RESET}\n"
    )
    summary: dict[str, list[tuple[Path, dict]]] = {}
    for _, p in pdfs:
        f = inspect_one(p, brief=False)
        summary.setdefault(f["verdict"], []).append((p, f))

    print(f"{BOLD}=== Summary by verdict ==={RESET}")
    total_save_ebook = 0
    total_save_printer = 0
    for v in (
        "ebook",
        "printer",
        "skip-already-low-dpi",
        "skip-optimized",
        "skip-small",
        "manual",
    ):
        if v not in summary:
            continue
        print(f"\n[{v}] ({len(summary[v])} file(s))")
        for p, f in summary[v]:
            est_pct = 0
            if v == "ebook" and f["avg_dpi"]:
                est_pct = int(100 * (1 - (150 / f["avg_dpi"]) ** 2))
                est_save = int(f["size"] * est_pct / 100 * 0.85)  # 85% raster heuristic
                total_save_ebook += est_save
            if v == "printer" and f["avg_dpi"]:
                if f["avg_dpi"] > 300:
                    est_pct = int(100 * (1 - (300 / f["avg_dpi"]) ** 2))
                else:
                    est_pct = 30
                est_save = int(f["size"] * est_pct / 100 * 0.75)
                total_save_printer += est_save
            label = f"~{est_pct}% est" if est_pct else "..."
            print(f"  {fmt_size(f['size']):>10}  {label:>10}  {p}")
    if total_save_ebook or total_save_printer:
        print()
        if total_save_ebook:
            print(
                f"Estimated savings if /ebook on the 'ebook' bucket: ~{fmt_size(total_save_ebook)}"
            )
        if total_save_printer:
            print(
                f"Estimated savings if /printer on the 'printer' bucket: ~{fmt_size(total_save_printer)}"
            )


def find_calibre_library(start: Path) -> Path | None:
    """Walk up from `start` looking for a metadata.db (signals a Calibre library root)."""
    for parent in (start, *start.parents):
        if (parent / "metadata.db").is_file():
            return parent
    return None


def update_calibre_size(library_root: Path, file_path: Path, new_size: int) -> None:
    """Sync Calibre's recorded size for the replaced file, printing the outcome.

    Updates both the core `data.uncompressed_size` (what Calibre uses to notice a
    format changed on disk) and, when the Count Pages plugin is present, its
    `books_pages_link.format_size` page-size cache.

    Best-effort: the PDF has already been replaced by the time this runs, so a
    busy/locked database (Calibre open), a missing book, or a missing plugin
    table must never raise.
    """
    rel = file_path.relative_to(library_root)
    parts = rel.parts
    if len(parts) < 3:
        return
    book_path = "/".join(parts[:2])
    fmt = file_path.suffix.lstrip(".").upper()
    db = library_root / "metadata.db"

    try:
        con = sqlite3.connect(db, timeout=5)
    except sqlite3.Error as e:
        print(f"{YELLOW}WARN{RESET}: could not open {db} to sync size: {e}")
        return
    try:
        cur = con.cursor()
        cur.execute("SELECT id FROM books WHERE path = ?", (book_path,))
        row = cur.fetchone()
        if not row:
            print(
                f"(Inside a Calibre library, but no book row for {book_path!r}; size not synced.)"
            )
            return
        book_id = row[0]
        cur.execute(
            "UPDATE data SET uncompressed_size = ? WHERE book = ? AND format = ?",
            (new_size, book_id, fmt),
        )
        synced = ["data.uncompressed_size"] if cur.rowcount > 0 else []

        # books_pages_link is created by the Count Pages plugin and is optional.
        try:
            cur.execute(
                "UPDATE books_pages_link SET format_size = ?, needs_scan = 1 WHERE book = ? AND format = ?",
                (new_size, book_id, fmt),
            )
            if cur.rowcount > 0:
                synced.append("books_pages_link.format_size")
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                raise  # a real error (e.g. lock) belongs to the outer handler

        con.commit()
        if synced:
            print(f"Synced {' + '.join(synced)} in {db}")
        else:
            print(
                f"(No data/books_pages_link rows for book {book_id}/{fmt}; size not synced.)"
            )
    except sqlite3.OperationalError as e:
        print(
            f"{YELLOW}WARN{RESET}: metadata.db is busy or locked ({e}). The PDF was "
            "replaced; close Calibre and re-run to sync the page-size cache."
        )
    except sqlite3.Error as e:
        print(f"{YELLOW}WARN{RESET}: could not sync size to metadata.db: {e}")
    finally:
        con.close()


def compress(src: Path, preset: str, dry_run: bool, out_dir: Path | None = None) -> int:
    gs = require("gs")
    if not src.is_file():
        print(f"{RED}ERROR{RESET}: {src} is not a file.")
        return 2
    if src.suffix.lower() != ".pdf":
        print(f"{RED}ERROR{RESET}: {src} does not look like a PDF.")
        return 2

    orig_size = src.stat().st_size
    orig_pages = page_count(src)

    print(f"{BOLD}Input{RESET}: {src}")
    print(f"  size:  {fmt_size(orig_size)}  ({orig_size:,} bytes)")
    print(
        f"  pages: {orig_pages if orig_pages is not None else '(pdfinfo unavailable; skipping)'}"
    )
    print(f"  preset: /{preset}")

    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_tmp = out_dir / (src.stem + ".compress.tmp.pdf")
        final_out = out_dir / src.name
    else:
        out_tmp = src.with_suffix(".compress.tmp.pdf")
        final_out = src
    backup = src.with_suffix(".pre-compress.pdf")
    # A leftover rollback file is the only copy of the true original; replacing
    # it with the (already once-compressed) src would destroy it silently.
    if out_dir is None and not dry_run and backup.exists():
        print(
            f"{RED}ABORT{RESET}: rollback file already exists: {backup}. "
            "Replacing would overwrite the original from a previous run; "
            "move or delete it first."
        )
        return 1

    cmd = [
        gs,
        "-sDEVICE=pdfwrite",
        "-dCompatibilityLevel=1.5",
        f"-dPDFSETTINGS=/{preset}",
        "-dNOPAUSE",
        "-dQUIET",
        "-dBATCH",
        f"-sOutputFile={out_tmp}",
        str(src),
    ]
    print("\nRunning ghostscript...")
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"{RED}ghostscript exited {e.returncode}{RESET}")
        out_tmp.unlink(missing_ok=True)
        return 1

    new_size = out_tmp.stat().st_size
    new_pages = page_count(out_tmp)
    saved = orig_size - new_size
    pct = (saved / orig_size) * 100 if orig_size else 0

    print(f"\n{BOLD}Result{RESET}:")
    print(f"  before: {fmt_size(orig_size)}  ({orig_size:,} bytes)")
    print(f"  after:  {fmt_size(new_size)}  ({new_size:,} bytes)")
    print(f"  saved:  {fmt_size(saved)}  ({pct:.1f}%)")
    print(f"  pages:  {new_pages} (was {orig_pages})")

    if orig_pages is not None and new_pages is not None and orig_pages != new_pages:
        print(
            f"\n{RED}ABORT{RESET}: page count changed ({orig_pages} -> {new_pages}). Original untouched; temp dropped."
        )
        out_tmp.unlink(missing_ok=True)
        return 1
    if new_size >= orig_size:
        print(
            f"\n{YELLOW}No size win{RESET}: output is not smaller. Original untouched; temp dropped."
        )
        out_tmp.unlink(missing_ok=True)
        return 1

    if dry_run:
        print(
            f"\n{YELLOW}DRY-RUN{RESET}: leaving {out_tmp.name} in place; nothing replaced."
        )
        return 0

    if out_dir is not None:
        # Out-of-tree mode: rename temp to final, leave original alone, skip Calibre DB sync.
        shutil.move(str(out_tmp), str(final_out))
        print(f"\n{GREEN}WROTE{RESET}: {final_out}")
        print(f"Original untouched at: {src}")
        return 0

    # In-place mode: leave a .pre-compress.pdf rollback file in the source dir
    shutil.move(str(src), str(backup))
    shutil.move(str(out_tmp), str(src))
    print(f"\n{GREEN}REPLACED{RESET}. Original at: {backup.name}")

    # If the file sits inside a Calibre library, sync books_pages_link.format_size
    library = find_calibre_library(src.parent)
    if library:
        update_calibre_size(library, src, new_size)

    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="Shrink a PDF via ghostscript, with verify-or-rollback."
    )
    p.add_argument(
        "path", type=Path, help="PDF (or directory with --inspect) to operate on"
    )
    p.add_argument(
        "--preset",
        choices=PRESETS,
        default="ebook",
        help="ghostscript pdfwrite quality preset (default: ebook = 150 dpi)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="run compression to a temp file but do not replace the original",
    )
    p.add_argument(
        "--inspect",
        action="store_true",
        help="report features and a per-file recommendation; do not compress",
    )
    p.add_argument(
        "--min-size-mb",
        type=int,
        default=50,
        help="when --inspect is on a directory, skip PDFs smaller than this (default 50)",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="write compressed copy to this directory instead of replacing in place "
        "(original PDF is left untouched; Calibre DB is not modified)",
    )
    args = p.parse_args()
    if args.inspect:
        inspect_path(args.path.resolve(), args.min_size_mb)
        return 0
    out_dir = args.out_dir.resolve() if args.out_dir else None
    return compress(args.path.resolve(), args.preset, args.dry_run, out_dir)


if __name__ == "__main__":
    sys.exit(main())
