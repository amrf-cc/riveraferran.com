"""Tests for scripts/regen_thumbs.py. Run: python3 -m unittest discover -s tests -v

Everything runs inside a tempfile root; the real assets are never touched.
Skipped when PIL is not importable.
"""
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image, ImageCms
except ImportError:
    Image = None
    ImageCms = None

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "regen_thumbs.py"


def load_module():
    spec = importlib.util.spec_from_file_location("regen_thumbs", str(SCRIPT))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _srgb_icc():
    if ImageCms is None:
        return None
    try:
        profile = ImageCms.createProfile("sRGB")
        return ImageCms.ImageCmsProfile(profile).tobytes()
    except Exception:
        return None


@unittest.skipIf(Image is None, "PIL not installed")
class TestRegenThumbs(unittest.TestCase):
    FULL = "assets/img/sample/01.jpg"
    THUMB = "assets/img/sample/thumbs/01.jpg"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="regen-thumbs-")
        self.root = Path(self.tmp)
        self.mod = load_module()
        self.icc = _srgb_icc()

        self._write_manifest([
            {"full": "assets/img/sample/01.jpg",
             "thumb": "assets/img/sample/thumbs/01.jpg", "alt": "one"},
            {"full": "assets/img/sample/02.jpg",
             "thumb": "assets/img/sample/thumbs/02.jpg", "alt": "two"},
        ])
        self._make_full("assets/img/sample/01.jpg", (2000, 1333))
        self._make_full("assets/img/sample/02.jpg", (2000, 1333))
        self._make_thumb("assets/img/sample/thumbs/01.jpg", (640, 427))
        self._make_thumb("assets/img/sample/thumbs/02.jpg", (640, 427))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_manifest(self, photos):
        data = {"projects": {"sample": {"photos": photos}}}
        (self.root / "assets-manifest.json").write_text(json.dumps(data))

    def _make_full(self, rel, size):
        im = Image.effect_noise(size, 128).convert("RGB")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        kwargs = {"icc_profile": self.icc} if self.icc else {}
        im.save(path, "JPEG", quality=90, **kwargs)

    def _make_thumb(self, rel, size):
        im = Image.effect_noise(size, 128).convert("RGB")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        im.save(path, "JPEG", quality=70)

    def test_regenerates_to_retina(self):
        stats = self.mod.regen(str(self.root))
        self.assertEqual(stats["regenerated"], 2)
        self.assertEqual(stats["skipped"], 0)
        self.assertEqual(stats["missing"], 0)
        for rel in (self.THUMB, "assets/img/sample/thumbs/02.jpg"):
            with Image.open(self.root / rel) as im:
                self.assertEqual(max(im.size), 1280)
                self.assertEqual(im.format, "JPEG")
                self.assertTrue(im.info.get("progressive"))
                if self.icc:
                    self.assertEqual(im.info.get("icc_profile"), self.icc)

    def test_second_run_skips(self):
        self.mod.regen(str(self.root))
        stats = self.mod.regen(str(self.root))
        self.assertEqual(stats["regenerated"], 0)
        self.assertEqual(stats["skipped"], 2)

    def test_force_regenerates(self):
        self.mod.regen(str(self.root))
        stats = self.mod.regen(str(self.root), force=True)
        self.assertEqual(stats["regenerated"], 2)
        self.assertEqual(stats["skipped"], 0)

    def test_small_full_not_upscaled(self):
        self._make_full("assets/img/sample/01.jpg", (900, 600))
        (self.root / self.THUMB).unlink()
        stats = self.mod.regen(str(self.root))
        self.assertEqual(stats["regenerated"], 2)
        with Image.open(self.root / self.THUMB) as im:
            self.assertEqual(max(im.size), 900)

    def test_missing_full_counted(self):
        manifest = self.root / "assets-manifest.json"
        data = json.loads(manifest.read_text())
        data["projects"]["sample"]["photos"].append(
            {"full": "assets/img/sample/99.jpg",
             "thumb": "assets/img/sample/thumbs/99.jpg", "alt": "gone"})
        manifest.write_text(json.dumps(data))
        stats = self.mod.regen(str(self.root), force=True)
        self.assertEqual(stats["missing"], 1)
        self.assertEqual(stats["regenerated"], 2)

    def test_dry_run_writes_nothing(self):
        before = (self.root / self.THUMB).read_bytes()
        stats = self.mod.regen(str(self.root), dry_run=True)
        after = (self.root / self.THUMB).read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(stats["regenerated"], 2)


if __name__ == "__main__":
    unittest.main()
