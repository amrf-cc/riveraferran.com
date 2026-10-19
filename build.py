#!/usr/bin/env python3
"""Generate the static site in place from templates + JSON data. Stdlib only."""
import hashlib
import html
import json
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parent


def load(name):
    with open(ROOT / name) as f:
        return json.load(f)


def tmpl(name):
    return Template((ROOT / "templates" / name).read_text())


def esc(s):
    return html.escape(str(s), quote=True)


_VERSIONS = {}


def asset_url(rel):
    """Repo-relative media path plus a content fingerprint (?v=<8 hex>).

    The content manager renumbers photo files when they are reordered, so the
    same URL starts serving a different image. Browsers and the Pages CDN keep
    showing the old one until their cache expires. A content hash changes the
    URL exactly when the bytes change, and leaves untouched files byte-stable
    across rebuilds."""
    if not rel:
        return rel
    if rel not in _VERSIONS:
        path = ROOT / rel
        _VERSIONS[rel] = (hashlib.sha1(path.read_bytes()).hexdigest()[:8]
                          if path.is_file() else "")
    v = _VERSIONS[rel]
    return "%s?v=%s" % (rel, v) if v else rel


def facts_line(p):
    bits = [b for b in (p.get("location"), p.get("years"), p.get("role")) if b]
    return esc(" · ".join(bits))


def tag_text(photos, videos):
    if photos and videos:
        return "Photo + Video"
    if videos:
        return "Video"
    return "Photo"


def lead_media(data, hero_paths=()):
    """Photo for the lead feature block: the project's cover (photo 01), the
    same one the cards and the Portfolio page use, so the cover set in the
    content manager wins everywhere. Falls back to the second shot only when
    the cover is itself the homepage hero, so the hero never repeats."""
    photos = data["photos"]
    if len(photos) > 1 and photos[0]["full"] in hero_paths:
        return photos[1]["full"], photos[1]["alt"]
    if photos:
        return photos[0]["full"], photos[0]["alt"]
    if data["videos"]:
        return data["videos"][0]["poster"], data["videos"][0]["title"]
    return None, ""


def card_html(p, manifest, root_prefix, num, show_number=True, use_full=False):
    data = manifest["projects"][p["slug"]]
    photos = data["photos"]
    videos = data["videos"]
    if photos:
        thumb = photos[0]["full"] if use_full else photos[0]["thumb"]
        alt = photos[0]["alt"]
    elif videos:
        thumb, alt = videos[0]["poster"], videos[0]["title"]
    else:
        thumb, alt = None, ""
    img = ('<div class="thumb"><img src="%s%s" alt="%s" loading="lazy" draggable="false"></div>'
           % (root_prefix, asset_url(thumb), esc(alt))) if thumb else ""
    sub = esc(p["title"]) + ((" · " + esc(p["years"])) if p.get("years") else "")
    num_line = ('          <span class="num">%02d</span>\n' % num) if show_number else ""
    return (
        '      <a class="card" href="%sgallery/%s.html">\n'
        '        %s\n'
        '        <div class="meta">\n'
        '%s'
        '          <div class="client">%s</div>\n'
        '          <div class="sub">%s</div>\n'
        '          <span class="tag">%s</span>\n'
        '        </div>\n'
        '      </a>'
        % (root_prefix, p["slug"], img, num_line, esc(p["client"]),
           sub, tag_text(photos, videos))
    )


def feature_html(p, manifest, root_prefix, num, hero_paths=()):
    data = manifest["projects"][p["slug"]]
    src, alt = lead_media(data, hero_paths)
    media = ('<a class="thumb" href="%sgallery/%s.html"><img src="%s%s" alt="%s" draggable="false"></a>'
             % (root_prefix, p["slug"], root_prefix, asset_url(src), esc(alt))) if src else ""
    return (
        '    <div class="feature">\n'
        '      %s\n'
        '      <div class="feature-body">\n'
        '        <span class="num">%02d</span>\n'
        '        <p class="kicker" style="margin-top:14px">%s</p>\n'
        '        <h3>%s</h3>\n'
        '        <p class="desc">%s</p>\n'
        '        <p style="color:var(--muted);font-size:14.5px">%s</p>\n'
        '        <span class="tag">%s</span>\n'
        '        <p style="margin-top:22px"><a class="link-arrow" href="%sgallery/%s.html">'
        'View project</a></p>\n'
        '      </div>\n'
        '    </div>'
        % (media, num, esc(p["client"]), esc(p["title"]),
           esc(p["description"]), facts_line(p),
           tag_text(data["photos"], data["videos"]), root_prefix, p["slug"])
    )


