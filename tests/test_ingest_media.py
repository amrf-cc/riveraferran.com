"""Phase 5 ingest (code side): AI / composite badges and comparison sets.

The badge helpers and the comparison-set renderer are exercised on synthetic
data, so these tests do not depend on the ingest worker's manifest writes.
One end-to-end test writes a flagged photo, a flagged video and a set into a
TEMP COPY of the repo and builds it there.
"""
import json
import re
import shutil
import unittest

from support import (ROOT, is_ph, load, local_links, make_temp_repo, read,
                     run_build, work_projects)
import build  # noqa: E402  (support puts the repo root on sys.path)

AI = "AI-assisted"
PHOTO = {"full": "assets/img/chip-city/01.jpg", "thumb": "assets/img/chip-city/thumbs/01.jpg",
         "alt": "Crookie tiramisu"}
PHOTO2 = {"full": "assets/img/chip-city/02.jpg", "thumb": "assets/img/chip-city/thumbs/02.jpg",
          "alt": "Berry New Weekend cookie"}
PHOTO3 = {"full": "assets/img/chip-city/03.jpg", "thumb": "assets/img/chip-city/thumbs/03.jpg",
          "alt": "Cookie counter"}
PROJECT = {"slug": "p", "client": "P", "title": "T"}


def make_data(manifest=None, projects=None, config=None, media=None):
    cfg = {"name": "N", "ai_label": AI}
    if config:
        cfg.update(config)
    return {
        "site-config.json": cfg,
        "projects.json": {"projects": projects or []},
        "media.json": media or {},
        "assets-manifest.json": {"projects": manifest or {}},
    }


def expected_text(flags, ai_label=AI):
    ai, composite = flags
    out = []
    if ai and ai_label:
        out.append(ai_label)
    if composite:
        out.append("Composite")
    return " · ".join(out)


class TestBadgeLabels(unittest.TestCase):
    def site(self, ai_label=AI):
        return build.Site(make_data(config={"ai_label": ai_label}))

    def test_ai_uses_the_config_label(self):
        self.assertEqual(self.site().badge_labels({"ai": True}), [AI])
        self.assertEqual(self.site("AI-generated").badge_labels({"ai": True}), ["AI-generated"])

    def test_composite_is_always_composite(self):
        self.assertEqual(self.site().badge_labels({"composite": True}), ["Composite"])

    def test_both_flags_keep_both_claims(self):
        self.assertEqual(self.site().badge_labels({"ai": True, "composite": True}),
                         [AI, "Composite"])

    def test_absent_flags_mean_no_badge(self):
        for item in ({}, None, "photo", PHOTO, {"ai": False, "composite": False}):
            self.assertEqual(self.site().badge_labels(item), [], repr(item))
            self.assertEqual(self.site().badge_html(item), "", repr(item))

    def test_ai_without_a_configured_label_draws_nothing(self):
        site = self.site(ai_label="")
        self.assertEqual(site.badge_labels({"ai": True}), [])
        # A composite still discloses itself, label or not.
        self.assertEqual(site.badge_labels({"ai": True, "composite": True}), ["Composite"])

    def test_badge_text_goes_through_escaping(self):
        site = build.Site(make_data(config={"ai_label": "AI & <pixels>"}))
        self.assertIn("AI &amp; &lt;pixels&gt;", site.badge_html({"ai": True}))


