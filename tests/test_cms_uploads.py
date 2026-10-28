"""The two masthead upload scopes: `hero` (JPEG, 2400 px) and `cutout` (PNG,
alpha kept).

A cut-out saved as JPEG loses its transparency and turns into a black box over
the photo, so the scope that carries one must produce a PNG. These tests check
the file on disk with macOS sips, exactly like the rest of the upload suite.
"""
import json
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import zlib
from http.server import ThreadingHTTPServer
from pathlib import Path

import cms_fixture as F

HAS_SIPS = shutil.which("sips") is not None


def png(w, h, rgba=(200, 60, 30, 255)):
    """A valid PNG with an alpha channel (colour type 6), no imaging library."""
    raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))

    def chunk(kind, data):
        body = struct.pack(">I", len(data)) + kind + data
        return body + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def probe(path):
    """(width, height, format, has_alpha) straight from sips."""
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format",
                          "-g", "hasAlpha", str(path)], capture_output=True, text=True).stdout
    found = [re.search(pattern, out) for pattern in (r"pixelWidth: (\d+)", r"pixelHeight: (\d+)",
                                                     r"format: (\w+)", r"hasAlpha: (\w+)")]
    assert all(found), "sips could not read %s: %s" % (path, out)
    return int(found[0].group(1)), int(found[1].group(1)), found[2].group(1), found[3].group(1) == "yes"


class UploadCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-upload-"))
        F.make_site(cls.tmp)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        F.make_site(self.tmp)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.run_build()

    # -- helpers ---------------------------------------------------------------------------
    def call(self, method, path, body=None, raw=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        headers = {"Content-Type": "application/octet-stream" if raw is not None
                   else "application/json"}
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                                     method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        return status, json.loads(payload)

    def upload(self, name, image, scope, **q):
        query = "&".join("%s=%s" % (k, urllib.parse.quote(str(v)))
                         for k, v in dict({"kind": "poster", "scope": scope, "name": name}, **q).items())
        return self.call("POST", "/api/upload?" + query, raw=image)

    def version(self):
        return self.call("GET", "/api/state")[1]["version"]

    def save(self, **raw):
        return self.call("POST", "/api/config", dict(raw, version=self.version()))

    def text(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestHeroUpload(UploadCase):
    def test_a_hero_lands_in_the_masthead_folder_as_a_jpeg_within_2400(self):
        status, body = self.upload("Masthead Back.PNG", png(3000, 2000), "hero")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["full"], "assets/img/masthead/masthead-back.jpg")
        self.assertEqual(list(body), ["full"], "a masthead upload returns one file, no thumb")
        width, height, fmt, _ = probe(self.tmp / body["full"])
        self.assertEqual((width, height), (2400, 1600))
        self.assertEqual(fmt, "jpeg")
        self.assertEqual(list((self.tmp / "assets/img/masthead").glob(".incoming*")), [],
                         "the temp file was left behind")

    def test_a_small_hero_is_never_enlarged(self):
        status, body = self.upload("small.png", png(900, 600), "hero")
        self.assertEqual(status, 200, body)
        self.assertEqual(probe(self.tmp / body["full"])[:2], (900, 600))

    def test_a_heic_hero_works(self):
        src = self.tmp / "source.png"
        src.write_bytes(png(300, 200))
        heic = self.tmp / "source.heic"
        r = subprocess.run(["sips", "-s", "format", "heic", str(src), "--out", str(heic)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not heic.is_file():
            self.skipTest("this Mac cannot encode HEIC")
        status, body = self.upload("IMG_0001.HEIC", heic.read_bytes(), "hero")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["full"].endswith(".jpg"))
        self.assertEqual(probe(self.tmp / body["full"])[:3], (300, 200, "jpeg"))


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestCutoutUpload(UploadCase):
    def test_a_cutout_is_a_png_and_keeps_its_alpha(self):
        status, body = self.upload("Masthead Front.png", png(1200, 900, (200, 60, 30, 128)), "cutout")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["full"], "assets/img/masthead/masthead-front.png")
        width, height, fmt, alpha = probe(self.tmp / body["full"])
        self.assertEqual(fmt, "png")
        self.assertTrue(alpha, "the alpha channel was dropped: the cut-out would show a black box")
        self.assertEqual((width, height), (1200, 900))

    def test_a_one_pixel_transparent_source_keeps_its_alpha(self):
        status, body = self.upload("dot.png", png(1, 1, (0, 0, 0, 0)), "cutout")
        self.assertEqual(status, 200, body)
        width, height, fmt, alpha = probe(self.tmp / body["full"])
        self.assertEqual((width, height, fmt, alpha), (1, 1, "png", True))

    def test_a_big_cutout_is_shrunk_to_2000_and_never_enlarged(self):
        status, body = self.upload("wide.png", png(2600, 1300), "cutout")
        self.assertEqual(status, 200, body)
        self.assertEqual(probe(self.tmp / body["full"])[:2], (2000, 1000))
        status, body = self.upload("tiny.png", png(40, 30), "cutout")
        self.assertEqual(probe(self.tmp / body["full"])[:2], (40, 30))

    def test_the_png_name_does_not_clash_with_a_jpeg_of_the_same_stem(self):
        first = self.upload("front.png", png(80, 60), "cutout")[1]
        second = self.upload("front.png", png(80, 60), "cutout")[1]
        third = self.upload("front.png", png(80, 60), "hero")[1]
        self.assertEqual((first["full"], second["full"], third["full"]),
                         ("assets/img/masthead/front.png", "assets/img/masthead/front-2.png",
                          "assets/img/masthead/front.jpg"))


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestMastheadContract(UploadCase):
    def test_the_three_paths_round_trip_through_a_config_save(self):
        hero = self.upload("desk.png", png(3000, 2000), "hero")[1]["full"]
        phone = self.upload("phone.png", png(1200, 2000), "hero")[1]["full"]
        cutout = self.upload("cut.png", png(1200, 900), "cutout")[1]["full"]
        status, body = self.save(hero_image=hero, hero_image_phone=phone, hero_cutout=cutout)
        self.assertEqual(status, 200, body)
        self.assertEqual(sorted(body["touched"]), ["hero_cutout", "hero_image", "hero_image_phone"])
        cfg = json.loads((self.tmp / "site-config.json").read_text(encoding="utf-8"))
        self.assertEqual((cfg["hero_image"], cfg["hero_image_phone"], cfg["hero_cutout"]),
                         (hero, phone, cutout))
        page = self.text("index.html")
        self.assertIn('src="%s"' % cutout, page)
        self.assertIn('srcset="%s"' % phone, page)

    def test_an_upload_writes_no_data_file(self):
        """The page sends the returned path in the same save as the rest of the
        form; until then an upload leaves only an unreferenced file behind."""
        before = {name: (self.tmp / name).read_bytes() for name in self.cms.DATA_NAMES}
        self.assertEqual(self.upload("desk.png", png(300, 200), "hero")[0], 200)
        after = {name: (self.tmp / name).read_bytes() for name in self.cms.DATA_NAMES}
        self.assertEqual(after, before)

    def test_unknown_scopes_and_bad_files_are_still_refused(self):
        self.assertEqual(self.upload("x.png", png(10, 10), "nope")[0], 400)
        self.assertEqual(self.upload("x.png", png(10, 10), "masthead")[0], 400)
        self.assertEqual(self.upload("x.png", png(10, 10), "hero", kind="photo")[0], 200,
                         "a masthead scope ignores kind: there is no project to attach it to")
        self.assertEqual(self.upload("note.txt", b"hello", "hero")[0], 400)
        self.assertEqual(self.upload("fake.png", b"not an image", "cutout")[0], 400)
        self.assertEqual(list((self.tmp / "assets/img/masthead").glob("fake*")), [],
                         "a failed conversion must leave nothing")

    def test_a_masthead_upload_never_needs_a_project_slug(self):
        self.assertEqual(self.upload("a.png", png(10, 10), "hero", slug="nope")[0], 200)
        self.assertEqual(self.upload("b.png", png(10, 10), "project")[0], 400,
                         "the project scope still demands a real slug")


if __name__ == "__main__":
    unittest.main()
