#!/usr/bin/env python3
"""Site Manager for the brand portfolio (127.0.0.1 only, Python standard library only).

    env -u PYTHONPATH /usr/bin/python3 tools/cms.py [port]      # default 8820

The same tool as the riveraferran.com Site Manager, for this site: a sidebar of projects (drag to
reorder, eye to hide), panes for site settings, visibility, reels and about/credits, photo and
video editors, Preview, Rebuild and a release check. The UI lives in tools/cms/.

Every change goes through one path: take the data lock, refuse a stale page (409), back up the
files it touches (tools/backups, newest 60 per file), write, rebuild, and put the backups back if
the build fails. A write that would not change a byte is skipped. A site with nothing left to
resolve is rebuilt as a RELEASE, anything else as a draft. Removed media goes to
tools/removed-media and removed projects to tools/removed-projects, never to the void.

API (JSON unless noted). Mutating calls carry the `version` the page was loaded with.
    GET  /api/state                          everything the UI draws
    GET  /api/release                        the release check: blockers, or ready
    GET  /api/livid?url=                     normalise a Livid link and say whether it plays
    POST /api/build                          rebuild, return the log
    POST /api/projects                       create or update one project
    POST /api/projects/order                 {slugs}: the whole list, in the new order
    POST /api/projects/<slug>/visibility     {hidden}: show or hide a project
    DELETE /api/projects/<slug>              archive the project, trash its files
    POST /api/media                          photos: add / remove / reorder / update
    PUT  /api/project/<slug>/videos          the whole video list, in order
    PUT  /api/project/<slug>/sets            the whole comparison-set list, in order
    POST /api/upload?slug=&kind=&scope=&name=    raw image bytes -> derivatives
    POST /api/config                         site settings, showreel, hidden pages, AI page cards
    PUT  /api/lists/<name>                   reels, motion, credits, experience, side_projects
    PUT  /api/resume?name=                   raw PDF bytes; ligatures are repaired on the way in
    GET  /api/publish                        can this copy go live? remote, branch, ahead/behind, dirty, ...
    POST /api/publish                        {message?, version}: start ONE background publish job -> {job}
    GET  /api/publish/job                    {state: idle|running|done|failed, step, log, error, commit, url}
    GET  /preview/...                        the built site (pages and assets only)

Publish commits everything and pushes `HEAD:main` to the origin remote, which puts the site live (GitHub
Pages). It never stashes, merges, resets, rebases or forces, and it refuses when GitHub has newer commits,
when the site is not release-ready, when the unit tests fail, or when a secret-looking file or a blob of
50 MB or more is staged. Credentials only come from the Mac's git credential helper and are scrubbed from
every log and every response.
"""
import hashlib
import importlib.util
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NoReturn
from urllib.parse import parse_qs, unquote, urlparse

REPO = Path(os.environ.get("CMS_REPO", Path(__file__).resolve().parent.parent)).resolve()
TOOLS = Path(__file__).resolve().parent
# Feature modules sit next to cms.py and own one route each. cms.py is loaded by path in the tests,
# so its own directory has to be importable before the first sibling import below.
_TOOLS_DIR = str(Path(__file__).resolve().parent)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)   # cms.py is loaded by path in tests, so sibling imports need this

import cms_sets  # noqa: E402  (needs _TOOLS_DIR on the path first)
EXTENSIONS = (cms_sets,)  # noqa: E402  one module per feature; each owns one route

UI_DIR = TOOLS / "cms"
BACKUPS = REPO / "tools" / "backups"
TRASH = REPO / "tools" / "removed-media"
ARCHIVE = REPO / "tools" / "removed-projects"
PORT = int(os.environ.get("CMS_PORT", 8820))           # `cms.py <port>` overrides it

FILES = {"projects": REPO / "projects.json", "config": REPO / "site-config.json",
         "manifest": REPO / "assets-manifest.json", "media": REPO / "media.json"}
DATA_NAMES = ("projects.json", "site-config.json", "assets-manifest.json", "media.json")

KEEP_BACKUPS = 60
JSON_CAP = 2_000_000
UPLOAD_CAP = 120 * 1024 * 1024
# The same pipeline as the existing derivatives: full 2000px at quality 72, thumbs 1280px.
FULL_EDGE, FULL_Q = 2000, 72
THUMB_EDGE, THUMB_Q = 1280, 55
POSTER_Q = 70
POSTER_EDGE = {"project": 1920, "showreel": 1920, "reel": 1280, "motion": 1280,
               "set": 1920,
               "hero": 2400,
               "cutout": 2000}
POSTER_DIR = {"reel": "posters/reels", "motion": "posters/motion", "showreel": "posters/showreel",
              "hero": "masthead",
              "cutout": "masthead"}
# The share card is the one image whose exact size the page promises (og:image:width/height), so the
# pipeline crops it instead of resizing it: 1200x630, quality 82, centre-cropped.
SHARE_W, SHARE_H, SHARE_Q = 1200, 630, 82
SHARE_MIN = (600, 315)
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff", ".gif")
RATIOS = ("9 / 16", "16 / 9", "4 / 5", "1 / 1")

SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LIVID_ID = re.compile(r"^[A-Za-z0-9_-]{6,32}$")
LOCK = threading.RLock()


class ApiError(ValueError):
    """A refused request. A ValueError, so plain validation helpers can be used on their own."""

    def __init__(self, status, message, **extra):
        ValueError.__init__(self, message)
        self.status, self.message, self.extra = status, message, extra


def bad(message, status=400, **extra) -> NoReturn:
    raise ApiError(status, message, **extra)


# --------------------------------------------------------------------------- small helpers
def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def dump(data):
    """The one serialisation every data file uses. Idempotent: load then dump is byte-identical."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def atomic_write(path, text):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def slugify(text):
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")[:40]


def unique_slug(base, taken):
    base = base or "item"
    slug, n = base, 1
    while slug in taken:
        n += 1
        slug = "%s-%d" % (base, n)
    return slug


def stamp():
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


def backup(path):
    """Copy a file into tools/backups under a unique, sortable name; keep the newest KEEP_BACKUPS."""
    path = Path(path)
    BACKUPS.mkdir(parents=True, exist_ok=True)
    dest = BACKUPS / ("%s-%s%s" % (path.stem, stamp(), path.suffix))
    shutil.copy2(path, dest)
    old = sorted(BACKUPS.glob("%s-*%s" % (path.stem, path.suffix)))
    for f in old[:-KEEP_BACKUPS]:
        f.unlink(missing_ok=True)
    return dest


def data_version():
    """Fingerprint of the four data files. A write must have been made from this state."""
    h = hashlib.sha1()
    for name in DATA_NAMES:
        f = REPO / name
        h.update(name.encode("utf-8"))
        h.update(f.read_bytes() if f.exists() else b"")
    return h.hexdigest()[:16]


def file_token(rel):
    """Cache-busting token for a repo file (inode + mtime): it changes whenever other bytes land there."""
    try:
        st = (REPO / rel).stat()
    except (OSError, ValueError):
        return ""
    return "%x%x" % (st.st_ino, st.st_mtime_ns)


_SITE_BUILD = None


def site_build():
    """The repo's own build.py, loaded once, for its constants (AI_FEATURE, AI_LIVE, ...)."""
    global _SITE_BUILD
    if _SITE_BUILD is None:
        spec = importlib.util.spec_from_file_location("site_build_for_cms", str(REPO / "build.py"))
        assert spec is not None and spec.loader is not None, "build.py is missing"
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SITE_BUILD = mod
    return _SITE_BUILD


def safe_asset(rel):
    """Resolve a repo-relative asset path, refusing anything outside assets/."""
    if not isinstance(rel, str) or not rel or ".." in Path(rel).parts:
        bad("Bad path.")
    p = (REPO / rel).resolve()
    if not str(p).startswith(str((REPO / "assets").resolve()) + os.sep):
        bad("Path outside assets/.")
    return p


def existing_asset(rel, what="File"):
    p = safe_asset(rel)
    if not p.is_file():
        bad("%s %s does not exist." % (what, rel))
    return rel


# --------------------------------------------------------------------------- build + release state
BUILD = {"last_run": "", "ok": None, "ready": None, "blockers": None, "items": [], "log": "", "version": ""}
ITEM = re.compile(r"^  (\S+) \u2192 (.+?) \u2192 (.*)$")


def _build_py(*args):
    return subprocess.run([sys.executable, "build.py"] + list(args), cwd=str(REPO),
                          capture_output=True, text=True, timeout=300)


def _items(stdout):
    out = []
    for line in stdout.splitlines():
        m = ITEM.match(line)
        if m:
            out.append({"file": m.group(1), "path": m.group(2), "value": m.group(3)})
    return out


def _cap(text, n=20000):
    return text if len(text) <= n else text[:n] + "\n... (log cut)"


def run_build():
    """Release-first build: a clean site is written as a release, one with placeholders as a draft."""
    r = _build_py("--release")
    if r.returncode == 0:
        res = {"ok": True, "ready": True, "items": [], "log": _cap(r.stdout + r.stderr)}
    elif "RELEASE BLOCKED" in r.stdout:
        d = _build_py()
        res = {"ok": d.returncode == 0, "ready": False, "items": _items(r.stdout),
               "log": _cap(d.stdout + d.stderr)}
    else:
        res = {"ok": False, "ready": False, "items": [], "log": _cap(r.stdout + r.stderr)}
    res["blockers"] = 0 if res["ready"] else len(res["items"])
    res.update(last_run=time.strftime("%Y-%m-%d %H:%M:%S"), version=data_version())
    BUILD.update(res)
    return dict(BUILD)


