"""Tests for tools/cms.py: the pure helpers, and the write guards over HTTP.

The HTTP tests point the editor at a temp copy of the data (CMS_REPO), so the real
projects.json, site-config.json and assets-manifest.json are never touched.
"""
import importlib.util
import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from support import ROOT, DATA_FILES

CMS_PATH = ROOT / "tools" / "cms.py"


def load_cms(repo):
    """Import tools/cms.py bound to `repo` (it reads CMS_REPO once, at import)."""
    old = os.environ.get("CMS_REPO")
    os.environ["CMS_REPO"] = str(repo)
    try:
        spec = importlib.util.spec_from_file_location("cms_under_test", str(CMS_PATH))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("CMS_REPO", None)
        else:
            os.environ["CMS_REPO"] = old


CMS = load_cms(ROOT)


class TestProjectSave(unittest.TestCase):
    """The save must only touch what the form owns. An earlier version rebuilt the project
    from a fixed key list and silently dropped standalone, demo, draft_only and friends."""

    EXISTING = {"slug": "ai", "client": "AI", "featured": 2, "title": "T", "oneliner": "o",
                "tags": ["ai"], "brief": "b", "did": ["x"], "scope": [], "result": "",
                "demo": "keep me", "category": "", "standalone": "ai.html#assistant",
                "draft_only": False, "hidden": True, "confirm": ["year"], "ai_steps": "steps"}

    def save(self, **raw):
        return CMS.clean_project(dict(raw, slug="ai"), dict(self.EXISTING))

    def test_unknown_keys_survive_in_their_original_order(self):
        out = self.save(title="New title", brief="New brief")
        self.assertEqual(list(out), list(self.EXISTING))
        for k in ("demo", "standalone", "draft_only", "hidden", "confirm", "ai_steps"):
            self.assertEqual(out[k], self.EXISTING[k], k)
        self.assertEqual(out["title"], "New title")

    def test_standalone_project_keeps_its_blank_category(self):
        self.assertEqual(self.save(category="")["category"], "")

    def test_featured_order_is_a_whole_number(self):
        self.assertEqual(self.save(featured="2")["featured"], 2)
        self.assertEqual(self.save(featured=3)["featured"], 3)
        self.assertEqual(self.save(featured=True)["featured"], 2, "a bare true must keep the current order")
        self.assertIs(self.save(featured="")["featured"], False)
        with self.assertRaises(ValueError):
            self.save(featured="soon")

    def test_featured_true_on_an_unfeatured_project_becomes_the_lead(self):
        out = CMS.clean_project({"slug": "p", "featured": True}, {"slug": "p", "client": "C", "title": "T"})
        self.assertEqual(out["featured"], 1)

    def test_required_fields_and_new_project_defaults(self):
        with self.assertRaises(ValueError):
            CMS.clean_project({"title": "x"})
        out = CMS.clean_project({"client": "Café Colibrí", "title": "Menu shoot"})
        self.assertEqual(out["slug"], "cafe-colibri-menu-shoot")
        self.assertEqual(out["category"], "food-beverage")
        self.assertEqual(list(out)[:3], ["slug", "client", "featured"])


class TestMediaHelpers(unittest.TestCase):
    def test_video_links_always_become_embed_urls(self):
        want = "https://livid.com/embed/HGGzzr4EBITa?livid_logo=0&fullscreen=1&title=1"
        for given in ("https://livid.com/watch/HGGzzr4EBITa", "https://livid.com/embed/HGGzzr4EBITa",
                      "https://livid.com/embed/HGGzzr4EBITa?title=0", "HGGzzr4EBITa"):
            self.assertEqual(CMS.embed_url(given), want, given)

    def test_garbage_is_not_a_video(self):
        for bad in ("", "  ", "x", "https://livid.com/", "https://livid.com/videos/86e95903-d513-49ca-9f79-0a1b2c3d4e5f"):
            with self.assertRaises(ValueError):
                CMS.embed_url(bad)

    def test_added_videos_are_normalised(self):
        v = CMS.clean_video({"title": " Demo ", "livid": "https://livid.com/watch/HGGzzr4EBITa"})
        self.assertEqual(v["title"], "Demo")
        self.assertTrue(v["livid"].startswith("https://livid.com/embed/HGGzzr4EBITa?"))
        self.assertEqual((v["poster"], v["ratio"], v["mp4"]), ("", "16 / 9", ""))

    def test_assets_paths_cannot_escape(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "assets/../projects.json", ""):
            with self.assertRaises(ValueError):
                CMS.safe_asset(bad)
        self.assertEqual(CMS.safe_asset("assets/css/styles.css").name, "styles.css")
        with self.assertRaises(ValueError):
            CMS.clean_photo({"full": "../x.jpg"})


class TestStaleWriteGuard(unittest.TestCase):
    """A page loaded before the data changed must not be able to overwrite it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-test-"))
        for name in DATA_FILES:
            shutil.copy2(str(ROOT / name), str(cls.tmp / name))
        (cls.tmp / "tools").mkdir()
        shutil.copy2(str(ROOT / "tools" / "cms.html"), str(cls.tmp / "tools" / "cms.html"))
        cls.cms = load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def call(self, path, body=None):
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path),
                                     data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_opening_the_page_never_writes_the_site(self):
        before = {p.name: p.stat().st_mtime_ns for p in self.tmp.glob("*.html")}
        status, d = self.call("/api/content")
        self.assertEqual(status, 200)
        self.assertIn("version", d)
        self.assertIn("ready", d["build"])
        self.assertEqual({p.name: p.stat().st_mtime_ns for p in self.tmp.glob("*.html")}, before)

    def test_stale_or_missing_version_is_refused_and_nothing_changes(self):
        projects = (self.tmp / "projects.json").read_bytes()
        for body in ({"slug": "chip-city", "client": "X", "title": "Y"},
                     {"slug": "chip-city", "client": "X", "title": "Y", "version": "0000"}):
            status, d = self.call("/api/project", body)
            self.assertEqual(status, 409, d)
            self.assertTrue(d.get("stale"))
        status, d = self.call("/api/media", {"slug": "chip-city", "kind": "photo", "action": "remove",
                                             "index": 0, "version": "0000"})
        self.assertEqual(status, 409)
        status, d = self.call("/api/config", {"headline": "x", "version": "0000"})
        self.assertEqual(status, 409)
        self.assertEqual((self.tmp / "projects.json").read_bytes(), projects)

    def test_version_changes_when_data_changes(self):
        v1 = self.cms.data_version()
        p = self.tmp / "media.json"
        raw = p.read_bytes()
        try:
            p.write_bytes(raw + b" ")
            self.assertNotEqual(self.cms.data_version(), v1)
        finally:
            p.write_bytes(raw)
        self.assertEqual(self.cms.data_version(), v1)

    def test_current_version_gets_past_the_guard(self):
        _, d = self.call("/api/content")
        status, out = self.call("/api/project", {"client": "", "title": "", "version": d["version"]})
        self.assertEqual(status, 400, out)          # validation answers, not the stale guard
        self.assertIn("Client is required", out["error"])


if __name__ == "__main__":
    unittest.main()
