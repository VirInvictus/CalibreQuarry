#!/usr/bin/env python3
"""
audit_epub_content.py: read the actual text of every EPUB and flag files
whose content is not English (wrong-language editions) or that carry an
injected foreign-language ad-notice. Metadata cannot catch these: the
offenders here all declared lang=eng while their bodies were Portuguese,
Italian, Dutch, Arabic, or Russian.

Companion to validate_library.py. The validator audits metadata against
taxonomy.yaml; this audits EPUB *content*, so it is deliberately separate
(it decompresses and scans ~10 GB of text and takes a few minutes).

Run from the library directory:
    python3 audit_epub_content.py              # audit the whole library (DB-driven)
    python3 audit_epub_content.py ~/Downloads  # vet loose .epub files before import

Library mode pulls the EPUB list from metadata.db and uses each book's tag
and declared language to separate unexpected foreign content from
expected-foreign books. Directory mode has no such context, so it reports a
verdict for every .epub it finds (recursively): the workflow for checking
replacement downloads before they enter the library.

Exit codes:
    0 = clean (no unexpected foreign content or injection signatures)
    1 = foreign-language content or injection signature found
    2 = setup error (missing DB / library, or no .epub files in directory)

Add to the periodic maintenance pass (e.g. after an import batch).

Method (stdlib only, no language-detection dependency):
  - strip <style>/<script> before tag-stripping (raw CSS otherwise reads
    as prose and poisons the language signal)
  - read spine documents in order up to CAP chars, plus the final spine
    document (back matter, where injected notices tend to live)
  - count non-Latin Unicode-block letters anywhere in the text
  - vote a language across EN/DE/FR/ES/IT/PT/NL stopword sets; on
    book-length text this is decisive (confirmed foreign editions scored
    0.27-0.36 for the winning language)
  - grep for the importknig / Книжный импорт injection signature
A book is "expected-foreign" (reported but not a failure) when its tag is
under NonFic.Language.* or its declared language is not English.
"""

import argparse
import os
import re
import sqlite3
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET


def resolve_library_root() -> Path | None:
    """The library root is wherever metadata.db sits: next to this script (the
    copy living inside the library) or the current working directory (running
    the repo copy from inside a library), in that order."""
    for d in (Path(__file__).resolve().parent, Path.cwd()):
        if (d / "metadata.db").is_file():
            return d
    return None


# ANSI colours; suppress when stdout isn't a TTY (matches validate_library.py)
USE_COLOR = sys.stdout.isatty()
RED = "\033[31m" if USE_COLOR else ""
YELLOW = "\033[33m" if USE_COLOR else ""
GREEN = "\033[32m" if USE_COLOR else ""
BOLD = "\033[1m" if USE_COLOR else ""
RESET = "\033[0m" if USE_COLOR else ""

CAP = 400_000  # clean chars read per book; ample for a language verdict

