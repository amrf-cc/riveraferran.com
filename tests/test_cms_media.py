"""The share card and the photo badges, through the Site Manager's HTTP API.

The share card is the one image whose exact size the site promises (og:image:width/height are the fixed
constants 1200x630 in templates/head.html.tmpl), so these tests check the file on disk with macOS sips:
the crop really is 1200x630, a portrait source is cut down rather than squashed, and a source too small
to survive that crop is refused. The photo flags are the AI-assisted / Composite badges build.py draws
on covers, tiles and in the lightbox; they are checked to survive an alt edit and a re-add, which is
where the manager used to drop them.
"""
import shutil
import struct
import subprocess
import tempfile
import unittest
import urllib.parse
import zlib
from pathlib import Path

import test_cms  # noqa: F401  (its CmsCase is the fixture harness; importing it runs no tests twice)

HAS_SIPS = shutil.which("sips") is not None
SHARE = "assets/img/studio-share-card.jpg"


def png(w, h, rgb=(200, 60, 30)):
    """A solid PNG (colour type 2: RGB), no imaging library."""
    return bands_png(w, h, [(0, h, rgb)])


def bands_png(w, h, bands):
    """A PNG whose rows take the colour of the first band that covers them: [(from_y, to_y, rgb)]."""
    rows = []
    for y in range(h):
        for y0, y1, rgb in bands:
            if y0 <= y < y1:
                rows.append(b"\x00" + bytes(rgb) * w)
                break

    def chunk(kind, data):
        body = struct.pack(">I", len(data)) + kind + data
        return body + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows))) + chunk(b"IEND", b""))


def dims(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                         capture_output=True, text=True).stdout
    width = out.split("pixelWidth:")[1].split()[0] if "pixelWidth:" in out else ""
    height = out.split("pixelHeight:")[1].split()[0] if "pixelHeight:" in out else ""
    assert width and height, "sips could not read %s: %s" % (path, out)
    return int(width), int(height)


