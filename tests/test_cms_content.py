"""The content the manager could not edit: media.json's AI process, tools line, reels anatomy note,
credits heading, the Paid Social page's intro and two ad lists, and a project's ai_steps / demo.

Runs against the synthetic site (cms_fixture) through the real HTTP handler, like the rest of the
manager's tests: what is checked is what the save buttons get — validation, the one-key write, the
rebuild, and the data file that comes out the other side.
"""
import json
import unittest

import test_cms  # noqa: F401  (its CmsCase is the fixture harness; importing it runs no tests twice)

FOUR = ("projects.json", "site-config.json", "assets-manifest.json", "media.json")
SHOT = "assets/img/alpha/01.jpg"          # an existing asset nothing else names
SPARE = "assets/img/posters/reels/spare.jpg"
OTHER = "assets/img/beta/01.jpg"
WATCH = "https://livid.com/watch/HGGzzr4EBITa"
EMBED = "https://livid.com/embed/HGGzzr4EBITa?livid_logo=0&fullscreen=1&title=1"

STEPS = ["Brief", "Reference shoot", "Prompt from product references", "Generate",
         "Check against the real product", "Retouch", "Deliver"]


class ContentCase(test_cms.CmsCase):
    """The shared harness, with the paid page switched on and its ads readable."""

    def media(self):
        return self.data("media.json")

    def paid(self):
        return self.media().get("paid_social") or {}

    def save(self, **body):
        return self.post("/api/config", body)

    def put_paid(self, body):
        return self.post("/api/paid", body, method="PUT")

    def page(self, rel):
        return self.text(rel)

    def show_paid_page(self):
        """The fixture ships the Paid Social page hidden; the page's own tests need it built."""
        st, body = self.save(hidden_pages=[])
        self.assertEqual(st, 200, body)

    def bytes_of(self, names=FOUR):
        return {n: (self.tmp / n).read_bytes() for n in names}


