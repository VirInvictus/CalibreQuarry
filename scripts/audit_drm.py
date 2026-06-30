#!/usr/bin/env python3
"""
audit_drm.py: scan ebook files for DRM, across every format the library holds.

The metadata and structural audits never look at encryption: a DRM-locked file
can pass epubcheck, read its page count, and even import, yet refuse to let its
embedded metadata be rewritten (the canonical case was a z-library PDF carrying
a residual Adobe ADEPT EBX_HANDLER dictionary that qpdf and pdfinfo both called
"not encrypted", while exiftool choked on it). This tool fills that gap. It
reads only enough of each file to classify it and changes nothing.

The hard part is NOT detecting encryption; it is NOT crying wolf. Two benign
things look like DRM to a crude check and are explicitly cleared here:

  * font obfuscation  An EPUB may carry META-INF/encryption.xml that scrambles
                      ONLY its embedded fonts (the IDPF or Adobe font-mangling
                      algorithms). That is not DRM; the book is unprotected.
  * permission flags  A PDF may be "encrypted" with the Standard handler and an
                      empty user password: it opens with no password and is only
                      flagged against printing/copying. That is not a lock.

Per format:

  EPUB   META-INF/rights.xml (Adobe ADEPT) or sinf.xml (Apple FairPlay) => DRM.
         META-INF/encryption.xml => DRM only if it encrypts content (XHTML/CSS/
         OPF) or uses a non-font algorithm; font-only obfuscation is BENIGN.
  PDF    A non-Standard security handler (EBX_HANDLER, FOPN_foweb/fLock, the
         ADEPT namespace) found by a streaming byte scan => DRM, even when the
         dictionary is residual/inactive. An active Standard-handler encryption
         is classed with qpdf: requires a password => DRM; opens without one =>
         PERMISSIONS (benign). qpdf is optional; without it, Standard encryption
         is reported as ENCRYPTED-UNCLASSIFIED rather than guessed.
  MOBI   The PalmDOC/MOBI record-0 encryption-type field (0 none, 1/2
  /AZW3   Mobipocket DRM). Pure stdlib.
  DJVU   No DRM scheme in practice => reported N/A.

Companion to audit_epub.py (body text) and validate_metadata.py (catalogue);
like them it has a library mode (formats and paths from metadata.db, opened
strictly mode=ro) and a directory mode (recursive scan of loose files before
import), and the same exit codes.

Run from the library directory:
    python3 audit_drm.py                  # scan every file in the library
    python3 audit_drm.py ~/Downloads      # vet loose files before import
    python3 audit_drm.py --csv drm.csv    # also write a CSV audit

Exit codes:
    0 = clean (no DRM; font obfuscation and permission flags are not DRM)
    1 = DRM found, or a scan error
    2 = setup error (missing DB / library, or no ebook files in directory)
"""

import argparse
import csv
import os
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# ANSI colours; suppress when stdout isn't a TTY.
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

SUPPORTED_EXT = (".epub", ".pdf", ".azw3", ".mobi", ".azw", ".prc", ".djvu")

# Verdict status values.
CLEAN = "CLEAN"
DRM = "DRM"
BENIGN = "BENIGN"  # font obfuscation / permission flags: looks encrypted, isn't a lock
NA = "N/A"  # format that does not carry DRM (DJVU)
ERROR = "ERROR"

# EPUB font-obfuscation algorithms: encryption of fonts ONLY, not a content lock.
FONT_OBFUSCATION_ALGOS = {
    "http://www.idpf.org/2008/embedding",
    "http://ns.adobe.com/pdf/enc#rc",
}
FONT_EXTS = (".ttf", ".otf", ".woff", ".woff2", ".dfont", ".ttc")

# PDF third-party security-handler signatures (bytes). Their presence anywhere
# in the file, active or residual, means the file was DRM'd.
PDF_DRM_SIGNATURES = (
    b"EBX_HANDLER",  # Adobe ADEPT (Digital Editions)
    b"FOPN_foweb",  # FileOpen
    b"FOPN_fLock",  # FileOpen
    b"ns.adobe.com/adept",  # ADEPT rights namespace
)
_SIG_OVERLAP = max(len(s) for s in PDF_DRM_SIGNATURES) - 1


