"""`run cover-source`: source covers for coverless books (issue #45).

The 2026-10-31 TTRPG and 2026-11-01 CofD waves arrived as PDFs with no
catalogued cover (issue #34's extractor is EPUB-only by design) and were
fixed by an agent-side pipeline proven across both waves. This verb is
that pipeline: it walks every coverless book and applies one ladder, in
the issue's order, until a rung yields cover bytes:

1. **declared-epub** -- `coverfind.declared_epub_cover()` over the book's
   EPUB (its own declared cover; catches pre-3.72.0 verb imports that
   predate the phase-2 cover pass).
2. **page1-render** -- PDFs: `pdftoppm -f 1 -l 1 -r 150 -png -singlefile`,
   then the gates. A render is catalogued only when it survives BOTH:
   the blank gate (stdlib PNG decode; a page whose modal quantized color
   covers 95%+ of the sampled grid is an empty or near-empty sheet or a
   sparse half-title, not cover art; a fully typeset title page still
   passes, the gate's named residual) and the wrapper-marker gate
   (pdftotext page 1 over the curated document-mill host list). A
   landscape render is declined as a
   likely front/back cover spread unless --spread-crop, which takes the
   right half by a SECOND render cropped in RENDER pixels measured from
   the PNG's IHDR -- the manual pipeline's recorded lesson ("measure the
   render, not the points": a points-based crop landed on the wrong half).
3. **sibling** -- exactly one other book sharing a case-folded author
   whose has_cover is truthy donates its catalogued cover (the indie
   two-volume shape: rules volume + art volume, the art volume's true
   cover living on the sibling's page 1). Zero covered siblings is a
   silent miss; two or more is a declined ambiguity.
4. **url** -- an explicit --url ID=URL overrides the ladder for that
   book (news's explicit-over-curated rule): the URL is fetched (network
   at --apply ONLY; the dry run plans it), direct image bytes are
   sniffed, and an HTML product page has its og:image/twitter:image
   extracted and fetched in a second request. No search API, no
   scraping beyond the one URL supplied.

Recorded trades: the ladder is render-first, so a busy art-volume page 1
that survives the gates beats the sibling rung -- every row names its
rung and source, a unique covered sibling is noted beside the render
candidate, and a wrong write is one `run cover --remove-cover` away.
The gates fail toward skipping (a declined book joins the report's
worklist), never toward writing a suspected junk cover.

The rails are the integration verbs': dry run by default (the offline
ladder is fully EVALUATED -- the renders are read-only spawns over the
files, the phase-1 ebook-meta precedent -- so the plan shows what would
be written, not what might), --apply demands a closed Calibre and a
--backup-dir outside the library, targets resolve read-only first,
usage problems exit 2, a live Calibre exits 1, and failed rows exit 1.
Declines are never failures: they are the worklist the report names.
"""

from __future__ import annotations

import http.client
import html
import json
import re
import struct
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zlib

from cquarry.helpers import sniff_image_format
from cquarry.write import WritableCalibreDB

from cquarry_cli.coverfind import declared_epub_cover

#: The page-1 render resolution (the manual pipeline's proven value).
_RENDER_DPI = 150

#: A page whose modal quantized color covers at least this share of the
#: sampled grid is an empty or near-empty sheet (or a sparse half-title),
#: not cover art. The code-review pass measured real renders: a dense
#: body-text page scores ~0.87 and a 12-line typeset title page ~0.985,
#: so 0.95 declines the sparse shapes while a fully typeset or dense page
#: still passes (the gate's named residual: the dry run is where a human
#: sees it). A 0.99 cut kept title pages; below ~0.9 the gate starts
#: eating legitimately flat cover art.
_BLANK_MODAL_SHARE = 0.95

#: The document-mill hosts whose download banners render on page 1 (the
#: ilide/pdfcoffee shapes the issue names, plus the siblings the same
#: rips surface). Grows as shapes do (the _IMPRINT_TAG precedent). A hit
#: declines the render rung: the cost of a false positive is one skipped
#: auto-cover, never a wrong one.
_WRAPPER_MARKERS: tuple[str, ...] = (
    "ilide",
    "pdfcoffee",
    "vdoc.pub",
    "dokumen.pub",
    "idoc.pub",
    "pdfslide",
    "123docz",
    "kipdf",
    "docplayer",
    "studylib",
)

