"""Paid Social: the intro line and the two ad lists on media.json -> paid_social.

One route, PUT /api/paid:

    {"version": "…", "intro": "", "video_ads": [...], "static_ads": [...]}

Each list is replaced whole, in order, exactly like the video list (`set_videos` in cms.py) and the
comparison sets (cms_sets.py): a row's `from` names its old position, the stored object is copied first
and only the fields this form edits are overwritten, so anything the form does not know survives a
round trip. A missing key is left alone, so the three sections of the pane save independently.

`dispatch()` is the shared extension shape: cms.py imports this module, keeps it in EXTENSIONS and calls
it from Handler.route() before its own if-chain. The response body is merged with `handler._after()`
there, so the page keeps its data version and its build state.
"""
INTRO_MAX = 240
MAX_VIDEO = 40
MAX_STATIC = 60
VIDEO_RATIO = "9 / 16"     # paid video runs vertical; the editor offers the same default
STATIC_RATIO = "1 / 1"     # what ad_card_html falls back to (build.py, ad_card_html)


def dispatch(api, handler, method, path, q):
    """None when this module does not own the route; the response body otherwise.

    `api` is tools/cms.py itself, so this module uses api.LOCK, api.check_version, api.bad,
    api.commit and api.trash_files without importing it (cms.py imports us, not the other way round).
    """
    if method != "PUT" or path != "/api/paid":
        return None                      # not ours: the request body is never read
    raw = handler.json_body()
    with api.LOCK:
        api.check_version(raw.pop("version", None))
        out = save_paid(api, raw)
        out.update(handler._after())
        return out


def save_paid(api, raw):
    """Write the paid-social block of media.json: the intro and/or either ad list."""
    media = api.read(api.FILES["media"])
    stored = media.get("paid_social")
    block = dict(stored) if isinstance(stored, dict) else {}
    touched, gone = [], []

    if "intro" in raw:
        intro = raw["intro"]
        if not isinstance(intro, str):
            api.bad("intro must be text.")
        text = intro.replace("\r\n", "\n").strip()
        if len(text) > INTRO_MAX:
            api.bad("The intro is too long: %d characters at most." % INTRO_MAX)
        block["intro"] = text
        touched.append("intro")

    for key, most, save_rows in (("video_ads", MAX_VIDEO, _video_ads),
                                 ("static_ads", MAX_STATIC, _static_ads)):
        if key not in raw:
            continue
        rows = raw[key]
        if not isinstance(rows, list):
            api.bad("%s must be an array." % key)
        if len(rows) > most:
            api.bad("At most %d %s." % (most, key.replace("_", " ")))
        old = [r for r in (block.get(key) or []) if isinstance(r, dict)]
        new = save_rows(api, rows, old)
        block[key] = new
        gone += _left_images(api, old, new, "poster" if key == "video_ads" else "image")
        touched.append(key)

    if not touched:
        api.bad("Nothing to save.")
    media["paid_social"] = block
    changed, _build = api.commit({api.FILES["media"]: media})
    moved = api.trash_files(gone)        # never deletes: skips anything still referenced elsewhere
    return {"ok": True, "changed": changed, "touched": touched,
            "video_ads": len(block.get("video_ads") or []),
            "static_ads": len(block.get("static_ads") or []),
            "files_moved": len(moved)}


def _video_ads(api, rows, old):
    """A video ad: a Livid link (or a local mp4 the form does not edit) in its placement ratio."""
    used, new = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            api.bad("Each video ad must be an object.")
        base = _origin(old, row.get("from"), used)
        label = "Video ad %d" % n
        out = base
        out["title"] = api.coerce("text!", _field(row, base, "title"), label + " title", set())
        out["campaign"] = api.coerce("text", _field(row, base, "campaign"), label + " campaign", set())
        link = _field(row, base, "livid")
        out["livid"] = api.coerce("livid", link, label + " Livid link", set(), base) if link else ""
        if not out["livid"] and not out.get("mp4"):
            api.bad("%s needs a Livid link." % label)
        out.setdefault("livid", "")
        out.setdefault("mp4", "")
        out["ratio"] = api.coerce("ratio", _field(row, base, "ratio") or base.get("ratio") or VIDEO_RATIO,
                                  label + " shape", set())
        if "poster" in row:
            out["poster"] = api.coerce("asset", row.get("poster"), label + " poster", set())
        out.setdefault("poster", "")
        new.append(out)
    return new


def _static_ads(api, rows, old):
    """A still: an existing image, shown whole in its placement ratio."""
    used, new = set(), []
    for n, row in enumerate(rows, 1):
        if not isinstance(row, dict):
            api.bad("Each static ad must be an object.")
        base = _origin(old, row.get("from"), used)
        label = "Static ad %d" % n
        out = base
        out["title"] = api.coerce("text!", _field(row, base, "title"), label + " title", set())
        out["campaign"] = api.coerce("text", _field(row, base, "campaign"), label + " campaign", set())
        image = _field(row, base, "image")
        if not image:
            api.bad("%s needs an image. Upload one first." % label)
        out["image"] = api.coerce("asset", image, label + " image", set())
        out["ratio"] = api.coerce("ratio", _field(row, base, "ratio") or base.get("ratio") or STATIC_RATIO,
                                  label + " shape", set())
        new.append(out)
    return new


def _field(row, base, key):
    """The value the form sent for `key`, or the stored one when the form left it out."""
    if key in row:
        return row[key]
    return base.get(key) or ""


def _origin(rows, value, used):
    """The stored row a `from` index names, or {} for a new one. An index is claimable once."""
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return {}
    if not 0 <= value < len(rows) or value in used:
        return {}
    used.add(value)
    return dict(rows[value])


def _left_images(api, old, new, key):
    """The files the new list no longer names, including a replaced poster on a kept row."""
    kept = {r[key] for r in new if r.get(key)}
    left = [r.get(key) for r in old if r.get(key) and r.get(key) not in kept]
    return [p for p in left if p]
