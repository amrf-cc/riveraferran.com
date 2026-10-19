"""Tests for the generated site. Run: python3 -m unittest discover -s tests -v"""
import hashlib
import html
import re
import unittest

from support import (ROOT, TOP_PAGES, EMAIL_OR_DOMAIN, NAME_UNACCENTED, built_pages,
                     is_draft_build, is_ph, load, local_links, meta_tags, read,
                     visible_projects)

NAV_LABELS = ("Work", "Reels", "AI &amp; Workflow", "Paid Social", "About", "Contact")


class BuiltSite(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "index.html").exists():
            self.skipTest("site not built yet — run python3 build.py")


class TestPages(BuiltSite):
    def test_expected_pages_exist(self):
        for n in TOP_PAGES + ("404.html",):
            self.assertTrue((ROOT / n).exists(), "missing %s" % n)
        for p in visible_projects():
            self.assertTrue((ROOT / "work" / (p["slug"] + ".html")).exists(),
                            "missing work/%s.html" % p["slug"])

    def test_no_stale_or_hidden_case_pages(self):
        expected = {p["slug"] + ".html" for p in visible_projects()}
        actual = {f.name for f in (ROOT / "work").glob("*.html")}
        self.assertEqual(actual, expected)

    def test_hidden_projects_not_linked(self):
        for p in load("projects.json")["projects"]:
            if not p.get("hidden"):
                continue
            link = "work/%s.html" % p["slug"]
            for page in built_pages():
                self.assertNotIn(link, read(page), "%s links hidden %s" % (page.name, link))

    def test_draft_only_follows_build_mode(self):
        draft = is_draft_build()
        work = read(ROOT / "work.html")
        for p in load("projects.json")["projects"]:
            if p.get("draft_only") and not p.get("hidden"):
                page = ROOT / "work" / (p["slug"] + ".html")
                self.assertEqual(page.exists(), draft, p["slug"])
                if draft:
                    self.assertIn('class="draft-ribbon"', read(page))
                    card = re.search(r'<article class="card"[^>]*>(?:(?!</article>).)*work/%s\.html'
                                     % re.escape(p["slug"]), work, re.S)
                    self.assertTrue(card and "draft-ribbon" in card.group(0),
                                    "no Draft ribbon on the %s card" % p["slug"])

    def test_gallery_output_removed(self):
        self.assertFalse((ROOT / "gallery.html").exists())
        self.assertFalse((ROOT / "gallery").exists())
        for page in built_pages():
            self.assertNotRegex(read(page), r'href="[^"]*gallery', page.name)

    def test_no_cname_without_domain(self):
        if not (load("site-config.json").get("domain") or "").strip():
            self.assertFalse((ROOT / "CNAME").exists(), "CNAME written with an empty domain")

    def test_social_tags(self):
        domain = (load("site-config.json").get("domain") or "").strip()
        for page in built_pages():
            meta = meta_tags(page)
            og_image = meta["og:image"][0]
            self.assertEqual(og_image, meta["twitter:image"][0], page.name)
            if domain:
                self.assertTrue(og_image.startswith("https://%s/" % domain), page.name)
                self.assertIn("og:url", meta, page.name)
            else:
                self.assertNotIn("og:url", meta, "og:url without a domain on %s" % page.name)
                self.assertFalse(og_image.startswith(("http", "/")), page.name)
                self.assertTrue((page.parent / og_image).resolve().is_file(),
                                "og:image does not resolve on %s" % page.name)

    def test_no_absolute_root_paths(self):
        """Root-relative URLs break on a subpath. 404.html is the one exemption."""
        for page in built_pages():
            hits = re.findall(r'(?:href|src|poster|data-full|data-src|data-poster|srcset)="/(?!/)', read(page))
            self.assertEqual(hits, [], "root-relative URLs in %s" % page.name)

    def test_no_broken_local_links(self):
        broken = []
        for page in built_pages():
            for link, target in local_links(page):
                if not target.exists():
                    broken.append("%s -> %s" % (page.relative_to(ROOT), link))
        self.assertEqual(broken, [], "Broken links:\n" + "\n".join(broken))

    def test_name_is_accented_everywhere(self):
        name = load("site-config.json")["name"]
        for page in built_pages(include_404=True):
            text = EMAIL_OR_DOMAIN.sub("", read(page))
            self.assertIsNone(NAME_UNACCENTED.search(text),
                              "unaccented name on %s: %r" % (page.name, NAME_UNACCENTED.search(text)))
            self.assertIn(html.escape(name), read(page), page.name)