#: The per-URL ceiling. A cover image is kilobytes; a product page plus
#: its og:image is a handful of requests. --timeout overrides (shared
#: with news, whose default is much larger because editions are huge).
_DEFAULT_URL_TIMEOUT = 60

#: A fetched body larger than this is not a cover (a wrong URL that
#: downloaded an album or a full edition is cut off, not stored).
_MAX_FETCH_BYTES = 32 * 1024 * 1024

#: Retail and publisher sites 403 the default Python-urllib agent;
#: calibre's own recipes carry browser user-agents for the same reason.
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "cquarry cover-source"
)

_OG_IMAGE_RE = re.compile(
    r"<meta[^>]+(?:property|name)=[\"'](?:og:image|twitter:image)[\"'][^>]+"
    r"content=[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
# The attribute order is sometimes reversed (content before property).
_OG_IMAGE_ALT_RE = re.compile(
    r"<meta[^>]+content=[\"']([^\"']+)[\"'][^>]+"
    r"(?:property|name)=[\"'](?:og:image|twitter:image)[\"']",
    re.IGNORECASE,
)


class _FetchError(Exception):
    """One URL rung failure (a report row's detail, never a traceback)."""


# --- the page-1 render (PDF rung) -----------------------------------

#: The spawns are module seams: the tests mock these two, the live class
#: exercises the real binaries (skipUnless they are installed; CI has no
#: poppler).


def _run_pdftoppm(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=300
    )


def _run_pdftotext(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", timeout=120
    )


def _render_page1(pdf: str, workdir: str, crop: tuple[int, int] | None = None):
    """One pdftoppm page-1 render; (bytes, detail) where bytes is None on
    any failure and detail names it. `crop` is (x_offset, width) in RENDER
    pixels -- the spread-crop's second pass, measured from the first
    render's IHDR (the manual pipeline's recorded lesson: never from the
    PDF's points)."""
    prefix = f"{workdir}/page1"
    cmd = [
        "pdftoppm",
        "-f",
        "1",
        "-l",
        "1",
        "-r",
        str(_RENDER_DPI),
        "-png",
        "-singlefile",
    ]
    if crop is not None:
        cmd += ["-x", str(crop[0]), "-W", str(crop[1])]
    cmd += [pdf, prefix]
    try:
        proc = _run_pdftoppm(cmd)
    except subprocess.TimeoutExpired:
        return None, "pdftoppm timed out"
    except OSError as e:
        return None, f"pdftoppm failed: {e}"
    out = f"{prefix}.png"
    if proc.returncode != 0:
        # Squashed: poppler's syntax errors are multi-line and the row
        # layout is one line per book.
        return None, " ".join((proc.stderr or "pdftoppm failed").split())[:160]
    try:
        with open(out, "rb") as f:
            return f.read(), ""
    except OSError as e:
        return None, f"render unreadable: {e}"


# --- the blank gate (stdlib PNG walk) --------------------------------


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    """(width, height) from a PNG's IHDR, or None. This is "measure the
    render, not the points": the spread crop's geometry comes from the
    rendered bytes themselves (cquarry's get_image_size is the same
    answer for a path; the render is already in memory here)."""
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except struct.error:
        return None
    return width, height


def _png_samples(data: bytes) -> list[tuple[int, int, int]] | None:
    """A grid of RGB samples from an 8-bit non-interlaced PNG (palette
    and gray expanded, alpha dropped). None when the bytes are not a PNG
    this walker understands (16-bit, interlaced, truncated, not a PNG):
    the blank gate then stands down rather than guessing."""
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    width = height = None
    palette: list[tuple[int, int, int]] = []
    idat: list[bytes] = []
    pos = 8
    try:
        while pos + 8 <= len(data):
            (length,) = struct.unpack(">I", data[pos : pos + 4])
            ctype = data[pos + 4 : pos + 8]
            body = data[pos + 8 : pos + 8 + length]
            pos += 12 + length
            if ctype == b"IHDR":
                width, height, depth, color, _comp, _filt, interlace = struct.unpack(
                    ">IIBBBBB", body
                )
                if depth != 8 or interlace != 0 or color not in (0, 2, 3, 4, 6):
                    return None
            elif ctype == b"PLTE":
                palette = [
                    (body[i], body[i + 1], body[i + 2])
                    for i in range(0, len(body) - 2, 3)
                ]
            elif ctype == b"IDAT":
                idat.append(body)
            elif ctype == b"IEND":
                break
        if not width or not height or not idat:
            return None
        raw = zlib.decompress(b"".join(idat))
    except struct.error, zlib.error, ValueError:
        return None

    bpp = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    stride = width * bpp

    def _paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        if pb <= pc:
            return b
        return c

    # Unfilter in place, row by row (filters: None, Sub, Up, Average,
    # Paeth). The full walk is required -- Sub/Average mix leftward
    # bytes, so the sampled grid cannot skip the sequential repair.
    expected = (stride + 1) * height
    if len(raw) < expected:
        return None
    prior = bytearray(stride)
    step = max(1, min(width, height) // 64)
    samples: list[tuple[int, int, int]] = []
    off = 0
    for y in range(height):
        ftype = raw[off]
        row = bytearray(raw[off + 1 : off + 1 + stride])
        off += 1 + stride
        if ftype == 1:
            for i in range(bpp, stride):
                row[i] = (row[i] + row[i - bpp]) & 0xFF
        elif ftype == 2:
            for i in range(stride):
                row[i] = (row[i] + prior[i]) & 0xFF
        elif ftype == 3:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + ((left + prior[i]) >> 1)) & 0xFF
        elif ftype == 4:
            for i in range(stride):
                left = row[i - bpp] if i >= bpp else 0
                upleft = prior[i - bpp] if i >= bpp else 0
                row[i] = (row[i] + _paeth(left, prior[i], upleft)) & 0xFF
        if y % step == 0:
            for x in range(0, width, step):
                px = x * bpp
                if color == 2:
                    samples.append((row[px], row[px + 1], row[px + 2]))
                elif color == 0:
                    g = row[px]
                    samples.append((g, g, g))
                elif color == 3:
                    samples.append(
                        palette[row[px]] if row[px] < len(palette) else (0, 0, 0)
                    )
                elif color == 4:
                    g = row[px]
                    samples.append((g, g, g))
                else:
                    samples.append((row[px], row[px + 1], row[px + 2]))
        prior = row
    return samples


def _is_blank_render(data: bytes) -> bool | None:
    """The blank gate: True when the render's modal quantized color
    covers the grid (a text page or an empty sheet), False when real art
    is present, None when the bytes are not a PNG we can walk (the gate
    stands down; the marker gate still applies)."""
    samples = _png_samples(data)
    if samples is None:
        return None
    counts: dict[tuple[int, int, int], int] = {}
    for rgb in samples:
        key = (rgb[0] >> 3, rgb[1] >> 3, rgb[2] >> 3)
        counts[key] = counts.get(key, 0) + 1
    return max(counts.values()) / len(samples) >= _BLANK_MODAL_SHARE


def _page1_markers(pdf: str) -> str:
    """The first wrapper-host marker on page 1's text layer, or ""."""
    try:
        proc = _run_pdftotext(["pdftotext", "-f", "1", "-l", "1", pdf, "-"])
    except subprocess.TimeoutExpired, OSError:
        return ""
    if proc.returncode != 0:
        return ""
    text = (proc.stdout or "").casefold()
    for marker in _WRAPPER_MARKERS:
        if marker in text:
            return marker
    return ""


# --- the sibling rung -------------------------------------------------


def _fold(name: str) -> str:
    return (name or "").strip().casefold()


def _covered_siblings(db, book: dict) -> list[dict]:
    """Other books sharing at least one case-folded author whose
    has_cover is truthy (the engine's any-author matching semantics)."""
    mine = {_fold(a) for a in (book["authors"] or [])} - {""}
    if not mine:
        return []
    out = []
    for other in db.get_all_books():
        if other["id"] == book["id"] or not other["has_cover"]:
            continue
        if {_fold(a) for a in (other["authors"] or [])} & mine:
            out.append(other)
    return out


# --- the url rung -----------------------------------------------------


def _fetch_url(url: str, timeout: int) -> bytes:
    """One URL rung: direct image bytes when the body sniffs as JPEG/PNG;
    an HTML product page's og:image/twitter:image fetched in a second
    request otherwise. Raises _FetchError with a row-detail reason."""
    data, ctype = _http_get(url, timeout)
    if sniff_image_format(data) is not None:
        return data
    head = data[:256].lstrip()
    looks_html = ctype.startswith("text/html") or head.startswith(b"<")
    if not looks_html:
        raise _FetchError(f"not an image ({ctype or 'unknown type'}); and not HTML")
    page = data[: 2 * 1024 * 1024].decode("latin-1", "replace")
    match = _OG_IMAGE_RE.search(page) or _OG_IMAGE_ALT_RE.search(page)
    if match is None:
        raise _FetchError("page carries no og:image/twitter:image")
    # Signed CDN URLs carry &amp; in the raw markup; the attribute value
    # is HTML, the fetch URL is not.
    raw = html.unescape(match.group(1).strip())
    try:
        image_url = urllib.parse.urljoin(url, raw)
    except ValueError as e:
        raise _FetchError(f"unusable og:image url {raw[:120]!r}: {e}") from None
    data, _ = _http_get(image_url, timeout)
    if sniff_image_format(data) is None:
        raise _FetchError(f"og:image is not a JPEG/PNG ({image_url[:120]})")
    return data


def _http_get(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
            data = resp.read(_MAX_FETCH_BYTES + 1)
    except (
        urllib.error.URLError,
        http.client.HTTPException,
        OSError,
        ValueError,
    ) as e:
        # HTTPException is the truncated-chunked-body class (IncompleteRead)
        # and the malformed-status class (BadStatusLine): neither is an
        # OSError, and both are row details, never tracebacks.
        raise _FetchError(f"fetch failed: {e}") from None
    if len(data) > _MAX_FETCH_BYTES:
        raise _FetchError(f"body over {_MAX_FETCH_BYTES >> 20} MB; not a cover")
    return data, ctype


def _parse_urls(raw_urls: list[str], db) -> dict[int, str]:
    """--url ID=URL entries as {book_id: url}; unknown ids refuse here
    (read-only, before anything opens writable)."""
    known = set(db.all_ids())
    out: dict[int, str] = {}
    for raw in raw_urls:
        bid, sep, url = raw.partition("=")
        if not sep or not bid.strip().isdigit() or not url.strip():
            raise ValueError(f"--url wants ID=URL, got {raw!r}")
        book = int(bid)
        if book not in known:
            raise ValueError(f"unknown book id {book} (--url {raw!r})")
        out[book] = url.strip()
    return out


# --- the ladder -------------------------------------------------------


def plan_cover_source(db, args) -> list[dict]:
    """The full ladder, evaluated. Every offline rung runs here (the
    renders are read-only spawns; the phase-1 ebook-meta precedent), so
    the dry run shows the cover each book WOULD get; the url rung is
    planned only (network at --apply). Raises ValueError on usage
    problems (bad --url entries, unknown ids, a bad search expression)."""
    from cquarry_cli.integrate import resolve_targets

    url_overrides = _parse_urls(getattr(args, "url", None) or [], db)

    coverless = [b for b in db.get_all_books() if not b["has_cover"]]
    if getattr(args, "search", None) or getattr(args, "ids", None):
        wanted = set(resolve_targets(db, args))
        books = [b for b in coverless if b["id"] in wanted]
    else:
        books = coverless

    spread = bool(getattr(args, "spread_crop", False))
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="cquarry_covers_") as workdir:
        for book in books:
            rows.append(_plan_one(db, book, spread, workdir))
    # The url overrides join last (they may name covered books too) and
    # win over whatever the ladder found for their book.
    by_id = {r["book"]: r for r in rows}
    for bid, url in url_overrides.items():
        row = {
            "book": bid,
            "title": (db.get_book(bid) or {}).get("title", ""),
            "action": "fetch_url",
            "rung": "url",
            "detail": url,
            "data": None,
        }
        if bid in by_id:
            row["ladder"] = {k: by_id[bid].get(k) for k in ("action", "rung", "detail")}
            rows[rows.index(by_id[bid])] = row
        else:
            rows.append(row)
    return rows


def _plan_one(db, book: dict, spread: bool, workdir: str) -> dict:
    """One book's ladder evaluation. `data` rides the row when a rung
    produced cover bytes (the apply pass writes it; the dry run never
    does). Rungs run in the issue's order and the first one yielding
    bytes wins; a rung with nothing contributes its reason to `notes`
    and the ladder CONTINUES (the sibling rung rescues what the gates
    skip). Row actions: set_cover (a sourced cover) or declined (every
    rung missed, or none applied -- either way the worklist)."""
    row = {
        "book": book["id"],
        "title": book["title"],
        "action": "declined",
        "rung": None,
        "detail": "",
        "notes": [],
        "data": None,
    }

    # Rung 1: the EPUB's own declared cover (free, deterministic).
    formats = db.get_formats(book["id"]) or {}
    epub = formats.get("EPUB")
    if epub is not None:
        data = declared_epub_cover(epub["path"])
        if data is not None:
            row.update(
                action="set_cover",
                rung="declared-epub",
                detail=f"{len(data) >> 10} KB from the EPUB's declared cover",
                data=data,
            )
            return row
        row["notes"].append("no declared EPUB cover")

    # Rung 2: the PDF page-1 render behind the blank and wrapper gates.
    pdf = formats.get("PDF")
    if pdf is not None:
        data, detail = _plan_render(pdf["path"], spread, workdir)
        if data is not None:
            siblings = _covered_siblings(db, book)
            note = ""
            if len(siblings) == 1:
                note = f"; sibling cover also available: #{siblings[0]['id']}"
            after = f" (after: {'; '.join(row['notes'])})" if row["notes"] else ""
            row.update(
                action="set_cover",
                rung="page1-render",
                detail=detail + note + after,
                data=data,
            )
            return row
        if detail:
            row["notes"].append(detail)

    # Rung 3: the unique covered sibling donates its cover.
    siblings = _covered_siblings(db, book)
    if len(siblings) == 1:
        data = db.get_cover_bytes(siblings[0]["id"])
        if data is not None:
            after = f" (after: {'; '.join(row['notes'])})" if row["notes"] else ""
            row.update(
                action="set_cover",
                rung="sibling",
                detail=(
                    f"#{siblings[0]['id']} {siblings[0]['title']}"
                    f" ({len(data) >> 10} KB){after}"
                ),
                data=data,
            )
            return row
        row["notes"].append(f"covered sibling #{siblings[0]['id']} has no cover file")
    elif len(siblings) > 1:
        ids = ", ".join(f"#{s['id']}" for s in siblings[:6])
        row["notes"].append(
            f"{len(siblings)} covered siblings share the author ({ids}); ambiguous"
        )

    row["action"] = "declined"
    row["rung"] = "ladder"
    row["detail"] = "; ".join(row["notes"]) or (
        "no declared EPUB cover, no PDF to render, no covered sibling"
    )
    return row


def _plan_render(pdf_path: str, spread: bool, workdir: str) -> tuple[bytes, str]:
    """The gated page-1 render. Returns (bytes, "") when a render
    survived the gates, (None, reason) when declined or failed, and
    (None, "") when the format simply has no render to try (never the
    case here: the caller only sends PDFs)."""
    data, detail = _render_page1(pdf_path, workdir)
    if data is None:
        return None, detail
    size = _png_dimensions(data)
    if size is None:
        return None, "render is not a readable PNG"
    width, height = size
    blank = _is_blank_render(data)
    if blank:
        return None, "page-1 is blank (modal color over the grid)"
    marker = _page1_markers(pdf_path)
    if marker:
        return None, f"wrapper banner on page 1: {marker}"
    if width > height and not spread:
        return (
            None,
            f"page-1 is landscape ({width}x{height}; likely a cover "
            "spread): re-run with --spread-crop to take the right half",
        )
    if width > height:
        half = width // 2
        data, detail = _render_page1(pdf_path, workdir, crop=(half, width - half))
        if data is None:
            return None, detail
        return data, (
            f"page-1 spread crop, right half "
            f"({width - half}x{height} of {width}x{height}, "
            f"{len(data) >> 10} KB)"
        )
    return data, f"page-1 render ({width}x{height}, {len(data) >> 10} KB)"


# --- the verb ---------------------------------------------------------


def _print_plan(rows: list[dict], args) -> None:
    counts = _counts(rows)
    if getattr(args, "format", None) == "json":
        print(
            json.dumps(
                {"plan": {"rows": [_public(r) for r in rows], "counts": counts}},
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    print(
        f"cover-source plan ({len(rows)} coverless/override book(s), "
        f"{counts['set_cover']} sourced):"
    )
    for r in rows:
        line = f"  #{r['book']} {r['action']}"
        if r.get("rung"):
            line += f" [{r['rung']}]"
        if r.get("detail"):
            line += f" {r['detail']}"
        if r.get("ladder"):
            line += f" (ladder said: {r['ladder']['action']})"
        print(line)
    print(
        "\nDry run: the ladder is evaluated (read-only), nothing written. "
        "Pass --apply to write the sourced covers (Calibre closed, "
        "--backup-dir outside the library)."
    )


def _counts(rows: list[dict]) -> dict[str, int]:
    counts = {"set_cover": 0, "declined": 0, "fetch_url": 0, "failed": 0}
    for r in rows:
        counts[r["action"]] = counts.get(r["action"], 0) + 1
    return counts


def _public(row: dict) -> dict:
    """The JSON row: everything but the cover bytes."""
    return {k: v for k, v in row.items() if k != "data"}


def run_cover_source(db, args, *, apply: bool, take_backup=None) -> int:
    try:
        rows = plan_cover_source(db, args)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    if not apply:
        _print_plan(rows, args)
        return 0
    if take_backup and (rc := take_backup()):
        return rc
    timeout = getattr(args, "timeout", None) or _DEFAULT_URL_TIMEOUT
    applied = failed = 0
    for row in rows:
        if row["action"] == "fetch_url":
            try:
                row["data"] = _fetch_url(row["detail"], timeout)
                row["detail"] += f" ({len(row['data']) >> 10} KB fetched)"
            except _FetchError as e:
                row["action"] = "failed"
                row["detail"] = f"{row['detail']}: {e}"
                failed += 1
                continue
            # Fetched bytes write exactly like a ladder-sourced cover.
            row["action"] = "set_cover"
        if row["action"] != "set_cover" or row["data"] is None:
            continue
        try:
            with WritableCalibreDB(db.db_path) as wdb:
                with wdb.batch():
                    wdb.set_cover(row["book"], row["data"])
        except Exception as e:
            row["action"] = "failed"
            row["detail"] = f"write failed: {e}"[:200]
            failed += 1
            continue
        applied += 1
    counts = _counts(rows)
    if getattr(args, "format", None) == "json":
        print(
            json.dumps(
                {
                    "results": [_public(r) for r in rows],
                    "applied": applied,
                    "declined": counts["declined"],
                    "failed": failed,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(
            f"Cover-source: {applied} applied, {counts['declined']} declined, "
            f"{failed} failed."
        )
        for r in rows:
            if r["action"] in ("declined", "failed"):
                print(f"  #{r['book']} {r['action']} [{r['rung']}]: {r['detail']}")
        if counts["declined"] and not failed:
            print(
                "The declined books are the worklist: source them by hand "
                "(run cover --cover FILE) or with --url ID=URL."
            )
    return 1 if failed else 0
