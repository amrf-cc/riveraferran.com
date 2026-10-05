"""Tests for the asset pipeline. Run: python3 -m unittest discover -s tests -v"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import prepare_assets  # noqa: E402


class TestSourcesExist(unittest.TestCase):
    def test_volume_mounted(self):
        if not prepare_assets.SOURCE_ROOT.exists():
            self.skipTest("Selected Works volume not mounted")

    def test_all_manifest_sources_exist(self):
        if not prepare_assets.SOURCE_ROOT.exists():
            self.skipTest("Selected Works volume not mounted")
        projects = prepare_assets.load_projects()
        missing = prepare_assets.check_sources(projects)
        self.assertEqual(missing, [], "Missing source files: %s" % missing)


class TestBuiltAssets(unittest.TestCase):
    """These fail until prepare_assets.py has been run for real."""

    def setUp(self):
        manifest_path = ROOT / "assets-manifest.json"
        if not manifest_path.exists():
            self.skipTest("assets-manifest.json not generated yet")
        self.manifest = json.loads(manifest_path.read_text())
        self.projects = prepare_assets.load_projects()

    def test_every_project_in_manifest(self):
        for p in self.projects:
            self.assertIn(p["slug"], self.manifest["projects"],
                          "%s missing from manifest" % p["slug"])

    def test_every_asset_file_exists_and_is_small(self):
        for slug, data in self.manifest["projects"].items():
            for photo in data["photos"]:
                full = ROOT / photo["full"]
                thumb = ROOT / photo["thumb"]
                self.assertTrue(full.exists(), "missing %s" % full)
                self.assertTrue(thumb.exists(), "missing %s" % thumb)
                self.assertLess(full.stat().st_size, 2 * 1024 * 1024,
                                "%s too big — recompress" % full)
            for video in data["videos"]:
                mp4 = ROOT / video["mp4"]
                poster = ROOT / video["poster"]
                self.assertTrue(mp4.exists(), "missing %s" % mp4)
                self.assertTrue(poster.exists(), "missing %s" % poster)
                self.assertLess(mp4.stat().st_size, 16 * 1024 * 1024,
                                "%s too big — recompress" % mp4)


if __name__ == "__main__":
    unittest.main()
