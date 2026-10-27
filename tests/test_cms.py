"""Tests for tools/cms.py, the Site Manager server.

They run against the synthetic site in cms_fixture.py (never the real content), through the real HTTP
handler on a random port, so what is checked is what the browser gets. Every test starts from a
pristine copy of the fixture.
"""
import json
import re
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest import mock
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Tuple

import cms_fixture as F
from support import ROOT

CMS = F.load_cms(ROOT)          # the module bound to the real repo, for the pure helpers
HAS_SIPS = shutil.which("sips") is not None


def dims(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", "-g", "format", str(path)],
                         capture_output=True, text=True).stdout
    found = [re.search(pattern, out) for pattern in (r"pixelWidth: (\d+)", r"pixelHeight: (\d+)", r"format: (\w+)")]
    assert all(found), "sips could not read %s" % path
    w, h, fmt = (m.group(1) for m in found if m)
    return int(w), int(h), fmt


class TestPureHelpers(unittest.TestCase):
    def test_json_serialisation_is_idempotent_on_the_real_data(self):
        """load -> dump must give the same bytes, or the first save rewrites the whole file."""
        for name in CMS.DATA_NAMES:
            text = (ROOT / name).read_text(encoding="utf-8")
            self.assertEqual(CMS.dump(json.loads(text)), text, name)

    def test_livid_links_always_become_embed_urls(self):
        want = "https://livid.com/embed/HGGzzr4EBITa?livid_logo=0&fullscreen=1&title=1"
        for given in ("https://livid.com/watch/HGGzzr4EBITa", "https://livid.com/embed/HGGzzr4EBITa",
                      "https://livid.com/embed/HGGzzr4EBITa?title=0", "HGGzzr4EBITa", "  HGGzzr4EBITa  "):
            self.assertEqual(CMS.embed_url(given), want, given)

    def test_garbage_is_not_a_video(self):
        for bad in ("", "  ", "x", "https://livid.com/", "https://example.com/watch/HGGzzr4EBITa",
                    "https://livid.com/videos/86e95903-d513-49ca-9f79-0a1b2c3d4e5f"):
            with self.assertRaises(ValueError):
                CMS.embed_url(bad)

    def test_a_deliberate_title_0_embed_is_not_rewritten_on_resave(self):
        old = "https://livid.com/embed/HGGzzr4EBITa?livid_logo=0&fullscreen=1&title=0"
        self.assertEqual(CMS.keep_or_embed("https://livid.com/watch/HGGzzr4EBITa", old), old)
        self.assertTrue(CMS.keep_or_embed("https://livid.com/watch/OTHEROTHER1", old).endswith("title=1"))

    def test_asset_paths_cannot_escape(self):
        for bad in ("../../etc/passwd", "/etc/passwd", "assets/../projects.json", "", None):
            with self.assertRaises(ValueError):
                CMS.safe_asset(bad)
        self.assertEqual(CMS.safe_asset("assets/css/styles.css").name, "styles.css")

    def test_slugs_fold_accents(self):
        self.assertEqual(CMS.slugify("Caf\u00e9 Colibr\u00ed \u2014 Menu!"), "cafe-colibri-menu")
        self.assertEqual(CMS.unique_slug("a", {"a", "a-2"}), "a-3")

    def test_featured_is_a_whole_number(self):
        f = CMS.featured_order
        self.assertEqual((f("2", 0), f(3, 0), f(True, 2), f(True, 0), f(False, 2), f("", 5), f(None, 5)), (2, 3, 2, 1, 0, 0, 0))
        with self.assertRaises(ValueError):
            f("soon", 0)


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=None):
        return self.body.encode("utf-8")


class TestLividCheck(unittest.TestCase):
    """The player check the manager has: a new Livid upload is Private with embedding off, and Livid then
    serves a generic page at the same URL, which is a dead player on the site."""

    def check(self, body=None, error=None):
        with mock.patch("urllib.request.urlopen") as fake:
            if error:
                fake.side_effect = error
            else:
                fake.return_value = FakeResponse(body)
            return CMS.livid_status("HGGzzr4EBITa")

    def test_a_public_video_plays_and_its_title_comes_back(self):
        self.assertEqual(self.check("<html><title>Wisin &amp; Jhay \u2014 Fiel</title><script src='https://api.livid.com/x'>"),
                         (True, "Wisin &amp; Jhay \u2014 Fiel"))

    def test_the_generic_page_means_the_embed_is_dead(self):
        self.assertEqual(self.check("<html><title>Livid</title><body>This video does not exist</body>"), (False, ""))
        self.assertEqual(self.check("<html><title>Some title</title><body>no api reference</body>"), (False, ""))

    def test_an_unreachable_livid_is_not_a_verdict(self):
        self.assertEqual(self.check(error=OSError("offline")), (None, ""))

    def test_it_asks_for_the_embed_of_that_id_only(self):
        with mock.patch("urllib.request.urlopen") as fake:
            fake.return_value = FakeResponse("<title>Livid</title>")
            CMS.livid_status("HGGzzr4EBITa")
        self.assertEqual(fake.call_args[0][0].full_url, "https://livid.com/embed/HGGzzr4EBITa")