class Verdict:
    """One file's classification: a status, a short kind, and a detail line."""

    __slots__ = ("status", "kind", "detail")

    def __init__(self, status: str, kind: str = "", detail: str = ""):
        self.status = status
        self.kind = kind
        self.detail = detail

    @property
    def is_drm(self) -> bool:
        return self.status in (DRM, ERROR)


def resolve_library_root() -> Path | None:
    """The library root is wherever metadata.db sits: next to this script (the
    copy living inside the library) or the current working directory."""
    for d in (Path(__file__).resolve().parent, Path.cwd()):
        if (d / "metadata.db").is_file():
            return d
    return None


# --------------------------------------------------------------------------- #
# EPUB
# --------------------------------------------------------------------------- #
def _local(tag: str) -> str:
    """Strip an XML namespace: '{ns}EncryptedData' -> 'EncryptedData'."""
    return tag.rsplit("}", 1)[-1]


def _is_font_uri(uri: str) -> bool:
    """A font resource by extension or by living under a fonts/ directory.
    Obfuscated fonts are often named fonts/00001.dat with no font extension."""
    u = uri.lower()
    return u.endswith(FONT_EXTS) or "/fonts/" in u or u.startswith("fonts/")


def classify_epub(path: Path) -> Verdict:
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
            # Adobe ADEPT rights token / Apple FairPlay sinf: unambiguous DRM.
            if "META-INF/rights.xml" in names:
                return Verdict(DRM, "Adobe ADEPT", "META-INF/rights.xml present")
            if any(n.lower().endswith("sinf.xml") for n in names):
                return Verdict(DRM, "Apple FairPlay", "sinf.xml present")
            if "META-INF/encryption.xml" not in names:
                return Verdict(CLEAN)
            raw = z.read("META-INF/encryption.xml")
            return _classify_encryption_xml(raw)
    except zipfile.BadZipFile as e:
        return Verdict(ERROR, "unreadable", f"bad zip: {e}")
    except Exception as e:  # noqa: BLE001 - report, don't crash the scan
        return Verdict(ERROR, "unreadable", f"{type(e).__name__}: {e}")


def _classify_encryption_xml(raw: bytes) -> Verdict:
    """encryption.xml is DRM unless every encrypted entry is font obfuscation."""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        # Malformed: if it names a content document, treat as DRM; else flag it.
        low = raw.lower()
        if b".xhtml" in low or b".html" in low or b".opf" in low:
            return Verdict(DRM, "encrypted content", "unparseable encryption.xml")
        return Verdict(DRM, "unknown", "unparseable encryption.xml")

    entries = []  # (algorithm, uri)
    for data in root.iter():
        if _local(data.tag) != "EncryptedData":
            continue
        algo, uri = "", ""
        for el in data.iter():
            name = _local(el.tag)
            if name == "EncryptionMethod":
                algo = (el.get("Algorithm") or "").strip()
            elif name == "CipherReference":
                uri = (el.get("URI") or "").strip()
        entries.append((algo.lower(), uri))

    if not entries:
        return Verdict(BENIGN, "empty", "encryption.xml lists nothing")

    content_hits = []
    for algo, uri in entries:
        # An entry is benign font obfuscation if it uses a font-scrambling
        # algorithm (IDPF/Adobe #RC are font-only by spec) OR targets a font
        # resource. The target test matters because publishers name obfuscated
        # fonts fonts/00001.dat (no font extension), which an algorithm- or
        # extension-only check would misread as an encrypted content document.
        if algo in FONT_OBFUSCATION_ALGOS or _is_font_uri(uri):
            continue
        content_hits.append(uri or algo or "?")

    if not content_hits:
        return Verdict(
            BENIGN,
            "font obfuscation",
            f"{len(entries)} font(s) obfuscated, no content lock",
        )
    sample = ", ".join(content_hits[:3])
    return Verdict(
        DRM, "encrypted content", f"{len(content_hits)} non-font entr(y/ies): {sample}"
    )


# --------------------------------------------------------------------------- #
# PDF
# --------------------------------------------------------------------------- #
def _scan_pdf_signatures(path: Path) -> bytes | None:
    """Stream the file; return the first DRM handler signature found, or None."""
    try:
        with path.open("rb") as f:
            tail = b""
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    break
                window = tail + chunk
                for sig in PDF_DRM_SIGNATURES:
                    if sig in window:
                        return sig
                tail = window[-_SIG_OVERLAP:]
    except OSError:
        return None
    return None


