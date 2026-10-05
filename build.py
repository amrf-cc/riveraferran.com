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


def card_html(p, manifest, root_prefix):
    photos = manifest["projects"][p["slug"]]["photos"]
    thumb = photos[0]["thumb"] if photos else None
    videos = manifest["projects"][p["slug"]]["videos"]
    media = ""
    if videos:
        media = '<span class="badge">Photo + Video</span>'
    elif thumb:
        media = '<span class="badge">Photo</span>'
    else:
        media = '<span class="badge">Video</span>'
    img = ('<div class="thumb"><img src="%s%s" alt="%s" loading="lazy"></div>'
           % (root_prefix, thumb, esc(photos[0]["alt"]))) if thumb else ""
    return (
        '      <a class="card" href="%sgallery/%s.html">\n'
        '%s'
        '        <div class="meta">\n'
        '          <div class="client">%s</div>\n'
        '          <div class="sub">%s%s</div>\n'
        '          %s\n'
        '        </div>\n'
        '      </a>'
        % (root_prefix, p["slug"], img, esc(p["client"]),
           esc(p["title"]),
           (" · " + esc(p["years"])) if p.get("years") else "",
           media)
    )


def video_block_html(v, root_prefix):
    if v.get("livid"):
        frame = ('<iframe src="%s" allow="autoplay; fullscreen; picture-in-picture" '
                 'allowfullscreen title="%s"></iframe>' % (esc(v["livid"]), esc(v["title"])))
    else:
        frame = ('<video controls preload="none" poster="%s%s" playsinline>'
                 '<source src="%s%s" type="video/mp4"></video>'
                 % (root_prefix, v["poster"], root_prefix, v["mp4"]))
    return ('    <div class="video-block">\n      <div class="frame">%s</div>\n'
            '      <p class="vtitle">%s</p>\n    </div>' % (frame, esc(v["title"])))


def photo_button_html(ph, root_prefix):
    return ('      <button class="shot" data-full="%s%s" data-alt="%s">'
            '<img src="%s%s" alt="%s" loading="lazy"></button>'
            % (root_prefix, ph["full"], esc(ph["alt"]), root_prefix, ph["thumb"], esc(ph["alt"])))


def render_page(template_name, root_prefix, config, extra):
    nav = tmpl("nav.html.tmpl").safe_substitute(
        ROOT_PREFIX=root_prefix, NAME=esc(config["name"]))
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

    # index.html
    bio = "\n".join("    <p>%s</p>" % esc(b) for b in config["bio"])
    featured = [p for p in projects if p.get("featured")]
    cards = "\n".join(card_html(p, manifest, "") for p in featured)
    page = render_page("index.html.tmpl", "", config, {
        "HERO_IMAGE": esc(config["hero_image"]),
        "HERO_CAPTION": esc(config["hero_caption"]),
        "META_DESCRIPTION": esc(config["title"] + " — " + config["based"]),
        "BIO_PARAGRAPHS": bio,
        "CTA": esc(config["cta"]),
        "FEATURED_CARDS": cards,
    })
    (ROOT / "index.html").write_text(page)

    # gallery.html
    all_cards = "\n".join(card_html(p, manifest, "") for p in projects)
    page = render_page("gallery.html.tmpl", "", config, {"ALL_CARDS": all_cards})
    (ROOT / "gallery.html").write_text(page)

    # contact.html
    page = render_page("contact.html.tmpl", "", config, {})
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
            photo_section = ('    <p class="kicker" style="margin-top:32px">Photos</p>\n'
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
        })
        (ROOT / "gallery" / (p["slug"] + ".html")).write_text(page)

    # 404 + CNAME
    page = tmpl("404.html.tmpl").safe_substitute(NAME=esc(config["name"]))
    (ROOT / "404.html").write_text(page)
    (ROOT / "CNAME").write_text(config["domain"] + "\n")

    print("Built index.html, gallery.html, contact.html, 404.html, CNAME, "
          "and %d project pages. BUILD_OK" % len(projects))


if __name__ == "__main__":
    main()
