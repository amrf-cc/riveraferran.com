"""Tests for tools/cms_sets.py: the comparison-set writer behind PUT /api/project/<slug>/sets.

They run against the synthetic site in cms_fixture.py (never the owner's content), through the real
HTTP handler on a random port, so what is checked is what the browser gets. The fixture's project
"rodowita-water" already has one set on it; the others start with none.
"""
import json
import unittest

import test_cms
from support import ROOT

ROD = "rodowita-water"          # the fixture project that already has a comparison set
SHOT0 = "assets/img/rodowita-water/01.jpg"      # also photograph 01 of that project
SHOT1 = "assets/img/rodowita-water/02.jpg"
SPARE = "assets/img/alpha/01.jpg"


def step(label, image, ai=False, composite=False, frm=None):
    return {"from": frm, "label": label, "image": image, "ai": ai, "composite": composite}


def a_set(title, *steps, frm=None):
    return {"from": frm, "title": title, "steps": list(steps)}


class SetsCase(test_cms.CmsCase):
    """CmsCase gives a pristine fixture per test plus call()/post()/data()/version()."""

    def put(self, slug, sets):
        return self.post("/api/project/%s/sets" % slug, {"sets": sets}, method="PUT")

    def sets(self, slug=ROD):
        return self.data("assets-manifest.json")["projects"][slug].get("sets") or []

    def as_rows(self):
        """The stored set rewritten the way the editor sends it back."""
        first = self.sets()[0]
        return [a_set(first.get("title", ""),
                      *[step(st.get("label", ""), st["image"], st.get("ai", False),
                             st.get("composite", False), j)
                        for j, st in enumerate(first["steps"])], frm=0)]


class TestWritingSets(SetsCase):
    def test_a_project_can_gain_its_first_set(self):
        st, d = self.put("beta", [a_set("Studio shot → final", step("Studio shot", "assets/img/beta/01.jpg"))])
        self.assertEqual((st, d["ok"], d["changed"]), (200, True, True))
        self.assertEqual((d["sets"], d["steps"], d["steps_without_image"], d["files_moved"]), (1, 1, 0, 0))
        self.assertTrue(d["version"], "the dispatcher must merge handler._after()")
        self.assertIn("build", d)
        self.assertEqual(self.sets("beta")[0]["title"], "Studio shot → final")
        self.assertEqual(self.sets("beta")[0]["steps"][0]["label"], "Studio shot")

    def test_editing_a_title_keeps_the_steps_and_writes_only_the_manifest(self):
        before = {name: (self.tmp / name).read_bytes()
                  for name in ("projects.json", "media.json", "site-config.json")}
        st, d = self.put(ROD, [a_set("Real shot → AI → final",
                                     *[step(s["label"], s["image"], s.get("ai", False),
                                            s.get("composite", False), j)
                                       for j, s in enumerate(self.sets()[0]["steps"])], frm=0)])
        self.assertEqual((st, d["sets"], d["steps"]), (200, 1, 2))
        after = self.sets()[0]
        self.assertEqual(after["title"], "Real shot → AI → final")
        self.assertEqual([s["label"] for s in after["steps"]], ["Studio shot", "Final"])
        for name, data in before.items():
            self.assertEqual((self.tmp / name).read_bytes(), data, name)

    def test_a_save_that_changes_nothing_writes_nothing(self):
        # The owner's real data keeps a flag only where it is true (the first step has no flag keys at
        # all). Save on an untouched set must not add "ai": false / "composite": false to every step:
        # that is a noisy manifest diff and a Publish button lit up for nothing.
        path = self.tmp / "assets-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["projects"][ROD]["sets"][0]["steps"][0].pop("ai", None)
        data["projects"][ROD]["sets"][0]["steps"][0].pop("composite", None)
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        before = path.read_bytes()
        st, d = self.put(ROD, self.as_rows())
        self.assertEqual((st, d["changed"]), (200, False))
        self.assertEqual(path.read_bytes(), before)

    def test_a_flag_is_stored_only_while_it_is_ticked(self):
        rows = self.as_rows()
        self.assertTrue(rows[0]["steps"][1]["ai"], "the fixture's second step starts AI-assisted")
        rows[0]["steps"][1]["ai"] = False                    # untick: the key must go, not turn false
        rows[0]["steps"][0]["composite"] = True              # tick another
        st, _d = self.put(ROD, rows)
        self.assertEqual(st, 200)
        steps = self.sets()[0]["steps"]
        self.assertNotIn("ai", steps[1])
        self.assertIs(steps[0]["composite"], True)
        self.assertNotIn("ai", steps[0])
        self.assertNotIn("composite", steps[1])

    def test_sets_reorder_and_keep_their_own_steps(self):
        first = self.sets()[0]
        st, _d = self.put(ROD, [a_set("A new second set", step("Second shot", SPARE)),
                                a_set(first["title"], *[step(s["label"], s["image"], s.get("ai", False),
                                                             False, j)
                                                        for j, s in enumerate(first["steps"])], frm=0)])
        self.assertEqual(st, 200)
        got = self.sets()
        self.assertEqual([s["title"] for s in got], ["A new second set", first["title"]])
        self.assertEqual([s["label"] for s in got[1]["steps"]], ["Studio shot", "Final"])

    def test_steps_reorder_inside_their_set(self):
        first = self.sets()[0]
        st, _d = self.put(ROD, [a_set(first["title"],
                                      step("Final", first["steps"][1]["image"], True, False, 1),
                                      step("Studio shot", first["steps"][0]["image"], False, False, 0),
                                      frm=0)])
        self.assertEqual(st, 200)
        got = self.sets()[0]
        self.assertEqual([s["label"] for s in got["steps"]], ["Final", "Studio shot"])
        self.assertIs(got["steps"][0]["ai"], True)

    def test_a_step_can_be_deleted(self):
        first = self.sets()[0]
        st, d = self.put(ROD, [a_set(first["title"], step("Final", first["steps"][1]["image"],
                                                          True, False, 1), frm=0)])
        self.assertEqual((st, d["steps"]), (200, 1))
        self.assertEqual([s["label"] for s in self.sets()[0]["steps"]], ["Final"])

    def test_a_whole_set_can_be_deleted(self):
        st, d = self.put(ROD, [])
        self.assertEqual((st, d["sets"], d["steps"]), (200, 0, 0))
        self.assertEqual(self.sets(), [])

    def test_unknown_keys_survive_a_from_round_trip(self):
        path = self.tmp / "assets-manifest.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["projects"][ROD]["sets"][0]["note"] = "keep me"
        data["projects"][ROD]["sets"][0]["steps"][1]["grip"] = "left"
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        st, _d = self.put(ROD, self.as_rows())
        self.assertEqual(st, 200)
        got = self.sets()[0]
        self.assertEqual(got["note"], "keep me")
        self.assertEqual(got["steps"][1]["grip"], "left")
        self.assertEqual(got["steps"][1]["label"], "Final")

    def test_a_row_without_a_from_starts_a_new_object(self):
        st, _d = self.put(ROD, [a_set("Only", step("One", SPARE))])
        self.assertEqual(st, 200)
        got = self.sets()
        self.assertEqual(len(got), 1)
        # A fresh step carries exactly what was given: unset flags are not stored (they were "false" keys once).
        self.assertEqual(sorted(got[0]["steps"][0]), ["image", "label"])

    def test_the_same_from_index_is_used_once(self):
        st, d = self.put(ROD, [a_set("First", step("One", SHOT0, frm=0), frm=0),
                               a_set("Second", step("Two", SPARE, frm=0), frm=0)])
        self.assertEqual((st, d["sets"]), (200, 2))
        self.assertEqual(self.sets()[1]["steps"][0]["label"], "Two",
                         "the second claim on index 0 must not copy the stored set")


