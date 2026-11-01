"""Tests for `run cover-source` (issue #45): the cover ladder for
coverless books. The house convention holds: cquarry's WritableCalibreDB
runs for real against the temp database (covers are really written and
read back through get_cover_bytes), the external programs are mocked at
the module seams (_run_pdftoppm / _run_pdftotext), and one live class
exercises the real poppler binaries over crafted PDFs (skipUnless they
are installed; CI has neither)."""

import contextlib
import http.client
import io
import shutil
import sqlite3
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import zlib

from cquarry.db import CalibreDB

from cquarry_cli import coversource
from cquarry_cli.cli import main

from _fixtures import SCHEMA as _SCHEMA


# --- byte builders ----------------------------------------------------


def _png(width: int, height: int, painter) -> bytes:
    """A real 8-bit RGB PNG; painter(x, y) -> (r, g, b)."""
    raw = b"".join(
        b"\x00" + bytes(v for x in range(width) for v in painter(x, y))
        for y in range(height)
    )

    def chunk(ctype: bytes, body: bytes) -> bytes:
        return (
            struct.pack(">I", len(body))
            + ctype
            + body
            + struct.pack(">I", zlib.crc32(ctype + body) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _solid_png(color=(250, 250, 250), size=64):
    return _png(size, size, lambda x, y: color)


def _busy_png(size=64):
    return _png(
        size,
        size,
        lambda x, y: (200, 30, 40) if (x // 4 + y // 4) % 2 else (20, 60, 200),
    )


def _text_page_png(lines: int, line_width: int, size=256):
    """A white page with `lines` thin black lines of `line_width` px (the
    blank-gate boundary probes: ~2% ink scores modal ~0.98, the sparse
    half-title shape; ~15% ink scores ~0.85, a busier page)."""
    ink_rows = set()
    for i in range(lines):
        y0 = 8 + i * 6
        ink_rows.update((y0, y0 + 1))

    def painter(x, y):
        if y in ink_rows and x < line_width:
            return (20, 20, 20)
        return (250, 250, 250)

    return _png(size, size, painter)


def _minimal_pdf(content: bytes) -> bytes:
    """A valid one-page Letter PDF whose content stream is `content`."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources"
        b" << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref)
    )
    return out.getvalue()


_BUSY_CONTENT = b"1 0 0 rg 0 0 612 792 re f 0 0 1 rg 60 60 492 672 re f"
_BLANK_CONTENT = b""
# A colorful checkerboard (the blank gate must pass it) carrying a
# wrapper-site banner (the marker gate must catch it).
_CHECKER = b"".join(
    b"%g %g %g %g %g %g rg %d %d %d %d re f "
    % (
        0.8,
        0.2,
        0.2,
        0.2,
        0.2,
        0.8,
        (x % 2) * 306,
        y * 40,
        306,
        40,
    )
    for y in range(18)
    for x in range(2)
)
_MARKER_CONTENT = _CHECKER + b"BT /F1 24 Tf 72 700 Td (Downloaded by ilide.info) Tj ET"


def _epub(path: Path, cover_name: str = "images/cover.png", cover: bytes | None = None):
    """A fixture EPUB with an EPUB2 cover declaration (the issue-#34
    shape) whose payload is a REAL PNG, so declared_epub_cover returns
    usable cover bytes."""
    cover = cover if cover is not None else _busy_png(32)
    opf = (
        "<?xml version='1.0'?><package xmlns='http://www.idpf.org/2007/opf'"
        " version='2.0' unique-identifier='id'><metadata"
        " xmlns:dc='http://purl.org/dc/elements/1.1/'>"
        "<dc:title>t</dc:title><meta name='cover' content='cv'/></metadata>"
        "<manifest><item id='cv' href='%s' media-type='image/png'/></manifest>"
        "<spine/></package>" % cover_name
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            "<?xml version='1.0'?><container version='1.0'"
            " xmlns='urn:oasis:names:tc:opendocument:xmlns:container'>"
            "<rootfiles><rootfile full-path='OEBPS/content.opf'"
            " media-type='application/oebps-package+xml'/></rootfiles></container>",
        )
        z.writestr("OEBPS/content.opf", opf)
        z.writestr("OEBPS/" + cover_name, cover)
    return path


# --- the fixture library ----------------------------------------------

_BOOKS = [
    # (id, title, author, formats as (FMT, stem) tuples, rel path)
    (1, "Rules Volume", "Hutchings, Tim", [("PDF", "Rules1")], "H/Rules Volume (1)"),
    (2, "Art Volume", "Hutchings, Tim", [("PDF", "Art2")], "H/Art Volume (2)"),
    (3, "Plain EPUB", "Other, Ann", [("EPUB", "Plain3")], "O/Plain EPUB (3)"),
    (4, "Mobi Book", "Third, Bob", [("MOBI", "Mobi4")], "T/Mobi Book (4)"),
    (
        5,
        "Second Hutchings",
        "Hutchings, Tim",
        [("PDF", "Second5")],
        "H/Second Hutchings (5)",
    ),
]


def _build(tmpdir: Path) -> Path:
    db_path = tmpdir / "metadata.db"
    con = sqlite3.connect(db_path)
    con.executescript(_SCHEMA)
    for bid, title, author, fmts, rel in _BOOKS:
        con.execute(
            "INSERT INTO books (id,title,sort,author_sort,timestamp,pubdate,"
            "has_cover,last_modified,series_index,path,uuid) VALUES "
            f"({bid},'{title}','{title}','S','2024-01-01','2024-01-01',0,"
            f"'2024-01-02 00:00:00',1.0,'{rel}','uuid-{bid}')"
        )
        con.execute(f"INSERT INTO authors (id,name,sort) VALUES ({bid},'{author}','S')")
        con.execute(
            f"INSERT INTO books_authors_link (book,author) VALUES ({bid},{bid})"
        )
        for n, (fmt, stem) in enumerate(fmts):
            con.execute(
                "INSERT INTO data (book,format,name,uncompressed_size) VALUES "
                f"({bid},'{fmt}','{stem}',{4096 * (n + 1)})"
            )
    con.commit()
    con.close()
    for bid, _title, _author, fmts, rel in _BOOKS:
        d = tmpdir / rel
        d.mkdir(parents=True, exist_ok=True)
        for fmt, stem in fmts:
            (d / f"{stem}.{fmt.lower()}").write_bytes(b"fake format bytes " * 10)
    return db_path


class CoverSourceCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp(prefix="cquarry_cov_"))
        self.addCleanup(shutil.rmtree, self.tmpdir, True)
        self.db_path = _build(self.tmpdir)
        self.backups = Path(tempfile.mkdtemp(prefix="cquarry_cov_bak_"))
        self.addCleanup(shutil.rmtree, self.backups, True)
        pg = mock.patch("cquarry_cli.integrate._calibre_running", return_value=False)
        pg.start()
        self.addCleanup(pg.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stderr(err):
            with contextlib.redirect_stdout(out):
                code = main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def cover(self, *argv):
        """`run cover-source` with the mockable seams silenced: the fake
        renders decline (junk bytes) and pdftotext reads nothing, so a
        test only sees the rung it is driving."""
        pops = [
            mock.patch(
                "cquarry_cli.coversource._run_pdftoppm",
                side_effect=self._fake_render(_solid_png()),
            ),
            mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ),
        ]
        with contextlib.ExitStack() as stack:
            for p in pops:
                stack.enter_context(p)
            return self.run_cli(*argv)

    @staticmethod
    def _fake_render(png: bytes, record: list | None = None):
        def _run(cmd, **kwargs):
            if record is not None:
                record.append(cmd)
            Path(cmd[-1] + ".png").write_bytes(png)
            return mock.Mock(returncode=0, stdout="", stderr="")

        return _run

    @staticmethod
    def _fake_text(text: str, record: list | None = None):
        def _run(cmd, **kwargs):
            if record is not None:
                record.append(cmd)
            return mock.Mock(returncode=0, stdout=text, stderr="")

        return _run

    def set_cover(self, book_id: int, data: bytes):
        """A REAL cover write: the sibling and override tests need
        covered donors, and the flag + file must be truthy, not seeded."""
        from cquarry.write import WritableCalibreDB

        with WritableCalibreDB(str(self.db_path)) as wdb:
            with wdb.batch():
                wdb.set_cover(book_id, data)

    def db_state(self, book_id: int):
        with CalibreDB(str(self.db_path)) as db:
            book = db.get_book(book_id)
            return book["has_cover"], db.get_cover_bytes(book_id)

    def plan_rows(self, out: str):
        """The per-book plan rows as (id, action, rung) triples."""
        import re

        rows = []
        for m in re.finditer(r"^  #(\d+) (\S+)(?: \[(\w[-\w]*)\])?", out, re.M):
            rows.append((int(m.group(1)), m.group(2), m.group(3)))
        return rows


# --- the cover.jpg naming contract (cquarry 1.28.3) --------------------


def _cquarry_at_least(major: int, minor: int, patch: int) -> bool:
    try:
        from importlib.metadata import version

        parts = tuple(int(p) for p in version("cquarry").split(".")[:3])
    except Exception:
        return False
    return parts >= (major, minor, patch)


@unittest.skipUnless(
    _cquarry_at_least(1, 28, 3),
    "needs cquarry >= 1.28.3 (covers are cover.jpg-only)",
)
class TestCoverJpgNaming(CoverSourceCase):
    """Issue #46, the cquarry 1.28.3 adoption: upstream aligned both
    sides of its cover surface with Calibre itself, which reads ONLY
    `cover.jpg` (calibre/db/constants.py COVER_FILE_NAME; zero
    cover.png references in calibre/db/backend.py). set_cover writes
    cover.jpg ALWAYS -- PNG payloads included, bytes verbatim, no
    transcode -- and sweeps a stale cover.png; the reads resolve
    cover.jpg only, so a legacy png-only row answers None, the GUI's
    own answer. The ladder rides this unchanged (rung-2 renders become
    GUI-visible at cover.jpg; a png-only sibling stops donating, the
    "has no cover file" decline). This repo only pins the contract --
    the code is upstream's, and the floor keeps a stale library from
    pairing the old write shape with the new expectations."""

    _COVER_NAMES = "cover.*"

    def _book_dir(self) -> Path:
        return self.tmpdir / "H" / "Rules Volume (1)"

    def _cover_files(self) -> set[str]:
        return {p.name for p in self._book_dir().glob(self._COVER_NAMES)}

    def _legacy_png_only(self, data: bytes) -> None:
        """A pre-1.28.3 row by hand: the bytes on disk at cover.png
        (the naming 1.28.2's set_cover chose for a PNG payload)."""
        self.set_cover(1, data)
        (self._book_dir() / "cover.jpg").rename(self._book_dir() / "cover.png")

    def test_png_payload_lands_at_cover_jpg(self):
        data = _busy_png(48)
        self.set_cover(1, data)
        self.assertEqual(self._cover_files(), {"cover.jpg"})
        _has, stored = self.db_state(1)
        self.assertEqual(stored, data)

    def test_stale_cover_png_is_swept_on_the_next_write(self):
        self._legacy_png_only(_busy_png(48))
        fresh = _busy_png(64)
        self.set_cover(1, fresh)
        self.assertEqual(self._cover_files(), {"cover.jpg"})
        _has, stored = self.db_state(1)
        self.assertEqual(stored, fresh)

    def test_reads_resolve_cover_jpg_only(self):
        # The read-side drop: has_cover=1 and the bytes on disk, and
        # every read answers None -- never a silent png fallback.
        self._legacy_png_only(_busy_png(48))
        with CalibreDB(str(self.db_path)) as db:
            self.assertIsNone(db.get_cover_path(1))
            self.assertIsNone(db.get_cover_bytes(1))

    def test_png_only_sibling_stops_donating(self):
        # The rung-3 shift: a png-only donor is declined through the
        # ladder's existing no-file note, never a crash and never the
        # byte-invisible donation 1.28.2 would have made.
        self._legacy_png_only(_busy_png(48))
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "2", "--db", str(self.db_path)
        )
        self.assertIn("declined", out)
        self.assertIn("no cover file", out)


# --- targets ----------------------------------------------------------


class TestTargets(CoverSourceCase):
    def test_default_target_is_every_coverless_book(self):
        code, out, _ = self.cover("run", "cover-source", "--db", str(self.db_path))
        self.assertEqual(code, 0)
        self.assertIn("cover-source plan", out)
        ids = {r[0] for r in self.plan_rows(out)}
        self.assertEqual(ids, {1, 2, 3, 4, 5})

    def test_covered_books_are_not_targets(self):
        self.set_cover(2, _busy_png(32))
        _code, out, _ = self.cover("run", "cover-source", "--db", str(self.db_path))
        self.assertNotIn(2, {r[0] for r in self.plan_rows(out)})

    def test_ids_narrow_the_target_set(self):
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "1,3", "--db", str(self.db_path)
        )
        self.assertEqual({r[0] for r in self.plan_rows(out)}, {1, 3})

    def test_search_narrows_the_target_set(self):
        _code, out, _ = self.cover(
            "run",
            "cover-source",
            "--search",
            "author:Hutchings",
            "--db",
            str(self.db_path),
        )
        self.assertEqual({r[0] for r in self.plan_rows(out)}, {1, 2, 5})

    def test_unknown_id_still_refuses(self):
        code, _out, err = self.cover(
            "run", "cover-source", "--ids", "99", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown book id 99", err)

    def test_bad_search_expression_is_a_usage_error(self):
        code, _out, err = self.cover(
            "run", "cover-source", "--search", "((", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("could not parse", err)

    def test_url_on_unknown_id_refuses(self):
        code, _out, err = self.cover(
            "run",
            "cover-source",
            "--url",
            "99=https://x/img.png",
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("unknown book id 99", err)

    def test_url_without_an_equals_refuses(self):
        code, _out, err = self.cover(
            "run", "cover-source", "--url", "not-a-url", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--url wants ID=URL", err)

    def test_url_adds_a_covered_book_as_an_override_target(self):
        self.set_cover(2, _busy_png(32))
        _code, out, _ = self.cover(
            "run",
            "cover-source",
            "--url",
            "2=https://x/img.png",
            "--db",
            str(self.db_path),
        )
        rows = {r[0]: r[1] for r in self.plan_rows(out)}
        self.assertEqual(rows[2], "fetch_url")


# --- the declared-epub rung --------------------------------------------


class TestDeclaredEpubRung(CoverSourceCase):
    def test_declared_cover_is_the_plan_candidate(self):
        _epub(self.tmpdir / "O" / "Plain EPUB (3)" / "Plain3.epub")
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "3", "--db", str(self.db_path)
        )
        rows = self.plan_rows(out)
        self.assertEqual(rows[0][1], "set_cover")
        self.assertEqual(rows[0][2], "declared-epub")

    def test_apply_writes_the_declared_cover(self):
        cover = _busy_png(32)
        _epub(self.tmpdir / "O" / "Plain EPUB (3)" / "Plain3.epub", cover=cover)
        code, out, _ = self.run_cli(
            "run",
            "cover-source",
            "--ids",
            "3",
            "--apply",
            "--backup-dir",
            str(self.backups),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("1 applied", out)
        has_cover, stored = self.db_state(3)
        self.assertTrue(has_cover)
        self.assertEqual(stored, cover)


# --- the page1-render rung ---------------------------------------------


class TestPage1RenderRung(CoverSourceCase):
    def test_busy_render_is_the_candidate_and_apply_writes_it(self):
        art = _busy_png(64)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(art),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
                )
        self.assertIn("[page1-render]", out)
        # The dry run wrote nothing.
        self.assertEqual(self.db_state(1), (0, None))
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(art),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                code, out, _ = self.run_cli(
                    "run",
                    "cover-source",
                    "--ids",
                    "1",
                    "--apply",
                    "--backup-dir",
                    str(self.backups),
                    "--db",
                    str(self.db_path),
                )
        self.assertEqual(code, 0)
        has_cover, stored = self.db_state(1)
        self.assertTrue(has_cover)
        self.assertEqual(stored, art)

    def test_blank_render_declined(self):
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_solid_png((250, 250, 250))),
        ):
            code, out, _ = self.run_cli(
                "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
            )
        self.assertEqual(code, 0)
        self.assertIn("declined", out)
        self.assertIn("page-1 is blank", out)

    def test_sparse_half_title_declines_but_a_busier_page_passes(self):
        # The code-review measurement: ~2% ink scores modal ~0.99 (the
        # sparse half-title shape), ~15% scores ~0.85. The 0.95 cut
        # declines the first and keeps the second -- and the residual
        # (a fully typeset title page) stays a named dry-run candidate,
        # not a silent write.
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_text_page_png(6, 60)),
        ):
            _code, out, _ = self.run_cli(
                "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
            )
        self.assertIn("page-1 is blank", out)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_text_page_png(24, 200)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
                )
        self.assertIn("[page1-render]", out)

    def test_wrapper_marker_declined(self):
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_busy_png(64)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text("Downloaded by ilide.info\n"),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
                )
        self.assertIn("wrapper banner", out)
        self.assertIn("ilide", out)

    def test_landscape_declined_without_the_flag(self):
        wide = _png(128, 64, lambda x, y: (10 * x % 256, 20 * y % 256, 90))
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(wide),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
                )
        self.assertIn("landscape", out)
        self.assertIn("--spread-crop", out)

    def test_spread_crop_renders_the_right_half_measured_from_the_render(self):
        wide = _png(128, 64, lambda x, y: (10 * x % 256, 20 * y % 256, 90))
        calls = []
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(wide, record=calls),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                _code, out, _ = self.run_cli(
                    "run",
                    "cover-source",
                    "--ids",
                    "1",
                    "--spread-crop",
                    "--db",
                    str(self.db_path),
                )
        # Two renders; the second is the crop, offset half the RENDERED
        # width (128 // 2 = 64), never a points-based number.
        self.assertEqual(len(calls), 2)
        crop = calls[1]
        self.assertIn("-x", crop)
        self.assertEqual(crop[crop.index("-x") + 1], "64")
        self.assertEqual(crop[crop.index("-W") + 1], "64")
        self.assertIn("spread crop", out)
        self.assertIn("[page1-render]", out)

    def test_render_failure_declined_with_the_tool_stderr(self):
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            return_value=mock.Mock(
                returncode=1,
                stdout="",
                stderr="Syntax Error: Couldn't read xref table\n",
            ),
        ):
            _code, out, _ = self.run_cli(
                "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
            )
        self.assertIn("declined", out)
        # The multi-line poppler stderr rides squashed to one line.
        self.assertNotIn("\nSyntax", out)