def video_block_html(v, root_prefix):
    if v.get("livid"):
        frame = ('<iframe src="%s" allow="autoplay; fullscreen; picture-in-picture" '
                 'allowfullscreen loading="lazy" referrerpolicy="strict-origin-when-cross-origin" title="%s"></iframe>' % (esc(v["livid"]), esc(v["title"])))
    elif v.get("mp4"):
        frame = ('<video controls controlslist="nodownload" draggable="false" preload="none" poster="%s%s" playsinline>'
                 '<source src="%s%s" type="video/mp4"></video>'
                 % (root_prefix, asset_url(v["poster"]), root_prefix, asset_url(v["mp4"])))
    else:
        return ""
    ratio = v.get("ratio") or "16 / 9"
    return ('    <div class="video-block">\n      <div class="frame" style="aspect-ratio:%s">%s</div>\n'
            '      <p class="vtitle">%s</p>\n    </div>' % (esc(ratio), frame, esc(v["title"])))


def photo_button_html(ph, root_prefix):
    return ('      <button class="shot" data-full="%s%s" data-alt="%s">'
            '<img src="%s%s" alt="%s" loading="lazy" draggable="false"></button>'
            % (root_prefix, asset_url(ph["full"]), esc(ph["alt"]),
               root_prefix, asset_url(ph["thumb"]), esc(ph["alt"])))


def render_page(template_name, root_prefix, config, extra, page_key="", og_path=""):
    site_url = "https://" + config["domain"].rstrip("/")
    nav = tmpl("nav.html.tmpl").safe_substitute(
        ROOT_PREFIX=root_prefix,
        NAME=esc(config["name"]),
        HOME_ACTIVE=' class="active"' if page_key == "home" else "",
        GALLERY_ACTIVE=' class="active"' if page_key in ("gallery", "project") else "")
    instagram = ""
    if config.get("instagram"):
        instagram = ' · <a href="%s">Instagram</a>' % esc(config["instagram"])
    footer = tmpl("footer.html.tmpl").safe_substitute(
        ROOT_PREFIX=root_prefix, NAME=esc(config["name"]),
        BASED=esc(config["based"]), EMAIL=esc(config["email"]),
        INSTAGRAM_LINK=instagram)
    form = tmpl("contact_form.html.tmpl").safe_substitute(
        ROOT_PREFIX=root_prefix,
        FORMSPREE_ENDPOINT=esc(config.get("formspree_endpoint", "")),
        EMAIL=esc(config["email"]))
    vars_ = {
        "ROOT_PREFIX": root_prefix,
        "NAME": esc(config["name"]),
        "TITLE": esc(config["title"]),
        "BASED": esc(config["based"]),
        "EMAIL": esc(config["email"]),
        "NAV": nav,
        "FOOTER": footer,
        "CONTACT_FORM": form,
        "SITE_URL": esc(site_url),
        "OG_IMAGE": esc(site_url + "/assets/img/og-cover.jpg"),
        "OG_URL": esc(site_url + "/" + og_path),
    }
    vars_.update(extra)
    return tmpl(template_name).safe_substitute(**vars_)