def release_status():
    """The release gate for the current data, cached by data version (--check writes nothing)."""
    if BUILD["ready"] is not None and BUILD["version"] == data_version():
        return BUILD
    r = _build_py("--check")
    ready = r.returncode == 0
    items = [] if ready else _items(r.stdout)
    if not BUILD["last_run"]:
        idx = REPO / "index.html"
        if idx.exists():
            BUILD["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(idx.stat().st_mtime))
    BUILD.update(ready=ready, items=items, blockers=len(items), version=data_version())
    if BUILD["ok"] is None:
        BUILD["ok"] = True
    return BUILD


def build_summary():
    b = release_status()
    return {"ok": b["ok"] is not False, "ready": b["ready"], "blockers": b["blockers"], "last_run": b["last_run"]}


def commit(changes):
    """Write {Path: data} to disk and rebuild; on any failure restore every touched file.

    Files whose serialisation does not change are left alone. Returns (changed, build)."""
    todo = {}
    for path, data in changes.items():
        text = dump(data)
        if Path(path).read_text(encoding="utf-8") != text:
            todo[Path(path)] = text
    if not todo:
        return False, dict(release_status())
    saved = {p: backup(p) for p in todo}
    try:
        for p, text in todo.items():
            atomic_write(p, text)
        res = run_build()
        if not res["ok"]:
            bad("The site did not build, so nothing was changed. " + res["log"][-400:], 422)
    except BaseException:
        for p, b in saved.items():
            shutil.copy2(b, p)
        run_build()
        raise
    return True, res


def check_version(given):
    if given != data_version():
        bad("The site data changed since this page loaded. Reload the page, then make the change again.",
            409, stale=True)


# --------------------------------------------------------------------------- Livid
def livid_id(value):
    v = (value or "").strip()
    m = re.search(r"livid\.com/(?:embed|watch)/([A-Za-z0-9_-]{6,32})", v)
    if m:
        return m.group(1)
    if LIVID_ID.match(v):
        return v
    bad("That isn't a Livid link. Copy the link from the video's page on livid.com "
        "(it looks like https://livid.com/watch/...).")


def embed_url(value):
    """A Livid embed link, a watch link or a bare id -> the embed link with this site's parameters."""
    return "https://livid.com/embed/%s?livid_logo=0&fullscreen=1&title=1" % livid_id(value)


def keep_or_embed(new, old):
    """Re-save a video without rewriting its embed parameters (title=0 is deliberate on some)."""
    try:
        if old and livid_id(old) == livid_id(new):
            return old
    except ApiError:
        pass
    return embed_url(new)


def livid_status(video_id):
    """Ask Livid whether the embed plays for a logged-out visitor.

    A new upload starts Private with embedding off; Livid then serves a generic page (title 'Livid',
    no api.livid.com) at the same URL, which is a dead player on the site. -> (playable, title);
    playable is None when Livid could not be reached."""
    try:
        req = urllib.request.Request("https://livid.com/embed/" + video_id,
                                     headers={"User-Agent": "Mozilla/5.0 site-manager"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            body = resp.read(200000).decode("utf-8", "replace")
    except Exception:
        return None, ""
    m = re.search(r"<title>([^<]*)</title>", body)
    title = m.group(1).strip() if m else ""
    playable = "api.livid.com" in body and title not in ("", "Livid")
    return playable, (title if playable else "")


# --------------------------------------------------------------------------- images
def _dims(path):
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
                         capture_output=True, text=True).stdout
    w, h = re.search(r"pixelWidth: (\d+)", out), re.search(r"pixelHeight: (\d+)", out)
    if not (w and h):
        bad("That file could not be read as an image.")
    return int(w.group(1)), int(h.group(1))


def make_jpeg(src, dst, edge, quality):
    """JPEG at `quality`, shrunk to `edge` on the long side. Never enlarged (sips -Z would), and EXIF
    rotation is kept, so a phone portrait still displays upright."""
    w, h = _dims(src)
    cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality)]
    if max(w, h) > edge:
        cmd += ["-Z", str(edge)]
    r = subprocess.run(cmd + [str(src), "--out", str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not Path(dst).is_file():
        bad("That file could not be converted to a JPEG.")


def make_png(src, dst, edge):
    """PNG, shrunk to `edge` on the long side and never enlarged. The alpha channel survives, which is
    the whole point: a masthead cut-out saved as JPEG would show a black box over the photo."""
    w, h = _dims(src)
    cmd = ["sips", "-s", "format", "png"]
    if max(w, h) > edge:
        cmd += ["-Z", str(edge)]
    r = subprocess.run(cmd + [str(src), "--out", str(dst)], capture_output=True, text=True)
    if r.returncode != 0 or not Path(dst).is_file():
        bad("That file could not be converted to a PNG.")


def share_crop(w, h):
    """The centre crop (width, height) that matches the card's 1200:630 and fits inside w x h."""
    if w * SHARE_H >= h * SHARE_W:      # wider than the card: the full height survives
        return max(1, min(w, int(round(h * SHARE_W / float(SHARE_H))))), h
    return w, max(1, min(h, int(round(w * SHARE_H / float(SHARE_W)))))


def _sips(args, src, dst):
    r = subprocess.run(["sips"] + list(args) + [str(src), "--out", str(dst)],
                       capture_output=True, text=True)
    return r.returncode == 0 and Path(dst).is_file()


def make_share_jpeg(src, dst):
    """1200x630 JPEG, centre-cropped. A share card is a designed image, so cropping beats letterboxing.

    Two passes: crop to the card's aspect from the centre, then resample to exactly 1200x630, because
    og:image:width/height are fixed constants and a card of another size would make them lie. A source
    smaller than the card is enlarged on purpose — a half-size card would break the same promise."""
    w, h = _dims(src)
    if w < SHARE_MIN[0] or h < SHARE_MIN[1]:
        bad("That image is too small for a share card (600\u00d7315 minimum).")
    cw, ch = share_crop(w, h)
    crop = Path(str(dst) + ".crop.jpg")
    try:
        # A phone photo carries its rotation in EXIF, so sips may hand the crop back transposed.
        if not _sips(["-c", str(ch), str(cw)], src, crop) or _dims(crop) != (cw, ch):
            if not _sips(["-c", str(cw), str(ch)], src, crop) or _dims(crop) != (ch, cw):
                bad("That file could not be cropped for a share card.")
        if not _sips(["-s", "format", "jpeg", "-s", "formatOptions", str(SHARE_Q),
                      "-z", str(SHARE_H), str(SHARE_W)], crop, dst):
            bad("That file could not be converted to a JPEG.")
    except BaseException:
        Path(dst).unlink(missing_ok=True)
        raise
    finally:
        crop.unlink(missing_ok=True)


def save_upload(q, body):
    """Raw image bytes -> full + thumb for a photo, or one poster. Does not touch the data files."""
    kind = q.get("kind") or "photo"
    scope = q.get("scope") or "project"
    if kind not in ("photo", "poster") or scope not in ("project", "reel", "motion", "showreel", "set",
                                                        "hero",
                                                        "cutout",
                                                        "share"):
        bad("Unknown upload type.")
    if scope in ("hero", "cutout", "share"):
        # Masthead images and the share card belong to no project: always one file, never a thumb.
        kind = "poster"
    name = q.get("name") or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        bad("That file type is not accepted. Use JPEG, PNG, WebP or HEIC.")
    stem = slugify(Path(name).stem)[:50] or "upload"
    if kind == "photo" or scope in ("project", "set"):
        slug = (q.get("slug") or "").strip()
        if not SLUG.match(slug) or not any(p.get("slug") == slug for p in read(FILES["projects"])["projects"]):
            bad("Unknown project.")
        folder = REPO / "assets" / "img" / slug
    elif scope == "share":
        # The share card sits next to the shipped og-cover.jpg, in the root media folder.
        folder = REPO / "assets" / "img"
    else:
        folder = REPO / "assets" / "img" / POSTER_DIR[scope]
    folder.mkdir(parents=True, exist_ok=True)
    thumbs = folder / "thumbs"
    if kind == "photo":
        thumbs.mkdir(exist_ok=True)
    ext = ".png" if scope == "cutout" else ".jpg"      # a cut-out keeps its alpha as a PNG
    base, n = stem, 1
    while (folder / (stem + ext)).exists() or (kind == "photo" and (thumbs / (stem + ".jpg")).exists()):
        n += 1
        stem = "%s-%d" % (base, n)
    src = folder / (".incoming-%s%s" % (stamp(), suffix))
    src.write_bytes(body)
    made = []
    try:
        full = folder / (stem + ext)
        if kind == "photo":
            make_jpeg(src, full, FULL_EDGE, FULL_Q)
            made.append(full)
            thumb = thumbs / (stem + ".jpg")
            make_jpeg(src, thumb, THUMB_EDGE, THUMB_Q)
            made.append(thumb)
            return {"full": str(full.relative_to(REPO)), "thumb": str(thumb.relative_to(REPO)),
                    "alt": q.get("alt", "")}
        if scope == "cutout":
            make_png(src, full, POSTER_EDGE[scope])
            made.append(full)
            return {"full": str(full.relative_to(REPO))}
        if scope == "share":
            make_share_jpeg(src, full)
            made.append(full)
            return {"full": str(full.relative_to(REPO)), "width": SHARE_W, "height": SHARE_H}
        make_jpeg(src, full, POSTER_EDGE[scope], POSTER_Q)
        made.append(full)
        return {"full": str(full.relative_to(REPO))}
    except BaseException:
        for f in made:
            f.unlink(missing_ok=True)
        raise
    finally:
        src.unlink(missing_ok=True)


def all_asset_refs(exclude_slug=None):
    """Every assets/ path named anywhere in the data, except in one project's own manifest entry."""
    found = set()

    def walk(node):
        if isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
        elif isinstance(node, str) and node.startswith("assets/"):
            found.add(node)
    for key in ("projects", "config", "media"):
        walk(read(FILES[key]))
    for slug, entry in read(FILES["manifest"]).get("projects", {}).items():
        if slug != exclude_slug:
            walk(entry)
    return found


def trash_files(rels):
    """Move files out of assets/ (never delete), skipping any that something else still uses."""
    still_used = all_asset_refs()
    TRASH.mkdir(parents=True, exist_ok=True)
    moved = []
    for rel in rels:
        if not rel or rel in still_used:
            continue
        try:
            src = safe_asset(rel)
        except ApiError:
            continue
        if src.is_file():
            shutil.move(str(src), str(TRASH / ("%s-%s" % (stamp(), src.name))))
            moved.append(rel)
    return moved


def entry_files(entry):
    out = []
    for p in entry.get("photos", []):
        out += [p.get("full"), p.get("thumb")]
    for v in entry.get("videos", []):
        out.append(v.get("poster"))
    return [x for x in out if x]


# --------------------------------------------------------------------------- projects
TEXT = ["client", "title", "oneliner", "role", "years", "location", "brief", "result", "category", "bts"]
LISTS = ["did", "scope", "tags"]
NEW_ORDER = ["slug", "client", "featured", "title", "oneliner", "role", "years", "location",
             "tags", "brief", "did", "scope", "result", "bts", "category"]


def featured_order(value, current):
    """Featured order is a whole number (1 = the lead block); 0 or blank = not featured.
    A bare true (an old checkbox) keeps the current number."""
    if isinstance(value, bool):
        if not value:
            return 0
        keep = current if isinstance(current, int) and not isinstance(current, bool) else 0
        return keep if keep > 0 else 1
    text = str(value if value is not None else "").strip()
    if not text:
        return 0
    try:
        n = int(text)
    except ValueError:
        bad("Featured order must be a whole number, or blank.")
    return max(n, 0)


def clean_project(raw, existing=None, categories=None):
    """Validate a project payload against the stored shape.

    Only the fields the form owns are touched. Everything else on an existing project (standalone,
    draft_only, hidden, confirm, demo, ai_steps, ...) is carried through untouched, in its order."""
    p = dict(existing or {})
    for k in TEXT:
        if k in raw:
            v = raw[k]
            p[k] = v.replace("\r\n", "\n").strip() if isinstance(v, str) else ""
    for k in LISTS:
        if k in raw:
            v = raw[k]
            if isinstance(v, str):
                v = v.splitlines()
            if not isinstance(v, list):
                bad("%s must be a list." % k)
            p[k] = [str(x).strip() for x in v if str(x).strip()]
    if "featured" in raw:
        n = featured_order(raw["featured"], p.get("featured"))
        if n:
            p["featured"] = n
        elif p.get("featured"):
            p["featured"] = False
    if "hidden" in raw:
        if raw["hidden"]:
            p["hidden"] = True
        else:
            p.pop("hidden", None)
    if not p.get("client", "").strip():
        bad("Client is required.")
    if not p.get("title", "").strip():
        bad("Title is required.")
    cat = p.get("category") or ""
    if categories is not None and "category" in raw and cat and cat not in categories \
            and cat != (existing or {}).get("category"):
        bad("Unknown category.")
    slug = (raw.get("slug") or p.get("slug") or "").strip().lower()
    if not slug:
        slug = slugify(p["client"] + " " + p["title"])
    if not SLUG.match(slug):
        bad("Slug must be lowercase letters, numbers and dashes.")
    p["slug"] = slug
    if existing is not None:
        return p
    p.setdefault("category", "food-beverage")
    p.setdefault("featured", False)
    return {k: p[k] for k in NEW_ORDER if k in p}


def effects(p, media):
    """What showing or hiding this project does to the rest of the site, in plain words."""
    sb = site_build()
    out = []
    if not sb.standalone_target(p):
        out.append("the Work page and its own case-study page")
    elif p.get("slug") == sb.AI_FEATURE:
        out.append("the AI & Workflow page feature")
    if p.get("featured"):
        out.append("the home page featured block")
    if p.get("slug") in sb.ai_live_slugs(media):
        out.append("the AI & Workflow \"Ran live\" cards")
    return out


def save_project(raw):
    projects = read(FILES["projects"])
    manifest = read(FILES["manifest"])
    cats = {c.get("key") for c in read(FILES["config"]).get("categories", [])}
    slug = (raw.get("slug") or "").strip().lower()
    idx = next((i for i, p in enumerate(projects["projects"]) if p["slug"] == slug), None) if slug else None
    p = clean_project(raw, projects["projects"][idx] if idx is not None else None, cats)
    if idx is None:
        if any(x["slug"] == p["slug"] for x in projects["projects"]):
            p["slug"] = unique_slug(p["slug"], {x["slug"] for x in projects["projects"]})
        projects["projects"].append(p)
        manifest["projects"].setdefault(p["slug"], {"photos": [], "videos": []})
        action = "created"
    else:
        projects["projects"][idx] = p
        action = "updated"
    changed, res = commit({FILES["projects"]: projects, FILES["manifest"]: manifest})
    return {"ok": True, "action": action, "slug": p["slug"], "changed": changed}


def set_hidden(slug, hidden):
    projects = read(FILES["projects"])
    media = read(FILES["media"])
    p = next((x for x in projects["projects"] if x.get("slug") == slug), None)
    if p is None:
        bad("No such project.", 404)
    was = bool(p.get("hidden"))
    if hidden:
        p["hidden"] = True
    else:
        p.pop("hidden", None)
    changed, res = commit({FILES["projects"]: projects})
    return {"ok": True, "slug": slug, "hidden": bool(hidden), "changed": changed and was != bool(hidden),
            "affects": effects(p, media)}


def order_projects(slugs):
    """Rearrange projects.json into `slugs` order. A partial or duplicate list is refused: it would
    silently drop projects."""
    if not isinstance(slugs, list) or not all(isinstance(s, str) for s in slugs):
        bad("slugs must be an array of strings.")
    projects = read(FILES["projects"])
    by = {p["slug"]: p for p in projects["projects"]}
    if len(slugs) != len(set(slugs)) or set(slugs) != set(by) or len(slugs) != len(projects["projects"]):
        bad("The order must list every project exactly once.")
    projects["projects"] = [by[s] for s in slugs]
    changed, res = commit({FILES["projects"]: projects})
    return {"ok": True, "changed": changed}


def delete_project(slug):
    """Archive a project (data + manifest entry) and trash the files nothing else uses. Reels that
    pointed at it stay on the Reels page, unlinked."""
    projects, manifest = read(FILES["projects"]), read(FILES["manifest"])
    media, config = read(FILES["media"]), read(FILES["config"])
    idx = next((i for i, p in enumerate(projects["projects"]) if p.get("slug") == slug), None)
    if idx is None:
        bad("No such project.", 404)
    project = projects["projects"].pop(idx)
    entry = manifest["projects"].pop(slug, {"photos": [], "videos": []})
    unlinked = []
    for r in media.get("reels", []):
        if r.get("project") == slug:
            r["project"] = ""
            unlinked.append(r.get("slug") or r.get("title"))
    if isinstance(media.get("ai_live"), list) and slug in media["ai_live"]:
        media["ai_live"] = [s for s in media["ai_live"] if s != slug]
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    keep = ARCHIVE / ("%s-%s.json" % (slug, stamp()))
    keep.write_text(dump({"project": project, "manifest": entry, "unlinked_reels": unlinked}), encoding="utf-8")
    try:
        changed, res = commit({FILES["projects"]: projects, FILES["manifest"]: manifest, FILES["media"]: media})
    except BaseException:
        keep.unlink(missing_ok=True)
        raise
    moved = trash_files(entry_files(entry))
    folder = REPO / "assets" / "img" / slug
    if folder.is_dir():
        for d in sorted(folder.glob("**/*"), reverse=True):           # deepest first
            if d.is_dir():
                try:
                    d.rmdir()
                except OSError:
                    pass
        try:
            folder.rmdir()
        except OSError:
            pass
    return {"ok": True, "slug": slug, "archive": str(keep.relative_to(REPO)), "files_moved": len(moved),
            "unlinked_reels": unlinked}


# --------------------------------------------------------------------------- photos and videos
def clean_photo(item):
    if not isinstance(item, dict) or not item.get("full"):
        bad("Bad photo.")
    existing_asset(item["full"], "Photo")
    if item.get("thumb"):
        existing_asset(item["thumb"], "Thumbnail")
    out = {"full": item["full"], "thumb": item.get("thumb") or item["full"], "alt": str(item.get("alt") or "")}
    # The badges (`ai`, `composite`) ride on the photo; a re-add must not lose them. Only a true flag is
    # stored, so an unflagged photo stays byte-identical to what it was.
    for flag in ("ai", "composite"):
        if item.get(flag):
            out[flag] = True
    return out


def photo_op(payload):
    slug = (payload.get("slug") or "").strip()
    action = payload.get("action")
    manifest = read(FILES["manifest"])
    if not SLUG.match(slug) or slug not in {p["slug"] for p in read(FILES["projects"])["projects"]}:
        bad("Unknown project.", 404)
    entry = manifest["projects"].setdefault(slug, {"photos": [], "videos": []})
    entry.setdefault("photos", [])
    entry.setdefault("videos", [])
    photos = entry["photos"]
    gone = []
    if action == "add":
        new = payload.get("items") or ([payload["item"]] if payload.get("item") else [])
        if not new:
            bad("Nothing to add.")
        photos.extend(clean_photo(x) for x in new)
    elif action == "remove":
        i = payload.get("index")
        if not isinstance(i, int) or not 0 <= i < len(photos):
            bad("No such photo.")
        removed = photos.pop(i)
        gone = [removed.get("full"), removed.get("thumb")]
    elif action == "reorder":
        order = payload.get("order")
        have = [p.get("full") for p in photos]
        if not isinstance(order, list) or not all(isinstance(x, str) for x in order) \
                or sorted(order) != sorted(have):
            bad("The order must list every photo of the project exactly once.")
        by = {}
        for p in photos:
            by.setdefault(p["full"], []).append(p)
        photos[:] = [by[f].pop(0) for f in order]
    elif action == "update":
        i = payload.get("index")
        if not isinstance(i, int) or not 0 <= i < len(photos):
            bad("No such photo.")
        if "alt" in payload:
            photos[i]["alt"] = str(payload["alt"] or "")
        # Two independent badges, both optional: a false (or absent) flag is not stored at all.
        for flag in ("ai", "composite"):
            if flag in payload:
                if payload[flag]:
                    photos[i][flag] = True
                else:
                    photos[i].pop(flag, None)
    else:
        bad("Unknown action.")
    changed, res = commit({FILES["manifest"]: manifest})
    moved = trash_files(gone)
    return {"ok": True, "changed": changed, "photos": len(photos), "files_moved": len(moved)}


def set_videos(slug, rows):
    """Replace a project's whole video list, in order. A row's `from` names its old position, so an
    existing video keeps everything the form does not edit (mp4, ai/composite flags, embed params)."""
    if not isinstance(rows, list):
        bad("videos must be an array.")
    projects = read(FILES["projects"])
    if not SLUG.match(slug or "") or slug not in {p["slug"] for p in projects["projects"]}:
        bad("Unknown project.", 404)
    manifest = read(FILES["manifest"])
    entry = manifest["projects"].setdefault(slug, {"photos": [], "videos": []})
    old = entry.get("videos", [])
    used, new = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            bad("Each video must be an object.")
        src = row.get("from")
        base = {}
        if isinstance(src, int) and 0 <= src < len(old) and src not in used:
            used.add(src)
            base = dict(old[src])
        title = str(row.get("title") or "").strip()
        if not title:
            bad("Give video %d a title." % n)
        if not str(row.get("livid") or "").strip() and not base.get("mp4"):
            bad("Video %d needs a Livid link." % n)
        ratio = row.get("ratio") or base.get("ratio") or "16 / 9"
        if ratio not in RATIOS:
            bad("Video %d: unknown shape %r." % (n, ratio))
        v = base
        v["title"] = title
        if str(row.get("livid") or "").strip():
            v["livid"] = keep_or_embed(row["livid"], base.get("livid"))
        v.setdefault("livid", "")
        v["ratio"] = ratio
        if "poster" in row:
            poster = str(row.get("poster") or "")
            v["poster"] = existing_asset(poster, "Poster") if poster else ""
        v.setdefault("poster", "")
        v.setdefault("mp4", "")
        new.append(v)
    gone = [o.get("poster") for i, o in enumerate(old) if i not in used]
    # a replaced poster leaves its old file behind
    gone += [old[r["from"]].get("poster") for r in rows
             if isinstance(r.get("from"), int) and 0 <= r["from"] < len(old)
             and "poster" in r and (r.get("poster") or "") != (old[r["from"]].get("poster") or "")]
    entry["videos"] = new
    changed, res = commit({FILES["manifest"]: manifest})
    moved = trash_files(gone)
    return {"ok": True, "changed": changed, "videos": len(new), "files_moved": len(moved)}


# --------------------------------------------------------------------------- lists (reels, credits, ...)
LISTS_SPEC = {
    "reels": {"file": "media", "key": "reels", "unique": "slug", "unique_from": "title",
              "fields": {"title": "text!", "brand": "text", "format": "text", "year": "text",
                         "project": "project", "livid": "livid!", "poster": "asset", "ratio": "ratio",
                         "ai": "bool"},
              "defaults": {"mp4": "", "poster": "", "year": "", "ratio": "9 / 16"}},
    "motion": {"file": "media", "key": "motion", "unique": "slug", "unique_from": "title",
               "fields": {"title": "text!", "client": "text", "year": "text", "role": "text",
                          "livid": "livid!", "poster": "asset", "ratio": "ratio"},
               "defaults": {"mp4": "", "poster": "", "year": "", "ratio": "16 / 9"}},
    "credits": {"file": "media", "key": "credits",
                "fields": {"artist": "text!", "work": "text!", "role": "text!", "year": "text"},
                "defaults": {"year": ""}},
    "experience": {"file": "config", "key": "experience",
                   "fields": {"role": "text!", "org": "text", "years": "text", "place": "text"},
                   "defaults": {"org": "", "years": "", "place": ""}},
    "side_projects": {"file": "config", "key": "side_projects",
                      "fields": {"label": "text!", "line": "text", "href": "url"},
                      "defaults": {"line": "", "href": ""}},
}


def coerce(kind, value, label, projects, old=None):
    required = kind.endswith("!")
    kind = kind.rstrip("!")
    if kind == "bool":
        return bool(value)
    text = "" if value is None else str(value).replace("\r\n", "\n").strip()
    if required and not text:
        bad("%s is required." % label)
    if kind == "text":
        return text
    if kind == "ratio":
        if text not in RATIOS:
            bad("%s: unknown shape %r." % (label, text))
        return text
    if kind == "project":
        if text and text not in projects:
            bad("%s: no project called %r." % (label, text))
        return text
    if kind == "asset":
        return existing_asset(text, "Poster") if text else ""
    if kind == "url":
        if text and not re.match(r"^https?://", text):
            bad("%s must start with http:// or https://." % label)
        return text
    if kind == "livid":
        return keep_or_embed(text, (old or {}).get("livid")) if text else ""
    return text


def save_list(name, rows):
    spec = LISTS_SPEC.get(name)
    if not spec:
        bad("Unknown list.", 404)
    if not isinstance(rows, list):
        bad("rows must be an array.")
    data = read(FILES[spec["file"]])
    old = data.get(spec["key"], [])
    projects = {p["slug"] for p in read(FILES["projects"])["projects"]}
    used, new = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            bad("Each row must be an object.")
        src = row.get("from")
        base = None
        if isinstance(src, int) and 0 <= src < len(old) and src not in used:
            used.add(src)
            base = dict(old[src])
        out = base if base is not None else dict(spec.get("defaults", {}))
        for field, kind in spec["fields"].items():
            label = "%s %d: %s" % (name.replace("_", " ").title(), n, field)
            if field in row:
                out[field] = coerce(kind, row[field], label, projects, base)
            elif base is None and kind.endswith("!"):
                coerce(kind, None, label, projects)          # raises "<field> is required"
            elif base is None and kind.rstrip("!") == "livid":
                out[field] = ""
        uniq = spec.get("unique")
        if uniq and not out.get(uniq):
            taken = {r.get(uniq) for r in new} | {r.get(uniq) for r in old}
            out[uniq] = unique_slug(slugify(out.get(spec["unique_from"], "")), taken)
        new.append(out)
    data[spec["key"]] = new
    changed, res = commit({FILES[spec["file"]]: data})
    return {"ok": True, "changed": changed, "rows": len(new)}


# --------------------------------------------------------------------------- site settings
CONFIG_TEXT = ("name", "title", "headline", "subline", "based", "email", "instagram", "linkedin",
               "ai_label", "hero_caption", "cta", "education", "languages")
CONFIG_ASSET = ("hero_image", "hero_image_phone", "hero_cutout")
CONFIG_LINES = ("tools", "certifications", "bio")
INTRO_KEYS = ("work", "reels", "ai", "paid", "about")
# Per-page SEO (site-config -> seo.pages.<page>): title and description caps, and the pages that are not
# a copy-registry page of their own (the two legal documents, and the 404, which has a title only).
SEO_FIELDS = ("title", "description")
SEO_MAX = {"title": 70, "description": 160}
SEO_EXTRA_PAGES = ("legal.privacy", "legal.terms", "notfound")
SEO_NO_DESCRIPTION = ("notfound",)


def clean_domain(v):
    v = re.sub(r"^https?://", "", (v or "").strip().lower()).split("/")[0]
    if v and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", v):
        bad("That does not look like a domain (example.com).")
    return v


def lines_of(v, field):
    if isinstance(v, str):
        v = v.splitlines()
    if not isinstance(v, list):
        bad("%s must be a list of lines." % field)
    return [str(x).strip() for x in v if str(x).strip()]


def save_config(raw):
    cfg, media = read(FILES["config"]), read(FILES["media"])
    projects = {p["slug"] for p in read(FILES["projects"])["projects"]}
    sb = site_build()
    touched = []
    for k in CONFIG_TEXT:
        if k in raw:
            if not isinstance(raw[k], str):
                bad("%s must be text." % k)
            cfg[k] = raw[k].strip()
            touched.append(k)
    if "name" in raw and not cfg["name"]:
        bad("Name is required.")
    if "email" in raw and cfg["email"] and "@" not in cfg["email"]:
        bad("That does not look like an email address.")
    for k in CONFIG_ASSET:
        if k in raw:
            cfg[k] = existing_asset(str(raw[k]).strip(), "Image") if str(raw[k]).strip() else ""
            touched.append(k)
    if "og_image" in raw:
        # The share card: an empty value means the default the code already ships, so the key goes.
        value = str(raw["og_image"] or "").strip()
        if value:
            cfg["og_image"] = existing_asset(value, "Share card")
        else:
            cfg.pop("og_image", None)
        touched.append("og_image")
    if "domain" in raw:
        cfg["domain"] = clean_domain(raw["domain"])
        touched.append("domain")
    for k in CONFIG_LINES:
        if k in raw:
            cfg[k] = lines_of(raw[k], k)
            touched.append(k)
    if "copy" in raw:
        touched.append("copy")
        if not isinstance(raw["copy"], dict):
            bad("copy must be an object of text field -> text.")
        limits = sb.copy_limits()
        store = cfg.get("copy")
        if not isinstance(store, dict):
            store = {}
        for key in sorted(raw["copy"]):
            value = raw["copy"][key]
            if key not in limits:
                bad("Unknown text field: %s." % key)
            if not isinstance(value, str):
                bad("That text field takes text: %s." % key)
            if "\r" in value:
                bad("Line breaks are not allowed in editable text.")
            v = value.strip()
            if len(v) >= 2 and v.startswith("[") and v.endswith("]"):
                bad("Square brackets are reserved for 'to add' boxes.")
            limit = limits[key][1]
            if limit and len(v) > limit:
                bad("That text is too long: %d characters at most." % limit)
            if v:
                store[key] = v
            else:
                store.pop(key, None)
        if store:
            cfg["copy"] = store
        else:
            cfg.pop("copy", None)
    if "seo" in raw:
        touched.append("seo")
        block = raw["seo"]
        if not isinstance(block, dict) or not isinstance(block.get("pages"), dict):
            bad('seo must be an object with a "pages" object.')
        known = (set(sb.copy_pages()) - {"everypage", "legal"}) | set(SEO_EXTRA_PAGES)
        seo = cfg.get("seo")
        if not isinstance(seo, dict):
            seo = {}
        pages_cfg = seo.get("pages")
        if not isinstance(pages_cfg, dict):
            pages_cfg = {}
        for page_key in sorted(block["pages"]):
            if page_key not in known:
                bad("Unknown page for SEO: %s." % page_key)
            given = block["pages"][page_key]
            if not isinstance(given, dict):
                bad("SEO for %s must be an object with title and description." % page_key)
            entry = pages_cfg.get(page_key)
            if not isinstance(entry, dict):
                entry = {}
            for field in sorted(given):
                if field not in SEO_FIELDS:
                    bad("Unknown SEO field: %s." % field)
                if field == "description" and page_key in SEO_NO_DESCRIPTION:
                    bad("The 404 page has a title only.")
                value = given[field]
                if not isinstance(value, str):
                    bad("SEO %s for %s must be text." % (field, page_key))
                if "\r" in value:
                    bad("Line breaks are not allowed in SEO text.")
                v = value.strip()
                if len(v) > SEO_MAX[field]:
                    bad("That SEO %s is too long: %d characters at most."
                        % (field, SEO_MAX[field]))
                if v:
                    entry[field] = v
                else:
                    entry.pop(field, None)
            if entry:
                pages_cfg[page_key] = entry
            else:
                pages_cfg.pop(page_key, None)
        if pages_cfg:
            seo["pages"] = pages_cfg
            cfg["seo"] = seo
        else:
            seo.pop("pages", None)
            if seo:
                cfg["seo"] = seo
            else:
                cfg.pop("seo", None)
    if "masthead_labels" in raw:
        labels = raw["masthead_labels"]
        if not isinstance(labels, list):
            bad("masthead_labels must be a list.")
        cfg["masthead_labels"] = [str(x).strip() for x in labels][:3]
        touched.append("masthead_labels")
    if "page_intros" in raw:
        if not isinstance(raw["page_intros"], dict):
            bad("page_intros must be an object.")
        intros = cfg.setdefault("page_intros", {})
        for k, v in raw["page_intros"].items():
            if k in INTRO_KEYS:
                intros[k] = str(v or "").strip()
        touched.append("page_intros")
    if "collaborations" in raw:
        c = raw["collaborations"]
        if not isinstance(c, dict):
            bad("collaborations must be an object.")
        cfg.setdefault("collaborations", {})
        if "label" in c:
            cfg["collaborations"]["label"] = str(c["label"] or "").strip()
        if "names" in c:
            cfg["collaborations"]["names"] = lines_of(c["names"], "collaborations")
        touched.append("collaborations")
    if "showreel" in raw:
        s = raw["showreel"]
        if not isinstance(s, dict):
            bad("showreel must be an object.")
        sr = cfg.setdefault("showreel", {})
        if "hidden" in s:
            if s["hidden"]:
                sr["hidden"] = True
            else:
                sr.pop("hidden", None)
        for slot in ("vertical", "horizontal"):
            if slot in s and isinstance(s[slot], dict) and "livid" in s[slot]:
                slot_data = sr.setdefault(slot, {})
                link = str(s[slot]["livid"] or "").strip()
                slot_data["livid"] = keep_or_embed(link, slot_data.get("livid")) if link else ""
        touched.append("showreel")
    if "hidden_pages" in raw:
        pages = raw["hidden_pages"]
        if not isinstance(pages, list) or any(p not in sb.HIDEABLE_PAGES for p in pages):
            bad("Only these pages can be switched off: %s." % ", ".join(sb.HIDEABLE_PAGES))
        if pages:
            cfg["hidden_pages"] = list(dict.fromkeys(pages))
        else:
            cfg.pop("hidden_pages", None)
        touched.append("hidden_pages")
    if "ai_live" in raw:
        live = raw["ai_live"]
        if not isinstance(live, list) or any(s not in projects for s in live):
            bad("ai_live must list existing project slugs.")
        media["ai_live"] = list(dict.fromkeys(live))
        touched.append("ai_live")
    if not touched:
        bad("Nothing to save.")
    changed, res = commit({FILES["config"]: cfg, FILES["media"]: media})
    return {"ok": True, "changed": changed, "touched": touched}


def replace_resume(name, body, version):
    """Swap the résumé PDF. Pages exports lose their fi/fl/ff text, so every upload is repaired."""
    if not body.startswith(b"%PDF-"):
        bad("That is not a PDF file.")
    cfg = read(FILES["config"])
    rel = cfg.get("resume_pdf") or ""
    dest = safe_asset(rel) if rel else REPO / "assets" / "docs" / "Resume.pdf"
    dest.parent.mkdir(parents=True, exist_ok=True)
    incoming = dest.parent / (".incoming-%s.pdf" % stamp())
    repaired = dest.parent / (".repaired-%s.pdf" % stamp())
    incoming.write_bytes(body)
    note = "stored as uploaded"
    final = incoming
    try:
        title = "%s \u2014 R\u00e9sum\u00e9 %s" % (cfg.get("name", ""), time.strftime("%Y"))
        r = subprocess.run([sys.executable, str(TOOLS / "fix_pdf_ligatures.py"), str(incoming), str(repaired),
                            "--title", title], capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and repaired.is_file():
            final, note = repaired, "text layer repaired (%s)" % (r.stdout.strip().splitlines() or ["ok"])[-2:][0]
        else:
            note = "stored as uploaded; the repair step failed: %s" % (r.stderr.strip()[-160:] or "no output")
        if dest.exists():
            BACKUPS.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, BACKUPS / ("resume-%s.pdf" % stamp()))
        shutil.move(str(final), str(dest))
    finally:
        incoming.unlink(missing_ok=True)
        repaired.unlink(missing_ok=True)
    if not rel:
        cfg["resume_pdf"] = str(dest.relative_to(REPO))
        commit({FILES["config"]: cfg})
    return {"ok": True, "path": str(dest.relative_to(REPO)), "size": dest.stat().st_size, "note": note}


# --------------------------------------------------------------------------- publish
# Publishing = commit everything and push `HEAD:main` to the GitHub remote, which puts the site live.
# Safe by construction: it never stashes, merges, resets, rebases or forces; it refuses when GitHub
# has commits this copy has not seen; it builds a RELEASE, runs the unit tests, and refuses a secret-
# looking file or a huge blob before anything is committed. Credentials only ever come from the
# Mac's git credential helper; they are never read here, and anything that looks like one is scrubbed
# from every string this module stores or returns.
PUBLISH_BRANCH = "main"
PUBLISH_DEFAULT_MESSAGE = "Site update (edited in the site manager)"
PUBLISH_MAX_BLOB = 50 * 1024 * 1024           # a staged blob this size or larger is refused
PUBLISH_FETCH_TIMEOUT = 15                    # the quick fetch behind the status
PUBLISH_PUSH_TIMEOUT = 900
PUBLISH_LOG_CAP = 20000
# Test modules the gate leaves out: test_cms_ui drives headless Chrome; test_legacy_redirects takes ~40 s
# and checks the old food-site URLs, not anything the manager edits; test_publish tests this very code on
# temp repos (it would run the gate inside the gate). Everything else must pass.
PUBLISH_GATE_SKIP = ("test_cms_ui", "test_legacy_redirects", "test_publish")
PUBLISH_STEPS = ("Checking", "Fetching from GitHub", "Building the site", "Running the tests",
                 "Preparing the commit", "Committing", "Pushing to GitHub", "Checking it landed")
SECRET_PATH = re.compile(r"^(?:\.env.*|.*settings\.local.*|.*token.*|.*secret.*|.*credential.*|"
                         r".*\.pem|.*\.key|id_rsa.*|id_dsa.*|id_ecdsa.*|id_ed25519.*)$", re.I)

JOB_LOCK = threading.Lock()
IDLE_JOB = {"id": "", "state": "idle", "step": "", "log": "", "error": "", "commit": "", "url": "",
            "message": ""}
JOB = dict(IDLE_JOB)
_KNOWN_SECRETS = set()            # credential strings seen in the remote URL, scrubbed wherever they show up


class Refused(Exception):
    """A publish stopped on purpose, with a plain-language reason. Nothing was pushed."""


# -- scrubbing
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s@'\"<>]+@")
_TOKEN_SHAPES = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|glpat-[A-Za-z0-9_-]{16,}|"
    r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|"
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,})")
_KEYED_VALUE = re.compile(r"(?i)\b(authorization|x-access-token|access[_-]?token|api[_-]?key|password|passwd|"
                          r"secret|token)(\"?'?\s*[:=]\s*\"?'?)(?:bearer\s+|basic\s+)?[^\s\"',;&]+")