# --- the sibling rung ---------------------------------------------------


class TestSiblingRung(CoverSourceCase):
    def test_unique_covered_sibling_donates_its_cover(self):
        donor = _busy_png(48)
        self.set_cover(1, donor)
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "2", "--db", str(self.db_path)
        )
        rows = self.plan_rows(out)
        self.assertEqual(rows[0][1], "set_cover")
        self.assertEqual(rows[0][2], "sibling")
        code, out, _ = self.run_cli(
            "run",
            "cover-source",
            "--ids",
            "2",
            "--apply",
            "--backup-dir",
            str(self.backups),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        _has, stored = self.db_state(2)
        self.assertEqual(stored, donor)

    def test_gate_decline_falls_through_to_sibling(self):
        # The issue's ladder order: a wrapper-marked page-1 declines and
        # the covered sibling still rescues the book.
        donor = _busy_png(48)
        self.set_cover(1, donor)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_busy_png(64)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text("ilide.info banner"),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "2", "--db", str(self.db_path)
                )
        self.assertIn("[sibling]", out)
        self.assertIn("ilide", out)  # the render decline rides the notes

    def test_two_covered_siblings_are_an_ambiguity_not_a_guess(self):
        self.set_cover(1, _busy_png(48))
        self.set_cover(5, _busy_png(48))
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "2", "--db", str(self.db_path)
        )
        self.assertIn("declined", out)
        self.assertIn("ambiguous", out)

    def test_covered_flag_without_a_file_declines(self):
        # has_cover=1 but the file is gone: the donation is refused and
        # named, never a silent skip or a traceback.
        self.set_cover(1, _busy_png(48))
        # set_cover wrote cover.jpg (it always does, PNG payload
        # included, as of cquarry 1.28.3); remove it.
        for p in (self.tmpdir / "H" / "Rules Volume (1)").iterdir():
            if p.name.startswith("cover."):
                p.unlink()
        _code, out, _ = self.cover(
            "run", "cover-source", "--ids", "2", "--db", str(self.db_path)
        )
        self.assertIn("no cover file", out)

    def test_render_candidate_notes_the_sibling_alternative(self):
        donor = _busy_png(48)
        self.set_cover(5, donor)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_busy_png(64)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                _code, out, _ = self.run_cli(
                    "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
                )
        self.assertIn("[page1-render]", out)
        self.assertIn("sibling cover also available: #5", out)


