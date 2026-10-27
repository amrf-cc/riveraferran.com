"""Exploratory pass over the whole editor UI, driven through headless Chrome against one server.

    python3 tests/ui_walk.py http://127.0.0.1:8821/ [sandbox-repo-path]

Prints PASS/FAIL per check. Not part of the unit suite (see test_cms_ui.py for that); this is what I run
against a clone of the real site to see the UI behave on real data.
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cdp  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8821/"
REPO = sys.argv[2] if len(sys.argv) > 2 else None
QA = os.path.expanduser("~/.hermes/cache/scratch/qa")
RESULTS = []

DND = """
window.__dnd = (src, dst, mime, after) => {
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
"""


def state():
    return json.load(urllib.request.urlopen(BASE + "api/state"))


def check(label, cond, extra=None):
    RESULTS.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + label + ((" | " + str(extra)) if extra not in (None, "") else ""), flush=True)
    return cond


def toast(p, pattern, timeout=40):
    p.wait("(()=>{const t=document.querySelector('#toast');return !!t && !t.hidden && /%s/.test(t.textContent)})()" % pattern,
           timeout=timeout, what="toast /%s/" % pattern)
    return p.js("document.querySelector('#toast').textContent")


def open_pane(p, index):
    p.js("document.querySelectorAll('#site-list li')[%d].click()" % index)
    time.sleep(0.9)


def open_project(p, slug):
    p.js("(()=>{const e=document.querySelector('#proj-list [data-vis-slug=\"%s\"]'); e.closest('li').click();})()" % slug)
    p.wait("document.querySelector('#pane .slug') && document.querySelector('#pane .slug').textContent.startsWith('%s')" % slug)
    time.sleep(0.5)


with cdp.Browser(1440, 900) as b:
    p = b.open(BASE)
    p.wait("document.querySelectorAll('#proj-list li.proj').length > 0", what="sidebar")
    time.sleep(0.6)
    p.js(DND)

    # ---- A. drag a project inside its group: the group reorders, other groups do not move
    print("\n== A. sidebar drag-to-order")
    before = [x["slug"] for x in state()["projects"]]
    rows = p.js("[...document.querySelectorAll('#proj-list li.proj')].slice(0,3).map(r=>r.querySelector('[data-vis-slug]').dataset.visSlug)")
    p.js("(()=>{const li=[...document.querySelectorAll('#proj-list li.proj')];window.__dnd(li[2], li[0], null, false)})()")
    toast(p, "Project order saved")
    after = [x["slug"] for x in state()["projects"]]
    group = set(rows) | set(p.js("[...document.querySelectorAll('#proj-list li.proj')].slice(0,11).map(r=>r.querySelector('[data-vis-slug]').dataset.visSlug)"))
    check("the dragged project now comes first in its group", after.index(rows[2]) < after.index(rows[0]), (rows, after[:6]))
    outside_before = [s for s in before if s not in group]
    outside_after = [s for s in after if s not in group]
    check("projects outside the group kept their order", outside_before == outside_after)
    check("no project lost or duplicated", sorted(before) == sorted(after) and len(after) == len(before))
    p.js("(()=>{const li=[...document.querySelectorAll('#proj-list li.proj')];window.__dnd(li[0], li[2], null, true)})()")
    time.sleep(1.5)

    # ---- B. filter and the Live/Hidden control
    print("\n== B. filter")
    p.js("(()=>{const q=document.querySelector('#q'); q.value='wisin'; q.dispatchEvent(new Event('input',{bubbles:true}))})()")
    check("filter narrows the list", p.js("document.querySelectorAll('#proj-list li.proj').length") == 1)
    check("drag is switched off while filtering", p.js("document.querySelector('#proj-list li.proj').getAttribute('draggable')") is None)
    p.js("(()=>{const q=document.querySelector('#q'); q.value=''; q.dispatchEvent(new Event('input',{bubbles:true}))})()")
    p.js("document.querySelector('#seg [data-seg=hidden]').click()")
    check("Hidden shows only hidden projects", p.js("[...document.querySelectorAll('#proj-list li.proj')].every(r=>r.classList.contains('is-hidden'))"))
    p.js("document.querySelector('#seg [data-seg=all]').click()")

    # ---- C. save a project's fields
    print("\n== C. project form")
    open_project(p, "chef-mia-castro")
    p.js("(()=>{const i=document.querySelector('[data-k=result]'); i.value='Sold out in a week.'; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    check("form edit shows Unsaved changes", "Unsaved changes" in p.js("document.querySelector('#status').textContent"))
    p.js("document.querySelector('#save-proj').click()")
    toast(p, "Project saved")
    check("result reached the server", [x for x in state()["projects"] if x["slug"] == "chef-mia-castro"][0]["result"] == "Sold out in a week.")
    check("pane is clean again", "Saved" in p.js("document.querySelector('#status').textContent") and p.js("document.querySelector('#pane h1').textContent") == "Chef Mia Castro")
    p.js("(()=>{const i=document.querySelector('[data-k=bts]'); i.value='On set.'; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    p.js("window.dispatchEvent(new Event('x'));document.dispatchEvent(new KeyboardEvent('keydown',{key:'s',metaKey:true,bubbles:true}))")
    toast(p, "Project saved")
    check("Cmd+S saves", [x for x in state()["projects"] if x["slug"] == "chef-mia-castro"][0].get("bts") == "On set.")
    p.js("document.querySelector('[data-tag=motion]').click()")
    p.js("document.querySelector('#save-proj').click()")
    toast(p, "Project saved")
    check("a skill chip toggles and saves", "motion" in [x for x in state()["projects"] if x["slug"] == "chef-mia-castro"][0]["tags"])

    # ---- D. photographs
    print("\n== D. photographs")
    n0 = len(state()["manifest"]["projects"]["chef-mia-castro"]["photos"])
    full0 = [ph["full"] for ph in state()["manifest"]["projects"]["chef-mia-castro"]["photos"]]
    check("photo grid shows every photo", p.js("document.querySelectorAll('.shot').length") == n0, n0)
    check("first photo carries the COVER badge", p.js("document.querySelector('.shot .cover')?.textContent") == "COVER")
    p.js("(()=>{const a=document.querySelectorAll('.shot .alt')[0]; a.value='Alt from the UI'; a.dispatchEvent(new Event('change',{bubbles:true}))})()")
    toast(p, "Alt text saved")
    check("alt text saved", state()["manifest"]["projects"]["chef-mia-castro"]["photos"][0]["alt"] == "Alt from the UI")
    p.js("(()=>{const s=document.querySelectorAll('.shot');window.__dnd(s[4], s[0], 'application/x-site-photo', false)})()")
    toast(p, "Order saved")
    full1 = [ph["full"] for ph in state()["manifest"]["projects"]["chef-mia-castro"]["photos"]]
    check("dragging a photo onto the first slot makes it the cover", full1[0] == full0[4] and sorted(full1) == sorted(full0))
    p.wait("document.querySelectorAll('.shot')[0].querySelector('img').src.length > 0")
    p.js("""(async () => {
      const c = document.createElement('canvas'); c.width = 3000; c.height = 2000;
      const x = c.getContext('2d'); x.fillStyle = '#2a8'; x.fillRect(0, 0, 3000, 2000); x.fillStyle = '#fff'; x.fillRect(100, 100, 400, 400);
      const blob = await new Promise(r => c.toBlob(r, 'image/png'));
      const dt = new DataTransfer(); dt.items.add(new File([blob], 'UI Test Shot.png', {type: 'image/png'}));
      const input = document.querySelector('#photo-section input[type=file]');
      input.files = dt.files; input.dispatchEvent(new Event('change', {bubbles: true}));
    })()""")
    toast(p, "1 photograph added", 90)
    photos = state()["manifest"]["projects"]["chef-mia-castro"]["photos"]
    check("upload added a photo at the end", len(photos) == n0 + 1 and photos[-1]["full"].endswith("ui-test-shot.jpg"), photos[-1]["full"])
    if REPO:
        def dims(rel):
            o = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", os.path.join(str(REPO), rel)], capture_output=True, text=True).stdout
            return tuple(int(x.split(":")[1]) for x in o.splitlines() if "pixel" in x)
        check("full is 2000px long edge, thumb 1280", max(dims(photos[-1]["full"])) == 2000 and max(dims(photos[-1]["thumb"])) == 1280, (dims(photos[-1]["full"]), dims(photos[-1]["thumb"])))
    # thumbnails are lazy-loaded, so scroll the new one into view before asking whether it painted
    p.js("[...document.querySelectorAll('.shot img')].pop().scrollIntoView({block:'center'})")
    check("the new thumbnail renders in the grid", p.wait("(()=>{const i=[...document.querySelectorAll('.shot img')].pop();return i.complete && i.naturalWidth>0})()", 20))
    p.js("[...document.querySelectorAll('.shot .del')].pop().click()")
    toast(p, "Photograph removed")
    check("remove took the photo out", len(state()["manifest"]["projects"]["chef-mia-castro"]["photos"]) == n0)
    p.js("(()=>{const s=document.querySelectorAll('.shot');window.__dnd(s[0], s[4], 'application/x-site-photo', false)})()")
    toast(p, "Order saved")

    # ---- E. videos
    print("\n== E. video list")
    v0 = len(state()["manifest"]["projects"]["chef-mia-castro"]["videos"])
    check("video rows match the manifest", p.js("document.querySelectorAll('#photo-section ~ section .rowx').length") == v0, v0)
    p.js("document.querySelector('#photo-section ~ section [data-add]').click()")
    p.js("""(()=>{const r=[...document.querySelectorAll('#photo-section ~ section .rowx')].pop();
      const t=r.querySelector('[data-f=title]'); t.value='UI test video'; t.dispatchEvent(new Event('input',{bubbles:true}));
      const l=r.querySelector('[data-f=livid]'); l.value='https://livid.com/watch/UITESTVID12'; l.dispatchEvent(new Event('input',{bubbles:true}));})()""")
    check("adding a row marks the list dirty", "Unsaved changes" in p.js("document.querySelector('#status').textContent"))
    p.js("document.querySelector('#photo-section ~ section .js-save').click()")
    toast(p, "Video list saved")
    vids = state()["manifest"]["projects"]["chef-mia-castro"]["videos"]
    check("the new video was saved and normalised to an embed", len(vids) == v0 + 1 and vids[-1]["livid"] == "https://livid.com/embed/UITESTVID12?livid_logo=0&fullscreen=1&title=1", vids[-1]["livid"])
    check("existing videos kept their posters", all(v.get("poster") for v in vids[:v0] if v.get("poster") is not None))
    p.js("[...document.querySelectorAll('#photo-section ~ section .rowx')].pop().querySelector('.del').click()")
    p.js("document.querySelector('#photo-section ~ section .js-save').click()")
    toast(p, "Video list saved")
    check("removing the row and saving restores the list", len(state()["manifest"]["projects"]["chef-mia-castro"]["videos"]) == v0)

    # ---- F. site settings
    print("\n== F. site settings")
    open_pane(p, 0)
    check("settings pane shows the headline", len(p.js("document.querySelector('[data-k=headline]').value")) > 5)
    old = state()["config"]["headline"]
    p.js("(()=>{const i=document.querySelector('[data-k=headline]'); i.value=i.value+' [ui]'; i.dispatchEvent(new Event('input',{bubbles:true}));})()")
    p.js("document.querySelector('#save-site').click()")
    toast(p, "Settings saved")
    check("headline saved", state()["config"]["headline"] == old + " [ui]")
    check("a draft banner did not appear (still a release)", state()["build"]["ready"])
    p.js("(()=>{const i=document.querySelector('[data-k=headline]'); i.value=%s; i.dispatchEvent(new Event('input',{bubbles:true}));})()" % json.dumps(old))
    p.js("document.querySelector('#save-site').click()")
    toast(p, "Settings saved")
    check("resume card lists the file", "Ferr" in p.js("document.querySelector('.res-file').textContent"), p.js("document.querySelector('.res-file').textContent"))

    # ---- G. visibility pane switches
    print("\n== G. visibility pane")
    open_pane(p, 1)
    p.js("document.querySelectorAll('.vcard .switch input')[0].click()")           # showreel on
    toast(p, "Showreel is on")
    cfg = state()
    check("showreel switch writes the config", "hidden" not in cfg["config"]["showreel"])
    check("and the release check now says draft (empty reel links)", cfg["build"]["ready"] is False and "Draft" in p.js("document.querySelector('#status').textContent"))
    p.js("document.querySelectorAll('.vcard .switch input')[0].click()")
    toast(p, "Showreel hidden")
    check("switching it off restores the release", state()["build"]["ready"])
    p.js("document.querySelectorAll('.vcard .switch input')[1].click()")           # paid social on
    toast(p, "Paid Social page is on")
    check("paid social page exists in the preview", urllib.request.urlopen(BASE + "preview/paid-social.html").status == 200)
    p.js("document.querySelectorAll('.vcard .switch input')[1].click()")
    toast(p, "Paid Social page hidden")
    try:
        urllib.request.urlopen(BASE + "preview/paid-social.html")
        check("paid social page is gone again", False)
    except Exception as e:
        check("paid social page is gone again", "404" in str(e))
    n_hidden = len([x for x in state()["projects"] if x.get("hidden")])
    p.js("document.querySelector('.vrow[data-vis-row=\"wisin\"] input').click()")
    toast(p, "Hidden: Wisin")
    check("the per-project switch hides it", len([x for x in state()["projects"] if x.get("hidden")]) == n_hidden + 1)
    check("row dims in the list", p.js("document.querySelector('.vrow[data-vis-row=\"wisin\"]').classList.contains('is-hidden')"))
    p.js("document.querySelector('.vrow[data-vis-row=\"wisin\"] input').click()")
    toast(p, "Visible again: Wisin")
    p.shot(os.path.join(QA, "ui-vis-after.png"))

    # ---- H. reels
    print("\n== H. reels")
    open_pane(p, 2)
    nreels = len(state()["media"]["reels"])
    p.js("document.querySelector('#rows-x, .rows-section [data-add]').click()")
    p.js("""(()=>{const sec=document.querySelectorAll('.rows-section')[0]; const r=[...sec.querySelectorAll('.rowx')].pop();
      const t=r.querySelector('[data-f=title]'); t.value='UI test reel'; t.dispatchEvent(new Event('input',{bubbles:true}));
      const l=r.querySelector('[data-f=livid]'); l.value='https://livid.com/watch/UIREELTEST1'; l.dispatchEvent(new Event('input',{bubbles:true}));
      const pr=r.querySelector('[data-f=project]'); pr.value='wisin'; pr.dispatchEvent(new Event('input',{bubbles:true}));})()""")
    p.js("document.querySelectorAll('.rows-section')[0].querySelector('.js-save').click()")
    toast(p, "Reels saved")
    reels = state()["media"]["reels"]
    check("reel added with a slug and its project link", len(reels) == nreels + 1 and reels[-1]["slug"] == "ui-test-reel" and reels[-1]["project"] == "wisin", reels[-1].get("slug"))
    check("older reels kept their poster and flags", all("poster" in r for r in reels))
    p.js("[...document.querySelectorAll('.rows-section')[0].querySelectorAll('.rowx')].pop().querySelector('.del').click()")
    p.js("document.querySelectorAll('.rows-section')[0].querySelector('.js-save').click()")
    toast(p, "Reels saved")
    check("reel removed again", len(state()["media"]["reels"]) == nreels)

    # ---- I. about and credits
    print("\n== I. about & credits")
    open_pane(p, 3)
    ncred = len(state()["media"]["credits"])
    check("credits rows rendered", p.js("document.querySelectorAll('.rows-section')[1].querySelectorAll('.rowx').length") == ncred, ncred)
    check("the long role is not clipped to nothing", p.js("document.querySelectorAll('.rows-section')[1].querySelector('[data-f=role]').getBoundingClientRect().width") > 250)

    # ---- J. new project, then delete it
    print("\n== J. new project and delete")
    p.js("document.querySelector('#btn-new').click()")
    p.wait("document.querySelector('#pane h1').textContent === 'New project'")
    p.js("""(()=>{const set=(k,v)=>{const i=document.querySelector('[data-k='+k+']'); i.value=v; i.dispatchEvent(new Event('input',{bubbles:true}));};
      set('client','UI Test Client'); set('title','UI test project'); set('brief','Made by the UI test.');})()""")
    p.js("document.querySelector('#save-proj').click()")
    toast(p, "Project created")
    check("new project exists with a manifest entry", "ui-test-client-ui-test-project" in state()["manifest"]["projects"])
    check("and it is selected with photo and video sections", p.js("!!document.querySelector('#photo-section') && !!document.querySelector('.rows-section')"))
    p.js("document.querySelector('#del-proj').click()")
    toast(p, "Project deleted")
    check("delete removed it", "ui-test-client-ui-test-project" not in [x["slug"] for x in state()["projects"]])

    # ---- K. the sheets
    print("\n== K. release check and rebuild")
    p.js("document.querySelector('#btn-release').click()")
    p.wait("!document.querySelector('#sheet').hidden")
    check("release check says release-ready", "Release-ready" in p.js("document.querySelector('#sheet-body').textContent"))
    p.js("document.querySelector('#sheet-close').click()")
    p.js("document.querySelector('#btn-build').click()")
    p.wait("!document.querySelector('#sheet').hidden && /BUILD_OK/.test(document.querySelector('#sheet-body').textContent)", 60)
    check("rebuild shows the build log", True)
    p.js("document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))")
    check("Escape closes the sheet", p.js("document.querySelector('#sheet').hidden"))

    p.pump(0.5)
    print("\nconsole errors:", p.errors or "none")
    check("no console errors", not p.errors, p.errors)

print("\n%d/%d checks passed" % (sum(RESULTS), len(RESULTS)))
sys.exit(0 if all(RESULTS) else 1)