_AUTH_SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")


def scrub(text):
    """Remove anything that looks like a credential: user:pass@ in URLs, token shapes, key=value secrets."""
    text = "" if text is None else str(text)
    for s in sorted(_KNOWN_SECRETS, key=len, reverse=True):
        text = text.replace(s, "***")
    text = _URL_USERINFO.sub(r"\1***@", text)
    text = _TOKEN_SHAPES.sub("***", text)
    text = _KEYED_VALUE.sub(r"\1\2***", text)
    return _AUTH_SCHEME.sub(r"\1 ***", text)


def strip_credentials(url):
    """A remote URL fit to show: no password, and no user name on http(s)."""
    m = re.match(r"^([a-z][a-z0-9+.-]*://)([^/?#]*@)?(.*)$", url or "", re.I | re.S)
    if not m or not m.group(2):
        return url or ""
    if m.group(1).lower().startswith("http"):
        return m.group(1) + m.group(3)
    return m.group(1) + m.group(2)[:-1].split(":")[0] + "@" + m.group(3)


def _remember_secrets(url):
    m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#]*)@", url or "", re.I)
    if m:
        user, _, password = m.group(1).partition(":")
        secret = password or (user if len(user) >= 20 else "")      # a user name alone is not a secret
        if len(secret) >= 4:
            _KNOWN_SECRETS.add(secret)


