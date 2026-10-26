#!/usr/bin/env python3
"""Site content editor for the portfolio (127.0.0.1 only, stdlib only).

    python3 tools/cms.py [port]      # default 8820

Endpoints
    GET  /                 the editor page (tools/cms.html)
    GET  /api/content      {config, categories, projects, manifest, build}
    POST /api/project      create or update one project, then rebuild
    POST /api/config       update site-copy keys, then rebuild
    POST /api/build        rebuild without changing anything

Every write keeps a copy of the previous file in tools/backups/ (20 kept) and is
rolled back when the rebuild fails, so a bad edit can never leave the site broken.
"""
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(os.environ.get("CMS_REPO", Path(__file__).resolve().parent.parent)).resolve()
BACKUPS = REPO / "tools" / "backups"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("CMS_PORT", 8820))
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_BODY = 1_000_000
KEEP_BACKUPS = 20

# First day the editor is used: the files it is allowed to write.
FILES = {"projects": REPO / "projects.json", "config": REPO / "site-config.json"}
MANIFEST = REPO / "assets-manifest.json"
TRASH = REPO / "tools" / "removed-media"
PHOTO_FULL_EDGE = 2000
PHOTO_THUMB_EDGE = 852

# Project fields the editor owns, with their type.
TEXT = ["client", "title", "oneliner", "role", "years", "location", "brief", "result", "category", "bts"]
LISTS = ["did", "scope", "tags"]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def backup(path):
    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUPS / ("%s-%s.json" % (path.stem, stamp))
    shutil.copy2(path, dest)
    old = sorted(BACKUPS.glob("%s-*.json" % path.stem))
    for f in old[:-KEEP_BACKUPS]:
        f.unlink(missing_ok=True)
    return dest


