"""The share card and the photo badges, driven in a real (headless Chrome) browser.

What the owner does: open Identity & sharing, replace the card, change their mind and go back to the
default; open a project and tick AI-assisted / Composite on a photograph. Every test also asserts the
page logged no console errors (see tearDown). Skipped when Chrome or websocket-client is missing;
the replace test needs macOS sips.
"""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cdp
import cms_fixture as F

DND = """
window.__dnd = (src, dst, after) => {
  const dt = new DataTransfer();
  src.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
  const r = dst.getBoundingClientRect();
  const y = r.top + r.height * (after ? 0.8 : 0.2);
  dst.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt, clientY: y}));
  dst.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt, clientY: y}));
  src.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
};
window.confirm = () => true;
window.alert = () => {};
window.setInput = (sel, value, root) => {
  const i = (root || document).querySelector(sel); i.value = value;
  i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true}));
};
"""
HAS_SIPS = shutil.which("sips") is not None
DEFAULT = "assets/img/og-cover.jpg"
CARD = "assets/img/hero.jpg"          # a fixture asset that already exists, for the "set" case


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestMediaUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-media-"))
        F.make_site(cls.tmp)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.browser = cdp.Browser(1440, 1200).__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.browser.__exit__(None, None, None)
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        F.make_site(self.tmp)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.run_build()
        self.p = self.browser.open(self.base)
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.p.js(DND)
        self.p.js("localStorage.clear(); sessionStorage.clear()")

    def tearDown(self):
        self.p.pump(0.2)
        errors = list(self.p.errors)
        self.p.js("dirty = false; dirtyLists.clear()")
        self.p.close()
        self.assertEqual(errors, [], "the page logged errors")

    # -- helpers ------------------------------------------------------------------------------
    def wait(self, expr, timeout=30):
        return self.p.wait(expr, timeout)

    def toast(self, pattern, timeout=40):
        self.wait("(()=>{const t=document.querySelector('#toast');return !!t && !t.hidden && /%s/.test(t.textContent)})()"
                  % pattern, timeout)
        return self.p.js("document.querySelector('#toast').textContent")

    def state(self):
        return json.load(urllib.request.urlopen(self.base + "api/state"))

    def config(self):
        return json.loads((self.tmp / "site-config.json").read_text(encoding="utf-8"))

    def post(self, path, body):
        """A write straight through the API, for setting up a state the UI should show."""
        data = json.dumps(dict(body, version=self.state()["version"])).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())

    def dirty(self):
        return "Unsaved changes" in self.p.js("document.querySelector('#status').textContent")

    def open_page(self, key, title):
        self.p.js("document.querySelector('#site-list [data-pane=\"%s\"]').click()" % key)
        self.wait("document.querySelector('#pane h1').textContent === %s" % json.dumps(title))

    def open_project(self, slug):
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"%s\"]').closest('li').click()" % slug)
        self.wait("(document.querySelector('#pane .slug')||{textContent:''}).textContent.startsWith('%s')" % slug)

    def open_share_card(self):
        self.open_page("identity", "Identity & sharing")
        self.p.js("document.querySelector('#pane .acc[data-sec=\"share\"] .acc-head').click()")
        self.wait("document.querySelector('#pane .acc[data-sec=\"share\"]').classList.contains('open')")

    def share(self, expr):
        return self.p.js("(() => { const s = document.querySelector('[data-share-card]'); return %s; })()" % expr)

    # -- the share card ------------------------------------------------------------------------
    def test_the_share_card_section_shows_the_default_card(self):
        self.open_share_card()
        self.assertIn("iMessage and Slack", self.p.js("document.querySelector('[data-share-caption]').textContent"))
        self.assertIn("1200", self.p.js("document.querySelector('[data-share-caption]').textContent"))
        self.assertEqual(self.share("s.querySelector('.img-path').textContent"), DEFAULT + " (default)")
        self.assertIn(DEFAULT, self.share("s.querySelector('img').getAttribute('src')"))
        self.assertFalse(self.share("s.querySelector('img').hidden"), "the default card is previewed")
        self.assertEqual(self.share("s.querySelector('[data-pick]').textContent"), "Replace image…")
        self.assertTrue(self.share("!!s.querySelector('input[type=file]')"), "there is nowhere to upload")
        self.assertEqual(self.share("s.querySelector('[data-clear]').textContent"), "Use the default")
        self.assertEqual(self.share("s.querySelector('[data-clear]').style.display"), "none",
                         "nothing is stored, so there is nothing to go back from")
        self.assertFalse(self.p.js("!!document.querySelector('[data-share-placeholder]')"),
                         "the placeholder is gone")

    @unittest.skipUnless(HAS_SIPS, "needs macOS sips")
    def test_replacing_the_card_saves_it_without_a_page_save(self):
        self.open_share_card()
        self.p.js("""(async () => {
          const c = document.createElement('canvas'); c.width = 1600; c.height = 900;
          const x = c.getContext('2d'); x.fillStyle = '#e2401c'; x.fillRect(0, 0, 1600, 900);
          const blob = await new Promise(r => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'UI Share Card.png', {type: 'image/png'}));
          const input = document.querySelector('[data-share-card] input[type=file]');
          input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
        })()""")
        self.assertEqual(self.toast("Share card replaced", 90), "Share card replaced")
        saved = "assets/img/ui-share-card.jpg"
        self.assertEqual(self.config()["og_image"], saved)
        self.assertTrue((self.tmp / saved).is_file())
        self.assertEqual(self.share("s.querySelector('.img-path').textContent"), saved)
        self.assertIn(saved, self.share("s.querySelector('img').getAttribute('src')"))
        self.assertEqual(self.share("s.querySelector('[data-clear]').style.display"), "",
                         "now there is something to go back from")
        self.assertFalse(self.dirty(), "the card is saved, so nothing is left unsaved")
        self.assertIn("og:image\" content=\"https://example.com/" + saved,
                      (self.tmp / "index.html").read_text(encoding="utf-8"))

    @unittest.skipUnless(HAS_SIPS, "needs macOS sips")
    def test_use_the_default_goes_back_to_the_shipped_card(self):
        self.post("/api/config", {"og_image": CARD})
        self.p.js("load()")
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.open_share_card()
        self.assertEqual(self.share("s.querySelector('.img-path').textContent"), CARD)
        self.p.js("document.querySelector('[data-share-card] [data-clear]').click()")
        self.assertEqual(self.toast("Back to the original share card", 90), "Back to the original share card")
        self.assertNotIn("og_image", self.config(), "the key is dropped, not stored as a default")
        self.assertEqual(self.share("s.querySelector('.img-path').textContent"), DEFAULT + " (default)")
        self.assertEqual(self.share("s.querySelector('[data-clear]').style.display"), "none")

    # -- the photo badges ----------------------------------------------------------------------
    def test_the_photo_toggles_set_and_clear_both_badges(self):
        self.open_project("alpha")
        self.assertEqual(self.p.js("document.querySelectorAll('.shot').length"), 2)
        toggle = "document.querySelectorAll('.shot')[0].querySelector('[data-flag=%s]')"
        self.p.js(toggle % "ai" + ".click()")
        self.assertEqual(self.toast("Badge updated"), "Badge updated")
        photo = self.state()["manifest"]["projects"]["alpha"]["photos"][0]
        self.assertIs(photo["ai"], True)
        self.assertNotIn("composite", photo)
        self.assertTrue(self.p.js(toggle % "ai" + ".checked"))
        self.p.js(toggle % "composite" + ".click()")
        self.toast("Badge updated")
        photo = self.state()["manifest"]["projects"]["alpha"]["photos"][0]
        self.assertIs(photo["composite"], True)
        self.assertIs(photo["ai"], True)
        self.p.js(toggle % "ai" + ".click()")
        self.toast("Badge updated")
        photo = self.state()["manifest"]["projects"]["alpha"]["photos"][0]
        self.assertNotIn("ai", photo)
        self.assertIs(photo["composite"], True, "the other badge stays where it was")
        self.assertFalse(self.p.js(toggle % "ai" + ".checked"))
        self.assertTrue(self.p.js(toggle % "composite" + ".checked"))
        self.assertEqual(self.p.js("document.querySelectorAll('.shot').length"), 2,
                         "a badge must not rebuild the photo list")

    def test_a_saved_badge_is_shown_when_the_project_is_opened_again(self):
        self.post("/api/media", {"slug": "alpha", "action": "update", "index": 0, "ai": True})
        self.p.js("load()")
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.open_project("alpha")
        self.assertTrue(self.p.js("document.querySelectorAll('.shot')[0].querySelector('[data-flag=ai]').checked"))
        self.assertFalse(self.p.js("document.querySelectorAll('.shot')[0]"
                                   ".querySelector('[data-flag=composite]').checked"))


if __name__ == "__main__":
    unittest.main()