GITHUB_REMOTE = re.compile(r"^(?:https?://(?:[^/@]+@)?github\.com/|ssh://(?:[^/@]+@)?github\.com(?::\d+)?/|"
                           r"(?:[^/@]+@)?github\.com:)([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", re.I)


def github_commit_url(remote_url, sha):
    """https://github.com/<owner>/<repo>/commit/<sha> for a GitHub remote; '' for any other kind."""
    m = GITHUB_REMOTE.match(strip_credentials(remote_url or "").strip())
    return "https://github.com/%s/%s/commit/%s" % (m.group(1), m.group(2), sha) if m and sha else ""


# -- git
def _git_env(extra=None):
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                                              "GIT_OBJECT_DIRECTORY", "GIT_NAMESPACE", "GIT_PREFIX")}
    env["GIT_TERMINAL_PROMPT"] = "0"              # never wait for a password nobody can type
    env["GIT_OPTIONAL_LOCKS"] = "0"               # a status poll must never fight a commit for the index lock
    env["GCM_INTERACTIVE"] = "never"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.setdefault("GIT_SSH_COMMAND", "ssh -o BatchMode=yes")
    env.update(extra or {})
    return env


def _git(*args, timeout=60, env=None, stdin=None):
    """Run git in the repo -> (returncode, stdout, stderr). stderr is scrubbed here; stdout is parsed as it comes,
    so whatever shows it to anyone scrubs it first. Never raises; a timeout is rc 124."""
    try:
        r = subprocess.run(["git"] + list(args), cwd=str(REPO), capture_output=True, text=True,
                           timeout=timeout, env=_git_env(env), input=stdin)
    except subprocess.TimeoutExpired:
        return 124, "", "git %s timed out after %d s" % (args[0], timeout)
    except OSError as e:
        return 127, "", "git could not run: %s" % e
    return r.returncode, r.stdout, scrub(r.stderr)