class TestNav(BuiltSite):
    def test_nav_on_every_page(self):
        config = load("site-config.json")
        for page in built_pages():
            text = read(page)
            nav = text[text.index('<nav class="main"'):text.index("</nav>", text.index('<nav class="main"'))]
            labels = re.findall(r">([^<>]+?)(?:<span[^>]*>[^<]*</span>)?</a>", nav)
            self.assertEqual(tuple(labels[:6]), NAV_LABELS, page.name)
            self.assertIn("Résumé", nav, page.name)
            self.assertIn('class="nav-toggle"', text, page.name)
            if not config.get("resume_pdf"):
                self.assertRegex(nav, r'href="(\.\./)?about\.html#resume"', page.name)

    def test_active_state(self):
        cases = {"work.html": "Work", "about.html": "About", "contact.html": "Contact",
                 "reels.html": "Reels", "ai.html": "AI &amp; Workflow", "paid-social.html": "Paid Social"}
        for name, label in cases.items():
            active = re.findall(r'<a [^>]*class="active"[^>]*>([^<]+)</a>', read(ROOT / name))
            self.assertEqual(active, [label], name)
        self.assertEqual(re.findall(r'class="active" aria-current', read(ROOT / "index.html")), [])
        for page in (ROOT / "work").glob("*.html"):
            active = re.findall(r'<a [^>]*class="active"[^>]*>([^<]+)</a>', read(page))
            self.assertEqual(active, ["Work"], page.name)

    def test_resume_anchor_exists(self):
        self.assertIn('id="resume"', read(ROOT / "about.html"))

    def test_only_home_has_masthead_body(self):
        for page in built_pages():
            body = re.search(r"<body([^>]*)>", read(page)).group(1)
            self.assertEqual("has-masthead" in body, page.name == "index.html", page.name)


class TestDraftLayer(BuiltSite):
    def test_banner_matches_build_mode(self):
        draft = is_draft_build()
        for page in built_pages():
            text = read(page)
            self.assertEqual('class="draft-banner"' in text, draft, page.name)
            self.assertIn("Draft preview — not for publishing" if draft else "<body", text)

    def test_placeholders_and_confirm_pills_render_in_draft(self):
        if not is_draft_build():
            self.skipTest("release build")
        for p in visible_projects():
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            for key in ("result", "bts", "demo", "ai_steps", "description", "brief"):
                v = p.get(key)
                if key == "description" and p.get("brief"):
                    continue
                if is_ph(v):
                    self.assertIn('<div class="ph-box"><span class="ph-label">To add</span><p>%s</p></div>'
                                  % html.escape(v[1:-1].strip()), text, "%s.%s" % (p["slug"], key))
            for key in ("role", "years", "client", "title"):
                v = p.get(key)
                if is_ph(v):
                    self.assertIn('<span class="ph-chip" title="To add">%s</span>'
                                  % html.escape(v[1:-1].strip()), text, "%s.%s" % (p["slug"], key))
            if p.get("confirm"):
                self.assertIn('<span class="confirm-pill">Confirm: %s</span>'
                              % html.escape(", ".join(f.replace("_", " ") for f in p["confirm"])),
                              text, p["slug"])

    def test_no_raw_brackets_leak(self):
        """Placeholder strings never render with their brackets."""
        for page in built_pages():
            text = read(page)
            for m in re.finditer(r">\s*\[[^\]<]{3,}\]\s*<", text):
                self.fail("raw placeholder on %s: %s" % (page.name, m.group(0)))


