"""The Site Manager UI, driven in a real (headless Chrome) browser against the synthetic site.

These are the flows that matter most: hiding and showing a project, ordering, saving, photos, videos,
the site panes, creating and deleting. Every test also asserts the page logged no console errors.
Skipped when Chrome or websocket-client is missing. Needs macOS sips for the upload test.
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


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-"))
        F.make_site(cls.tmp)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.browser = cdp.Browser(1440, 900).__enter__()

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
        self.expect_errors = False
        self.p = self.browser.open(self.base)
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.p.js(DND)

    def tearDown(self):
        self.p.pump(0.2)
        errors = [] if self.expect_errors else list(self.p.errors)
        # A test may end with unsaved edits on purpose. Without this, leaving the page would trigger the
        # beforeunload prompt, which headless Chrome reports as an error against the NEXT test's page.
        self.p.js("dirty = false; dirtyLists.clear()")
        self.p.close()
        self.assertEqual(errors, [], "the page logged errors")

    # -- helpers ------------------------------------------------------------------------------
    def wait(self, expr, timeout=30):
        return self.p.wait(expr, timeout)

    def toast(self, pattern, timeout=40):
        self.wait("(()=>{const t=document.querySelector('#toast');return !!t && !t.hidden && /%s/.test(t.textContent)})()" % pattern, timeout)
        return self.p.js("document.querySelector('#toast').textContent")

    def state(self):
        return json.load(urllib.request.urlopen(self.base + "api/state"))

    def project(self, slug):
        return next(p for p in self.state()["projects"] if p["slug"] == slug)

    def hidden(self):
        return sorted(p["slug"] for p in self.state()["projects"] if p.get("hidden"))

    def open_project(self, slug):
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"%s\"]').closest('li').click()" % slug)
        self.wait("(document.querySelector('#pane .slug')||{textContent:''}).textContent.startsWith('%s')" % slug)

    PANES = ["Site settings", "Visibility", "Reels & motion", "About & credits"]

    def open_pane(self, index):
        self.p.js("document.querySelectorAll('#site-list li')[%d].click()" % index)
        self.wait("document.querySelector('#pane h1') && document.querySelector('#pane h1').textContent === %s" % json.dumps(self.PANES[index]))

    def dirty(self):
        return "Unsaved changes" in self.p.js("document.querySelector('#status').textContent")

    # -- the sidebar ----------------------------------------------------------------------------
    def test_the_sidebar_groups_projects_like_the_work_page(self):
        heads = self.p.js("[...document.querySelectorAll('#proj-list li.group-head')].map(x => x.textContent.trim().replace(/\\s+/g, ' '))")
        self.assertEqual(heads, ["Food & Beverage 3", "Music Videos 1", "Own page, not in Work 1"])
        self.assertEqual(self.p.js("[...document.querySelectorAll('#proj-list li .flag')].map(x => x.textContent)"), ["LEAD", "FEATURED", "FEATURED"])
        self.assertIn("Release-ready", self.p.js("document.querySelector('#status').textContent"))

    def test_the_eye_hides_and_shows_a_project_in_one_click(self):
        self.p.js("document.querySelector('#proj-list .eye[data-vis-slug=\"beta\"]').click()")
        self.assertIn("Gone from the Work page", self.toast("Hidden: Beta"))
        self.assertEqual(self.hidden(), ["beta"])
        self.assertFalse((self.tmp / "work" / "beta.html").exists())
        self.assertTrue(self.p.js("""(()=>{const li=[...document.querySelectorAll('#proj-list li')];
            const h=li.findIndex(x=>x.classList.contains('group-head')&&/Hidden/.test(x.textContent));
            return h>=0 && li.findIndex(x=>x.querySelector('[data-vis-slug=beta]')) > h})()"""))
        self.assertIn("1 hidden", self.p.js("document.querySelector('#status').textContent"))
        self.p.js("document.querySelector('#proj-list .eye[data-vis-slug=\"beta\"]').click()")
        self.toast("Visible again: Beta")
        self.assertEqual(self.hidden(), [])
        self.assertTrue((self.tmp / "work" / "beta.html").exists())

    def test_the_header_switch_toggles_without_touching_unsaved_edits(self):
        self.open_project("alpha")
        self.p.js("setInput('[data-k=oneliner]', 'A line I have not saved yet')")
        self.assertTrue(self.dirty())
        self.p.js("document.querySelector('[data-vis-switch=\"alpha\"]').click()")
        self.toast("Hidden: Alpha")
        self.assertEqual(self.hidden(), ["alpha"])
        self.assertFalse(self.p.js("document.querySelector('[data-vis-note=\"alpha\"]').hidden"))
        self.assertTrue(self.p.js("document.querySelector('[data-view-slug=\"alpha\"]').hidden"))
        self.assertEqual(self.p.js("document.querySelector('.head .switch .txt b').textContent"), "Hidden from the site")
        self.assertEqual(self.p.js("document.querySelector('[data-k=oneliner]').value"), "A line I have not saved yet")
        self.assertEqual([x for x in self.p.js("[...document.querySelectorAll('#proj-list .flag')].map(x=>x.textContent)")][0], "LEAD",
                         "the next featured project leads the home page now")
        self.p.js("document.querySelector('[data-vis-switch=\"alpha\"]').click()")
        self.toast("Visible again: Alpha")
        self.assertEqual(self.hidden(), [])

    def test_leaving_with_unsaved_edits_asks_first(self):
        self.open_project("alpha")
        self.p.js("setInput('[data-k=title]', 'Changed')")
        self.p.js("window.confirm = () => false")
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"beta\"]').closest('li').click()")
        self.assertEqual(self.p.js("document.querySelector('#pane h1').textContent"), "Alpha")
        self.p.js("window.confirm = () => true")
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"beta\"]').closest('li').click()")
        self.wait("document.querySelector('#pane h1').textContent === 'Beta'")
        self.assertFalse(self.dirty())

    def test_dragging_a_project_reorders_its_group_only(self):
        before = [p["slug"] for p in self.state()["projects"]]
        self.p.js("(()=>{const li=[...document.querySelectorAll('#proj-list li.proj')]; window.__dnd(li[2], li[0], false)})()")
        self.toast("Project order saved")
        after = [p["slug"] for p in self.state()["projects"]]
        self.assertEqual(after.index("rodowita-water") < after.index("alpha"), True)
        self.assertEqual([s for s in before if s not in ("alpha", "beta", "rodowita-water")],
                         [s for s in after if s not in ("alpha", "beta", "rodowita-water")])
        self.assertEqual(sorted(before), sorted(after))

    def test_filter_and_segments(self):
        self.p.js("setInput('#q', 'gam')")
        self.assertEqual(self.p.js("document.querySelectorAll('#proj-list li.proj').length"), 1)
        self.assertIsNone(self.p.js("document.querySelector('#proj-list li.proj').getAttribute('draggable')"))
        self.p.js("setInput('#q', '')")
        self.p.js("document.querySelector('#proj-list .eye[data-vis-slug=\"gamma\"]').click()")
        self.toast("Hidden: Gamma")
        self.p.js("document.querySelector('#seg [data-seg=hidden]').click()")
        self.assertEqual(self.p.js("[...document.querySelectorAll('#proj-list li.proj')].length"), 1)
        self.p.js("document.querySelector('#seg [data-seg=live]').click()")
        self.assertEqual(self.p.js("[...document.querySelectorAll('#proj-list li.proj')].every(r => !r.classList.contains('is-hidden'))"), True)

    # -- project editing ------------------------------------------------------------------------
    def test_the_form_saves_and_cmd_s_saves_too(self):
        self.open_project("beta")
        self.p.js("setInput('[data-k=result]', 'Sold out in a week.')")
        self.assertTrue(self.dirty())
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project saved")
        self.assertEqual(self.project("beta")["result"], "Sold out in a week.")
        self.assertFalse(self.dirty())
        self.p.js("setInput('[data-k=bts]', 'On set.')")
        self.p.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 's', metaKey: true, bubbles: true}))")
        self.toast("Project saved")
        self.assertEqual(self.project("beta")["bts"], "On set.")
        self.p.js("document.querySelector('[data-tag=social]').click()")
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project saved")
        self.assertEqual(self.project("beta")["tags"], ["product", "social"])

    def test_featured_needs_a_number_and_a_blank_unfeatures(self):
        self.open_project("gamma")
        self.assertTrue(self.p.js("document.querySelector('[data-k=featured]').disabled"))
        self.p.js("document.querySelector('[data-k=feat-on]').click()")
        self.assertEqual(self.p.js("document.querySelector('[data-k=featured]').value"), "4")
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project saved")
        self.assertEqual(self.project("gamma")["featured"], 4)
        self.p.js("document.querySelector('[data-k=feat-on]').click()")
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project saved")
        self.assertIs(self.project("gamma")["featured"], False)

    @unittest.skipUnless(HAS_SIPS, "needs macOS sips")
    def test_photos_alt_reorder_upload_and_remove(self):
        self.open_project("alpha")
        full0 = [x["full"] for x in self.state()["manifest"]["projects"]["alpha"]["photos"]]
        self.assertEqual(self.p.js("document.querySelectorAll('.shot').length"), 2)
        self.assertEqual(self.p.js("document.querySelector('.shot .cover').textContent"), "COVER")
        self.p.js("setInput('.shot .alt', 'Alt from the UI')")
        self.toast("Alt text saved")
        self.assertEqual(self.state()["manifest"]["projects"]["alpha"]["photos"][0]["alt"], "Alt from the UI")
        self.p.js("(()=>{const s=document.querySelectorAll('.shot'); window.__dnd(s[1], s[0], false)})()")
        self.toast("Order saved")
        self.assertEqual([x["full"] for x in self.state()["manifest"]["projects"]["alpha"]["photos"]], full0[::-1])
        self.p.js("""(async () => {
          const c = document.createElement('canvas'); c.width = 3000; c.height = 2000;
          const x = c.getContext('2d'); x.fillStyle = '#2a8'; x.fillRect(0, 0, 3000, 2000);
          const blob = await new Promise(r => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'UI Test Shot.png', {type: 'image/png'}));
          const input = document.querySelector('#photo-section input[type=file]');
          input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
        })()""")
        self.toast("1 photograph added", 90)
        photos = self.state()["manifest"]["projects"]["alpha"]["photos"]
        self.assertEqual((len(photos), photos[-1]["full"]), (3, "assets/img/alpha/ui-test-shot.jpg"))
        self.assertTrue((self.tmp / photos[-1]["thumb"]).is_file())
        self.p.js("[...document.querySelectorAll('.shot .del')].pop().click()")
        self.toast("Photograph removed")
        self.assertEqual(len(self.state()["manifest"]["projects"]["alpha"]["photos"]), 2)

    def test_the_video_list_adds_normalises_and_removes(self):
        self.open_project("alpha")
        rows = "document.querySelectorAll('#photo-section ~ section .rowx')"
        self.assertEqual(self.p.js(rows + ".length"), 1)
        self.p.js("document.querySelector('#photo-section ~ section [data-add]').click()")
        self.p.js("""(()=>{const r=[...document.querySelectorAll('#photo-section ~ section .rowx')].pop();
          setInput('[data-f=title]', 'UI test video', r); setInput('[data-f=livid]', 'https://livid.com/watch/UITESTVID12', r);})()""")
        self.assertTrue(self.dirty())
        self.p.js("document.querySelector('#photo-section ~ section .js-save').click()")
        self.toast("Video list saved")
        vids = self.state()["manifest"]["projects"]["alpha"]["videos"]
        self.assertEqual(vids[-1]["livid"], "https://livid.com/embed/UITESTVID12?livid_logo=0&fullscreen=1&title=1")
        self.assertIs(vids[0]["ai"], True, "the existing video keeps its flags")
        self.assertIn("title=0", vids[0]["livid"], "and its deliberate embed parameters")
        self.p.js("[...document.querySelectorAll('#photo-section ~ section .rowx')].pop().querySelector('.del').click()")
        self.p.js("document.querySelector('#photo-section ~ section .js-save').click()")
        self.toast("Video list saved")
        self.assertEqual(len(self.state()["manifest"]["projects"]["alpha"]["videos"]), 1)

    def test_a_new_project_can_be_created_and_deleted(self):
        self.p.js("document.querySelector('#btn-new').click()")
        self.wait("document.querySelector('#pane h1').textContent === 'New project'")
        self.p.js("setInput('[data-k=client]', 'UI Test Client'); setInput('[data-k=title]', 'UI test project')")
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project created")
        slug = "ui-test-client-ui-test-project"
        self.assertIn(slug, self.state()["manifest"]["projects"])
        self.assertTrue((self.tmp / "work" / (slug + ".html")).exists())
        self.wait("!!document.querySelector('#photo-section')")
        self.p.js("document.querySelector('#del-proj').click()")
        self.toast("Project deleted")
        self.assertNotIn(slug, [p["slug"] for p in self.state()["projects"]])
        self.assertFalse((self.tmp / "work" / (slug + ".html")).exists())

    # -- the site panes -------------------------------------------------------------------------
    def test_site_settings_save_together_and_the_resume_card_lists_the_file(self):
        self.open_pane(0)
        self.assertEqual(self.p.js("document.querySelector('[data-k=headline]').value"), "Headline.")
        self.p.js("setInput('[data-k=headline]', 'A new headline.')")
        self.p.js("document.querySelector('#save-site').click()")
        self.toast("Settings saved")
        self.assertEqual(self.state()["config"]["headline"], "A new headline.")
        self.assertIn("Resume.pdf", self.p.js("document.querySelector('.res-file').textContent"))
        self.p.js("setInput('[data-k=domain]', 'not a domain')")
        self.p.js("document.querySelector('#save-site').click()")
        self.expect_errors = True                       # the browser logs the refused request
        self.toast("domain")
        self.assertEqual(self.state()["config"]["domain"], "example.com")

    def test_the_visibility_pane_switches_pages_sections_and_cards(self):
        self.open_pane(1)
        sw = "document.querySelectorAll('.vcard .switch input')"
        self.p.js(sw + "[0].click()")
        self.toast("Showreel is on")
        self.assertNotIn("hidden", self.state()["config"]["showreel"])
        self.assertIn("Draft", self.p.js("document.querySelector('#status').textContent"))
        self.p.js(sw + "[0].click()")
        self.toast("Showreel hidden")
        self.assertIn("Release-ready", self.p.js("document.querySelector('#status').textContent"))
        self.p.js(sw + "[1].click()")
        self.toast("Paid Social page is on")
        self.assertTrue((self.tmp / "paid-social.html").exists())
        self.p.js(sw + "[1].click()")
        self.toast("Paid Social page hidden")
        self.assertFalse((self.tmp / "paid-social.html").exists())
        self.p.js(sw + "[2].click()")
        self.toast("Rodowita is off the AI page")
        self.assertEqual(self.state()["media"]["ai_live"], [])
        self.p.js(sw + "[2].click()")
        self.toast("Rodowita is on the AI page")
        self.p.js("document.querySelector('.vrow[data-vis-row=\"gamma\"] input').click()")
        self.toast("Hidden: Gamma")
        self.assertEqual(self.hidden(), ["gamma"])
        self.assertTrue(self.p.js("document.querySelector('.vrow[data-vis-row=\"gamma\"]').classList.contains('is-hidden')"))

    def test_reels_and_credits_are_editable_lists(self):
        self.open_pane(2)
        sec = "document.querySelectorAll('.rows-section')[0]"
        self.p.js(sec + ".querySelector('[data-add]').click()")
        self.p.js("""(()=>{const r=[...document.querySelectorAll('.rows-section')[0].querySelectorAll('.rowx')].pop();
          setInput('[data-f=title]', 'UI test reel', r); setInput('[data-f=livid]', 'https://livid.com/watch/UIREELTEST1', r);
          setInput('[data-f=project]', 'gamma', r);})()""")
        self.p.js(sec + ".querySelector('.js-save').click()")
        self.toast("Reels saved")
        reels = self.state()["media"]["reels"]
        self.assertEqual((len(reels), reels[-1]["slug"], reels[-1]["project"], reels[0]["ai"]), (2, "ui-test-reel", "gamma", True))
        self.open_pane(3)
        self.p.js("setInput('.rows-section:nth-of-type(2) [data-f=role]', 'Director')")
        self.p.js("document.querySelectorAll('.rows-section')[1].querySelector('.js-save').click()")
        self.toast("Credits saved")
        self.assertEqual(self.state()["media"]["credits"][0]["role"], "Director")

    def test_the_release_check_lists_what_blocks_and_rebuild_shows_the_log(self):
        self.p.js("document.querySelector('#btn-release').click()")
        self.wait("!document.querySelector('#sheet').hidden")
        self.assertIn("Release-ready", self.p.js("document.querySelector('#sheet-body').textContent"))
        self.p.js("document.querySelector('#sheet-close').click()")
        self.open_pane(1)
        self.p.js("document.querySelectorAll('.vcard .switch input')[0].click()")
        self.toast("Showreel is on")
        self.p.js("document.querySelector('#pill-release').click()")
        self.wait("!document.querySelector('#sheet').hidden && /left to resolve/.test(document.querySelector('#sheet-body').textContent)")
        text = self.p.js("document.querySelector('#sheet-body').textContent")
        self.assertIn("showreel.vertical", text)
        self.assertIn("showreel.horizontal", text)
        self.p.js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))")
        self.assertTrue(self.p.js("document.querySelector('#sheet').hidden"))
        self.p.js("document.querySelector('#btn-build').click()")
        self.wait("!document.querySelector('#sheet').hidden && /BUILD_OK/.test(document.querySelector('#sheet-body').textContent)", 60)

    def test_a_page_left_open_across_a_change_is_refused_not_overwritten(self):
        before = (self.tmp / "projects.json").read_bytes()
        data = json.loads(before)
        data["projects"][2]["title"] = "Changed behind the page's back"
        (self.tmp / "projects.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        changed = (self.tmp / "projects.json").read_bytes()
        self.p.js("window.confirm = () => false")           # decline the reload offer
        self.expect_errors = True                            # the browser logs the 409
        self.p.js("document.querySelector('#proj-list .eye[data-vis-slug=\"gamma\"]').click()")
        self.toast("changed since this page loaded")
        self.assertEqual((self.tmp / "projects.json").read_bytes(), changed, "the stale click must not write")


if __name__ == "__main__":
    unittest.main()