# --- the url rung --------------------------------------------------------


def _response(data: bytes, ctype: str = "image/png"):
    resp = mock.Mock()
    resp.headers = {"Content-Type": ctype}
    resp.read = mock.Mock(return_value=data)
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    return resp


class TestUrlRung(CoverSourceCase):
    def test_dry_run_touches_no_network(self):
        guard = mock.Mock(side_effect=AssertionError("network in a dry run"))
        with mock.patch("urllib.request.urlopen", guard):
            code, out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/art.png",
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertIn("https://example.org/art.png", out)
        self.assertIn("fetch_url", out)
        guard.assert_not_called()

    def test_direct_image_url_applies(self):
        art = _busy_png(32)
        sent = []

        def fake_urlopen(req, timeout=None):
            sent.append(req.full_url)
            return _response(art)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/art.png",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertEqual(sent, ["https://example.org/art.png"])
        _has, stored = self.db_state(2)
        self.assertEqual(stored, art)

    def test_html_page_og_image_is_extracted_and_fetched(self):
        art = _busy_png(32)
        html = (
            "<html><head><meta property='og:image' "
            "content='https://cdn.example.org/cover.jpg'/></head></html>"
        ).encode()
        sent = []

        def fake_urlopen(req, timeout=None):
            sent.append(req.full_url)
            return (
                _response(html, "text/html")
                if len(sent) == 1
                else _response(art, "image/jpeg")
            )

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, _out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/product",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertEqual(sent[1], "https://cdn.example.org/cover.jpg")
        self.assertEqual(self.db_state(2)[1], art)

    def test_relative_og_image_resolves_against_the_page(self):
        art = _busy_png(32)
        html = (
            "<html><head><meta property='og:image' content='/img/art.png'/>"
            "</head></html>"
        ).encode()
        sent = []

        def fake_urlopen(req, timeout=None):
            sent.append(req.full_url)
            return _response(html, "text/html") if len(sent) == 1 else _response(art)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, _out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/product?page=1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertEqual(sent[1], "https://example.org/img/art.png")

    def test_not_an_image_and_not_html_is_a_failed_row(self):
        with mock.patch(
            "urllib.request.urlopen",
            return_value=_response(b"\x00\x01binary junk", "application/octet-stream"),
        ):
            code, out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/junk",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 1)
        self.assertIn("failed", out)

    def test_url_overrides_the_ladder_and_records_what_it_replaced(self):
        art = _busy_png(32)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_busy_png(64)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                with mock.patch(
                    "urllib.request.urlopen",
                    return_value=_response(art),
                ):
                    code, out, _ = self.run_cli(
                        "run",
                        "cover-source",
                        "--ids",
                        "2",
                        "--url",
                        "2=https://example.org/true-cover.png",
                        "--apply",
                        "--backup-dir",
                        str(self.backups),
                        "--db",
                        str(self.db_path),
                    )
        self.assertEqual(code, 0)
        # The stored bytes are the URL's, not the render's.
        self.assertEqual(self.db_state(2)[1], art)

    def test_url_override_json_records_the_ladder_it_replaced(self):
        art = _busy_png(32)
        with mock.patch(
            "cquarry_cli.coversource._run_pdftoppm",
            side_effect=self._fake_render(_busy_png(64)),
        ):
            with mock.patch(
                "cquarry_cli.coversource._run_pdftotext",
                side_effect=self._fake_text(""),
            ):
                with mock.patch(
                    "urllib.request.urlopen",
                    return_value=_response(art),
                ):
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        code = main(
                            [
                                "run",
                                "cover-source",
                                "--ids",
                                "2",
                                "--url",
                                "2=https://example.org/true-cover.png",
                                "--apply",
                                "--backup-dir",
                                str(self.backups),
                                "--format",
                                "json",
                                "--db",
                                str(self.db_path),
                            ]
                        )
        self.assertEqual(code, 0)
        import json

        (row,) = json.loads(buf.getvalue())["results"]
        self.assertEqual(row["rung"], "url")
        # The AGENTS-documented shape: the replaced ladder outcome rides
        # the row's `ladder` key.
        self.assertEqual(row["ladder"]["action"], "set_cover")
        self.assertEqual(row["ladder"]["rung"], "page1-render")

    def test_twitter_card_name_spelling_is_extracted_too(self):
        art = _busy_png(32)
        page = (
            "<html><head><meta name='twitter:image' "
            "content='https://cdn.example.org/tw.png'/></head></html>"
        ).encode()
        sent = []

        def fake_urlopen(req, timeout=None):
            sent.append(req.full_url)
            return _response(page, "text/html") if len(sent) == 1 else _response(art)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, _out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/product",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertEqual(sent[1], "https://cdn.example.org/tw.png")

    def test_og_image_entities_are_unescaped_before_the_fetch(self):
        art = _busy_png(32)
        page = (
            "<html><head><meta property='og:image' "
            "content='https://cdn.example.org/c.png?a=1&amp;b=2'/></head></html>"
        ).encode()
        sent = []

        def fake_urlopen(req, timeout=None):
            sent.append(req.full_url)
            return _response(page, "text/html") if len(sent) == 1 else _response(art)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, _out, _ = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/product",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 0)
        self.assertEqual(sent[1], "https://cdn.example.org/c.png?a=1&b=2")

    def test_truncated_response_is_a_failed_row_not_a_traceback(self):
        # IncompleteRead is an http.client.HTTPException, not an OSError:
        # the class the fetch contract promises never to traceback on.
        broken = _response(b"", "image/png")
        broken.read = mock.Mock(
            side_effect=http.client.IncompleteRead(b"partial chunk")
        )
        with mock.patch("urllib.request.urlopen", return_value=broken):
            code, out, err = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "2",
                "--url",
                "2=https://example.org/truncated",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 1)
        self.assertIn("fetch failed", out)
        self.assertNotIn("Traceback", err)