def _is_own_repo():
    """True only when REPO itself is a git work tree (not a folder that happens to sit inside another one)."""
    rc, out, _ = _git("rev-parse", "--show-toplevel")
    return rc == 0 and Path(out.strip()).resolve() == REPO


def _origin_url():
    rc, out, _ = _git("remote", "get-url", "origin")
    url = out.strip() if rc == 0 else ""
    _remember_secrets(url)
    return url


def _branch():
    rc, out, _ = _git("symbolic-ref", "--short", "-q", "HEAD")
    return out.strip() if rc == 0 else ""


def _fetch(timeout):
    """git fetch origin main -> (reachable, error text). A GitHub with no main yet still counts as reachable."""
    rc, _, err = _git("fetch", "--quiet", "origin", PUBLISH_BRANCH, timeout=timeout)
    if rc == 0 or "couldn't find remote ref" in err:
        return True, ""
    return False, err.strip()


def _ahead_behind():
    """(ahead, behind) of HEAD against origin/main as last fetched."""
    rc, _, _ = _git("rev-parse", "-q", "--verify", "refs/remotes/origin/" + PUBLISH_BRANCH)
    if rc != 0:                                    # GitHub has no main we know of: all of HEAD is ahead
        rc, out, _ = _git("rev-list", "--count", "HEAD")
        return (int(out.strip()) if rc == 0 and out.strip().isdigit() else 0), 0
    rc, out, _ = _git("rev-list", "--left-right", "--count", "HEAD...refs/remotes/origin/" + PUBLISH_BRANCH)
    parts = out.split()
    if rc != 0 or len(parts) != 2:
        return 0, 0
    return int(parts[0]), int(parts[1])