class TestHome(BuiltSite):
    def setUp(self):
        super().setUp()
        self.text = read(ROOT / "index.html")
        self.config = load("site-config.json")

    def test_masthead_picture_markup(self):
        c = self.config
        phone = re.findall(r'<source[^>]*media="\(max-width: 720px\)"[^>]*srcset="([^"]+)"', self.text)
        self.assertIn(c["hero_image_phone"], phone)
        self.assertTrue(re.findall(r'<source[^>]*srcset="(data:image/gif[^"]*)"', self.text))
        self.assertRegex(self.text, r'<img[^>]*class="layer front"[^>]*src="%s"' % re.escape(c["hero_cutout"]))

    def test_masthead_labels(self):
        top = re.search(r'<div class="labels top">(.*?)</div>', self.text, re.S).group(1)
        spans = re.findall(r"<span>(.*?)</span>", top)
        self.assertEqual(spans, [html.escape(self.config["title"]), html.escape(self.config["based"])])

    def test_masthead_name_lockup(self):
        words = self.config["name"].split()
        h1 = re.search(r'<h1 class="name">(.*?)</h1>', self.text, re.S).group(1)
        self.assertTrue(h1.startswith('<span class="w">%s</span><span class="last">' % html.escape(words[0])), h1)
        rest = re.search(r'<span class="last">(.*)</span>$', h1).group(1)
        self.assertEqual(re.findall(r'<span class="w">([^<]+)</span>', rest),
                         [html.escape(w) for w in words[1:]])

    def test_masthead_css_constant(self):
        css = read(ROOT / "assets/css/styles.css")
        self.assertIn("/ 7.384), 240px)", css)
        self.assertNotIn("7.263), 240px)", css)
        rule = re.search(r"\.masthead \.name \{([^}]*)\}", css).group(1)
        self.assertRegex(rule, r"padding-top:\s*0\.1\d+em", "accent headroom missing")

    def test_phone_hides_front_layer(self):
        css = read(ROOT / "assets/css/styles.css")
        m = re.search(r"@media\s*\(max-width:\s*720px\)\s*\{(.*?)\n\}", css, flags=re.S)
        self.assertRegex(m.group(1), r"\.masthead\s+\.front\s*\{\s*display:\s*none;?\s*\}")

    def test_intro_and_buttons(self):
        self.assertIn('<a class="btn" href="#reel">Watch reel</a>', self.text)
        self.assertIn('href="work.html">See work</a>', self.text)
        self.assertIn('id="reel"', self.text)

    def test_showreel_or_placeholder(self):
        reel = re.search(r'<section class="reel" id="reel">(.*?)</section>', self.text, re.S).group(1)
        sr = self.config["showreel"]
        empty = [s for s in ("vertical", "horizontal") if not (sr[s].get("livid") or sr[s].get("mp4"))]
        if empty:
            self.assertIn('class="ph-box"', reel)
            if is_ph(sr.get("placeholder")):
                self.assertIn(html.escape(sr["placeholder"][1:-1].strip()), reel)
        for s in ("vertical", "horizontal"):
            if sr[s].get("livid"):
                self.assertIn(html.escape(sr[s]["livid"]), reel)

    def test_featured_order(self):
        featured = sorted([p for p in visible_projects() if p.get("featured")], key=lambda p: p["featured"])
        lead = re.search(r'<article class="feature"[^>]*>\s*<a class="thumb[^"]*" href="work/([^"]+)\.html"', self.text)
        self.assertEqual(lead.group(1), featured[0]["slug"])
        grid = re.search(r'<div class="grid featured-grid">(.*?)\n    </div>', self.text, re.S).group(1)
        cards = re.findall(r'<a class="thumb[^"]*" href="work/([^"]+)\.html"', grid)
        self.assertEqual(cards, [p["slug"] for p in featured[1:4]])

    def test_home_covers_use_full_photos(self):
        manifest = load("assets-manifest.json")["projects"]
        for slug, src in re.findall(r'<a class="thumb" href="work/([^"]+)\.html"[^>]*><img src="([^"?]+)', self.text):
            photos = manifest[slug]["photos"]
            if photos:
                self.assertEqual(src, photos[0]["full"], slug)

    def test_capabilities_link_to_work_filters(self):
        for cap in self.config["capabilities"]:
            self.assertIn('href="work.html#%s"' % cap["tag"], self.text)

    def test_collaborations_and_bio(self):
        for n in self.config["collaborations"]["names"]:
            self.assertIn(html.escape(n), self.text)
        for b in self.config["bio"]:
            self.assertIn(html.escape(b), self.text)
        self.assertIn('href="about.html"', self.text)
        self.assertIn('href="contact.html"', self.text)


