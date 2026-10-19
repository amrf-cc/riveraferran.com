"""Tests for the media inputs (read-only). Run: python3 -m unittest discover -s tests -v"""
import unittest

from support import ROOT, load

MASTHEAD_DIR = ROOT / "assets/img/masthead"


class TestManifest(unittest.TestCase):
    def setUp(self):
        self.manifest = load("assets-manifest.json")["projects"]

    def test_every_project_in_manifest(self):
        for p in load("projects.json")["projects"]:
            self.assertIn(p["slug"], self.manifest, "%s missing from assets-manifest.json" % p["slug"])

    def test_every_asset_file_exists_and_is_small(self):
        for slug, data in self.manifest.items():
            for photo in data.get("photos", []):
                for key in ("full", "thumb"):
                    path = ROOT / photo[key]
                    self.assertTrue(path.is_file(), "missing %s" % path)
                self.assertLess((ROOT / photo["full"]).stat().st_size, 2 * 1024 * 1024,
                                "%s too big" % photo["full"])
            for video in data.get("videos", []):
                if video.get("mp4"):
                    path = ROOT / video["mp4"]
                    self.assertTrue(path.is_file(), "missing %s" % path)
                    self.assertLess(path.stat().st_size, 16 * 1024 * 1024, "%s too big" % path)
                if video.get("poster"):
                    self.assertTrue((ROOT / video["poster"]).is_file(), "missing %s" % video["poster"])


class TestMediaJson(unittest.TestCase):
    def test_reel_and_motion_files_exist(self):
        media = load("media.json")
        missing = []
        for group in ("reels", "motion"):
            for item in media.get(group, []):
                for key in ("mp4", "poster"):
                    rel = item.get(key)
                    if rel and not (ROOT / rel).is_file():
                        missing.append("%s[%s].%s = %s" % (group, item.get("slug"), key, rel))
        self.assertEqual(missing, [], "\n".join(missing))

    def test_reels_point_at_real_projects(self):
        slugs = {p["slug"] for p in load("projects.json")["projects"]}
        for r in load("media.json").get("reels", []):
            if r.get("project"):
                self.assertIn(r["project"], slugs, r.get("slug"))


class TestMastheadAssets(unittest.TestCase):
    def test_masthead_layers_exist(self):
        config = load("site-config.json")
        for key in ("hero_image", "hero_image_phone", "hero_cutout"):
            self.assertTrue((ROOT / config[key]).is_file(), "missing %s" % config[key])
        self.assertTrue((ROOT / "assets/img/og-cover.jpg").is_file())


if __name__ == "__main__":
    unittest.main()
