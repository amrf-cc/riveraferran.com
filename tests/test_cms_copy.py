"""POST /api/config for editable copy and per-page SEO.

Runs against the synthetic site (cms_fixture) through the real HTTP handler, so
what is checked is what the manager's save button gets: validation, the delta
write, the rebuild, and the data file that comes out the other side.
"""
import json
import shutil
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import cms_fixture as F


class CopyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="cms-copy-"))
        F.make_site(cls.tmp)
        cls.cms = F.load_cms(cls.tmp)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), cls.cms.Handler)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        shutil.rmtree(str(cls.tmp), ignore_errors=True)

    def setUp(self):
        F.make_site(self.tmp)
        self.cms.BUILD.update(last_run="", ok=None, ready=None, blockers=None, items=[], log="", version="")
        self.cms.run_build()

    # -- helpers ---------------------------------------------------------------------------
    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request("http://127.0.0.1:%d%s" % (self.port, path), data=data,
                                     method=method, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                status, payload = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, payload = e.code, e.read()
        return status, json.loads(payload)

    def version(self):
        return self.call("GET", "/api/state")[1]["version"]

    def save(self, **raw):
        return self.call("POST", "/api/config", dict(raw, version=self.version()))

    def cfg(self):
        return json.loads((self.tmp / "site-config.json").read_text(encoding="utf-8"))

    def text(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")

    def page_titles(self):
        out = {}
        for f in sorted(self.tmp.glob("*.html")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.startswith("<title>"):
                    out[f.name] = line
                    break
        return out

    def copy(self, **fields):
        return self.save(copy=fields)

    def seo(self, page, **fields):
        return self.save(seo={"pages": {page: fields}})


class TestCopyOverrides(CopyCase):
    def test_setting_one_field_rebuilds_the_page_it_belongs_to(self):
        before = {p: self.text(p) for p in ("index.html", "work.html")}
        status, body = self.copy(**{"work.heading": "Selected projects"})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["changed"])
        self.assertEqual(body["touched"], ["copy"])
        self.assertIn("build", body)
        self.assertEqual(self.cfg()["copy"], {"work.heading": "Selected projects"})
        self.assertIn("<h1>Selected projects</h1>", self.text("work.html"))
        self.assertNotIn("Selected projects", before["work.html"])
        self.assertEqual(self.text("index.html"), before["index.html"], "another page moved")

    def test_copy_is_a_delta(self):
        self.assertEqual(self.copy(**{"work.heading": "Selected", "work.kicker": "Portfolio"})[0], 200)
        self.assertEqual(self.copy(**{"work.kicker": "Casework"})[0], 200)
        self.assertEqual(self.cfg()["copy"], {"work.heading": "Selected", "work.kicker": "Casework"})

    def test_clearing_the_last_field_removes_the_copy_object(self):
        self.copy(**{"home.about.more": "Read more"})
        self.assertIn("copy", self.cfg())
        status, body = self.copy(**{"home.about.more": ""})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["changed"])
        self.assertNotIn("copy", self.cfg())
        self.assertNotIn("Read more", self.text("index.html"))
        self.assertIn("More about me", self.text("index.html"))

    def test_an_unknown_key_is_refused_by_name(self):
        status, body = self.copy(**{"work.nope": "x"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "Unknown text field: work.nope.")
        self.assertNotIn("copy", self.cfg())

    def test_a_value_over_its_limit_is_refused(self):
        limit = None
        for row in self.call("GET", "/api/state")[1]["copy_registry"]["keys"]:
            if row["key"] == "work.heading":
                limit = row["max"]
        self.assertIsNotNone(limit)
        status, body = self.copy(**{"work.heading": "x" * (limit + 1)})
        self.assertEqual(status, 400, body)
        self.assertIn(str(limit), body["error"])
        self.assertEqual(self.copy(**{"work.heading": "x" * limit})[0], 200)

    def test_square_brackets_are_refused(self):
        for value in ("[To add]", "[pending]"):
            status, body = self.copy(**{"work.heading": value})
            self.assertEqual(status, 400, value)
            self.assertIn("Square brackets", body["error"])
        self.assertNotIn("copy", self.cfg())

    def test_carriage_returns_are_refused(self):
        status, body = self.copy(**{"contact.lede": "One line\r\nand another"})
        self.assertEqual(status, 400)
        self.assertIn("Line breaks", body["error"])

    def test_only_text_is_accepted(self):
        for value in (5, None, ["Work"], {"text": "Work"}):
            self.assertEqual(self.copy(**{"work.heading": value})[0], 400, repr(value))
        self.assertEqual(self.save(copy="Work")[0], 400)
        self.assertEqual(self.save(copy=[{"key": "work.heading"}])[0], 400)

    def test_a_save_that_changes_nothing_reports_it(self):
        self.assertEqual(self.copy(**{"work.heading": "Selected"})[0], 200)
        status, body = self.copy(**{"work.heading": "Selected"})
        self.assertEqual(status, 200, body)
        self.assertFalse(body["changed"])

    def test_the_version_guard_still_applies(self):
        stale = self.version()
        self.call("GET", "/api/state")
        self.copy(**{"work.heading": "First"})
        status, body = self.call("POST", "/api/config",
                                 {"version": stale, "copy": {"work.heading": "Second"}})
        self.assertEqual(status, 409, body)
        self.assertTrue(body.get("stale"))
        self.assertEqual(self.cfg()["copy"], {"work.heading": "First"})

    def test_the_rest_of_the_config_is_untouched(self):
        redirects = {"to": "https://rfcreativeservices.com", "paths": ["gallery.html"]}
        cfg = self.cfg()
        cfg["legacy_redirects"] = redirects
        (self.tmp / "site-config.json").write_text(self.cms.dump(cfg), encoding="utf-8")
        self.cms.run_build()
        names = [p["key"] for p in self.cfg()["categories"]]
        status, body = self.copy(**{"work.heading": "Selected"})
        self.assertEqual(status, 200, body)
        after = self.cfg()
        self.assertEqual(after["legacy_redirects"], redirects)
        self.assertEqual([p["key"] for p in after["categories"]], names)
        self.assertEqual(after["page_intros"]["work"], "Work intro.")

    def test_copy_and_settings_in_one_save(self):
        status, body = self.save(headline="A new headline.", copy={"work.heading": "Selected"})
        self.assertEqual(status, 200, body)
        self.assertEqual(sorted(body["touched"]), ["copy", "headline"])
        self.assertEqual(self.cfg()["copy"], {"work.heading": "Selected"})
        self.assertIn("A new headline.", self.text("index.html"))

    def test_the_contact_messages_reach_the_form_attributes(self):
        status, body = self.copy(**{"contact.msg.error": "Write to {email} instead.",
                                    "contact.msg.sent": "In the inbox."})
        self.assertEqual(status, 200, body)
        text = self.text("contact.html")
        self.assertIn('data-msg-error="Write to test@example.com instead."', text)
        self.assertIn('data-msg-sent="In the inbox."', text)
        self.assertNotIn("data-msg-opening", text, "only overrides are emitted")
        self.assertNotIn("data-msg-sending", text)

    def test_an_untouched_form_emits_no_message_attributes(self):
        text = self.text("contact.html")
        for attr in ("data-msg-opening", "data-msg-sending", "data-msg-sent", "data-msg-error"):
            self.assertNotIn(attr, text)


class TestSeoOverrides(CopyCase):
    def test_a_pages_title_and_description_are_used_verbatim(self):
        before = self.page_titles()
        status, body = self.seo("work", title="Selected projects", description="Grouped by kind.")
        self.assertEqual(status, 200, body)
        self.assertEqual(body["touched"], ["seo"])
        self.assertEqual(self.cfg()["seo"]["pages"]["work"],
                         {"title": "Selected projects", "description": "Grouped by kind."})
        text = self.text("work.html")
        self.assertIn("<title>Selected projects</title>", text)
        self.assertIn('<meta name="description" content="Grouped by kind.">', text)
        after = self.page_titles()
        self.assertEqual({k: v for k, v in after.items() if k != "work.html"},
                         {k: v for k, v in before.items() if k != "work.html"})

    def test_the_legal_pages_and_the_404_have_their_own_entries(self):
        self.assertEqual(self.seo("legal.privacy", title="Privacy", description="How.")[0], 200)
        self.assertEqual(self.seo("legal.terms", title="Terms")[0], 200)
        self.assertEqual(self.seo("notfound", title="Lost")[0], 200)
        # An SEO title replaces the whole <title>; the visible h1 and the copy
        # of the document stay under the registry's legal.* keys.
        self.assertIn("<title>Privacy</title>", self.text("privacy.html"))
        self.assertIn("<h1>Privacy Notice</h1>", self.text("privacy.html"))
        self.assertIn("<title>Lost — Test Person</title>", self.text("404.html"))
        pages = self.cfg()["seo"]["pages"]
        self.assertEqual(sorted(pages), ["legal.privacy", "legal.terms", "notfound"])

    def test_an_empty_value_clears_that_field_only(self):
        self.seo("home", title="Home page", description="Landing.")
        self.seo("home", description="")
        self.assertEqual(self.cfg()["seo"]["pages"]["home"], {"title": "Home page"})
        self.seo("home", title="")
        self.assertNotIn("seo", self.cfg())
        self.assertIn("<title>Test Person — Photo &amp; Video</title>", self.text("index.html"))

    def test_limits_and_unknown_names_are_refused(self):
        self.assertEqual(self.seo("work", title="x" * 71)[0], 400)
        self.assertEqual(self.seo("work", description="x" * 161)[0], 400)
        self.assertEqual(self.seo("work", title="x" * 70)[0], 200)
        self.assertEqual(self.seo("nope", title="x")[0], 400)
        self.assertEqual(self.seo("work", keywords="x")[0], 400)
        self.assertEqual(self.seo("notfound", description="x")[0], 400)
        self.assertEqual(self.seo("everypage", title="x")[0], 400)
        self.assertEqual(self.save(seo={"work": {"title": "x"}})[0], 400)
        self.assertEqual(self.save(seo={"pages": {"work": "x"}})[0], 400)
        self.assertEqual(self.save(seo={"pages": {"work": {"title": 5}}})[0], 400)

    def test_a_value_equal_to_the_default_leaves_the_page_alone(self):
        before = self.text("work.html")
        status, body = self.seo("work", title="Work — Test Person", description="Work intro.")
        self.assertEqual(status, 200, body)
        self.assertEqual(self.text("work.html"), before)


class TestStateRegistry(CopyCase):
    def test_the_state_payload_carries_the_registry(self):
        status, body = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        reg = body["copy_registry"]
        self.assertEqual([p["key"] for p in reg["pages"]],
                         ["everypage", "home", "work", "case", "reels", "ai", "paid",
                          "about", "contact", "legal"])
        self.assertGreaterEqual(len(reg["keys"]), 100)
        for row in reg["keys"]:
            self.assertEqual(sorted(row), ["default", "key", "label", "max", "page", "section"])

    def test_a_missing_registry_does_not_break_the_manager(self):
        """A half-copied install must still open the manager (with no copy
        forms) instead of failing to load at all."""
        reg = self.cms.REPO / "templates" / "copy.json"
        keep = reg.read_bytes()
        reg.unlink()
        try:
            status, body = self.call("GET", "/api/state")
            self.assertEqual(status, 200)
            self.assertEqual(body["copy_registry"], {"pages": [], "keys": []})
        finally:
            reg.write_bytes(keep)


if __name__ == "__main__":
    unittest.main()
