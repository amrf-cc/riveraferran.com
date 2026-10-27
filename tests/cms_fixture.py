"""A small, self-contained portfolio site for the editor's tests.

Nothing here reads the real content: the tests must not break when Adrián edits a title, and a test
that writes (uploads, deletes, reorders) must never reach his files. make_site() writes the four data
files, the few assets they name, and copies build.py and templates/ so the repo can be built.
"""
import importlib.util
import json
import os
import shutil
import struct
import zlib
from pathlib import Path

from support import ROOT

CMS_PATH = ROOT / "tools" / "cms.py"


def png(w=8, h=8, rgb=(200, 60, 30)):
    """A valid PNG without any imaging library."""
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(kind, data):
        body = struct.pack(">I", len(data)) + kind + data
        return body + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def pdf_bytes(text="Hello first Certificate Effects"):
    """A one-page PDF, or None when PyMuPDF is not installed."""
    try:
        import fitz
    except ImportError:
        return None
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


CATEGORIES = [{"key": "food", "label": "Food & Beverage", "personal": False},
              {"key": "music", "label": "Music Videos", "personal": False}]
CAPABILITIES = [{"tag": "product", "label": "Studio product", "line": "Product."},
                {"tag": "social", "label": "Short-form social", "line": "Social."},
                {"tag": "ai", "label": "AI-assisted creative", "line": "AI."}]


def _project(slug, client, **kw):
    p = {"slug": slug, "client": client, "featured": False, "title": client + " title", "oneliner": client + " one-liner.",
         "role": "Photographer", "years": "2024", "location": "New York City", "tags": ["product"],
         "brief": "Brief for " + client + ".", "did": ["Shot it."], "scope": [], "result": "", "category": "food"}
    p.update(kw)
    return p


def _photo(slug, n):
    return {"full": "assets/img/%s/%02d.jpg" % (slug, n), "thumb": "assets/img/%s/thumbs/%02d.jpg" % (slug, n),
            "alt": "%s %d" % (slug, n)}


def site_data():
    config = {
        "name": "Test Person", "title": "Photo & Video", "headline": "Headline.", "subline": "Subline.",
        "based": "New York City", "email": "test@example.com", "domain": "example.com",
        "formspree_endpoint": "https://formspree.io/f/test", "instagram": "", "linkedin": "",
        "resume_pdf": "assets/docs/Resume.pdf",
        "showreel": {"vertical": {"livid": "", "mp4": "", "poster": "", "ratio": "9 / 16", "title": "V"},
                     "horizontal": {"livid": "", "mp4": "", "poster": "", "ratio": "16 / 9", "title": "H"},
                     "placeholder": "", "hidden": True},
        "hero_image": "assets/img/hero.jpg", "hero_image_phone": "assets/img/hero.jpg", "hero_caption": "A caption.",
        "hero_cutout": "assets/img/hero-cutout.png", "masthead_labels": ["", "", ""],
        "bio": ["Bio one.", "Bio two."], "cta": "Hire me.", "capabilities": CAPABILITIES,
        "collaborations": {"label": "Collaborations", "names": ["Brand A", "Brand B"]},
        "tools": ["Premiere Pro"], "education": "BA, Communication", "certifications": ["Certificate"],
        "languages": "English and Spanish",
        "side_projects": [{"label": "Side project", "line": "A line.", "href": "https://example.com/side"}],
        "page_intros": {"work": "Work intro.", "reels": "Reels intro.", "ai": "AI intro.", "paid": "", "about": "About intro."},
        "experience": [{"role": "Creator", "org": "Company", "years": "2020\u20132024", "place": "New York"}],
        "ai_label": "AI-assisted", "categories": CATEGORIES, "hidden_pages": ["paid-social"],
    }
    projects = {"projects": [
        _project("alpha", "Alpha", featured=1),
        _project("beta", "Beta", featured=2),
        _project("gamma", "Gamma", category="music"),
        _project("ai-production-assistant", "AI assistant", featured=3, category="", tags=["ai"],
                 standalone="ai.html#assistant", scope=["Scope line."], result="Result line."),
        _project("rodowita-water", "Rodowita", tags=["ai"], ai_steps="Real shot, AI step, final."),
    ]}
    manifest = {"projects": {
        "alpha": {"photos": [_photo("alpha", 1), _photo("alpha", 2)],
                  "videos": [{"title": "Alpha film", "mp4": "", "ratio": "9 / 16", "ai": True,
                              "livid": "https://livid.com/embed/AAAAAA11?livid_logo=0&fullscreen=1&title=0",
                              "poster": "assets/img/alpha/film.jpg"}]},
        "beta": {"photos": [_photo("beta", 1)], "videos": []},
        "gamma": {"photos": [], "videos": []},
        "ai-production-assistant": {"photos": [], "videos": []},
        "rodowita-water": {"photos": [_photo("rodowita-water", 1)], "videos": [],
                           "sets": [{"title": "Studio shot → final",
                                     "steps": [{"label": "Studio shot",
                                                "image": "assets/img/rodowita-water/01.jpg"},
                                               {"label": "Final",
                                                "image": "assets/img/rodowita-water/02.jpg",
                                                "ai": True}]}]},
    }}
    media = {
        "reels": [{"slug": "alpha-film", "title": "Alpha film", "brand": "Alpha", "project": "alpha",
                   "format": "Brand reel", "ratio": "9 / 16", "year": "2024", "ai": True, "mp4": "", "poster": "",
                   "livid": "https://livid.com/embed/BBBBBB22?livid_logo=0&fullscreen=1&title=1"}],
        "reel_formats_note": "", "motion": [], "paid_social": {"intro": "", "video_ads": [], "static_ads": []},
        "ai_process": ["Brief"], "ai_tools_line": "Tools line.", "ai_live": ["rodowita-water"],
        "credits_note": "Production credits", "credits_confirm": False,
        "credits": [{"artist": "Artist", "work": "Work", "role": "Producer", "year": "2020"}],
    }
    return {"site-config.json": config, "projects.json": projects, "assets-manifest.json": manifest, "media.json": media}


