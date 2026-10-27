"""Tests for the .nojekyll file and the legacy-redirect stubs build.py writes.

Everything runs in a temp copy of the repo (support.make_temp_repo) with its own
`legacy_redirects` block, so none of it depends on the real data, on which projects
are hidden, or on the real generated pages.
"""
import json
import re
import shutil
import unittest

from support import load, make_release_ready, make_temp_repo, run_build
import build  # noqa: E402  (support puts the repo root on sys.path)

TO = "https://rfcreativeservices.com"
PATHS = ["gallery.html", "gallery/chip-city.html", "gallery/massis.html"]
MARKER = "<!-- legacy-redirect -->"


class RedirectCase(unittest.TestCase):
    def setUp(self):
        self.tmp = make_temp_repo()

    def tearDown(self):
        shutil.rmtree(str(self.tmp), ignore_errors=True)

    def config(self, to=TO, paths=PATHS, **extra):
        """Set (or, with paths=None, remove) the legacy_redirects block of the temp config."""
        cfg = load("site-config.json", self.tmp)
        cfg.pop("legacy_redirects", None)
        if paths is not None:
            cfg["legacy_redirects"] = dict({"to": to, "paths": paths}, **extra)
        self.write_config(cfg)

    def write_config(self, cfg):
        (self.tmp / "site-config.json").write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    def build(self, *args):
        return run_build(self.tmp, *args)

    def ok(self, *args):
        r = self.build(*args)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def text(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    def tree(self):
        """Every file under the temp repo except the symlinked media, with its bytes."""
        out = {}
        for f in sorted(self.tmp.rglob("*")):
            rel = f.relative_to(self.tmp).as_posix()
            if rel.startswith("assets/img") or rel.startswith("assets/video") or not f.is_file():
                continue
            out[rel] = f.read_bytes()
        return out


class TestNojekyll(RedirectCase):
    def test_build_writes_an_empty_nojekyll_at_the_root(self):
        self.config(paths=None)
        self.ok()
        f = self.tmp / ".nojekyll"
        self.assertTrue(f.is_file())
        self.assertEqual(f.stat().st_size, 0)

    def test_a_release_build_writes_it_too(self):
        make_release_ready(self.tmp)
        self.ok("--release")
        self.assertEqual((self.tmp / ".nojekyll").stat().st_size, 0)

    def test_nojekyll_does_not_depend_on_the_domain(self):
        cfg = load("site-config.json", self.tmp)
        cfg["domain"] = ""
        self.write_config(cfg)
        self.ok()
        self.assertFalse((self.tmp / "CNAME").exists())
        self.assertTrue((self.tmp / ".nojekyll").is_file())


class TestStubs(RedirectCase):
    def test_every_path_gets_a_complete_stub_with_the_right_target(self):
        self.config()
        r = self.ok()
        self.assertIn("Legacy redirects: 3 written.", r.stdout)
        self.assertTrue(r.stdout.strip().endswith("BUILD_OK"), r.stdout)
        for path in PATHS:
            target = TO + "/" + path
            page = self.text(path)
            self.assertTrue(page.startswith("<!doctype html>"), path)
            self.assertIn('<html lang="en">', page)
            self.assertIn('<meta charset="utf-8">', page)
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', page)
            self.assertIn("<title>Moved</title>", page)
            self.assertIn('<link rel="canonical" href="%s">' % target, page)
            self.assertIn('<meta http-equiv="refresh" content="0; url=%s">' % target, page)
            self.assertIn('<a href="%s">' % target, page)
            self.assertIn("This page has moved to", page)
            self.assertEqual(page.count(MARKER), 1, path)
            # Nothing of the site's own: no CSS, JS, images or relative links.
            for banned in ("<script", "stylesheet", "<img", "<video", "<iframe", 'href="assets'):
                self.assertNotIn(banned, page, path)
            # Every URL in the page is the absolute target, nothing else.
            self.assertEqual(set(re.findall(r'(?:href="|url=)([^"]+)', page)), {target}, path)

    def test_the_example_target_in_the_brief(self):
        self.config()
        self.ok()
        page = self.text("gallery/chip-city.html")
        self.assertIn("https://rfcreativeservices.com/gallery/chip-city.html", page)
        self.assertEqual(page.count("https://rfcreativeservices.com/gallery/chip-city.html"), 4)

    def test_stubs_carry_the_same_hardening_tags_as_every_other_page(self):
        self.config()
        self.ok()
        shared = build.hardening_meta()
        self.assertIn("Content-Security-Policy", shared)
        for path in PATHS:
            stub = self.text(path)
            self.assertIn(shared, stub, path)
            csp = re.search(r'<meta http-equiv="Content-Security-Policy" content="([^"]+)">', stub)
            assert csp, "no CSP in " + path
            self.assertIn("frame-src https://livid.com", csp.group(1))
            self.assertIn("form-action 'self' https://formspree.io", csp.group(1))
            self.assertIn("object-src 'none'", csp.group(1))
            self.assertIn('name="referrer"', stub)
            self.assertIn('name="robots" content="noai, noimageai"', stub)
        for page in ("index.html", "404.html", "work.html"):
            self.assertIn(shared, self.text(page), page)

    def test_the_stubs_survive_the_hardening_scan_of_every_page(self):
        """The same checks tests/test_site.py runs over the real pages, run over the stubs."""
        self.config()
        self.ok()
        for path in PATHS:
            stub = self.text(path)
            for tag in re.findall(r"<img\b[^>]*>", stub):
                self.assertIn('draggable="false"', tag)
            self.assertNotRegex(stub, r'(?:href|src)="/[^/]')       # no root-relative URLs
            self.assertNotRegex(stub, r"\bAdrian\b|\bFerran\b")     # no unaccented name
            self.assertNotIn("riveraferran.com", stub)

    def test_a_character_that_would_need_escaping_is_refused_not_escaped(self):
        for path in ("a&b.html", "a\"b.html", "a<b.html", "a'b.html"):
            with self.subTest(path=path):
                self.config(paths=[path])
                r = self.build()
                self.assertEqual(r.returncode, 1, path)
                self.assertFalse((self.tmp / path).exists())

    def test_a_release_build_writes_the_stubs_and_check_writes_nothing(self):
        make_release_ready(self.tmp)
        self.config()
        before = self.tree()
        r = self.ok("--check")
        self.assertTrue(r.stdout.strip().endswith("RELEASE READY: nothing left to resolve."), r.stdout)
        self.assertEqual(self.tree(), before, "--check wrote or changed files")
        self.assertFalse((self.tmp / ".nojekyll").exists())
        self.assertFalse((self.tmp / "gallery").exists())
        r = self.ok("--release")
        self.assertIn("Legacy redirects: 3 written.", r.stdout)
        self.assertTrue(r.stdout.strip().endswith("BUILD_OK (release)"), r.stdout)
        for path in PATHS:
            self.assertIn(MARKER, self.text(path))

    def test_a_blocked_release_writes_no_stubs(self):
        self.config()
        cfg = load("site-config.json", self.tmp)
        cfg["subline"] = "[Subline to confirm]"
        self.write_config(cfg)
        r = self.build("--release")
        self.assertEqual(r.returncode, 1)
        self.assertIn("RELEASE BLOCKED", r.stdout)
        self.assertFalse((self.tmp / "gallery").exists())
        self.assertFalse((self.tmp / "gallery.html").exists())
        self.assertFalse((self.tmp / ".nojekyll").exists())

    def test_check_still_validates_the_redirect_config(self):
        make_release_ready(self.tmp)
        self.config(paths=["/gallery.html"])
        r = self.build("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("BUILD FAILED", r.stderr)
        self.assertEqual(list(self.tmp.glob("*.html")), [])

    def test_building_twice_gives_the_same_files(self):
        self.config()
        self.ok()
        first = {p: self.text(p) for p in PATHS}
        self.ok()
        self.assertEqual({p: self.text(p) for p in PATHS}, first)

    def test_no_key_and_no_paths_write_nothing_and_break_nothing(self):
        for label, paths in (("absent", None), ("empty", [])):
            with self.subTest(label):
                self.config(paths=paths)
                r = self.ok()
                self.assertIn("Legacy redirects: 0 written.", r.stdout)
                self.assertFalse((self.tmp / "gallery").exists())
                self.assertFalse((self.tmp / "gallery.html").exists())
                self.assertTrue((self.tmp / "index.html").is_file())
        cfg = load("site-config.json", self.tmp)
        cfg["legacy_redirects"] = {"paths": []}          # no "to" is fine when there is nothing to forward
        self.write_config(cfg)
        self.ok()
        cfg["legacy_redirects"] = {}
        self.write_config(cfg)
        self.ok()


class TestValidation(RedirectCase):
    def fails(self, expect, **config):
        """The build must exit 1 with ONE clear line on stderr, and write nothing at all."""
        before = self.tree()
        self.config(**config)
        cfg_after = self.tree()["site-config.json"]
        before["site-config.json"] = cfg_after
        r = self.build()
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        lines = [l for l in r.stderr.splitlines() if l.strip()]
        self.assertEqual(len(lines), 1, r.stderr)
        self.assertTrue(lines[0].startswith("BUILD FAILED: legacy_redirects"), lines[0])
        self.assertIn(expect, lines[0])
        self.assertIn("Nothing was written.", lines[0])
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.tree(), before, "a failed build wrote or removed files")
        return lines[0]

    def test_to_must_be_an_https_url_with_no_path(self):
        for to in ("http://rfcreativeservices.com", "rfcreativeservices.com", "//rfcreativeservices.com",
                   "https://rfcreativeservices.com/", "https://rfcreativeservices.com/gallery",
                   "https://rfcreativeservices.com?x=1", "https://rfcreativeservices.com#x",
                   "https://user@rfcreativeservices.com", "https://", "ftp://rfcreativeservices.com",
                   "https://rfcreativeservices.com:abc", "https://rfcreative services.com", ""):
            with self.subTest(to=to):
                self.fails("legacy_redirects.to", to=to)
        for to in (None, 5, ["https://rfcreativeservices.com"]):
            with self.subTest(to=to):
                self.fails("legacy_redirects.to", to=to)

    def test_to_may_not_be_this_sites_own_domain(self):
        cfg = load("site-config.json", self.tmp)
        cfg["domain"] = "riveraferran.com"
        self.write_config(cfg)
        for to in ("https://riveraferran.com", "https://www.riveraferran.com"):
            self.fails("own domain", to=to)

    def test_to_with_a_port_is_allowed(self):
        self.config(to="https://rfcreativeservices.com:8443")
        self.ok()
        self.assertIn("https://rfcreativeservices.com:8443/gallery.html", self.text("gallery.html"))

    def test_paths_must_be_relative_html_files_without_tricks(self):
        cases = (
            ("/gallery.html", "relative"),
            ("\\gallery.html", "backslash"),
            ("gallery\\chip-city.html", "backslash"),
            ("../gallery.html", ".."),
            ("gallery/../index.html", ".."),
            ("gallery/..html", ".."),
            ("gallery/chip-city.htm", ".html"),
            ("gallery/chip-city", ".html"),
            ("gallery/chip-city.html ", ".html"),
            ("", "non-empty"),
            ("gallery//x.html", "letters, digits"),
            ("./gallery.html", "letters, digits"),
            (".hidden.html", "letters, digits"),
            ("gallery/a b.html", "letters, digits"),
            ("gallery/ñ.html", "letters, digits"),
            ("gallery/x.html?y=1", ".html"),
            ("gallery/x.html#frag", ".html"),
            ("gallery/%2e%2e.html", "letters, digits"),
            ("work/new.html", "build owns"),
            ("assets/new.html", "build owns"),
            (5, "non-empty"),
            (None, "non-empty"),
        )
        for path, expect in cases:
            with self.subTest(path=path):
                self.fails(expect, paths=["gallery.html", path])

    def test_paths_must_be_a_list_and_the_block_an_object(self):
        self.fails("must be a list", paths="gallery.html")
        self.fails("must be a list", paths={"a": "b"})
        cfg = load("site-config.json", self.tmp)
        cfg["legacy_redirects"] = ["gallery.html"]
        self.write_config(cfg)
        r = self.build()
        self.assertEqual(r.returncode, 1)
        self.assertIn("must be an object", r.stderr)

    def test_paths_must_not_repeat(self):
        self.fails("listed twice", paths=["gallery.html", "gallery/a.html", "gallery.html"])
        self.fails("listed twice", paths=["gallery/a.html", "Gallery/A.html"])    # case-insensitive disks

    def test_a_stub_must_never_replace_a_real_page(self):
        site_pages = set(build.Site(build.load_data(), release=False).build_all())
        # Whichever projects are visible, the fixed pages are always generated.
        for real in ("index.html", "work.html", "reels.html", "ai.html", "about.html",
                     "contact.html", "privacy.html", "terms.html", "404.html"):
            self.assertIn(real, site_pages)
            with self.subTest(real=real):
                self.fails("real page", paths=["gallery.html", real])
                self.assertFalse((self.tmp / real).exists())

    def test_a_case_variant_of_a_real_page_is_refused_too(self):
        self.fails("real page", paths=["INDEX.html"])

    def test_the_hidden_page_the_build_would_delete_is_reserved(self):
        self.fails("real page", paths=["paid-social.html"])

    def test_a_stub_cannot_sit_inside_a_real_page_name(self):
        self.fails("inside a real page", paths=["index.html/x.html"])

    def test_a_real_case_study_name_is_refused(self):
        slug = sorted(p["slug"] for p in load("projects.json", self.tmp)["projects"])[0]
        self.fails("build owns", paths=["work/%s.html" % slug])

    def test_an_existing_file_that_is_not_a_stub_is_never_overwritten(self):
        mine = self.tmp / "gallery" / "mine.html"
        mine.parent.mkdir()
        mine.write_text("<p>hand-made</p>", encoding="utf-8")
        self.fails("not a legacy-redirect stub", paths=["gallery.html", "gallery/mine.html"])
        self.assertEqual(mine.read_text(encoding="utf-8"), "<p>hand-made</p>")

    def test_a_bad_config_leaves_earlier_stubs_alone(self):
        self.config()
        self.ok()
        before = self.tree()
        self.config(paths=PATHS + ["/bad.html"])
        before["site-config.json"] = self.tree()["site-config.json"]
        r = self.build()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self.tree(), before)

    def test_validation_helpers_directly(self):
        self.assertEqual(build.legacy_settings({}), ("", []))
        self.assertEqual(build.legacy_settings({"legacy_redirects": {"to": TO, "paths": []}}), ("", []))
        self.assertEqual(build.legacy_settings({"legacy_redirects": {"to": TO, "paths": ["a.html"]}}),
                         (TO, ["a.html"]))
        with self.assertRaises(build.RedirectConfigError):
            build.legacy_settings({"legacy_redirects": {"to": TO + "/", "paths": ["a.html"]}})


