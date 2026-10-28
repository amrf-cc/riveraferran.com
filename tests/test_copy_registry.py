"""The editable-copy registry (templates/copy.json), checked against build.py.

The registry is data, so nothing but a test keeps it in step with the code: a key
typed in a call site but missing from the file would silently render as "" and a
row nobody renders would sit in the manager doing nothing. Both directions are
checked here, plus the shape of the file and the placeholders the templates ask
for (string.Template.safe_substitute hides a typo by leaving it in the page).
"""
import ast
import json
import re
import unittest

import support

ROOT = support.ROOT
KEY_RE = re.compile(r"^[a-z][a-z0-9]*(\.[a-z0-9_]+)+$")
TEMPLATE_TOKEN = re.compile(r"\$\{([A-Za-z0-9_]+)\}")
PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
T_CALLS = ("t", "t_html", "t_text")
# The only defaults allowed to carry a {placeholder} — the build fills each
# with markup or a value it generates, never with owner text.
DOCUMENTED_PLACEHOLDERS = {
    "ai.tools_mark": {"label"},
    "contact.lede": {"email"},
    "contact.form.note": {"privacy"},
    "contact.msg.error": {"email"},
}
FIELDS = {"key", "page", "section", "label", "default", "max"}


def registry():
    return json.loads((ROOT / "templates" / "copy.json").read_text(encoding="utf-8"))


def rows():
    return registry()["keys"]


def keys():
    return {row["key"] for row in rows()}


def build_source():
    return (ROOT / "build.py").read_text(encoding="utf-8")


def build_tree():
    return ast.parse(build_source())


def copy_calls(tree):
    """{key: [method]} for every self.t('literal', ...) style call in build.py."""
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in T_CALLS:
            continue
        arg = node.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            out.setdefault(arg.value, []).append(func.attr)
    return out


def string_constants(tree):
    return {n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)}


def template_files():
    out = []
    for pattern in ("*.tmpl", "legal/*.html"):
        out.extend(sorted((ROOT / "templates").glob(pattern)))
    return out


class TestRegistryShape(unittest.TestCase):
    def test_keys_are_unique_and_well_formed(self):
        seen = set()
        for row in rows():
            self.assertEqual(set(row), FIELDS, row["key"])
            self.assertRegex(row["key"], KEY_RE)
            self.assertNotIn(row["key"], seen, "duplicate key %s" % row["key"])
            seen.add(row["key"])
            for field in ("page", "section", "label"):
                self.assertTrue(str(row[field]).strip(), "%s has an empty %s" % (row["key"], field))

    def test_every_row_sits_on_a_registered_page_and_fits_its_limit(self):
        pages = [p["key"] for p in registry()["pages"]]
        self.assertEqual(len(pages), len(set(pages)))
        for row in rows():
            self.assertIn(row["page"], pages, row["key"])
            self.assertIsInstance(row["max"], int, row["key"])
            self.assertGreaterEqual(row["max"], len(row["default"]), row["key"])
            self.assertTrue(row["default"].strip(), "%s has an empty default" % row["key"])
            self.assertNotIn("\r", row["default"], row["key"])

    def test_labels_are_unique_within_a_page_and_section(self):
        """Two fields with the same label in the same section would be
        indistinguishable in the manager."""
        seen = {}
        for row in rows():
            where = (row["page"], row["section"])
            seen.setdefault(where, set())
            self.assertNotIn(row["label"], seen[where],
                             "%s repeats %r in %s / %s" % (row["key"], row["label"], where[0], where[1]))
            seen[where].add(row["label"])

    def test_only_the_documented_defaults_carry_placeholders(self):
        for row in rows():
            found = set(PLACEHOLDER.findall(row["default"]))
            want = DOCUMENTED_PLACEHOLDERS.get(row["key"], set())
            self.assertEqual(found, want, row["key"])

    def test_the_registry_pages_are_the_ones_the_brief_names(self):
        self.assertEqual([p["key"] for p in registry()["pages"]],
                         ["everypage", "home", "work", "case", "reels", "ai", "paid",
                          "about", "contact", "legal"])


