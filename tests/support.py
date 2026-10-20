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


# Release-ready stand-ins for the gaps the TEMPLATES fill with generated
# placeholders (no data string behind them), so the clean release can pass.
RELEASE_AI_STEPS = "Real shot, AI step, final ad (release test)."
RELEASE_VIDEO_AD = {"title": "Release test video ad", "campaign": "Test campaign", "ratio": "9 / 16",
                    "mp4": "assets/video/motion/pride.mp4", "poster": "assets/img/posters/motion/pride.jpg"}
RELEASE_STATIC_AD = {"title": "Release test static ad", "campaign": "Test campaign", "ratio": "4 / 5",
                     "image": "assets/img/posters/motion/world-water-day.jpg"}


def make_release_ready(root):
    import build  # the repo's build.py (support puts ROOT on sys.path)
    for name in DATA_FILES:
        data = strip_drafts(load(name, root))
        if name == "site-config.json":
            data["domain"] = "example.com"
            data["resume_pdf"] = "assets/resume.pdf"
            for slot in ("vertical", "horizontal"):
                data["showreel"][slot]["livid"] = "https://livid.com/embed/test-" + slot
        elif name == "projects.json":
            # ai.html "Ran live": a project without ai_steps gets a generated placeholder.
            for p in data["projects"]:
                if p.get("slug") in build.AI_LIVE and not p.get("ai_steps"):
                    p["ai_steps"] = RELEASE_AI_STEPS
        elif name == "media.json":
            # paid-social.html: an empty ad list gets a generated placeholder.
            ps = data.setdefault("paid_social", {})
            if not ps.get("video_ads"):
                ps["video_ads"] = [dict(RELEASE_VIDEO_AD)]
            if not ps.get("static_ads"):
                ps["static_ads"] = [dict(RELEASE_STATIC_AD)]
        (root / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (root / "assets" / "resume.pdf").write_bytes(b"%PDF-1.4 test\n")


WORK_ITEM = re.compile(
    r'<h2 class="work-group[^"]*" data-tags="([^"]*)" data-filter-heading>(.*?)</h2>'
    r'|<article class="card" data-tags="([^"]*)">\s*<a class="thumb[^"]*" href="work/([^"]+)\.html"(.*?)</article>',
    re.S)


def work_sequence(text):
    """The Work grid in document order: ("heading", key, inner_html) and
    ("card", slug, data_tags, card_html) tuples."""
    out = []
    for m in WORK_ITEM.finditer(text):
        if m.group(2) is not None:
            out.append(("heading", m.group(1), m.group(2)))
        else:
            out.append(("card", m.group(4), m.group(3), m.group(0)))
    return out


def expected_work_groups(projects, categories):
    """[(key or "", [slugs])] the Work page should show, in display order."""
    keys = [c["key"] for c in categories]
    out = []
    for k in keys:
        members = [p["slug"] for p in projects if p.get("category") == k]
        if members:
            out.append((k, members))
    rest = [p["slug"] for p in projects if p.get("category") not in keys]
    if rest:
        out.append(("", rest))
    return out


def html_pages(root):
    return sorted(root.glob("*.html")) + sorted((root / "work").glob("*.html"))


NAME_UNACCENTED = re.compile(r"\bAdrian\b|\bFerran\b", re.IGNORECASE)
EMAIL_OR_DOMAIN = re.compile(r"[\w.+-]+@[\w.-]+|[\w.-]*riveraferran\.com", re.IGNORECASE)
