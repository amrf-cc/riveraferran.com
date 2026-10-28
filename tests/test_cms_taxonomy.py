"""Tests for tools/cms_taxonomy.py: the writer behind PUT /api/taxonomy, and the contact form's
endpoint in POST /api/config.

They run against the synthetic site in cms_fixture.py (never the owner's content), through the real
HTTP handler on a random port. The fixture has two groups — `food` (three projects) and `music` (one
project) — and three skills: `product` (three projects), `ai` (two) and `social` (none).
"""
import json
import unittest

import test_cms

FOOD, MUSIC = "food", "music"
PRODUCT, SOCIAL, AI = "product", "social", "ai"


def cat(frm, key, label, personal=False):
    return {"from": frm, "key": key, "label": label, "personal": personal}


def skill(frm, tag, label, line):
    return {"from": frm, "tag": tag, "label": label, "line": line}


class TaxCase(test_cms.CmsCase):
    """CmsCase gives a pristine fixture per test plus call()/post()/data()/version()."""

    def put(self, **body):
        return self.post("/api/taxonomy", body, method="PUT")

    def config(self):
        return self.data("site-config.json")

    def cats(self):
        return self.config()["categories"]

    def caps(self):
        return self.config()["capabilities"]

    def cat_rows(self):
        """The stored groups the way the editor sends them back."""
        return [cat(i, c["key"], c["label"], c.get("personal", False)) for i, c in enumerate(self.cats())]

    def cap_rows(self):
        return [skill(i, c["tag"], c["label"], c["line"]) for i, c in enumerate(self.caps())]


class TestCategories(TaxCase):
    def test_a_group_can_be_renamed(self):
        rows = self.cat_rows()
        rows[0]["label"] = "Food, Drink & Hospitality"
        st, d = self.put(categories=rows)
        self.assertEqual((st, d["ok"], d["changed"]), (200, True, True))
        self.assertEqual([c["label"] for c in self.cats()], ["Food, Drink & Hospitality", "Music Videos"])
        self.assertEqual([c["key"] for c in self.cats()], [FOOD, MUSIC], "a rename keeps every key")
        self.assertTrue(d["version"], "the dispatcher must merge handler._after()")
        self.assertIn("build", d)

    def test_groups_reorder_and_the_key_travels_with_its_projects(self):
        st, d = self.put(categories=[cat(1, MUSIC, "Music Videos"), cat(0, FOOD, "Food & Beverage")])
        self.assertEqual(st, 200)
        self.assertEqual([c["key"] for c in self.cats()], [MUSIC, FOOD])
        self.assertEqual(d["categories"][0]["key"], MUSIC)
        self.assertEqual([p["category"] for p in self.data("projects.json")["projects"]].count(FOOD), 3,
                         "reordering categories never rewrites a project")

    def test_a_new_group_appears_with_its_key(self):
        st, d = self.put(categories=self.cat_rows() + [cat(None, "events", "Events", True)])
        self.assertEqual(st, 200)
        self.assertEqual([c["key"] for c in self.cats()], [FOOD, MUSIC, "events"])
        self.assertEqual(self.cats()[-1], {"key": "events", "label": "Events", "personal": True})
        self.assertEqual(len(d["categories"]), 3)

    def test_a_duplicate_key_is_refused(self):
        st, d = self.put(categories=self.cat_rows() + [cat(None, FOOD, "Another food group")])
        self.assertEqual(st, 400)
        self.assertIn("cannot share the key", d["error"])
        self.assertEqual(len(self.cats()), 2)

    def test_a_bad_key_is_refused(self):
        for bad in ("Food & Beverage", "food_beverage", "-food", "food-", "Café", "food--drink"):
            st, d = self.put(categories=self.cat_rows() + [cat(None, bad, "Events")])
            self.assertEqual(st, 400, bad)
            self.assertIn("lowercase words joined by hyphens", d["error"], bad)
        st, d = self.put(categories=self.cat_rows() + [cat(None, "", "Events")])
        self.assertEqual((st, d["error"]), (400, "A new group needs a key."))

    def test_an_unused_group_can_be_deleted(self):
        self.put(categories=self.cat_rows() + [cat(None, "events", "Events")])
        rows = self.cat_rows()[:2]
        st, d = self.put(categories=rows)
        self.assertEqual((st, d["changed"]), (200, True))
        self.assertEqual([c["key"] for c in self.cats()], [FOOD, MUSIC])

    def test_a_group_a_project_still_uses_cannot_be_deleted(self):
        st, d = self.put(categories=[cat(1, MUSIC, "Music Videos")])
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "3 projects still use this group: alpha, beta, rodowita-water. "
                                     "Move them first.")
        self.assertEqual(len(self.cats()), 2, "a refused delete writes nothing")

    def test_the_one_project_case_reads_as_a_sentence(self):
        st, d = self.put(categories=[cat(0, FOOD, "Food & Beverage")])
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "1 project still uses this group: gamma. Move it first.")

    def test_a_key_cannot_be_changed(self):
        rows = self.cat_rows()
        rows[0] = cat(0, "food-and-drink", rows[0]["label"])
        st, d = self.put(categories=rows)
        self.assertEqual(st, 400)
        self.assertIn("key cannot change", d["error"])
        self.assertEqual(self.cats()[0]["key"], FOOD)

    def test_personal_toggles_both_ways(self):
        rows = self.cat_rows()
        rows[1]["personal"] = True
        self.assertEqual(self.put(categories=rows)[0], 200)
        self.assertIs(self.cats()[1]["personal"], True)
        rows = self.cat_rows()
        rows[0]["personal"] = True
        self.assertEqual(self.put(categories=rows)[0], 200)
        self.assertEqual([c["personal"] for c in self.cats()], [True, True])
        rows = self.cat_rows()
        rows[0]["personal"] = 0
        self.assertEqual(self.put(categories=rows)[0], 200)
        self.assertIs(self.cats()[0]["personal"], False, "personal is coerced with bool()")

    def test_a_label_over_forty_characters_is_refused(self):
        rows = self.cat_rows()
        rows[0]["label"] = "x" * 41
        st, d = self.put(categories=rows)
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "That label is too long: 40 characters at most.")
        rows[0]["label"] = "x" * 40
        self.assertEqual(self.put(categories=rows)[0], 200, "40 is allowed")

    def test_an_empty_label_is_refused(self):
        rows = self.cat_rows()
        rows[0]["label"] = "   "
        st, d = self.put(categories=rows)
        self.assertEqual(st, 400)
        self.assertIn("needs a label", d["error"])

    def test_unknown_fields_on_a_row_are_kept(self):
        path = self.tmp / "site-config.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["categories"][0]["note"] = "keep me"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        rows = self.cat_rows()
        rows[0]["label"] = "Food & Drink"
        self.assertEqual(self.put(categories=rows)[0], 200)
        self.assertEqual(self.cats()[0]["note"], "keep me")