def pixels(path):
    """(width, height, at) for a JPEG: sips turns it into a BMP, which is plain bytes to read."""
    tmp = Path(tempfile.mkdtemp(prefix="cms-media-probe-")) / "probe.bmp"
    try:
        subprocess.run(["sips", "-s", "format", "bmp", str(path), "--out", str(tmp)],
                       capture_output=True, check=True)
        data = tmp.read_bytes()
    finally:
        shutil.rmtree(str(tmp.parent), ignore_errors=True)
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    step = struct.unpack_from("<H", data, 28)[0] // 8
    stride = ((width * step + 3) // 4) * 4
    top_down, height = height < 0, abs(height)

    def at(x, y):
        row = y if top_down else height - 1 - y
        i = offset + row * stride + x * step
        return data[i + 2], data[i + 1], data[i]      # BMP stores blue, green, red
    return width, height, at


def near(rgb, want, tol=40):
    return all(abs(a - b) <= tol for a, b in zip(rgb, want))


class MediaCase(test_cms.CmsCase):
    """The fixture site over HTTP, plus the two uploads and the two files these tests look at."""

    def upload(self, name, image, scope="share", **q):
        query = urllib.parse.urlencode(dict({"kind": "poster", "scope": scope, "name": name}, **q))
        return self.call("POST", "/api/upload?" + query, raw=image)

    def photos(self, slug="alpha"):
        return self.data("assets-manifest.json")["projects"][slug]["photos"]

    def card(self):
        return self.data("site-config.json").get("og_image", "")

    def leftovers(self):
        return sorted(str(p.name) for p in (self.tmp / "assets" / "img").iterdir()
                      if p.name.startswith(".incoming") or p.name.endswith(".crop.jpg"))


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestShareCardUpload(MediaCase):
    def test_a_share_upload_is_exactly_1200x630_and_has_no_thumb(self):
        status, body = self.upload("Studio Share Card.PNG", png(1600, 900))
        self.assertEqual(status, 200, body)
        self.assertEqual(body["full"], SHARE, "the card lands in assets/img, next to og-cover.jpg")
        self.assertEqual((body["width"], body["height"]), (1200, 630))
        self.assertEqual(list(body), ["full", "width", "height"], "one file, no thumb")
        self.assertEqual(dims(self.tmp / SHARE), (1200, 630))
        self.assertEqual(self.leftovers(), [], "the temp files were cleaned up")
        self.assertFalse((self.tmp / "assets" / "img" / "thumbs").exists(),
                         "a share card belongs to no project, so there is no thumbs folder")

    def test_a_portrait_source_is_centre_cropped_not_stretched(self):
        # Three bands: a 2:1 portrait can only give the card a 600x315 slice, taken from the middle.
        red, green, blue = (255, 0, 0), (0, 200, 0), (0, 0, 255)
        status, body = self.upload("Portrait.png",
                                   bands_png(600, 1200, [(0, 400, red), (400, 800, green), (800, 1200, blue)]))
        self.assertEqual(status, 200, body)
        width, height, at = pixels(self.tmp / body["full"])
        self.assertEqual((width, height), (1200, 630))
        for y in (5, 315, 624):
            self.assertTrue(near(at(600, y), green), "row %d is %s, not the middle band" % (y, at(600, y)))
        self.assertTrue(near(at(20, 315), green), "the left edge of the crop is still the middle band")

    def test_a_small_source_is_enlarged_to_fill_the_card(self):
        status, body = self.upload("Just Big Enough.png", png(600, 315))
        self.assertEqual(status, 200, body)
        self.assertEqual(dims(self.tmp / body["full"]), (1200, 630),
                         "a half-size card would make og:image:width lie")

    def test_a_source_below_the_minimum_is_refused(self):
        status, body = self.upload("Too Small.png", png(400, 200))
        self.assertEqual(status, 400, body)
        self.assertIn("too small for a share card", body["error"])
        self.assertIn("600", body["error"])
        self.assertFalse((self.tmp / "assets" / "img" / "too-small.jpg").exists())
        self.assertEqual(self.leftovers(), [])

    def test_a_heic_card_works(self):
        src = self.tmp / "source.png"
        src.write_bytes(png(2000, 1200))
        heic = self.tmp / "source.heic"
        r = subprocess.run(["sips", "-s", "format", "heic", str(src), "--out", str(heic)],
                           capture_output=True, text=True)
        if r.returncode != 0 or not heic.is_file():
            self.skipTest("this Mac cannot write HEIC")
        status, body = self.upload("Phone Card.heic", heic.read_bytes())
        self.assertEqual(status, 200, body)
        self.assertEqual(dims(self.tmp / body["full"]), (1200, 630))


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestOgImage(MediaCase):
    def test_the_card_is_set_through_api_config(self):
        _s, up = self.upload("Studio Share Card.PNG", png(1600, 900))
        status, body = self.post("/api/config", {"og_image": up["full"]})
        self.assertEqual(status, 200, body)
        self.assertEqual(self.card(), SHARE)
        self.assertEqual(self.call("GET", "/api/state")[1]["config"]["og_image"], SHARE)
        self.assertIn("og:image\" content=\"https://example.com/" + SHARE,
                      self.text("index.html"), "the build picks the new card up")
        self.assertIn('<meta property="og:image" content="https://example.com/%s">' % SHARE,
                      self.text("work/alpha.html"), "and a case study carries it too")

    def test_clearing_the_card_goes_back_to_the_default(self):
        _s, up = self.upload("Studio Share Card.PNG", png(1600, 900))
        self.post("/api/config", {"og_image": up["full"]})
        status, body = self.post("/api/config", {"og_image": ""})
        self.assertEqual(status, 200, body)
        self.assertNotIn("og_image", self.data("site-config.json"),
                         "nothing is stored that repeats a code default")
        self.assertIn('content="https://example.com/assets/img/og-cover.jpg"', self.text("index.html"))

    def test_a_card_outside_assets_is_refused(self):
        for given in ("index.html", "assets/../projects.json", "../../etc/passwd", "assets/img/nope.jpg"):
            status, body = self.post("/api/config", {"og_image": given})
            self.assertEqual(status, 400, (given, body))
            self.assertNotIn("og_image", self.data("site-config.json"), given)


class TestPhotoBadges(MediaCase):
    def test_a_badge_is_set_and_cleared(self):
        status, body = self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0,
                                                "ai": True, "composite": True})
        self.assertEqual(status, 200, body)
        photo = self.photos()[0]
        self.assertIs(photo["ai"], True)
        self.assertIs(photo["composite"], True)
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0,
                                 "ai": False, "composite": False})
        photo = self.photos()[0]
        self.assertNotIn("ai", photo, "an unflagged photo carries no key at all")
        self.assertNotIn("composite", photo)
        self.assertEqual(photo["alt"], "alpha 1", "and it keeps its alt text")

    def test_one_badge_can_be_set_without_the_other(self):
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0, "ai": True})
        self.assertIs(self.photos()[0]["ai"], True)
        self.assertNotIn("composite", self.photos()[0])
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0, "composite": True})
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0, "ai": False})
        self.assertNotIn("ai", self.photos()[0])
        self.assertIs(self.photos()[0]["composite"], True)

    def test_an_alt_edit_keeps_both_badges(self):
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0,
                                 "ai": True, "composite": True})
        status, body = self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0,
                                                "alt": "A rewritten description"})
        self.assertEqual(status, 200, body)
        photo = self.photos()[0]
        self.assertEqual(photo["alt"], "A rewritten description")
        self.assertTrue(photo.get("ai"), "the alt edit dropped the AI badge")
        self.assertTrue(photo.get("composite"), "the alt edit dropped the Composite badge")

    def test_re_adding_a_photo_keeps_its_badge(self):
        item = {"full": "assets/img/alpha/01.jpg", "thumb": "assets/img/alpha/thumbs/01.jpg",
                "alt": "Added again", "ai": True, "composite": True}
        status, body = self.post("/api/media", {"slug": "alpha", "action": "add", "items": [item]})
        self.assertEqual(status, 200, body)
        added = self.photos()[-1]
        self.assertTrue(added.get("ai"), "clean_photo dropped the flags on the way in")
        self.assertTrue(added.get("composite"))
        self.assertEqual(added["alt"], "Added again")

    def test_an_unflagged_photo_is_written_exactly_as_before(self):
        item = {"full": "assets/img/alpha/02.jpg", "thumb": "assets/img/alpha/thumbs/02.jpg",
                "alt": "Plain"}
        self.post("/api/media", {"slug": "alpha", "action": "add", "items": [item]})
        self.assertEqual(self.photos()[-1], item, "no ai/composite key is invented")

    def test_a_stale_page_is_refused(self):
        status, body = self.call("POST", "/api/media", {"slug": "alpha", "action": "update", "index": 0,
                                                        "ai": True, "version": "0" * 16})
        self.assertEqual(status, 409, body)
        self.assertTrue(body.get("stale"))
        self.assertNotIn("ai", self.photos()[0])


if __name__ == "__main__":
    unittest.main()
