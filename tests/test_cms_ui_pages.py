"""The page-first Site Manager, driven in a real (headless Chrome) browser against the synthetic site.

What the owner does with it: pick a page in the sidebar, open a section, change a word, save that page.
Every test asserts the page logged no console errors. Skipped when Chrome or websocket-client is
missing; the upload test needs macOS sips.
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

PAGES = [
    ("everypage", "Every page"), ("home", "Home"), ("work", "Work"), ("case", "Case study"),
    ("reels", "Reels"), ("ai", "AI & Workflow"), ("paid", "Paid Social"), ("about", "About"),
    ("contact", "Contact"), ("legal", "Legal & 404"),
]
TITLES = dict([("identity", "Identity & sharing"), ("visibility", "Visibility")] + PAGES)


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestPagesUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-pages-"))
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

    def config_file(self):
        return json.loads((self.tmp / "site-config.json").read_text(encoding="utf-8"))

    def page_html(self, name):
        return (self.tmp / name).read_text(encoding="utf-8")

    def open_page(self, key):
        self.p.js("document.querySelector('#site-list [data-pane=\"%s\"]').click()" % key)
        self.wait("document.querySelector('#pane h1').textContent === %s" % json.dumps(TITLES[key]))

    def dirty(self):
        return "Unsaved changes" in self.p.js("document.querySelector('#status').textContent")

    def sections(self):
        return self.p.js("[...document.querySelectorAll('#pane .acc .acc-title')].map(x => x.textContent)")

    def open_sections(self):
        return self.p.js("[...document.querySelectorAll('#pane .acc')].map(a => a.classList.contains('open'))")

    def expand(self, index):
        self.p.js("document.querySelectorAll('#pane .acc-head')[%d].click()" % index)

    def save(self, key):
        """Click the page's Save button with the toast hidden, so the next toast() is this save's."""
        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("document.querySelector('#save-%s').click()" % key)

    # -- the tree -----------------------------------------------------------------------------
    def test_the_sidebar_lists_site_then_every_page(self):
        groups = self.p.js("[...document.querySelectorAll('#site-list li.group-head')].map(x => x.textContent)")
        self.assertEqual(groups, ["Site", "Pages"])
        self.assertEqual(self.p.js("[...document.querySelectorAll('#site-list li[data-pane]')].map(x => x.dataset.pane)"),
                         ["identity", "visibility"] + [k for k, _ in PAGES])
        names = self.p.js("[...document.querySelectorAll('#site-list li[data-pane]')].map(x => x.querySelector('.name').textContent)")
        self.assertEqual(names, ["Identity & sharing", "Visibility"] + [t for _, t in PAGES])

    def test_every_page_pane_opens_with_its_sections_in_render_order(self):
        self.open_page("home")
        self.assertEqual(self.sections(),
                         ["Hero", "Selected work", "Capabilities", "Collaborations", "About block"])
        self.open_page("work")
        self.assertEqual(self.sections(), ["Head", "Filters", "Groups", "Cards", "SEO"])
        self.open_page("legal")
        self.assertEqual(self.sections(), ["Privacy", "Terms", "Not found", "SEO"])
        for key, title in PAGES:
            self.open_page(key)
            self.assertEqual(self.p.js("document.querySelector('#pane h1').textContent"), title)
            self.assertTrue(self.p.js("document.querySelectorAll('#pane .acc').length") > 0, key)

    def test_the_first_section_is_open_and_a_collapsed_one_opens_on_click(self):
        self.open_page("home")
        self.assertEqual(self.open_sections(), [True, False, False, False, False])
        self.assertFalse(self.p.js("!!document.querySelector('[data-copy=\"home.selected.kicker\"]').offsetParent"),
                         "a closed section hides its fields")
        self.expand(1)
        self.assertTrue(self.p.js("!!document.querySelector('[data-copy=\"home.selected.kicker\"]').offsetParent"))
        # The open section survives a trip to another pane.
        self.open_page("contact")
        self.open_page("home")
        self.assertEqual(self.open_sections(), [True, True, False, False, False])

    def test_a_page_shows_its_own_registry_text_and_nothing_elses(self):
        self.open_page("everypage")
        keys = self.p.js("[...document.querySelectorAll('[data-copy]')].map(i => i.dataset.copy)")
        self.assertEqual(len(keys), 19)
        self.assertTrue(all(k.split(".")[0] in ("nav", "footer", "lightbox") for k in keys), keys)
        self.assertEqual(self.p.js("document.querySelector('[data-copy=\"footer.rights\"]').placeholder"),
                         "All rights reserved.")

    def test_the_legacy_pane_keys_still_select_their_pane(self):
        for alias, title in [("__site", "Identity & sharing"), ("__vis", "Visibility"),
                             ("__reels", "Reels"), ("__about", "About")]:
            self.p.js("select(%s)" % json.dumps(alias))
            self.assertEqual(self.p.js("document.querySelector('#pane h1').textContent"), title)
            self.assertEqual(self.p.js("document.querySelector('#site-list li.sel').dataset.pane"),
                             {"__site": "identity", "__vis": "visibility",
                              "__reels": "reels", "__about": "about"}[alias])

    # -- saving -------------------------------------------------------------------------------
    def test_editing_one_page_saves_and_rebuilds_only_that_page(self):
        work_before = self.page_html("work.html")
        other = {n: (self.tmp / n).read_bytes() for n in ("projects.json", "media.json", "assets-manifest.json")}
        self.open_page("home")
        self.expand(1)
        self.p.js("setInput('[data-copy=\"home.selected.heading\"]', 'Featured work')")
        self.assertTrue(self.dirty())
        self.save("home")
        self.toast("Home saved")
        self.assertFalse(self.dirty())
        self.assertEqual(self.state()["config"]["copy"], {"home.selected.heading": "Featured work"})
        self.assertIn("Featured work", self.page_html("index.html"))
        self.assertEqual(self.page_html("work.html"), work_before, "another page's bytes must not move")
        for name, data in other.items():
            self.assertEqual((self.tmp / name).read_bytes(), data, name)

    def test_clearing_an_override_restores_the_built_in_text(self):
        self.open_page("home")
        self.expand(1)
        self.p.js("setInput('[data-copy=\"home.selected.heading\"]', 'Featured work')")
        self.save("home")
        self.toast("Home saved")
        self.open_page("home")
        self.expand(1)
        self.assertEqual(self.p.js("document.querySelector('[data-copy=\"home.selected.heading\"]').value"),
                         "Featured work")
        self.p.js("document.querySelector('[data-copy=\"home.selected.heading\"] ~ .reset-default').click()")
        self.save("home")
        self.toast("Home saved")
        self.assertNotIn("copy", self.config_file())
        self.assertIn("Featured projects", self.page_html("index.html"))

    def test_saving_a_page_with_nothing_changed_writes_nothing(self):
        self.open_page("contact")
        self.save("contact")
        self.toast("Nothing changed")
        cfg = self.config_file()
        self.assertNotIn("copy", cfg)
        self.assertNotIn("seo", cfg)

    def test_the_unsaved_pill_appears_and_goes_on_save(self):
        self.open_page("work")
        self.assertIn("Saved", self.p.js("document.querySelector('#status').textContent"))
        self.p.js("setInput('[data-copy=\"work.heading\"]', 'Selected projects')")
        self.assertTrue(self.dirty())
        self.save("work")
        self.toast("Work saved")
        self.assertFalse(self.dirty())
        self.assertIn("Selected projects", self.page_html("work.html"))

    def test_the_character_limit_is_shown_and_enforced(self):
        self.open_page("work")
        self.assertEqual(self.p.js("document.querySelector('[data-copy=\"work.heading\"]').maxLength"), 40)
        self.p.js("setInput('[data-copy=\"work.heading\"]', %s)" % json.dumps("x" * 45))
        self.save("work")
        self.assertIn("40 characters at most", self.toast("too long"))
        self.assertNotIn("copy", self.config_file(), "an over-long value must not be sent")
        self.p.js("setInput('[data-copy=\"work.heading\"]', 'Selected projects')")
        self.save("work")
        self.toast("Work saved")
        self.assertEqual(self.state()["config"]["copy"]["work.heading"], "Selected projects")

    def test_per_page_seo_is_edited_beside_its_page(self):
        self.open_page("work")
        self.expand(4)
        self.p.js("setInput('[data-seo=\"work|description\"]', 'Grouped by the kind of work.')")
        self.save("work")
        self.toast("Work saved")
        self.assertEqual(self.state()["config"]["seo"]["pages"]["work"]["description"],
                         "Grouped by the kind of work.")
        self.assertNotIn("copy", self.config_file())

    def test_leaving_a_page_with_unsaved_text_asks_first(self):
        self.open_page("home")
        self.expand(1)
        self.p.js("setInput('[data-copy=\"home.selected.heading\"]', 'Not saved')")
        self.p.js("window.confirm = () => false")
        self.p.js("document.querySelector('#site-list [data-pane=contact]').click()")
        self.assertEqual(self.p.js("document.querySelector('#pane h1').textContent"), "Home")
        self.p.js("window.confirm = () => true")
        self.p.js("document.querySelector('#site-list [data-pane=contact]').click()")
        self.wait("document.querySelector('#pane h1').textContent === 'Contact'")
        self.assertFalse(self.dirty())

    def test_cmd_s_saves_the_open_page(self):
        self.open_page("contact")
        self.p.js("setInput('[data-copy=\"contact.form.submit\"]', 'Send it')")
        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 's', metaKey: true, bubbles: true}))")
        self.toast("Contact saved")
        self.assertEqual(self.state()["config"]["copy"]["contact.form.submit"], "Send it")

    # -- the images ---------------------------------------------------------------------------
    def test_the_three_masthead_images_have_replace_and_clear_controls(self):
        self.open_page("home")
        self.assertEqual(self.p.js("[...document.querySelectorAll('#pane .imgfield')].map(x => x.dataset.img)"),
                         ["hero_image", "hero_image_phone", "hero_cutout"])
        self.assertEqual(self.p.js("document.querySelector('[data-img=hero_image] .img-path').textContent"),
                         "assets/img/hero.jpg")
        self.assertEqual(self.p.js("document.querySelector('[data-img=hero_image] [data-pick]').textContent"),
                         "Replace image…")
        self.assertEqual(self.p.js("document.querySelector('[data-img=hero_cutout] [data-clear]').textContent"),
                         "Clear")
        self.assertTrue(self.p.js("!!document.querySelector('[data-img=hero_image] input[type=file]')"))

    @unittest.skipUnless(HAS_SIPS, "needs macOS sips")
    def test_an_uploaded_hero_lands_in_the_masthead_and_saves_with_the_page(self):
        self.open_page("home")
        self.p.js("""(async () => {
          const c = document.createElement('canvas'); c.width = 3000; c.height = 2000;
          const x = c.getContext('2d'); x.fillStyle = '#2a8'; x.fillRect(0, 0, 3000, 2000);
          const blob = await new Promise(r => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'UI Hero.png', {type: 'image/png'}));
          const input = document.querySelector('[data-img=hero_image] input[type=file]');
          input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
        })()""")
        self.toast("Image uploaded", 90)
        self.assertTrue(self.dirty())
        path = self.p.js("document.querySelector('[data-img=hero_image] .img-path').textContent")
        self.assertEqual(path, "assets/img/masthead/ui-hero.jpg")
        self.assertTrue((self.tmp / path).is_file())
        self.save("home")
        self.toast("Home saved")
        self.assertEqual(self.state()["config"]["hero_image"], path)

    def test_clearing_a_hero_image_removes_the_value(self):
        self.open_page("home")
        self.p.js("document.querySelector('[data-img=hero_image_phone] [data-clear]').click()")
        self.assertTrue(self.dirty())
        self.save("home")
        self.toast("Home saved")
        self.assertEqual(self.state()["config"]["hero_image_phone"], "")

    def test_the_share_card_is_a_marked_placeholder(self):
        self.open_page("identity")
        self.assertIn("assets/img/og-cover.jpg",
                      self.p.js("document.querySelector('[data-share-card] .img-path').textContent"))
        note = self.p.js("document.querySelector('[data-share-placeholder]').textContent")
        self.assertIn("Coming in the next update", note)
        self.assertEqual(self.p.js("document.querySelector('[data-share-card]').dataset.shareCard"),
                         "assets/img/og-cover.jpg")
        self.assertFalse(self.p.js("!!document.querySelector('[data-share-card] input[type=file]')"),
                         "the upload belongs to WS3, not here")

    # -- the rest of the manager ---------------------------------------------------------------
    def test_the_case_and_ai_panes_link_to_a_projects_comparison_sets(self):
        self.open_page("case")
        self.assertEqual(self.p.js("document.querySelector('[data-sets-link]').textContent"),
                         "Comparison sets are edited in the project: Rodowita →")
        self.p.js("document.querySelector('[data-open-sets=\"rodowita-water\"]').click()")
        self.wait("document.querySelector('#pane .slug').textContent.startsWith('rodowita-water')")
        self.assertTrue(self.p.js("document.querySelector('details.sets').open"),
                        "the sets editor is opened for the owner")
        self.open_page("ai")
        self.assertIn("Rodowita", self.p.js("document.querySelector('[data-sets-link]').textContent"))

    def test_the_project_list_still_drags_and_saves_the_order(self):
        before = [p["slug"] for p in self.state()["projects"]]
        self.p.js("(()=>{const li=[...document.querySelectorAll('#proj-list li.proj')]; window.__dnd(li[2], li[0], false)})()")
        self.toast("Project order saved")
        after = [p["slug"] for p in self.state()["projects"]]
        self.assertTrue(after.index("rodowita-water") < after.index("alpha"),
                        "the dragged project now comes first in its group")
        self.assertEqual(sorted(before), sorted(after))


if __name__ == "__main__":
    unittest.main()
