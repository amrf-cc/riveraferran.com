#!/usr/bin/env python3
"""Generate the static site in place from templates + JSON data. Stdlib only.

    python3 build.py            draft build (default): placeholders, confirm pills,
                                draft-only projects and a draft banner are rendered
                                so every gap is visible in the preview.
    python3 build.py --release  scans ALL data first. Any placeholder, confirm list,
                                draft_only item, empty showreel, empty resume_pdf or
                                empty domain is listed (file -> path -> value) and the
                                build exits 1 WITHOUT writing a single page.

A PLACEHOLDER is any string value that starts with "[" and ends with "]".
"""
import hashlib
import html
import json
import sys
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent

DATA_FILES = ("site-config.json", "projects.json", "media.json", "assets-manifest.json")

# Nav order: (page_key, label, file). Labels are already HTML-escaped.
NAV_ITEMS = (
    ("work", "Work", "work.html"),
    ("reels", "Reels", "reels.html"),
    ("ai", "AI &amp; Workflow", "ai.html"),
    ("paid", "Paid Social", "paid-social.html"),
    ("about", "About", "about.html"),
    ("contact", "Contact", "contact.html"),
)

# AI & Workflow page: the feature project and the "Ran live" projects, by slug.
AI_FEATURE = "ai-production-assistant"
AI_LIVE = ("rodowita-water", "chip-city")

# Pages that can be switched off while their content is still being gathered
# ("hidden_pages": ["paid-social"] in site-config.json). A hidden page is not
# built, is deleted if an earlier build left it behind, drops out of the nav, and
# every link that pointed at it (home capability strip, case-study tag pills)
# becomes plain text. Nothing else about the release gate changes.
HIDEABLE_PAGES = ("paid-social",)


def ai_live_slugs(media):
    """Projects shown as 'Ran live' on the AI page: media.json `ai_live` when it
    is a list (an empty list shows none), else the AI_LIVE default."""
    listed = (media or {}).get("ai_live")
    return tuple(listed) if isinstance(listed, list) else AI_LIVE

# Ad placement sizes the Paid Social page asks for while a list is empty.
AD_SIZES = "9:16, 4:5, 1:1"

# Capability tag -> the page that shows that skill (root-relative to the site,
# prefixed per page). The home capability strip and the case-study tag pills
# both link through this one map. "" (or a tag missing here) renders as plain
# text with no link.
CAP_LINKS = {
    "social": "reels.html",
    "motion": "reels.html#motion",
    "paid": "paid-social.html",
    "ai": "ai.html",
    "product": "work.html#food-beverage",
    "campaigns": "work.html#commercial-corporate",
    "founder": "",
}

# Work page: heading for projects that belong to no category (All view only),
# and the note personal categories carry on their heading and cards.
OTHER_WORK_LABEL = "Other work"
PERSONAL_LABEL = "Personal work"

# Media disclosure. A manifest photo/video (or a comparison-set step) may carry
# "ai": true or "composite": true. The AI wording comes from site-config
# `ai_label`; composites always read "Composite". The comparison-set heading is
# the one the case study and the AI page share.
COMPOSITE_LABEL = "Composite"
COMPARE_HEADING = "Real shot → AI step → final"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load(name, root=None):
    path = (root or ROOT) / name
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_data(root=None):
    return {name: load(name, root) for name in DATA_FILES}


def tmpl(name):
    return Template((ROOT / "templates" / name).read_text(encoding="utf-8"))


def esc(s):
    return html.escape(str(s), quote=True)


def slugify(s):
    """'Product reveal' -> 'product-reveal' (filter keys for the Reels page)."""
    out, dash = [], False
    for ch in plain(s).lower():
        if ch.isalnum():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).strip("-") or "other"


# ---------------------------------------------------------------------------
# Placeholders and the release gate
# ---------------------------------------------------------------------------

def is_ph(v):
    """A placeholder is a string that starts with '[' and ends with ']'."""
    return isinstance(v, str) and len(v) >= 2 and v.startswith("[") and v.endswith("]")


def ph_text(v):
    return v[1:-1].strip()


def plain(v):
    """Text for attributes and meta tags: placeholders lose their brackets."""
    if v is None:
        return ""
    return ph_text(v) if is_ph(v) else str(v)


def _walk(node, path):
    """Yield (path, key, value) for every dict entry and list item."""
    if isinstance(node, dict):
        for k, v in node.items():
            p = "%s.%s" % (path, k) if path else k
            yield p, k, v
            if isinstance(v, (dict, list)):
                for item in _walk(v, p):
                    yield item
    elif isinstance(node, list):
        for i, v in enumerate(node):
            label = v.get("slug") if isinstance(v, dict) and v.get("slug") else i
            p = "%s[%s]" % (path, label)
            yield p, None, v
            if isinstance(v, (dict, list)):
                for item in _walk(v, p):
                    yield item


def scan_release(data):
    """Every data item that blocks a release, as (file, path, value) tuples."""
    out = []
    for fname in DATA_FILES:
        tree = data.get(fname) or {}
        for path, key, value in _walk(tree, ""):
            if key in ("confirm", "credits_confirm", "draft_only"):
                if value:
                    out.append((fname, path, value))
                continue
            if is_ph(value):
                out.append((fname, path, value))
    config = data.get("site-config.json") or {}
    showreel = config.get("showreel") or {}
    # "hidden": true is the owner's call to publish without a showreel for now:
    # the section and the hero button are left out, so there is nothing to demand.
    for slot in (() if showreel.get("hidden") else ("vertical", "horizontal")):
        s = showreel.get(slot) or {}
        if not (s.get("livid") or s.get("mp4")):
            out.append(("site-config.json", "showreel.%s" % slot,
                        "empty showreel (no livid or mp4)"))
    if not config.get("resume_pdf"):
        out.append(("site-config.json", "resume_pdf", "empty (résumé PDF pending)"))
    if not (config.get("domain") or "").strip():
        out.append(("site-config.json", "domain", "empty"))
    return out


def fmt_value(v):
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False) if (is_ph(v) or not v) else v
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Media helpers
# ---------------------------------------------------------------------------

_VERSIONS = {}


def asset_url(rel):
    """Repo-relative media path plus a content fingerprint (?v=<8 hex>).

    Photo files get renumbered when they are reordered, so the same URL can
    start serving a different image. A content hash changes the URL exactly
    when the bytes change, and leaves untouched files byte-stable across
    rebuilds."""
    if not rel:
        return rel
    if rel not in _VERSIONS:
        path = ROOT / rel
        _VERSIONS[rel] = (hashlib.sha1(path.read_bytes()).hexdigest()[:8]
                          if path.is_file() else "")
    v = _VERSIONS[rel]
    return "%s?v=%s" % (rel, v) if v else rel


def standalone_target(p):
    """The page#anchor a standalone project lives at, or "" for a normal
    (Work) project. Root-relative to the site, no leading slash."""
    v = p.get("standalone") if isinstance(p, dict) else None
    return v.strip() if isinstance(v, str) and v.strip() and not is_ph(v) else ""


def standalone_anchor(p):
    """The anchor part of a standalone target ("assistant"), or ""."""
    return standalone_target(p).partition("#")[2]


def is_remote(url):
    return url.startswith(("http://", "https://", "//"))


