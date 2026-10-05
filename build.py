#!/usr/bin/env python3
"""Generate the static site in place from templates + JSON data. Stdlib only."""
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


def facts_line(p):
    bits = [b for b in (p.get("location"), p.get("years"), p.get("role")) if b]
    return esc(" · ".join(bits))


def tag_text(photos, videos):
    if photos and videos:
        return "Photo + Video"
    if videos:
        return "Video"
    return "Photo"


def lead_media(data):
    """Photo for the lead feature block: prefer the second shot so it never
    repeats the hero image."""
    photos = data["photos"]
    if len(photos) > 1:
        return photos[1]["full"], photos[1]["alt"]
    if photos:
        return photos[0]["full"], photos[0]["alt"]
    if data["videos"]:
        return data["videos"][0]["poster"], data["videos"][0]["title"]
    return None, ""


def card_html(p, manifest, root_prefix, num, show_number=True):
    data = manifest["projects"][p["slug"]]
    photos = data["photos"]
    videos = data["videos"]
    if photos:
        thumb, alt = photos[0]["thumb"], photos[0]["alt"]
    elif videos:
        thumb, alt = videos[0]["poster"], videos[0]["title"]
    else:
        thumb, alt = None, ""
    img = ('<div class="thumb"><img src="%s%s" alt="%s" loading="lazy"></div>'
           % (root_prefix, thumb, esc(alt))) if thumb else ""
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


def feature_html(p, manifest, root_prefix, num):
    data = manifest["projects"][p["slug"]]
    src, alt = lead_media(data)
    media = ('<a class="thumb" href="%sgallery/%s.html"><img src="%s%s" alt="%s"></a>'
             % (root_prefix, p["slug"], root_prefix, src, esc(alt))) if src else ""
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
                 'allowfullscreen title="%s"></iframe>' % (esc(v["livid"]), esc(v["title"])))
    else:
        frame = ('<video controls preload="none" poster="%s%s" playsinline>'
                 '<source src="%s%s" type="video/mp4"></video>'
                 % (root_prefix, v["poster"], root_prefix, v["mp4"]))
    ratio = v.get("ratio") or "16 / 9"
    return ('    <div class="video-block">\n      <div class="frame" style="aspect-ratio:%s">%s</div>\n'
            '      <p class="vtitle">%s</p>\n    </div>' % (esc(ratio), frame, esc(v["title"])))


def photo_button_html(ph, root_prefix):
    return ('      <button class="shot" data-full="%s%s" data-alt="%s">'
            '<img src="%s%s" alt="%s" loading="lazy"></button>'
            % (root_prefix, ph["full"], esc(ph["alt"]), root_prefix, ph["thumb"], esc(ph["alt"])))


def render_page(template_name, root_prefix, config, extra, page_key=""):
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
    }
    vars_.update(extra)
    return tmpl(template_name).safe_substitute(**vars_)


def main():
    config = load("site-config.json")
    projects = load("projects.json")["projects"]
    manifest = load("assets-manifest.json")
    numbers = {p["slug"]: i + 1 for i, p in enumerate(projects)}

    # index.html — first featured project leads, the rest fill the grid
    bio = "\n".join("      <p>%s</p>" % esc(b) for b in config["bio"])
    featured = [p for p in projects if p.get("featured")]
    lead, rest = (featured[0], featured[1:]) if featured else (None, [])
    feature = feature_html(lead, manifest, "", numbers[lead["slug"]]) if lead else ""
    cards = "\n".join(card_html(p, manifest, "", numbers[p["slug"]]) for p in rest)
    parts = config["name"].split(" ", 1)
    name_html = esc(parts[0]) + "<br>" + esc(parts[1]) if len(parts) > 1 else esc(config["name"])
    page = render_page("index.html.tmpl", "", config, {
        "HERO_IMAGE": esc(config["hero_image"]),
        "HERO_CAPTION": esc(config["hero_caption"]),
        "META_DESCRIPTION": esc(config["title"] + " — " + config["based"]),
        "BIO_PARAGRAPHS": bio,
        "NAME_HTML": name_html,
        "CTA": esc(config["cta"]),
        "FEATURE": feature,
        "FEATURED_CARDS": cards,
    }, page_key="home")
    (ROOT / "index.html").write_text(page)

    # gallery.html
    all_cards = "\n".join(card_html(p, manifest, "", numbers[p["slug"]], show_number=False) for p in projects)
    page = render_page("gallery.html.tmpl", "", config, {"ALL_CARDS": all_cards},
                       page_key="gallery")
    (ROOT / "gallery.html").write_text(page)

    # contact.html
    page = render_page("contact.html.tmpl", "", config, {}, page_key="contact")
    (ROOT / "contact.html").write_text(page)

    # gallery/<slug>.html
    (ROOT / "gallery").mkdir(exist_ok=True)
    for p in projects:
        data = manifest["projects"][p["slug"]]
        rp = "../"
        if data["videos"]:
            vids = "\n".join(video_block_html(v, rp) for v in data["videos"])
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
        }, page_key="project")
        (ROOT / "gallery" / (p["slug"] + ".html")).write_text(page)

    # 404 + CNAME
    page = tmpl("404.html.tmpl").safe_substitute(NAME=esc(config["name"]))
    (ROOT / "404.html").write_text(page)
    (ROOT / "CNAME").write_text(config["domain"] + "\n")

    print("Built index.html, gallery.html, contact.html, 404.html, CNAME, "
          "and %d project pages. BUILD_OK" % len(projects))


if __name__ == "__main__":
    main()
