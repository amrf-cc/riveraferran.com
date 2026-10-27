"""The comparison-set editor, driven in a real (headless Chrome) browser against the synthetic site.

What the owner does with it: open the project, open the section, add and reorder a step, delete a
set, replace a picture, save. Every test also asserts the page logged no console errors. Skipped
when Chrome or websocket-client is missing; the upload test needs macOS sips.
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
ROD = "rodowita-water"
SETS = "document.querySelector('details.sets')"


@unittest.skipUnless(cdp.available(), "needs Chrome and websocket-client")
class TestSetsUi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-ui-sets-"))
        F.make_site(cls.tmp)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.base = "http://127.0.0.1:%d/" % cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.browser = cdp.Browser(1440, 1600).__enter__()

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
        self.open_project(ROD)

    def tearDown(self):
        self.p.pump(0.2)
        errors = list(self.p.errors)
        self.p.js("dirty = false; dirtyLists.clear()")
        self.p.close()
        self.assertEqual(errors, [], "the page logged errors")

    # -- helpers ------------------------------------------------------------------------------
    def wait(self, expr, timeout=30):
        return self.p.wait(expr, timeout)

    def toast(self, pattern, timeout=60):
        self.wait("(()=>{const t=document.querySelector('#toast');return !!t && !t.hidden && /%s/.test(t.textContent)})()"
                  % pattern, timeout)
        return self.p.js("document.querySelector('#toast').textContent")

    def open_project(self, slug):
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"%s\"]').closest('li').click()" % slug)
        self.wait("(document.querySelector('#pane .slug')||{textContent:''}).textContent.startsWith('%s')" % slug)

    def open_sets(self):
        self.p.js("%s.open = true" % SETS)
        self.wait("document.querySelectorAll('.set-card, [data-sets-empty]').length > 0")

    def sets(self, slug=ROD):
        data = json.loads((self.tmp / "assets-manifest.json").read_text(encoding="utf-8"))
        return data["projects"][slug].get("sets") or []

    def labels(self):
        return [st["label"] for s in self.sets() for st in s["steps"]]

    def dirty(self):
        return "Unsaved changes" in self.p.js("document.querySelector('#status').textContent")

    def save(self):
        self.p.js("document.querySelector('.js-save[data-scope=\"sets\"]').click()")
        return self.toast("Comparison sets saved")

    # -- the section --------------------------------------------------------------------------
    def test_the_section_shows_the_stored_set_and_says_where_it_appears(self):
        self.assertEqual(self.p.js("%s.querySelector('summary .sets-head').textContent" % SETS),
                         "Real shot → AI step → final")
        self.assertEqual(self.p.js("%s.querySelector('summary .count').textContent" % SETS), "1")
        self.assertFalse(self.p.js("%s.open" % SETS), "collapsed until the owner opens it")
        self.open_sets()
        self.assertEqual(self.p.js("[...document.querySelectorAll('.set-step [data-f=\"label\"]')].map(i=>i.value)"),
                         ["Studio shot", "Final"])
        self.assertEqual(self.p.js("document.querySelector('.set-card [data-f=\"title\"]').value"),
                         "Studio shot → final")
        self.assertEqual(self.p.js("document.querySelectorAll('.set-step [data-f=\"ai\"]')[1].checked"), True)
        self.assertEqual(self.p.js("document.querySelectorAll('.set-step [data-f=\"ai\"]')[0].checked"), False)
        self.assertIn("work/rodowita-water.html, ai.html",
                      self.p.js("document.querySelector('[data-shown-on]').textContent"))
        self.assertEqual(self.p.js("document.querySelectorAll('.set-step img[src]').length"), 2,
                         "both steps preview their image")
        self.assertFalse(self.p.js("!!document.querySelector('[data-sets-warn]')"))
        self.assertTrue(self.p.js("document.querySelector('[data-pick]').textContent") == "Replace image…")

    def test_a_project_without_sets_shows_the_empty_state(self):
        self.p.js("document.querySelector('#proj-list [data-vis-slug=\"beta\"]').closest('li').click()")
        self.wait("document.querySelector('#pane h1').textContent === 'Beta'")
        self.open_sets()
        self.assertIn("No comparison sets.", self.p.js("document.querySelector('[data-sets-empty]').textContent"))
        self.assertEqual(self.p.js("%s.querySelector('summary .count').textContent" % SETS), "0")
        self.p.js("document.querySelector('[data-add-set]').click()")
        self.assertEqual(self.p.js("document.querySelectorAll('.set-card').length"), 1)
        self.assertTrue(self.dirty())

    # -- editing ------------------------------------------------------------------------------
    def test_a_step_is_added_warned_about_and_saved(self):
        self.open_sets()
        self.p.js("document.querySelector('.set-card [data-add-step]').click()")
        self.assertTrue(self.dirty())
        self.assertEqual(self.p.js("document.querySelectorAll('.set-step').length"), 3)
        self.assertIn("1 step needs an image", self.p.js("document.querySelector('[data-sets-warn]').textContent"))
        self.assertIn("publishing is blocked",
                      self.p.js("document.querySelector('[data-no-image]').textContent"))
        self.p.js("""(() => { const s = [...document.querySelectorAll('.set-step')].pop();
          setInput('[data-f="label"]', 'AI pass', s);
          s.querySelector('[data-f="ai"]').click(); })()""")
        self.assertEqual(len(self.sets()[0]["steps"]), 2, "nothing is written until Save")
        self.save()
        steps = self.sets()[0]["steps"]
        self.assertEqual(len(steps), 3)
        self.assertEqual((steps[2]["label"], steps[2]["ai"], steps[2]["image"]), ("AI pass", True, ""))

    def test_a_step_reorders_by_drag_and_keeps_its_badge(self):
        self.open_sets()
        self.p.js("(() => { const s = [...document.querySelectorAll('.set-step')]; window.__dnd(s[1], s[0], false); })()")
        self.assertTrue(self.dirty())
        self.assertEqual(self.p.js("[...document.querySelectorAll('.set-step [data-f=\"label\"]')].map(i=>i.value)"),
                         ["Final", "Studio shot"])
        self.save()
        self.assertEqual(self.labels(), ["Final", "Studio shot"])
        self.assertIs(self.sets()[0]["steps"][0]["ai"], True, "the step keeps its badge through the move")

    def test_the_arrows_also_move_a_step(self):
        self.open_sets()
        self.p.js("[...document.querySelectorAll('.set-step')][1].querySelector('[data-move=\"-\"]').click()")
        self.assertEqual(self.p.js("[...document.querySelectorAll('.set-step [data-f=\"label\"]')].map(i=>i.value)"),
                         ["Final", "Studio shot"])
        self.save()
        self.assertEqual(self.labels(), ["Final", "Studio shot"])

    def test_sets_reorder_by_drag(self):
        self.open_sets()
        self.p.js("document.querySelector('[data-add-set]').click()")
        self.p.js("(() => { const c = [...document.querySelectorAll('.set-card')].pop(); setInput('[data-f=\"title\"]', 'Second set', c); })()")
        self.p.js("(() => { const c = [...document.querySelectorAll('.set-card')]; window.__dnd(c[1], c[0], false); })()")
        self.save()
        self.assertEqual([s["title"] for s in self.sets()], ["Second set", "Studio shot → final"])

    def test_a_set_is_deleted_only_after_a_confirm(self):
        self.open_sets()
        self.p.js("window.confirm = () => false")
        self.p.js("document.querySelector('[data-drop-set]').click()")
        self.assertEqual(self.p.js("document.querySelectorAll('.set-card').length"), 1)
        self.p.js("window.confirm = () => true")
        self.p.js("document.querySelector('[data-drop-set]').click()")
        self.assertEqual(self.p.js("document.querySelectorAll('.set-card').length"), 0)
        self.assertTrue(self.p.js("!!document.querySelector('[data-sets-empty]')"))
        self.save()
        self.assertEqual(self.sets(), [])

    def test_typing_marks_unsaved_and_only_the_save_button_writes(self):
        before = self.sets()
        others = {n: (self.tmp / n).read_bytes() for n in ("projects.json", "media.json", "site-config.json")}
        self.open_sets()
        self.p.js("setInput('.set-card [data-f=\"title\"]', 'Changed title')")
        self.assertTrue(self.dirty())
        self.assertFalse(self.p.js("document.querySelector('.sets .vids-note').hidden"),
                         "the section says it has unsaved changes")
        self.assertEqual(self.sets(), before, "typing alone writes nothing")
        self.save()
        self.assertEqual(self.sets()[0]["title"], "Changed title")
        self.assertFalse(self.dirty())
        for name, data in others.items():
            self.assertEqual((self.tmp / name).read_bytes(), data, name)

    def test_the_save_button_is_disabled_at_six_steps(self):
        self.open_sets()
        for _ in range(4):
            self.p.js("document.querySelector('.set-card [data-add-step]').click()")
        self.assertEqual(self.p.js("document.querySelectorAll('.set-step').length"), 6)
        self.assertTrue(self.p.js("document.querySelector('.set-card [data-add-step]').disabled"))
        self.assertEqual(self.p.js("document.querySelector('.set-card [data-add-step]').title"),
                         "Six steps is the most a row shows")

    @unittest.skipUnless(HAS_SIPS, "needs macOS sips")
    def test_a_replaced_image_uploads_and_the_old_file_is_moved(self):
        self.open_sets()
        self.p.js("""(async () => {
          const c = document.createElement('canvas'); c.width = 1200; c.height = 900;
          const x = c.getContext('2d'); x.fillStyle = '#2a8'; x.fillRect(0, 0, 1200, 900);
          const blob = await new Promise(r => c.toBlob(r, 'image/png'));
          const dt = new DataTransfer(); dt.items.add(new File([blob], 'UI Step.png', {type: 'image/png'}));
          const input = [...document.querySelectorAll('.set-step')][1].querySelector('input[type=file]');
          input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
        })()""")
        self.toast("Image uploaded", 90)
        self.assertTrue(self.dirty())
        self.assertIn("ui-step.jpg", self.p.js("document.querySelectorAll('.set-step img')[1].src"))
        toast = self.save()
        self.assertIn("1 replaced file moved to tools/removed-media", toast)
        steps = self.sets()[0]["steps"]
        self.assertEqual(steps[1]["image"], "assets/img/rodowita-water/ui-step.jpg")
        self.assertTrue((self.tmp / steps[1]["image"]).is_file())
        self.assertFalse((self.tmp / "assets/img/rodowita-water/02.jpg").exists(),
                         "the replaced file left assets/ for tools/removed-media")
        self.assertTrue((self.tmp / "tools" / "removed-media").is_dir())


if __name__ == "__main__":
    unittest.main()