def build():
    """Run the site build. Returns (ok, output)."""
    r = subprocess.run([sys.executable, "build.py"], cwd=str(REPO), capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip().splitlines()
    return r.returncode == 0, "\n".join(out[-6:])


def write_json(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def manifest_counts():
    try:
        man = read(MANIFEST)["projects"]
    except (OSError, ValueError, KeyError):
        return {}
    return {slug: {"photos": len(d.get("photos", [])), "videos": len(d.get("videos", []))} for slug, d in man.items()}


def manifest_media():
    try:
        man = read(MANIFEST)["projects"]
    except (OSError, ValueError, KeyError):
        return {}
    return {slug: {"photos": d.get("photos", []), "videos": d.get("videos", [])} for slug, d in man.items()}


def content():
    cfg = read(FILES["config"])
    projects = read(FILES["projects"])["projects"]
    ok, out = build()
    return {
        "config": cfg,
        "categories": cfg.get("categories", []),
        "projects": projects,
        "manifest": manifest_counts(),
        "media": manifest_media(),
        "build": {"ok": ok, "output": out},
    }


def clean_project(raw, existing=None):
    """Validate an incoming project payload against the stored shape."""
    p = dict(existing or {})
    for k in TEXT:
        if k in raw:
            v = raw[k]
            p[k] = v if isinstance(v, str) else ""
    for k in LISTS:
        if k in raw:
            v = raw[k]
            if isinstance(v, str):
                v = [x.strip() for x in v.splitlines()]
            p[k] = [str(x).strip() for x in v if str(x).strip()]
    if "featured" in raw:
        p["featured"] = bool(raw["featured"])
    if not p.get("client", "").strip():
        raise ValueError("Client is required.")
    if not p.get("title", "").strip():
        raise ValueError("Title is required.")
    slug = (raw.get("slug") or p.get("slug") or "").strip().lower()
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", (p["client"] + " " + p["title"]).lower()).strip("-")[:40]
    if not SLUG.match(slug):
        raise ValueError("Slug must be lowercase letters, numbers and dashes.")
    p["slug"] = slug
    p.setdefault("category", "food-beverage")
    p.setdefault("featured", False)
    # Keep the field order the build expects.
    order = ["slug", "client", "featured", "title", "oneliner", "role", "years", "location",
             "tags", "brief", "did", "scope", "result", "bts", "category"]
    return {k: p[k] for k in order if k in p}


def save_project(raw):
    data = read(FILES["projects"])
    projects = data["projects"]
    slug = (raw.get("slug") or "").strip().lower()
    idx = next((i for i, p in enumerate(projects) if p["slug"] == slug), None) if slug else None
    p = clean_project(raw, projects[idx] if idx is not None else None)
    if idx is None:
        if any(x["slug"] == p["slug"] for x in projects):
            raise ValueError("A project with slug '%s' already exists." % p["slug"])
        projects.append(p)
        action = "created"
    else:
        projects[idx] = p
        action = "updated"
    dest = backup(FILES["projects"])
    write_json(FILES["projects"], data)
    ok, out = build()
    if not ok:
        shutil.copy2(dest, FILES["projects"])
        build()
        raise RuntimeError("The site did not build. Reverted. %s" % out)
    return {"ok": True, "action": action, "slug": p["slug"], "build": out}


EDITABLE_CONFIG = ["name", "title", "headline", "subline", "based", "email", "domain",
                   "resume_pdf", "cta", "education", "languages", "instagram", "linkedin",
                   "ai_label", "hero_caption"]


def save_config(raw):
    cfg = read(FILES["config"])
    touched = []
    for k, v in raw.items():
        if k in EDITABLE_CONFIG and isinstance(v, str):
            cfg[k] = v
            touched.append(k)
        elif k == "bio" and isinstance(v, list):
            cfg["bio"] = [str(x).strip() for x in v if str(x).strip()]
            touched.append(k)
        elif k == "page_intros" and isinstance(v, dict):
            cfg["page_intros"] = {str(a): str(b) for a, b in v.items()}
            touched.append(k)
        elif k == "collaborations" and isinstance(v, dict):
            names = v.get("names")
            if isinstance(names, str):
                names = [x.strip() for x in names.splitlines() if x.strip()]
            cfg.setdefault("collaborations", {})["names"] = names
            touched.append("collaborations")
    if not touched:
        raise ValueError("Nothing to save.")
    dest = backup(FILES["config"])
    write_json(FILES["config"], cfg)
    ok, out = build()
    if not ok:
        shutil.copy2(dest, FILES["config"])
        build()
        raise RuntimeError("The site did not build. Reverted. %s" % out)
    return {"ok": True, "changed": touched, "build": out}


def safe_asset(rel):
    """Resolve a repo-relative asset path, refusing anything outside assets/."""
    if not rel or ".." in Path(rel).parts:
        raise ValueError("Bad path.")
    p = (REPO / rel).resolve()
    if not str(p).startswith(str((REPO / "assets").resolve()) + os.sep):
        raise ValueError("Path outside assets/.")
    return p


def sips_resize(path, edge):
    """Shrink an image in place to a maximum edge, keeping the aspect ratio."""
    subprocess.run(["sips", "-Z", str(edge), str(path), "--out", str(path)],
                   check=True, capture_output=True)


def media_entry(man, slug):
    if not SLUG.match(slug or ""):
        raise ValueError("Unknown project.")
    e = man["projects"].setdefault(slug, {})
    e.setdefault("photos", [])
    e.setdefault("videos", [])
    return e


def trash_files(paths):
    """Move files out of assets/ instead of deleting them."""
    TRASH.mkdir(parents=True, exist_ok=True)
    for rel in paths:
        try:
            src = safe_asset(rel)
        except ValueError:
            continue
        if src.exists() and src.is_file():
            dest = TRASH / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), src.name))
            shutil.move(str(src), str(dest))


def media_edit(payload):
    slug = (payload.get("slug") or "").strip()
    kind = payload.get("kind")
    action = payload.get("action")
    man = read(MANIFEST)
    entry = media_entry(man, slug)
    key = "photos" if kind == "photo" else "videos" if kind == "video" else None
    if not key:
        raise ValueError("kind must be photo or video.")
    items = entry[key]
    idx = payload.get("index")
    if action == "add":
        new = payload.get("items") or ([payload.get("item")] if payload.get("item") else [])
        if not new:
            raise ValueError("Nothing to add.")
        items.extend(new)
    elif action == "remove":
        if idx is None or not (0 <= int(idx) < len(items)):
            raise ValueError("No such item.")
        gone = items.pop(int(idx))
        files = [gone.get("full"), gone.get("thumb")] if key == "photos" else [gone.get("poster")]
        trash_files([f for f in files if f])
    elif action == "move":
        a, b = int(payload.get("from", -1)), int(payload.get("to", -1))
        if not (0 <= a < len(items) and 0 <= b < len(items)):
            raise ValueError("Bad move.")
        items.insert(b, items.pop(a))
    elif action == "update":
        if idx is None or not (0 <= int(idx) < len(items)):
            raise ValueError("No such item.")
        row = items[int(idx)]
        for k in ("alt", "title", "livid", "poster", "ratio"):
            if k in payload:
                row[k] = str(payload[k])
        if key == "videos" and payload.get("livid"):
            row["livid"] = embed_url(str(payload["livid"]))
    else:
        raise ValueError("Unknown action.")
    dest = backup(MANIFEST)
    write_json(MANIFEST, man)
    ok, out = build()
    if not ok:
        shutil.copy2(dest, MANIFEST)
        build()
        raise RuntimeError("The site did not build. Reverted. %s" % out)
    return {"ok": True, "photos": len(entry["photos"]), "videos": len(entry["videos"]), "build": out}