class TestProjectCleaning(unittest.TestCase):
    """A save edits the form's fields on a copy of the stored project. An early version rebuilt the
    project from a fixed key list and silently dropped standalone, demo, hidden and friends."""

    EXISTING = {"slug": "ai", "client": "AI", "featured": 2, "title": "T", "oneliner": "o", "tags": ["ai"],
                "brief": "b", "did": ["x"], "scope": [], "result": "", "demo": "keep me", "category": "",
                "standalone": "ai.html#assistant", "draft_only": False, "hidden": True, "confirm": ["year"]}

    def save(self, **raw):
        return CMS.clean_project(dict(raw, slug="ai"), dict(self.EXISTING), {"food"})

    def test_unknown_keys_survive_in_their_original_order(self):
        out = self.save(title="New title", brief="New brief")
        self.assertEqual(list(out), list(self.EXISTING))
        for k in ("demo", "standalone", "draft_only", "hidden", "confirm"):
            self.assertEqual(out[k], self.EXISTING[k], k)

    def test_a_blank_category_is_allowed_an_unknown_one_is_not(self):
        self.assertEqual(self.save(category="")["category"], "")
        with self.assertRaises(ValueError):
            CMS.clean_project({"client": "C", "title": "T", "category": "nope"}, None, {"food"})

    def test_featured_and_hidden(self):
        self.assertEqual(self.save(featured="3")["featured"], 3)
        self.assertIs(self.save(featured="")["featured"], False)
        self.assertNotIn("hidden", self.save(hidden=False))
        self.assertIs(self.save(hidden=True)["hidden"], True)

    def test_required_fields_and_new_project_defaults(self):
        with self.assertRaises(ValueError):
            CMS.clean_project({"title": "x"})
        out = CMS.clean_project({"client": "Caf\u00e9 Colibr\u00ed", "title": "Menu shoot"})
        self.assertEqual((out["slug"], out["category"], list(out)[:3]),
                         ("cafe-colibri-menu-shoot", "food-beverage", ["slug", "client", "featured"]))


class CmsCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-test-"))
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
        self.cms.run_build()                     # the real site is always built; so is the fixture

    # -- helpers ------------------------------------------------------------------------------
    def call(self, method, path, body=None, raw=None, headers=None) -> Tuple[int, Any]:
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        h = {"Content-Type": "application/octet-stream" if raw is not None else "application/json"}
        h.update(headers or {})
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data, method=method, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, payload, ctype = r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            status, payload, ctype = e.code, e.read(), e.headers.get("Content-Type", "")
        return status, (json.loads(payload) if "json" in ctype else payload)

    def version(self):
        return self.call("GET", "/api/state")[1]["version"]

    def post(self, path, body=None, method="POST"):
        return self.call(method, path, dict(body or {}, version=self.version()))

    def data(self, name):
        return json.loads((self.tmp / name).read_text(encoding="utf-8"))

    def project(self, slug):
        return next(p for p in self.data("projects.json")["projects"] if p["slug"] == slug)

    def text(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    def slugs(self):
        return [p["slug"] for p in self.data("projects.json")["projects"]]


class TestState(CmsCase):
    def test_state_has_what_the_ui_draws(self):
        st, d = self.call("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual([p["slug"] for p in d["projects"]], self.slugs())
        self.assertTrue(d["manifest"]["projects"]["alpha"]["photos"][0]["v"])
        self.assertEqual(d["constants"]["ai_feature"], "ai-production-assistant")
        self.assertEqual(d["media"]["ai_live"], ["rodowita-water"])
        self.assertEqual((d["resume"]["exists"], d["resume"]["name"]), (True, "Resume.pdf"))
        self.assertEqual((d["build"]["ready"], d["build"]["blockers"]), (True, 0))
        self.assertEqual(d["version"], self.cms.data_version())

    def test_reading_never_writes(self):
        files = list(self.tmp.glob("*.html")) + [self.tmp / n for n in self.cms.DATA_NAMES]
        before = {f: f.stat().st_mtime_ns for f in files}
        self.call("GET", "/api/state")
        self.call("GET", "/api/release")
        self.assertEqual({f: f.stat().st_mtime_ns for f in files}, before)
        self.assertFalse((self.tmp / "tools" / "backups").exists())

    def test_the_page_and_its_scripts_are_served(self):
        for path in ("/", "/app.css", "/app.js", "/project.js", "/site.js"):
            st, body = self.call("GET", path)
            self.assertEqual(st, 200, path)
        st, body = self.call("GET", "/")
        self.assertIn(b"Site Manager", body)
        self.assertEqual(self.call("GET", "/tools/cms.py")[0], 404)
        self.assertEqual(self.call("GET", "/../cms.py")[0], 404)

    def test_the_livid_endpoint_refuses_a_bad_link_and_normalises_a_good_one(self):
        self.assertEqual(self.call("GET", "/api/livid?url=" + urllib.request.quote("https://example.com/watch/xyz12345"))[0], 400)
        # the network call itself is covered by TestLividCheck; here only the endpoint's own work
        with mock.patch.object(self.cms, "livid_status", return_value=(True, "Fiel")) as fake:
            st, d = self.call("GET", "/api/livid?url=" + urllib.request.quote("https://livid.com/watch/HGGzzr4EBITa"))
        self.assertEqual(fake.call_args[0][0], "HGGzzr4EBITa")
        self.assertEqual((st, d["playable"], d["title"]), (200, True, "Fiel"))
        self.assertEqual(d["embed"], "https://livid.com/embed/HGGzzr4EBITa?livid_logo=0&fullscreen=1&title=1")

    def test_foreign_hosts_and_origins_are_refused(self):
        self.assertEqual(self.call("GET", "/api/state", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.call("POST", "/api/build", {}, headers={"Origin": "https://evil.example"})[0], 403)


class TestVisibility(CmsCase):
    def hide(self, slug, hidden=True):
        return self.post("/api/projects/%s/visibility" % slug, {"hidden": hidden})

    def test_hide_then_show_is_reversible_to_the_byte(self):
        original = (self.tmp / "projects.json").read_bytes()
        st, d = self.hide("beta")
        self.assertEqual(st, 200)
        self.assertTrue(d["hidden"])
        self.assertIs(self.project("beta")["hidden"], True)
        self.assertFalse((self.tmp / "work" / "beta.html").exists(), "the case-study page must go")
        self.assertNotIn("work/beta.html", self.text("work.html"))
        st, d = self.hide("beta", False)
        self.assertEqual(st, 200)
        self.assertNotIn("hidden", self.project("beta"))
        self.assertTrue((self.tmp / "work" / "beta.html").exists())
        self.assertIn("work/beta.html", self.text("work.html"))
        self.assertEqual((self.tmp / "projects.json").read_bytes(), original)

    def test_it_says_what_the_toggle_does_to_the_site(self):
        cases = {"beta": {"the Work page and its own case-study page", "the home page featured block"},
                 "gamma": {"the Work page and its own case-study page"},
                 "ai-production-assistant": {"the AI & Workflow page feature", "the home page featured block"},
                 "rodowita-water": {"the Work page and its own case-study page",
                                    "the AI & Workflow \"Ran live\" cards"}}
        for slug, want in cases.items():
            st, d = self.hide(slug)
            self.assertEqual((st, set(d["affects"])), (200, want), slug)
            self.hide(slug, False)

    def test_hiding_the_ai_feature_removes_its_block_and_showing_it_brings_it_back(self):
        self.assertIn('id="assistant"', self.text("ai.html"))
        self.hide("ai-production-assistant")
        self.assertNotIn('id="assistant"', self.text("ai.html"))
        self.hide("ai-production-assistant", False)
        self.assertIn('id="assistant"', self.text("ai.html"))

    def test_hiding_the_lead_hands_the_homepage_to_the_next_featured_project(self):
        self.hide("alpha")
        index = self.text("index.html")
        self.assertNotIn("work/alpha.html", index)
        self.assertIn("work/beta.html", index)

    def test_a_toggle_that_changes_nothing_writes_nothing(self):
        before = (self.tmp / "projects.json").stat().st_mtime_ns
        st, d = self.hide("alpha", False)             # already visible
        self.assertEqual((st, d["changed"]), (200, False))
        self.assertEqual((self.tmp / "projects.json").stat().st_mtime_ns, before)
        self.assertFalse((self.tmp / "tools" / "backups").exists())

    def test_the_site_stays_a_release_after_every_toggle(self):
        self.hide("gamma")
        self.assertNotIn("draft-banner", self.text("index.html"))
        st, d = self.call("GET", "/api/state")
        self.assertTrue(d["build"]["ready"])

    def test_unknown_project_and_stale_page(self):
        self.assertEqual(self.hide("nope")[0], 404)
        before = (self.tmp / "projects.json").read_bytes()
        st, d = self.call("POST", "/api/projects/beta/visibility", {"hidden": True, "version": "0000"})
        self.assertEqual((st, d.get("stale")), (409, True))
        self.assertEqual((self.tmp / "projects.json").read_bytes(), before)

    def test_save_never_touches_the_hidden_flag(self):
        self.hide("beta")
        st, d = self.post("/api/projects", {"slug": "beta", "client": "Beta", "title": "New title"})
        self.assertEqual(st, 200)
        self.assertIs(self.project("beta")["hidden"], True)
        self.assertEqual(self.project("beta")["title"], "New title")


class TestOrder(CmsCase):
    def test_the_whole_list_in_a_new_order(self):
        new = list(reversed(self.slugs()))
        st, d = self.post("/api/projects/order", {"slugs": new})
        self.assertEqual(st, 200)
        self.assertEqual(self.slugs(), new)

    def test_partial_duplicate_and_unknown_lists_are_refused(self):
        before = (self.tmp / "projects.json").read_bytes()
        full = self.slugs()
        for bad in (full[:-1], full + [full[0]], ["nope"] + full[1:], "alpha", [1, 2, 3, 4, 5]):
            st, d = self.post("/api/projects/order", {"slugs": bad})
            self.assertEqual(st, 400, bad)
        self.assertEqual((self.tmp / "projects.json").read_bytes(), before)

    def test_the_work_page_follows_the_order_inside_a_group(self):
        self.post("/api/projects/order", {"slugs": ["beta", "alpha", "gamma", "ai-production-assistant", "rodowita-water"]})
        work = self.text("work.html")
        self.assertLess(work.index("work/beta.html"), work.index("work/alpha.html"))


class TestProjects(CmsCase):
    def test_create_makes_the_manifest_entry_and_the_page(self):
        st, d = self.post("/api/projects", {"client": "Caf\u00e9 Colibr\u00ed", "title": "Menu shoot", "category": "food"})
        self.assertEqual((st, d["action"], d["slug"]), (200, "created", "cafe-colibri-menu-shoot"))
        self.assertEqual(self.data("assets-manifest.json")["projects"]["cafe-colibri-menu-shoot"],
                         {"photos": [], "videos": []})
        self.assertTrue((self.tmp / "work" / "cafe-colibri-menu-shoot.html").exists())

    def test_a_duplicate_name_gets_a_suffix(self):
        self.post("/api/projects", {"client": "Alpha", "title": "Alpha title", "slug": "alpha-two"})
        st, d = self.post("/api/projects", {"client": "Zed", "title": "Z", "slug": "alpha-two"})
        self.assertEqual(d["action"], "updated")          # the slug names an existing project: that is an update
        st, d = self.post("/api/projects", {"client": "Alpha", "title": "Alpha title"})
        self.assertEqual((st, d["slug"]), (200, "alpha-alpha-title"))
        st, d = self.post("/api/projects", {"client": "Alpha", "title": "Alpha title"})
        self.assertEqual(d["slug"], "alpha-alpha-title-2")

    def test_validation(self):
        self.assertEqual(self.post("/api/projects", {"title": "no client"})[0], 400)
        self.assertEqual(self.post("/api/projects", {"client": "C", "title": "T", "category": "nope"})[0], 400)
        self.assertEqual(self.post("/api/projects", {"client": "C", "title": "T", "featured": "soon"})[0], 400)

    def test_update_keeps_every_field_the_form_does_not_own(self):
        st, d = self.post("/api/projects", {"slug": "ai-production-assistant", "client": "AI assistant",
                                            "title": "Agent v2", "category": "", "featured": "2"})
        p = self.project("ai-production-assistant")
        self.assertEqual((p["title"], p["featured"], p["standalone"], p["category"]), ("Agent v2", 2, "ai.html#assistant", ""))

    def test_delete_archives_unlinks_and_trashes(self):
        st, d = self.call("DELETE", "/api/projects/alpha?version=" + self.version())
        self.assertEqual(st, 200, d)
        self.assertNotIn("alpha", self.slugs())
        self.assertNotIn("alpha", self.data("assets-manifest.json")["projects"])
        self.assertFalse((self.tmp / "work" / "alpha.html").exists())
        archived = json.loads((self.tmp / d["archive"]).read_text(encoding="utf-8"))
        self.assertEqual((archived["project"]["slug"], len(archived["manifest"]["photos"])), ("alpha", 2))
        self.assertEqual(self.data("media.json")["reels"][0]["project"], "", "the reel must stay, unlinked")
        self.assertEqual(d["unlinked_reels"], ["alpha-film"])
        trashed = sorted(p.name.split("-", 2)[-1] for p in (self.tmp / "tools" / "removed-media").iterdir())
        self.assertTrue(all(n.endswith(".jpg") for n in trashed) and len(trashed) == 5, trashed)
        self.assertFalse((self.tmp / "assets" / "img" / "alpha").exists())

    def test_delete_keeps_a_file_another_project_still_uses(self):
        m = self.data("assets-manifest.json")
        m["projects"]["beta"]["photos"][0] = dict(m["projects"]["alpha"]["photos"][0])   # beta shows alpha's first photo
        (self.tmp / "assets-manifest.json").write_text(self.cms.dump(m), encoding="utf-8")
        st, d = self.call("DELETE", "/api/projects/alpha?version=" + self.version())
        self.assertEqual(st, 200)
        self.assertTrue((self.tmp / "assets/img/alpha/01.jpg").exists(), "still used by beta")
        self.assertFalse((self.tmp / "assets/img/alpha/02.jpg").exists())

    def test_delete_drops_the_project_from_the_ai_live_list(self):
        st, d = self.call("DELETE", "/api/projects/rodowita-water?version=" + self.version())
        self.assertEqual(st, 200)
        self.assertEqual(self.data("media.json")["ai_live"], [])

    def test_delete_unknown_and_stale(self):
        self.assertEqual(self.call("DELETE", "/api/projects/nope?version=" + self.version())[0], 404)
        self.assertEqual(self.call("DELETE", "/api/projects/alpha?version=0000")[0], 409)
        self.assertIn("alpha", self.slugs())


@unittest.skipUnless(HAS_SIPS, "needs macOS sips")
class TestPhotos(CmsCase):
    def upload(self, name, image, **q):
        query = "&".join("%s=%s" % (k, urllib.request.quote(v)) for k, v in dict({"slug": "alpha", "kind": "photo", "name": name}, **q).items())
        return self.call("POST", "/api/upload?" + query, raw=image)

    def test_an_upload_becomes_a_full_and_a_thumb_in_the_site_pipeline(self):
        st, d = self.upload("Big Shot.PNG", F.png(2400, 1600))
        self.assertEqual(st, 200, d)
        self.assertEqual((d["full"], d["thumb"]), ("assets/img/alpha/big-shot.jpg", "assets/img/alpha/thumbs/big-shot.jpg"))
        self.assertEqual(dims(self.tmp / d["full"]), (2000, 1333, "jpeg"))
        self.assertEqual(dims(self.tmp / d["thumb"]), (1280, 853, "jpeg"))
        self.assertEqual(list((self.tmp / "assets/img/alpha").glob(".incoming*")), [], "temp file left behind")

    def test_heic_uploads_work(self):
        src = self.tmp / "source.png"
        src.write_bytes(F.png(64, 48))
        heic = self.tmp / "source.heic"
        r = subprocess.run(["sips", "-s", "format", "heic", str(src), "--out", str(heic)], capture_output=True, text=True)
        if r.returncode != 0 or not heic.is_file():
            self.skipTest("this Mac cannot encode HEIC")
        st, d = self.upload("IMG_0001.HEIC", heic.read_bytes())
        self.assertEqual(st, 200, d)
        self.assertEqual(dims(self.tmp / d["full"]), (64, 48, "jpeg"))

    def test_a_small_image_is_never_enlarged(self):
        st, d = self.upload("tiny.png", F.png(40, 30))
        self.assertEqual(dims(self.tmp / d["full"])[:2], (40, 30))

    def test_name_clashes_get_a_suffix_and_bad_uploads_are_refused(self):
        a = self.upload("same.png", F.png(20, 20))[1]
        b = self.upload("same.png", F.png(20, 20))[1]
        self.assertEqual((a["full"], b["full"]), ("assets/img/alpha/same.jpg", "assets/img/alpha/same-2.jpg"))
        self.assertEqual(self.upload("note.txt", b"hello")[0], 400)
        self.assertEqual(self.upload("fake.png", b"not an image")[0], 400)
        self.assertEqual(self.upload("x.png", F.png(), slug="nope")[0], 400)
        self.assertEqual(list((self.tmp / "assets/img/alpha").glob("fake*")), [], "a failed conversion must leave nothing")

    def test_posters_go_where_the_site_keeps_them(self):
        st, d = self.upload("My Reel.png", F.png(2000, 1000), kind="poster", scope="reel")
        self.assertEqual(d["full"], "assets/img/posters/reels/my-reel.jpg")
        self.assertEqual(dims(self.tmp / d["full"])[0], 1280)
        st, d = self.upload("film.png", F.png(2400, 1200), kind="poster")
        self.assertEqual((d["full"], dims(self.tmp / d["full"])[0]), ("assets/img/alpha/film-2.jpg", 1920))

    def test_add_reorder_alt_and_remove(self):
        up = self.upload("new.png", F.png(100, 100))[1]
        st, d = self.post("/api/media", {"slug": "alpha", "action": "add", "items": [dict(up, alt="A new one")]})
        self.assertEqual((st, d["photos"]), (200, 3))
        order = [p["full"] for p in self.data("assets-manifest.json")["projects"]["alpha"]["photos"]]
        self.assertEqual(order[-1], up["full"])
        new = [order[2], order[0], order[1]]
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "reorder", "order": new})[0], 200)
        photos = self.data("assets-manifest.json")["projects"]["alpha"]["photos"]
        self.assertEqual([p["full"] for p in photos], new)
        self.assertEqual(photos[0]["alt"], "A new one", "alt text travels with its photo")
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0, "alt": "Renamed"})[0], 200)
        self.assertEqual(self.data("assets-manifest.json")["projects"]["alpha"]["photos"][0]["alt"], "Renamed")
        st, d = self.post("/api/media", {"slug": "alpha", "action": "remove", "index": 0})
        self.assertEqual((st, d["photos"], d["files_moved"]), (200, 2, 2))
        self.assertFalse((self.tmp / up["full"]).exists())
        self.assertEqual(len(list((self.tmp / "tools" / "removed-media").iterdir())), 2, "moved, not deleted")

    def test_a_reorder_must_name_every_photo_once(self):
        have = [p["full"] for p in self.data("assets-manifest.json")["projects"]["alpha"]["photos"]]
        for bad in (have[:1], have + have[:1], ["x", "y"], None, [1, 2]):
            self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "reorder", "order": bad})[0], 400, bad)

    def test_bad_photo_requests(self):
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "remove", "index": 9})[0], 400)
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "add", "items": [{"full": "assets/nope.jpg"}]})[0], 400)
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "add", "items": [{"full": "../projects.json"}]})[0], 400)
        self.assertEqual(self.post("/api/media", {"slug": "nope", "action": "remove", "index": 0})[0], 404)
        self.assertEqual(self.post("/api/media", {"slug": "alpha", "action": "explode"})[0], 400)