class TestWork(BuiltSite):
    def test_one_card_per_visible_project(self):
        text = read(ROOT / "work.html")
        cards = re.findall(r'<article class="card" data-tags="([^"]*)">\s*<a class="thumb[^"]*" href="work/([^"]+)\.html"', text)
        vis = visible_projects()
        self.assertEqual([c[1] for c in cards], [p["slug"] for p in vis])
        for (tags, slug), p in zip(cards, vis):
            self.assertEqual(tags.split(), p.get("tags", []), slug)

    def test_filter_pills(self):
        text = read(ROOT / "work.html")
        filters = re.findall(r'<a class="filter[^"]*" href="#([^"]+)" data-filter="([^"]+)"', text)
        caps = load("site-config.json")["capabilities"]
        self.assertEqual([f[1] for f in filters], ["all"] + [c["tag"] for c in caps])
        self.assertIn("data-filter-group", text)

    def test_filter_js(self):
        js = read(ROOT / "assets/js/main.js")
        for marker in ("data-filter-group", "hashchange", "data-tags"):
            self.assertIn(marker, js)


class TestCaseStudies(BuiltSite):
    def test_body_sections(self):
        for p in visible_projects():
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            if p.get("brief"):
                self.assertIn('<h2 class="kicker">The brief</h2>', text, p["slug"])
                if p.get("did"):
                    self.assertIn('<h2 class="kicker">What I did</h2>', text, p["slug"])
                self.assertEqual('<h2 class="kicker">Scope</h2>' in text, bool(p.get("scope")), p["slug"])
                if p.get("result"):
                    self.assertIn('<h2 class="kicker">Result</h2>', text, p["slug"])
            elif p.get("description") and not is_ph(p["description"]):
                self.assertIn(html.escape(p["description"]), text, p["slug"])

    def test_videos_reels_and_photos(self):
        manifest = load("assets-manifest.json")["projects"]
        reels = load("media.json").get("reels", [])
        for p in visible_projects():
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            data = manifest[p["slug"]]
            for v in data["videos"]:
                if v.get("livid"):
                    self.assertIn(html.escape(v["livid"]), text, p["slug"])
                elif v.get("mp4"):
                    self.assertIn(v["mp4"], text, p["slug"])
            for r in reels:
                if r.get("project") != p["slug"]:
                    continue
                key = r.get("livid") or r.get("mp4")
                if key:
                    self.assertIn(html.escape(key), text, "%s missing reel %s" % (p["slug"], r["slug"]))
            self.assertEqual(text.count('<button class="shot"'), len(data["photos"]), p["slug"])
            self.assertEqual('id="lightbox"' in text, bool(data["photos"]), p["slug"])

    def test_prev_next(self):
        vis = visible_projects()
        n = len(vis)
        for i, p in enumerate(vis):
            text = read(ROOT / "work" / (p["slug"] + ".html"))
            self.assertIn('<a class="prev" href="%s.html">' % vis[(i - 1) % n]["slug"], text)
            self.assertIn('<a class="next" href="%s.html">' % vis[(i + 1) % n]["slug"], text)

    def test_lightbox_controls(self):
        for page in (ROOT / "work").glob("*.html"):
            text = read(page)
            if 'class="shot"' not in text:
                continue
            for marker in ('id="lightbox"', 'role="dialog"', "lb-prev", "lb-next", "lb-close", "lb-count"):
                self.assertIn(marker, text, "%s missing %s" % (page.name, marker))
        for name in TOP_PAGES:
            self.assertNotIn('id="lightbox"', read(ROOT / name), name)

    def test_lightbox_js_and_css(self):
        js = read(ROOT / "assets/js/main.js")
        for marker in ("ArrowRight", "ArrowLeft", "Escape", "touchstart", "lb-prev", "lb-next", "preload"):
            self.assertIn(marker, js)
        css = read(ROOT / "assets/css/styles.css")
        for marker in (".lb-prev", ".lb-next", ".lb-close", ".lightbox.single"):
            self.assertIn(marker, css)


