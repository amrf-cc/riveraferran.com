"""The content panes, driven in a real (headless Chrome) browser against the synthetic site.

What the owner does with them: open the AI page's “Process steps”, type one step per line, save; write
the Reels anatomy note; change the About page's credits heading; add a paid video ad and a paid still;
fill in a project's AI steps and demo lines. Every test asserts the page logged no console errors. Each
section saves its own keys, so a save here must not move any other key.
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

HELPERS = """
window.confirm = () => true;
window.alert = () => {};
window.setInput = (sel, value, root) => {
  const i = (root || document).querySelector(sel); i.value = value;
  i.dispatchEvent(new Event('input', {bubbles: true})); i.dispatchEvent(new Event('change', {bubbles: true}));
};
window.typeInput = (sel, value, root) => {   // no change event: no Livid check, no upload
  const i = (root || document).querySelector(sel); i.value = value;
  i.dispatchEvent(new Event('input', {bubbles: true}));
};
"""
HAS_SIPS = shutil.which("sips") is not None
TITLES = {"ai": "AI & Workflow", "reels": "Reels", "about": "About", "paid": "Paid Social"}
STEPS = ["Brief", "Reference shoot", "Prompt from product references", "Generate",
         "Check against the real product", "Retouch", "Deliver"]


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestContentUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-content-"))
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
        self.p.js(HELPERS)
        self.p.js("localStorage.clear(); sessionStorage.clear()")

    def tearDown(self):
        self.p.pump(0.2)
        errors = list(self.p.errors)
        self.p.js("dirty = false; dirtyLists.clear()")
        self.p.close()
        self.assertEqual(errors, [], "the page logged errors")

    # -- helpers -------------------------------------------------------------------------------
    def wait(self, expr, timeout=30):
        return self.p.wait(expr, timeout)

    def toast(self, pattern, timeout=40):
        self.wait("(()=>{const t=document.querySelector('#toast');return !!t && !t.hidden && /%s/.test(t.textContent)})()"
                  % pattern, timeout)
        return self.p.js("document.querySelector('#toast').textContent")

    def state(self):
        return json.load(urllib.request.urlopen(self.base + "api/state"))

    def data(self, name):
        return json.loads((self.tmp / name).read_text(encoding="utf-8"))

    def page_html(self, name):
        return (self.tmp / name).read_text(encoding="utf-8")

    def open_page(self, key):
        self.p.js("document.querySelector('#site-list [data-pane=\"%s\"]').click()" % key)
        self.wait("(()=>{const h=document.querySelector('#pane h1');return !!h && h.textContent === %s})()"
                  % json.dumps(TITLES[key]))

    def open_section(self, sec):
        self.wait("!!document.querySelector('#pane .acc[data-sec=\"%s\"]')" % sec)
        self.p.js("(()=>{const a=document.querySelector('#pane .acc[data-sec=\"%s\"]');"
                  "if(!a.classList.contains('open')) a.querySelector('.acc-head').click();})()" % sec)
        self.wait("document.querySelector('#pane .acc[data-sec=\"%s\"]').classList.contains('open')" % sec)

    def save_section(self, sec):
        """Click one section's own Save button with the toast hidden, so toast() sees this save."""
        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("document.querySelector('#pane .acc[data-sec=\"%s\"] .js-save').click()" % sec)

    def section(self, sec):
        return "#pane .acc[data-sec=\"%s\"] " % sec

    def dirty(self):
        return "Unsaved changes" in self.p.js("document.querySelector('#status').textContent")

    def enable_paid_page(self):
        self.p.js("select('visibility')")
        self.wait("document.querySelector('#pane h1').textContent === 'Visibility'")
        self.p.js("(()=>{const s=[...document.querySelectorAll('#pane .vcard .switch')]"
                  ".find(x => /Paid Social page/.test(x.textContent));"
                  "s.querySelector('input').click();})()")
        self.wait("!((S.config.hidden_pages||[]).includes('paid-social'))")

    # -- the AI page ---------------------------------------------------------------------------
    def test_the_process_steps_save_and_the_page_shows_them_numbered(self):
        self.open_page("ai")
        self.open_section("process-steps")
        counter = self.p.js("document.querySelector('%s[data-steps]').textContent"
                            % self.section("process-steps"))
        self.assertEqual(counter, "1 step", "the fixture ships one step")
        self.p.js("typeInput('%s textarea', %s)" % (self.section("process-steps"), json.dumps("\n".join(STEPS))))
        self.assertEqual(self.p.js("document.querySelector('%s[data-steps]').textContent"
                                   % self.section("process-steps")), "7 steps")
        self.assertTrue(self.dirty())
        self.save_section("process-steps")
        self.toast("Process steps saved")
        self.assertFalse(self.dirty())
        self.assertEqual(self.state()["media"]["ai_process"], STEPS)
        ai = self.page_html("ai.html")
        self.assertIn('step-num">01<', ai)
        self.assertIn('step-num">07<', ai)
        self.assertIn(STEPS[-1], ai)

    def test_saving_the_steps_touches_no_other_media_text(self):
        before = self.data("media.json")
        self.open_page("ai")
        self.open_section("process-steps")
        self.p.js("typeInput('%s textarea', %s)" % (self.section("process-steps"), json.dumps("Only step")))
        self.save_section("process-steps")
        self.toast("Process steps saved")
        after = self.data("media.json")
        for key in ("ai_tools_line", "reel_formats_note", "credits_note", "paid_social"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["ai_process"], ["Only step"])

    def test_the_tools_paragraph_saves_into_the_disclosure_line(self):
        self.open_page("ai")
        self.open_section("tools-line")
        self.p.js("typeInput('%s textarea', 'Mostly Firefly, refined in Wondr Forge.')"
                  % self.section("tools-line"))
        self.save_section("tools-line")
        self.toast("Tools paragraph saved")
        self.assertEqual(self.state()["media"]["ai_tools_line"], "Mostly Firefly, refined in Wondr Forge.")
        self.assertIn("refined in Wondr Forge", self.page_html("ai.html"))

    # -- the Reels page ------------------------------------------------------------------------
    def test_the_anatomy_note_appears_on_the_reels_page_after_saving(self):
        note = "Hook, turn, payoff: three beats, under twelve seconds."
        self.assertNotIn("reel-anatomy", self.page_html("reels.html"))
        self.open_page("reels")
        self.open_section("anatomy-note")
        self.p.js("typeInput('%s textarea', %s)" % (self.section("anatomy-note"), json.dumps(note)))
        self.save_section("anatomy-note")
        self.toast("Anatomy note saved")
        self.assertEqual(self.state()["media"]["reel_formats_note"], note)
        self.assertIn(note, self.page_html("reels.html"))
        self.assertIn("reel-anatomy", self.page_html("reels.html"))

    # -- the About page ------------------------------------------------------------------------
    def test_the_credits_heading_changes_the_about_page(self):
        self.open_page("about")
        self.open_section("credits-heading")
        input_sel = self.section("credits-heading") + "input[type=text]"
        self.assertEqual(self.p.js("document.querySelector('%s').placeholder" % input_sel),
                         "Production credits")
        self.p.js("typeInput('%s', 'Client list')" % input_sel)
        self.save_section("credits-heading")
        self.toast("Credits heading saved")
        self.assertEqual(self.state()["media"]["credits_note"], "Client list")
        self.assertIn("Client list", self.page_html("about.html"))

    # -- the Paid Social page ------------------------------------------------------------------
    def test_a_video_ad_is_added_saved_and_built_onto_the_paid_page(self):
        self.enable_paid_page()
        self.open_page("paid")
        self.open_section("video-ads")
        self.p.js("document.querySelector('%s[data-add]').click()" % self.section("video-ads"))
        self.p.js("typeInput('%s .rowx [data-f=title]', 'Cookie drop')" % self.section("video-ads"))
        self.p.js("typeInput('%s .rowx [data-f=campaign]', 'Spring')" % self.section("video-ads"))
        self.p.js("typeInput('%s .rowx [data-f=livid]', 'https://livid.com/watch/HGGzzr4EBITa')"
                  % self.section("video-ads"))
        self.assertTrue(self.dirty())
        self.save_section("video-ads")
        self.toast("Video ads saved")
        ads = self.state()["media"]["paid_social"]["video_ads"]
        self.assertEqual([a["title"] for a in ads], ["Cookie drop"])
        self.assertEqual(ads[0]["ratio"], "9 / 16")
        self.assertIn("livid.com/embed/HGGzzr4EBITa", ads[0]["livid"])
        paid = self.page_html("paid-social.html")
        self.assertIn("Cookie drop", paid)
        self.assertIn("livid.com/embed/HGGzzr4EBITa", paid)

    def test_a_static_ad_uploads_its_image_and_saves(self):
        self.enable_paid_page()
        self.open_page("paid")
        self.open_section("static-ads")
        self.p.js("document.querySelector('%s[data-add]').click()" % self.section("static-ads"))
        image_input = self.section("static-ads") + ".adrow .acell-img input[type=file]"
        self.p.js("""(async () => {
          const c = document.createElement('canvas'); c.width = 1080; c.height = 1080;
          const x = c.getContext('2d'); x.fillStyle = '#c40'; x.fillRect(0, 0, 1080, 1080);
          const blob = await new Promise(r => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'Paid Still.png', {type: 'image/png'}));
          const input = document.querySelector('%s');
          input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
        })()""" % image_input)
        self.toast("Image uploaded", 90)
        path = self.p.js("document.querySelector('%s').dataset.image" % (self.section("static-ads") + ".adrow"))
        self.assertEqual(path, "assets/img/posters/paid/paid-still.jpg")
        self.assertTrue((self.tmp / path).is_file())
        self.p.js("typeInput('%s .adrow [data-f=title]', 'End of summer')" % self.section("static-ads"))
        self.save_section("static-ads")
        self.toast("Static ads saved")
        ads = self.state()["media"]["paid_social"]["static_ads"]
        self.assertEqual([a["title"] for a in ads], ["End of summer"])
        self.assertEqual((ads[0]["image"], ads[0]["ratio"]), (path, "1 / 1"))
        paid = self.page_html("paid-social.html")
        self.assertIn("End of summer", paid)
        self.assertIn(path, paid)

    def test_the_paid_intro_is_its_own_section(self):
        self.open_page("paid")
        self.open_section("paid-intro")
        self.p.js("typeInput('%s textarea', 'Paid social, end to end.')" % self.section("paid-intro"))
        self.save_section("paid-intro")
        self.toast("Paid Social intro saved")
        self.assertEqual(self.state()["media"]["paid_social"]["intro"], "Paid social, end to end.")
        self.assertEqual(self.data("media.json")["paid_social"]["video_ads"], [])

    # -- the project pane ----------------------------------------------------------------------
    def test_the_project_pane_carries_the_ai_steps_and_demo_fields(self):
        self.p.js("(()=>{const li=[...document.querySelectorAll('#proj-list li.proj')]"
                  ".find(x => /Rodowita/.test(x.textContent)); li.click();})()")
        self.wait("!!document.querySelector('#pane [data-k=\"ai_steps\"]')")
        labels = self.p.js("[...document.querySelectorAll('#pane .grid .field > span')].map(x => x.textContent)")
        self.assertIn("AI steps line (AI page “Ran live” card)", labels)
        self.assertIn("Demo line (AI feature block)", labels)
        self.assertEqual(self.p.js("document.querySelector('#pane [data-k=\"ai_steps\"]').value"),
                         "Real shot, AI step, final.")
        self.p.js("typeInput('#pane [data-k=\"ai_steps\"]', 'Shot, prompted, finished.')")
        self.p.js("typeInput('#pane [data-k=\"demo\"]', 'Demo reel under the feature.')")
        self.p.js("document.querySelector('#toast').hidden = true")
        self.p.js("document.querySelector('#save-proj').click()")
        self.toast("Project saved")
        p = next(x for x in self.state()["projects"] if x["slug"] == "rodowita-water")
        self.assertEqual(p["ai_steps"], "Shot, prompted, finished.")
        self.assertEqual(p["demo"], "Demo reel under the feature.")
        self.assertIn("Shot, prompted, finished.", self.page_html("ai.html"))


if __name__ == "__main__":
    unittest.main()