class TestVideos(CmsCase):
    def put(self, rows, slug="alpha"):
        return self.post("/api/project/%s/videos" % slug, {"videos": rows}, method="PUT")

    def videos(self, slug="alpha"):
        return self.data("assets-manifest.json")["projects"][slug]["videos"]

    def test_an_existing_video_keeps_what_the_form_does_not_edit(self):
        old = self.videos()[0]
        st, d = self.put([{"from": 0, "title": "Renamed film", "livid": "https://livid.com/watch/AAAAAA11", "ratio": "16 / 9"}])
        self.assertEqual(st, 200, d)
        v = self.videos()[0]
        self.assertEqual((v["title"], v["ratio"]), ("Renamed film", "16 / 9"))
        self.assertIs(v["ai"], True, "unknown flags survive")
        self.assertEqual(v["poster"], old["poster"])
        self.assertEqual(v["livid"], old["livid"], "a deliberate title=0 embed must not be rewritten")

    def test_a_new_video_is_normalised_and_the_list_can_be_reordered(self):
        st, d = self.put([{"title": "New one", "livid": "https://livid.com/watch/NEWNEW123", "ratio": "9 / 16"},
                          {"from": 0, "title": "Alpha film", "livid": "AAAAAA11", "ratio": "9 / 16"}])
        self.assertEqual(st, 200, d)
        v = self.videos()
        self.assertEqual([x["title"] for x in v], ["New one", "Alpha film"])
        self.assertEqual(v[0]["livid"], "https://livid.com/embed/NEWNEW123?livid_logo=0&fullscreen=1&title=1")
        self.assertEqual((v[0]["poster"], v[0]["mp4"]), ("", ""))

    def test_removing_a_row_moves_its_poster_to_the_trash(self):
        self.assertEqual(self.put([])[0], 200)
        self.assertEqual(self.videos(), [])
        self.assertFalse((self.tmp / "assets/img/alpha/film.jpg").exists())
        self.assertEqual(len(list((self.tmp / "tools" / "removed-media").iterdir())), 1)

    def test_a_replaced_poster_trashes_the_old_one(self):
        spare = "assets/img/posters/reels/spare.jpg"
        self.assertEqual(self.put([{"from": 0, "title": "Alpha film", "livid": "AAAAAA11", "poster": spare}])[0], 200)
        self.assertEqual(self.videos()[0]["poster"], spare)
        self.assertFalse((self.tmp / "assets/img/alpha/film.jpg").exists())

    def test_validation(self):
        self.assertEqual(self.put([{"title": "", "livid": "AAAAAA11"}])[0], 400)
        self.assertEqual(self.put([{"title": "T", "livid": ""}])[0], 400)
        self.assertEqual(self.put([{"title": "T", "livid": "https://example.com/x"}])[0], 400)
        self.assertEqual(self.put([{"title": "T", "livid": "AAAAAA11", "ratio": "3 / 2"}])[0], 400)
        self.assertEqual(self.put([{"title": "T", "livid": "AAAAAA11", "poster": "assets/nope.jpg"}])[0], 400)
        self.assertEqual(self.put([{"title": "T", "livid": "AAAAAA11"}], slug="nope")[0], 404)
        self.assertEqual(len(self.videos()), 1, "a refused save changes nothing")


