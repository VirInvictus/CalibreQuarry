#!/usr/bin/env python3
"""
check_pdf.py: the per-file PDF/DJVU battery from the phase-1 skill, as a
standing tool (Phase 17's last hand-assembled check, retired from prose).

For each file it reports, without changing anything:

  header        the real magic bytes (%PDF- / AT&TFORM), not the extension
  pages         page count (qpdf --show-npages; djvused -e n for DJVU)
  qpdf_check    qpdf --check classified: clean / warnings (benign; qpdf
                exits 3 on warning-only files all the time) / errors
                (a real structural problem) / unavailable (no qpdf)
  fonts         pdffonts: how many fonts are NOT embedded (print hazard)
  text_layer    pdftotext sampled at pages 1, middle, and last (the
                page-1-only sample used to pass OCR-once scans)
  images        pdfimages -list: image count and area-weighted DPI

Every external tool is optional; a missing tool marks its check
"unavailable" rather than failing the file. Exit codes: 0 all files
verified without structural findings, 1 findings, 2 setup error.
`--json FILE` writes the machine report for the phase-1 runner.
"""

import argparse
import json
import subprocess
import sys

_PDF_MAGIC = b"%PDF-"
_DJVU_MAGIC = b"AT&TFORM"


def _run(cmd: list[str], timeout: int = 120) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except OSError, subprocess.TimeoutExpired:
        return None


def sniff_header(path: str) -> str:
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(_PDF_MAGIC):
        return "pdf"
    if head.startswith(_DJVU_MAGIC):
        return "djvu"
    return "unknown"


def classify_qpdf(returncode: int | None) -> str:
    """qpdf --check's documented exit contract (qpdf --help=exit-status):
    0 clean, 3 warnings (benign; qpdf exits 3 on warning-only files all
    the time), 2 errors, anything else errors too. The class reads the
    exit code alone: qpdf writes its warnings and the "operation succeeded
    with warnings" summary to STDERR, so gating exit 3 on a marker in
    stdout (the old behavior) misfiled every warning-only file as errors."""
    if returncode is None:
        return "unavailable"
    if returncode == 0:
        return "clean"
    if returncode == 3:
        return "warnings"
    return "errors"


def _page_count_pdf(path: str) -> int | None:
    proc = _run(["qpdf", "--show-npages", path])
    if proc is None or proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _page_count_djvu(path: str) -> int | None:
    proc = _run(["djvused", path, "-e", "n"])
    if proc is None or proc.returncode != 0:
        return None
    try:
        return int(proc.stdout.strip())
    except ValueError:
        return None


def _unembedded_fonts(path: str) -> int | None:
    proc = _run(["pdffonts", path])
    if proc is None or proc.returncode != 0:
        return None
    count = 0
    for line in proc.stdout.splitlines()[2:]:
        columns = line.split()
        if len(columns) >= 5 and columns[-5] == "no":
            count += 1
    return count


