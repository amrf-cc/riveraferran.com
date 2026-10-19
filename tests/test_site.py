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


if __name__ == "__main__":
    unittest.main()