# --- the rails ------------------------------------------------------------


class TestRails(CoverSourceCase):
    def test_apply_demands_backup_dir(self):
        code, _out, err = self.run_cli(
            "run", "cover-source", "--ids", "1", "--apply", "--db", str(self.db_path)
        )
        self.assertEqual(code, 2)
        self.assertIn("--backup-dir", err)

    def test_backup_dir_inside_library_refused(self):
        code, _out, err = self.run_cli(
            "run",
            "cover-source",
            "--ids",
            "1",
            "--apply",
            "--backup-dir",
            str(self.tmpdir),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 2)
        self.assertIn("OUTSIDE the library", err)

    def test_calibre_running_is_exit_one(self):
        with mock.patch("cquarry_cli.integrate._calibre_running", return_value=True):
            code, _out, err = self.run_cli(
                "run",
                "cover-source",
                "--ids",
                "1",
                "--apply",
                "--backup-dir",
                str(self.backups),
                "--db",
                str(self.db_path),
            )
        self.assertEqual(code, 1)
        self.assertIn("Calibre is running", err)

    def test_all_declined_is_exit_zero(self):
        code, out, _ = self.cover(
            "run",
            "cover-source",
            "--apply",
            "--backup-dir",
            str(self.backups),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        self.assertIn("worklist", out)

    def test_json_plan_shape(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = main(
                [
                    "run",
                    "cover-source",
                    "--ids",
                    "1",
                    "--format",
                    "json",
                    "--db",
                    str(self.db_path),
                ]
            )
        self.assertEqual(code, 0)
        import json

        payload = json.loads(buf.getvalue())
        (row,) = payload["plan"]["rows"]
        self.assertNotIn("data", row)
        self.assertIn("counts", payload["plan"])

    def test_json_results_shape(self):
        self.set_cover(1, _busy_png(48))
        import json

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(
                [
                    "run",
                    "cover-source",
                    "--ids",
                    "2",
                    "--apply",
                    "--backup-dir",
                    str(self.backups),
                    "--format",
                    "json",
                    "--db",
                    str(self.db_path),
                ]
            )
        self.assertEqual(code, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["failed"], 0)
        self.assertNotIn("data", payload["results"][0])


# --- the live poppler seam (skipped without the binaries) -----------------


class TestLiveRender(CoverSourceCase):
    def setUp(self):
        import shutil as _shutil

        if not (_shutil.which("pdftoppm") and _shutil.which("pdftotext")):
            self.skipTest("poppler tools (pdftoppm/pdftotext) are not on PATH")
        super().setUp()

    def test_busy_page_1_applies_end_to_end(self):
        pdf = self.tmpdir / "H" / "Rules Volume (1)" / "Rules1.pdf"
        pdf.write_bytes(_minimal_pdf(_BUSY_CONTENT))
        _code, out, _ = self.run_cli(
            "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
        )
        self.assertIn("[page1-render]", out)
        code, out, _ = self.run_cli(
            "run",
            "cover-source",
            "--ids",
            "1",
            "--apply",
            "--backup-dir",
            str(self.backups),
            "--db",
            str(self.db_path),
        )
        self.assertEqual(code, 0)
        has_cover, stored = self.db_state(1)
        self.assertTrue(has_cover)
        # The stored cover is the rendered PNG (dims match the render).
        self.assertEqual(coversource._png_dimensions(stored), (1275, 1650))

    def test_blank_page_1_declines(self):
        pdf = self.tmpdir / "H" / "Rules Volume (1)" / "Rules1.pdf"
        pdf.write_bytes(_minimal_pdf(_BLANK_CONTENT))
        _code, out, _ = self.run_cli(
            "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
        )
        self.assertIn("page-1 is blank", out)

    def test_wrapper_banner_page_declines_on_the_marker(self):
        pdf = self.tmpdir / "H" / "Rules Volume (1)" / "Rules1.pdf"
        pdf.write_bytes(_minimal_pdf(_MARKER_CONTENT))
        _code, out, _ = self.run_cli(
            "run", "cover-source", "--ids", "1", "--db", str(self.db_path)
        )
        self.assertIn("wrapper banner", out)
        self.assertIn("ilide", out)


if __name__ == "__main__":
    unittest.main()