class TestPhotoAndVideoBadges(unittest.TestCase):
    def setUp(self):
        self.site = build.Site(make_data())

    def test_flagged_photo_carries_a_tile_badge_and_a_caption_value(self):
        text = self.site.photo_button_html(dict(PHOTO, ai=True), "../")
        self.assertIn('data-badge="%s"' % AI, text)
        self.assertIn('<span class="ai-badge shot-badge">%s</span>' % AI, text)
        self.assertIn('data-full="../assets/img/chip-city/01.jpg?v=', text)

    def test_unflagged_photo_has_an_empty_badge_slot(self):
        text = self.site.photo_button_html(PHOTO, "../")
        self.assertIn('data-badge=""', text)
        self.assertNotIn("ai-badge", text)

    def test_ai_video_badge_sits_beside_the_title(self):
        text = self.site.video_block_html(
            {"title": "Brand reveal", "livid": "https://livid.com/embed/x", "ratio": "9 / 16", "ai": True},
            "../")
        self.assertIn('<p class="vtitle">Brand reveal <span class="ai-badge vtitle-badge">%s</span></p>'
                      % AI, text)

    def test_composite_video_badge(self):
        text = self.site.video_block_html(
            {"title": "Composite cut", "mp4": "assets/video/x.mp4",
             "poster": "assets/img/chip-city/01.jpg", "composite": True}, "")
        self.assertIn('<span class="ai-badge vtitle-badge">Composite</span>', text)
        self.assertNotIn(AI, text.split("<p class=\"vtitle\">")[1])

    def test_unflagged_video_has_no_badge(self):
        text = self.site.video_block_html({"title": "Reel", "livid": "https://livid.com/embed/x"}, "")
        self.assertNotIn("ai-badge", text)

    def test_card_cover_badge_follows_the_cover_media(self):
        site = build.Site(make_data(manifest={"p": {"photos": [dict(PHOTO, ai=True)]}}))
        html = site.cover_html(PROJECT, "../", False)
        self.assertIn('<span class="ai-badge thumb-badge">%s</span>' % AI, html)
        # The badge comes after the image, so the cover <a><img pattern holds.
        self.assertLess(html.index("<img"), html.index("thumb-badge"))

    def test_card_cover_badge_for_a_flagged_video_poster(self):
        site = build.Site(make_data(manifest={"p": {"photos": [], "videos": [
            {"title": "Cut", "poster": "assets/img/chip-city/01.jpg", "composite": True}]}}))
        self.assertIn("thumb-badge\">Composite</span>", site.cover_html(PROJECT, "../", False))

    def test_plain_tile_has_no_badge(self):
        site = build.Site(make_data(manifest={"p": {"photos": [], "videos": []}}))
        self.assertNotIn("ai-badge", site.cover_html(PROJECT, "../", False))

    def test_reel_card_badge_when_the_reel_is_flagged(self):
        site = build.Site(make_data())
        card = site.reel_card_html({"slug": "r", "title": "R", "livid": "https://livid.com/embed/r",
                                    "ratio": "9 / 16", "ai": True}, "")
        self.assertIn('<span class="ai-badge reel-badge">%s</span>' % AI, card)

    def test_card_repeats_the_badge_in_text_for_screen_readers(self):
        # The cover sits inside an aria-hidden link, so the meta carries the
        # disclosure as hidden text.
        site = build.Site(make_data(manifest={"p": {"photos": [dict(PHOTO, ai=True)]}},
                                    projects=[PROJECT]))
        self.assertIn('<span class="sr-only">%s</span>' % AI, site.card_html(PROJECT, ""))
        self.assertIn('<span class="sr-only">%s</span>' % AI, site.feature_html(PROJECT, ""))
        clean = build.Site(make_data(manifest={"p": {"photos": [PHOTO]}}, projects=[PROJECT]))
        self.assertNotIn("sr-only", clean.card_html(PROJECT, ""))
        self.assertNotIn("sr-only", clean.feature_html(PROJECT, ""))


SETS = [
    {"title": "P9 → F3", "steps": [
        {"label": "Real shot", "image": PHOTO2["full"]},
        {"label": "AI step", "image": PHOTO3["full"], "ai": True},
        {"label": "Final", "image": PHOTO["full"]}]},
    {"title": "S2 → S14 → S1", "steps": [
        {"label": "Real shot", "image": PHOTO["full"]},
        {"label": "Composite", "image": PHOTO2["full"], "composite": True},
        {"label": "Final", "image": PHOTO3["full"]}]},
]