class TestSkills(TaxCase):
    def test_a_skill_label_and_line_can_be_edited(self):
        rows = self.cap_rows()
        rows[0]["label"] = "Studio product photography"
        rows[0]["line"] = "Product, process and team, in studio and on location."
        st, d = self.put(capabilities=rows)
        self.assertEqual((st, d["changed"]), (200, True))
        self.assertEqual(self.caps()[0]["label"], "Studio product photography")
        self.assertEqual(self.caps()[0]["line"], "Product, process and team, in studio and on location.")
        self.assertEqual(self.caps()[0]["tag"], PRODUCT)

    def test_skills_reorder(self):
        rows = self.cap_rows()
        st, _d = self.put(capabilities=[rows[2], rows[0], rows[1]])
        self.assertEqual(st, 200)
        self.assertEqual([c["tag"] for c in self.caps()], [AI, PRODUCT, SOCIAL])

    def test_a_new_skill_appears_with_its_tag(self):
        st, d = self.put(capabilities=self.cap_rows() + [skill(None, "motion-design", "Motion design",
                                                              "Brand-accurate animation.")])
        self.assertEqual(st, 200)
        self.assertEqual(self.caps()[-1], {"tag": "motion-design", "label": "Motion design",
                                           "line": "Brand-accurate animation."})
        self.assertEqual(len(d["capabilities"]), 4)

    def test_a_new_skill_needs_a_line(self):
        st, d = self.put(capabilities=self.cap_rows() + [skill(None, "motion", "Motion", "")])
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "Every skill needs a line.")

    def test_a_line_over_160_characters_is_refused(self):
        rows = self.cap_rows()
        rows[0]["line"] = "y" * 161
        st, d = self.put(capabilities=rows)
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "That line is too long: 160 characters at most.")

    def test_a_skill_a_project_still_uses_cannot_be_deleted(self):
        st, d = self.put(capabilities=[skill(1, SOCIAL, "Short-form social", "Social.")])
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "3 projects still use this skill: alpha, beta, gamma. Move them first.")

    def test_an_unused_skill_can_be_deleted(self):
        st, d = self.put(capabilities=[self.cap_rows()[0], self.cap_rows()[2]])
        self.assertEqual((st, d["changed"]), (200, True))
        self.assertEqual([c["tag"] for c in self.caps()], [PRODUCT, AI])

    def test_a_tag_cannot_be_changed(self):
        rows = self.cap_rows()
        rows[1] = skill(1, "short-social", rows[1]["label"], rows[1]["line"])
        st, d = self.put(capabilities=rows)
        self.assertEqual(st, 400)
        self.assertIn("tag cannot change", d["error"])
        self.assertEqual(self.caps()[1]["tag"], SOCIAL)


