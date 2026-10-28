"""The Categories & skills pane and the contact form's endpoint, driven in a real headless Chrome.

What the owner does with it: rename a Work group (and watch the sidebar follow), add a skill and see
it on a project's chip list, try to delete a group a project still uses and read why, and point the
contact form at a Formspree form. Every test asserts the page logged no console errors. Skipped when
Chrome or websocket-client is missing.
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

SETUP = """
window.confirm = () => true;
window.alert = () => {};
window.setInput = (sel, value, root) => {
  const i = (root || document).querySelector(sel); i.value = value;
  i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true}));
};
window.taxRow = (list, key) => [...document.querySelectorAll('[data-tax=' + list + '] .taxrow')]
  .find(r => r.querySelector('.tkey').textContent.trim() === key);
window.taxType = (list, key, field, value) =>
  setInput('[data-f=' + field + ']', value, taxRow(list, key));
window.__dnd = (src, dst, after) => {
  const dt = new DataTransfer();
  src.dispatchEvent(new DragEvent('dragstart', {bubbles: true, dataTransfer: dt}));
  const r = dst.getBoundingClientRect();
  const y = r.top + r.height * (after ? 0.8 : 0.2);
  dst.dispatchEvent(new DragEvent('dragover', {bubbles: true, cancelable: true, dataTransfer: dt, clientY: y}));
  dst.dispatchEvent(new DragEvent('drop', {bubbles: true, cancelable: true, dataTransfer: dt, clientY: y}));
  src.dispatchEvent(new DragEvent('dragend', {bubbles: true, dataTransfer: dt}));
};
"""


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestTaxonomyUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-taxonomy-"))
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
        self.expect_errors = False       # only a test that provokes a refusal sets this
        F.make_site(self.tmp)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.run_build()
        self.p = self.browser.open(self.base)
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.p.js(SETUP)
        self.p.js("localStorage.clear(); sessionStorage.clear()")

    def tearDown(self):
        self.p.pump(0.2)
        # A refused write is a 400 the browser logs as a failed request; that test says so itself.
        errors = [] if self.expect_errors else list(self.p.errors)
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

    def open_pane(self, key, title):
        self.p.js("document.querySelector('#site-list [data-pane=\"%s\"]').click()" % key)
        self.wait("document.querySelector('#pane h1').textContent === %s" % json.dumps(title))

    def open_taxonomy(self):
        self.open_pane("taxonomy", "Categories & skills")

    def sections(self):
        return self.p.js("[...document.querySelectorAll('#pane .acc .acc-title')].map(x => x.textContent)")

    def expand(self, index):
        self.p.js("document.querySelectorAll('#pane .acc-head')[%d].click()" % index)

    def save_taxonomy(self):
        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("document.querySelector('#save-taxonomy').click()")

    def groups(self):
        return self.config_file()["categories"]

    def skills(self):
        return self.config_file()["capabilities"]

    # -- the pane -----------------------------------------------------------------------------
    def test_the_pane_lives_under_site_with_one_section_per_list(self):
        self.assertEqual(
            self.p.js("[...document.querySelectorAll('#site-list li[data-pane]')].map(x => x.dataset.pane)"),
            ["identity", "visibility", "taxonomy", "everypage", "home", "work", "case", "reels", "ai",
             "paid", "about", "contact", "legal"])
        self.assertEqual(self.p.js("document.querySelector('#site-list [data-pane=taxonomy] .name').textContent"),
                         "Categories & skills")
        self.open_taxonomy()
        self.assertEqual(self.sections(), ["Work groups", "Skills"])
        self.assertEqual(self.p.js("[...document.querySelectorAll('[data-tax=categories] .taxrow')].length"), 2)
        self.assertEqual(self.p.js("[...document.querySelectorAll('[data-tax=skills] .taxrow')].length"), 3)
        # A stored key is read-only text with the reason in its tooltip; a label is a 40-character input.
        self.assertEqual(self.p.js("taxRow('categories', 'food').querySelector('.tkey').tagName"), "SPAN")
        self.assertIn("cannot change", self.p.js("taxRow('categories', 'food').querySelector('.tkey').title"))
        self.assertEqual(self.p.js("taxRow('categories', 'food').querySelector('[data-f=label]').maxLength"), 40)
        self.assertEqual(self.p.js("taxRow('skills', 'product').querySelector('[data-f=line]').maxLength"), 160)
        self.assertTrue(self.p.js("!!taxRow('categories', 'food').querySelector('[data-f=personal]')"),
                        "a group carries its personal-work switch")
        self.assertEqual(self.p.js("document.querySelectorAll('[data-tax=skills] .taxrow [data-f=line]').length"), 3)

    # -- editing ------------------------------------------------------------------------------
    def test_renaming_a_group_saves_and_the_sidebar_follows(self):
        self.open_taxonomy()
        self.p.js("taxType('categories', 'food', 'label', 'Food & Drink')")
        self.assertIn("Unsaved changes", self.p.js("document.querySelector('#status').textContent"))
        self.save_taxonomy()
        self.toast("Categories saved")
        self.assertEqual([c["label"] for c in self.groups()], ["Food & Drink", "Music Videos"])
        self.assertEqual([c["key"] for c in self.groups()], ["food", "music"], "the key never changes")
        heads = self.p.js("[...document.querySelectorAll('#proj-list li.group-head')].map(x => x.textContent.trim())")
        self.assertIn("Food & Drink 3", heads, "the sidebar groups follow the new name")
        self.assertIn("Food &amp; Drink", self.page_html("work.html"))

    def test_a_group_can_be_reordered_and_the_sidebar_follows(self):
        self.open_taxonomy()
        self.p.js("(()=>{const rows=[...document.querySelectorAll('[data-tax=categories] .taxrow')];"
                  "window.__dnd(rows[1], rows[0], false)})()")
        self.save_taxonomy()
        self.toast("Categories saved")
        self.assertEqual([c["key"] for c in self.groups()], ["music", "food"])
        heads = self.p.js("[...document.querySelectorAll('#proj-list li.group-head')].map(x => x.textContent.trim())")
        self.assertEqual(heads[:2], ["Music Videos 1", "Food & Beverage 3"])

    def test_a_new_skill_shows_up_in_the_project_pane_chip_list(self):
        self.open_taxonomy()
        self.p.js("document.querySelector('[data-tax-add=skills]').click()")
        self.p.js("""(()=>{const rows=[...document.querySelectorAll('[data-tax=skills] .taxrow')];
          const r=rows[rows.length-1]; const box=r;
          setInput('[data-f=label]', 'Motion design', box);})()""")
        self.p.js("""(()=>{const rows=[...document.querySelectorAll('[data-tax=skills] .taxrow')];
          const r=rows[rows.length-1];
          setInput('[data-f=line]', 'Brand-accurate animation in After Effects.', r);})()""")
        self.assertEqual(self.p.js("(()=>{const rows=[...document.querySelectorAll('[data-tax=skills] .taxrow')];"
                                   "return rows[rows.length-1].querySelector('[data-f=tag]').value})()"),
                         "motion-design", "the tag is derived from the name and shown before saving")
        self.save_taxonomy()
        self.toast("Skills saved")
        self.assertEqual(self.skills()[-1], {"tag": "motion-design", "label": "Motion design",
                                             "line": "Brand-accurate animation in After Effects."})
        # The project pane's chip list is drawn from the same list.
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"alpha\"]').closest('li').click()")
        self.wait("document.querySelector('#pane .slug') && document.querySelector('#pane .slug').textContent.startsWith('alpha')")
        chips = self.p.js("[...document.querySelectorAll('#pane .chips .chip')].map(c => c.textContent)")
        self.assertIn("Motion design", chips)
        self.assertIn("Studio product", chips, "the existing skills are still there")

    def test_deleting_a_group_a_project_uses_is_refused_and_the_row_stays(self):
        self.expect_errors = True                           # the browser logs the refused request
        before = self.groups()
        self.open_taxonomy()
        self.p.js("taxRow('categories', 'food').querySelector('.del').click()")
        self.assertEqual(self.p.js("[...document.querySelectorAll('[data-tax=categories] .taxrow:not([hidden])')].length"), 1)
        self.save_taxonomy()
        self.wait("!document.querySelector('[data-tax-notice]').hidden")
        message = self.p.js("document.querySelector('[data-tax-notice]').textContent")
        self.assertEqual(message, "3 projects still use this group: alpha, beta, rodowita-water. "
                                  "Move them first.")
        self.assertEqual(self.p.js("[...document.querySelectorAll('[data-tax=categories] .taxrow:not([hidden])')].length"),
                         2, "a refused save puts the row back instead of losing the edit")
        self.assertEqual(self.groups(), before, "a refused save writes nothing")
        self.assertIn("Unsaved changes", self.p.js("document.querySelector('#status').textContent"))

    # -- the contact form ---------------------------------------------------------------------
    def test_the_form_endpoint_saves_and_a_wrong_url_is_refused(self):
        self.expect_errors = True                           # the browser logs the refused endpoint
        self.p.js("document.querySelector('#site-list [data-pane=contact]').click()")
        self.wait("document.querySelector('#pane h1').textContent === 'Contact'")
        self.assertEqual(self.sections(), ["Head", "Form", "Messages", "SEO", "Form endpoint"])
        self.expand(4)
        self.assertEqual(self.p.js("document.querySelector('[data-endpoint]').value"), "https://formspree.io/f/test")
        self.assertEqual(self.p.js("document.querySelector('a[href=\"https://formspree.io/forms\"]').target"), "_blank")

        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("setInput('[data-endpoint]', 'https://formspree.io/f/mgaoebdr')")
        self.p.js("document.querySelector('[data-endpoint-save]').click()")
        self.toast("Form endpoint saved")
        self.assertEqual(self.config_file()["formspree_endpoint"], "https://formspree.io/f/mgaoebdr")
        self.assertIn('data-endpoint="https://formspree.io/f/mgaoebdr"', self.page_html("contact.html"))

        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("setInput('[data-endpoint]', 'https://example.com/not-formspree')")
        self.p.js("document.querySelector('[data-endpoint-save]').click()")
        self.assertIn("Formspree URL", self.toast("Formspree URL"))
        self.assertEqual(self.config_file()["formspree_endpoint"], "https://formspree.io/f/mgaoebdr",
                         "a refused endpoint writes nothing")


if __name__ == "__main__":
    unittest.main()
