"""Work groups and skill chips: site-config -> categories[] and capabilities[].

One route, PUT /api/taxonomy. Both lists are replaced whole and in order (array order is display
order: the Work page's groups and filter pills, the home page's capability strip). A row's `from`
names its old position, so a matched row keeps every field this form does not edit and unknown keys
survive a round trip.

Two things never change, because they are join keys rather than labels:

* a category `key` is the Work URL fragment (work.html#food-beverage), the project's `data-tags`,
  the sidebar group id and the target of CAP_LINKS["product"] in build.py;
* a capability `tag` is what projects[].tags[] matches to draw a skill chip.

Renaming either silently unlinks a section of the site, so the server refuses it. Deleting one is
refused while a project still points at it, and the refusal names the projects so the owner knows
what to move first. Everything here is a data write: build.py already renders both lists.
"""
import re

ROUTE = "/api/taxonomy"
KEY = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LABEL_MAX = 40
LINE_MAX = 160
MAX_NAMED = 6          # projects named in an "in use" refusal before the list is cut with an ellipsis


def dispatch(api, handler, method, path, q):
    """None when this module does not own the route; the response body otherwise.

    `api` is tools/cms.py itself, so this module uses api.LOCK, api.check_version, api.bad and
    api.commit without importing it (cms.py imports us, not the other way round).
    """
    if method != "PUT" or path != ROUTE:
        return None                      # not ours: the request body is never read
    raw = handler.json_body()
    with api.LOCK:
        api.check_version(raw.pop("version", None))
        out = save_taxonomy(api, raw)
        out.update(handler._after())     # the page keeps its data version and build state
        return out


def save_taxonomy(api, raw):
    """Replace one or both lists. A list left out of the payload is not touched."""
    cfg = api.read(api.FILES["config"])
    projects = [p for p in api.read(api.FILES["projects"]).get("projects", []) if isinstance(p, dict)]
    touched = []
    if "categories" in raw:
        cfg["categories"] = save_categories(api, raw.get("categories"), cfg.get("categories") or [], projects)
        touched.append("categories")
    if "capabilities" in raw:
        cfg["capabilities"] = save_capabilities(api, raw.get("capabilities"), cfg.get("capabilities") or [],
                                                projects)
        touched.append("capabilities")
    if not touched:
        api.bad("Nothing to save.")
    changed, _build = api.commit({api.FILES["config"]: cfg})
    return {"ok": True, "changed": changed, "touched": touched,
            "categories": cfg.get("categories") or [], "capabilities": cfg.get("capabilities") or []}


def save_categories(api, rows, old, projects):
    """The whole categories list, in order. New rows carry a fresh key; matched rows keep theirs."""
    rows = _as_list(api, rows, "categories must be an array.")
    old = [c for c in old if isinstance(c, dict) and c.get("key")]
    claimed, out, seen = set(), [], set()
    for row in rows:
        if not isinstance(row, dict):
            api.bad("Each group must be an object.")
        base = _origin(old, row.get("from"), claimed)
        if base:
            key = base["key"]
            if row.get("key") not in (None, key):
                api.bad("A group's key cannot change (it is the Work page anchor, and every project "
                        "that points at this group). Add a new group instead.")
        else:
            key = _new_key(api, row.get("key"), "group")
        if key in seen:
            api.bad("Two groups cannot share the key %s." % key)
        seen.add(key)
        entry = {"key": key, "label": _label(api, row, base, "group"),
                 "personal": bool(row.get("personal"))}
        for k, v in base.items():        # unknown keys on a matched row survive
            entry.setdefault(k, v)
        out.append(entry)
    _refuse_removal(api, "group", "category", old, seen, projects, by_tag=False)
    return out


def save_capabilities(api, rows, old, projects):
    """The whole capabilities list, in order. New rows carry a fresh tag; matched rows keep theirs."""
    rows = _as_list(api, rows, "capabilities must be an array.")
    old = [c for c in old if isinstance(c, dict) and c.get("tag")]
    claimed, out, seen = set(), [], set()
    for row in rows:
        if not isinstance(row, dict):
            api.bad("Each skill must be an object.")
        base = _origin(old, row.get("from"), claimed)
        if base:
            tag = base["tag"]
            if row.get("tag") not in (None, tag):
                api.bad("A skill's tag cannot change (it is what links the skill to every project "
                        "that shows it). Add a new skill instead.")
        else:
            tag = _new_key(api, row.get("tag"), "skill")
        if tag in seen:
            api.bad("Two skills cannot share the tag %s." % tag)
        seen.add(tag)
        entry = {"tag": tag, "label": _label(api, row, base, "skill"),
                 "line": _line(api, row, base)}
        for k, v in base.items():
            entry.setdefault(k, v)
        out.append(entry)
    _refuse_removal(api, "skill", "tags", old, seen, projects, by_tag=True)
    return out


# --------------------------------------------------------------------------- helpers
def _as_list(api, value, message):
    """The value as a list, or a 400. The `return []` is unreachable: api.bad always raises."""
    if not isinstance(value, list):
        api.bad(message)
        return []
    return value


def _origin(rows, value, claimed):
    """The stored row a `from` index names, or {} for a new one. An index is claimable once."""
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return {}
    if not 0 <= value < len(rows) or value in claimed:
        return {}
    claimed.add(value)
    return dict(rows[value])


def _new_key(api, value, kind):
    """A key/tag for a row that did not exist before: lowercase words joined by hyphens."""
    key = str(value if value is not None else "").strip()
    word = "key" if kind == "group" else "tag"
    if not key:
        api.bad("A new %s needs a %s." % (kind, word))
    if not KEY.match(key):
        api.bad("A %s %s is lowercase words joined by hyphens, like %s."
                % (kind, word, "food-beverage" if kind == "group" else "short-form"))
    return key


def _label(api, row, base, kind):
    text = str(row["label"] if "label" in row else (base.get("label") or "")).strip()
    if not text:
        api.bad("Every %s needs a label." % kind)
    if len(text) > LABEL_MAX:
        api.bad("That label is too long: %d characters at most." % LABEL_MAX)
    return text


def _line(api, row, base):
    text = str(row["line"] if "line" in row else (base.get("line") or "")).strip()
    if not text:
        api.bad("Every skill needs a line.")
    if len(text) > LINE_MAX:
        api.bad("That line is too long: %d characters at most." % LINE_MAX)
    return text


def _refuse_removal(api, kind, field, old, seen, projects, by_tag):
    """A list may not drop a row a project still points at, and the message names the projects."""
    for row in old:
        key = row.get("tag") if by_tag else row.get("key")
        if not key or key in seen:
            continue
        users = [str(p.get("slug") or "?") for p in projects if _points_at(p, field, key)]
        if users:
            api.bad(_in_use(kind, users))


def _points_at(project, field, key):
    """True when a project still uses `key` through categories' `category` or capabilities' `tags`."""
    value = project.get(field)
    if isinstance(value, list):
        return key in value
    return value == key


def _in_use(kind, slugs):
    """"3 projects still use this group: a, b, c. Move them first." — names, so the owner can act."""
    named = ", ".join(slugs[:MAX_NAMED])
    if len(slugs) > MAX_NAMED:
        named += ", \u2026"
    one = len(slugs) == 1
    return ("%d project%s still %s this %s: %s. Move %s first."
            % (len(slugs), "" if one else "s", "uses" if one else "use", kind, named,
               "it" if one else "them"))
