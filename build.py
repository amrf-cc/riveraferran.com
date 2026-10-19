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

# Ad placement sizes the Paid Social page asks for while a list is empty.
AD_SIZES = "9:16, 4:5, 1:1"


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
    for slot in ("vertical", "horizontal"):
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


def is_remote(url):
    return url.startswith(("http://", "https://", "//"))


def video_block_html(v, root_prefix):
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
    return ('    <div class="video-block">\n      <div class="frame" style="aspect-ratio:%s">%s</div>\n'
            '      <p class="vtitle">%s</p>\n    </div>' % (esc(ratio), frame, esc(plain(v.get("title", "")))))


def photo_button_html(ph, root_prefix):
    return ('      <button class="shot" data-full="%s%s" data-alt="%s">'
            '<img src="%s%s" alt="%s" loading="lazy" draggable="false"></button>'
            % (root_prefix, asset_url(ph["full"]), esc(ph["alt"]),
               root_prefix, asset_url(ph["thumb"]), esc(ph["alt"])))


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
        self.cap_labels = {c.get("tag"): c.get("label", c.get("tag"))
                           for c in self.config.get("capabilities", [])}
        self.hero_paths = {self.config.get("hero_image"),
                           self.config.get("hero_image_phone")} - {None, ""}
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

    def facts(self, p):
        return " · ".join(self.txt(p.get(k)) for k in ("role", "years", "location") if p.get(k))

    def pills(self, tags, root_prefix=None):
        """Tag pills. With root_prefix set they link to the Work filter."""
        if not tags:
            return ""
        items = []
        for t in tags:
            label = esc(self.cap_labels.get(t, t))
            if root_prefix is None:
                items.append('<li class="pill">%s</li>' % label)
            else:
                items.append('<li><a class="pill" href="%swork.html#%s">%s</a></li>'
                             % (root_prefix, esc(t), label))
        return '<ul class="pills">%s</ul>' % "".join(items)

    # -- covers and cards ---------------------------------------------------

    def related_reels(self, slug):
        return [r for r in self.media.get("reels", []) if r.get("project") == slug]

    def cover(self, p, use_full):
        """Card cover: manifest photo 01 (02 if 01 is the homepage hero); else
        the first video poster; else the first related reel poster; else
        (None, "") and the card shows a neutral tile with the client name."""
        data = self.manifest.get(p["slug"], {})
        photos = data.get("photos", [])
        if photos:
            ph = photos[1] if (len(photos) > 1 and photos[0]["full"] in self.hero_paths) else photos[0]
            return (ph["full"] if use_full else ph["thumb"]), ph.get("alt", "")
        for v in data.get("videos", []):
            if v.get("poster"):
                return v["poster"], plain(v.get("title", ""))
        for r in self.related_reels(p["slug"]):
            if r.get("poster"):
                return r["poster"], plain(r.get("title", ""))
        return None, ""

    def cover_html(self, p, rp, use_full):
        href = "%swork/%s.html" % (rp, p["slug"])
        src, alt = self.cover(p, use_full)
        if src:
            inner = ('<img src="%s%s" alt="%s" loading="lazy" draggable="false">'
                     % (rp, asset_url(src), esc(alt)))
            cls = "thumb"
        else:
            inner = '<span class="tile-name">%s</span>' % esc(plain(p.get("client", "")))
            cls = "thumb tile"
        return ('<a class="%s" href="%s" tabindex="-1" aria-hidden="true">%s%s</a>'
                % (cls, href, inner, self.ribbon(p)))

    def card_html(self, p, rp, use_full=False, extra="", cls="card"):
        href = "%swork/%s.html" % (rp, p["slug"])
        lines = ['          <p class="kicker">%s</p>' % self.txt(p.get("client")),
                 '          <h3><a href="%s">%s</a></h3>' % (href, self.txt(p.get("title")))]
        if p.get("oneliner"):
            lines.append('          <p class="oneliner">%s</p>' % self.txt(p["oneliner"]))
        facts = self.facts(p)
        if facts:
            lines.append('          <p class="facts">%s</p>' % facts)
        if p.get("tags"):
            lines.append("          " + self.pills(p["tags"]))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            lines.append("          " + confirm)
        if extra:
            lines.append("          " + extra)
        lines.append('          <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        return ('      <article class="%s" data-tags="%s">\n        %s\n        <div class="meta">\n'
                '%s\n        </div>\n      </article>'
                % (cls, esc(" ".join(p.get("tags", []))), self.cover_html(p, rp, use_full), "\n".join(lines)))

    def feature_html(self, p, rp):
        href = "%swork/%s.html" % (rp, p["slug"])
        lines = ['        <p class="kicker">%s</p>' % self.txt(p.get("client")),
                 '        <h3><a href="%s">%s</a></h3>' % (href, self.txt(p.get("title")))]
        if p.get("oneliner"):
            lines.append("        " + self.para(p["oneliner"], "desc"))
        facts = self.facts(p)
        if facts:
            lines.append('        <p class="facts">%s</p>' % facts)
        if p.get("tags"):
            lines.append("        " + self.pills(p["tags"]))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            lines.append("        " + confirm)
        lines.append('        <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        return ('    <article class="feature" data-tags="%s">\n      %s\n      <div class="feature-body">\n'
                '%s\n      </div>\n    </article>'
                % (esc(" ".join(p.get("tags", []))), self.cover_html(p, rp, True), "\n".join(lines)))

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
        return ('      <figure class="reel-card"%s>\n'
                '        <div class="frame" style="aspect-ratio:%s">%s</div>\n'
                '        <figcaption><span class="rtitle">%s</span>%s%s%s</figcaption>\n'
                '      </figure>'
                % ((' data-format="%s"' % esc(fmt)) if fmt else "", esc(ratio), frame, self.txt(r.get("title")),
                   ('<span class="rmeta">%s</span>' % meta) if meta else "", extra,
                   self.confirm_pill(r.get("confirm"))))

    # -- shared chrome ------------------------------------------------------

    def nav_html(self, rp, page_key):
        links = []
        for key, label, href in NAV_ITEMS:
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

    def showreel_html(self, rp):
        sr = self.config.get("showreel") or {}
        blocks = []
        missing = False
        for slot in ("vertical", "horizontal"):
            s = sr.get(slot) or {}
            b = video_block_html(s, rp) if (s.get("livid") or s.get("mp4")) else ""
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

    def build_home(self):
        c = self.config
        featured = sorted([p for p in self.visible if p.get("featured")],
                          key=lambda p: p["featured"])
        lead = self.feature_html(featured[0], "") if featured else ""
        cards = "\n".join(self.card_html(p, "", use_full=True) for p in featured[1:4])
        caps = "\n".join(
            '      <a class="cap" href="work.html#%s"><span class="cap-label">%s</span>'
            '<span class="cap-line">%s</span></a>'
            % (esc(cap.get("tag", "")), self.txt(cap.get("label")), self.txt(cap.get("line")))
            for cap in c.get("capabilities", []))
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
            SHOWREEL=self.showreel_html(""),
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
        filters = ['      <a class="filter active" href="#all" data-filter="all" aria-pressed="true">All</a>']
        for cap in self.config.get("capabilities", []):
            filters.append('      <a class="filter" href="#%s" data-filter="%s" aria-pressed="false">%s</a>'
                           % (esc(cap.get("tag", "")), esc(cap.get("tag", "")), self.txt(cap.get("label"))))
        main = tmpl("work.main.tmpl").safe_substitute(
            INTRO=self.page_intro("work"),
            FILTERS="\n".join(filters),
            CARDS="\n".join(self.card_html(p, "") for p in self.visible))
        intro = (self.config.get("page_intros") or {}).get("work", "")
        self.page("work.html", main, "Work — %s" % self.config.get("name", ""),
                  plain(intro) or "Work by %s" % self.config.get("name", ""), "work")

    def build_case(self, idx, p):
        rp = "../"
        slug = p["slug"]
        data = self.manifest.get(slug, {})
        sections = []
        # Lead media: manifest videos first, then demo / ai_steps placeholders.
        lead = [b for b in (video_block_html(v, rp) for v in data.get("videos", [])) if b]
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
            shots = "\n".join(photo_button_html(ph, rp) for ph in data["photos"])
            sections.append('<section class="case-photos">\n  <div class="wrap">\n'
                            '    <p class="kicker">Photos</p>\n    <div class="photos">\n%s\n    </div>\n'
                            '  </div>\n</section>' % shots)
        # Behind the scenes
        if p.get("bts"):
            bts = self.box(p["bts"]) if is_ph(p["bts"]) else '<p class="desc">%s</p>' % esc(p["bts"])
            sections.append('<section class="case-bts">\n  <div class="wrap">\n'
                            '    <p class="kicker">Behind the scenes</p>\n    %s\n  </div>\n</section>' % bts)
        # Previous / next, in visible order, wrapping around.
        n = len(self.visible)
        prev_p, next_p = self.visible[(idx - 1) % n], self.visible[(idx + 1) % n]
        pager = ('<nav class="case-nav" aria-label="More projects">\n'
                 '      <a class="prev" href="%s.html"><span class="kicker">← Previous</span>'
                 '<span class="cn-title">%s</span></a>\n'
                 '      <a class="next" href="%s.html"><span class="kicker">Next →</span>'
                 '<span class="cn-title">%s</span></a>\n'
                 '    </nav>' % (esc(prev_p["slug"]), self.txt(prev_p.get("client")),
                                  esc(next_p["slug"]), self.txt(next_p.get("client"))))
        main = tmpl("case.main.tmpl").safe_substitute(
            RIBBON=self.ribbon(p),
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
        blocks = []
        for key in order:
            label, items = groups[key]
            filters.append('      <a class="filter" href="#%s" data-filter="%s" aria-pressed="false">%s</a>'
                           % (esc(key), esc(key), self.txt(label)))
            cards = [c for c in (self.reel_card_html(r, "") for r in items) if c]
            if not cards:
                continue
            blocks.append('      <div class="reel-group" data-tags="%s">\n'
                          '        <h2 class="reel-group-title">%s <span class="count">%d</span></h2>\n'
                          '        <div class="reel-grid">\n%s\n        </div>\n      </div>'
                          % (esc(key), self.txt(label), len(cards), "\n".join(cards)))
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
        p = self.project(AI_FEATURE)
        if not p:
            return ""
        href = "work/%s.html" % p["slug"]
        copy = ['        <p class="kicker">%s</p>' % self.txt(p.get("client")),
                '        <h2><a href="%s">%s</a></h2>%s' % (href, self.txt(p.get("title")), self.ribbon(p))]
        if p.get("oneliner"):
            copy.append("        " + self.para(p["oneliner"], "desc"))
        if p.get("did"):
            copy.append('        <ul class="did">%s</ul>' % "".join(
                "<li>%s</li>" % self.txt(d) for d in p["did"]))
        confirm = self.confirm_pill(p.get("confirm"))
        if confirm:
            copy.append("        " + confirm)
        copy.append('        <p class="more"><a class="link-arrow" href="%s">View project</a></p>' % href)
        media = [b for b in (video_block_html(v, "") for v in
                             self.manifest.get(p["slug"], {}).get("videos", [])) if b]
        if p.get("demo"):
            media.append("      " + (self.box(p["demo"]) if is_ph(p["demo"])
                                     else '<p class="desc">%s</p>' % esc(p["demo"])))
        return ('<section class="ai-feature">\n  <div class="wrap">\n    <article class="ai-feature-card">\n'
                '      <div class="ai-feature-copy">\n%s\n      </div>\n'
                '      <div class="ai-feature-media">\n%s\n      </div>\n'
                '    </article>\n  </div>\n</section>' % ("\n".join(copy), "\n".join(media)))

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
        for slug in AI_LIVE:
            p = self.project(slug)
            if not p:
                continue
            v = p.get("ai_steps")
            if v:
                steps_html = self.box(v) if is_ph(v) else '<p class="ai-steps">%s</p>' % esc(v)
            else:
                steps_html = self.gen_box(
                    "ai.html#ran-live → %s.ai_steps" % slug,
                    "[%s AI-assisted pieces: real shot → AI step → final]" % plain(p.get("client", slug)))
            live.append(self.card_html(p, "", use_full=True, extra=badge + steps_html,
                                       cls="card live-card"))
        live_html = ""
        if live:
            live_html = ('<section class="ai-live" id="ran-live">\n  <div class="wrap">\n'
                         '    <div class="sec-head">\n      <div>\n        <h2>Ran live</h2>\n      </div>\n    </div>\n'
                         '    <div class="live-grid">\n%s\n    </div>\n  </div>\n</section>' % "\n".join(live))
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
        for i, p in enumerate(self.visible):
            self.build_case(i, p)
        self.build_reels()
        self.build_ai()
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
    # Stale case-study pages (hidden, draft-only in release, deleted projects)
    # must not linger pointing at nothing.
    work_dir = ROOT / "work"
    if work_dir.is_dir():
        for f in work_dir.glob("*.html"):
            if "work/" + f.name not in pages:
                f.unlink()
    # CNAME only when a domain is set; never an empty or guessed one.
    domain = (config.get("domain") or "").strip()
    if domain:
        (ROOT / "CNAME").write_text(domain + "\n", encoding="utf-8")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    release = "--release" in argv
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
        write_pages(pages, site.config)
        print("Built %d pages (%d case studies). BUILD_OK (release)" % (len(pages), len(site.visible)))
        return 0

    write_pages(pages, site.config)
    blocking = len(scan_release(data)) + len(site.generated)
    print("Draft build: %d item(s) still block a release (python3 build.py --release lists them)."
          % blocking)
    print("Built %d pages (%d case studies). BUILD_OK" % (len(pages), len(site.visible)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
