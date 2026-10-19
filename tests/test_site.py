"""Tests for the generated site. Run: python3 -m unittest discover -s tests -v"""
import html
import json
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name):
    with open(ROOT / name) as f:
        return json.load(f)


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag == "a" and d.get("href"):
            self.links.append(d["href"])
        if tag in ("img", "script") and d.get("src"):
            self.links.append(d["src"])
        if tag in ("img", "source") and d.get("srcset"):
            srcset = d["srcset"].strip()
            if not srcset.startswith("data:"):
                for candidate in srcset.split(","):
                    candidate = candidate.strip()
                    if not candidate:
                        continue
                    token = candidate.split()[0] if candidate.split() else ""
                    if not token or token.startswith("data:"):
                        continue
                    self.links.append(token)
        if tag == "link" and d.get("href"):
            self.links.append(d["href"])
        if tag in ("video", "source") and d.get("src"):
            self.links.append(d["src"])
        if tag == "video" and d.get("poster"):
            self.links.append(d["poster"])


class MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        d = dict(attrs)
        key = d.get("property") or d.get("name")
        if key and "content" in d:
            self.meta.setdefault(key, []).append(d["content"])


def meta_tags(html_path):
    parser = MetaTagParser()
    parser.feed(html_path.read_text())
    return parser.meta


def local_links(html_path):
    parser = LinkExtractor()
    parser.feed(html_path.read_text())
    out = []
    for link in parser.links:
        if link.startswith(("http://", "https://", "mailto:", "#", "data:", "//")):
            continue
        target = (html_path.parent / link.split("?")[0].split("#")[0])
        out.append(target.resolve())
    return out


def generated_pages():
    pages = [ROOT / n for n in ("index.html", "gallery.html", "contact.html", "404.html")]
    pages += sorted((ROOT / "gallery").glob("*.html"))
    return pages


def social_pages():
    pages = [ROOT / n for n in ("index.html", "gallery.html", "contact.html")]
    pages += sorted((ROOT / "gallery").glob("*.html"))
    return pages