class TestWholeListRules(TaxCase):
    def test_only_the_list_that_is_sent_is_touched(self):
        caps_before = (self.tmp / "site-config.json").read_text(encoding="utf-8")
        rows = self.cat_rows()
        rows[0]["label"] = "Food & Drink"
        self.assertEqual(self.put(categories=rows)[0], 200)
        self.assertEqual(self.config()["capabilities"], json.loads(caps_before)["capabilities"])

    def test_unknown_payload_keys_are_ignored(self):
        st, d = self.put(categories=self.cat_rows(), junk="ignored", something={"a": 1})
        self.assertEqual((st, d["touched"]), (200, ["categories"]))
        self.assertNotIn("junk", self.config())
        self.assertNotIn("something", self.config())

    def test_nothing_to_save_is_refused(self):
        st, d = self.put()
        self.assertEqual(st, 400)
        self.assertEqual(d["error"], "Nothing to save.")

    def test_a_stale_version_is_refused(self):
        st, d = self.call("PUT", "/api/taxonomy",
                          {"categories": self.cat_rows(), "version": "0123456789abcdef"})
        self.assertEqual(st, 409)
        self.assertIs(d.get("stale"), True)
        self.assertEqual(len(self.cats()), 2)

    def test_only_site_config_is_written(self):
        before = {name: (self.tmp / name).read_bytes()
                  for name in ("projects.json", "media.json", "assets-manifest.json")}
        rows = self.cat_rows()
        rows[0]["label"] = "Food & Drink"
        caps = self.cap_rows()
        caps[0]["label"] = "Studio product"
        st, _d = self.put(categories=rows, capabilities=caps)
        self.assertEqual(st, 200)
        for name, data in before.items():
            self.assertEqual((self.tmp / name).read_bytes(), data, name)

    def test_an_unchanged_list_saves_nothing(self):
        st, d = self.put(categories=self.cat_rows(), capabilities=self.cap_rows())
        self.assertEqual((st, d["changed"]), (200, False))


class TestFormspreeEndpoint(TaxCase):
    def config_post(self, **raw):
        return self.call("POST", "/api/config", dict(raw, version=self.version()))

    def contact_page(self):
        return (self.tmp / "contact.html").read_text(encoding="utf-8")

    def test_the_endpoint_is_set_and_reaches_the_page(self):
        st, d = self.config_post(formspree_endpoint="https://formspree.io/f/mgaoebdr")
        self.assertEqual((st, d["ok"], d["changed"]), (200, True, True))
        self.assertEqual(self.config()["formspree_endpoint"], "https://formspree.io/f/mgaoebdr")
        self.assertIn('data-endpoint="https://formspree.io/f/mgaoebdr"', self.contact_page())

    def test_clearing_it_leaves_the_form_on_the_mailto_fallback(self):
        st, d = self.config_post(formspree_endpoint="")
        self.assertEqual((st, d["changed"]), (200, True))
        self.assertEqual(self.config()["formspree_endpoint"], "")
        page = self.contact_page()
        self.assertIn('data-endpoint=""', page, "an empty endpoint is what tells the page to use mailto")
        self.assertIn('id="contact-form"', page)

    def test_anything_that_is_not_a_formspree_form_url_is_refused(self):
        for bad in ("https://example.com/f/mgaoebdr", "http://formspree.io/f/mgaoebdr",
                    "https://formspree.io/f/", "https://formspree.io/f/mga oebdr",
                    "formspree.io/f/mgaoebdr", "https://formspree.io/forms"):
            st, d = self.config_post(formspree_endpoint=bad)
            self.assertEqual(st, 400, bad)
            self.assertIn("Formspree URL", d["error"], bad)
        self.assertEqual(self.config()["formspree_endpoint"], "https://formspree.io/f/test",
                         "a refused value writes nothing")


if __name__ == "__main__":
    unittest.main()