def _qpdf_classify_standard(path: Path) -> Verdict | None:
    """For active Standard-handler encryption: password-locked (DRM) vs
    permission-flags-only (benign). Returns None if qpdf can't be used."""
    qpdf = shutil.which("qpdf")
    if not qpdf:
        return None
    try:
        enc = subprocess.run(
            [qpdf, "--is-encrypted", str(path)], capture_output=True, timeout=60
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    # --is-encrypted: 0 encrypted, 2 not encrypted, 3 not a pdf / error.
    if enc.returncode != 0:
        return Verdict(CLEAN)
    try:
        needs = subprocess.run(
            [qpdf, "--requires-password", str(path)], capture_output=True, timeout=60
        )
    except OSError, subprocess.TimeoutExpired:
        return None
    # --requires-password: 0 a password is required, 3 it is not.
    if needs.returncode == 0:
        return Verdict(DRM, "password-locked", "PDF requires a password to open")
    return Verdict(
        BENIGN, "permissions", "Standard encryption, opens without a password"
    )


def classify_pdf(path: Path) -> Verdict:
    sig = _scan_pdf_signatures(path)
    if sig is not None:
        name = sig.decode("ascii", "replace")
        handler = {
            "EBX_HANDLER": "Adobe ADEPT",
            "ns.adobe.com/adept": "Adobe ADEPT",
            "FOPN_foweb": "FileOpen",
            "FOPN_fLock": "FileOpen",
        }.get(name, name)
        return Verdict(DRM, handler, f"security handler signature {name!r}")
    verdict = _qpdf_classify_standard(path)
    if verdict is not None:
        return verdict
    # qpdf unavailable: fall back to a trailer /Encrypt check so we don't claim
    # clean on a standard-encrypted file we couldn't classify.
    if _pdf_has_encrypt_dict(path):
        return Verdict(
            BENIGN,
            "encrypted-unclassified",
            "/Encrypt present; install qpdf to classify",
        )
    return Verdict(CLEAN)


def _pdf_has_encrypt_dict(path: Path) -> bool:
    """Cheap check for a trailer /Encrypt reference near the file's end."""
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - (64 << 10)))
            return b"/Encrypt" in f.read()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# Kindle (MOBI / AZW3 / AZW / PRC) - PalmDB container
# --------------------------------------------------------------------------- #
def classify_mobi(path: Path) -> Verdict:
    try:
        with path.open("rb") as f:
            header = f.read(78)
            if len(header) < 78:
                return Verdict(ERROR, "unreadable", "truncated PalmDB header")
            (num_records,) = struct.unpack(">H", header[76:78])
            if num_records < 1:
                return Verdict(ERROR, "unreadable", "no PalmDB records")
            # First record-info entry (8 bytes) starts at offset 78; its first
            # uint32 is the byte offset of record 0.
            entry = f.read(8)
            if len(entry) < 4:
                return Verdict(ERROR, "unreadable", "truncated record list")
            (rec0_off,) = struct.unpack(">I", entry[:4])
            f.seek(rec0_off)
            rec0 = f.read(16)
            if len(rec0) < 14:
                return Verdict(ERROR, "unreadable", "truncated record 0")
            (enc_type,) = struct.unpack(">H", rec0[12:14])
    except OSError as e:
        return Verdict(ERROR, "unreadable", str(e))
    if enc_type == 0:
        return Verdict(CLEAN)
    if enc_type == 1:
        return Verdict(DRM, "Mobipocket", "legacy Mobipocket encryption (type 1)")
    if enc_type == 2:
        return Verdict(DRM, "Mobipocket", "Mobipocket DRM (type 2)")
    return Verdict(DRM, "Mobipocket", f"unknown encryption type {enc_type}")


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #
def classify_file(path: Path) -> Verdict:
    ext = path.suffix.lower()
    if ext == ".epub":
        return classify_epub(path)
    if ext == ".pdf":
        return classify_pdf(path)
    if ext in (".azw3", ".mobi", ".azw", ".prc"):
        return classify_mobi(path)
    if ext == ".djvu":
        return Verdict(NA, "djvu", "DJVU has no DRM scheme")
    return Verdict(NA, "unsupported", f"no DRM check for {ext}")


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def _color(status: str) -> str:
    return {DRM: RED, ERROR: RED, BENIGN: YELLOW, CLEAN: GREEN, NA: GREEN}.get(
        status, ""
    )