class TestSiteGenerated(unittest.TestCase):
    def setUp(self):
        if not (ROOT / "index.html").exists():
            self.skipTest("site not built yet — run python3 build.py")

    def test_all_expected_pages_exist(self):
        projects = load("projects.json")["projects"]
        index_text = (ROOT / "index.html").read_text()
        # The About section (site-config.json bio prose) legitimately mentions
        # hidden clients such as 'Bodeguita Manolo', "McDonald's" and 'Pepsi';
        # that copy is a content decision outside this task, so strip it before
        # checking index.html for hidden client names.
        index_without_about = re.sub(r'<section class="about".*?</section>',
                                     "", index_text, flags=re.DOTALL)
        gallery_text = (ROOT / "gallery.html").read_text()
        for p in projects:
            page = ROOT / "gallery" / (p["slug"] + ".html")
            if p.get("hidden"):
                self.assertFalse(page.exists(),
                                 "hidden project has a page: %s" % page)
                link = "gallery/%s.html" % p["slug"]
                self.assertNotIn(link, index_text,
                                 "hidden project link in index.html: %s" % link)
                self.assertNotIn(link, gallery_text,
                                 "hidden project link in gallery.html: %s" % link)
                name = p.get("client", "")
                if name:
                    for candidate in {name, html.escape(name, quote=True)}:
                        self.assertNotIn(candidate, index_without_about,
                                         "hidden client %r in index.html" % name)
                        self.assertNotIn(candidate, gallery_text,
                                         "hidden client %r in gallery.html" % name)
            else:
                self.assertTrue(page.exists(), "missing %s" % page)
        for n in ("index.html", "gallery.html", "contact.html", "404.html"):
            self.assertTrue((ROOT / n).exists(), "missing %s" % n)

    def test_hidden_projects_are_off_the_site(self):
        projects = load("projects.json")["projects"]
        visible = [p for p in projects if not p.get("hidden")]
        for p in projects:
            if p.get("hidden"):
                page = ROOT / "gallery" / (p["slug"] + ".html")
                self.assertFalse(page.exists(), "hidden page exists: %s" % page)
        gallery_text = (ROOT / "gallery.html").read_text()
        self.assertEqual(gallery_text.count('class="card"'), len(visible),
                         "gallery.html should have one card per visible project")

    def test_no_broken_local_links(self):
        # 404.html uses root-absolute paths (allowed: GitHub Pages serves it
        # only at the domain root), so its "/" links are not repo-relative.
        skip = {"404.html"}
        broken = []
        for page in generated_pages():
            if not page.exists() or page.name in skip:
                continue
            for target in local_links(page):
                if not target.exists():
                    broken.append("%s -> %s" % (page.name, target))
        self.assertEqual(broken, [], "Broken links:\n" + "\n".join(broken))

    def test_no_absolute_root_paths(self):
        """Root-relative paths (/assets/...) break on the github.io subpath."""
        # 404.html is the one page allowed root-absolute paths: GitHub Pages
        # serves it only at the domain root, so "/assets/..." and "/" are correct there.
        skip = {"404.html"}
        for page in generated_pages():
            if not page.exists() or page.name in skip:
                continue
            text = page.read_text()
            hits = re.findall(r'(?:href|src|poster)="/(?!/)', text)
            self.assertEqual(hits, [], "root-relative URLs in %s" % page.name)

    def test_cname(self):
        self.assertEqual((ROOT / "CNAME").read_text().strip(), "riveraferran.com")

    def test_masthead_picture_markup(self):
        config = load("site-config.json")
        text = (ROOT / "index.html").read_text()
        phone_sources = re.findall(
            r'<source[^>]*media="\(max-width: 720px\)"[^>]*srcset="([^"]+)"',
            text)
        self.assertIn(config["hero_image_phone"], phone_sources,
                      "phone <source> should point at hero_image_phone")
        gif_sources = re.findall(
            r'<source[^>]*srcset="(data:image/gif[^"]*)"', text)
        self.assertTrue(gif_sources, "missing data:image/gif <source>")
        self.assertRegex(
            text, r'<img[^>]*class="layer front"[^>]*src="%s"'
            % re.escape(config["hero_cutout"]),
            "front img should point at hero_cutout")

    def test_phone_hides_front_layer(self):
        text = (ROOT / "assets/css/styles.css").read_text()
        match = re.search(r'@media\s*\(max-width:\s*720px\)\s*\{(.*?)\n\}',
                          text, flags=re.DOTALL)
        self.assertIsNotNone(match, "no @media (max-width: 720px) block")
        self.assertRegex(
            match.group(1),
            r'\.masthead\s+\.front\s*\{\s*display:\s*none;?\s*\}',
            "phone block must hide .masthead .front")

    def test_contact_form_present_on_index_and_contact(self):
        config = load("site-config.json")
        for page in (ROOT / "index.html", ROOT / "contact.html"):
            text = page.read_text()
            self.assertIn("contact-form", text)
            self.assertIn(config["email"], text)
            self.assertIn("_gotcha", text)  # honeypot

    def test_featured_projects_on_landing(self):
        projects = load("projects.json")["projects"]
        text = (ROOT / "index.html").read_text()
        for p in projects:
            if p.get("featured") and not p.get("hidden"):
                self.assertIn(html.escape(p["client"], quote=True), text)

    def test_video_block_per_project_with_videos(self):
        projects = load("projects.json")["projects"]
        visible = set(p["slug"] for p in projects if not p.get("hidden"))
        manifest = load("assets-manifest.json")
        for slug, data in manifest["projects"].items():
            if slug not in visible:
                continue
            for v in data["videos"]:
                livid = v.get("livid") or ""
                mp4 = v.get("mp4") or ""
                if not livid and not mp4:
                    # A video with neither a Livid embed nor an mp4 renders
                    # nothing, so it must not be asserted here.
                    continue
                text = (ROOT / "gallery" / (slug + ".html")).read_text()
                rendered = (livid and livid in text) or (mp4 and mp4 in text)
                self.assertTrue(rendered, "no video rendered on %s" % slug)

    def test_social_tags_are_absolute(self):
        base = "https://" + load("site-config.json")["domain"]
        pages = social_pages()
        self.assertTrue(pages, "no pages found")
        for page in pages:
            if not page.exists():
                continue
            meta = meta_tags(page)
            for key in ("og:image", "og:url", "twitter:image"):
                self.assertIn(key, meta, "missing %s on %s" % (key, page.name))
            og_image = meta["og:image"][0]
            og_url = meta["og:url"][0]
            twitter_image = meta["twitter:image"][0]
            self.assertTrue(og_image.startswith(base + "/"),
                            "og:image not absolute on %s: %s" % (page.name, og_image))
            self.assertTrue(og_url.startswith(base + "/"),
                            "og:url not absolute on %s: %s" % (page.name, og_url))
            self.assertEqual(og_image, twitter_image,
                             "og:image != twitter:image on %s" % page.name)
            rel = page.relative_to(ROOT).as_posix()
            expected = base + "/" if rel == "index.html" else base + "/" + rel
            self.assertEqual(og_url, expected, "wrong og:url on %s" % page.name)

    def test_og_image_resolves_locally(self):
        base = "https://" + load("site-config.json")["domain"]
        meta = meta_tags(ROOT / "index.html")
        self.assertIn("og:image", meta, "missing og:image on index.html")
        og_image = meta["og:image"][0]
        self.assertTrue(og_image.startswith(base + "/"),
                        "og:image not absolute: %s" % og_image)
        rel = og_image[len(base) + 1:]
        target = ROOT / rel
        self.assertTrue(target.exists(), "og:image missing on disk: %s" % rel)
        self.assertGreater(target.stat().st_size, 20 * 1024,
                           "og:image is suspiciously small: %s" % rel)
        try:
            from PIL import Image
        except ImportError:
            return
        with Image.open(target) as img:
            self.assertEqual(img.size, (1200, 630),
                             "og:image should be 1200x630: %s" % rel)
            self.assertEqual(img.format, "JPEG",
                             "og:image should be JPEG: %s" % rel)

    def test_parallax_css(self):
        """Desktop parallax block pins the masthead and drifts name/labels."""
        text = (ROOT / "assets/css/styles.css").read_text()
        media = (r'@media\s*\(min-width:\s*721px\)\s*and\s*'
                 r'\(prefers-reduced-motion:\s*no-preference\)')
        self.assertEqual(len(re.findall(media, text)), 1,
                         "expected exactly one desktop parallax @media block")
        start = re.search(media, text)
        open_brace = text.index("{", start.end())
        depth = 0
        for i in range(open_brace, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
        block = re.sub(r"\s+", " ", text[open_brace:i + 1])

        pinned = re.search(r"\.masthead\s*\{([^}]*)\}", block)
        self.assertIsNotNone(pinned, "no .masthead rule in the parallax block")
        self.assertRegex(pinned.group(1), r"position:\s*sticky")

        content = re.search(
            r"body\.has-masthead main > section:not\(\.masthead\)\s*,\s*"
            r"body\.has-masthead footer\.site\s*\{([^}]*)\}", block)
        self.assertIsNotNone(content, "no content/footer rule in the block")
        self.assertRegex(content.group(1), r"z-index:\s*1")
        self.assertRegex(content.group(1), r"background:\s*var\(--paper\)")

        for selector in (r"\.masthead \.name", r"\.masthead \.labels"):
            rule = re.search(selector + r"\s*\{([^}]*)\}", block)
            self.assertIsNotNone(rule, "no %s rule in the block" % selector)
            self.assertRegex(rule.group(1), r"translate3d\([^;]*--mp",
                             "%s must translate3d with --mp" % selector)

        for rule in re.finditer(r"\.stack[^{}]*\{([^{}]*)\}", text):
            self.assertNotRegex(rule.group(1), r"\btransform\b",
                                ".stack must not have a transform")

    def test_parallax_js(self):
        """main.js drives the parallax offset on scroll."""
        text = (ROOT / "assets/js/main.js").read_text()
        self.assertIn("--mp", text)
        self.assertIn("requestAnimationFrame", text)
        self.assertIn("prefers-reduced-motion", text)

    def test_project_pages_have_lightbox_controls(self):
        for page in sorted((ROOT / "gallery").glob("*.html")):
            text = page.read_text()
            if 'class="shot"' not in text:
                continue
            for marker in ('id="lightbox"', 'role="dialog"', "lb-prev",
                           "lb-next", "lb-close", "lb-count"):
                self.assertIn(marker, text, "%s missing %s" % (page.name, marker))
        for name in ("index.html", "gallery.html", "contact.html"):
            page = ROOT / name
            if page.exists():
                self.assertNotIn('id="lightbox"', page.read_text(),
                                 "%s must not carry the lightbox" % name)

    def test_lightbox_js_browse_controls(self):
        text = (ROOT / "assets/js/main.js").read_text()
        for marker in ("ArrowRight", "ArrowLeft", "Escape", "Home", "End",
                       "touchstart", "touchend", "lb-prev", "lb-next", "preload"):
            self.assertIn(marker, text, "main.js missing %s" % marker)

    def test_lightbox_css_controls(self):
        text = (ROOT / "assets/css/styles.css").read_text()
        for marker in (".lb-prev", ".lb-next", ".lb-close", ".lightbox.single"):
            self.assertIn(marker, text, "styles.css missing %s" % marker)
        self.assertNotIn("zoom-out", text, "styles.css still has zoom-out")

    def test_homepage_cards_use_full_photos(self):
        manifest = load("assets-manifest.json")
        card_img = re.compile(r'<div class="thumb"><img src="([^"]+)"')
        home_srcs = card_img.findall((ROOT / "index.html").read_text())
        self.assertTrue(home_srcs, "index.html has no feature cards")
        for src in home_srcs:
            self.assertNotIn("/thumbs/", src,
                             "homepage card uses a thumbnail: %s" % src)
        card_href = re.compile(
            r'<a class="card" href="gallery/([^"]+)\.html">\s*'
            r'<div class="thumb"><img src="([^"]+)"')

        def cards_by_slug(page):
            return {slug: src.split("?", 1)[0]
                    for slug, src in card_href.findall(page.read_text())}

        projects = manifest["projects"]
        gallery_cards = cards_by_slug(ROOT / "gallery.html")
        gallery_checked = 0
        for slug, data in projects.items():
            if not data["photos"] or slug not in gallery_cards:
                continue
            self.assertEqual(gallery_cards[slug], data["photos"][0]["thumb"],
                             "gallery card for %s must use photos[0].thumb" % slug)
            gallery_checked += 1
        self.assertTrue(gallery_checked, "gallery.html has no photo cards")

        home_cards = cards_by_slug(ROOT / "index.html")
        home_checked = 0
        for slug, data in projects.items():
            if not data["photos"] or slug not in home_cards:
                continue
            self.assertEqual(home_cards[slug], data["photos"][0]["full"],
                             "homepage card for %s must use photos[0].full" % slug)
            home_checked += 1
        self.assertTrue(home_checked,
                        "index.html has no featured photo cards")


class TestHardening(unittest.TestCase):
    """Security headers, legal pages, download deterrents, AI-crawler policy."""

    def pages(self):
        names = ["index.html", "gallery.html", "contact.html", "privacy.html", "terms.html"]
        out = [ROOT / n for n in names]
        out += sorted((ROOT / "gallery").glob("*.html"))
        return out

    def csp(self, text):
        m = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', text)
        return m.group(1) if m else None

    def test_security_meta_on_every_page(self):
        for p in self.pages():
            text = p.read_text()
            self.assertIsNotNone(self.csp(text), "no CSP: %s" % p.name)
            self.assertIn('name="referrer"', text, p.name)
            self.assertIn('name="robots" content="noai, noimageai"', text, p.name)

    def test_csp_is_strict(self):
        policy = self.csp((ROOT / "index.html").read_text())
        self.assertNotIn("'unsafe-eval'", policy)
        directives = {d.strip().split(" ", 1)[0]: d.strip() for d in policy.split(";") if d.strip()}
        self.assertEqual(directives["script-src"], "script-src 'self'")
        self.assertEqual(directives["object-src"], "object-src 'none'")

    def test_legal_pages_linked_from_every_footer(self):
        for p in self.pages():
            rp = "../" if p.parent.name == "gallery" else ""
            text = p.read_text()
            footer = text[text.index('<footer class="site">'):]
            self.assertIn('href="%sprivacy.html"' % rp, footer, p.name)
            self.assertIn('href="%sterms.html"' % rp, footer, p.name)

    def test_legal_pages_carry_copy(self):
        self.assertIn("Formspree", (ROOT / "privacy.html").read_text())
        terms = (ROOT / "terms.html").read_text()
        self.assertIn("No AI training or scraping", terms)
        self.assertIn("Article 4(3)", terms)

    def test_media_not_draggable(self):
        for p in self.pages():
            text = p.read_text()
            for tag in re.findall(r"<img\b[^>]*>", text):
                self.assertIn('draggable="false"', tag, "%s: %s" % (p.name, tag))
            for tag in re.findall(r"<video\b[^>]*>", text):
                self.assertIn('controlslist="nodownload"', tag, "%s: %s" % (p.name, tag))

    def test_robots_blocks_ai_crawlers_only(self):
        text = (ROOT / "robots.txt").read_text()
        for bot in ("GPTBot", "CCBot", "Google-Extended", "ClaudeBot"):
            self.assertIn("User-agent: %s\n" % bot, text)
        groups = [g for g in text.strip().split("\n\n") if "User-agent" in g]
        self.assertIn("Disallow: /", groups[0])
        self.assertTrue(groups[-1].strip().endswith("User-agent: *\nAllow: /"))
        self.assertNotIn("User-agent: Googlebot\n", text)

    def test_js_blocks_context_menu_and_drag(self):
        text = (ROOT / "assets/js/main.js").read_text()
        self.assertIn("'contextmenu'", text)
        self.assertIn("'dragstart'", text)


class TestLeadUsesCover(unittest.TestCase):
    """The homepage lead block shows the project's cover (photo 01), the same
    photo the cards use, unless that photo is itself the homepage hero."""

    def test_lead_media_rule(self):
        import build
        data = {"photos": [{"full": "a/01.jpg", "alt": "one"}, {"full": "a/02.jpg", "alt": "two"}],
                "videos": []}
        self.assertEqual(build.lead_media(data, {"x/hero.jpg"}), ("a/01.jpg", "one"))
        self.assertEqual(build.lead_media(data, {"a/01.jpg"}), ("a/02.jpg", "two"))

    def test_built_lead_is_cover(self):
        if not (ROOT / "index.html").exists():
            self.skipTest("site not built yet")
        idx = (ROOT / "index.html").read_text()
        m = re.search(r'<div class="feature">\s*<a class="thumb" href="gallery/([^"]+)\.html">'
                      r'<img src="([^"?]+)', idx)
        self.assertTrue(m, "no lead feature block on index.html")
        slug, src = m.group(1), m.group(2)
        config = load("site-config.json")
        cover = load("assets-manifest.json")["projects"][slug]["photos"][0]["full"]
        if cover not in (config.get("hero_image"), config.get("hero_image_phone")):
            self.assertEqual(src, cover, "lead block for %s is not its cover photo" % slug)


class TestMediaVersioning(unittest.TestCase):
    """Reordering photos in the content manager renames files in place, so a
    URL can start serving a different image. Every gallery media URL carries a
    content fingerprint so caches drop the old one."""

    def setUp(self):
        if not (ROOT / "index.html").exists():
            self.skipTest("site not built yet — run python3 build.py")

    def test_gallery_media_urls_carry_content_hash(self):
        import hashlib
        checked = 0
        for page in generated_pages():
            if not page.exists() or page.name == "404.html":
                continue
            text = page.read_text()
            urls = re.findall(r'(?:src|data-full|poster)="([^"]*assets/(?:img|video)/[^"]+)"', text)
            for url in urls:
                rel = url.split("?")[0]
                if "/masthead/" in rel or rel.endswith("og-cover.jpg"):
                    continue  # fixed hero art, never renumbered
                self.assertIn("?v=", url, "%s: unversioned media %s" % (page.name, url))
                target = (page.parent / rel).resolve()
                want = hashlib.sha1(target.read_bytes()).hexdigest()[:8]
                self.assertEqual(url.split("?v=")[1], want,
                                 "%s: stale version on %s" % (page.name, rel))
                checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