class TestMediaText(ContentCase):
    def test_the_process_steps_are_written_in_order(self):
        st, body = self.save(ai_process=STEPS)
        self.assertEqual(st, 200, body)
        self.assertEqual(body["touched"], ["ai_process"])
        self.assertTrue(body["changed"])
        self.assertEqual(self.media()["ai_process"], STEPS)

    def test_the_process_refuses_more_than_twenty_lines(self):
        st, body = self.save(ai_process=["Step %d" % n for n in range(21)])
        self.assertEqual(st, 400, body)
        self.assertIn("20", body["error"])
        self.assertEqual(self.media()["ai_process"], ["Brief"], "the stored list must not move")

    def test_a_step_longer_than_eighty_characters_is_refused(self):
        st, body = self.save(ai_process=["ok", "x" * 81])
        self.assertEqual(st, 400, body)
        self.assertIn("80", body["error"])
        self.assertEqual(self.save(ai_process=["x" * 80])[0], 200)

    def test_an_empty_process_list_is_refused(self):
        for value in ([], ["", "   "]):
            st, body = self.save(ai_process=value)
            self.assertEqual(st, 400, body)
            self.assertIn("at least one step", body["error"])
        self.assertEqual(self.media()["ai_process"], ["Brief"])

    def test_the_tools_line_is_set_and_cleared(self):
        text = "Mostly Adobe Firefly, refined in Wondr Forge."
        st, body = self.save(ai_tools_line=text)
        self.assertEqual(st, 200, body)
        self.assertEqual(self.media()["ai_tools_line"], text)
        st, body = self.save(ai_tools_line="")
        self.assertEqual(st, 200, body)
        self.assertEqual(self.media()["ai_tools_line"], "")

    def test_the_reels_anatomy_note_is_set_and_cleared(self):
        note = "Hook, turn, payoff: three beats in under twelve seconds."
        self.assertEqual(self.save(reel_formats_note=note)[0], 200)
        self.assertEqual(self.media()["reel_formats_note"], note)
        self.assertEqual(self.save(reel_formats_note="")[0], 200)
        self.assertEqual(self.media()["reel_formats_note"], "")

    def test_the_credits_heading_is_set_and_cleared(self):
        self.assertEqual(self.save(credits_note="Client list")[0], 200)
        self.assertEqual(self.media()["credits_note"], "Client list")
        self.assertEqual(self.save(credits_note="")[0], 200)
        self.assertEqual(self.media()["credits_note"], "")

    def test_each_text_has_its_own_limit(self):
        cases = (("ai_tools_line", 400), ("reel_formats_note", 400), ("credits_note", 60))
        for key, most in cases:
            st, body = self.save(**{key: "x" * (most + 1)})
            self.assertEqual(st, 400, (key, body))
            self.assertIn(str(most), body["error"])
            self.assertEqual(self.save(**{key: "x" * most})[0], 200, key)

    def test_only_text_is_accepted(self):
        for value in (5, None, ["a"], {"a": 1}):
            self.assertEqual(self.save(ai_tools_line=value)[0], 400, repr(value))

    def test_a_media_text_save_touches_only_media_json(self):
        before = self.bytes_of()
        st, body = self.save(ai_tools_line="A line.", credits_note="Credits")
        self.assertEqual(st, 200, body)
        self.assertEqual(sorted(body["touched"]), ["ai_tools_line", "credits_note"])
        after = self.bytes_of()
        for name in FOUR:
            if name != "media.json":
                self.assertEqual(after[name], before[name], name + " moved")
        media = json.loads(after["media.json"].decode("utf-8"))
        self.assertEqual(media["ai_tools_line"], "A line.")
        self.assertEqual(media["credits_note"], "Credits")

    def test_the_version_guard_still_applies(self):
        stale = self.version()
        self.assertEqual(self.save(ai_tools_line="First")[0], 200)
        st, body = self.call("POST", "/api/config", {"version": stale, "ai_tools_line": "Second"})
        self.assertEqual(st, 409, body)
        self.assertTrue(body.get("stale"))
        self.assertEqual(self.media()["ai_tools_line"], "First")

    def test_state_carries_the_media_text_the_panes_draw(self):
        st, body = self.call("GET", "/api/state")
        self.assertEqual(st, 200)
        self.assertEqual(body["media"]["ai_process"], ["Brief"])
        self.assertEqual(body["media"]["ai_tools_line"], "Tools line.")
        self.assertEqual(body["media"]["reel_formats_note"], "")
        self.assertEqual(body["media"]["credits_note"], "Production credits")