class TestLists(CmsCase):
    def put(self, name, rows):
        return self.post("/api/lists/%s" % name, {"rows": rows}, method="PUT")

    def reels(self):
        return self.data("media.json")["reels"]

    def test_reels_keep_unknown_keys_and_get_a_unique_slug(self):
        rows = [{"from": 0, "title": "Alpha film (cut 2)", "livid": "BBBBBB22", "ratio": "9 / 16", "project": "alpha"},
                {"title": "Alpha film (cut 2)", "livid": "https://livid.com/watch/CCCCCC33", "ratio": "16 / 9",
                 "format": "Collab reel", "brand": "Beta", "project": "beta", "year": "2026"}]
        st, d = self.put("reels", rows)
        self.assertEqual((st, d["rows"]), (200, 2), d)
        r = self.reels()
        self.assertEqual((r[0]["slug"], r[0]["ai"], r[0]["year"], r[0]["brand"]), ("alpha-film", True, "2024", "Alpha"))
        self.assertEqual((r[1]["slug"], r[1]["project"]), ("alpha-film-cut-2", "beta"))
        self.assertEqual(r[0]["livid"], "https://livid.com/embed/BBBBBB22?livid_logo=0&fullscreen=1&title=1")
        self.assertEqual((r[1]["mp4"], r[1]["poster"]), ("", ""))

    def test_reorder_and_delete(self):
        self.put("reels", [{"from": 0, "title": "Alpha film", "livid": "BBBBBB22", "ratio": "9 / 16"},
                           {"title": "Second", "livid": "DDDDDD44", "ratio": "9 / 16"}])
        self.put("reels", [{"from": 1, "title": "Second", "livid": "DDDDDD44", "ratio": "9 / 16"},
                           {"from": 0, "title": "Alpha film", "livid": "BBBBBB22", "ratio": "9 / 16"}])
        self.assertEqual([r["title"] for r in self.reels()], ["Second", "Alpha film"])
        self.put("reels", [{"from": 1, "title": "Alpha film", "livid": "BBBBBB22", "ratio": "9 / 16"}])
        self.assertEqual([r["title"] for r in self.reels()], ["Alpha film"])

    def test_a_row_that_omits_a_field_keeps_its_stored_value(self):
        self.assertEqual(self.put("reels", [{"from": 0, "title": "Retitled"}])[0], 200)
        r = self.reels()[0]
        self.assertEqual((r["title"], r["livid"].split("/")[-1][:6], r["ratio"]), ("Retitled", "BBBBBB", "9 / 16"))

    def test_validation(self):
        base = {"title": "T", "livid": "EEEEEE55", "ratio": "9 / 16"}
        self.assertEqual(self.put("reels", [dict(base, title="")])[0], 400)
        self.assertEqual(self.put("reels", [{"title": "T", "ratio": "9 / 16"}])[0], 400)          # new reel without a link
        self.assertEqual(self.put("reels", [dict(base, project="nope")])[0], 400)
        self.assertEqual(self.put("reels", [dict(base, ratio="2 / 1")])[0], 400)
        self.assertEqual(self.put("reels", [dict(base, poster="assets/nope.jpg")])[0], 400)
        self.assertEqual(self.put("nope", [])[0], 404)
        self.assertEqual(self.put("reels", "no")[0], 400)
        self.assertEqual(len(self.reels()), 1)

    def test_motion_credits_experience_and_side_projects(self):
        self.assertEqual(self.put("motion", [{"title": "Logo", "client": "Alpha", "role": "Animator", "year": "2025",
                                              "livid": "FFFFFF66", "ratio": "1 / 1"}])[0], 200)
        m = self.data("media.json")["motion"][0]
        self.assertEqual((m["slug"], m["mp4"], m["poster"]), ("logo", "", ""))
        self.assertEqual(self.put("credits", [{"from": 0, "artist": "Artist", "work": "Work", "role": "Director", "year": "2021"}])[0], 200)
        self.assertEqual(self.data("media.json")["credits"][0]["role"], "Director")
        self.assertEqual(self.put("credits", [{"artist": "A", "work": "", "role": "R"}])[0], 400)
        self.assertEqual(self.put("experience", [{"from": 0, "role": "Lead", "org": "Co", "years": "2024", "place": "NYC"}])[0], 200)
        self.assertEqual(self.data("site-config.json")["experience"][0]["role"], "Lead")
        self.assertEqual(self.put("side_projects", [{"label": "Book", "line": "A book.", "href": "ftp://x"}])[0], 400)
        self.assertEqual(self.put("side_projects", [{"from": 0, "label": "Book", "line": "A book.", "href": "https://example.com/book"}])[0], 200)


