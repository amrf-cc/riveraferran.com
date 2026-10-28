"""Where the share card and the photo badges end up in the built site.

build.py is not changed by this workstream (WS2 had already taught head_html to read
`site-config.og_image`); these tests prove the two writers produce data the existing renderer already
understands: the meta tags on every page, the badge on a masonry tile, on a card cover and in the
lightbox caption that travels through data-badge.
"""
import unittest

import test_cms  # noqa: F401  (its CmsCase is the fixture harness; importing it runs no tests twice)

CARD = "assets/img/hero.jpg"          # a fixture asset: the og_image writer only needs it to exist
DEFAULT = "https://example.com/assets/img/og-cover.jpg"


class BuildMedia(test_cms.CmsCase):
    def pages(self):
        """Every built page that carries the shared <head>: 404.html is a standalone page by design."""
        root = sorted(p for p in self.tmp.glob("*.html") if p.name != "404.html")
        work = sorted((self.tmp / "work").glob("*.html"))
        self.assertTrue(root and work, "the fixture site was not built")
        return root + work

    def flags(self, slug="alpha", index=0, **kw):
        status, body = self.post("/api/media", dict({"slug": slug, "action": "update", "index": index}, **kw))
        self.assertEqual(status, 200, body)

    def work_page(self, slug="alpha"):
        return self.text("work/%s.html" % slug)


class TestShareCardMeta(BuildMedia):
    def test_a_set_card_is_og_and_twitter_image_on_every_page(self):
        status, body = self.post("/api/config", {"og_image": CARD})
        self.assertEqual(status, 200, body)
        want = "https://example.com/" + CARD
        for page in self.pages():
            html = page.read_text(encoding="utf-8")
            self.assertIn('<meta property="og:image" content="%s">' % want, html, page.name)
            self.assertIn('<meta name="twitter:image" content="%s">' % want, html, page.name)

    def test_with_no_card_every_page_uses_the_shipped_one(self):
        for page in self.pages():
            html = page.read_text(encoding="utf-8")
            self.assertIn('<meta property="og:image" content="%s">' % DEFAULT, html, page.name)
            self.assertIn('<meta name="twitter:image" content="%s">' % DEFAULT, html, page.name)
            self.assertIn('<meta property="og:image:width" content="1200">', html, page.name)
            self.assertIn('<meta property="og:image:height" content="630">', html, page.name)

    def test_clearing_the_card_puts_the_shipped_one_back(self):
        self.post("/api/config", {"og_image": CARD})
        self.assertNotIn(DEFAULT, self.text("index.html"))
        self.post("/api/config", {"og_image": ""})
        self.assertIn('<meta property="og:image" content="%s">' % DEFAULT, self.text("index.html"))
        self.assertIn('<meta property="og:image" content="%s">' % DEFAULT, self.work_page())


class TestPhotoBadgesInTheBuild(BuildMedia):
    def test_the_tile_the_cover_and_the_lightbox_caption_carry_the_badge(self):
        self.flags(ai=True)
        html = self.work_page()
        self.assertIn('<span class="ai-badge shot-badge">AI-assisted</span>', html,
                      "the masonry tile lost its badge")
        self.assertIn('data-badge="AI-assisted"', html, "the lightbox caption reads data-badge")
        self.assertNotIn('data-badge=""', html.split("assets/img/alpha/01.jpg")[1].split("<button")[0],
                         "the first tile is the flagged one")
        self.assertIn('<span class="ai-badge thumb-badge">AI-assisted</span>', self.text("work.html"),
                      "the card cover lost its badge")
        self.assertIn("AI-assisted", self.text("index.html"), "the featured cover on the home page too")

    def test_composite_adds_a_second_badge(self):
        self.flags(ai=True, composite=True)
        html = self.work_page()
        self.assertIn('<span class="ai-badge shot-badge">AI-assisted · Composite</span>', html)
        self.assertIn('data-badge="AI-assisted · Composite"', html)

    def test_clearing_the_flag_removes_the_badge(self):
        self.flags(ai=True, composite=True)
        self.assertIn('data-badge="AI-assisted · Composite"', self.work_page())
        self.flags(ai=False, composite=False)
        html = self.work_page()
        self.assertNotIn('data-badge="AI-assisted', html)
        self.assertNotIn("ai-badge shot-badge", html)
        self.assertNotIn("thumb-badge", self.text("work.html"))


if __name__ == "__main__":
    unittest.main()