class TestHardening(BuiltSite):
    """CSP, noai, legal links, download deterrents, AI-crawler policy."""

    def csp(self, text):
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', text)
        return m.group(1) if m else None

    def test_security_meta_on_every_page(self):
        for p in built_pages():
            text = read(p)
            policy = self.csp(text)
            self.assertIsNotNone(policy, "no CSP: %s" % p.name)
            self.assertIn("frame-src https://livid.com", policy, p.name)
            self.assertIn("connect-src https://formspree.io", policy, p.name)
            self.assertIn("form-action 'self' https://formspree.io", policy, p.name)
            self.assertIn('name="referrer"', text, p.name)
            self.assertIn('name="robots" content="noai, noimageai"', text, p.name)
        self.assertIn('name="robots" content="noai, noimageai"', read(ROOT / "404.html"))

    def test_csp_is_strict(self):
        policy = self.csp(read(ROOT / "index.html"))
        self.assertNotIn("'unsafe-eval'", policy)
        directives = {d.strip().split(" ", 1)[0]: d.strip() for d in policy.split(";") if d.strip()}
        self.assertEqual(directives["script-src"], "script-src 'self'")
        self.assertEqual(directives["object-src"], "object-src 'none'")

    def test_legal_links_in_every_footer(self):
        for p in built_pages():
            rp = "../" if p.parent.name == "work" else ""
            text = read(p)
            footer = text[text.index('<footer class="site">'):]
            self.assertIn('href="%sprivacy.html"' % rp, footer, p.name)
            self.assertIn('href="%sterms.html"' % rp, footer, p.name)

    def test_legal_pages_carry_copy(self):
        self.assertIn("Formspree", read(ROOT / "privacy.html"))
        terms = read(ROOT / "terms.html")
        self.assertIn("No AI training or scraping", terms)
        self.assertIn("Article 4(3)", terms)

    def test_media_not_draggable(self):
        for p in built_pages():
            text = read(p)
            for tag in re.findall(r"<img\b[^>]*>", text):
                self.assertIn('draggable="false"', tag, "%s: %s" % (p.name, tag))
            for tag in re.findall(r"<video\b[^>]*>", text):
                self.assertIn('controlslist="nodownload"', tag, "%s: %s" % (p.name, tag))

    def test_js_video_swap_keeps_hardening(self):
        js = read(ROOT / "assets/js/main.js")
        self.assertIn("'controlslist', 'nodownload'", js)
        self.assertIn("'draggable', 'false'", js)

    def test_css_touch_callout(self):
        self.assertIn("-webkit-touch-callout: none", read(ROOT / "assets/css/styles.css"))

    def test_robots_blocks_ai_crawlers_only(self):
        text = read(ROOT / "robots.txt")
        for bot in ("GPTBot", "CCBot", "Google-Extended", "ClaudeBot"):
            self.assertIn("User-agent: %s\n" % bot, text)
        groups = [g for g in text.strip().split("\n\n") if "User-agent" in g]
        self.assertIn("Disallow: /", groups[0])
        self.assertTrue(groups[-1].strip().endswith("User-agent: *\nAllow: /"))

    def test_js_blocks_context_menu_and_drag(self):
        text = read(ROOT / "assets/js/main.js")
        self.assertIn("'contextmenu'", text)
        self.assertIn("'dragstart'", text)


class TestMediaVersioning(BuiltSite):
    """Every local image/video/poster URL carries a content hash."""

    def test_media_urls_carry_content_hash(self):
        checked = 0
        for page in built_pages():
            text = read(page)
            urls = re.findall(r'(?:src|data-full|data-src|data-poster|poster)="([^"]*assets/(?:img|video)/[^"]+)"', text)
            for url in urls:
                rel = url.split("?")[0]
                if "/masthead/" in rel or rel.endswith("og-cover.jpg"):
                    continue  # fixed hero art, never renumbered
                self.assertIn("?v=", url, "%s: unversioned media %s" % (page.name, url))
                want = hashlib.sha1((page.parent / rel).resolve().read_bytes()).hexdigest()[:8]
                self.assertEqual(url.split("?v=")[1], want, "%s: stale version on %s" % (page.name, rel))
                checked += 1
        self.assertGreater(checked, 0)