def sample_pages(pages: int | None) -> list[int]:
    """The page numbers the text layer is sampled at: 1, middle, last."""
    if not pages:
        return [1]
    return sorted({1, (pages + 1) // 2, pages})


def _text_layer_samples(path: str, pages: int | None) -> dict[str, str]:
    """present/absent per sampled page (first/middle/last keys)."""
    out: dict[str, str] = {}
    if not pages:
        pages = 1
    labels = {1: "first", (pages + 1) // 2: "middle", pages: "last"}
    for page in sample_pages(pages):
        proc = _run(["pdftotext", "-f", str(page), "-l", str(page), path, "-"])
        label = labels.get(page, f"p{page}")
        if proc is None or proc.returncode != 0:
            out[label] = "unavailable"
        else:
            out[label] = "present" if proc.stdout.strip() else "absent"
    return out


def _parse_pdfimages_list(stdout: str) -> dict | None:
    """Image count and area-weighted DPI from `pdfimages -list` output.

    Row shape (the documented header): page num type width height color
    comp bpc enc interp object ID x-ppi y-ppi size ratio. The DPI is the
    area-weighted mean of min(x-ppi, y-ppi), so a small logo cannot
    hide a full-page 72-dpi scan. Pure: takes the tool's stdout.
    """
    images = []
    for line in stdout.splitlines()[2:]:
        cols = line.split()
        if len(cols) < 14 or cols[0] == "page":
            continue
        try:
            width, height = int(cols[3]), int(cols[4])
            x_ppi, y_ppi = float(cols[12]), float(cols[13])
        except ValueError:
            continue
        ppi = min(x_ppi, y_ppi)
        if ppi <= 0 or width <= 0 or height <= 0:
            continue
        images.append({"area": width * height, "ppi": ppi})
    if not images:
        return {"count": 0, "weighted_ppi": None}
    total_area = sum(i["area"] for i in images)
    weighted = sum(i["area"] * i["ppi"] for i in images) / total_area
    return {"count": len(images), "weighted_ppi": round(weighted, 1)}


def _image_stats(path: str) -> dict | None:
    proc = _run(["pdfimages", "-list", path])
    if proc is None or proc.returncode != 0:
        return None
    return _parse_pdfimages_list(proc.stdout)


def check_file(path: str) -> dict:
    """The full battery for one file; pure data out, no side effects."""
    kind = sniff_header(path)
    report: dict = {"path": path, "kind": kind, "findings": []}
    if kind == "unknown":
        report["findings"].append({"kind": "header", "detail": "unrecognized"})
        return report
    if kind == "djvu":
        pages = _page_count_djvu(path)
        report["pages"] = pages
        if pages is None:
            report["findings"].append(
                {"kind": "pages", "detail": "djvused unavailable"}
            )
        return report

    proc = _run(["qpdf", "--check", path])
    verdict = classify_qpdf(proc.returncode if proc else None)
    report["qpdf_check"] = verdict
    if verdict == "errors":
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        report["findings"].append(
            {"kind": "qpdf_errors", "detail": detail[0][:120] if detail else ""}
        )
    elif verdict == "warnings":
        # Benign by class, but the warning text itself is the triage
        # evidence (unknown-token tolerance, linearization drift), so the
        # report carries the first line instead of a bare "benign".
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        report["findings"].append(
            {"kind": "qpdf_warnings", "detail": detail[0][:120] if detail else "benign"}
        )

    pages = _page_count_pdf(path)
    report["pages"] = pages
    fonts = _unembedded_fonts(path)
    if fonts is not None:
        report["unembedded_fonts"] = fonts
        if fonts:
            report["findings"].append(
                {"kind": "unembedded_fonts", "detail": f"{fonts} font(s) not embedded"}
            )
    samples = _text_layer_samples(path, pages)
    report["text_layer"] = samples
    present = [v for v in samples.values() if v == "present"]
    absent = [v for v in samples.values() if v == "absent"]
    if absent and present:
        report["findings"].append(
            {
                "kind": "text_layer_partial",
                "detail": "text on some sampled pages only: an OCR pass "
                "that covered page 1 (the old page-1 sample passed this), "
                "or a truncated file",
            }
        )
    elif absent:
        report["findings"].append(
            {"kind": "text_layer", "detail": "no text on any sampled page (scan?)"}
        )
    stats = _image_stats(path)
    if stats is not None:
        report["image_count"] = stats["count"]
        if stats["weighted_ppi"] is not None:
            report["avg_dpi"] = stats["weighted_ppi"]
            if stats["weighted_ppi"] < 150:
                report["findings"].append(
                    {
                        "kind": "low_dpi",
                        "detail": f"area-weighted {stats['weighted_ppi']} dpi "
                        "(below 150: soft or screen-resolution scans)",
                    }
                )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description="The phase-1 PDF/DJVU battery: header, page count, "
        "qpdf --check (real-vs-benign), fonts, text layer, images. Read-only."
    )
    ap.add_argument("files", nargs="+", help="PDF/DJVU files to check")
    ap.add_argument("--json", metavar="FILE", help="also write the machine report")
    ap.add_argument(
        "--quiet", action="store_true", help="suppress the per-file progress lines"
    )
    args = ap.parse_args()

    reports = []
    for path in args.files:
        try:
            reports.append(check_file(path))
        except OSError as e:
            print(f"ERROR: {path}: {e}", file=sys.stderr)
            return 2
        report = reports[-1]
        if not args.quiet:
            print(
                f"{path}: {report['kind']}, {report.get('pages', '?')} page(s), "
                f"qpdf {report.get('qpdf_check', 'n/a')}, "
                f"{len(report['findings'])} finding(s)"
            )
    structural = sum(
        1
        for r in reports
        for f in r["findings"]
        if f["kind"] in ("header", "qpdf_errors")
    )
    print(f"\n{len(reports)} file(s) checked, {structural} structural finding(s).")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"files": reports, "structural": structural}, f, indent=1)
            f.write("\n")
    return 1 if structural else 0


if __name__ == "__main__":
    sys.exit(main())
