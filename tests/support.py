"""Shared helpers for the site tests. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOP_PAGES = ("index.html", "work.html", "reels.html", "ai.html", "paid-social.html",
             "about.html", "contact.html", "privacy.html", "terms.html")


def load(name, root=ROOT):
    with open(root / name, encoding="utf-8") as f:
        return json.load(f)


def read(path):
    return Path(path).read_text(encoding="utf-8")


def is_ph(v):
    return isinstance(v, str) and len(v) >= 2 and v.startswith("[") and v.endswith("]")


def is_draft_build():
    return 'class="draft-banner"' in read(ROOT / "index.html")


def visible_projects(draft=None):
    if draft is None:
        draft = is_draft_build()
    return [p for p in load("projects.json")["projects"]
            if not p.get("hidden") and (draft or not p.get("draft_only"))]


def built_pages(include_404=False):
    pages = [ROOT / n for n in TOP_PAGES if (ROOT / n).exists()]
    pages += sorted((ROOT / "work").glob("*.html"))
    if include_404:
        pages.append(ROOT / "404.html")
    return pages


class LinkExtractor(HTMLParser):
    """Every local URL a page asks the browser (or main.js) to load."""

    ATTRS = ("href", "src", "poster", "data-full", "data-src", "data-poster")

    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        for a in self.ATTRS:
            if d.get(a):
                self.links.append(d[a])
        srcset = (d.get("srcset") or "").strip()
        if srcset and not srcset.startswith("data:"):
            for cand in srcset.split(","):
                token = cand.strip().split()[0] if cand.strip() else ""
                if token and not token.startswith("data:"):
                    self.links.append(token)


def local_links(page):
    parser = LinkExtractor()
    parser.feed(read(page))
    out = []
    for link in parser.links:
        if link.startswith(("http://", "https://", "mailto:", "#", "data:", "//")):
            continue
        out.append((link, (page.parent / link.split("?")[0].split("#")[0]).resolve()))
    return out


class MetaTagParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta = {}

    def handle_starttag(self, tag, attrs):
        if tag != "meta":
            return
        d = dict(attrs)
        key = d.get("property") or d.get("name")
        if key and "content" in d:
            self.meta.setdefault(key, []).append(d["content"])


def meta_tags(page):
    p = MetaTagParser()
    p.feed(read(page))
    return p.meta


# ---------------------------------------------------------------------------
# Temp copies of the repo: release builds are exercised there, never on the
# real data files or the real generated pages.
# ---------------------------------------------------------------------------

DATA_FILES = ("site-config.json", "projects.json", "media.json", "assets-manifest.json")


def make_temp_repo():
    tmp = Path(tempfile.mkdtemp(prefix="portfolio-build-"))
    shutil.copy2(str(ROOT / "build.py"), str(tmp / "build.py"))
    for name in DATA_FILES:
        shutil.copy2(str(ROOT / name), str(tmp / name))
    shutil.copytree(str(ROOT / "templates"), str(tmp / "templates"))
    (tmp / "assets").mkdir()
    for sub in ("css", "js", "fonts"):
        shutil.copytree(str(ROOT / "assets" / sub), str(tmp / "assets" / sub))
    # Media is read-only input: link it rather than copy ~200MB.
    for sub in ("img", "video"):
        os.symlink(str(ROOT / "assets" / sub), str(tmp / "assets" / sub))
    return tmp


def run_build(root, *args):
    return subprocess.run([sys.executable, str(root / "build.py")] + list(args),
                          cwd=str(root), capture_output=True, text=True, timeout=600)


def strip_drafts(node):
    """A copy of the data with every draft marker resolved: placeholders lose
    their brackets, confirm lists are dropped, draft_only items removed."""
    if isinstance(node, dict):
        out = {}
        for k, v in node.items():
            if k == "confirm":
                continue
            if k == "credits_confirm":
                out[k] = False
                continue
            out[k] = strip_drafts(v)
        return out
    if isinstance(node, list):
        return [strip_drafts(v) for v in node
                if not (isinstance(v, dict) and v.get("draft_only"))]
    if is_ph(node):
        return node[1:-1].strip()
    return node


def make_release_ready(root):
    for name in DATA_FILES:
        data = strip_drafts(load(name, root))
        if name == "site-config.json":
            data["domain"] = "example.com"
            data["resume_pdf"] = "assets/resume.pdf"
            for slot in ("vertical", "horizontal"):
                data["showreel"][slot]["livid"] = "https://livid.com/embed/test-" + slot
        (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "assets" / "resume.pdf").write_bytes(b"%PDF-1.4 test\n")


def html_pages(root):
    return sorted(root.glob("*.html")) + sorted((root / "work").glob("*.html"))


NAME_UNACCENTED = re.compile(r"\bAdrian\b|\bFerran\b", re.IGNORECASE)
EMAIL_OR_DOMAIN = re.compile(r"[\w.+-]+@[\w.-]+|[\w.-]*riveraferran\.com", re.IGNORECASE)