def embed_url(value):
    """Accept an embed link, a watch link or a bare id, return the embed link with our params."""
    v = value.strip()
    vid = ""
    for pattern in (r"/embed/([A-Za-z0-9_-]+)", r"/([A-Za-z0-9_-]{6,})/?$"):
        m = re.search(pattern, v)
        if m:
            vid = m.group(1)
            break
    if not vid:
        vid = v
    vid = re.sub(r"[^A-Za-z0-9_-]", "", vid)
    if len(vid) < 6:
        raise ValueError("That does not look like a Livid video id or link.")
    return "https://livid.com/embed/%s?livid_logo=0&fullscreen=1&title=1" % vid


def save_upload(params, body):
    slug = (params.get("slug") or "").strip()
    kind = params.get("kind") or "photo"
    if not SLUG.match(slug):
        raise ValueError("Unknown project.")
    name = params.get("name") or "upload"
    stem = re.sub(r"[^a-z0-9]+", "-", Path(name).stem.lower()).strip("-")[:50] or "upload"
    suffix = Path(name).suffix.lower()
    if suffix not in (".jpg", ".jpeg", ".png", ".webp"):
        raise ValueError("Only jpg, png and webp are accepted.")
    folder = REPO / "assets" / "img" / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "thumbs").mkdir(exist_ok=True)
    base, n = stem, 1
    while (folder / (stem + suffix)).exists():
        n += 1
        stem = "%s-%d" % (base, n)
    dest = folder / (stem + suffix)
    dest.write_bytes(body)
    sips_resize(dest, PHOTO_FULL_EDGE if kind == "photo" else 2400)
    out = {"full": str(dest.relative_to(REPO))}
    if kind == "photo":
        thumb = folder / "thumbs" / (stem + ".jpg")
        shutil.copy2(dest, thumb)
        sips_resize(thumb, PHOTO_THUMB_EDGE)
        out["thumb"] = str(thumb.relative_to(REPO))
        out["alt"] = params.get("alt", "")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "PortfolioCMS/1"

    def log_message(self, format, *args):
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

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

    def do_GET(self):
        if not self._guard():
            return
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = (Path(__file__).parent / "cms.html").read_bytes()
            return self._send(200, body, "text/html; charset=utf-8")
        if path == "/media":
            q = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            try:
                f = safe_asset(q.get("p", ""))
            except ValueError as e:
                return self._json(403, {"ok": False, "error": str(e)})
            if not f.exists() or not f.is_file():
                return self._json(404, {"ok": False, "error": "No such file."})
            ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
            return self._send(200, f.read_bytes(), ctype)
        if path == "/api/content":
            try:
                return self._json(200, content())
            except Exception as e:  # noqa: BLE001 - report, never crash the editor
                return self._json(500, {"ok": False, "error": str(e)})
        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self):
        if not self._guard():
            return
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY * 20:
            return self._json(400, {"ok": False, "error": "Empty or oversized request."})
        u = urlparse(self.path)
        if u.path == "/api/upload":
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                return self._json(200, save_upload(q, self.rfile.read(n)))
            except ValueError as e:
                return self._json(400, {"ok": False, "error": str(e)})
            except Exception as e:  # noqa: BLE001
                return self._json(500, {"ok": False, "error": str(e)})
        try:
            raw = json.loads(self.rfile.read(n))
        except ValueError:
            return self._json(400, {"ok": False, "error": "Bad JSON."})
        if not isinstance(raw, dict):
            return self._json(400, {"ok": False, "error": "Expected an object."})
        path = u.path
        try:
            if path == "/api/project":
                return self._json(200, save_project(raw))
            if path == "/api/config":
                return self._json(200, save_config(raw))
            if path == "/api/media":
                return self._json(200, media_edit(raw))
            if path == "/api/build":
                ok, out = build()
                return self._json(200, {"ok": ok, "build": out})
        except ValueError as e:
            return self._json(400, {"ok": False, "error": str(e)})
        except RuntimeError as e:
            return self._json(422, {"ok": False, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            return self._json(500, {"ok": False, "error": str(e)})
        self._json(404, {"ok": False, "error": "Not found"})


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print("Site content editor on http://127.0.0.1:%d/  (repo %s)" % (PORT, REPO), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