class TestCleanup(RedirectCase):
    def test_a_path_taken_out_of_the_config_loses_its_stub_and_the_empty_directory(self):
        self.config()
        self.ok()
        self.assertTrue((self.tmp / "gallery" / "massis.html").is_file())
        self.config(paths=["gallery.html"])
        r = self.ok()
        self.assertIn("Legacy redirects: 1 written.", r.stdout)
        self.assertTrue((self.tmp / "gallery.html").is_file())
        self.assertFalse((self.tmp / "gallery").exists(), "the emptied gallery/ directory stayed")

    def test_removing_the_key_removes_all_stubs(self):
        self.config()
        self.ok()
        self.config(paths=None)
        self.ok()
        self.assertFalse((self.tmp / "gallery.html").exists())
        self.assertFalse((self.tmp / "gallery").exists())

    def test_cleanup_deletes_only_files_that_carry_the_marker(self):
        self.config()
        self.ok()
        gallery = self.tmp / "gallery"
        keep = {
            gallery / "mine.html": "<p>hand-made, no marker</p>",
            gallery / "note.txt": "legacy-redirect " + MARKER + " in a text file",
            self.tmp / "notes.html": "<p>unrelated root file</p>",
            self.tmp / "assets" / "docs" / "decoy.html": MARKER,          # assets/ is never scanned
        }
        for f, text in keep.items():
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
        real_before = {p: self.text(p) for p in ("index.html", "work.html", "404.html")}
        self.config(paths=["gallery.html"])
        self.ok()
        for f, text in keep.items():
            self.assertEqual(f.read_text(encoding="utf-8"), text, f.name)
        self.assertTrue(gallery.is_dir(), "a directory that still holds other files must stay")
        for stale in ("chip-city.html", "massis.html"):
            self.assertFalse((gallery / stale).exists(), stale)
        self.assertTrue((self.tmp / "gallery.html").is_file())
        for p, text in real_before.items():
            self.assertEqual(self.text(p), text, p)

    def test_cleanup_never_touches_real_pages_even_if_they_carry_the_marker(self):
        self.config(paths=None)
        self.ok()
        index = self.tmp / "index.html"
        tampered = index.read_text(encoding="utf-8") + "\n" + MARKER + "\n"
        index.write_text(tampered, encoding="utf-8")
        # index.html is regenerated by the build, so it is rewritten (never deleted).
        self.ok()
        self.assertTrue(index.is_file())
        self.assertNotIn(MARKER, index.read_text(encoding="utf-8"))

    def test_a_nested_stub_directory_is_removed_only_when_empty(self):
        self.config(paths=["old/deep/a.html", "old/b.html", "keepme.html"])
        self.ok()
        self.config(paths=["keepme.html"])
        self.ok()
        self.assertFalse((self.tmp / "old").exists())
        self.assertTrue((self.tmp / "keepme.html").is_file())


if __name__ == "__main__":
    unittest.main()
