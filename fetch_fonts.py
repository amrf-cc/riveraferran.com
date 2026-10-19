#!/usr/bin/env python3
"""Download Google Fonts (Fraunces + Inter) woff2 subsets and generate a local fonts.css."""
import re
import urllib.request
from pathlib import Path

ROOT = Path("/Users/adrianriveraferran/Projects/riveraferran.com")
FONT_DIR = ROOT / "assets" / "fonts"
FONT_DIR.mkdir(parents=True, exist_ok=True)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

FAMILIES = {
    "fraunces": "Fraunces:opsz,wght@9..144,300..800",
    "inter": "Inter:wght@300..700",
}

KEEP_SUBSETS = ("latin", "latin-ext")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def main():
    out_css = ["/* Self-hosted variable fonts — no third-party requests. */"]
    for slug, spec in FAMILIES.items():
        css_url = "https://fonts.googleapis.com/css2?family=%s&display=swap" % spec
        css = fetch(css_url).decode("utf-8")
        blocks = re.split(r"(?=/\*\s*[a-z-]+\s*\*/)", css)
        kept = 0
        for block in blocks:
            m = re.match(r"/\*\s*([a-z-]+)\s*\*/", block.lstrip())
            if not m or m.group(1) not in KEEP_SUBSETS:
                continue
            subset = m.group(1)
            url_m = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", block)
            if not url_m:
                continue
            remote = url_m.group(1)
            dest_name = "%s-%s.woff2" % (slug, subset)
            data = fetch(remote)
            (FONT_DIR / dest_name).write_bytes(data)
            local_block = block.replace(remote, "../fonts/" + dest_name)
            local_block = re.sub(r"/\*\s*[a-z-]+\s*\*/", "/* %s */" % subset, local_block)
            out_css.append(local_block.strip())
            kept += 1
            print("  %-28s %7d bytes" % (dest_name, len(data)))
        print("%s: kept %d subsets" % (slug, kept))
    (ROOT / "assets" / "css" / "fonts.css").write_text("\n".join(out_css) + "\n")
    print("wrote assets/css/fonts.css")


if __name__ == "__main__":
    main()