class TestParallax(unittest.TestCase):
    def test_parallax_css(self):
        text = read(ROOT / "assets/css/styles.css")
        media = r"@media\s*\(min-width:\s*721px\)\s*and\s*\(prefers-reduced-motion:\s*no-preference\)"
        self.assertEqual(len(re.findall(media, text)), 1)
        start = re.search(media, text)
        i = text.index("{", start.end())
        depth = 0
        for j in range(i, len(text)):
            depth += {"{": 1, "}": -1}.get(text[j], 0)
            if depth == 0:
                break
        block = re.sub(r"\s+", " ", text[i:j + 1])
        self.assertRegex(re.search(r"\.masthead\s*\{([^}]*)\}", block).group(1), r"position:\s*sticky")
        for selector in (r"\.masthead \.name", r"\.masthead \.labels"):
            self.assertRegex(re.search(selector + r"\s*\{([^}]*)\}", block).group(1), r"translate3d\([^;]*--mp")

    def test_parallax_js(self):
        text = read(ROOT / "assets/js/main.js")
        for marker in ("--mp", "requestAnimationFrame", "prefers-reduced-motion"):
            self.assertIn(marker, text)


def ph_or_text(v):
    """The escaped text a value renders as (placeholders lose their brackets)."""
    return html.escape(v[1:-1].strip() if is_ph(v) else v)


class TestReels(BuiltSite):
    def setUp(self):
        super().setUp()
        self.text = read(ROOT / "reels.html")
        self.media = load("media.json")

    def test_format_filters_first_seen_order(self):
        import build
        seen = []
        for r in self.media["reels"]:
            key = build.slugify(r.get("format", ""))
            if key not in seen:
                seen.append(key)
        filters = re.findall(r'<a class="filter[^"]*" href="#([^"]+)" data-filter="([^"]+)"', self.text)
        self.assertEqual([f[1] for f in filters], ["all"] + seen)
        self.assertIn('data-filter-scope=".reel-groups"', self.text)
        items = re.findall(r'<div class="reel-item" data-tags="([^"]+)">', self.text)
        self.assertEqual(len(items), len(self.media["reels"]))
        self.assertEqual(sorted(set(items)), sorted(seen))
        self.assertEqual(self.text.count('class="reel-grid"'), 1)

    def test_every_reel_and_motion_item_rendered(self):
        for group in ("reels", "motion"):
            for r in self.media.get(group, []):
                if r.get("livid"):
                    self.assertRegex(self.text, r'style="aspect-ratio:%s"><iframe src="%s"[^>]*loading="lazy"'
                                     % (re.escape(r["ratio"]), re.escape(html.escape(r["livid"]))), r["slug"])
                elif r.get("mp4"):
                    m = re.search(r'style="aspect-ratio:%s"><button type="button" class="reel-play" '
                                  r'data-src="%s\?v=[0-9a-f]{8}"' % (re.escape(r["ratio"]), re.escape(r["mp4"])),
                                  self.text)
                    self.assertTrue(m, r["slug"])
                    if r.get("poster"):
                        self.assertIn('data-poster="%s?v=' % r["poster"], self.text, r["slug"])
                self.assertIn('<span class="rtitle">%s</span>' % ph_or_text(r["title"]), self.text, r["slug"])

    def test_reel_labels_and_confirm_pills(self):
        draft = is_draft_build()
        for r in self.media["reels"]:
            meta = " · ".join(html.escape(b) for b in (r.get("brand"), r.get("format"), r.get("year")) if b)
            self.assertIn('<span class="rtitle">%s</span><span class="rmeta">%s</span>'
                          % (html.escape(r["title"]), meta), self.text, r["slug"])
            if r.get("confirm") and draft:
                pill = '<span class="confirm-pill">Confirm: %s</span>' % ", ".join(r["confirm"])
                card = re.search(r'<span class="rtitle">%s</span>.*?</figcaption>' % re.escape(html.escape(r["title"])),
                                 self.text).group(0)
                self.assertIn(pill, card, r["slug"])

    def test_motion_section(self):
        motion = self.text[self.text.index('id="motion"'):]
        for m in self.media["motion"]:
            if m.get("role"):
                self.assertIn('<span class="role-chip">%s</span>' % ph_or_text(m["role"]), motion, m["slug"])
            if is_ph(m.get("client")) and is_draft_build():
                self.assertIn('<span class="ph-chip" title="To add">%s</span>' % ph_or_text(m["client"]), motion)
        ratios = set(re.findall(r'style="aspect-ratio:([^"]+)"', motion))
        self.assertEqual(ratios, {m["ratio"] for m in self.media["motion"]})

    def test_anatomy_after_grid(self):
        note = self.media.get("reel_formats_note")
        if not note:
            self.skipTest("no reel_formats_note")
        i = self.text.index("Anatomy of a reel</p>")
        self.assertGreater(i, self.text.rindex('class="reel-item"'))
        self.assertLess(i, self.text.index('id="motion"'))
        self.assertIn(ph_or_text(note), self.text)

    def test_reel_js_reused(self):
        js = read(ROOT / "assets/js/main.js")
        self.assertIn("data-filter-scope", js)
        self.assertEqual(js.count("button.reel-play"), 1, "reel playback JS duplicated")
        self.assertIn("pauseOthers", js)


