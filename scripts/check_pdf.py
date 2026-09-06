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
  text_layer    pdftotext sample of page 1: present / absent / unavailable
  images        pdfimages -list: image count

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

_BENIGN_QPDF = "operation succeeded with warnings"


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


def classify_qpdf(output: str, returncode: int | None) -> str:
    """qpdf --check's three real classes: warnings are benign (qpdf exits
    3 for warning-only files constantly), errors are not."""
    if returncode is None:
        return "unavailable"
    if returncode == 0:
        return "clean"
    if returncode == 3 and _BENIGN_QPDF in output:
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


def _has_text_layer(path: str) -> str:
    proc = _run(["pdftotext", "-l", "1", path, "-"])
    if proc is None or proc.returncode != 0:
        return "unavailable"
    return "present" if proc.stdout.strip() else "absent"


def _image_count(path: str) -> int | None:
    proc = _run(["pdfimages", "-list", path])
    if proc is None or proc.returncode != 0:
        return None
    return max(0, len(proc.stdout.splitlines()) - 2)


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
    verdict = classify_qpdf(
        proc.stdout if proc else "", proc.returncode if proc else None
    )
    report["qpdf_check"] = verdict
    if verdict == "errors":
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        report["findings"].append(
            {"kind": "qpdf_errors", "detail": detail[0][:120] if detail else ""}
        )
    elif verdict == "warnings":
        report["findings"].append({"kind": "qpdf_warnings", "detail": "benign"})

    pages = _page_count_pdf(path)
    report["pages"] = pages
    fonts = _unembedded_fonts(path)
    if fonts is not None:
        report["unembedded_fonts"] = fonts
        if fonts:
            report["findings"].append(
                {"kind": "unembedded_fonts", "detail": f"{fonts} font(s) not embedded"}
            )
    layer = _has_text_layer(path)
    report["text_layer"] = layer
    if layer == "absent":
        report["findings"].append(
            {"kind": "text_layer", "detail": "no text on page 1 (scan?)"}
        )
    images = _image_count(path)
    if images is not None:
        report["image_count"] = images
    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description="The phase-1 PDF/DJVU battery: header, page count, "
        "qpdf --check (real-vs-benign), fonts, text layer, images. Read-only."
    )
    ap.add_argument("files", nargs="+", help="PDF/DJVU files to check")
    ap.add_argument("--json", metavar="FILE", help="also write the machine report")
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
