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
    POST /api/upload?slug=&kind=&scope=&name=    raw image bytes -> derivatives
    POST /api/config                         site settings, showreel, hidden pages, AI page cards
    PUT  /api/lists/<name>                   reels, motion, credits, experience, side_projects
    PUT  /api/resume?name=                   raw PDF bytes; ligatures are repaired on the way in
    GET  /preview/...                        the built site (pages and assets only)
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
POSTER_EDGE = {"project": 1920, "showreel": 1920, "reel": 1280, "motion": 1280}
POSTER_DIR = {"reel": "posters/reels", "motion": "posters/motion", "showreel": "posters/showreel"}
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


def save_upload(q, body):
    """Raw image bytes -> full + thumb for a photo, or one poster. Does not touch the data files."""
    kind = q.get("kind") or "photo"
    scope = q.get("scope") or "project"
    if kind not in ("photo", "poster") or scope not in ("project", "reel", "motion", "showreel"):
        bad("Unknown upload type.")
    name = q.get("name") or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        bad("That file type is not accepted. Use JPEG, PNG, WebP or HEIC.")
    stem = slugify(Path(name).stem)[:50] or "upload"
    if kind == "photo" or scope == "project":
        slug = (q.get("slug") or "").strip()
        if not SLUG.match(slug) or not any(p.get("slug") == slug for p in read(FILES["projects"])["projects"]):
            bad("Unknown project.")
        folder = REPO / "assets" / "img" / slug
    else:
        folder = REPO / "assets" / "img" / POSTER_DIR[scope]
    folder.mkdir(parents=True, exist_ok=True)
    thumbs = folder / "thumbs"
    if kind == "photo":
        thumbs.mkdir(exist_ok=True)
    base, n = stem, 1
    while (folder / (stem + ".jpg")).exists() or (kind == "photo" and (thumbs / (stem + ".jpg")).exists()):
        n += 1
        stem = "%s-%d" % (base, n)
    src = folder / (".incoming-%s%s" % (stamp(), suffix))
    src.write_bytes(body)
    made = []
    try:
        full = folder / (stem + ".jpg")
        if kind == "photo":
            make_jpeg(src, full, FULL_EDGE, FULL_Q)
            made.append(full)
            thumb = thumbs / (stem + ".jpg")
            make_jpeg(src, thumb, THUMB_EDGE, THUMB_Q)
            made.append(thumb)
            return {"full": str(full.relative_to(REPO)), "thumb": str(thumb.relative_to(REPO)),
                    "alt": q.get("alt", "")}
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
    return {"full": item["full"], "thumb": item.get("thumb") or item["full"], "alt": str(item.get("alt") or "")}


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
    if "domain" in raw:
        cfg["domain"] = clean_domain(raw["domain"])
        touched.append("domain")
    for k in CONFIG_LINES:
        if k in raw:
            cfg[k] = lines_of(raw[k], k)
            touched.append(k)
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


# --------------------------------------------------------------------------- state
def ui_manifest(projects):
    out = {}
    for slug, d in projects.items():
        e = dict(d)
        e["photos"] = [dict(p, v=file_token(p.get("thumb") or p.get("full") or "")) for p in d.get("photos", [])]
        e["videos"] = [dict(v, v=file_token(v["poster"])) if v.get("poster") else dict(v)
                       for v in d.get("videos", [])]
        out[slug] = e
    return out


def with_poster_token(rows):
    return [dict(r, v=file_token(r["poster"])) if r.get("poster") else dict(r) for r in rows]


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
UI_FILES = {"/": "index.html", "/index.html": "index.html", "/app.css": "app.css", "/app.js": "app.js",
            "/project.js": "project.js", "/site.js": "site.js", "/favicon.svg": "favicon.svg"}
PREVIEW_OK = re.compile(r"^(?:[A-Za-z0-9_-]+\.html|work/[A-Za-z0-9_-]+\.html|robots\.txt)$")


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
            if method == "GET":
                if path in UI_FILES:
                    f = UI_DIR / UI_FILES[path]
                    if not f.is_file():
                        bad("The editor UI is missing (tools/cms/%s)." % UI_FILES[path], 404)
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