class TestConfig(CmsCase):
    def save(self, **raw):
        return self.post("/api/config", raw)

    def cfg(self):
        return self.data("site-config.json")

    def test_text_fields_and_the_page_reflects_them(self):
        st, d = self.save(headline="A new headline.", name="Test Person", based="Brooklyn", bio=["One.", "Two.", "Three."])
        self.assertEqual((st, sorted(d["touched"])), (200, ["based", "bio", "headline", "name"]))
        self.assertEqual(self.cfg()["bio"], ["One.", "Two.", "Three."])
        self.assertIn("A new headline.", self.text("index.html"))

    def test_domain_is_normalised_and_validated(self):
        self.save(domain="https://Riveraferran.com/work")
        self.assertEqual(self.cfg()["domain"], "riveraferran.com")
        self.assertEqual(self.save(domain="not a domain")[0], 400)
        self.assertEqual(self.cfg()["domain"], "riveraferran.com")

    def test_hero_images_must_exist(self):
        self.assertEqual(self.save(hero_image="assets/img/nope.jpg")[0], 400)
        self.assertEqual(self.save(hero_image="../projects.json")[0], 400)
        self.assertEqual(self.save(hero_image="assets/img/hero.jpg")[0], 200)

    def test_page_intros_collaborations_and_lines(self):
        self.save(page_intros={"work": "New work intro.", "bogus": "ignored"}, collaborations={"label": "With", "names": "X\nY\n\nZ"},
                  tools=["A", "B"], certifications="C1\nC2")
        c = self.cfg()
        self.assertEqual((c["page_intros"]["work"], "bogus" in c["page_intros"]), ("New work intro.", False))
        self.assertEqual((c["collaborations"]["names"], c["tools"], c["certifications"]), (["X", "Y", "Z"], ["A", "B"], ["C1", "C2"]))

    def test_showreel_switch_and_links(self):
        self.assertNotIn('id="reel"', self.text("index.html"))
        st, d = self.save(showreel={"hidden": False, "vertical": {"livid": "https://livid.com/watch/VERTVERT1"},
                                    "horizontal": {"livid": "HORIHORI22"}})
        self.assertEqual(st, 200)
        sr = self.cfg()["showreel"]
        self.assertNotIn("hidden", sr)
        self.assertEqual(sr["vertical"]["livid"], "https://livid.com/embed/VERTVERT1?livid_logo=0&fullscreen=1&title=1")
        self.assertIn('id="reel"', self.text("index.html"))
        self.assertTrue(self.call("GET", "/api/state")[1]["build"]["ready"])
        self.save(showreel={"hidden": True})
        self.assertIs(self.cfg()["showreel"]["hidden"], True)
        self.assertNotIn('id="reel"', self.text("index.html"))

    def test_the_showreel_switch_on_without_links_is_a_draft_that_says_why(self):
        self.save(showreel={"hidden": False})
        st, d = self.call("GET", "/api/state")
        self.assertFalse(d["build"]["ready"])
        items = self.call("GET", "/api/release")[1]["items"]
        self.assertTrue(any(i["path"].startswith("showreel.") for i in items), items)
        self.assertIn("draft-banner", self.text("index.html"))

    def test_hidden_pages_switch(self):
        self.assertFalse((self.tmp / "paid-social.html").exists())
        self.assertEqual(self.save(hidden_pages=[])[0], 200)
        self.assertNotIn("hidden_pages", self.cfg())
        self.assertTrue((self.tmp / "paid-social.html").exists())
        self.assertFalse(self.call("GET", "/api/state")[1]["build"]["ready"], "an empty Paid Social page blocks a release")
        self.save(hidden_pages=["paid-social"])
        self.assertFalse((self.tmp / "paid-social.html").exists())
        self.assertTrue(self.call("GET", "/api/state")[1]["build"]["ready"])
        self.assertEqual(self.save(hidden_pages=["about"])[0], 400)

    def test_ai_live_cards(self):
        self.assertEqual(self.save(ai_live=["rodowita-water", "nope"])[0], 400)
        self.assertEqual(self.save(ai_live=[])[0], 200)
        self.assertNotIn('id="ran-live"', self.text("ai.html"))
        self.assertEqual(self.save(ai_live=["rodowita-water"])[0], 200)
        self.assertIn('id="ran-live"', self.text("ai.html"))

    def test_nothing_to_save_and_bad_types(self):
        self.assertEqual(self.save()[0], 400)
        self.assertEqual(self.save(name="")[0], 400)
        self.assertEqual(self.save(email="nope")[0], 400)
        self.assertEqual(self.save(headline=5)[0], 400)