def _change_count():
    """Uncommitted tracked changes plus new non-ignored files, as a number."""
    rc, out, _ = _git("status", "--porcelain=v1", "-z", "--untracked-files=normal")
    return len([p for p in out.split("\0") if p]) if rc == 0 else 0


def _git_operation_in_progress():
    rc, out, _ = _git("rev-parse", "--git-dir")
    gd = Path(out.strip()) if rc == 0 else None
    if gd is None:
        return ""
    gd = gd if gd.is_absolute() else REPO / gd
    for name, what in (("MERGE_HEAD", "a merge"), ("rebase-merge", "a rebase"), ("rebase-apply", "a rebase"),
                       ("CHERRY_PICK_HEAD", "a cherry-pick"), ("REVERT_HEAD", "a revert")):
        if (gd / name).exists():
            return what
    return ""


def _repo_blocker():
    """Why this copy cannot publish at all, from git alone ('' when it can)."""
    if not _is_own_repo() or not _origin_url():
        return "No GitHub remote is set up for this site, so there is nowhere to publish to."
    branch = _branch()
    if branch != PUBLISH_BRANCH:
        return "This copy is on %s, not %s. Ask Hermes to switch it back before publishing." % (
            ("branch '%s'" % branch) if branch else "no branch (detached)", PUBLISH_BRANCH)
    busy = _git_operation_in_progress()
    if busy:
        return "Git is in the middle of %s. Ask Hermes to finish it first." % busy
    return ""


def _jset(**kw):
    with JOB_LOCK:
        JOB.update(kw)


def _jlog(text):
    text = scrub(text).rstrip()
    if not text:
        return
    with JOB_LOCK:
        log = (JOB["log"] + text + "\n")
        JOB["log"] = log if len(log) <= PUBLISH_LOG_CAP else "... (earlier log cut)\n" + log[-PUBLISH_LOG_CAP:]


def _job_running():
    with JOB_LOCK:
        return JOB["state"] == "running"


def job_snapshot():
    with JOB_LOCK:
        return dict(JOB, ok=True, steps=list(PUBLISH_STEPS))


def publish_status(fetch=True):
    """Everything the Publish button and its pill need. Runs a quick fetch so ahead/behind are fresh."""
    running = _job_running()
    st = {"ok": True, "remote": False, "remote_url": "", "branch": "", "ahead": 0, "behind": 0, "dirty": False,
          "changed_files": 0, "release_ready": False, "last_commit": "", "live_url": "", "reachable": True,
          "running": running, "can_publish": False, "reason": ""}
    try:
        domain = str(read(FILES["config"]).get("domain") or "").strip()
    except (OSError, ValueError):
        domain = ""
    if domain and not domain.startswith("["):
        st["live_url"] = "https://%s/" % domain
    if not _is_own_repo():
        st["reason"] = "This site is not in a git repository, so it cannot be published."
        return st
    raw = _origin_url()
    st["remote"] = bool(raw)
    st["remote_url"] = strip_credentials(raw)
    st["branch"] = _branch()
    if raw and fetch and not running:
        st["reachable"], _ = _fetch(PUBLISH_FETCH_TIMEOUT)
    if raw:
        st["ahead"], st["behind"] = _ahead_behind()
    st["changed_files"] = _change_count()
    st["dirty"] = st["changed_files"] > 0
    rc, out, _ = _git("log", "-1", "--format=%h %s")
    st["last_commit"] = scrub(out.strip()) if rc == 0 else ""
    if running:                                    # the job holds the data lock; use what the last build learned
        st["release_ready"] = bool(BUILD.get("ready"))
    else:
        with LOCK:
            st["release_ready"] = bool(release_status().get("ready"))
    if not raw:
        st["reason"] = _repo_blocker()
    elif running:
        st["reason"] = "A publish is already running."
    elif not st["reachable"]:
        st["reason"] = "Cannot reach GitHub"
    elif st["behind"] > 0:
        st["reason"] = "GitHub has newer commits; ask Hermes to merge them first"
    else:
        st["reason"] = _repo_blocker()
    st["can_publish"] = not st["reason"]
    return st


# -- the job
def clean_message(message):
    if message is None or message == "":
        return PUBLISH_DEFAULT_MESSAGE
    if not isinstance(message, str):
        bad("The commit message must be text.")
    text = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", message).strip()
    if len(text) > 500:
        bad("Keep the commit message under 500 characters.")
    return text or PUBLISH_DEFAULT_MESSAGE


def _step(i):
    _jset(step=PUBLISH_STEPS[i])
    _jlog("== %s" % PUBLISH_STEPS[i])