class TestRegistryCoversTheBuild(unittest.TestCase):
    def test_every_key_the_build_asks_for_is_in_the_registry(self):
        used = copy_calls(build_tree())
        missing = sorted(k for k in used if k not in keys())
        self.assertEqual(missing, [], "build.py uses copy keys the registry does not list")
        self.assertGreater(len(used), 80, "the AST scan found almost nothing to check")

    def test_every_registry_key_is_used_by_the_build(self):
        """An unused row would be a field in the manager that changes nothing."""
        constants = string_constants(build_tree())
        unused = sorted(k for k in keys() if k not in constants)
        self.assertEqual(unused, [], "registry keys no build.py string mentions")

    def test_the_call_sites_use_a_known_accessor(self):
        used = copy_calls(build_tree())
        for key, methods in used.items():
            self.assertTrue(set(methods) <= set(T_CALLS), key)

    def test_module_constants_still_agree_with_the_registry(self):
        """build.py keeps a few literals that other tests and helpers read
        (support.py compares against them); they must not drift from the
        registry rows that replaced them."""
        import build
        defaults = build.copy_defaults()
        self.assertEqual(build.COMPARE_HEADING, defaults["case.compare"])
        self.assertEqual(build.OTHER_WORK_LABEL, defaults["work.other"])
        self.assertEqual(build.PERSONAL_LABEL, defaults["work.personal"])
        self.assertEqual(build.AD_SIZES, defaults["paid.sizes"])

    def test_navigation_rows_match_nav_items(self):
        import build
        self.assertEqual([copy_key for _, copy_key, _ in build.NAV_ITEMS],
                         ["nav.work", "nav.reels", "nav.ai", "nav.paid", "nav.about", "nav.contact"])


class TestTemplatePlaceholders(unittest.TestCase):
    def test_every_template_placeholder_is_passed_by_the_build(self):
        """safe_substitute leaves an unknown ${TOKEN} in the page verbatim, so
        this scan is the only thing that catches a typo."""
        source = build_source()
        missing = []
        for path in template_files():
            for token in set(TEMPLATE_TOKEN.findall(path.read_text(encoding="utf-8"))):
                if token not in source:
                    missing.append((path.name, token))
        self.assertEqual(missing, [], "template placeholders build.py never passes")

    def test_the_templates_no_longer_hard_code_the_copy_they_gained(self):
        """The literals the registry replaced are gone from the templates; a
        second copy of the same sentence would defeat every override."""
        templates = {p.relative_to(ROOT / "templates").as_posix(): p.read_text(encoding="utf-8")
                     for p in template_files()}
        for name, literal in (("home.main.tmpl", "Selected work"),
                              ("home.main.tmpl", "Featured projects"),
                              ("home.main.tmpl", "What I do"),
                              ("home.reel.tmpl", "Showreel"),
                              ("work.main.tmpl", "No projects in this group yet."),
                              ("case.main.tmpl", "Like what you see?"),
                              ("contact_form.html.tmpl", "Send message"),
                              ("404.html.tmpl", "Back to sharpness"),
                              ("legal/privacy.body.html", "Effective October 14, 2026")):
            self.assertNotIn(literal, templates[name], "%s still hard-codes %r" % (name, literal))


class TestSiteLookup(unittest.TestCase):
    """Site.t(): the owner's value when set, the default otherwise."""

    def site(self, copy=None):
        import build
        return build.Site({"site-config.json": {"name": "Test Person", "copy": copy or {}}})

    def test_a_missing_override_renders_the_default(self):
        import build
        self.assertEqual(self.site().t("work.heading"), build.copy_defaults()["work.heading"])

    def test_an_override_wins_and_blank_values_fall_back(self):
        self.assertEqual(self.site({"work.heading": "Selected projects"}).t("work.heading"),
                         "Selected projects")
        for blank in ("", "   ", None, 5):
            self.assertEqual(self.site({"work.heading": blank}).t("work.heading"), "Work", repr(blank))

    def test_placeholders_are_filled_by_the_caller(self):
        site = self.site({"contact.msg.error": "Write to {email} instead."})
        self.assertEqual(site.t("contact.msg.error", email="a@example.com"),
                         "Write to a@example.com instead.")

    def test_used_copy_records_what_was_rendered(self):
        site = self.site()
        site.t("work.heading")
        site.t_html("contact.form.note", privacy="X")
        self.assertEqual(site.used_copy, {"work.heading", "contact.form.note"})

    def test_t_html_escapes_the_text_but_not_the_markup_it_is_handed(self):
        site = self.site({"contact.form.note": 'Tom & Jerry say "hi" — see {privacy}.'})
        out = site.t_html("contact.form.note", privacy="<a>Privacy</a>")
        self.assertIn("Tom &amp; Jerry", out)
        self.assertIn("&quot;hi&quot;", out)
        self.assertIn("<a>Privacy</a>", out)

    def test_an_unknown_key_renders_nothing_and_does_not_crash(self):
        self.assertEqual(self.site().t("nope.nothing"), "")


if __name__ == "__main__":
    unittest.main()