class TestAI(BuiltSite):
    def setUp(self):
        super().setUp()
        import build
        self.build = build
        self.text = read(ROOT / "ai.html")
        self.projects = {p["slug"]: p for p in visible_projects()}
        self.media = load("media.json")

    def test_feature_block(self):
        p = self.projects.get(self.build.AI_FEATURE)
        if not p:
            self.skipTest("feature project not visible")
        feature = re.search(r'<section class="ai-feature">(.*?)</section>', self.text, re.S).group(1)
        self.assertIn('href="work/%s.html"' % p["slug"], feature)
        self.assertIn(ph_or_text(p["title"]), feature)
        for d in p.get("did", []):
            self.assertIn("<li>%s</li>" % ph_or_text(d), feature)
        if p.get("demo"):
            self.assertIn(ph_or_text(p["demo"]), feature)

    def test_process_strip(self):
        steps = re.findall(r'<li><span class="step-num">(\d\d)</span><span class="step-label">([^<]+)</span></li>',
                           self.text)
        self.assertEqual([s[0] for s in steps], ["%02d" % (i + 1) for i in range(len(self.media["ai_process"]))])
        self.assertEqual([s[1] for s in steps], [html.escape(s) for s in self.media["ai_process"]])

    def test_ran_live(self):
        live = re.search(r'<section class="ai-live" id="ran-live">(.*?)</section>', self.text, re.S).group(1)
        draft = is_draft_build()
        for slug in self.build.AI_LIVE:
            p = self.projects.get(slug)
            if not p:
                continue
            card = re.search(r'<article class="card live-card"[^>]*>\s*<a class="thumb[^"]*" href="work/%s\.html".*?</article>'
                             % re.escape(slug), live, re.S)
            self.assertTrue(card, slug)
            card = card.group(0)
            if p.get("ai_steps"):
                self.assertIn(ph_or_text(p["ai_steps"]), card)
            elif draft:
                self.assertIn('<div class="ph-box"><span class="ph-label">To add</span><p>%s AI-assisted pieces: '
                              'real shot → AI step → final</p></div>' % html.escape(p["client"]), card)

    def test_tools_line_and_label(self):
        config = load("site-config.json")
        self.assertIn(html.escape(self.media["ai_tools_line"]), self.text)
        self.assertIn('Every AI-assisted piece on this site is marked <span class="ai-badge">%s</span>.'
                      % html.escape(config["ai_label"]), self.text)


class TestPaidSocial(BuiltSite):
    def test_sections_and_size_placeholders(self):
        text = read(ROOT / "paid-social.html")
        ps = load("media.json").get("paid_social", {})
        for key, anchor, heading in (("video_ads", "video-ads", "Video ads"), ("static_ads", "static-ads", "Static ads")):
            sec = re.search(r'<section class="paid-section" id="%s">(.*?)</section>' % anchor, text, re.S)
            self.assertTrue(sec, anchor)
            sec = sec.group(1)
            self.assertIn("<h2>%s</h2>" % heading, sec)
            if not ps.get(key) and is_draft_build():
                self.assertIn("9:16, 4:5, 1:1", sec)
                self.assertIn('class="ph-box"', sec)
            for item in ps.get(key) or []:
                src = item.get("mp4") or item.get("image") or item.get("livid")
                self.assertIn(html.escape(src), sec)
                self.assertIn('aspect-ratio:%s' % item.get("ratio", ""), sec)

    def test_intro(self):
        intro = load("site-config.json")["page_intros"].get("paid")
        if intro:
            self.assertIn(ph_or_text(intro), read(ROOT / "paid-social.html"))


