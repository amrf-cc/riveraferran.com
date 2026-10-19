"""Tests for build.py itself: the draft/placeholder system and the release gate.

Release builds run in a temp copy of the repo (media symlinked, data copied),
so the real data files and the real generated pages are never touched.
"""
import shutil
import unittest

from support import (ROOT, NAME_UNACCENTED, EMAIL_OR_DOMAIN, RELEASE_AI_STEPS, RELEASE_STATIC_AD,
                     RELEASE_VIDEO_AD, html_pages, load, make_release_ready, make_temp_repo,
                     run_build)
import build  # noqa: E402  (support puts the repo root on sys.path)


class TestPlaceholderHelpers(unittest.TestCase):
    def test_is_ph(self):
        self.assertTrue(build.is_ph("[2026]"))
        self.assertTrue(build.is_ph("[Résumé PDF: pending]"))
        for v in ("2026", "[open", "close]", "", None, ["[x]"], 3, "[ ] x"):
            self.assertFalse(build.is_ph(v), repr(v))

    def test_scan_release_finds_every_kind(self):
        data = {
            "site-config.json": {"name": "N", "domain": "", "resume_pdf": "",
                                 "showreel": {"vertical": {"livid": "x"}, "horizontal": {}},
                                 "page_intros": {"paid": "[Paid intro]"}},
            "projects.json": {"projects": [
                {"slug": "a", "years": "[2020]", "confirm": ["years"]},
                {"slug": "b", "draft_only": True, "did": ["ok", "[one more]"]},
                {"slug": "c", "confirm": []}]},
            "media.json": {"credits_confirm": True, "reels": [{"slug": "r", "confirm": ["year"]}]},
            "assets-manifest.json": {"projects": {}},
        }
        found = {(f, p) for f, p, v in build.scan_release(data)}
        for want in [("site-config.json", "page_intros.paid"),
                     ("site-config.json", "showreel.horizontal"),
                     ("site-config.json", "resume_pdf"),
                     ("site-config.json", "domain"),
                     ("projects.json", "projects[a].years"),
                     ("projects.json", "projects[a].confirm"),
                     ("projects.json", "projects[b].draft_only"),
                     ("projects.json", "projects[b].did[1]"),
                     ("media.json", "credits_confirm"),
                     ("media.json", "reels[r].confirm")]:
            self.assertIn(want, found)
        self.assertNotIn(("site-config.json", "showreel.vertical"), found)
        self.assertNotIn(("projects.json", "projects[c].confirm"), found)

    def test_masthead_name_split(self):
        site = build.Site({"site-config.json": {"name": "Adrián Rivera Ferrán"}})
        self.assertEqual(site.masthead_name(),
                         '<span class="w">Adrián</span><span class="last"> '
                         '<span class="w">Rivera</span> <span class="w">Ferrán</span></span>')

    def test_slugify(self):
        self.assertEqual(build.slugify("Product reveal"), "product-reveal")
        self.assertEqual(build.slugify("Close-up"), "close-up")
        self.assertEqual(build.slugify("  Brand & reel!! "), "brand-reel")
        self.assertEqual(build.slugify(""), "other")

    def test_generated_placeholders_are_recorded(self):
        """Template-made placeholders (no data behind them) must block a release."""
        data = build.load_data()
        site = build.Site(data, release=True)
        site.build_all()
        where = {w for w, _ in site.generated}
        live_missing = [s for s in build.AI_LIVE
                        if site.project(s) and not site.project(s).get("ai_steps")]
        for slug in live_missing:
            self.assertIn("ai.html#ran-live → %s.ai_steps" % slug, where)
        ps = data["media.json"].get("paid_social", {})
        self.assertEqual("paid-social.html#video-ads" in where, not ps.get("video_ads"))
        self.assertEqual("paid-social.html#static-ads" in where, not ps.get("static_ads"))
        self.assertEqual("about.html#resume" in where, not data["site-config.json"].get("resume_pdf"))
        for _, text in site.generated:
            self.assertTrue(build.is_ph(text), text)

    def test_paid_items_render_in_their_ratio(self):
        data = {"site-config.json": {"name": "N"}, "projects.json": {"projects": []},
                "assets-manifest.json": {"projects": {}},
                "media.json": {"paid_social": {
                    "video_ads": [{"title": "V", "campaign": "C", "ratio": "4 / 5",
                                   "mp4": "assets/video/reels/baklava.mp4",
                                   "poster": "assets/img/posters/reels/baklava.jpg"}],
                    "static_ads": [{"title": "S", "campaign": "C", "ratio": "1 / 1",
                                    "image": "assets/img/posters/reels/birthday.jpg"}]}}}
        site = build.Site(data, release=True)
        site.build_paid()
        page = site.pages["paid-social.html"]
        self.assertEqual(site.generated, [])
        self.assertIn('style="aspect-ratio:4 / 5"><button type="button" class="reel-play"', page)
        self.assertIn('style="aspect-ratio:1 / 1"><img src="assets/img/posters/reels/birthday.jpg?v=', page)
        self.assertNotIn("ph-box", page)

    def test_release_render_hides_draft_layer(self):
        data = build.load_data()
        site = build.Site(data, release=True)
        self.assertEqual(site.confirm_pill(["years"]), "")
        self.assertFalse(any(p.get("draft_only") for p in site.visible))