def _run_tests():
    """The unit tests, minus PUBLISH_GATE_SKIP, in a clean child process (no PYTHONPATH). -> (ok, names, log)."""
    if not (REPO / "tests").is_dir():
        return False, [], "There is no tests/ folder, so the tests cannot run."
    script = (
        "import sys, unittest\n"
        "skip = set(sys.argv[1].split(','))\n"
        "def flat(s):\n"
        "    for t in s:\n"
        "        if isinstance(t, unittest.TestSuite):\n"
        "            yield from flat(t)\n"
        "        else:\n"
        "            yield t\n"
        "suite = unittest.TestSuite(t for t in flat(unittest.defaultTestLoader.discover('tests'))\n"
        "                           if type(t).__module__.split('.')[0] not in skip)\n"
        "res = unittest.TextTestRunner(stream=sys.stdout, verbosity=1).run(suite)\n"
        "if res.testsRun == 0:\n"
        "    print('NO TESTS RAN')\n"
        "    sys.exit(2)\n"
        "sys.exit(0 if res.wasSuccessful() else 1)\n")
    try:
        r = subprocess.run([sys.executable, "-c", script, ",".join(PUBLISH_GATE_SKIP)], cwd=str(REPO),
                           capture_output=True, text=True, timeout=900, env=_git_env())
    except subprocess.TimeoutExpired:
        return False, [], "The tests took longer than 15 minutes and were stopped."
    log = scrub(r.stdout + r.stderr)
    names = re.findall(r"^(?:FAIL|ERROR): (\S+) \(([\w.]+)\)", log, re.M)
    return r.returncode == 0, ["%s (%s)" % n for n in names], log