class TestAbout(BuiltSite):
    def setUp(self):
        super().setUp()
        self.text = read(ROOT / "about.html")
        self.config = load("site-config.json")
        self.media = load("media.json")

    def test_bio_and_intro(self):
        for b in self.config["bio"]:
            self.assertIn(ph_or_text(b), self.text)
        self.assertIn(ph_or_text(self.config["page_intros"]["about"]), self.text)

    def test_experience(self):
        items = re.findall(r'<li><span class="x-role">(.*?)</li>', self.text)
        self.assertEqual(len(items), len(self.config["experience"]))
        for li, e in zip(items, self.config["experience"]):
            self.assertTrue(li.startswith(ph_or_text(e["role"])), li)
            self.assertEqual('class="x-org"' in li, bool(e.get("org")), li)
            if e.get("org"):
                self.assertIn("— %s" % ph_or_text(e["org"]), li)
            for k in ("years", "place"):
                if e.get(k):
                    self.assertIn(ph_or_text(e[k]), li)

    def test_credits_table(self):
        table = re.search(r'<table class="credits">(.*?)</table>(.*?)</div>\s*</div>', self.text, re.S)
        self.assertTrue(table)
        rows = re.findall(r"<tr><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td><td>(.*?)</td></tr>", table.group(1))
        self.assertEqual(len(rows), len(self.media["credits"]))
        for row, cr in zip(rows, self.media["credits"]):
            self.assertEqual(row[0], ph_or_text(cr["artist"]))
            self.assertEqual(row[1], ph_or_text(cr["work"]))
        pill = "confirm-pill" in table.group(2)
        self.assertEqual(pill, bool(self.media.get("credits_confirm")) and is_draft_build())

    def test_lists_and_links(self):
        for t in self.config["tools"]:
            self.assertIn("<li>%s</li>" % html.escape(t), self.text)
        self.assertIn(html.escape(self.config["education"]), self.text)
        for c in self.config["certifications"]:
            self.assertIn(html.escape(c), self.text)
        self.assertIn(html.escape(self.config["languages"]), self.text)
        for sp in self.config["side_projects"]:
            self.assertIn('<a href="%s" rel="noopener">%s</a>' % (html.escape(sp["href"]), html.escape(sp["label"])),
                          self.text)

    def test_resume_section(self):
        sec = re.search(r'id="resume">(.*?)</div>\s*</div>', self.text, re.S).group(1)
        if self.config.get("resume_pdf"):
            self.assertIn('href="%s" download' % html.escape(self.config["resume_pdf"]), sec)
        elif is_draft_build():
            self.assertIn("Résumé PDF: brand version pending", sec)


class TestLegalContact404(BuiltSite):
    def test_legal_is_domain_agnostic(self):
        email = load("site-config.json")["email"]
        for name in ("privacy.html", "terms.html"):
            body = read(ROOT / name)
            body = body[body.index('<article class="wrap legal">'):body.index("</article>")]
            self.assertNotIn("riveraferran.com", body.replace(email, ""), name)
            self.assertNotIn("GitHub", body, name)
            self.assertIn('href="mailto:%s"' % email, body, name)
        privacy = read(ROOT / "privacy.html")
        for processor in ("Formspree", "Livid", "the hosting provider"):
            self.assertIn(processor, privacy.lower() if processor.islower() else privacy, processor)

    def test_contact_subject(self):
        name = load("site-config.json")["name"]
        text = read(ROOT / "contact.html")
        self.assertIn('<input type="hidden" name="_subject" value="Portfolio inquiry — %s">' % html.escape(name), text)
        self.assertNotIn('value="Portfolio inquiry — riveraferran.com"', text)

    def test_case_cta_label(self):
        for page in (ROOT / "work").glob("*.html"):
            text = read(page)
            self.assertNotIn("Start a project", text, page.name)
            self.assertIn('href="../contact.html">Get in touch</a>', text, page.name)

    def test_404(self):
        text = read(ROOT / "404.html")
        self.assertIn('http-equiv="Content-Security-Policy"', text)
        self.assertIn("frame-src https://livid.com", text)
        self.assertIn(html.escape(load("site-config.json")["name"]), text)
        for href in ("/", "/work.html", "/contact.html"):
            self.assertIn('href="%s"' % href, text)
        for page in ("work.html", "reels.html", "contact.html"):
            self.assertTrue((ROOT / page).exists())


if __name__ == "__main__":
    unittest.main()