class TestValidation(SetsCase):
    def test_an_image_that_is_not_there_is_refused(self):
        st, d = self.put(ROD, [a_set("Only", step("One", SHOT0), step("Two", "assets/img/nope.jpg"))])
        self.assertEqual((st, d["ok"]), (400, False))
        self.assertEqual(d["error"], "Step 2: that image does not exist.")
        self.assertEqual(self.sets()[0]["title"], "Studio shot → final", "nothing was written")

    def test_an_image_outside_assets_is_refused(self):
        for outside in ("/etc/passwd", "projects.json"):
            st, d = self.put(ROD, [a_set("Only", step("One", outside))])
            self.assertEqual(st, 400, outside)
            self.assertIn("assets/", d["error"])
        st, _d = self.put(ROD, [a_set("Only", step("One", "assets/../projects.json"))])
        self.assertEqual(st, 400, "a path that climbs out of assets/ is refused too")

    def test_an_empty_image_is_allowed_and_counted(self):
        st, d = self.put(ROD, [a_set("Half built", step("Studio shot", SHOT0), step("Final", ""))])
        self.assertEqual((st, d["ok"], d["steps_without_image"]), (200, True, 1))
        self.assertEqual(self.sets()[0]["steps"][1]["image"], "")

    def test_more_than_six_sets_is_refused(self):
        rows = [a_set("Set %d" % n, step("One", SPARE)) for n in range(7)]
        st, d = self.put(ROD, rows)
        self.assertEqual((st, d["ok"]), (400, False))
        self.assertIn("6", d["error"])

    def test_more_than_eight_steps_is_refused(self):
        st, d = self.put(ROD, [a_set("Many", *[step("Step %d" % n, SPARE) for n in range(9)])])
        self.assertEqual((st, d["ok"]), (400, False))
        self.assertIn("8", d["error"])

    def test_a_set_needs_a_step(self):
        st, d = self.put(ROD, [a_set("Empty")])
        self.assertEqual((st, d["error"]), (400, "A set needs at least one step."))

    def test_a_title_or_a_label_can_be_empty_but_not_too_long(self):
        st, _d = self.put(ROD, [a_set("", step("", SHOT0))])
        self.assertEqual(st, 200)
        self.assertEqual(self.sets()[0]["title"], "")
        st, d = self.put(ROD, [a_set("x" * 121, step("One", SHOT0))])
        self.assertEqual((st, d["ok"]), (400, False))
        st, d = self.put(ROD, [a_set("Only", step("y" * 61, SHOT0))])
        self.assertEqual((st, d["ok"]), (400, False))
        self.assertIn("60", d["error"])

    def test_unknown_project_and_stale_page(self):
        st, d = self.put("nope", [])
        self.assertEqual((st, d["error"]), (404, "Unknown project."))
        before = (self.tmp / "assets-manifest.json").read_bytes()
        st, d = self.call("PUT", "/api/project/%s/sets" % ROD,
                          {"sets": [], "version": "0000000000000000"})
        self.assertEqual((st, d.get("stale")), (409, True))
        self.assertEqual((self.tmp / "assets-manifest.json").read_bytes(), before)

    def test_sets_must_be_an_array(self):
        st, d = self.post("/api/project/%s/sets" % ROD, {"sets": "one"}, method="PUT")
        self.assertEqual((st, d["ok"]), (400, False))


