"""Tests for audit_drm.py's per-format DRM classification.

The script is standalone (not part of the cquarry package), so it is loaded by
path the same way test_scripts.py loads its siblings. Fixtures are built in a
temp dir with stdlib only: zip archives for EPUB, raw bytes for PDF, and a
hand-assembled PalmDB container for MOBI. The qpdf-backed Standard-encryption
branch is not exercised (it shells out); every case here is decided by the
pure-Python logic.
"""

import importlib.util
import pathlib
import struct
import tempfile
import unittest
import zipfile

_SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


drm = _load("audit_drm")


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #
def _epub(tmp, name, extra_files):
    """Minimal EPUB: mimetype + container.opf + whatever extra entries given."""
    p = pathlib.Path(tmp) / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("mimetype", "application/epub+zip")
        z.writestr("OEBPS/content.opf", "<package/>")
        for entry, data in extra_files.items():
            z.writestr(entry, data)
    return p


def _enc_xml(algorithm, uri):
    return (
        '<?xml version="1.0"?>'
        '<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">'
        f'<EncryptionMethod Algorithm="{algorithm}"/>'
        f'<CipherData><CipherReference URI="{uri}"/></CipherData>'
        "</EncryptedData></encryption>"
    )


def _palmdb(tmp, name, enc_type):
    """A 1-record PalmDB whose record 0 carries the given encryption type."""
    p = pathlib.Path(tmp) / name
    header = bytearray(78)
    header[60:64] = b"BOOK"
    header[64:68] = b"MOBI"
    struct.pack_into(">H", header, 76, 1)  # one record
    rec0_off = 78 + 8  # after the single record-info entry
    rec_info = struct.pack(">I", rec0_off) + struct.pack(">I", 0)
    rec0 = bytearray(16)
    struct.pack_into(">H", rec0, 0, 2)  # PalmDOC compression
    struct.pack_into(">H", rec0, 12, enc_type)  # encryption type
    p.write_bytes(bytes(header) + rec_info + bytes(rec0))
    return p


class EpubTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_clean_epub(self):
        p = _epub(self.tmp, "clean.epub", {"OEBPS/ch1.xhtml": "<html/>"})
        self.assertEqual(drm.classify_epub(p).status, drm.CLEAN)

    def test_font_obfuscation_is_benign(self):
        enc = _enc_xml("http://www.idpf.org/2008/embedding", "OEBPS/fonts/body.otf")
        p = _epub(self.tmp, "fonts.epub", {"META-INF/encryption.xml": enc})
        v = drm.classify_epub(p)
        self.assertEqual(v.status, drm.BENIGN)
        self.assertIn("font", v.kind)

    def test_adobe_font_algo_also_benign(self):
        enc = _enc_xml("http://ns.adobe.com/pdf/enc#RC", "fonts/x.ttf")
        p = _epub(self.tmp, "afont.epub", {"META-INF/encryption.xml": enc})
        self.assertEqual(drm.classify_epub(p).status, drm.BENIGN)

    def test_encrypted_content_is_drm(self):
        enc = _enc_xml("http://www.w3.org/2001/04/xmlenc#aes128-cbc", "OEBPS/ch1.xhtml")
        p = _epub(self.tmp, "drm.epub", {"META-INF/encryption.xml": enc})
        v = drm.classify_epub(p)
        self.assertEqual(v.status, drm.DRM)

    def test_dat_named_fonts_are_benign(self):
        # the Columbine case: Adobe #RC obfuscation of fonts/00002.dat, fonts
        # named with no font extension. Benign, must not read as content DRM.
        enc = _enc_xml("http://ns.adobe.com/pdf/enc#RC", "fonts/00002.dat")
        p = _epub(self.tmp, "datfonts.epub", {"META-INF/encryption.xml": enc})
        self.assertEqual(drm.classify_epub(p).status, drm.BENIGN)

    def test_aes_on_content_without_voucher_is_drm(self):
        # a real cipher on a content document, not a font: DRM.
        enc = _enc_xml("http://www.w3.org/2001/04/xmlenc#aes256-cbc", "OEBPS/ch1.xhtml")
        p = _epub(self.tmp, "aes.epub", {"META-INF/encryption.xml": enc})
        self.assertEqual(drm.classify_epub(p).status, drm.DRM)

    def test_rights_xml_is_drm(self):
        p = _epub(self.tmp, "adept.epub", {"META-INF/rights.xml": "<adept/>"})
        v = drm.classify_epub(p)
        self.assertEqual(v.status, drm.DRM)
        self.assertEqual(v.kind, "Adobe ADEPT")

    def test_sinf_is_drm(self):
        p = _epub(self.tmp, "fairplay.epub", {"META-INF/sinf.xml": "<sinf/>"})
        self.assertEqual(drm.classify_epub(p).status, drm.DRM)

    def test_unparseable_encryption_xml_naming_content_is_drm(self):
        p = _epub(
            self.tmp, "broken.epub", {"META-INF/encryption.xml": "<broken ch1.xhtml"}
        )
        self.assertEqual(drm.classify_epub(p).status, drm.DRM)


class PdfTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _pdf(self, name, body):
        p = pathlib.Path(self.tmp) / name
        p.write_bytes(body)
        return p

    def test_ebx_handler_is_drm(self):
        p = self._pdf("adept.pdf", b"%PDF-1.6\n/Filter /EBX_HANDLER\n%%EOF")
        v = drm.classify_pdf(p)
        self.assertEqual(v.status, drm.DRM)
        self.assertEqual(v.kind, "Adobe ADEPT")

    def test_fileopen_is_drm(self):
        p = self._pdf("fo.pdf", b"%PDF-1.6\n/Filter /FOPN_foweb\n%%EOF")
        v = drm.classify_pdf(p)
        self.assertEqual(v.status, drm.DRM)
        self.assertEqual(v.kind, "FileOpen")

    def test_signature_across_chunk_boundary(self):
        # straddle the 1 MiB streaming boundary to exercise the tail overlap.
        sig = b"EBX_HANDLER"
        pad = (1 << 20) - 5
        body = b"%PDF-1.6\n" + b"\x00" * pad + sig + b"\n%%EOF"
        p = self._pdf("straddle.pdf", body)
        self.assertEqual(drm.classify_pdf(p).status, drm.DRM)

    def test_clean_pdf_no_signature_no_encrypt(self):
        # no DRM handler and no /Encrypt: clean regardless of qpdf availability.
        p = self._pdf("clean.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF")
        self.assertEqual(drm.classify_pdf(p).status, drm.CLEAN)


class MobiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_clean_mobi(self):
        p = _palmdb(self.tmp, "clean.azw3", 0)
        self.assertEqual(drm.classify_mobi(p).status, drm.CLEAN)

    def test_mobipocket_drm(self):
        p = _palmdb(self.tmp, "drm.azw3", 2)
        v = drm.classify_mobi(p)
        self.assertEqual(v.status, drm.DRM)
        self.assertEqual(v.kind, "Mobipocket")

    def test_legacy_mobi_drm(self):
        p = _palmdb(self.tmp, "old.mobi", 1)
        self.assertEqual(drm.classify_mobi(p).status, drm.DRM)

    def test_truncated_is_error(self):
        p = pathlib.Path(self.tmp) / "trunc.mobi"
        p.write_bytes(b"too short")
        self.assertEqual(drm.classify_mobi(p).status, drm.ERROR)


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_djvu_is_na(self):
        p = pathlib.Path(self.tmp) / "x.djvu"
        p.write_bytes(b"AT&TFORM")
        self.assertEqual(drm.classify_file(p).status, drm.NA)

    def test_dispatch_by_extension(self):
        p = _palmdb(self.tmp, "book.mobi", 0)
        self.assertEqual(drm.classify_file(p).status, drm.CLEAN)


if __name__ == "__main__":
    unittest.main()