def _print_row(label: str, verdict: Verdict) -> None:
    c = _color(verdict.status)
    kind = (
        f" [{verdict.kind}]"
        if verdict.kind and verdict.status in (DRM, BENIGN, ERROR)
        else ""
    )
    print(f"  {c}{verdict.status:<7}{RESET}{kind} {label}")
    if verdict.detail and verdict.status in (DRM, ERROR):
        print(f"      {verdict.detail}")


def _summary(drm, benign, errors, scanned) -> int:
    print()
    if drm or errors:
        print(
            f"{RED}{BOLD}FOUND{RESET}: {drm} DRM-locked file(s), "
            f"{errors} scan error(s); {benign} benign-encrypted, {scanned} scanned."
        )
        return 1
    note = f" ({benign} benign font/permission encryption)" if benign else ""
    print(f"{GREEN}{BOLD}CLEAN{RESET}: no DRM in {scanned} file(s){note}.")
    return 0


def _write_csv(csv_path: Path, rows: list[tuple]) -> None:
    with csv_path.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(("id", "status", "kind", "detail", "path"))
        for bid, status, kind, detail, p in rows:
            w.writerow((bid, status, kind, detail, p))


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
def run_directory(directory: Path, csv_path: Path | None) -> int:
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory.")
        return 2
    files = sorted(p for p in directory.rglob("*") if p.suffix.lower() in SUPPORTED_EXT)
    if not files:
        print(f"No ebook files found under {directory}")
        return 2
    print(f"Scanning {len(files)} file(s) in {directory} for DRM\n")
    drm = benign = errors = scanned = 0
    csv_rows = []
    for path in files:
        verdict = classify_file(path)
        if verdict.status == NA:
            continue
        scanned += 1
        if verdict.status == DRM:
            drm += 1
        elif verdict.status == BENIGN:
            benign += 1
        elif verdict.status == ERROR:
            errors += 1
        if verdict.status in (DRM, BENIGN, ERROR):
            _print_row(path.name, verdict)
        csv_rows.append(("", verdict.status, verdict.kind, verdict.detail, str(path)))
    if csv_path:
        _write_csv(csv_path, csv_rows)
        print(f"\nWrote {csv_path}")
    return _summary(drm, benign, errors, scanned)


def run_library(csv_path: Path | None) -> int:
    import sqlite3

    library_root = resolve_library_root()
    if library_root is None:
        print(
            "ERROR: no metadata.db next to this script or in the current "
            "directory. Run from the library directory."
        )
        return 2
    db_path = library_root / "metadata.db"
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    cur = con.cursor()
    rows = cur.execute(
        "SELECT b.id, b.title, b.path, d.name, d.format FROM data d "
        "JOIN books b ON b.id = d.book ORDER BY b.id"
    ).fetchall()
    con.close()

    print(f"Scanning {len(rows)} file(s) across the library for DRM\n")
    drm = benign = errors = scanned = 0
    csv_rows = []
    for book_id, title, path, name, fmt in rows:
        full = library_root / path / f"{name}.{fmt.lower()}"
        if not full.is_file():
            errors += 1
            v = Verdict(ERROR, "missing", "file not found")
            _print_row(f"#{book_id} {title}", v)
            csv_rows.append((book_id, v.status, v.kind, v.detail, str(full)))
            continue
        verdict = classify_file(full)
        if verdict.status == NA:
            continue
        scanned += 1
        if verdict.status == DRM:
            drm += 1
        elif verdict.status == BENIGN:
            benign += 1
        elif verdict.status == ERROR:
            errors += 1
        if verdict.status in (DRM, BENIGN, ERROR):
            _print_row(f"#{book_id} [{fmt}] {title}", verdict)
        csv_rows.append(
            (book_id, verdict.status, verdict.kind, verdict.detail, str(full))
        )
    if csv_path:
        _write_csv(csv_path, csv_rows)
        print(f"\nWrote {csv_path}")
    return _summary(drm, benign, errors, scanned)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan ebook files (EPUB/PDF/MOBI/AZW3) for DRM, clearing "
        "benign font obfuscation and permission flags."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="scan loose files under this directory instead of the library",
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="also write a CSV audit (id,status,kind,detail,path)",
    )
    args = parser.parse_args()
    csv_path = Path(args.csv).expanduser() if args.csv else None
    if args.directory:
        return run_directory(Path(args.directory).expanduser(), csv_path)
    return run_library(csv_path)


if __name__ == "__main__":
    sys.exit(main())