class TestReleaseGate(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_repo()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def test_release_blocks_on_current_data_without_writing(self):
        offenders = build.scan_release(build.load_data())
        if not offenders:
            self.skipTest("current data is release-clean")
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RELEASE BLOCKED", r.stdout)
        for fname, path, _ in offenders:
            self.assertIn("%s → %s →" % (fname, path), r.stdout)
        site = build.Site(build.load_data(), release=True)
        site.build_all()
        for where, text in site.generated:
            self.assertIn("(generated) → %s → %s" % (where, build.fmt_value(text)), r.stdout)
        self.assertEqual(list(self.tmp.glob("*.html")), [], "release gate wrote pages")
        self.assertFalse((self.tmp / "work").exists(), "release gate wrote case studies")
        self.assertFalse((self.tmp / "CNAME").exists())

    def test_draft_build_in_copy(self):
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().endswith("BUILD_OK"), r.stdout)
        self.assertIn('class="draft-banner"', (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.assertFalse((self.tmp / "CNAME").exists())

    def test_clean_release_build(self):
        make_release_ready(self.tmp)
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().endswith("BUILD_OK (release)"), r.stdout)
        pages = html_pages(self.tmp)
        self.assertTrue(pages)
        for page in pages:
            text = page.read_text(encoding="utf-8")
            for marker in ("draft-banner", "Draft preview", "is-draft", "confirm-pill",
                           "ph-box", "ph-chip", "draft-ribbon"):
                self.assertNotIn(marker, text, "%s in release %s" % (marker, page.name))
            self.assertIsNone(NAME_UNACCENTED.search(EMAIL_OR_DOMAIN.sub("", text)), page.name)
        for p in load("projects.json")["projects"]:
            if p.get("draft_only"):
                self.assertFalse((self.tmp / "work" / (p["slug"] + ".html")).exists(), p["slug"])
        self.assertEqual((self.tmp / "CNAME").read_text().strip(), "example.com")
        self.assertIn('<meta property="og:url" content="https://example.com/">',
                      (self.tmp / "index.html").read_text(encoding="utf-8"))
        self.assertIn('href="assets/resume.pdf"', (self.tmp / "index.html").read_text(encoding="utf-8"))
        about = (self.tmp / "about.html").read_text(encoding="utf-8")
        self.assertIn('<a class="btn" href="assets/resume.pdf" download>', about)
        ai = (self.tmp / "ai.html").read_text(encoding="utf-8")
        self.assertIn(RELEASE_AI_STEPS, ai)
        paid = (self.tmp / "paid-social.html").read_text(encoding="utf-8")
        self.assertIn(RELEASE_VIDEO_AD["mp4"] + "?v=", paid)
        self.assertIn(RELEASE_STATIC_AD["image"] + "?v=", paid)
        reels = (self.tmp / "reels.html").read_text(encoding="utf-8")
        self.assertIn('id="motion"', reels)

    def test_real_data_untouched_by_tests(self):
        # The temp copy shares no data files with the repo.
        for name in ("site-config.json", "projects.json", "media.json"):
            self.assertFalse((self.tmp / name).samefile(ROOT / name))


if __name__ == "__main__":
    unittest.main()