def main():
    config = load("site-config.json")
    projects = load("projects.json")["projects"]
    visible = [p for p in projects if not p.get("hidden")]
    manifest = load("assets-manifest.json")
    numbers = {p["slug"]: i + 1 for i, p in enumerate(visible)}

    # index.html — first featured project leads, the rest fill the grid
    bio = "\n".join("      <p>%s</p>" % esc(b) for b in config["bio"])
    featured = [p for p in visible if p.get("featured")]
    lead, rest = (featured[0], featured[1:]) if featured else (None, [])
    hero_paths = {config.get("hero_image"), config.get("hero_image_phone")} - {None, ""}
    feature = feature_html(lead, manifest, "", numbers[lead["slug"]], hero_paths) if lead else ""
    cards = "\n".join(card_html(p, manifest, "", numbers[p["slug"]], use_full=True) for p in rest)
    parts = config["name"].split(" ", 1)
    name_html = esc(parts[0]) + "<br>" + esc(parts[1]) if len(parts) > 1 else esc(config["name"])
    words = config["name"].split()
    if len(words) > 1:
        name_masthead = (" ".join('<span class="w">%s</span>' % esc(w) for w in words[:-1])
                         + '<span class="last"> %s</span>' % esc(words[-1]))
    else:
        name_masthead = '<span class="w">%s</span>' % esc(config["name"])
    # Bottom masthead labels render only when at least one has text. The
    # content manager keeps masthead_labels as three strings; empty strings
    # switch the row off without removing the capability.
    bottom_labels = [l for l in config.get("masthead_labels", []) if l and l.strip()]
    if bottom_labels:
        labels_bottom = ('\n    <div class="labels bottom">\n%s\n    </div>'
                         % "\n".join("      <span>%s</span>" % esc(l) for l in bottom_labels))
    else:
        labels_bottom = ""
    page = render_page("index.html.tmpl", "", config, {
        "HERO_IMAGE": esc(config["hero_image"]),
        "HERO_IMAGE_PHONE": esc(config.get("hero_image_phone", config["hero_image"])),
        "HERO_CAPTION": esc(config["hero_caption"]),
        "META_DESCRIPTION": esc(config["title"] + " — " + config["based"]),
        "BIO_PARAGRAPHS": bio,
        "NAME_HTML": name_html,
        "NAME_MASTHEAD": name_masthead,
        "HERO_CUTOUT": esc(config["hero_cutout"]),
        "LABEL_TL": esc(config["title"]),
        "LABEL_TR": esc(config["based"]),
        "LABELS_BOTTOM": labels_bottom,
        "CTA": esc(config["cta"]),
        "FEATURE": feature,
        "FEATURED_CARDS": cards,
    }, page_key="home", og_path="")
    (ROOT / "index.html").write_text(page)

    # gallery.html
    all_cards = "\n".join(card_html(p, manifest, "", numbers[p["slug"]], show_number=False) for p in visible)
    page = render_page("gallery.html.tmpl", "", config, {"ALL_CARDS": all_cards},
                       page_key="gallery", og_path="gallery.html")
    (ROOT / "gallery.html").write_text(page)

    # contact.html
    page = render_page("contact.html.tmpl", "", config, {}, page_key="contact", og_path="contact.html")
    (ROOT / "contact.html").write_text(page)

    # privacy.html + terms.html
    legal = (
        ("privacy", "Privacy Notice", "How riveraferran.com handles your information."),
        ("terms", "Terms of Use", "Copyright, permitted use, and the no-AI-training terms for riveraferran.com."),
    )
    for slug, title, desc in legal:
        body = (ROOT / "templates" / "legal" / (slug + ".body.html")).read_text()
        page = render_page("legal.html.tmpl", "", config, {
            "LEGAL_TITLE": esc(title),
            "LEGAL_DESC": esc(desc),
            "LEGAL_BODY": body,
        }, page_key="legal", og_path=slug + ".html")
        (ROOT / (slug + ".html")).write_text(page)

    # gallery/<slug>.html
    (ROOT / "gallery").mkdir(exist_ok=True)
    for p in projects:
        if p.get("hidden"):
            stale = ROOT / "gallery" / (p["slug"] + ".html")
            if stale.exists():
                stale.unlink()
            continue
        data = manifest["projects"][p["slug"]]
        rp = "../"
        blocks = [b for b in (video_block_html(v, rp) for v in data["videos"]) if b]
        if blocks:
            vids = "\n".join(blocks)
            video_section = ('    <p class="kicker">Video</p>\n    <div class="videos">\n%s\n    </div>' % vids)
        else:
            video_section = ""
        if data["photos"]:
            shots = "\n".join(photo_button_html(ph, rp) for ph in data["photos"])
            photo_section = ('    <p class="kicker" style="margin-top:48px">Photos</p>\n'
                             '    <div class="photos">\n%s\n    </div>' % shots)
        else:
            photo_section = ""
        page = render_page("project.html.tmpl", rp, config, {
            "CLIENT": esc(p["client"]),
            "PTITLE": esc(p["title"]),
            "FACTS": facts_line(p),
            "DESCRIPTION": esc(p["description"]),
            "VIDEO_SECTION": video_section,
            "PHOTO_SECTION": photo_section,
        }, page_key="project", og_path="gallery/" + p["slug"] + ".html")
        (ROOT / "gallery" / (p["slug"] + ".html")).write_text(page)

    # 404 + CNAME
    page = tmpl("404.html.tmpl").safe_substitute(NAME=esc(config["name"]))
    (ROOT / "404.html").write_text(page)
    (ROOT / "CNAME").write_text(config["domain"] + "\n")

    print("Built index.html, gallery.html, contact.html, privacy.html, terms.html, 404.html, CNAME, "
          "and %d project pages. BUILD_OK" % len(visible))


if __name__ == "__main__":
    main()