class TestPaidVideoAds(ContentCase):
    def test_a_video_ad_is_added(self):
        st, body = self.put_paid({"video_ads": [{"title": "Cookie drop", "campaign": "Spring",
                                                 "livid": WATCH, "ratio": "9 / 16",
                                                 "poster": SPARE}]})
        self.assertEqual(st, 200, body)
        self.assertTrue(body["changed"])
        self.assertEqual((body["video_ads"], body["static_ads"]), (1, 0))
        row = self.paid()["video_ads"][0]
        self.assertEqual(row["title"], "Cookie drop")
        self.assertEqual(row["campaign"], "Spring")
        self.assertEqual(row["livid"], EMBED)
        self.assertEqual(row["ratio"], "9 / 16")
        self.assertEqual(row["poster"], SPARE)

    def test_the_shape_defaults_to_vertical_and_a_link_is_needed(self):
        st, body = self.put_paid({"video_ads": [{"title": "No shape", "livid": WATCH}]})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.paid()["video_ads"][0]["ratio"], "9 / 16")
        st, body = self.put_paid({"video_ads": [{"title": "No link"}]})
        self.assertEqual(st, 400, body)
        self.assertIn("Livid link", body["error"])

    def test_a_video_ad_needs_a_title(self):
        st, body = self.put_paid({"video_ads": [{"title": "  ", "livid": WATCH}]})
        self.assertEqual(st, 400, body)
        self.assertIn("title", body["error"])

    def test_a_bad_livid_link_is_refused(self):
        st, body = self.put_paid({"video_ads": [{"title": "x", "livid": "https://example.com/watch/abc"}]})
        self.assertEqual(st, 400, body)
        self.assertIn("Livid", body["error"])
        self.assertEqual(self.paid()["video_ads"], [], "nothing was written")

    def test_a_bad_shape_is_refused(self):
        for ratio in ("3 / 4", "vertical", 9):
            st, body = self.put_paid({"video_ads": [{"title": "x", "livid": WATCH, "ratio": ratio}]})
            self.assertEqual(st, 400, (ratio, body))
        self.assertEqual(self.paid()["video_ads"], [])

    def test_video_ads_reorder_without_losing_what_the_form_does_not_edit(self):
        st, body = self.put_paid({"video_ads": [{"title": "One", "campaign": "A", "livid": WATCH},
                                                {"title": "Two", "campaign": "B", "livid": WATCH}]})
        self.assertEqual(st, 200, body)
        # A field the form has no column for: the mp4 fallback and a note the build understands.
        media = self.media()
        media["paid_social"]["video_ads"][0]["mp4"] = "assets/video/one.mp4"
        media["paid_social"]["video_ads"][0]["confirm"] = ["brand"]
        (self.tmp / "media.json").write_text(self.cms.dump(media), encoding="utf-8")
        st, body = self.put_paid({"video_ads": [{"from": 1, "title": "Two", "campaign": "B", "livid": WATCH},
                                                {"from": 0, "title": "One", "campaign": "A",
                                                 "livid": WATCH}]})
        self.assertEqual(st, 200, body)
        ads = self.paid()["video_ads"]
        self.assertEqual([a["title"] for a in ads], ["Two", "One"])
        self.assertEqual(ads[1]["mp4"], "assets/video/one.mp4", "an unlisted field survives the round trip")
        self.assertEqual(ads[1]["confirm"], ["brand"])

    def test_a_video_ad_is_removed_and_its_poster_is_kept_on_the_removed_shelf(self):
        self.assertEqual(self.put_paid({"video_ads": [{"title": "Temp", "livid": WATCH,
                                                       "poster": SPARE}]})[0], 200)
        self.assertTrue((self.tmp / SPARE).is_file())
        st, body = self.put_paid({"video_ads": []})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.paid()["video_ads"], [])
        self.assertFalse((self.tmp / SPARE).is_file(), "the file left assets/")
        moved = [p.name for p in (self.tmp / "tools" / "removed-media").glob("*")]
        self.assertTrue(any(n.endswith("spare.jpg") for n in moved), moved)

    def test_replacing_a_poster_moves_the_old_file(self):
        self.assertEqual(self.put_paid({"video_ads": [{"title": "Kept", "livid": WATCH,
                                                       "poster": SPARE}]})[0], 200)
        st, body = self.put_paid({"video_ads": [{"from": 0, "title": "Kept", "livid": WATCH,
                                                 "poster": OTHER}]})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["files_moved"], 1, body)
        self.assertEqual(self.paid()["video_ads"][0]["poster"], OTHER)
        self.assertTrue((self.tmp / OTHER).is_file())
        self.assertFalse((self.tmp / SPARE).is_file())

    def test_the_video_list_has_a_ceiling(self):
        rows = [{"title": "Ad %d" % n, "livid": WATCH} for n in range(41)]
        st, body = self.put_paid({"video_ads": rows})
        self.assertEqual(st, 400, body)
        self.assertIn("40", body["error"])


