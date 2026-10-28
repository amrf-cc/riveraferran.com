"""A group or skill edited in the Site Manager must reach the built pages.

build.py is not changed by this workstream: these tests prove the writer produces the data the
existing renderer already understands — the Work page's filter pills and group headings, a case
study's category line and skill chips, and the home page's capability strip.
"""
import unittest

import test_cms
from support import expected_work_groups, run_build, work_sequence

FOOD, MUSIC = "food", "music"
PRODUCT = "product"


def cat(frm, key, label, personal=False):
    return {"from": frm, "key": key, "label": label, "personal": personal}


def skill(frm, tag, label, line):
    return {"from": frm, "tag": tag, "label": label, "line": line}


class BuildTaxCase(test_cms.CmsCase):
    def put(self, **body):
        return self.post("/api/taxonomy", body, method="PUT")

    def cat_rows(self):
        return [cat(i, c["key"], c["label"], c.get("personal", False))
                for i, c in enumerate(self.data("site-config.json")["categories"])]

    def cap_rows(self):
        return [skill(i, c["tag"], c["label"], c["line"])
                for i, c in enumerate(self.data("site-config.json")["capabilities"])]

    def page(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")


class TestGroupsReachThePages(BuildTaxCase):
    def test_a_renamed_group_changes_the_pills_the_heading_and_the_case_line(self):
        # A group's label is also printed on every card (Home, Work, the AI page's live cards), so the
        # pages that must not move are the ones with no Work card on them.
        others = {n: (self.tmp / n).read_bytes()
                  for n in ("about.html", "reels.html", "contact.html", "privacy.html", "terms.html")}
        rows = self.cat_rows()
        rows[0]["label"] = "Food & Drink"
        st, _d = self.put(categories=rows)
        self.assertEqual(st, 200)
        for name, data in others.items():
            self.assertEqual((self.tmp / name).read_bytes(), data, "%s must not move" % name)
        for name in ("index.html", "ai.html"):
            self.assertIn("Food &amp; Drink", self.page(name), "the cards carry the group's name too")
        work = self.page("work.html")
        self.assertIn('<a class="filter" href="#food" data-filter="food" aria-pressed="false">Food &amp; Drink</a>',
                      work, "the filter pill carries the new name and the same key")
        self.assertIn('<h2 class="work-group" data-tags="food" data-filter-heading>Food &amp; Drink</h2>',
                      work, "the group heading carries the new name")
        self.assertNotIn("Food &amp; Beverage", work)
        self.assertIn("Music Videos", work, "the other group is untouched")
        case = self.page("work/alpha.html")
        self.assertIn('<a class="cat-link" href="../work.html#food">Food &amp; Drink</a>', case,
                      "the case study's category line follows the rename, and still points at #food")

    def test_reordering_the_groups_reorders_the_work_page(self):
        rows = self.cat_rows()
        st, _d = self.put(categories=[rows[1], rows[0]])
        self.assertEqual(st, 200)
        work = self.page("work.html")
        headings = [item for item in work_sequence(work) if item[0] == "heading"]
        self.assertEqual([h[1] for h in headings], [MUSIC, FOOD])
        pills = [f for f in work.split('<a class="filter')[1:]]
        self.assertLess(work.index('data-filter="music"'), work.index('data-filter="food"'))
        self.assertTrue(pills)
        self.assertEqual(expected_work_groups(self.data("projects.json")["projects"],
                                              self.data("site-config.json")["categories"]),
                         [(MUSIC, ["gamma"]), (FOOD, ["alpha", "beta", "rodowita-water"])])

    def test_a_new_group_shows_nothing_until_a_project_uses_it(self):
        rows = self.cat_rows()
        st, _d = self.put(categories=rows + [cat(None, "events", "Events")])
        self.assertEqual(st, 200)
        work = self.page("work.html")
        self.assertNotIn('data-filter="events"', work, "the Work page lists groups with a visible project")
        self.assertEqual(run_build(self.tmp, "--release").returncode, 0, "an unused group still releases")


class TestSkillsReachThePages(BuildTaxCase):
    def test_a_renamed_skill_changes_the_chip_on_a_project(self):
        rows = self.cap_rows()
        rows[0]["label"] = "Studio product photography"
        st, _d = self.put(capabilities=rows)
        self.assertEqual(st, 200)
        case = self.page("work/alpha.html")
        self.assertIn("Studio product photography", case, "the project's chip shows the new label")
        self.assertNotIn(">Studio product<", case)
        self.assertIn('href="../work.html#food-beverage"', case, "the chip still links through CAP_LINKS")

    def test_a_new_skill_reaches_the_home_strip_as_plain_text(self):
        rows = self.cap_rows()
        st, _d = self.put(capabilities=rows + [skill(None, "motion-design", "Motion design",
                                                     "Brand-accurate animation in After Effects.")])
        self.assertEqual(st, 200)
        home = self.page("index.html")
        self.assertIn('<div class="cap cap-plain"><span class="cap-label">Motion design</span>'
                      '<span class="cap-line">Brand-accurate animation in After Effects.</span></div>',
                      home, "a tag CAP_LINKS does not know renders as plain text, not a link")
        self.assertNotIn('href="#">Motion design', home)

    def test_a_deleted_unused_skill_leaves_the_strip(self):
        rows = self.cap_rows()
        st, _d = self.put(capabilities=[rows[0], rows[2]])
        self.assertEqual(st, 200)
        home = self.page("index.html")
        self.assertNotIn("Short-form social", home)
        self.assertIn("Studio product", home)
        self.assertIn("AI-assisted creative", home)


if __name__ == "__main__":
    unittest.main()
