"""Tests for the asset pipeline. Run: python3 -m unittest discover -s tests -v"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ingest_media  # noqa: E402
import prepare_assets  # noqa: E402

GALLERY_PATHS = [
    "assets/img/acai-express/01.jpg",
]

MASTHEAD_DIR = ROOT / "assets/img/masthead"
MASTHEAD_DESKTOP_BACK = MASTHEAD_DIR / "tbs-desktop-back.jpg"
MASTHEAD_DESKTOP_FRONT = MASTHEAD_DIR / "tbs-desktop-front.webp"
MASTHEAD_PHONE_BACK = MASTHEAD_DIR / "tbs-phone-back.jpg"


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

    def test_photo_counts_match_projects(self):
        for p in self.projects:
            entry = self.manifest["projects"][p["slug"]]
            self.assertEqual(len(entry["photos"]), len(p["photos"]),
                             "%s photo count differs between projects.json "
                             "and manifest" % p["slug"])

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
                mp4 = video.get("mp4") or ""
                poster = video.get("poster") or ""
                # Livid-only videos have an empty mp4 (no self-hosted fallback).
                if mp4:
                    mp4_path = ROOT / mp4
                    self.assertTrue(mp4_path.exists(), "missing %s" % mp4_path)
                    self.assertLess(mp4_path.stat().st_size, 16 * 1024 * 1024,
                                    "%s too big — recompress" % mp4_path)
                if poster:
                    poster_path = ROOT / poster
                    self.assertTrue(poster_path.exists(),
                                    "missing %s" % poster_path)


class TestGalleryPhotoUntouched(unittest.TestCase):
    def test_gallery_photo_unchanged(self):
        try:
            probe = subprocess.run(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=str(ROOT), capture_output=True, text=True)
        except OSError:
            self.skipTest("git not available")
        if probe.returncode != 0 or probe.stdout.strip() != "true":
            self.skipTest("ROOT is not a git repository")
        result = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--"] + GALLERY_PATHS,
            cwd=str(ROOT), capture_output=True)
        self.assertNotEqual(result.returncode, 1,
                            "gallery photo was modified")


class TestMastheadAssets(unittest.TestCase):
    def test_masthead_assets(self):
        for path in (MASTHEAD_DESKTOP_BACK, MASTHEAD_DESKTOP_FRONT,
                     MASTHEAD_PHONE_BACK):
            self.assertTrue(path.exists(), "missing %s" % path)
        self.assertLess(MASTHEAD_DESKTOP_BACK.stat().st_size, 600 * 1024,
                        "desktop back JPEG too big — recompress")
        self.assertLess(MASTHEAD_DESKTOP_FRONT.stat().st_size, 450 * 1024,
                        "desktop front WebP too big — recompress")
        self.assertLess(MASTHEAD_PHONE_BACK.stat().st_size, 750 * 1024,
                        "phone back JPEG too big — recompress")
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("PIL not importable; skipping dimension checks")
        with Image.open(MASTHEAD_DESKTOP_BACK) as im:
            self.assertEqual(im.size, (3200, 1800))
            self.assertEqual(im.format, "JPEG")
        with Image.open(MASTHEAD_DESKTOP_FRONT) as im:
            self.assertEqual(im.size, (3200, 1800))
            self.assertIn("A", im.getbands())
        with Image.open(MASTHEAD_PHONE_BACK) as im:
            self.assertEqual(im.size, (1170, 2535))


class TestIngestIdempotent(unittest.TestCase):
    def test_nothing_to_ingest(self):
        if not ingest_media.MEDIA_ROOT.exists():
            self.skipTest("MEDIA_ROOT not mounted")
        manifest_path = ROOT / "assets-manifest.json"
        if not manifest_path.exists():
            self.skipTest("assets-manifest.json not generated yet")
        projects = json.loads((ROOT / "projects.json").read_text())
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(ingest_media.plan(projects, manifest), [])


class TestIngestSkipsRemoved(unittest.TestCase):
    """A photo deleted in the content manager is recorded in the project's
    removed_photos list; ingest must never re-import its original."""

    def test_removed_photos_not_reingested(self):
        import tempfile
        slug, folder = next(iter(ingest_media.FOLDERS.items()))
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / folder).mkdir()
            # Ingest only looks at extension and content hash, so distinct
            # bytes are enough; no image library needed.
            for name in ("kept.jpg", "deleted.jpg", "new.jpg"):
                (Path(tmp) / folder / name).write_bytes(name.encode())
            project = {"slug": slug, "client": "X", "photos": [{"src": folder + "/kept.jpg", "alt": "k"}],
                       "videos": [], "removed_photos": [folder + "/deleted.jpg"]}
            manifest = {"projects": {slug: {"photos": [{}], "videos": []}}}
            plan = ingest_media.plan({"projects": [project]}, manifest, media_root=Path(tmp))
            names = [i["rel_src"].split("/")[-1] for e in plan for i in e["items"]]
            self.assertEqual(names, ["new.jpg"])


if __name__ == "__main__":
    unittest.main()
