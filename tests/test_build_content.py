"""The content WS4 made editable must reach the built pages, through the writers that were just added.

build.py is not touched by this workstream: these tests prove each writer produces the data the
existing renderer already understands, and that a project with an `ai_steps` line and no comparison
sets no longer leaves the AI page with a generated placeholder (which would block a release).
"""
import unittest

import test_cms  # noqa: F401  (its CmsCase is the fixture harness; importing it runs no tests twice)
from support import run_build

ROD = "rodowita-water"          # the fixture project on the AI page's "Ran live" list
STEPS = ["Brief", "Reference shoot", "Prompt from product references", "Generate",
         "Check against the real product", "Retouch", "Deliver"]


class BuildCase(test_cms.CmsCase):
    def save(self, **body):
        return self.post("/api/config", body)

    def page(self, rel):
        return self.text(rel)

    def release(self):
        return run_build(self.tmp, "--release")


class TestMediaTextRenders(BuildCase):
    def test_the_seven_steps_render_numbered_in_order(self):
        st, body = self.save(ai_process=STEPS)
        self.assertEqual(st, 200, body)
        ai = self.page("ai.html")
        self.assertIn('class="process"', ai)
        marks = ['<span class="step-num">%02d</span><span class="step-label">%s</span>' % (n, step)
                 for n, step in enumerate(STEPS, 1)]
        self.assertIn(marks[0], ai)
        self.assertIn(marks[-1], ai)
        self.assertIn('step-num">07<', ai)
        # In order, not just present.
        positions = [ai.index(m) for m in marks]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(len(marks), 7)

    def test_the_tools_paragraph_appears_above_the_label(self):
        line = "Mostly Adobe Firefly, refined in Wondr Forge."
        self.assertEqual(self.save(ai_tools_line=line)[0], 200)
        ai = self.page("ai.html")
        self.assertIn('class="ai-tools"', ai)
        self.assertIn('<p class="lede">%s</p>' % line, ai)

    def test_clearing_the_tools_line_removes_the_paragraph(self):
        self.assertEqual(self.save(ai_tools_line="Something.")[0], 200)
        self.assertIn("Something.", self.page("ai.html"))
        self.assertEqual(self.save(ai_tools_line="")[0], 200)
        self.assertNotIn("Something.", self.page("ai.html"))

    def test_the_anatomy_note_renders_its_block_and_clearing_hides_it(self):
        note = "Hook, turn, payoff: three beats, under twelve seconds."
        self.assertEqual(self.page("reels.html").count('class="reel-anatomy"'), 0,
                         "the fixture starts with an empty note")
        self.assertEqual(self.save(reel_formats_note=note)[0], 200)
        reels = self.page("reels.html")
        self.assertEqual(reels.count('class="reel-anatomy"'), 1)
        self.assertIn(note, reels)
        self.assertEqual(self.save(reel_formats_note="")[0], 200)
        self.assertNotIn('class="reel-anatomy"', self.page("reels.html"))

    def test_the_credits_heading_reaches_the_about_page(self):
        self.assertEqual(self.save(credits_note="Selected credits")[0], 200)
        self.assertIn("Selected credits", self.page("about.html"))
        self.assertEqual(self.save(credits_note="")[0], 200)
        self.assertIn("Production credits", self.page("about.html"),
                      "an empty heading falls back to the built-in wording")


class TestPaidPageRenders(BuildCase):
    def test_one_video_and_one_still_make_the_paid_page_release_ready(self):
        self.assertEqual(self.post("/api/config", {"hidden_pages": []})[0], 200)
        st, body = self.post("/api/paid", {"intro": "Paid social, end to end.",
                                           "video_ads": [{"title": "Cookie drop", "campaign": "Spring",
                                                          "livid": "https://livid.com/watch/HGGzzr4EBITa"}],
                                           "static_ads": [{"title": "End of summer",
                                                           "image": "assets/img/alpha/01.jpg"}]},
                             method="PUT")
        self.assertEqual(st, 200, body)
        paid = self.page("paid-social.html")
        self.assertIn("Cookie drop", paid)
        self.assertIn("End of summer", paid)
        self.assertNotIn("paid-social.html#video-ads", paid, "the video placeholder is gone")
        self.assertNotIn("paid-social.html#static-ads", paid, "the static placeholder is gone")
        ready = run_build(self.tmp, "--check")
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)


class TestProjectAiFieldsRender(BuildCase):
    def test_the_ran_live_card_shows_the_saved_line_instead_of_a_placeholder(self):
        line = "Real shot, AI pass, final, in one afternoon."
        st, body = self.post("/api/projects", {"slug": ROD, "client": "Rodowita", "title": "Rodowita title",
                                               "ai_steps": line})
        self.assertEqual(st, 200, body)
        ai = self.page("ai.html")
        self.assertIn('<p class="ai-steps">%s</p>' % line, ai)
        self.assertNotIn("ai.html#ran-live → %s.ai_steps" % ROD, ai,
                         "the generated placeholder is gone")

    def test_a_project_with_a_steps_line_and_no_sets_no_longer_blocks_the_release(self):
        # An empty line, and the comparison sets taken away: the card has nothing to show.
        self.assertEqual(self.post("/api/project/%s/sets" % ROD, {"sets": []}, method="PUT")[0], 200)
        self.assertEqual(self.post("/api/projects", {"slug": ROD, "client": "Rodowita",
                                                     "title": "Rodowita title", "ai_steps": ""})[0], 200)
        blocked = self.release()
        self.assertEqual(blocked.returncode, 1, blocked.stdout + blocked.stderr)
        self.assertIn("RELEASE BLOCKED", blocked.stdout)
        self.assertIn("ai.html#ran-live → %s.ai_steps" % ROD, blocked.stdout)
        # The line the manager now offers is all it takes.
        self.assertEqual(self.post("/api/projects", {"slug": ROD, "client": "Rodowita",
                                                     "title": "Rodowita title",
                                                     "ai_steps": "Shot, prompted, finished."})[0], 200)
        ready = self.release()
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)

    def test_the_demo_line_renders_on_the_case_page(self):
        line = "The demo reel that ran with the feature."
        self.assertEqual(self.post("/api/projects", {"slug": "alpha", "client": "Alpha",
                                                     "title": "Alpha title", "demo": line})[0], 200)
        case = self.page("work/alpha.html")
        self.assertIn("case-media", case)
        self.assertIn('<p class="desc">%s</p>' % line, case)

    def test_everything_above_is_a_release_build_that_passes(self):
        self.assertEqual(self.save(ai_process=STEPS, ai_tools_line="Tools.",
                                   reel_formats_note="Anatomy.", credits_note="Credits")[0], 200)
        self.assertEqual(self.post("/api/projects", {"slug": ROD, "client": "Rodowita",
                                                     "title": "Rodowita title",
                                                     "ai_steps": "Steps line."})[0], 200)
        r = self.release()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
