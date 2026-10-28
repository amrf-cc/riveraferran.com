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
from unittest import mock
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

    PANES = {"identity": "Identity & sharing", "visibility": "Visibility", "home": "Home",
             "reels": "Reels", "about": "About"}

    def open_pane(self, key):
        self.p.js("document.querySelector('#site-list [data-pane=\"%s\"]').click()" % key)
        self.wait("document.querySelector('#pane h1') && document.querySelector('#pane h1').textContent === %s"
                  % json.dumps(self.PANES[key]))

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
        self.open_pane("identity")
        self.assertEqual(self.p.js("document.querySelector('[data-k=name]').value"), "Test Person")
        self.p.js("setInput('[data-k=name]', 'A New Name')")
        self.p.js("document.querySelector('#save-identity').click()")
        self.toast("saved")
        self.assertEqual(self.state()["config"]["name"], "A New Name")
        self.assertIn("Resume.pdf", self.p.js("document.querySelector('.res-file').textContent"))
        self.p.js("setInput('[data-k=domain]', 'not a domain')")
        self.p.js("document.querySelector('#save-identity').click()")
        self.expect_errors = True                       # the browser logs the refused request
        self.toast("domain")
        self.assertEqual(self.state()["config"]["domain"], "example.com")
        # The headline moved to its own page pane; it still saves in that page's one request.
        self.open_pane("home")
        self.assertEqual(self.p.js("document.querySelector('[data-k=headline]').value"), "Headline.")
        self.p.js("setInput('[data-k=headline]', 'A new headline.')")
        self.p.js("document.querySelector('#save-home').click()")
        self.toast("Home saved")
        self.assertEqual(self.state()["config"]["headline"], "A new headline.")

    def test_the_visibility_pane_switches_pages_sections_and_cards(self):
        self.open_pane("visibility")
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
        self.open_pane("reels")
        sec = "document.querySelectorAll('.rows-section')[0]"
        self.p.js(sec + ".querySelector('[data-add]').click()")
        self.p.js("""(()=>{const r=[...document.querySelectorAll('.rows-section')[0].querySelectorAll('.rowx')].pop();
          setInput('[data-f=title]', 'UI test reel', r); setInput('[data-f=livid]', 'https://livid.com/watch/UIREELTEST1', r);
          setInput('[data-f=project]', 'gamma', r);})()""")
        self.p.js(sec + ".querySelector('.js-save').click()")
        self.toast("Reels saved")
        reels = self.state()["media"]["reels"]
        self.assertEqual((len(reels), reels[-1]["slug"], reels[-1]["project"], reels[0]["ai"]), (2, "ui-test-reel", "gamma", True))
        self.open_pane("about")
        self.p.js("setInput('[data-f=role]', 'Director', document.querySelectorAll('.rows-section')[1])")
        self.p.js("document.querySelectorAll('.rows-section')[1].querySelector('.js-save').click()")
        self.toast("Credits saved")
        self.assertEqual(self.state()["media"]["credits"][0]["role"], "Director")

    def test_the_release_check_lists_what_blocks_and_rebuild_shows_the_log(self):
        self.p.js("document.querySelector('#btn-release').click()")
        self.wait("!document.querySelector('#sheet').hidden")
        self.assertIn("Release-ready", self.p.js("document.querySelector('#sheet-body').textContent"))
        self.p.js("document.querySelector('#sheet-close').click()")
        self.open_pane("visibility")
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


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestPublishUi(unittest.TestCase):
    """The Publish button, its status pill and the confirm flow, against a real git repo whose `origin` is a
    local bare repository (never the network). The fixture's own tiny tests/ folder is the publish gate."""

    @classmethod
    def setUpClass(cls):
        cls.template = F.GitSite()
        cls.work = Path(tempfile.mkdtemp(prefix="publish-ui-"))
        cls.tmp, cls.bare = cls.work / "site", cls.work / "origin.git"
        cls.template.copy_to(cls.tmp, cls.bare)
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
        shutil.rmtree(str(cls.work), ignore_errors=True)
        cls.template.close()

    def setUp(self):
        self.template.copy_to(self.tmp, self.bare)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.JOB.clear()
        self.cms.JOB.update(self.cms.IDLE_JOB)
        self.p = self.browser.open(self.base)
        self.wait("document.querySelectorAll('#proj-list li.proj').length > 0")
        self.p.js(DND)
        self.wait_pill("All published")

    def tearDown(self):
        self.p.pump(0.2)
        errors = list(self.p.errors)
        self.p.close()
        self.assertEqual(errors, [], "the page logged errors")

    def wait(self, expr, timeout=30):
        return self.p.wait(expr, timeout)

    def pill(self):
        return self.p.js("document.querySelector('#pub-pill').textContent")

    def wait_pill(self, text):
        self.wait("document.querySelector('#pub-pill').textContent === %s" % json.dumps(text))

    def refocus(self):
        self.p.js("window.dispatchEvent(new Event('focus'))")

    def button(self):
        return self.p.js("(()=>{const b=document.querySelector('#btn-publish');return {disabled:b.disabled,title:b.title}})()")

    def hide_beta(self):
        self.p.js("document.querySelector('#proj-list .eye[data-vis-slug=\"beta\"]').click()")
        self.wait("/Hidden: Beta/.test(document.querySelector('#toast').textContent)")

    def test_a_clean_copy_reads_all_published_and_the_button_is_ready(self):
        self.wait_pill("All published")
        b = self.button()
        self.assertFalse(b["disabled"])
        self.assertIn("example.com", b["title"])
        self.assertIn(str(self.bare), self.p.js("document.querySelector('#pub-pill').title"))

    def test_a_save_repolls_and_the_pill_counts_the_change(self):
        self.wait_pill("All published")
        self.hide_beta()
        self.wait_pill("1 change to publish")                 # no window focus needed: the save re-polls
        self.assertFalse(self.button()["disabled"])

    def test_no_remote_disables_the_button_with_the_reason_as_its_tooltip(self):
        F.git(self.tmp, "remote", "remove", "origin")
        self.refocus()
        self.wait_pill("No GitHub remote")
        b = self.button()
        self.assertTrue(b["disabled"])
        self.assertIn("remote", b["title"].lower())

    def test_unreachable_github(self):
        F.git(self.tmp, "remote", "set-url", "origin", str(self.work / "gone.git"))
        self.refocus()
        self.wait_pill("Cannot reach GitHub")
        self.assertEqual(self.button(), {"disabled": True, "title": "Cannot reach GitHub"})

    def test_newer_commits_on_github_disable_the_button(self):
        other = self.work / "other"
        shutil.rmtree(str(other), ignore_errors=True)
        F.git(self.work, "clone", "-q", str(self.bare), str(other))
        F.git(other, "config", "user.name", "Else")
        F.git(other, "config", "user.email", "else@example.invalid")
        (other / "x.txt").write_text("x\n", encoding="utf-8")
        F.git(other, "add", "-A")
        F.git(other, "commit", "-q", "-m", "Newer")
        F.git(other, "push", "-q", "origin", "HEAD:main")
        self.refocus()
        self.wait_pill("GitHub has newer commits")
        b = self.button()
        self.assertTrue(b["disabled"])
        self.assertIn("merge them first", b["title"])

    def test_commits_waiting_to_be_pushed_count_too(self):
        (self.tmp / "note.txt").write_text("x\n", encoding="utf-8")
        F.git(self.tmp, "add", "-A")
        F.git(self.tmp, "commit", "-q", "-m", "Local only")
        self.refocus()
        self.wait_pill("1 change to publish")

    def test_cancel_closes_the_dialog_and_publishes_nothing(self):
        self.hide_beta()
        self.wait_pill("1 change to publish")
        self.p.js("document.querySelector('#btn-publish').click()")
        self.wait("!document.querySelector('#pub-dialog').hidden")
        text = self.p.js("document.querySelector('#pub-body').textContent")
        self.assertIn("Publish to example.com?", text)
        self.assertIn("This commits everything and pushes to GitHub, which puts it live.", text)
        self.assertEqual(self.p.js("document.querySelector('#pub-msg').placeholder"), "Site update (edited in the site manager)")
        self.p.js("document.querySelector('#pub-cancel').click()")
        self.assertTrue(self.p.js("document.querySelector('#pub-dialog').hidden"))
        self.assertEqual(self.cms.job_snapshot()["state"], "idle")
        self.assertEqual(int(F.git(self.bare, "rev-list", "--count", "main")), 1)

    def test_confirming_shows_the_steps_then_a_toast_with_the_commit_link(self):
        self.hide_beta()
        self.wait_pill("1 change to publish")
        self.p.js("document.querySelector('#btn-publish').click()")
        self.wait("!document.querySelector('#pub-dialog').hidden")
        self.p.js("setInput('#pub-msg', 'Hide Beta (UI test)')")
        with mock.patch.object(self.cms, "_origin_url", return_value="https://github.com/amrf-cc/riveraferran.com.git"):
            self.p.js("document.querySelector('#pub-go').click()")
            self.wait("!!document.querySelector('#pub-steps')")
            steps = self.p.js("[...document.querySelectorAll('#pub-steps li')].map(x => x.textContent.replace(/^\\u2713 /, ''))")
            self.assertEqual(steps, list(self.cms.PUBLISH_STEPS))
            self.wait("/Live in about a minute\\./.test(document.querySelector('#toast').textContent)", 120)
            toast = self.p.js("document.querySelector('#toast').textContent")
            href = self.p.js("document.querySelector('#toast a').href")
        self.assertIn("Published.", toast)
        sha = F.git(self.tmp, "rev-parse", "--short", "HEAD")
        self.assertEqual(href, "https://github.com/amrf-cc/riveraferran.com/commit/" + sha)
        self.assertTrue(self.p.js("document.querySelector('#pub-dialog').hidden"))
        self.assertEqual(F.git(self.bare, "log", "-1", "--format=%s", "main"), "Hide Beta (UI test)")
        self.assertEqual(int(F.git(self.bare, "rev-list", "--count", "main")), 2)
        self.wait_pill("All published")
        self.assertEqual(F.git(self.tmp, "status", "--porcelain"), "")

    def test_a_refused_publish_shows_the_reason_and_the_log(self):
        hook = self.bare / "hooks" / "pre-receive"
        hook.write_text("#!/bin/sh\necho 'pre-receive: not today' >&2\nexit 1\n")
        hook.chmod(0o755)
        self.hide_beta()
        self.wait_pill("1 change to publish")
        self.p.js("document.querySelector('#btn-publish').click()")
        self.wait("!document.querySelector('#pub-dialog').hidden")
        self.p.js("document.querySelector('#pub-go').click()")
        self.wait("!!document.querySelector('#pub-reason')", 120)
        self.assertIn("Not published.", self.p.js("document.querySelector('#pub-body').textContent"))
        self.assertIn("GitHub did not accept the push", self.p.js("document.querySelector('#pub-reason').textContent"))
        self.assertIn("not today", self.p.js("document.querySelector('#pub-log').textContent"))
        self.p.js("document.querySelector('#pub-done').click()")
        self.assertTrue(self.p.js("document.querySelector('#pub-dialog').hidden"))
        self.wait_pill("1 change to publish")                  # the local commit is kept and still waiting
        self.assertEqual(int(F.git(self.bare, "rev-list", "--count", "main")), 1)


if __name__ == "__main__":
    unittest.main()