ASSET_FILES = ["assets/img/hero.jpg", "assets/img/hero-cutout.png", "assets/img/alpha/film.jpg",
               "assets/img/alpha/01.jpg", "assets/img/alpha/02.jpg", "assets/img/alpha/thumbs/01.jpg",
               "assets/img/alpha/thumbs/02.jpg", "assets/img/beta/01.jpg", "assets/img/beta/thumbs/01.jpg",
               "assets/img/rodowita-water/01.jpg", "assets/img/rodowita-water/02.jpg",
               "assets/img/rodowita-water/thumbs/01.jpg", "assets/img/rodowita-water/thumbs/02.jpg",
               "assets/img/posters/reels/spare.jpg"]


def make_site(tmp):
    """(Re)write the fixture site into `tmp`. Safe to call again to reset it."""
    tmp = Path(tmp)
    for child in tmp.iterdir():
        shutil.rmtree(child) if child.is_dir() else child.unlink()
    shutil.copy2(str(ROOT / "build.py"), str(tmp / "build.py"))
    shutil.copytree(str(ROOT / "templates"), str(tmp / "templates"))
    for name, data in site_data().items():
        (tmp / name).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for rel in ASSET_FILES:
        f = tmp / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(png(8 + len(rel) % 5, 8))
    docs = tmp / "assets" / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "Resume.pdf").write_bytes(pdf_bytes() or b"%PDF-1.4\n%fixture\n")
    return tmp


def load_cms(repo):
    """tools/cms.py bound to `repo` (it reads CMS_REPO once, at import)."""
    old = os.environ.get("CMS_REPO")
    os.environ["CMS_REPO"] = str(repo)
    try:
        spec = importlib.util.spec_from_file_location("cms_under_test_%d" % abs(hash(str(repo))), str(CMS_PATH))
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if old is None:
            os.environ.pop("CMS_REPO", None)
        else:
            os.environ["CMS_REPO"] = old


# --------------------------------------------------------------------------- git: a site with a bare origin
# Publish tests use REAL git and a local bare repository as `origin`; they never touch the network or
# github.com. The site is built, committed and pushed once per process; each test gets a copy.
import subprocess
import tempfile

FIXTURE_TESTS = '''import unittest


class TestFixture(unittest.TestCase):
    def test_passes(self):
        self.assertTrue(True)
'''


def git_env():
    env = {k: v for k, v in os.environ.items()
           if k != "PYTHONPATH" and k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git_try(cwd, *args):
    """Run git in `cwd` -> (returncode, stdout stripped, stderr stripped)."""
    r = subprocess.run(["git"] + list(args), cwd=str(cwd), capture_output=True, text=True, env=git_env())
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def git(cwd, *args):
    """Run git in `cwd`; return stdout stripped. A failure raises."""
    rc, out, err = git_try(cwd, *args)
    if rc != 0:
        raise AssertionError("git %s failed in %s: %s" % (" ".join(args), cwd, err or out))
    return out


class GitSite:
    """A built, release-ready fixture site committed on main and pushed to a local bare `origin`."""

    def __init__(self):
        from support import run_build
        self.root = Path(tempfile.mkdtemp(prefix="publish-template-"))
        self.site, self.bare = self.root / "site", self.root / "origin.git"
        self.site.mkdir()
        make_site(self.site)
        (self.site / "tests").mkdir()
        (self.site / "tests" / "test_fixture.py").write_text(FIXTURE_TESTS, encoding="utf-8")
        (self.site / ".gitignore").write_text((ROOT / ".gitignore").read_text(encoding="utf-8"), encoding="utf-8")
        built = run_build(self.site, "--release")
        assert built.returncode == 0, built.stdout + built.stderr
        git(self.root, "init", "--bare", "-q", "-b", "main", str(self.bare))
        git(self.site, "init", "-q", "-b", "main")
        git(self.site, "config", "user.name", "Publish Test")
        git(self.site, "config", "user.email", "publish-test@example.invalid")
        git(self.site, "config", "commit.gpgsign", "false")
        git(self.site, "add", "-A")
        git(self.site, "commit", "-q", "-m", "Fixture site")
        git(self.site, "remote", "add", "origin", str(self.bare))
        git(self.site, "push", "-q", "-u", "origin", "main")

    def copy_to(self, site, bare):
        """Reset `site` and `bare` (made if missing) to the pristine fixture, origin pointing at `bare`."""
        for dst in (Path(site), Path(bare)):
            dst.mkdir(parents=True, exist_ok=True)
            for child in dst.iterdir():
                shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        shutil.copytree(str(self.site), str(site), symlinks=True, dirs_exist_ok=True)
        shutil.copytree(str(self.bare), str(bare), symlinks=True, dirs_exist_ok=True)
        git(site, "remote", "set-url", "origin", str(bare))

    def close(self):
        shutil.rmtree(str(self.root), ignore_errors=True)