def media_flags(item):
    """(ai, composite) for a manifest photo, video, reel or set step. Missing
    flags read as false, and a missing/None entry is simply unflagged."""
    if not isinstance(item, dict):
        return False, False
    return bool(item.get("ai")), bool(item.get("composite"))



# ---------------------------------------------------------------------------
# Site renderer
# ---------------------------------------------------------------------------

class Site(object):
    def __init__(self, data, release=False):
        self.release = release
        self.config = data.get("site-config.json") or {}
        self.manifest = (data.get("assets-manifest.json") or {}).get("projects", {})
        self.media = data.get("media.json") or {}
        self.projects = (data.get("projects.json") or {}).get("projects", [])
        self.visible = [p for p in self.projects if not p.get("hidden")
                        and not (release and p.get("draft_only"))]
        # Work = visible projects that are not standalone. A standalone
        # project ("standalone": "<page>#<anchor>") lives on its own page: no
        # Work card, no work/<slug>.html, not in the pager or the groups.
        self.work = [p for p in self.visible if not standalone_target(p)]
        self.cap_labels = {c.get("tag"): c.get("label", c.get("tag"))
                           for c in self.config.get("capabilities", [])}
        # Work groups, in display order. A project whose category is "" or a
        # key missing here belongs to no group (Work "All" view only).
        self.categories = [c for c in self.config.get("categories", [])
                           if isinstance(c, dict) and c.get("key")]
        self.cat_by_key = {c["key"]: c for c in self.categories}
        self.hero_paths = {self.config.get("hero_image"),
                           self.config.get("hero_image_phone")} - {None, ""}
        self.hidden_pages = {x for x in (self.config.get("hidden_pages") or [])
                             if x in HIDEABLE_PAGES}
        self.live_slugs = ai_live_slugs(self.media)
        # Placeholders the templates had to invent (no data string behind
        # them). They block a release exactly like data placeholders.
        self.generated = []
        self.pages = {}

    # -- small render helpers ---------------------------------------------

    def txt(self, v):
        """Inline text: placeholders render as a dashed chip."""
        if v is None:
            return ""
        if is_ph(v):
            return '<span class="ph-chip" title="To add">%s</span>' % esc(ph_text(v))
        return esc(v)

    def box(self, v, label="To add"):
        """Block placeholder: dashed box with a small 'To add' label."""
        return ('<div class="ph-box"><span class="ph-label">%s</span><p>%s</p></div>'
                % (esc(label), esc(ph_text(v) if is_ph(v) else v)))

    def gen_box(self, where, text):
        """A placeholder generated by the template itself (not from data)."""
        self.generated.append((where, text))
        return self.box(text)

    def para(self, v, cls=""):
        """A paragraph that becomes a placeholder box when its value is one."""
        if not v:
            return ""
        if is_ph(v):
            return self.box(v)
        return '<p%s>%s</p>' % ((' class="%s"' % cls) if cls else "", esc(v))

    def confirm_pill(self, fields):
        if self.release or not fields:
            return ""
        names = ", ".join(str(f).replace("_", " ") for f in fields)
        return '<span class="confirm-pill">Confirm: %s</span>' % esc(names)

    def ribbon(self, p):
        if p.get("draft_only") and not self.release:
            return '<span class="draft-ribbon">Draft</span>'
        return ""

    def page_hidden(self, target):
        """True when a link target ("paid-social.html#x") is a hidden page."""
        stem = (target or "").partition("#")[0]
        if stem.endswith(".html"):
            stem = stem[:-5]
        return stem in self.hidden_pages

    def facts(self, p):
        return " · ".join(self.txt(p.get(k)) for k in ("role", "years", "location") if p.get(k))

    def pills(self, tags, root_prefix=None):
        """Capability tag pills (case studies). With root_prefix set, a tag
        with a CAP_LINKS target links to the page that shows that skill."""
        if not tags:
            return ""
        items = []
        for t in tags:
            label = esc(self.cap_labels.get(t, t))
            target = CAP_LINKS.get(t) or ""
            if self.page_hidden(target):
                target = ""
            if root_prefix is None or not target:
                items.append('<li class="pill">%s</li>' % label)
            else:
                items.append('<li><a class="pill" href="%s%s">%s</a></li>'
                             % (root_prefix, esc(target), label))
        return '<ul class="pills">%s</ul>' % "".join(items)

    # -- work groups (categories) ------------------------------------------

    def category(self, p):
        """The project's category dict, or None (no group / unknown key)."""
        return self.cat_by_key.get(p.get("category") or "")

    def cat_key(self, p):
        c = self.category(p)
        return c["key"] if c else ""

    def personal_note(self):
        return '<span class="personal-note">%s</span>' % esc(PERSONAL_LABEL)

    def cat_label_html(self, p, href=None):
        """Category label for cards and case studies, plus the personal note
        for personal categories. Empty when the project has no group."""
        c = self.category(p)
        if not c:
            return ""
        label = self.txt(c.get("label") or c["key"])
        if href:
            label = '<a class="cat-link" href="%s">%s</a>' % (esc(href), label)
        note = (" " + self.personal_note()) if c.get("personal") else ""
        return label + note

    def project_href(self, p, rp):
        """Every project link goes through here: work/<slug>.html normally,
        the standalone target (e.g. ai.html#assistant) otherwise."""
        target = standalone_target(p)
        if target:
            return esc(target if is_remote(target) else rp + target)
        return "%swork/%s.html" % (rp, esc(p["slug"]))

    def work_groups(self):
        """[(category or None, [projects])] for the Work page: categories in
        config order (only those with a Work project), projects in data
        order within each; projects without a group last, under None.
        Standalone projects are not Work and never appear here."""
        groups = []
        for c in self.categories:
            members = [p for p in self.work if self.cat_key(p) == c["key"]]
            if members:
                groups.append((c, members))
        rest = [p for p in self.work if not self.cat_key(p)]
        if rest:
            groups.append((None, rest))
        return groups

    # -- covers and cards ---------------------------------------------------

    def related_reels(self, slug):
        return [r for r in self.media.get("reels", []) if r.get("project") == slug]

    # -- AI / composite disclosure -----------------------------------------

    def badge_labels(self, item):
        """Plain-text labels an item earns: the config's AI label when `ai`,
        "Composite" when `composite`. [] means no badge at all."""
        ai, composite = media_flags(item)
        out = []
        if ai:
            label = plain(self.config.get("ai_label") or "")
            if label:
                out.append(label)
        if composite:
            out.append(COMPOSITE_LABEL)
        return out

    def badge_attr(self, item):
        """The badge text for data-attributes (the lightbox caption)."""
        return " · ".join(self.badge_labels(item))

    def badge_html(self, item, cls="ai-badge"):
        labels = self.badge_labels(item)
        if not labels:
            return ""
        return '<span class="%s">%s</span>' % (cls, esc(" · ".join(labels)))

    # -- video and photo markup --------------------------------------------

    def video_block_html(self, v, root_prefix):
        if v.get("livid"):
            frame = ('<iframe src="%s" allow="autoplay; fullscreen; picture-in-picture" '
                     'allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin" title="%s"></iframe>'
                     % (esc(v["livid"]), esc(plain(v.get("title", "")))))
        elif v.get("mp4"):
            poster = (' poster="%s%s"' % (root_prefix, asset_url(v["poster"]))) if v.get("poster") else ""
            frame = ('<video controls controlslist="nodownload" draggable="false" preload="none"%s playsinline>'
                     '<source src="%s%s" type="video/mp4"></video>'
                     % (poster, root_prefix, asset_url(v["mp4"])))
        else:
            return ""
        ratio = v.get("ratio") or "16 / 9"
        badge = self.badge_html(v, "ai-badge vtitle-badge")
        return ('    <div class="video-block">\n      <div class="frame" style="aspect-ratio:%s">%s</div>\n'
                '      <p class="vtitle">%s%s</p>\n    </div>'
                % (esc(ratio), frame, esc(plain(v.get("title", ""))),
                   (" " + badge) if badge else ""))

    def photo_button_html(self, ph, root_prefix):
        """A masonry tile: the badge rides on the tile and travels to the
        lightbox caption through data-badge."""
        alt = ph.get("alt", "")
        badge = self.badge_html(ph, "ai-badge shot-badge")
        return ('      <button class="shot" data-full="%s%s" data-alt="%s" data-badge="%s">'
                '<img src="%s%s" alt="%s" loading="lazy" draggable="false">%s</button>'
                % (root_prefix, asset_url(ph["full"]), esc(alt), esc(self.badge_attr(ph)),
                   root_prefix, asset_url(ph["thumb"]), esc(alt), badge))

    def cover(self, p, use_full):
        """Card cover: manifest photo 01 (02 if 01 is the homepage hero); else
        the first video poster; else the first related reel poster; else
        (None, "", None) and the card shows a neutral tile with the client
        name. The item comes back so the cover can carry its AI badge."""
        data = self.manifest.get(p["slug"], {})
        photos = data.get("photos", [])
        if photos:
            ph = photos[1] if (len(photos) > 1 and photos[0]["full"] in self.hero_paths) else photos[0]
            return (ph["full"] if use_full else ph["thumb"]), ph.get("alt", ""), ph
        for v in data.get("videos", []):
            if v.get("poster"):
                return v["poster"], plain(v.get("title", "")), v
        for r in self.related_reels(p["slug"]):
            if r.get("poster"):
                return r["poster"], plain(r.get("title", "")), r
        return None, "", None

    def cover_badge_text(self, p, use_full):
        """Badge text for the card's cover media ("" when unflagged)."""
        item = self.cover(p, use_full)[2]
        return " · ".join(self.badge_labels(item)) if item is not None else ""

    def cover_html(self, p, rp, use_full):
        href = self.project_href(p, rp)
        src, alt, item = self.cover(p, use_full)
        if src:
            inner = ('<img src="%s%s" alt="%s" loading="lazy" draggable="false">'
                     % (rp, asset_url(src), esc(alt)))
            inner += self.badge_html(item, "ai-badge thumb-badge")
            cls = "thumb"
        else:
            inner = '<span class="tile-name">%s</span>' % esc(plain(p.get("client", "")))
            cls = "thumb tile"
        return ('<a class="%s" href="%s" tabindex="-1" aria-hidden="true">%s%s</a>'
                % (cls, href, inner, self.ribbon(p)))

    def card_html(self, p, rp, use_full=False, extra="", cls="card"):
        href = self.project_href(p, rp)
        lines = ['          <p class="kicker">%s</p>' % self.txt(p.get("client")),
                 '          <h3><a href="%s">%s</a></h3>' % (href, self.txt(p.get("title")))]
        if p.get("oneliner"):
            lines.append('          <p class="oneliner">%s</p>' % self.txt(p["oneliner"]))
        facts = self.facts(p)
        if facts:
            lines.append('          <p class="facts">%s</p>' % facts)
        cat = self.cat_label_html(p)
        if cat:
            lines.append('          <p class="kicker card-cat">%s</p>' % cat)
        sr = self.cover_badge_text(p, use_full)
        if sr:
            # The cover badge rides inside an aria-hidden link; say it in text.
            lines.append('          <span class="sr-only">%s</span>' % esc(sr))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            lines.append("          " + confirm)
        if extra:
            lines.append("          " + extra)
        lines.append('          <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        return ('      <article class="%s" data-tags="%s">\n        %s\n        <div class="meta">\n'
                '%s\n        </div>\n      </article>'
                % (cls, esc(self.cat_key(p)), self.cover_html(p, rp, use_full), "\n".join(lines)))

    # -- comparison sets (real shot → AI step → final) ----------------------

    def compare_list_html(self, slug, root_prefix):
        """A project's manifest `sets` as rows of labelled steps with arrows.
        Each step is a plain <img> (real shots included); a step that carries
        `ai`/`composite` gets the badge. A step without an image becomes a
        generated placeholder, so a half-built set blocks a release. Returns
        "" when the project has no sets (the normal case today)."""
        data = self.manifest.get(slug) or {}
        figures = []
        for si, s in enumerate(data.get("sets") or []):
            if not isinstance(s, dict):
                continue
            title = s.get("title")
            steps = []
            for sj, step in enumerate(s.get("steps") or []):
                if not isinstance(step, dict):
                    continue
                label = self.txt(step.get("label"))
                image = step.get("image")
                if image and not is_ph(image):
                    alt = plain(step.get("label") or title or "")
                    frame = ('<img src="%s%s" alt="%s" loading="lazy" draggable="false">'
                             % (root_prefix, asset_url(image), esc(alt)))
                else:
                    where = ("assets-manifest.json → projects.%s.sets[%d].steps[%d].image"
                             % (slug, si, sj))
                    frame = self.gen_box(where, "[Comparison step image: %s]"
                                         % (plain(step.get("label")) or "image"))
                badge = self.badge_html(step, "ai-badge step-badge")
                steps.append('        <li class="compare-step">\n'
                             '          <figure class="compare-frame">%s</figure>\n'
                             '          <p class="compare-label">%s%s</p>\n'
                             '        </li>'
                             % (frame, label, badge))
            if not steps:
                continue
            cap = ('\n        <figcaption class="compare-title">%s</figcaption>' % self.txt(title)) if title else ""
            figures.append('      <figure class="compare-set">%s\n'
                           '      <ol class="compare-steps">\n%s\n      </ol>\n'
                           '      </figure>' % (cap, "\n".join(steps)))
        if not figures:
            return ""
        return '    <div class="compare-list">\n%s\n    </div>' % "\n".join(figures)

    def sets_section_html(self, slug, root_prefix, heading=COMPARE_HEADING):
        inner = self.compare_list_html(slug, root_prefix)
        if not inner:
            return ""
        return ('<section class="compare-sets" id="compare">\n  <div class="wrap">\n'
                '    <p class="kicker">%s</p>\n%s\n  </div>\n</section>'
                % (self.txt(heading), inner))

    def feature_html(self, p, rp):
        href = self.project_href(p, rp)
        lines = ['        <p class="kicker">%s</p>' % self.txt(p.get("client")),
                 '        <h3><a href="%s">%s</a></h3>' % (href, self.txt(p.get("title")))]
        if p.get("oneliner"):
            lines.append("        " + self.para(p["oneliner"], "desc"))
        facts = self.facts(p)
        if facts:
            lines.append('        <p class="facts">%s</p>' % facts)
        cat = self.cat_label_html(p)
        if cat:
            lines.append('        <p class="kicker card-cat">%s</p>' % cat)
        sr = self.cover_badge_text(p, True)
        if sr:
            lines.append('        <span class="sr-only">%s</span>' % esc(sr))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            lines.append("        " + confirm)
        lines.append('        <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        return ('    <article class="feature" data-tags="%s">\n      %s\n      <div class="feature-body">\n'
                '%s\n      </div>\n    </article>'
                % (esc(self.cat_key(p)), self.cover_html(p, rp, True), "\n".join(lines)))

    def reel_card_html(self, r, rp, meta_bits=None, extra=""):
        """Ratio-true player card: a lazy Livid iframe, or the poster with a
        play button that main.js swaps for a <video>."""
        ratio = r.get("ratio") or "9 / 16"
        title = plain(r.get("title", ""))
        if r.get("livid"):
            frame = ('<iframe src="%s" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen '
                     'loading="lazy" referrerpolicy="strict-origin-when-cross-origin" title="%s"></iframe>'
                     % (esc(r["livid"]), esc(title)))
        elif r.get("mp4"):
            poster = (rp + asset_url(r["poster"])) if r.get("poster") else ""
            img = ('<img src="%s" alt="" loading="lazy" draggable="false">' % poster) if poster else ""
            frame = ('<button type="button" class="reel-play" data-src="%s%s"%s aria-label="Play: %s">'
                     '%s<span class="play-icon" aria-hidden="true"></span></button>'
                     % (rp, asset_url(r["mp4"]),
                        (' data-poster="%s"' % poster) if poster else "", esc(title), img))
        else:
            return ""
        if meta_bits is None:
            meta_bits = [r.get("brand"), r.get("format"), r.get("year")]
        meta = " · ".join(self.txt(b) for b in meta_bits if b)
        fmt = plain(r.get("format", ""))
        badge = self.badge_html(r, "ai-badge reel-badge")
        return ('      <figure class="reel-card"%s>\n'
                '        <div class="frame" style="aspect-ratio:%s">%s</div>\n'
                '        <figcaption><span class="rtitle">%s</span>%s%s%s%s</figcaption>\n'
                '      </figure>'
                % ((' data-format="%s"' % esc(fmt)) if fmt else "", esc(ratio), frame, self.txt(r.get("title")),
                   badge, ('<span class="rmeta">%s</span>' % meta) if meta else "", extra,
                   self.confirm_pill(r.get("confirm"))))

    # -- shared chrome ------------------------------------------------------

    def nav_html(self, rp, page_key):
        links = []
        for key, label, href in NAV_ITEMS:
            if self.page_hidden(href):
                continue
            active = (key == page_key) or (key == "work" and page_key == "project")
            attrs = ' class="active" aria-current="page"' if active else ""
            links.append('      <a href="%s%s"%s>%s</a>' % (rp, href, attrs, label))
        resume = self.config.get("resume_pdf") or ""
        if resume:
            href = resume if is_remote(resume) else rp + resume
            links.append('      <a class="btn" href="%s">Résumé</a>' % esc(href))
        else:
            links.append('      <a class="btn" href="%sabout.html#resume">Résumé'
                         '<span class="ph-chip ph-chip-btn">To add</span></a>' % rp)
        return tmpl("nav.html.tmpl").safe_substitute(
            ROOT_PREFIX=rp, NAME=esc(self.config.get("name", "")),
            NAV_LINKS="\n".join(links))

    def footer_html(self, rp):
        c = self.config
        social = ""
        for key, label in (("instagram", "Instagram"), ("linkedin", "LinkedIn")):
            if c.get(key):
                social += ' · <a href="%s" rel="noopener">%s</a>' % (esc(c[key]), label)
        return tmpl("footer.html.tmpl").safe_substitute(
            ROOT_PREFIX=rp, NAME=esc(c.get("name", "")), BASED=esc(plain(c.get("based", ""))),
            EMAIL=esc(c.get("email", "")), SOCIAL_LINKS=social)

    def head_html(self, rp, title, description, og_path, og_type):
        domain = (self.config.get("domain") or "").strip().rstrip("/")
        if domain:
            site_url = "https://" + domain
            og_image = site_url + "/assets/img/og-cover.jpg"
            og_url = '<meta property="og:url" content="%s">\n' % esc(site_url + "/" + og_path)
        else:
            # No domain yet: omit og:url and use a relative og:image.
            og_image = rp + "assets/img/og-cover.jpg"
            og_url = ""
        return tmpl("head.html.tmpl").safe_substitute(
            ROOT_PREFIX=rp, PAGE_TITLE=esc(title), META_DESCRIPTION=esc(description),
            OG_TYPE=og_type, OG_URL_META=og_url, OG_IMAGE=esc(og_image))

    def page(self, out_path, main, title, description, page_key, body_class="",
             og_type="website", after_main=""):
        rp = "../" * out_path.count("/")
        classes = [c for c in (body_class, "" if self.release else "is-draft") if c]
        banner = ("" if self.release else
                  '<div class="draft-banner" role="note">Draft preview — not for publishing</div>\n')
        og_path = "" if out_path == "index.html" else out_path
        self.pages[out_path] = tmpl("page.html.tmpl").safe_substitute(
            HEAD=self.head_html(rp, title, description, og_path, og_type),
            BODY_CLASS=(' class="%s"' % " ".join(classes)) if classes else "",
            DRAFT_BANNER=banner,
            NAV=self.nav_html(rp, page_key),
            MAIN=main.replace("${ROOT_PREFIX}", rp),
            AFTER_MAIN=after_main,
            FOOTER=self.footer_html(rp))

    def contact_form(self, rp):
        return tmpl("contact_form.html.tmpl").safe_substitute(
            ROOT_PREFIX=rp,
            FORMSPREE_ENDPOINT=esc(self.config.get("formspree_endpoint", "")),
            EMAIL=esc(self.config.get("email", "")),
            SUBJECT=esc("Portfolio inquiry — %s" % self.config.get("name", "")))

    def page_intro(self, key):
        v = (self.config.get("page_intros") or {}).get(key, "")
        return self.para(v, "lede") if v else ""

    # -- pages --------------------------------------------------------------

    def masthead_name(self):
        """Two-line lockup: first word, then the rest ('Adrián' / 'Rivera
        Ferrán'). Every word is its own span so phones stack one per line."""
        words = self.config.get("name", "").split()
        if not words:
            return ""
        first = '<span class="w">%s</span>' % esc(words[0])
        if len(words) == 1:
            return first
        rest = " ".join('<span class="w">%s</span>' % esc(w) for w in words[1:])
        return first + '<span class="last"> %s</span>' % rest

    def reel_hidden(self):
        return bool((self.config.get("showreel") or {}).get("hidden"))

    def showreel_html(self, rp):
        sr = self.config.get("showreel") or {}
        if sr.get("hidden"):
            return ""
        blocks = []
        missing = False
        for slot in ("vertical", "horizontal"):
            s = sr.get(slot) or {}
            b = self.video_block_html(s, rp) if (s.get("livid") or s.get("mp4")) else ""
            if b:
                blocks.append('      <div class="reel-slot reel-%s">\n%s\n      </div>' % (slot, b))
            else:
                missing = True
        out = ""
        if blocks:
            out += '    <div class="showreel">\n%s\n    </div>\n' % "\n".join(blocks)
        if missing:
            text = sr.get("placeholder") or "[Showreel]"
            out += "    " + (self.box(text) if is_ph(text)
                             else self.gen_box("index.html#reel", "[%s]" % text))
        return out

    def cap_html(self, cap):
        """One capability-strip item: linked to its skill page via CAP_LINKS,
        plain text when the map has no target for the tag."""
        inner = ('<span class="cap-label">%s</span><span class="cap-line">%s</span>'
                 % (self.txt(cap.get("label")), self.txt(cap.get("line"))))
        target = CAP_LINKS.get(cap.get("tag", "")) or ""
        if self.page_hidden(target):
            target = ""
        if target:
            return '      <a class="cap" href="%s">%s</a>' % (esc(target), inner)
        return '      <div class="cap cap-plain">%s</div>' % inner

    def hero_actions(self):
        see = 'href="${ROOT_PREFIX}work.html"'
        if self.reel_hidden():
            return '<a class="btn" %s>See work</a>' % see
        return '<a class="btn" href="#reel">Watch reel</a><a class="btn btn-ghost" %s>See work</a>' % see

    def reel_section(self):
        if self.reel_hidden():
            return ""
        return tmpl("home.reel.tmpl").safe_substitute(SHOWREEL=self.showreel_html("")) + "\n"

    def build_home(self):
        c = self.config
        featured = sorted([p for p in self.visible if p.get("featured")],
                          key=lambda p: p["featured"])
        lead = self.feature_html(featured[0], "") if featured else ""
        cards = "\n".join(self.card_html(p, "", use_full=True) for p in featured[1:4])
        caps = "\n".join(self.cap_html(cap) for cap in c.get("capabilities", []))
        collab = c.get("collaborations") or {}
        if collab.get("names"):
            names = '<span class="sep" aria-hidden="true"> · </span>'.join(
                "<span>%s</span>" % self.txt(n) for n in collab["names"])
            collabs = ('<section class="collabs">\n  <div class="wrap">\n'
                       '    <p class="kicker">%s</p>\n    <p class="collab-names">%s</p>\n'
                       '  </div>\n</section>' % (self.txt(collab.get("label", "")), names))
        else:
            collabs = ""
        # Bottom masthead labels render only when at least one has text.
        bottom = [l for l in c.get("masthead_labels", []) if l and l.strip()]
        labels_bottom = ('\n    <div class="labels bottom">\n%s\n    </div>'
                         % "\n".join("      <span>%s</span>" % esc(l) for l in bottom)) if bottom else ""
        main = tmpl("home.main.tmpl").safe_substitute(
            HERO_IMAGE=esc(c.get("hero_image", "")),
            HERO_IMAGE_PHONE=esc(c.get("hero_image_phone") or c.get("hero_image", "")),
            HERO_CAPTION=esc(plain(c.get("hero_caption", ""))),
            HERO_CUTOUT=esc(c.get("hero_cutout", "")),
            LABEL_TL=self.txt(c.get("title")),
            LABEL_TR=self.txt(c.get("based")),
            LABELS_BOTTOM=labels_bottom,
            NAME_MASTHEAD=self.masthead_name(),
            HEADLINE=self.txt(c.get("headline")),
            SUBLINE=self.txt(c.get("subline")),
            ACTIONS=self.hero_actions(),
            REEL_SECTION=self.reel_section(),
            FEATURE=lead,
            FEATURED_CARDS=cards,
            CAPABILITIES=caps,
            COLLABS=collabs,
            NAME=esc(c.get("name", "")),
            BIO_PARAGRAPHS="\n".join("      " + self.para(b) for b in c.get("bio", [])),
            CTA=self.txt(c.get("cta")))
        self.page("index.html", main,
                  "%s — %s" % (c.get("name", ""), plain(c.get("title", ""))),
                  plain(c.get("headline") or c.get("title", "")),
                  "home", body_class="has-masthead")

    def build_work(self):
        """Filter pills = All + each category with a visible project. The All
        view lists the groups in config order, each under its heading; the
        headings carry data-tags too, so the shared filter JS shows only the
        active group's heading. Projects with no group come last, All only."""
        groups = self.work_groups()
        filters = ['      <a class="filter active" href="#all" data-filter="all" aria-pressed="true">All</a>']
        blocks = []
        for c, members in groups:
            if c is not None:
                key = esc(c["key"])
                filters.append('      <a class="filter" href="#%s" data-filter="%s" aria-pressed="false">%s</a>'
                               % (key, key, self.txt(c.get("label") or c["key"])))
                note = (" " + self.personal_note()) if c.get("personal") else ""
                heading = ('      <h2 class="work-group" data-tags="%s" data-filter-heading>%s%s</h2>'
                           % (key, self.txt(c.get("label") or c["key"]), note))
            else:
                heading = ('      <h2 class="work-group work-group-other" data-tags="" data-filter-heading>%s</h2>'
                           % esc(OTHER_WORK_LABEL))
            blocks.append(heading)
            blocks.extend(self.card_html(p, "") for p in members)
        main = tmpl("work.main.tmpl").safe_substitute(
            INTRO=self.page_intro("work"),
            FILTERS="\n".join(filters),
            CARDS="\n".join(blocks))
        intro = (self.config.get("page_intros") or {}).get("work", "")
        self.page("work.html", main, "Work — %s" % self.config.get("name", ""),
                  plain(intro) or "Work by %s" % self.config.get("name", ""), "work")

    def build_case(self, idx, p):
        rp = "../"
        slug = p["slug"]
        data = self.manifest.get(slug, {})
        sections = []
        # Lead media: manifest videos first, then demo / ai_steps placeholders.
        lead = [b for b in (self.video_block_html(v, rp) for v in data.get("videos", [])) if b]
        lead_html = ('    <div class="videos">\n%s\n    </div>' % "\n".join(lead)) if lead else ""
        for key in ("demo", "ai_steps"):
            if p.get(key):
                lead_html += "\n    " + (self.box(p[key]) if is_ph(p[key])
                                         else '<p class="desc">%s</p>' % esc(p[key]))
        if lead_html.strip():
            sections.append('<section class="case-media">\n  <div class="wrap">\n%s\n  </div>\n</section>'
                            % lead_html)
        # Body: brief / did / scope / result, else description.
        if p.get("brief"):
            blocks = [("The brief", self.para(p["brief"]))]
            if p.get("did"):
                blocks.append(("What I did", "<ul>%s</ul>" % "".join(
                    "<li>%s</li>" % self.txt(d) for d in p["did"])))
            if p.get("scope"):
                blocks.append(("Scope", "<ul>%s</ul>" % "".join(
                    "<li>%s</li>" % self.txt(s) for s in p["scope"])))
            if p.get("result"):
                blocks.append(("Result", self.para(p["result"])))
            body = "\n".join(
                '    <div class="case-block">\n      <h2 class="kicker">%s</h2>\n'
                '      <div class="case-copy">%s</div>\n    </div>' % (esc(h), b) for h, b in blocks)
        elif p.get("description"):
            body = '    <div class="case-desc">%s</div>' % self.para(p["description"], "desc")
        else:
            body = ""
        if body:
            sections.append('<section class="case-body">\n  <div class="wrap">\n%s\n  </div>\n</section>' % body)
        # Comparison sets (real shot → AI step → final), straight after the copy.
        sets_html = self.sets_section_html(slug, rp)
        if sets_html:
            sections.append(sets_html)
        # Related reels, minus any already shown as a lead video.
        shown = set()
        for v in data.get("videos", []):
            shown.update(x for x in (v.get("livid"), v.get("mp4")) if x)
        reels = [r for r in self.related_reels(slug)
                 if not (set(x for x in (r.get("livid"), r.get("mp4")) if x) & shown)]
        reel_cards = [c for c in (self.reel_card_html(r, rp) for r in reels) if c]
        if reel_cards:
            sections.append('<section class="case-reels">\n  <div class="wrap">\n'
                            '    <p class="kicker">Reels</p>\n    <div class="reel-row">\n%s\n    </div>\n'
                            '  </div>\n</section>' % "\n".join(reel_cards))
        # Photos (masonry + lightbox)
        has_photos = bool(data.get("photos"))
        if has_photos:
            shots = "\n".join(self.photo_button_html(ph, rp) for ph in data["photos"])
            sections.append('<section class="case-photos">\n  <div class="wrap">\n'
                            '    <p class="kicker">Photos</p>\n    <div class="photos">\n%s\n    </div>\n'
                            '  </div>\n</section>' % shots)
        # Behind the scenes
        if p.get("bts"):
            bts = self.box(p["bts"]) if is_ph(p["bts"]) else '<p class="desc">%s</p>' % esc(p["bts"])
            sections.append('<section class="case-bts">\n  <div class="wrap">\n'
                            '    <p class="kicker">Behind the scenes</p>\n    %s\n  </div>\n</section>' % bts)
        # Previous / next, in the Work page's grouped order, wrapping around.
        order = [q for _, members in self.work_groups() for q in members]
        idx = next((i for i, q in enumerate(order) if q.get("slug") == p.get("slug")), idx)
        n = len(order)
        prev_p, next_p = order[(idx - 1) % n], order[(idx + 1) % n]
        pager = ('<nav class="case-nav" aria-label="More projects">\n'
                 '      <a class="prev" href="%s.html"><span class="kicker">← Previous</span>'
                 '<span class="cn-title">%s</span></a>\n'
                 '      <a class="next" href="%s.html"><span class="kicker">Next →</span>'
                 '<span class="cn-title">%s</span></a>\n'
                 '    </nav>' % (esc(prev_p["slug"]), self.txt(prev_p.get("client")),
                                  esc(next_p["slug"]), self.txt(next_p.get("client"))))
        cat = self.category(p)
        cat_html = ('<p class="kicker case-cat">%s</p>'
                    % self.cat_label_html(p, href="%swork.html#%s" % (rp, cat["key"]))) if cat else ""
        main = tmpl("case.main.tmpl").safe_substitute(
            RIBBON=self.ribbon(p),
            CATEGORY=cat_html,
            CLIENT=self.txt(p.get("client")),
            PTITLE=self.txt(p.get("title")),
            ONELINER=self.para(p.get("oneliner"), "desc") if p.get("oneliner") else "",
            FACTS=self.facts(p),
            PILLS=self.pills(p.get("tags"), rp),
            CONFIRM=self.confirm_pill(p.get("confirm")),
            SECTIONS="\n".join(sections),
            PAGER=pager)
        desc = plain(p.get("oneliner") or p.get("description") or p.get("brief") or p.get("title", ""))
        after = tmpl("lightbox.html.tmpl").template if has_photos else ""
        self.page("work/%s.html" % slug, main,
                  "%s — %s" % (plain(p.get("client", "")), self.config.get("name", "")),
                  desc, "project", og_type="article", after_main=after)

    def intro_desc(self, key, fallback):
        intro = (self.config.get("page_intros") or {}).get(key, "")
        return plain(intro) or fallback

    def project(self, slug):
        """A visible project by slug (None when hidden, missing or draft-only
        in a release)."""
        for p in self.visible:
            if p.get("slug") == slug:
                return p
        return None

    def build_reels(self):
        reels = self.media.get("reels", [])
        order, groups = [], {}
        for r in reels:
            key = slugify(r.get("format", ""))
            if key not in groups:
                order.append(key)
                groups[key] = (r.get("format", ""), [])
            groups[key][1].append(r)
        filters = ['      <a class="filter active" href="#all" data-filter="all" aria-pressed="true">All</a>']
        for key in order:
            filters.append('      <a class="filter" href="#%s" data-filter="%s" aria-pressed="false">%s</a>'
                           % (esc(key), esc(key), self.txt(groups[key][0])))
        # One grid, filtered by format. Separate per-format rows left long empty
        # stretches beside formats that hold a single reel.
        items = []
        for r in reels:
            card = self.reel_card_html(r, "")
            if card:
                items.append('      <div class="reel-item" data-tags="%s">\n%s\n      </div>'
                             % (esc(slugify(r.get("format", ""))), card))
        blocks = ['      <div class="reel-grid">\n%s\n      </div>' % "\n".join(items)] if items else []
        note = self.media.get("reel_formats_note")
        if note:
            anatomy = ('    <div class="reel-anatomy">\n      <p class="kicker">Anatomy of a reel</p>\n'
                       '      %s\n    </div>' % (self.box(note) if is_ph(note)
                                                  else '<p class="desc">%s</p>' % esc(note)))
        else:
            anatomy = ""
        motion = []
        for m in self.media.get("motion", []):
            role = ('<span class="role-chip">%s</span>' % self.txt(m["role"])) if m.get("role") else ""
            card = self.reel_card_html(m, "", meta_bits=[m.get("client"), m.get("year")], extra=role)
            if card:
                motion.append(card)
        motion_html = ""
        if motion:
            motion_html = ('<section class="motion" id="motion">\n  <div class="wrap">\n'
                           '    <div class="sec-head">\n      <div>\n        <h2>Motion</h2>\n'
                           '      </div>\n    </div>\n    <div class="motion-grid">\n%s\n    </div>\n'
                           '  </div>\n</section>' % "\n".join(motion))
        main = tmpl("reels.main.tmpl").safe_substitute(
            INTRO=self.page_intro("reels"), FILTERS="\n".join(filters),
            GROUPS="\n".join(blocks), ANATOMY=anatomy, MOTION=motion_html)
        self.page("reels.html", main, "Reels — %s" % self.config.get("name", ""),
                  self.intro_desc("reels", "Reels"), "reels")

    def ai_feature_html(self):
        """The AI page feature. A standalone feature project has no case
        study, so the block carries that content itself (brief, did, scope,
        result, demo), takes its section id from the standalone anchor and
        drops the "View project" link (it would point at itself)."""
        p = self.project(AI_FEATURE)
        if not p:
            return ""
        standalone = bool(standalone_target(p))
        href = "" if standalone else self.project_href(p, "")
        title = self.txt(p.get("title"))
        if href:
            title = '<a href="%s">%s</a>' % (href, title)
        copy = ['        <p class="kicker">%s</p>' % self.txt(p.get("client")),
                '        <h2>%s</h2>%s' % (title, self.ribbon(p))]
        if p.get("oneliner"):
            copy.append("        " + self.para(p["oneliner"], "desc"))
        if standalone and p.get("brief"):
            copy.append("        " + self.para(p["brief"], "desc brief"))
        if p.get("did"):
            copy.append('        <ul class="did">%s</ul>' % "".join(
                "<li>%s</li>" % self.txt(d) for d in p["did"]))
        if standalone and p.get("scope"):
            copy.append('        <div class="ai-scope"><p class="kicker">Scope</p><ul class="did">%s</ul></div>'
                        % "".join("<li>%s</li>" % self.txt(x) for x in p["scope"]))
        if standalone and p.get("result"):
            copy.append('        <div class="ai-result"><p class="kicker">Result</p>%s</div>'
                        % self.para(p["result"], "desc"))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            copy.append("        " + confirm)
        if href:
            copy.append('        <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        media = [b for b in (self.video_block_html(v, "") for v in
                             self.manifest.get(p["slug"], {}).get("videos", [])) if b]
        if p.get("demo"):
            media.append("      " + (self.box(p["demo"]) if is_ph(p["demo"])
                                     else '<p class="desc">%s</p>' % esc(p["demo"])))
        anchor = standalone_anchor(p)
        sid = (' id="%s"' % esc(anchor)) if anchor else ""
        return ('<section class="ai-feature"%s>\n  <div class="wrap">\n    <article class="ai-feature-card">\n'
                '      <div class="ai-feature-copy">\n%s\n      </div>\n'
                '      <div class="ai-feature-media">\n%s\n      </div>\n'
                '    </article>\n  </div>\n</section>' % (sid, "\n".join(copy), "\n".join(media)))

    def build_ai(self):
        c = self.config
        label = c.get("ai_label") or ""
        badge = ('<span class="ai-badge">%s</span>' % self.txt(label)) if label else ""
        steps = "\n".join(
            '      <li><span class="step-num">%02d</span><span class="step-label">%s</span></li>'
            % (i + 1, self.txt(step)) for i, step in enumerate(self.media.get("ai_process", [])))
        process = ""
        if steps:
            process = ('<section class="ai-process" id="process">\n  <div class="wrap">\n'
                       '    <div class="sec-head">\n      <div>\n        <h2>Process</h2>\n      </div>\n    </div>\n'
                       '    <ol class="process">\n%s\n    </ol>\n  </div>\n</section>' % steps)
        live = []
        for slug in self.live_slugs:
            p = self.project(slug)
            if not p:
                continue
            v = p.get("ai_steps")
            has_sets = bool((self.manifest.get(slug) or {}).get("sets"))
            if v:
                steps_html = self.box(v) if is_ph(v) else '<p class="ai-steps">%s</p>' % esc(v)
            elif has_sets:
                steps_html = ""   # the comparison sets below the cards carry this
            else:
                steps_html = self.gen_box(
                    "ai.html#ran-live → %s.ai_steps" % slug,
                    "[%s AI-assisted pieces: real shot → AI step → final]" % plain(p.get("client", slug)))
            live.append(self.card_html(p, "", use_full=True, extra=badge + steps_html,
                                       cls="card live-card"))
        live_html = ""
        if live:
            # Comparison sets for the live projects sit under the cards (inside
            # this section, as divs, so the section stays one block).
            live_sets = []
            for slug in self.live_slugs:
                p = self.project(slug)
                if not p:
                    continue
                inner = self.compare_list_html(slug, "")
                if not inner:
                    continue
                live_sets.append((p, inner))
            live_sets_html = ""
            if live_sets:
                # With one project the set titles are enough; a client kicker
                # only earns its line when two projects share the block.
                named = len(live_sets) > 1
                parts = []
                for p, inner in live_sets:
                    head = ('      <p class="kicker">%s</p>\n' % self.txt(p.get("client")))
                    parts.append('    <div class="live-set">\n%s%s\n    </div>'
                                 % (head if named else "", inner))
                live_sets_html = ('    <div class="live-sets">\n      <p class="kicker">%s</p>\n%s\n    </div>\n'
                                  % (self.txt(COMPARE_HEADING), "\n".join(parts)))
            live_html = ('<section class="ai-live" id="ran-live">\n  <div class="wrap">\n'
                         '    <div class="sec-head">\n      <div>\n        <h2>Ran live</h2>\n      </div>\n    </div>\n'
                         '    <div class="live-grid">\n%s\n    </div>\n%s  </div>\n</section>'
                         % ("\n".join(live), live_sets_html))
        tools = []
        if self.media.get("ai_tools_line"):
            tools.append("    " + self.para(self.media["ai_tools_line"], "lede"))
        if label:
            tools.append('    <p class="ai-mark">Every AI-assisted piece on this site is marked %s.</p>' % badge)
        tools_html = ('<section class="ai-tools">\n  <div class="wrap">\n%s\n  </div>\n</section>'
                      % "\n".join(tools)) if tools else ""
        main = tmpl("ai.main.tmpl").safe_substitute(
            INTRO=self.page_intro("ai"), FEATURE=self.ai_feature_html(), PROCESS=process,
            LIVE=live_html, TOOLS=tools_html)
        self.page("ai.html", main, "AI & Workflow — %s" % c.get("name", ""),
                  self.intro_desc("ai", "AI & Workflow"), "ai")

    def ad_card_html(self, ad):
        """A paid-social item in its placement ratio: video (Livid or local
        mp4, via the reel card) or a static image shown whole (contain)."""
        meta = [ad.get("campaign")]
        if ad.get("livid") or ad.get("mp4"):
            return self.reel_card_html(ad, "", meta_bits=meta)
        if not ad.get("image"):
            return ""
        ratio = ad.get("ratio") or "1 / 1"
        title = plain(ad.get("title", ""))
        img = ('<img src="%s" alt="%s" loading="lazy" draggable="false">'
               % (esc(asset_url(ad["image"])), esc(title)))
        m = " · ".join(self.txt(b) for b in meta if b)
        return ('      <figure class="reel-card ad-static">\n'
                '        <div class="frame" style="aspect-ratio:%s">%s</div>\n'
                '        <figcaption><span class="rtitle">%s</span>%s%s</figcaption>\n      </figure>'
                % (esc(ratio), img, self.txt(ad.get("title")),
                   ('<span class="rmeta">%s</span>' % m) if m else "",
                   self.confirm_pill(ad.get("confirm"))))

    def build_paid(self):
        ps = self.media.get("paid_social") or {}
        sections = []
        for key, heading, anchor in (("video_ads", "Video ads", "video-ads"),
                                     ("static_ads", "Static ads", "static-ads")):
            cards = [c for c in (self.ad_card_html(a) for a in ps.get(key) or []) if c]
            if cards:
                body = '    <div class="ad-grid">\n%s\n    </div>' % "\n".join(cards)
            else:
                body = "    " + self.gen_box("paid-social.html#%s" % anchor,
                                             "[%s to add, in the sizes they ran: %s]" % (heading, AD_SIZES))
            sections.append('<section class="paid-section" id="%s">\n  <div class="wrap">\n'
                            '    <div class="sec-head">\n      <div>\n        <h2>%s</h2>\n      </div>\n    </div>\n'
                            '%s\n  </div>\n</section>' % (anchor, esc(heading), body))
        intro = (self.config.get("page_intros") or {}).get("paid") or ps.get("intro") or ""
        main = tmpl("paid.main.tmpl").safe_substitute(
            INTRO=self.para(intro, "lede") if intro else "", SECTIONS="\n".join(sections))
        self.page("paid-social.html", main, "Paid Social — %s" % self.config.get("name", ""),
                  plain(intro) or "Paid Social", "paid")

    def build_about(self):
        c = self.config
        blocks = []

        def block(heading, inner, anchor=""):
            blocks.append('    <div class="case-block about-block"%s>\n      <h2 class="kicker">%s</h2>\n'
                          '      <div class="case-copy">%s</div>\n    </div>'
                          % ((' id="%s"' % anchor) if anchor else "", heading, inner))

        exp = []
        for e in c.get("experience", []):
            org = (' <span class="x-org">— %s</span>' % self.txt(e["org"])) if e.get("org") else ""
            when = " · ".join(self.txt(e.get(k)) for k in ("years", "place") if e.get(k))
            exp.append('<li><span class="x-role">%s</span>%s%s</li>'
                       % (self.txt(e.get("role", "")), org,
                          ('<span class="x-meta">%s</span>' % when) if when else ""))
        if exp:
            block("Experience", '<ul class="xp">%s</ul>' % "".join(exp))
        credits = self.media.get("credits", [])
        if credits:
            rows = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % self.txt(cr.get(k, ""))
                                                    for k in ("artist", "work", "role", "year"))
                           for cr in credits)
            pill = self.confirm_pill(["credits"]) if self.media.get("credits_confirm") else ""
            table = ('<div class="credits-wrap"><table class="credits"><thead><tr>'
                     '<th scope="col">Artist</th><th scope="col">Work</th><th scope="col">Role</th>'
                     '<th scope="col">Year</th></tr></thead><tbody>%s</tbody></table></div>%s' % (rows, pill))
            block(self.txt(self.media.get("credits_note") or "Production credits"), table)
        if c.get("tools"):
            block("Tools", '<ul class="inline-list">%s</ul>' % "".join(
                "<li>%s</li>" % self.txt(t) for t in c["tools"]))
        if c.get("education"):
            block("Education", self.para(c["education"]))
        if c.get("certifications"):
            block("Certifications", "<ul>%s</ul>" % "".join(
                "<li>%s</li>" % self.txt(x) for x in c["certifications"]))
        if c.get("languages"):
            block("Languages", self.para(c["languages"]))
        sides = []
        for sp in c.get("side_projects", []):
            label = self.txt(sp.get("label", ""))
            href = sp.get("href") or ""
            if href and not is_ph(href):
                label = '<a href="%s" rel="noopener">%s</a>' % (esc(href), label)
            line = (' <span class="sp-line">%s</span>' % self.txt(sp["line"])) if sp.get("line") else ""
            sides.append('<li><span class="sp-label">%s</span>%s</li>' % (label, line))
        if sides:
            block("Side projects", '<ul class="side-projects">%s</ul>' % "".join(sides))
        resume = c.get("resume_pdf") or ""
        if resume:
            resume_html = ('<p><a class="btn" href="%s" download>Download résumé (PDF)</a></p>' % esc(resume))
        else:
            resume_html = self.gen_box("about.html#resume", "[Résumé PDF: brand version pending]")
        block("Résumé", resume_html, anchor="resume")
        bio = "\n".join("      " + self.para(b) for b in c.get("bio", []))
        main = tmpl("about.main.tmpl").safe_substitute(
            NAME=esc(c.get("name", "")), INTRO=self.page_intro("about"), BIO=bio,
            BLOCKS="\n".join(blocks))
        self.page("about.html", main, "About — %s" % c.get("name", ""),
                  self.intro_desc("about", "About %s" % c.get("name", "")), "about")

    def build_contact(self):
        c = self.config
        main = tmpl("contact.main.tmpl").safe_substitute(
            EMAIL=esc(c.get("email", "")), CONTACT_FORM=self.contact_form(""))
        self.page("contact.html", main, "Contact — %s" % c.get("name", ""),
                  "Contact %s, %s in %s." % (c.get("name", ""), plain(c.get("title", "")),
                                             plain(c.get("based", ""))), "contact")

    def build_legal(self):
        legal = (
            ("privacy", "Privacy Notice", "How this site handles your information."),
            ("terms", "Terms of Use", "Copyright, permitted use, and the no-AI-training terms for this site."),
        )
        for slug, title, desc in legal:
            body = Template((ROOT / "templates" / "legal" / (slug + ".body.html"))
                            .read_text(encoding="utf-8")).safe_substitute(
                NAME=esc(self.config.get("name", "")), EMAIL=esc(self.config.get("email", "")),
                BASED=esc(plain(self.config.get("based", ""))))
            main = tmpl("legal.main.tmpl").safe_substitute(LEGAL_TITLE=esc(title), LEGAL_BODY=body)
            self.page(slug + ".html", main, "%s — %s" % (title, self.config.get("name", "")),
                      desc, "legal")

    def build_404(self):
        self.pages["404.html"] = tmpl("404.html.tmpl").safe_substitute(
            NAME=esc(self.config.get("name", "")))

    def build_all(self):
        self.build_home()
        self.build_work()
        for i, p in enumerate(self.work):
            self.build_case(i, p)
        self.build_reels()
        self.build_ai()
        if "paid-social" not in self.hidden_pages:
            self.build_paid()
        self.build_about()
        self.build_contact()
        self.build_legal()
        self.build_404()
        return self.pages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def write_pages(pages, config):
    for rel, text in pages.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    # Stale case-study pages (hidden, draft-only in release, standalone,
    # deleted projects) must not linger pointing at nothing.
    work_dir = ROOT / "work"
    if work_dir.is_dir():
        for f in work_dir.glob("*.html"):
            if "work/" + f.name not in pages:
                f.unlink()
    # A hidden page must not linger from an earlier build.
    for stem in config.get("hidden_pages") or []:
        if stem in HIDEABLE_PAGES:
            (ROOT / (stem + ".html")).unlink(missing_ok=True)
    # CNAME only when a domain is set; never an empty or guessed one.
    domain = (config.get("domain") or "").strip()
    if domain:
        (ROOT / "CNAME").write_text(domain + "\n", encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv            # the release gate, writes nothing either way
    release = "--release" in argv or check
    data = load_data()
    site = Site(data, release=release)
    pages = site.build_all()          # rendered in memory; nothing written yet

    if release:
        offenders = scan_release(data)
        offenders += [("(generated)", where, text) for where, text in site.generated]
        if offenders:
            print("RELEASE BLOCKED: %d item(s) must be resolved first. No pages were written."
                  % len(offenders))
            for fname, path, value in offenders:
                print("  %s → %s → %s" % (fname, path, fmt_value(value)))
            return 1
        if check:
            print("RELEASE READY: nothing left to resolve.")
            return 0
        write_pages(pages, site.config)
        print("Built %d pages (%d case studies). BUILD_OK (release)" % (len(pages), len(site.work)))
        return 0

    write_pages(pages, site.config)
    blocking = len(scan_release(data)) + len(site.generated)
    print("Draft build: %d item(s) still block a release (python3 build.py --release lists them)."
          % blocking)
    print("Built %d pages (%d case studies). BUILD_OK" % (len(pages), len(site.work)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