class TestReleaseGate(CmsCase):
    def test_a_clean_site_is_built_as_a_release_and_a_placeholder_makes_it_a_draft(self):
        self.assertTrue(self.call("GET", "/api/release")[1]["ready"])
        st, d = self.post("/api/projects", {"slug": "beta", "client": "Beta", "title": "Beta title", "result": "[Placeholder: a result]"})
        self.assertEqual(st, 200)
        self.assertIn("draft-banner", self.text("index.html"))
        st, d = self.call("GET", "/api/release")
        self.assertEqual((d["ready"], d["count"]), (False, 1))
        self.assertEqual((d["items"][0]["file"], d["items"][0]["path"]), ("projects.json", "projects[beta].result"))
        self.post("/api/projects", {"slug": "beta", "client": "Beta", "title": "Beta title", "result": "Sold out."})
        self.assertNotIn("draft-banner", self.text("index.html"))
        self.assertTrue(self.call("GET", "/api/release")[1]["ready"])

    def test_rebuild_returns_the_log(self):
        st, d = self.call("POST", "/api/build", {})
        self.assertEqual((st, d["ok"], d["ready"]), (200, True, True))
        self.assertIn("BUILD_OK (release)", d["log"])
        self.assertRegex(d["last_run"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")


class TestSafety(CmsCase):
    def test_a_failed_build_restores_every_file(self):
        before = {n: (self.tmp / n).read_bytes() for n in self.cms.DATA_NAMES}
        real, calls = self.cms.run_build, []

        def failing():
            calls.append(1)
            if len(calls) == 1:
                return {"ok": False, "ready": False, "items": [], "log": "boom", "blockers": 0, "last_run": "", "version": ""}
            return real()
        self.cms.run_build = failing
        try:
            st, d = self.post("/api/projects/beta/visibility", {"hidden": True})
        finally:
            self.cms.run_build = real
        self.assertEqual(st, 422)
        self.assertIn("did not build", d["error"])
        self.assertEqual({n: (self.tmp / n).read_bytes() for n in self.cms.DATA_NAMES}, before)
        self.assertEqual(len(calls), 2, "the failed build, then the build that puts the pages back")

    def test_every_write_is_backed_up_and_the_stale_guard_covers_every_write_route(self):
        self.post("/api/projects/beta/visibility", {"hidden": True})
        self.assertEqual(len(list((self.tmp / "tools" / "backups").glob("projects-*.json"))), 1)
        before = {n: (self.tmp / n).read_bytes() for n in self.cms.DATA_NAMES}
        stale = "0000"
        calls = [("POST", "/api/projects", {"client": "X", "title": "Y"}),
                 ("POST", "/api/projects/order", {"slugs": self.slugs()}),
                 ("POST", "/api/projects/alpha/visibility", {"hidden": True}),
                 ("POST", "/api/media", {"slug": "alpha", "action": "remove", "index": 0}),
                 ("POST", "/api/config", {"headline": "x"}),
                 ("PUT", "/api/project/alpha/videos", {"videos": []}),
                 ("PUT", "/api/lists/credits", {"rows": []})]
        for method, path, body in calls:
            st, d = self.call(method, path, dict(body, version=stale))
            self.assertEqual((st, d.get("stale")), (409, True), (method, path))
        self.assertEqual(self.call("DELETE", "/api/projects/alpha?version=" + stale)[0], 409)
        self.assertEqual({n: (self.tmp / n).read_bytes() for n in self.cms.DATA_NAMES}, before)

    def test_two_writes_from_the_same_page_cannot_both_land(self):
        v = self.version()
        results = []

        def hit(slug):
            results.append(self.call("POST", "/api/projects/%s/visibility" % slug, {"hidden": True, "version": v})[0])
        threads = [threading.Thread(target=hit, args=(s,)) for s in ("alpha", "beta")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409])
        self.assertEqual(sum(1 for p in self.data("projects.json")["projects"] if p.get("hidden")), 1)

    def test_the_preview_serves_the_built_site_and_nothing_else(self):
        for path, ctype in (("/preview/", "text/html"), ("/preview/work.html", "text/html"), ("/preview/work/alpha.html", "text/html"),
                            ("/preview/assets/img/hero.jpg", "image/jpeg"), ("/preview/assets/docs/Resume.pdf", "application/pdf")):
            st, body = self.call("GET", path)
            self.assertEqual(st, 200, path)
        for path in ("/preview/projects.json", "/preview/site-config.json", "/preview/assets-manifest.json", "/preview/media.json",
                     "/preview/build.py", "/preview/templates/page.html.tmpl", "/preview/tools/backups/", "/preview/../projects.json",
                     "/preview/%2e%2e/projects.json", "/preview/assets/../projects.json", "/preview/.git/config", "/preview/work/"):
            self.assertEqual(self.call("GET", path)[0], 404, path)

    def test_no_partial_writes_are_left_behind(self):
        self.post("/api/projects/beta/visibility", {"hidden": True})
        self.assertEqual(list(self.tmp.glob("*.tmp")), [])


@unittest.skipUnless(F.pdf_bytes(), "needs PyMuPDF")
class TestResume(CmsCase):
    def test_a_new_pdf_replaces_the_old_one_and_the_old_one_is_kept(self):
        new = F.pdf_bytes("A different first Certificate resume")
        old = (self.tmp / "assets/docs/Resume.pdf").read_bytes()
        v = self.version()
        st, d = self.call("PUT", "/api/resume?name=new.pdf&version=" + v, raw=new)
        self.assertEqual(st, 200, d)
        self.assertEqual(d["path"], "assets/docs/Resume.pdf")
        self.assertTrue((self.tmp / d["path"]).read_bytes().startswith(b"%PDF-"))
        self.assertNotEqual((self.tmp / d["path"]).read_bytes(), old)
        self.assertEqual(len(list((self.tmp / "tools" / "backups").glob("resume-*.pdf"))), 1)
        self.assertEqual(list((self.tmp / "assets/docs").glob(".*")), [], "temp files left behind")

    def test_only_pdfs_and_only_from_a_current_page(self):
        self.assertEqual(self.call("PUT", "/api/resume?name=x.pdf&version=" + self.version(), raw=b"not a pdf")[0], 400)
        self.assertEqual(self.call("PUT", "/api/resume?name=x.pdf&version=0000", raw=F.pdf_bytes())[0], 409)


if __name__ == "__main__":
    unittest.main()
