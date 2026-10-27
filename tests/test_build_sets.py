"""A comparison set written through the Site Manager must reach the built pages.

build.py is not changed by this workstream: these tests prove the writer produces the data the
existing renderer already understands, on the case-study page and, for a project on the AI page's
"Ran live" list, on ai.html. They also prove the release gate still sees a step with no image.
"""
import unittest

import test_cms  # noqa: F401  (its CmsCase is the fixture harness; importing it runs no tests twice)
from support import run_build

ROD = "rodowita-water"          # the fixture project that is on the AI page's "Ran live" list
SHOT0 = "assets/img/rodowita-water/01.jpg"
SHOT1 = "assets/img/rodowita-water/02.jpg"
SPARE = "assets/img/alpha/01.jpg"


def step(label, image, ai=False, composite=False, frm=None):
    return {"from": frm, "label": label, "image": image, "ai": ai, "composite": composite}


def a_set(title, *steps, frm=None):
    return {"from": frm, "title": title, "steps": list(steps)}


class BuildCase(test_cms.CmsCase):
    def put(self, slug, sets):
        return self.post("/api/project/%s/sets" % slug, {"sets": sets}, method="PUT")

    def page(self, rel):
        return (self.tmp / rel).read_text(encoding="utf-8")


class TestComparisonSetsRender(BuildCase):
    def test_a_saved_set_renders_on_the_case_study_page(self):
        st, _d = self.put(ROD, [a_set("Studio shot → AI step → final",
                                      step("Studio shot", SHOT0),
                                      step("AI pass", SPARE, True),
                                      step("Final", SHOT1))])
        self.assertEqual(st, 200)
        page = self.page("work/%s.html" % ROD)
        self.assertIn('class="compare-list"', page)
        self.assertIn("Studio shot → AI step → final", page)
        for label in ("Studio shot", "AI pass", "Final"):
            self.assertIn('<p class="compare-label">%s' % label, page)
        self.assertIn('<span class="ai-badge step-badge">AI-assisted</span>', page,
                      "the AI step carries its badge")
        self.assertNotIn("ph-box", page, "every step has an image, so nothing is a placeholder")

    def test_it_also_renders_on_the_ai_page_for_a_ran_live_project(self):
        self.put(ROD, [a_set("Live comparison", step("Studio shot", SHOT0), step("Final", SHOT1, True))])
        ai = self.page("ai.html")
        self.assertIn('class="live-sets"', ai)
        self.assertIn("Live comparison", ai)
        self.assertIn('<p class="compare-label">Final', ai)

    def test_a_project_off_the_ran_live_list_stays_out_of_the_ai_page(self):
        self.put("alpha", [a_set("Alpha comparison", step("Alpha step one", SPARE))])
        self.assertIn("Alpha comparison", self.page("work/alpha.html"))
        self.assertNotIn("Alpha step one", self.page("ai.html"))

    def test_a_step_without_an_image_blocks_the_release_and_names_its_path(self):
        st, d = self.put(ROD, [a_set("Half built", step("Studio shot", SHOT0), step("Final", ""))])
        self.assertEqual((st, d["steps_without_image"]), (200, 1))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("RELEASE BLOCKED", r.stdout)
        self.assertIn("assets-manifest.json → projects.%s.sets[0].steps[1].image" % ROD, r.stdout)
        self.assertEqual(run_build(self.tmp, "--release").returncode, 1,
                         "nothing was written by the blocked release")
        self.assertIn("Comparison step image: Final",
                      self.page("work/%s.html" % ROD), "the draft build shows what is missing")

    def test_filling_the_image_makes_the_release_pass_again(self):
        self.put(ROD, [a_set("Half built", step("Studio shot", SHOT0), step("Final", ""))])
        st, d = self.put(ROD, [a_set("Half built", step("Studio shot", SHOT0, frm=0),
                                     step("Final", SPARE, frm=1), frm=0)])
        self.assertEqual((st, d["steps_without_image"]), (200, 0))
        r = run_build(self.tmp, "--release")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
