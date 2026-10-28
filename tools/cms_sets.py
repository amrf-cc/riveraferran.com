"""Comparison sets: the real shot -> AI step -> final rows of a project page.

One route, PUT /api/project/<slug>/sets: the whole list, in order, exactly like the video list
(`set_videos` in cms.py). A row's `from` names its old position, so an existing set or step keeps
every field this form does not edit and unknown keys survive a round trip.

`dispatch()` is the shared extension shape: cms.py imports this module, keeps it in EXTENSIONS and
calls it from Handler.route() before its own if-chain. The response body is merged with
`handler._after()` there, so the page keeps its data version and build state.
"""
import re

ROUTE = re.compile(r"/api/project/([a-z0-9-]+)/sets")

MAX_SETS = 6          # past six rows a page stops reading as a comparison
MAX_STEPS = 8         # the editor offers 1-6; the server leaves room above it
TITLE_MAX = 120
LABEL_MAX = 60


def dispatch(api, handler, method, path, q):
    """None when this module does not own the route; the response body otherwise.

    `api` is tools/cms.py itself, so this module uses api.LOCK, api.check_version, api.bad and
    api.commit without importing it (cms.py imports us, not the other way round).
    """
    match = ROUTE.fullmatch(path)
    if method != "PUT" or not match:
        return None                      # not ours: the request body is never read
    raw = handler.json_body()
    with api.LOCK:
        api.check_version(raw.pop("version", None))
        out = save_sets(api, match.group(1), raw.get("sets"))
        out.update(handler._after())
        return out


def save_sets(api, slug, rows):
    """Replace a project's whole comparison-set list, in order."""
    rows = _as_list(api, rows, "sets must be an array.")
    projects = api.read(api.FILES["projects"])
    if not api.SLUG.match(slug or "") or slug not in {p.get("slug") for p in projects.get("projects", [])}:
        api.bad("Unknown project.", 404)
    if len(rows) > MAX_SETS:
        api.bad("A project can have at most %d comparison sets." % MAX_SETS)
    manifest = api.read(api.FILES["manifest"])
    entry = manifest.setdefault("projects", {}).setdefault(slug, {"photos": [], "videos": []})
    old = [s for s in (entry.get("sets") or []) if isinstance(s, dict)]
    used, new = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            api.bad("Each comparison set must be an object.")
        base = _origin(old, row.get("from"), used)
        given = _as_list(api, row.get("steps"), "Set %d needs a list of steps." % n)
        if not given:
            api.bad("A set needs at least one step.")
        if len(given) > MAX_STEPS:
            api.bad("A set can have at most %d steps." % MAX_STEPS)
        old_steps = [s for s in (base.get("steps") or []) if isinstance(s, dict)]
        seen, steps = set(), []
        for m, step in enumerate(given, 1):
            if not isinstance(step, dict):
                api.bad("Each step must be an object.")
            kept = _origin(old_steps, step.get("from"), seen)
            kept["label"] = _text(api, step.get("label"), LABEL_MAX, "Step %d label" % m)
            # An image is optional: the owner may build a set before choosing the picture, and the
            # release gate already refuses to publish a step without one (build.py compare_list_html).
            image = str(step["image"] if "image" in step else (kept.get("image") or "")).strip()
            if image:
                if not image.startswith("assets/"):
                    api.bad("Step %d: that image must live under assets/." % m)
                try:
                    api.existing_asset(image, "Step %d image" % m)
                except api.ApiError:
                    api.bad("Step %d: that image does not exist." % m)
            kept["image"] = image
            # A flag is stored only while it is ticked, exactly like a photo's: the owner's data holds real
            # changes, so Save on an untouched set writes nothing and a ticked-then-cleared flag leaves no key.
            for flag in ("ai", "composite"):
                if step.get(flag):
                    kept[flag] = True
                else:
                    kept.pop(flag, None)
            steps.append(kept)
        base["title"] = _text(api, row.get("title"), TITLE_MAX, "Set %d title" % n)
        base["steps"] = steps
        new.append(base)
    gone = _left_images(old, new)
    entry["sets"] = new
    changed, _build = api.commit({api.FILES["manifest"]: manifest})
    moved = api.trash_files(gone)        # never deletes: skips anything still referenced elsewhere
    return {"ok": True, "changed": changed, "sets": len(new),
            "steps": sum(len(s["steps"]) for s in new),
            "steps_without_image": sum(1 for s in new for st in s["steps"] if not st.get("image")),
            "files_moved": len(moved)}


def _as_list(api, value, message):
    """The value as a list, or a 400. The `return []` is unreachable: api.bad always raises."""
    if not isinstance(value, list):
        api.bad(message)
        return []
    return value


def _text(api, value, most, what):
    text = str(value if value is not None else "").strip()
    if len(text) > most:
        api.bad("%s is longer than %d characters." % (what, most))
    return text


def _origin(rows, value, used):
    """The stored row a `from` index names, or {} for a new one. An index is claimable once."""
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return {}
    if not 0 <= value < len(rows) or value in used:
        return {}
    used.add(value)
    return dict(rows[value])


def _left_images(old, new):
    """Step images the new list no longer names. trash_files() protects anything else still in use."""
    def images(sets):
        found = set()
        for s in sets:
            for st in s.get("steps") or []:
                if isinstance(st, dict) and st.get("image"):
                    found.add(st["image"])
        return found
    return sorted(images(old) - images(new))