class TestPaidStaticAds(ContentCase):
    def test_a_static_ad_needs_an_image(self):
        for image in ("", None):
            st, body = self.put_paid({"static_ads": [{"title": "Still", "image": image}]})
            self.assertEqual(st, 400, (image, body))
            self.assertIn("image", body["error"])
        st, body = self.put_paid({"static_ads": [{"title": "Still", "image": "assets/img/nope.jpg"}]})
        self.assertEqual(st, 400, body)
        self.assertIn("does not exist", body["error"])

    def test_a_static_ad_is_added_with_the_square_default(self):
        st, body = self.put_paid({"static_ads": [{"title": "Still", "campaign": "Summer", "image": SHOT}]})
        self.assertEqual(st, 200, body)
        self.assertEqual((body["video_ads"], body["static_ads"]), (0, 1))
        row = self.paid()["static_ads"][0]
        self.assertEqual((row["title"], row["campaign"], row["image"], row["ratio"]),
                         ("Still", "Summer", SHOT, "1 / 1"))

    def test_a_static_ad_keeps_an_explicit_shape(self):
        self.assertEqual(self.put_paid({"static_ads": [{"title": "Still", "image": SHOT,
                                                        "ratio": "4 / 5"}]})[0], 200)
        self.assertEqual(self.paid()["static_ads"][0]["ratio"], "4 / 5")
        st, body = self.put_paid({"static_ads": [{"title": "Still", "image": SHOT, "ratio": "2 / 3"}]})
        self.assertEqual(st, 400, body)

    def test_the_static_list_has_a_ceiling(self):
        rows = [{"title": "Still %d" % n, "image": SHOT} for n in range(61)]
        st, body = self.put_paid({"static_ads": rows})
        self.assertEqual(st, 400, body)
        self.assertIn("60", body["error"])

    def test_a_saved_static_ad_and_video_ad_do_not_touch_each_other(self):
        self.assertEqual(self.put_paid({"video_ads": [{"title": "Vid", "livid": WATCH}]})[0], 200)
        st, body = self.put_paid({"static_ads": [{"title": "Still", "image": SHOT}]})
        self.assertEqual(st, 200, body)
        self.assertEqual([a["title"] for a in self.paid()["video_ads"]], ["Vid"])
        self.assertEqual([a["title"] for a in self.paid()["static_ads"]], ["Still"])


class TestPaidIntro(ContentCase):
    def test_the_intro_is_saved_and_cleared(self):
        st, body = self.put_paid({"intro": "Paid social, end to end."})
        self.assertEqual(st, 200, body)
        self.assertEqual(self.paid()["intro"], "Paid social, end to end.")
        self.assertEqual(self.put_paid({"intro": ""})[0], 200)
        self.assertEqual(self.paid()["intro"], "")

    def test_the_intro_has_a_limit(self):
        st, body = self.put_paid({"intro": "x" * 241})
        self.assertEqual(st, 400, body)
        self.assertIn("240", body["error"])
        self.assertEqual(self.put_paid({"intro": "x" * 240})[0], 200)

    def test_a_body_with_only_the_intro_validates(self):
        """No slug, no lists: the one key that was sent is the only one written."""
        before = {k: v for k, v in self.paid().items() if k != "intro"}
        st, body = self.put_paid({"intro": "Only the intro."})
        self.assertEqual(st, 200, body)
        self.assertEqual(body["touched"], ["intro"])
        after = {k: v for k, v in self.paid().items() if k != "intro"}
        self.assertEqual(after, before)

    def test_an_empty_body_is_refused(self):
        st, body = self.put_paid({})
        self.assertEqual(st, 400, body)
        self.assertIn("Nothing to save", body["error"])

    def test_the_version_guard_is_checked_before_anything_is_written(self):
        stale = self.version()
        self.assertEqual(self.put_paid({"intro": "First"})[0], 200)
        st, body = self.call("PUT", "/api/paid", {"version": stale, "intro": "Second"})
        self.assertEqual(st, 409, body)
        self.assertTrue(body.get("stale"))
        self.assertEqual(self.paid()["intro"], "First")

    def test_a_paid_write_touches_only_media_json(self):
        before = self.bytes_of()
        st, body = self.put_paid({"intro": "A line.",
                                  "static_ads": [{"title": "Still", "image": SHOT}]})
        self.assertEqual(st, 200, body)
        self.assertEqual(sorted(body["touched"]), ["intro", "static_ads"])
        after = self.bytes_of()
        for name in FOUR:
            if name != "media.json":
                self.assertEqual(after[name], before[name], name + " moved")


