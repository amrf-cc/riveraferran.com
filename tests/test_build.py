"""Tests for build.py itself: the draft/placeholder system and the release gate.

Release builds run in a temp copy of the repo (media symlinked, data copied),
so the real data files and the real generated pages are never touched.
"""
import shutil
import unittest

import json
import re

from support import (ROOT, NAME_UNACCENTED, EMAIL_OR_DOMAIN, RELEASE_AI_STEPS, RELEASE_STATIC_AD,
                     RELEASE_VIDEO_AD, expected_work_groups, html_pages, load, make_release_ready,
                     make_temp_repo, run_build, standalone, work_sequence)
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
        live_missing = [s for s in build.ai_live_slugs(data["media.json"])
                        if site.project(s) and not site.project(s).get("ai_steps")
                        and not (site.manifest.get(s) or {}).get("sets")]
        for slug in live_missing:
            self.assertIn("ai.html#ran-live → %s.ai_steps" % slug, where)
        ps = data["media.json"].get("paid_social", {})
        paid_built = "paid-social" not in site.hidden_pages
        self.assertEqual("paid-social.html#video-ads" in where, paid_built and not ps.get("video_ads"))
        self.assertEqual("paid-social.html#static-ads" in where, paid_built and not ps.get("static_ads"))
        self.assertEqual("about.html#resume" in where, not data["site-config.json"].get("resume_pdf"))
        for _, text in site.generated:
            self.assertTrue(build.is_ph(text), text)

    def test_paid_items_render_in_their_ratio(self):
        data = {"site-config.json": {"name": "N"}, "projects.json": {"projects": []},
                "assets-manifest.json": {"projects": {}},
                "media.json": {"paid_social": {
                    "video_ads": [{"title": "V", "campaign": "C", "ratio": "4 / 5",
                                   "mp4": "assets/video/motion/pride.mp4",
                                   "poster": "assets/img/posters/motion/pride.jpg"}],
                    "static_ads": [{"title": "S", "campaign": "C", "ratio": "1 / 1",
                                    "image": "assets/img/posters/motion/world-water-day.jpg"}]}}}
        site = build.Site(data, release=True)
        site.build_paid()
        page = site.pages["paid-social.html"]
        self.assertEqual(site.generated, [])
        self.assertIn('style="aspect-ratio:4 / 5"><button type="button" class="reel-play"', page)
        self.assertIn('style="aspect-ratio:1 / 1"><img src="assets/img/posters/motion/world-water-day.jpg?v=', page)
        self.assertNotIn("ph-box", page)

    def test_project_href_and_work_list(self):
        data = {"site-config.json": {"name": "N", "categories": [{"key": "k", "label": "K"}]},
                "projects.json": {"projects": [
                    {"slug": "a", "category": "k"},
                    {"slug": "b", "category": "", "standalone": "ai.html#assistant"},
                    {"slug": "c", "category": ""},
                    {"slug": "d", "category": "k", "standalone": "[later]"}]},
                "media.json": {}, "assets-manifest.json": {"projects": {}}}
        site = build.Site(data)
        by = {p["slug"]: p for p in site.visible}
        self.assertEqual(site.project_href(by["a"], "../"), "../work/a.html")
        self.assertEqual(site.project_href(by["b"], ""), "ai.html#assistant")
        self.assertEqual(site.project_href(by["b"], "../"), "../ai.html#assistant")
        # A placeholder target is not a target: the project stays Work.
        self.assertEqual(site.project_href(by["d"], ""), "work/d.html")
        self.assertEqual([p["slug"] for p in site.work], ["a", "c", "d"])
        groups = [(c["key"] if c else None, [p["slug"] for p in m]) for c, m in site.work_groups()]
        self.assertEqual(groups, [("k", ["a", "d"]), (None, ["c"])])
        pages = site.build_all()
        self.assertNotIn("work/b.html", pages)
        self.assertEqual(sorted(k for k in pages if k.startswith("work/")),
                         ["work/a.html", "work/c.html", "work/d.html"])
        for k in ("work/a.html", "work/c.html", "work/d.html"):
            self.assertNotIn('href="b.html"', pages[k], k)
        # Only standalone b was ungrouped besides c; drop c and "Other work" goes.
        data["projects.json"]["projects"] = [q for q in data["projects.json"]["projects"] if q["slug"] != "c"]
        site = build.Site(data)
        site.build_work()
        self.assertNotIn(build.OTHER_WORK_LABEL, site.pages["work.html"])

    def test_ai_feature_without_standalone_keeps_case_link(self):
        data = build.load_data()
        for p in data["projects.json"]["projects"]:
            if p["slug"] == build.AI_FEATURE:
                p.pop("standalone", None)
                p.pop("hidden", None)          # the subject of this test must be visible, whatever Adrián hid
        site = build.Site(data)
        pages = site.build_all()
        self.assertIn("work/%s.html" % build.AI_FEATURE, pages)
        ai = pages["ai.html"]
        feature = ai[ai.index('<section class="ai-feature"'):]
        feature = feature[:feature.index("</section>")]
        self.assertTrue(feature.startswith('<section class="ai-feature">'), feature[:60])
        self.assertIn('href="work/%s.html">View project</a>' % build.AI_FEATURE, feature)
        self.assertNotIn("ai-result", feature)

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
        cfg = load("site-config.json", self.tmp)
        cfg["domain"] = ""          # an empty domain must never write a CNAME
        (self.tmp / "site-config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
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
        self.assertIn(RELEASE_VIDEO_AD["livid"], paid)
        self.assertIn(RELEASE_STATIC_AD["image"] + "?v=", paid)
        reels = (self.tmp / "reels.html").read_text(encoding="utf-8")
        self.assertIn('id="motion"', reels)
        # Work pills: draft-only projects do not count in a release.
        work = (self.tmp / "work.html").read_text(encoding="utf-8")
        released = [p for p in load("projects.json", self.tmp)["projects"] if not p.get("hidden")]
        cats = load("site-config.json", self.tmp)["categories"]
        want = [k for k, _ in expected_work_groups(released, cats) if k]
        self.assertEqual(re.findall(r'data-filter="([^"]+)"', work), ["all"] + want)

    def test_work_groups_generic_over_categories(self):
        """Fixture: move projects into other groups (incl. the personal one and
        an unknown key) in the temp copy only, then check the draft build."""
        projects = load("projects.json", self.tmp)
        cats = load("site-config.json", self.tmp)["categories"]
        personal = [c for c in cats if c.get("personal")]
        self.assertTrue(personal, "fixture needs a personal category in site-config")
        plain_cat = [c for c in cats if not c.get("personal")][-1]
        movable = [p for p in projects["projects"] if not p.get("hidden") and not p.get("draft_only")
                   and p.get("category")]
        self.assertGreaterEqual(len(movable), 3)
        moved = {movable[0]["slug"]: personal[0]["key"], movable[1]["slug"]: plain_cat["key"],
                 movable[2]["slug"]: "no-such-group"}
        for p in projects["projects"]:
            if p["slug"] in moved:
                p["category"] = moved[p["slug"]]
        (self.tmp / "projects.json").write_text(json.dumps(projects, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        work = (self.tmp / "work.html").read_text(encoding="utf-8")
        visible = [p for p in projects["projects"] if not p.get("hidden")]
        groups = expected_work_groups(visible, cats)
        pills = re.findall(r'data-filter="([^"]+)"', work)
        self.assertEqual(pills, ["all"] + [k for k, _ in groups if k])
        self.assertIn(personal[0]["key"], pills)
        self.assertIn(plain_cat["key"], pills)
        self.assertNotIn("no-such-group", work)
        # Order: config order of groups, the unknown key falls to "Other work".
        seq = work_sequence(work)
        got, cur = [], None
        for item in seq:
            if item[0] == "heading":
                cur = (item[1], [])
                got.append(cur)
            else:
                self.assertEqual(item[2], cur[0], item[1])
                cur[1].append(item[1])
        self.assertEqual(got, groups)
        self.assertIn(movable[2]["slug"], dict(got)[""])
        note = '<span class="personal-note">Personal work</span>'
        personal_keys = {c["key"] for c in load("site-config.json", self.tmp)["categories"] if c.get("personal")}
        personal_slugs = {p["slug"] for p in visible if p.get("category") in personal_keys}
        for item in seq:
            if item[0] == "heading":
                self.assertEqual(note in item[2], item[1] in personal_keys, item[1])
            else:
                self.assertEqual(note in item[3], item[1] in personal_slugs, item[1])
        case = (self.tmp / "work" / (movable[0]["slug"] + ".html")).read_text(encoding="utf-8")
        self.assertIn('<a class="cat-link" href="../work.html#%s">' % personal[0]["key"], case)
        self.assertIn(note, case)
        other = (self.tmp / "work" / (movable[2]["slug"] + ".html")).read_text(encoding="utf-8")
        self.assertNotIn("case-cat", other)
        self.assertNotIn(note, (self.tmp / "work" / (movable[1]["slug"] + ".html")).read_text(encoding="utf-8"))

    def test_second_standalone_project(self):
        """Fixture: make a featured, AI "Ran live" project standalone (target
        reels.html#motion) in the temp copy only, then check the draft build."""
        projects = load("projects.json", self.tmp)
        slug = build.AI_LIVE[0]
        target = "reels.html#motion"
        p = [q for q in projects["projects"] if q["slug"] == slug][0]
        self.assertFalse(p.get("hidden") or p.get("draft_only") or p.get("standalone"))
        self.assertTrue(p.get("featured"), "fixture needs a featured project")
        p["standalone"] = target
        (self.tmp / "projects.json").write_text(json.dumps(projects, ensure_ascii=False, indent=2),
                                                encoding="utf-8")
        (self.tmp / "work").mkdir()
        (self.tmp / "work" / (slug + ".html")).write_text("stale", encoding="utf-8")
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        standalones = [q for q in projects["projects"] if standalone(q) and not q.get("hidden")]
        self.assertGreaterEqual(len(standalones), 2)
        work_slugs = [q["slug"] for q in projects["projects"] if not q.get("hidden") and not standalone(q)]
        self.assertIn("(%d case studies)" % len(work_slugs), r.stdout)
        self.assertEqual(sorted(f.stem for f in (self.tmp / "work").glob("*.html")), sorted(work_slugs))
        for q in standalones:
            self.assertFalse((self.tmp / "work" / (q["slug"] + ".html")).exists(), q["slug"])
        for page in html_pages(self.tmp):
            text = page.read_text(encoding="utf-8")
            for q in standalones:
                self.assertNotIn("work/%s.html" % q["slug"], text, page.name)
                self.assertNotIn('href="%s.html"' % q["slug"], text, page.name)
        # Work: no card, groups exclude it.
        work = (self.tmp / "work.html").read_text(encoding="utf-8")
        cats = load("site-config.json", self.tmp)["categories"]
        visible = [q for q in projects["projects"] if not q.get("hidden")]
        groups = expected_work_groups(visible, cats)
        got = [(i[1], []) for i in work_sequence(work) if i[0] == "heading"]
        cur = None
        for item in work_sequence(work):
            if item[0] == "heading":
                cur = [g for g in got if g[0] == item[1]][0]
            else:
                cur[1].append(item[1])
        self.assertEqual(got, groups)
        self.assertNotIn(slug, [s for _, slugs in got for s in slugs])
        # Home card and AI "Ran live" card link to the standalone target.
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        title = build.esc(p["title"])
        card = re.search(r'<article class="(?:card|feature)[^"]*"[^>]*>(?:(?!</article>).)*?%s.*?</article>'
                         % re.escape(title), index, re.S).group(0)
        self.assertEqual(set(re.findall(r'href="([^"]+)"', card)), {target})
        ai = (self.tmp / "ai.html").read_text(encoding="utf-8")
        live = re.search(r'<article class="card live-card"[^>]*>\s*<a class="thumb[^"]*" href="([^"]+)"', ai)
        self.assertEqual(live.group(1), target)
        self.assertIn('id="motion"', (self.tmp / "reels.html").read_text(encoding="utf-8"))
        # The AI feature keeps its own anchor and content.
        self.assertIn('<section class="ai-feature" id="assistant">', ai)

    def test_real_data_untouched_by_tests(self):
        # The temp copy shares no data files with the repo.
        for name in ("site-config.json", "projects.json", "media.json"):
            self.assertFalse((self.tmp / name).samefile(ROOT / name))


class TestHiddenProjects(unittest.TestCase):
    """What `hidden: true` does to the generated site, on a temp copy of the real data. The copy shows the
    projects the fixtures name (support.show_fixture_subjects), so these tests do not depend on what Adrián
    has hidden in the site manager."""

    def setUp(self):
        self.tmp = make_temp_repo()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def projects(self):
        return load("projects.json", self.tmp)["projects"]

    def set_hidden(self, slugs, hidden=True):
        data = load("projects.json", self.tmp)
        for p in data["projects"]:
            if p["slug"] in slugs:
                if hidden:
                    p["hidden"] = True
                else:
                    p.pop("hidden", None)
        (self.tmp / "projects.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def build(self):
        r = run_build(self.tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def plain_work_project(self):
        subjects = {"chip-city", build.AI_FEATURE} | set(build.AI_LIVE)
        return next(p for p in self.projects() if not p.get("hidden") and not standalone(p)
                    and p["slug"] not in subjects and not p.get("featured"))

    def test_a_hidden_project_has_no_page_card_or_link_and_showing_it_brings_them_back(self):
        slug = self.plain_work_project()["slug"]
        self.set_hidden([slug])
        self.build()
        self.assertFalse((self.tmp / "work" / (slug + ".html")).exists())
        for page in html_pages(self.tmp):
            self.assertNotIn("work/%s.html" % slug, page.read_text(encoding="utf-8"), page.name)
        for p in self.projects():
            if not p.get("hidden") and not standalone(p):
                self.assertTrue((self.tmp / "work" / (p["slug"] + ".html")).exists(), p["slug"])
        self.set_hidden([slug], hidden=False)
        self.build()
        self.assertTrue((self.tmp / "work" / (slug + ".html")).exists())
        self.assertIn("work/%s.html" % slug, (self.tmp / "work.html").read_text(encoding="utf-8"))

    def test_hiding_the_lead_hands_the_home_page_to_the_next_featured_project(self):
        featured = sorted((p for p in self.projects() if p.get("featured") and not p.get("hidden")),
                          key=lambda p: p["featured"])
        self.assertGreaterEqual(len(featured), 2, "fixture needs two featured projects")
        lead, nxt = featured[0], featured[1]
        self.set_hidden([lead["slug"]])
        self.build()
        index = (self.tmp / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('href="%s"' % (standalone(lead) or "work/%s.html" % lead["slug"]), index)
        self.assertIn('href="%s"' % (standalone(nxt) or "work/%s.html" % nxt["slug"]), index)

    def test_a_group_with_nothing_visible_loses_its_filter_pill(self):
        cats = load("site-config.json", self.tmp)["categories"]
        groups = [(c["key"], [p for p in self.projects() if not standalone(p) and p.get("category") == c["key"]
                              and not p.get("hidden")]) for c in cats]
        key, members = min((g for g in groups if g[1]), key=lambda g: len(g[1]))
        self.build()
        self.assertIn('data-filter="%s"' % key, (self.tmp / "work.html").read_text(encoding="utf-8"))
        self.set_hidden([p["slug"] for p in members])
        self.build()
        work = (self.tmp / "work.html").read_text(encoding="utf-8")
        self.assertNotIn('data-filter="%s"' % key, work)

    def test_a_hidden_standalone_project_takes_its_block_off_its_page(self):
        self.build()
        self.assertIn('id="assistant"', (self.tmp / "ai.html").read_text(encoding="utf-8"))
        self.set_hidden([build.AI_FEATURE])
        self.build()
        self.assertNotIn('id="assistant"', (self.tmp / "ai.html").read_text(encoding="utf-8"))

    def test_a_hidden_ran_live_project_drops_its_card(self):
        slug = build.AI_LIVE[0]
        self.build()
        self.assertIn('href="work/%s.html"' % slug, (self.tmp / "ai.html").read_text(encoding="utf-8"))
        self.set_hidden([slug])
        self.build()
        self.assertNotIn('href="work/%s.html"' % slug, (self.tmp / "ai.html").read_text(encoding="utf-8"))


class TestSwitches(unittest.TestCase):
    """The "hidden until ready" switches (showreel.hidden, hidden_pages, ai_live) and --check.
    Each runs a real build in a temp copy; the repo's own data and pages are never touched."""

    def setUp(self):
        self.tmp = make_temp_repo()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def edit(self, name, fn):
        data = load(name, self.tmp)
        fn(data)
        (self.tmp / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def ready(self):
        """Release-clean data in the state the site ships in: showreel and Paid Social
        page switched off, only Rodowita on the AI page. Self-contained, so these tests
        do not depend on what the real data has switched on today."""
        make_release_ready(self.tmp, keep_hidden=True)
        self.edit("site-config.json", lambda d: (d.update(hidden_pages=["paid-social"]),
                                                  d["showreel"].update(hidden=True, placeholder="")))
        self.edit("media.json", lambda d: d.update(ai_live=["rodowita-water"]))

    def page(self, name):
        return (self.tmp / name).read_text(encoding="utf-8")

    def test_check_writes_nothing_and_reports_the_gate(self):
        self.ready()
        self.edit("site-config.json", lambda d: d.update(subline="[Subline to confirm]"))
        r = run_build(self.tmp, "--check")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RELEASE BLOCKED", r.stdout)
        self.edit("site-config.json", lambda d: d.update(subline="A real subline."))
        r = run_build(self.tmp, "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("RELEASE READY", r.stdout)
        self.assertEqual(list(self.tmp.glob("*.html")), [], "--check wrote pages")
        self.assertFalse((self.tmp / "CNAME").exists(), "--check wrote a CNAME")

    def test_hidden_showreel_is_not_demanded_and_leaves_no_trace(self):
        self.ready()
        self.edit("site-config.json", lambda d: d["showreel"].update(hidden=True, placeholder=""))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        home = self.page("index.html")
        for gone in ("Watch reel", 'id="reel"', "reel-slot", ">Showreel<", "ph-box"):
            self.assertNotIn(gone, home, gone)
        self.assertIn('<a class="btn" href="work.html">See work</a>', home)
        self.assertNotIn("btn-ghost", home.split('<p class="actions">')[1].split("</p>")[0])

    def test_showreel_not_hidden_is_still_demanded(self):
        self.ready()
        self.edit("site-config.json", lambda d: d["showreel"].pop("hidden", None))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("showreel.vertical", r.stdout)
        self.assertEqual(list(self.tmp.glob("*.html")), [])

    def test_showreel_visible_markup_is_unchanged(self):
        make_release_ready(self.tmp)               # showreel filled, nothing hidden
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        home = self.page("index.html")
        self.assertIn('<a class="btn" href="#reel">Watch reel</a><a class="btn btn-ghost" href="work.html">See work</a>', home)
        self.assertIn('<section class="reel" id="reel">', home)
        self.assertIn('<span class="kicker">Showreel</span>', home)

    def test_hidden_page_is_not_built_linked_or_left_behind(self):
        self.ready()
        self.edit("site-config.json", lambda d: d.update(hidden_pages=["paid-social"]))
        (self.tmp / "paid-social.html").write_text("stale page from an earlier build", encoding="utf-8")
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.tmp / "paid-social.html").exists(), "stale hidden page survived the build")
        for page in html_pages(self.tmp):
            text = page.read_text(encoding="utf-8")
            self.assertNotIn("paid-social", text, page.name)
            self.assertNotIn(">Paid Social<", text, page.name)
        home = self.page("index.html")
        cap = re.search(r'<div class="cap cap-plain"><span class="cap-label">Paid social</span>', home)
        self.assertTrue(cap, "the Paid social capability should stay, as plain text")

    def test_unknown_hidden_page_names_are_ignored(self):
        self.ready()
        self.edit("site-config.json", lambda d: d.update(hidden_pages=["paid-social", "index", "about", "../x"]))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        for name in ("index.html", "about.html"):
            self.assertTrue((self.tmp / name).exists(), name)

    def test_ai_live_list_chooses_the_cards(self):
        make_release_ready(self.tmp)
        self.edit("media.json", lambda d: d.update(ai_live=["rodowita-water"]))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        live = re.search(r'<section class="ai-live" id="ran-live">(.*?)</section>', self.page("ai.html"), re.S).group(1)
        self.assertIn('href="work/rodowita-water.html"', live)
        self.assertNotIn("chip-city", live)

    def test_empty_ai_live_list_drops_the_section(self):
        make_release_ready(self.tmp)
        self.edit("media.json", lambda d: d.update(ai_live=[]))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn('id="ran-live"', self.page("ai.html"))

    def test_live_cards_without_a_list_default_to_the_two_projects(self):
        self.assertEqual(build.ai_live_slugs({}), build.AI_LIVE)
        self.assertEqual(build.ai_live_slugs({"ai_live": ["x"]}), ("x",))
        self.assertEqual(build.ai_live_slugs({"ai_live": []}), ())
        self.assertEqual(build.ai_live_slugs({"ai_live": "nope"}), build.AI_LIVE)


if __name__ == "__main__":
    unittest.main()