CONTAINER_NS = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
OPF_NS = "{http://www.idpf.org/2007/opf}"
STYLE_RE = re.compile(r"<(style|script)\b[^>]*>.*?</\1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"[a-zA-ZàâäéèêëïîôöùûüçñáíóúãõßÀ-ÿ']+")
SIGNATURE_RE = re.compile(r"importknig|книжный импорт|knizhny", re.I)

# Distinctive stopword sets. Book-length text makes the vote unambiguous;
# the small EN/IT overlap on "i" etc. is swamped by the rest.
STOPWORDS: dict[str, set[str]] = {
    "en": set(
        "the of and to a in that is was for it with as his on be at by he this had not are but from or have an they which one you were her all she there would their".split()
    ),
    "de": set(
        "der die und in den von zu das mit sich des auf für ist im dem nicht ein eine als auch es an werden aus er hat dass sie nach wird bei einer um".split()
    ),
    "fr": set(
        "le la les de des un une et en dans que qui pour pas sur au avec ce il ne se plus par je nous vous est son ses aux".split()
    ),
    "es": set(
        "el la los las de un una y en que no se con por para es su lo como más pero sus le ya o este sí porque esta entre".split()
    ),
    "it": set(
        "il lo la i gli le di un uno una e che non per con su come più ma anche da sono mi si nel alla dei delle".split()
    ),
    "pt": set(
        "o a os as de um uma e que do da em não se com por para mais mas como ao dos das na no à seu".split()
    ),
    "nl": set(
        "de het een en van te dat die in is op ik niet met zijn er maar om ook als voor naar dan zou hij heeft".split()
    ),
}


def script_of(codepoint: int) -> str | None:
    if 0x0400 <= codepoint <= 0x04FF:
        return "Cyrillic"
    if 0x4E00 <= codepoint <= 0x9FFF:
        return "CJK-Han"
    if 0x3040 <= codepoint <= 0x30FF:
        return "Japanese-kana"
    if 0xAC00 <= codepoint <= 0xD7A3:
        return "Korean"
    if 0x0600 <= codepoint <= 0x06FF:
        return "Arabic"
    if 0x0370 <= codepoint <= 0x03FF:
        return "Greek"
    if 0x0590 <= codepoint <= 0x05FF:
        return "Hebrew"
    if 0x0900 <= codepoint <= 0x097F:
        return "Devanagari"
    return None


def clean_text(z: zipfile.ZipFile, name: str) -> str:
    try:
        raw = z.read(name).decode("utf-8", "replace")
    except Exception:
        raw = z.read(name).decode("latin-1", "replace")
    raw = STYLE_RE.sub(" ", raw)
    return WS_RE.sub(" ", TAG_RE.sub(" ", raw))


def scan(path: Path) -> dict:
    """Read a book's text and return its language/script signal."""
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        container = ET.fromstring(z.read("META-INF/container.xml"))
        rootfile = container.find(".//c:rootfile", CONTAINER_NS)
        opf_path = rootfile.get("full-path") if rootfile is not None else None
        if not opf_path:
            raise ValueError("container.xml has no rootfile")
        opf = ET.fromstring(z.read(opf_path))
        base = os.path.dirname(opf_path)
        manifest = {it.get("id"): it.get("href") for it in opf.iter(OPF_NS + "item")}

        lang = ""
        for el in opf.iter():
            if el.tag.endswith("}language") and el.text:
                lang = el.text.strip().lower()
                break

        docs: list[str] = []
        for itemref in opf.iter(OPF_NS + "itemref"):
            href = manifest.get(itemref.get("idref"))
            if not href:
                continue
            full = os.path.normpath(f"{base}/{href}" if base else href).replace(
                "\\", "/"
            )
            if full in names:
                docs.append(full)

        parts: list[str] = []
        total = 0
        for doc in docs:
            if total >= CAP:
                break
            text = clean_text(z, doc)
            parts.append(text)
            total += len(text)
        if docs and docs[-1] not in docs[: len(parts)]:
            parts.append(clean_text(z, docs[-1]))

    text = " ".join(parts)
    # Count scripts over the first 250k letters in a single pass, instead of
    # materializing a list of every letter's codepoint in a book-length string.
    scripts: Counter = Counter()
    total_letters = 0
    for c in text:
        if c.isalpha():
            total_letters += 1
            s = script_of(ord(c))
            if s:
                scripts[s] += 1
            if total_letters >= 250_000:
                break
    nonlatin = sum(scripts.values())
    total_letters = total_letters or 1
    words = WORD_RE.findall(text.lower())[:5000]
    ratios = {
        code: (sum(w in stops for w in words) / len(words) if words else 0.0)
        for code, stops in STOPWORDS.items()
    }
    best = max(ratios, key=lambda c: ratios[c])
    return {
        "lang": lang,
        "scripts": dict(scripts),
        "nonlatin": nonlatin,
        "nonlatin_frac": nonlatin / total_letters,
        "ratios": ratios,
        "best": best,
        "nwords": len(words),
        "signature": bool(SIGNATURE_RE.search(text)),
    }


def findings(r: dict) -> list[tuple[str, str]]:
    """Classify a scan result into [(category, detail)]; empty = English and clean."""
    out: list[tuple[str, str]] = []
    if r["nonlatin"] >= 150 and r["nonlatin_frac"] > 0.02:
        top = max(r["scripts"], key=lambda s: r["scripts"][s])
        out.append(
            (
                "NON-LATIN SCRIPT",
                f"{r['nonlatin_frac'] * 100:.0f}% {top} ({r['nonlatin']} non-Latin letters)",
            )
        )
    if (
        r["best"] != "en"
        and r["nwords"] >= 400
        and r["ratios"][r["best"]] > 0.06
        and r["ratios"][r["best"]] > 1.3 * r["ratios"]["en"]
    ):
        top3 = ", ".join(
            f"{c}={v:.2f}"
            for c, v in sorted(r["ratios"].items(), key=lambda x: -x[1])[:3]
        )
        out.append(("LATIN-SCRIPT FOREIGN", f"looks {r['best'].upper()} [{top3}]"))
    if r["signature"]:
        out.append(
            ("INJECTION SIGNATURE", "importknig / Книжный импорт signature present")
        )
    return out


def audit_library() -> int:
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
    # Aggregate ALL tags / languages per book. A plain dict() would keep only the
    # last row, so a multi-tagged book (e.g. NonFic.Language.* plus a genre tag)
    # could be misclassified as unexpected-foreign.
    booktags: dict[int, list[str]] = {}
    for bid, tname in cur.execute(
        "SELECT bt.book, t.name FROM books_tags_link bt JOIN tags t ON t.id = bt.tag"
    ):
        booktags.setdefault(bid, []).append(tname)
    declared: dict[int, set[str]] = {}
    for bid, lang in cur.execute(
        "SELECT bl.book, l.lang_code FROM books_languages_link bl "
        "JOIN languages l ON l.id = bl.lang_code"
    ):
        declared.setdefault(bid, set()).add(lang)
    rows = cur.execute(
        "SELECT b.id, b.title, b.path, d.name FROM data d "
        "JOIN books b ON b.id = d.book WHERE d.format = 'EPUB'"
    ).fetchall()
    con.close()

    # (book_id, title, tag, expected, detail)
    nonlatin_hits: list[tuple] = []
    latin_foreign: list[tuple] = []
    signature_hits: list[tuple] = []
    errors: list[tuple] = []
    scanned = 0

    for book_id, title, path, name in rows:
        full = library_root / path / f"{name}.epub"
        tags = booktags.get(book_id, [])
        tag = tags[0] if tags else "?"
        langs = declared.get(book_id, set())
        decl = ",".join(sorted(langs)) if langs else "?"
        expected = any(lang != "eng" for lang in langs) or any(
            t.startswith("NonFic.Language.") for t in tags
        )
        try:
            r = scan(full)
        except Exception as e:
            errors.append((book_id, title, tag, f"{type(e).__name__}: {e}"))
            continue
        scanned += 1

        for category, detail in findings(r):
            if category == "NON-LATIN SCRIPT":
                nonlatin_hits.append(
                    (book_id, title, tag, expected, f"{detail}; declared={decl}")
                )
            elif category == "LATIN-SCRIPT FOREIGN":
                latin_foreign.append(
                    (book_id, title, tag, expected, f"{detail}; declared={decl}")
                )
            else:
                signature_hits.append((book_id, title, tag, expected, detail))

    return report(
        scanned, nonlatin_hits, latin_foreign, signature_hits, errors, library_root
    )


def report(
    scanned, nonlatin_hits, latin_foreign, signature_hits, errors, library_root
) -> int:
    def show(label: str, hits: list[tuple], color: str) -> int:
        unexpected = [h for h in hits if not h[3]]
        expected = [h for h in hits if h[3]]
        if hits:
            print(f"{color}{BOLD}{label} ({len(hits)}){RESET}")
            for book_id, title, tag, _exp, detail in sorted(unexpected):
                print(f"  {RED}#{book_id}{RESET} [{tag}] {title}")
                print(f"    {detail}")
            for book_id, title, tag, _exp, detail in sorted(expected):
                print(f"  #{book_id} [{tag}] {title}  {GREEN}(expected-foreign){RESET}")
                print(f"    {detail}")
            print()
        return len(unexpected)

    print(f"Scanned {scanned} EPUBs in {library_root}\n")
    unexpected = 0
    unexpected += show("NON-LATIN SCRIPT", nonlatin_hits, RED)
    unexpected += show("LATIN-SCRIPT FOREIGN (stopword vote)", latin_foreign, RED)
    # an injection signature is always actionable regardless of tag
    unexpected += show("INJECTION SIGNATURE", signature_hits, YELLOW)

    if errors:
        print(f"{YELLOW}{BOLD}SCAN ERRORS ({len(errors)}){RESET}")
        for book_id, title, tag, msg in errors:
            print(f"  #{book_id} [{tag}] {title}")
            print(f"    {msg}")
        print()

    if unexpected == 0 and not signature_hits:
        print(
            f"{GREEN}{BOLD}CLEAN{RESET}: no unexpected foreign content or injection signatures."
        )
        return 0 if not errors else 1
    print(
        f"{RED}{BOLD}FOUND{RESET}: {unexpected} file(s) need review "
        f"(replace wrong-language editions with English copies)."
    )
    return 1


def audit_directory(directory: Path) -> int:
    """Vet loose .epub files (recursively) before they enter the library."""
    if not directory.is_dir():
        print(f"ERROR: {directory} is not a directory.")
        return 2
    epubs = sorted(directory.rglob("*.epub"))
    if not epubs:
        print(f"No .epub files found under {directory}")
        return 2

    print(f"Auditing {len(epubs)} EPUB(s) in {directory}\n")
    problems = 0
    errors = 0
    for path in epubs:
        try:
            hits = findings(scan(path))
        except Exception as e:
            print(f"  {YELLOW}ERROR {RESET} {path.name}\n      {type(e).__name__}: {e}")
            errors += 1
            continue
        if not hits:
            print(f"  {GREEN}OK    {RESET} {path.name}  (English, clean)")
        else:
            problems += 1
            print(f"  {RED}REVIEW{RESET} {path.name}")
            for category, detail in hits:
                print(f"      {category}: {detail}")
    print()

    if problems == 0 and errors == 0:
        print(
            f"{GREEN}{BOLD}CLEAN{RESET}: all {len(epubs)} file(s) look English with no injection signatures."
        )
        return 0
    print(
        f"{RED}{BOLD}FOUND{RESET}: {problems} file(s) need review, {errors} scan error(s)."
    )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit EPUB content for non-English text or injected foreign notices."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        help="vet loose .epub files under this directory instead of auditing the library",
    )
    args = parser.parse_args()
    if args.directory:
        return audit_directory(Path(args.directory).expanduser())
    return audit_library()


if __name__ == "__main__":
    sys.exit(main())