class TestProjectAiFields(ContentCase):
    def test_ai_steps_and_demo_are_saved_on_an_existing_project(self):
        st, body = self.post("/api/projects", {"slug": "alpha", "client": "Alpha", "title": "Alpha title",
                                               "ai_steps": "Real shot, AI pass, final.",
                                               "demo": "The demo reel under the feature."})
        self.assertEqual(st, 200, body)
        p = self.project("alpha")
        self.assertEqual(p["ai_steps"], "Real shot, AI pass, final.")
        self.assertEqual(p["demo"], "The demo reel under the feature.")

    def test_a_new_project_can_carry_both(self):
        st, body = self.post("/api/projects", {"client": "New Client", "title": "New title",
                                               "ai_steps": "Steps line.", "demo": "Demo line."})
        self.assertEqual(st, 200, body)
        p = self.project(body["slug"])
        self.assertEqual((p["ai_steps"], p["demo"]), ("Steps line.", "Demo line."))

    def test_an_empty_line_removes_the_field(self):
        self.assertEqual(self.post("/api/projects", {"slug": "alpha", "client": "Alpha",
                                                     "title": "Alpha title",
                                                     "ai_steps": "Something."})[0], 200)
        self.assertIn("ai_steps", self.project("alpha"))
        self.assertEqual(self.post("/api/projects", {"slug": "alpha", "client": "Alpha",
                                                     "title": "Alpha title", "ai_steps": ""})[0], 200)
        self.assertNotIn("ai_steps", self.project("alpha"),
                         "an empty line is unset, never a stored default")

    def test_a_line_over_four_hundred_characters_is_refused(self):
        for key in ("ai_steps", "demo"):
            st, body = self.post("/api/projects", {"slug": "alpha", "client": "Alpha",
                                                   "title": "Alpha title", key: "x" * 401})
            self.assertEqual(st, 400, (key, body))
            self.assertIn("400", body["error"])
            self.assertNotIn(key, self.project("alpha"))
            self.assertEqual(self.post("/api/projects", {"slug": "alpha", "client": "Alpha",
                                                          "title": "Alpha title", key: "x" * 400})[0], 200)


class TestPaidWritesAreSafe(ContentCase):
    def test_a_failed_build_restores_media_json(self):
        before = {n: (self.tmp / n).read_bytes() for n in FOUR}
        real, calls = self.cms.run_build, []

        def failing():
            calls.append(1)
            if len(calls) == 1:
                return {"ok": False, "ready": False, "items": [], "log": "boom", "blockers": 0,
                        "last_run": "", "version": ""}
            return real()
        self.cms.run_build = failing
        try:
            st, body = self.put_paid({"intro": "Never lands.", "static_ads": [{"title": "Still",
                                                                              "image": SHOT}]})
        finally:
            self.cms.run_build = real
        self.assertEqual(st, 422, body)
        self.assertIn("did not build", body["error"])
        self.assertEqual({n: (self.tmp / n).read_bytes() for n in FOUR}, before,
                         "every file went back to what it was")
        self.assertEqual(len(calls), 2, "the failed build, then the build that puts the pages back")

    def test_a_paid_write_is_backed_up(self):
        self.assertEqual(self.put_paid({"intro": "Backed up."})[0], 200)
        backups = list((self.tmp / "tools" / "backups").glob("media-*.json"))
        self.assertEqual(len(backups), 1, backups)


if __name__ == "__main__":
    unittest.main()