class TestReplacedFiles(SetsCase):
    def test_a_replaced_image_moves_to_removed_media(self):
        st, d = self.put(ROD, [a_set("Studio shot → final", step("Studio shot", SHOT0, frm=0),
                                     step("Final", SPARE, True, False, 1), frm=0)])
        self.assertEqual((st, d["files_moved"]), (200, 1))
        self.assertFalse((self.tmp / SHOT1).exists(), "the replaced file leaves assets/")
        moved = [p.name for p in (self.tmp / "tools" / "removed-media").iterdir()]
        self.assertEqual(len(moved), 1)
        self.assertTrue(moved[0].endswith("02.jpg"), moved)

    def test_an_image_that_is_also_a_photograph_stays(self):
        st, d = self.put(ROD, [a_set("Studio shot → final", step("Studio shot", SPARE, frm=0),
                                     step("Final", SHOT1, True, False, 1), frm=0)])
        self.assertEqual((st, d["files_moved"]), (200, 0))
        self.assertTrue((self.tmp / SHOT0).is_file(), "photograph 01 still uses it")

    def test_an_image_still_used_by_another_set_stays(self):
        st, _d = self.put(ROD, [a_set("First", step("A", SHOT1)), a_set("Second", step("B", SHOT1))])
        self.assertEqual(st, 200)
        st, d = self.put(ROD, [a_set("Second", step("B", SHOT1, frm=0), frm=1)])
        self.assertEqual((st, d["files_moved"]), (200, 0))
        self.assertTrue((self.tmp / SHOT1).is_file())


class TestEverythingElse(SetsCase):
    def test_other_projects_and_files_are_untouched(self):
        manifest = self.data("assets-manifest.json")["projects"]
        others = {slug: json.dumps(d, sort_keys=True) for slug, d in manifest.items() if slug != ROD}
        projects = (self.tmp / "projects.json").read_bytes()
        st, _d = self.put(ROD, [a_set("Only", step("One", SPARE))])
        self.assertEqual(st, 200)
        after = self.data("assets-manifest.json")["projects"]
        self.assertEqual({slug: json.dumps(d, sort_keys=True) for slug, d in after.items() if slug != ROD},
                         others)
        self.assertEqual((self.tmp / "projects.json").read_bytes(), projects)

    def test_state_ships_sets_with_a_cache_token(self):
        sets = self.call("GET", "/api/state")[1]["manifest"]["projects"][ROD]["sets"]
        self.assertEqual(len(sets), 1)
        self.assertTrue(sets[0]["steps"][0]["v"], "a replaced image must never show from cache")
        self.assertEqual(sets[0]["title"], "Studio shot → final")
        self.assertNotIn("sets", self.call("GET", "/api/state")[1]["manifest"]["projects"]["beta"],
                         "no project gains an empty key")

    def test_the_route_is_ours_and_nothing_else_is(self):
        # A GET on this path must fall through to the manager's own 404: the module may not read a body.
        self.assertEqual(self.call("GET", "/api/project/%s/sets" % ROD)[0], 404)
        self.assertEqual(self.call("PUT", "/api/project/%s/set" % ROD)[0], 404)
        self.assertEqual(self.call("PUT", "/api/project/%s/videos" % ROD)[0], 400,
                         "the video route still owns its own path")

    def test_the_editor_script_is_served_without_registration(self):
        st, body = self.call("GET", "/sets.js")
        self.assertEqual(st, 200)
        self.assertIn(b"setsEditor", body)
        self.assertIn(b"sets.js", (ROOT / "tools" / "cms" / "index.html").read_bytes())


if __name__ == "__main__":
    unittest.main()
