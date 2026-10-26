"""The résumé PDF the site ships must survive text extraction.

A Pages export draws fi / fl / ff ligatures as single glyphs inside /ActualText spans. Poppler
(pdftotext) drops them ("Certi cate", "After E ects", "social-rst") and dumps the lost letters at the
end of the page; other parsers return the single character U+FB00..FB06. Either way a job board that
reads the file never sees "Effects" or "first".
The repair tool is ~/Projects/resume/fix_pdf_ligatures.py: run it on every fresh Pages export.

The checks do not depend on what the résumé says, so editing it later will not break them.
"""
import collections
import re
import shutil
import subprocess
import unittest

from support import ROOT, load

LIGATURE_CHARS = re.compile("[\ufb00-\ufb06]")
HINT = "run ~/Projects/resume/fix_pdf_ligatures.py on the export"


def resume_file():
    rel = load("site-config.json").get("resume_pdf") or ""
    return ROOT / rel if rel else None


def run(*cmd):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=120)


def words(text):
    """Word multiset. A hyphen at a line end is joined first, because poppler joins it and MuPDF does not."""
    return collections.Counter(re.findall(r"[^\W_]+", re.sub(r"-[ \t]*\n[ \t]*", "", text)))


class TestResumePdf(unittest.TestCase):
    def setUp(self):
        path = resume_file()
        self.assertTrue(path is not None and path.is_file(), "resume_pdf is not set or the file is missing")
        assert path is not None
        self.path = path

    def test_it_is_a_real_one_page_pdf(self):
        self.assertTrue(self.path.read_bytes().startswith(b"%PDF-"))
        if not shutil.which("pdfinfo"):
            self.skipTest("poppler is not installed")
        pages = re.search(r"^Pages:\s+(\d+)", run("pdfinfo", str(self.path)).stdout, re.M)
        self.assertEqual(pages and pages.group(1), "1", "the résumé should be one page")

    def test_every_font_carries_a_unicode_map(self):
        if not shutil.which("pdffonts"):
            self.skipTest("poppler is not installed")
        rows = [l.split() for l in run("pdffonts", str(self.path)).stdout.splitlines()[2:] if l.strip()]
        self.assertTrue(rows, "no fonts found")
        # columns from the right: ... emb sub uni <object> <generation>
        missing = [r[0] for r in rows if r[-3] != "yes"]
        self.assertEqual(missing, [], "fonts with no /ToUnicode map: " + HINT)

    def test_no_font_maps_a_glyph_to_a_single_ligature_character(self):
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")
        bad = []
        with fitz.open(str(self.path)) as doc:
            for page in doc:
                for f in page.get_fonts(full=True):
                    kind, ref = doc.xref_get_key(f[0], "ToUnicode")
                    if kind != "xref":
                        continue
                    cmap = doc.xref_stream(int(ref.split()[0])).decode("latin-1").lower()
                    if re.search(r"<fb0[0-6]>", cmap):
                        bad.append(f[3])
        self.assertEqual(bad, [], "ToUnicode maps to U+FB00..FB06, which plain-text search will not match: " + HINT)

    def test_poppler_and_mupdf_read_the_same_words(self):
        if not shutil.which("pdftotext"):
            self.skipTest("poppler is not installed")
        try:
            import fitz
        except ImportError:
            self.skipTest("PyMuPDF is not installed")
        poppler = run("pdftotext", "-enc", "UTF-8", str(self.path), "-").stdout
        with fitz.open(str(self.path)) as doc:
            mupdf = "".join(p.get_text() for p in doc)
        self.assertIsNone(LIGATURE_CHARS.search(poppler), "a ligature came back as one character")
        self.assertIsNone(LIGATURE_CHARS.search(mupdf), "a ligature came back as one character")
        a, b = words(poppler), words(mupdf)
        self.assertEqual(dict(b - a), {}, "words poppler cannot read (dropped ligatures?): " + HINT)
        self.assertEqual(dict(a - b), {}, "fragments poppler invented (split words, stray ff/fi/fl): " + HINT)


if __name__ == "__main__":
    unittest.main()