class TestComparisonSets(unittest.TestCase):
    def setUp(self):
        self.site = build.Site(make_data(manifest={"p": {"photos": [PHOTO], "sets": SETS}}))

    def test_sets_render_in_order_with_one_li_per_step(self):
        html = self.site.compare_list_html("p", "../")
        self.assertLess(html.index("P9 → F3"), html.index("S2 → S14 → S1"))
        self.assertEqual(html.count('<li class="compare-step">'), 6)
        labels = re.findall(r'<p class="compare-label">([^<]*)', html)
        self.assertEqual(labels, ["Real shot", "AI step", "Final",
                                  "Real shot", "Composite", "Final"])

    def test_only_ai_or_composite_steps_carry_the_badge(self):
        html = self.site.compare_list_html("p", "../")
        self.assertEqual(re.findall(r'class="ai-badge step-badge">([^<]+)<', html),
                         [AI, "Composite"])

    def test_step_images_are_plain_and_versioned(self):
        html = self.site.compare_list_html("p", "../")
        self.assertIn('<img src="../assets/img/chip-city/02.jpg?v=', html)
        self.assertIn('loading="lazy" draggable="false"', html)
        self.assertNotIn('<button class="shot" data-full="../assets/img/chip-city/02.jpg', html)

    def test_section_wrapper_has_the_heading_and_an_anchor(self):
        html = self.site.sets_section_html("p", "../")
        self.assertTrue(html.startswith('<section class="compare-sets" id="compare">'), html[:80])
        self.assertIn('<p class="kicker">%s</p>' % build.COMPARE_HEADING, html)
        self.assertIn('class="wrap"', html)

    def test_project_without_sets_renders_nothing(self):
        site = build.Site(make_data(manifest={"q": {"photos": [PHOTO]}}))
        self.assertEqual(site.compare_list_html("q", "../"), "")
        self.assertEqual(site.sets_section_html("q", "../"), "")
        self.assertEqual(site.compare_list_html("missing", "../"), "")

    def test_placeholders_in_a_set_render_as_chips(self):
        site = build.Site(make_data(manifest={"p": {"sets": [
            {"title": "[Set title]", "steps": [{"label": "[Final]", "image": PHOTO["full"]}]}]}}))
        html = site.compare_list_html("p", "")
        self.assertIn('<figcaption class="compare-title">', html)
        self.assertIn('<span class="ph-chip" title="To add">Set title</span>', html)
        self.assertIn('<span class="ph-chip" title="To add">Final</span>', html)
        self.assertNotIn("[Set title]", html)

    def test_missing_step_image_is_a_generated_placeholder(self):
        site = build.Site(make_data(manifest={"p": {"sets": [
            {"title": "T", "steps": [{"label": "Real shot", "image": PHOTO["full"]},
                                     {"label": "AI step"}]}]}}))
        html = site.compare_list_html("p", "")
        self.assertIn('class="ph-box"', html)
        where = {w for w, _ in site.generated}
        self.assertEqual(where, {"assets-manifest.json → projects.p.sets[0].steps[1].image"})
        for _, text in site.generated:
            self.assertTrue(is_ph(text), text)

    def test_release_scan_flags_placeholder_set_copy(self):
        data = make_data(manifest={"p": {"sets": [
            {"title": "[Set title]", "steps": [{"label": "[Real shot]", "image": PHOTO["full"]}]}]}})
        found = {(f, p) for f, p, _ in build.scan_release(data)}
        self.assertIn(("assets-manifest.json", "projects.p.sets[0].title"), found)
        self.assertIn(("assets-manifest.json", "projects.p.sets[0].steps[0].label"), found)

    def test_junk_sets_do_not_crash_the_builder(self):
        site = build.Site(make_data(manifest={"p": {"sets": [
            "nonsense", {}, {"title": "Empty", "steps": []}, {"title": "Bad steps", "steps": ["x", None]}]}}))
        self.assertEqual(site.compare_list_html("p", ""), "")
        self.assertEqual(site.generated, [])


class TestAiPageSets(unittest.TestCase):
    def page(self, both):
        manifest = {"rodowita-water": {"sets": SETS},
                    "chip-city": {"sets": SETS if both else []}}
        projects = [{"slug": build.AI_LIVE[0], "client": "Rodowita", "title": "R"},
                    {"slug": build.AI_LIVE[1], "client": "Chip City", "title": "C"}]
        site = build.Site(make_data(manifest=manifest, projects=projects))
        site.build_ai()
        return site.pages["ai.html"]

    def test_one_project_with_sets_needs_no_client_kicker(self):
        page = self.page(both=False)
        self.assertEqual(page.count('class="compare-list"'), 1)
        self.assertIn(build.COMPARE_HEADING, page)
        block = page[page.index('<div class="live-sets">'):]
        # Only the shared heading kicker — no per-client label for one project.
        self.assertEqual(block.count('<p class="kicker">'), 1)

    def test_two_projects_with_sets_name_each_one(self):
        page = self.page(both=True)
        self.assertEqual(page.count('class="compare-list"'), 2)
        live = re.search(r'<section class="ai-live" id="ran-live">(.*?)</section>', page, re.S).group(1)
        self.assertLess(live.index("Rodowita"), live.index("Chip City"))
        self.assertEqual(live.count('<div class="live-set">'), 2)


