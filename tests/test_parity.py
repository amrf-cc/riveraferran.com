"""The hard constraint: copy and SEO are additive, so an untouched site must
still build the committed pages byte for byte.

Every build here runs in a temp copy of the repo (support.make_temp_repo), never
in the repository itself, and always with --release: the committed pages are a
release build. Test 2 is the one that catches a registry default that has drifted
from the literal it replaced; test 1 would only catch the drift after the fact.
"""
import json
import re
import shutil
import unittest

import support

ROOT = support.ROOT
# The four status messages are the one place where an override adds markup
# (data-msg-* on the contact form), so they are checked on their own below.
MSG_KEYS = ("contact.msg.opening", "contact.msg.sending", "contact.msg.sent", "contact.msg.error")
MSG_ATTRS = ("data-msg-opening", "data-msg-sending", "data-msg-sent", "data-msg-error")


def temp_repo():
    """A temp copy of the repo with the data files exactly as committed, so the
    page set is the one the committed pages were generated from."""
    tmp = support.make_temp_repo()
    for name in support.DATA_FILES:
        shutil.copy2(str(ROOT / name), str(tmp / name))
    return tmp


def set_config(tmp, copy=None, seo=None):
    path = tmp / "site-config.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if copy is not None:
        cfg["copy"] = copy
    if seo is not None:
        cfg["seo"] = seo
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class ParityCase(unittest.TestCase):
    def build(self, copy=None, seo=None):
        if copy is None and seo is None:
            tmp = temp_repo()
        else:
            tmp = temp_repo()
            set_config(tmp, copy=copy, seo=seo)
        self.addCleanup(shutil.rmtree, str(tmp), True)
        result = support.run_build(tmp, "--release")
        self.assertEqual(result.returncode, 0,
                         "the release build failed:\n%s\n%s" % (result.stdout, result.stderr))
        return tmp

    def differences(self, tmp):
        """{relative page: (built bytes, committed bytes)} for every page that
        differs, is missing, or was not built."""
        out = {}
        built = {f.relative_to(tmp).as_posix(): f for f in support.html_pages(tmp)}
        committed = {f.relative_to(ROOT).as_posix(): f for f in support.html_pages(ROOT)}
        self.assertTrue(committed, "no committed pages found to compare against")
        for rel in sorted(set(built) | set(committed)):
            if rel not in built:
                out[rel] = (None, committed[rel].read_bytes())
            elif rel not in committed:
                out[rel] = (built[rel].read_bytes(), None)
            elif built[rel].read_bytes() != committed[rel].read_bytes():
                out[rel] = (built[rel].read_bytes(), committed[rel].read_bytes())
        return out

    def assert_same(self, tmp, why):
        diffs = self.differences(tmp)
        self.assertEqual(sorted(diffs), [], why)


class TestUntouchedBuild(ParityCase):
    def test_an_untouched_release_build_reproduces_the_committed_pages(self):
        tmp = self.build()
        diffs = self.differences(tmp)
        self.assertEqual(sorted(diffs), [],
                         "a clean release build no longer matches the committed pages "
                         "(rebuild the site, or the committed pages are behind the data)")

    def test_the_four_data_files_are_never_rewritten(self):
        """Nothing in the copy registry may leak a default into the owner's
        data: an untouched site-config.json stays without `copy`/`seo`."""
        cfg = json.loads((ROOT / "site-config.json").read_text(encoding="utf-8"))
        self.assertNotIn("copy", cfg)
        self.assertNotIn("seo", cfg)


class TestDefaultsThroughTheOverridePath(ParityCase):
    def test_every_registry_default_through_the_override_path_is_identical(self):
        """Set every key to its own default in site-config -> copy: the pages
        must not move. If one does, that default has drifted from the literal
        it replaced."""
        import build
        defaults = build.copy_defaults()
        self.assertGreaterEqual(len(defaults), 100, "the registry is suspiciously small")
        copy = {k: v for k, v in defaults.items() if k not in MSG_KEYS}
        tmp = self.build(copy=copy)
        self.assert_same(tmp, "a registry default no longer matches the literal it replaced")

    def test_the_four_status_messages_only_add_their_attributes(self):
        """contact.msg.* are the documented exception: an override is delivered
        as a data-msg-* attribute. Setting them to their defaults may only add
        those attributes, with exactly the default text."""
        import build
        defaults = build.copy_defaults()
        copy = {k: defaults[k] for k in MSG_KEYS}
        tmp = self.build(copy=copy)
        diffs = self.differences(tmp)
        self.assertEqual(sorted(diffs), ["contact.html"])
        built, committed = diffs["contact.html"]
        built, committed = built.decode("utf-8"), committed.decode("utf-8")
        for attr in MSG_ATTRS:
            self.assertIn(attr + '="', built)
            built = re.sub(r" %s=\"[^\"]*\"" % attr, "", built)
        self.assertEqual(built, committed, "the attributes are the only change allowed")
        email = json.loads((ROOT / "site-config.json").read_text(encoding="utf-8"))["email"]
        self.assertIn('data-msg-error="Something went wrong. Email %s directly."' % email,
                      diffs["contact.html"][0].decode("utf-8"))

    def test_seo_defaults_through_the_override_path_are_identical(self):
        """The per-page SEO defaults are what the build already computes: set
        the committed values explicitly and no page moves."""
        text = (ROOT / "work.html").read_text(encoding="utf-8")
        title = re.search(r"<title>(.*?)</title>", text).group(1)
        desc = re.search(r'<meta name="description" content="(.*?)">', text).group(1)
        nf_text = (ROOT / "404.html").read_text(encoding="utf-8")
        nf_title = re.search(r"<title>(.*?)</title>", nf_text).group(1).rsplit(" — ", 1)[0]
        self.assertTrue(title and desc and nf_title)
        tmp = self.build(seo={"pages": {"work": {"title": title, "description": desc},
                                        "notfound": {"title": nf_title}}})
        self.assert_same(tmp, "an SEO value equal to the default changed the page")


class TestClearingRestores(ParityCase):
    def test_one_override_changes_one_page_and_clearing_it_restores_the_bytes(self):
        edited = self.build(copy={"work.heading": "Selected projects"})
        diffs = self.differences(edited)
        self.assertEqual(sorted(diffs), ["work.html"], "one field must touch one page")
        self.assertIn(b"<h1>Selected projects</h1>", diffs["work.html"][0])

        cleared = self.build(copy={"work.heading": ""})
        self.assert_same(cleared, "clearing an override must restore the default page")

    def test_one_seo_override_changes_only_that_pages_head(self):
        tmp = self.build(seo={"pages": {"reels": {"title": "Reels and motion",
                                                  "description": "Short-form video."}}})
        diffs = self.differences(tmp)
        self.assertEqual(sorted(diffs), ["reels.html"])
        built = diffs["reels.html"][0].decode("utf-8")
        self.assertIn("<title>Reels and motion</title>", built)
        self.assertIn('<meta name="description" content="Short-form video.">', built)
        self.assertIn('<meta property="og:title" content="Reels and motion">', built)


if __name__ == "__main__":
    unittest.main()