def _staged_guard(env=None):
    """Refuse a secret-looking path or a blob of PUBLISH_MAX_BLOB bytes or more in the staged set. `env` points
    git at a throwaway index, so the check can run before the real index is touched."""
    rc, out, err = _git("diff", "--cached", "--no-renames", "--raw", "-z", "--no-abbrev", env=env)
    if rc != 0:
        raise Refused("Could not list what is staged: %s" % err.strip()[-300:])
    tokens = out.split("\0")
    paths, blobs = [], {}
    i = 0
    while i + 1 < len(tokens):
        meta = tokens[i]
        if not meta.startswith(":"):
            i += 1
            continue
        fields = meta[1:].split()
        path = tokens[i + 1]
        i += 2
        paths.append(path)
        if len(fields) >= 5 and fields[1] != "160000" and set(fields[3]) != {"0"}:
            blobs[path] = fields[3]
    secret = [p for p in paths if any(SECRET_PATH.match(part) for part in p.split("/"))]
    if secret:
        raise Refused("Nothing was committed. These files look like secrets or keys and must not go on GitHub: %s. "
                      "Ask Hermes to remove or ignore them." % ", ".join(secret[:8]))
    if blobs:
        rc, sizes, _ = _git("cat-file", "--batch-check=%(objectname) %(objectsize)", env=env,
                            stdin="\n".join(blobs.values()) + "\n")
        size_of = {}
        for line in sizes.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].isdigit():
                size_of[parts[0]] = int(parts[1])
        big = ["%s (%d MB)" % (p, size_of[b] // (1024 * 1024)) for p, b in blobs.items()
               if size_of.get(b, 0) >= PUBLISH_MAX_BLOB]
        if big:
            raise Refused("Nothing was committed. These files are %d MB or larger, too big for GitHub: %s."
                          % (PUBLISH_MAX_BLOB // (1024 * 1024), ", ".join(big[:8])))
    return paths


def _guard_before_staging():
    """Run the staged-set guards on a COPY of the index, so a refusal leaves the real index untouched."""
    rc, out, _ = _git("rev-parse", "--git-path", "index")
    index = Path(out.strip()) if rc == 0 else None
    if index is not None and not index.is_absolute():
        index = REPO / index
    tmp = tempfile.mkdtemp(prefix="publish-index-")
    try:
        fake = Path(tmp) / "index"
        if index is not None and index.exists():
            shutil.copy2(str(index), str(fake))
        env = {"GIT_INDEX_FILE": str(fake)}
        rc, _, err = _git("add", "-A", env=env, timeout=300)
        if rc != 0:
            raise Refused("git add failed: %s" % err.strip()[-300:])
        _staged_guard(env)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _prepare_and_commit(message):
    """Steps a-f, under the data lock. Returns True when a new commit was made."""
    _step(0)
    blocker = _repo_blocker()
    if blocker:
        raise Refused(blocker)
    _step(1)
    ok, err = _fetch(120)
    if not ok:
        _jlog(err)
        raise Refused("Cannot reach GitHub")
    ahead, behind = _ahead_behind()
    if behind > 0:
        raise Refused("GitHub has newer commits; ask Hermes to merge them first")
    _step(2)
    r = _build_py("--release")
    if r.returncode != 0:
        head = "\n".join((r.stdout or r.stderr).strip().splitlines()[:8])
        _jlog(head)
        if "RELEASE BLOCKED" in r.stdout:
            raise Refused("The site is not release-ready, so it cannot go live yet:\n" + scrub(head))
        raise Refused("The site did not build:\n" + scrub(head))
    BUILD.update(ok=True, ready=True, blockers=0, items=[], log=_cap(r.stdout + r.stderr),
                 last_run=time.strftime("%Y-%m-%d %H:%M:%S"), version=data_version())
    _step(3)
    started = time.time()
    passed, failing, log = _run_tests()
    _jlog("Tests: %s in %.0f s" % ("passed" if passed else "FAILED", time.time() - started))
    if not passed:
        _jlog(log[-6000:])
        last = log.strip().splitlines()[-1] if log.strip() else "no output"
        raise Refused("The tests failed, so nothing was published: %s" % ("; ".join(failing[:10]) if failing else last))
    _step(4)
    _guard_before_staging()
    rc, _, err = _git("add", "-A", timeout=300)
    if rc != 0:
        raise Refused("git add failed: %s" % err.strip()[-300:])
    staged = _staged_guard()
    if not staged:
        _jlog("Nothing new to commit.")
        return False
    _step(5)
    for key in ("user.name", "user.email"):
        rc, out, _ = _git("config", "--get", key)
        if rc != 0 or not out.strip():
            raise Refused("Git has no %s set for this site, so it cannot commit. "
                          "Ask Hermes to set it; the changes are staged, nothing was lost." % key)
    rc, out, err = _git("commit", "-q", "-m", message, timeout=120)
    _jlog(out + err)
    if rc != 0:
        raise Refused("git commit failed: %s" % (err or out).strip()[-300:])
    _jlog("Committed %d file%s." % (len(staged), "" if len(staged) == 1 else "s"))
    return True


def _push_and_verify():
    """Steps g-h, with the data lock released. -> (short sha, commit url) or ('', '') when nothing was pending."""
    ahead, _ = _ahead_behind()
    if ahead == 0:
        return "", ""
    _step(6)
    rc, out, err = _git("push", "origin", "HEAD:main", timeout=PUBLISH_PUSH_TIMEOUT)
    _jlog(out + err)
    if rc != 0:
        raise Refused("GitHub did not accept the push. Your changes are saved in a local commit and nothing "
                      "on the site changed:\n" + (err or out).strip()[-600:])
    _step(7)
    ok, why = _fetch(120)
    ahead, _ = _ahead_behind()
    if not ok or ahead != 0:
        raise Refused("The push finished but GitHub does not show it yet%s. Check again in a moment." % (
            " (" + why[-200:] + ")" if why else ""))
    rc, out, _ = _git("rev-parse", "--short", "HEAD")
    sha = out.strip()
    return sha, github_commit_url(_origin_url(), sha)


def _publish_run(message):
    try:
        with LOCK:
            committed = _prepare_and_commit(message)
        sha, url = _push_and_verify()
        if not sha:
            _jlog("Nothing to publish.")
            _jset(state="done", step="Done", message="Nothing to publish", commit="", url="")
        else:
            _jlog("Published %s. Live in about a minute." % sha)
            _jset(state="done", step="Done", commit=sha, url=url,
                  message="Published." if committed else "Pushed the earlier commits.")
    except Refused as e:
        _jlog("Stopped: %s" % e)
        _jset(state="failed", error=scrub(str(e)))
    except BaseException as e:  # noqa: BLE001 - the job must always end in a state the page can show
        _jlog("Stopped: %s: %s" % (type(e).__name__, e))
        _jset(state="failed", error=scrub("Publish stopped unexpectedly: %s: %s" % (type(e).__name__, e)))


def publish_start(message, version):
    """Reserve the one job slot, check the page is current, run the job in the background -> job id."""
    msg = clean_message(message)
    with JOB_LOCK:
        if JOB["state"] == "running":
            bad("A publish is already running.", 409)
        JOB.clear()
        JOB.update(IDLE_JOB, id="%x" % time.time_ns(), state="running", step=PUBLISH_STEPS[0])
        jid = JOB["id"]
    try:
        with LOCK:
            check_version(version)
    except BaseException:
        with JOB_LOCK:
            JOB.clear()
            JOB.update(IDLE_JOB)
        raise
    threading.Thread(target=_publish_run, args=(msg,), daemon=True).start()
    return jid


# --------------------------------------------------------------------------- state
def ui_manifest(projects):
    out = {}
    for slug, d in projects.items():
        e = dict(d)
        e["photos"] = [dict(p, v=file_token(p.get("thumb") or p.get("full") or "")) for p in d.get("photos", [])]
        e["videos"] = [dict(v, v=file_token(v["poster"])) if v.get("poster") else dict(v)
                       for v in d.get("videos", [])]
        # A replaced step image must never show from cache. Only for projects that have sets, so no
        # project gains an empty key.
        if d.get("sets"):
            e["sets"] = [dict(s, steps=[dict(st, v=file_token(st.get("image") or ""))
                                        for st in s.get("steps", [])])
                         for s in d["sets"]]
        out[slug] = e
    return out


def with_poster_token(rows):
    return [dict(r, v=file_token(r["poster"])) if r.get("poster") else dict(r) for r in rows]


def copy_registry_from_file():
    """templates/copy.json as the manager reads it. The UI renders its pages,
    sections and fields from this, so no copy key is ever hard-coded in JS.
    An unreadable file leaves the manager running with no copy forms."""
    try:
        with open(REPO / "templates" / "copy.json", encoding="utf-8") as f:
            reg = json.load(f)
    except (OSError, ValueError):
        return {"pages": [], "keys": []}
    if not isinstance(reg, dict):
        return {"pages": [], "keys": []}
    return reg


def state():
    cfg, media = read(FILES["config"]), read(FILES["media"])
    sb = site_build()
    rel = cfg.get("resume_pdf") or ""
    resume = {"path": rel, "name": Path(rel).name if rel else "", "exists": False, "size": 0, "v": ""}
    if rel:
        try:
            f = safe_asset(rel)
            if f.is_file():
                resume.update(exists=True, size=f.stat().st_size, v=file_token(rel))
        except ApiError:
            pass
    return {
        "ok": True, "version": data_version(),
        "config": cfg, "categories": cfg.get("categories", []),
        "copy_registry": copy_registry_from_file(),
        "projects": read(FILES["projects"])["projects"],
        "manifest": {"projects": ui_manifest(read(FILES["manifest"]).get("projects", {}))},
        "media": {"reels": with_poster_token(media.get("reels", [])),
                  "motion": with_poster_token(media.get("motion", [])),
                  "credits": media.get("credits", []), "credits_note": media.get("credits_note", ""),
                  "ai_live": list(sb.ai_live_slugs(media)), "paid_social": media.get("paid_social", {})},
        "formats": sorted({r.get("format", "") for r in media.get("reels", []) if r.get("format")}),
        "constants": {"ai_feature": sb.AI_FEATURE, "ai_live_default": list(sb.AI_LIVE),
                      "hideable_pages": list(sb.HIDEABLE_PAGES)},
        "resume": resume, "build": build_summary(),
    }


# --------------------------------------------------------------------------- HTTP
UI_FILES = {"/": "index.html", "/index.html": "index.html", "/favicon.svg": "favicon.svg"}
PREVIEW_OK = re.compile(r"^(?:[A-Za-z0-9_-]+\.html|work/[A-Za-z0-9_-]+\.html|robots\.txt)$")


_EXT_API = None


def ext_api():
    """cms.py itself, handed to the feature modules in EXTENSIONS.

    `python cms.py` is in sys.modules as __main__; the tests load this file by path, so then its own
    globals stand in — same LOCK, same functions, same FILES, so nothing is copied in effect."""
    global _EXT_API
    if _EXT_API is None:
        _EXT_API = sys.modules.get(__name__)
        if _EXT_API is None:
            _EXT_API = type(sys)("cms_api")
            _EXT_API.__dict__.update(globals())
    return _EXT_API


def preview_target(path):
    """The built site only: html pages, robots.txt and everything under assets/. Never data, tools,
    staging, tests or .git."""
    rel = unquote(path[len("/preview"):]).lstrip("/") or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    parts = Path(rel).parts
    if ".." in parts or not parts:
        return None
    ok = bool(PREVIEW_OK.match(rel)) or (parts[0] == "assets" and len(parts) > 1)
    if not ok:
        return None
    target = (REPO / rel).resolve()
    if REPO not in target.parents or not target.is_file():
        return None
    return target


class Handler(BaseHTTPRequestHandler):
    server_version = "SiteManager/2"

    def log_message(self, format, *args):
        pass

    # -- plumbing ---------------------------------------------------------------------------
    def _send(self, code, body, ctype, cache="no-store"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _guard(self):
        """Refuse anything that is not a same-origin request from this machine."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost"):
            self._json(403, {"ok": False, "error": "Bad Host"})
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc.split(":")[0] not in ("127.0.0.1", "localhost"):
            self._json(403, {"ok": False, "error": "Bad Origin"})
            return False
        return True

    def body(self, cap):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > cap:
            bad("Empty or oversized request.")
        return self.rfile.read(n)

    def json_body(self):
        try:
            raw = json.loads(self.body(JSON_CAP))
        except ValueError:
            bad("Bad JSON.")
        if not isinstance(raw, dict):
            bad("Expected an object.")
        return raw

    def serve_file(self, path, cache="no-store"):
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype += "; charset=utf-8" if "charset" not in ctype else ""
        self._send(200, path.read_bytes(), ctype, cache)

    # -- dispatch ---------------------------------------------------------------------------
    def do_GET(self):
        self.route("GET")

    def do_POST(self):
        self.route("POST")

    def do_PUT(self):
        self.route("PUT")

    def do_DELETE(self):
        self.route("DELETE")

    def route(self, method):
        if not self._guard():
            return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        try:
            # Feature modules (cms_sets, ...) own their routes; None means "not mine".
            for ext in EXTENSIONS:
                out = ext.dispatch(ext_api(), self, method, path, q)
                if out is not None:
                    return self._json(200, out)
            if method == "GET":
                if path in UI_FILES:
                    f = UI_DIR / UI_FILES[path]
                    if not f.is_file():
                        bad("The editor UI is missing (tools/cms/%s)." % UI_FILES[path], 404)
                    return self.serve_file(f)
                if re.fullmatch(r"/[a-z0-9_-]+\.(?:js|css)", path):
                    f = UI_DIR / path[1:]
                    if f.is_file():
                        return self.serve_file(f)
                if path == "/preview" or path.startswith("/preview/"):
                    target = preview_target(path)
                    if target is None:
                        bad("Not found.", 404)
                    return self.serve_file(target, "no-cache")
                if path in ("/api/state", "/api/content"):
                    with LOCK:
                        return self._json(200, state())
                if path == "/api/release":
                    with LOCK:
                        b = release_status()
                        return self._json(200, {"ok": True, "ready": b["ready"], "count": b["blockers"],
                                                "items": b["items"], "last_run": b["last_run"]})
                if path == "/api/publish":
                    return self._json(200, publish_status())
                if path == "/api/publish/job":
                    return self._json(200, job_snapshot())
                if path == "/api/livid":
                    vid = livid_id(q.get("url", ""))
                    playable, title = livid_status(vid)
                    return self._json(200, {"ok": True, "embed": embed_url(vid), "playable": playable, "title": title})
            elif method == "POST":
                if path == "/api/build":
                    with LOCK:
                        res = run_build()
                        return self._json(200, {"ok": res["ok"], "ready": res["ready"], "blockers": res["blockers"],
                                                "log": res["log"], "last_run": res["last_run"],
                                                "version": data_version()})
                if path == "/api/publish":
                    raw = self.json_body()
                    return self._json(200, {"ok": True, "job": publish_start(raw.get("message"), raw.get("version"))})
                if path == "/api/upload":
                    with LOCK:
                        return self._json(200, save_upload(q, self.body(UPLOAD_CAP)))
                if path in ("/api/projects", "/api/project", "/api/projects/order", "/api/media", "/api/config") \
                        or re.fullmatch(r"/api/projects/[a-z0-9-]+/visibility", path):
                    raw = self.json_body()
                    with LOCK:
                        check_version(raw.pop("version", None))
                        if path in ("/api/projects", "/api/project"):
                            out = save_project(raw)
                        elif path == "/api/projects/order":
                            out = order_projects(raw.get("slugs"))
                        elif path == "/api/media":
                            out = photo_op(raw)
                        elif path == "/api/config":
                            out = save_config(raw)
                        else:
                            out = set_hidden(path.split("/")[3], bool(raw.get("hidden")))
                        out.update(self._after())
                        return self._json(200, out)
            elif method == "PUT":
                m = re.fullmatch(r"/api/project/([a-z0-9-]+)/videos", path)
                if m:
                    raw = self.json_body()
                    with LOCK:
                        check_version(raw.pop("version", None))
                        out = set_videos(m.group(1), raw.get("videos"))
                        out.update(self._after())
                        return self._json(200, out)
                m = re.fullmatch(r"/api/lists/([a-z_]+)", path)
                if m:
                    raw = self.json_body()
                    with LOCK:
                        check_version(raw.pop("version", None))
                        out = save_list(m.group(1), raw.get("rows"))
                        out.update(self._after())
                        return self._json(200, out)
                if path == "/api/resume":
                    body = self.body(UPLOAD_CAP)
                    with LOCK:
                        check_version(q.get("version"))
                        out = replace_resume(q.get("name") or "resume.pdf", body, q.get("version"))
                        out.update(self._after())
                        return self._json(200, out)
            elif method == "DELETE":
                m = re.fullmatch(r"/api/projects/([a-z0-9-]+)", path)
                if m:
                    with LOCK:
                        check_version(q.get("version"))
                        out = delete_project(m.group(1))
                        out.update(self._after())
                        return self._json(200, out)
            bad("Not found.", 404)
        except ApiError as e:
            self._json(e.status, dict({"ok": False, "error": e.message}, **e.extra))
        except Exception as e:  # noqa: BLE001 - report, never crash the editor
            self._json(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})

    def _after(self):
        """What every successful write returns, so the page can carry on without a reload."""
        return {"version": data_version(), "build": build_summary()}


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else PORT
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    srv.daemon_threads = True
    print("Site Manager on http://127.0.0.1:%d/  (repo %s)" % (port, REPO), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