class TestLightboxDisclosure(unittest.TestCase):
    def test_template_has_a_hidden_badge_slot(self):
        tmpl = read(ROOT / "templates" / "lightbox.html.tmpl")
        self.assertIn('<span class="lb-badge ai-badge" hidden></span>', tmpl)

    def test_js_copies_the_badge_into_the_caption(self):
        js = read(ROOT / "assets" / "js" / "main.js")
        self.assertIn("data-badge", js)
        self.assertIn("badgeEl.hidden", js)

    def test_css_hides_the_empty_badge_and_places_the_tile_badge(self):
        css = read(ROOT / "assets" / "css" / "styles.css")
        for marker in (".lb-badge[hidden]", ".shot-badge", ".thumb-badge",
                       ".compare-steps", ".compare-step + .compare-step::before", ".sr-only"):
            self.assertIn(marker, css)


class BuiltSite(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "index.html").exists():
            self.skipTest("site not built yet — run python3 build.py")


class TestBuiltBadgesMatchTheManifest(BuiltSite):
    """Data-independent: whatever Worker A flags, the pages must badge exactly
    those items (and nothing else)."""

    def setUp(self):
        super().setUp()
        self.manifest = load("assets-manifest.json")["projects"]
        self.ai_label = load("site-config.json")["ai_label"]

    def test_case_photo_badges_match_flagged_photos(self):
        for p in work_projects():
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            photos = self.manifest[p["slug"]]["photos"]
            want = [expected_text(build.media_flags(ph), self.ai_label)
                    for ph in photos if expected_text(build.media_flags(ph), self.ai_label)]
            self.assertEqual(re.findall(r'<span class="ai-badge shot-badge">([^<]+)</span>', text),
                             want, p["slug"])
            attrs = [a for a in re.findall(r'<button class="shot"[^>]*data-badge="([^"]*)"', text) if a]
            self.assertEqual(attrs, want, p["slug"])

    def test_case_video_badges_match_flagged_videos(self):
        for p in work_projects():
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            want = [expected_text(build.media_flags(v), self.ai_label)
                    for v in self.manifest[p["slug"]]["videos"]
                    if (v.get("livid") or v.get("mp4")) and build.media_flags(v) != (False, False)]
            self.assertEqual(re.findall(r'class="ai-badge vtitle-badge">([^<]+)<', text),
                             want, p["slug"])

    def test_work_cards_badge_their_flagged_covers(self):
        text = read(ROOT / "work.html")
        site = build.Site(build.load_data())
        want = 0
        for p in work_projects():
            item = site.cover(p, False)[2]
            if item is not None and build.media_flags(item) != (False, False):
                want += 1
        self.assertEqual(len(re.findall(r'class="ai-badge thumb-badge"', text)), want)

    def test_badges_only_use_the_configured_words(self):
        allowed = {self.ai_label, build.COMPOSITE_LABEL, "%s · %s" % (self.ai_label, build.COMPOSITE_LABEL)}
        for page in list(ROOT.glob("*.html")) + sorted((ROOT / "work").glob("*.html")):
            for badge in re.findall(r'class="[^"]*\bai-badge\b[^"]*"[^>]*>([^<]*)<', read(page)):
                if badge:
                    self.assertIn(badge, allowed, page.name)

    def test_ai_page_shows_sets_inside_ran_live_only_when_they_exist(self):
        sets = self.manifest["rodowita-water"].get("sets") or []
        text = read(ROOT / "ai.html")
        if not sets:
            self.assertNotIn("compare-list", text)
            return
        live = re.search(r'<section class="ai-live" id="ran-live">(.*?)</section>', text, re.S).group(1)
        self.assertIn("compare-list", live)
        self.assertIn(build.COMPARE_HEADING, live)
        # No nested <section>: the Ran live block has to stay one section.
        self.assertNotIn("<section", live)


class TestFixtureBuild(unittest.TestCase):
    """A temp copy with flagged media and a set written into the manifest."""

    def setUp(self):
        self.tmp = make_temp_repo()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def write_manifest(self):
        manifest = load("assets-manifest.json", self.tmp)
        chip = manifest["projects"]["chip-city"]
        chip["photos"][0]["ai"] = True
        chip["photos"][1]["composite"] = True
        chip["videos"][0]["ai"] = True
        chip["sets"] = [dict(SETS[0])]
        manifest["projects"]["rodowita-water"]["sets"] = [dict(s) for s in SETS]
        (self.tmp / "assets-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    def test_release_still_blocks_and_writes_nothing(self):
        self.write_manifest()
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RELEASE BLOCKED", r.stdout)
        self.assertEqual(list(self.tmp.glob("*.html")), [])
        self.assertFalse((self.tmp / "work").exists())

    def test_badges_and_sets_render(self):
        self.write_manifest()
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().endswith("BUILD_OK"), r.stdout)
        cfg = load("site-config.json", self.tmp)
        case = read(self.tmp / "work" / "chip-city.html")
        # Photos: AI then composite, in order, with the lightbox caption value.
        self.assertEqual(re.findall(r'<span class="ai-badge shot-badge">([^<]+)</span>', case),
                         [cfg["ai_label"], "Composite"])
        self.assertIn('data-badge="%s"' % cfg["ai_label"], case)
        # Video title badge.
        self.assertIn('<span class="ai-badge vtitle-badge">%s</span>' % cfg["ai_label"], case)
        # The set renders between the copy and the photo grid.
        self.assertIn('<section class="compare-sets" id="compare">', case)
        self.assertIn("<p class=\"kicker\">%s</p>" % build.COMPARE_HEADING, case)
        self.assertIn('P9 → F3', case)
        self.assertEqual(case.count('<li class="compare-step">'), 3)
        self.assertIn('<span class="ai-badge step-badge">%s</span>' % cfg["ai_label"], case)
        self.assertGreater(case.index('id="compare"'), case.index('class="case-body"'))
        self.assertLess(case.index('id="compare"'), case.index('class="case-photos"'))
        for label in ("Real shot", "AI step", "Final"):
            self.assertIn(label, case)
        # Every URL on the page — set images included — resolves.
        links = local_links(self.tmp / "work" / "chip-city.html")
        self.assertTrue(any("chip-city/02.jpg" in link for link, _ in links))
        for link, target in links:
            self.assertTrue(target.exists(), "%s -> %s" % (link, target))
        # The AI page shows the Rodowita sets under Ran live.
        ai = read(self.tmp / "ai.html")
        live = re.search(r'<section class="ai-live" id="ran-live">(.*?)</section>', ai, re.S).group(1)
        self.assertIn("compare-list", live)
        self.assertIn('S2 → S14 → S1', live)
        self.assertIn('<span class="ai-badge step-badge">Composite</span>', live)
        # Lightbox only exists where there are photos, and carries the badge slot.
        self.assertIn("lb-badge", case)
        # The Work card for the flagged cover shows the badge and says it in text.
        work = read(self.tmp / "work.html")
        card = re.search(r'<article class="card"[^>]*>\s*<a class="thumb[^"]*" href="work/chip-city\.html".*?</article>',
                         work, re.S).group(0)
        self.assertIn('<span class="ai-badge thumb-badge">%s</span>' % cfg["ai_label"], card)
        self.assertIn('<span class="sr-only">%s</span>' % cfg["ai_label"], card)

    def test_a_project_without_flags_or_sets_stays_clean(self):
        self.write_manifest()
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        manifest = load("assets-manifest.json", self.tmp)["projects"]
        for p in work_projects():
            if manifest[p["slug"]].get("sets") or any(
                    build.media_flags(x) != (False, False)
                    for x in manifest[p["slug"]]["photos"] + manifest[p["slug"]]["videos"]):
                continue
            text = read(self.tmp / "work" / (p["slug"] + ".html"))
            self.assertNotIn("compare-sets", text, p["slug"])
            self.assertNotIn("shot-badge", text, p["slug"])


if __name__ == "__main__":
    unittest.main()
