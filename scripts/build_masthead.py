#!/usr/bin/env python3
"""Build the TBS masthead hero images from the staged source PNGs.

Desktop gets a full-bleed back photo plus a cut-out front layer; phones get a
plain (flattened) photo and no cut-out. Idempotent: re-running overwrites the
three outputs with byte-identical files.

Usage:
    /usr/bin/python3 scripts/build_masthead.py
"""

import os

from PIL import Image, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SRC_DIR = os.path.join(ROOT, "staging", "masthead-src")
OUT_DIR = os.path.join(ROOT, "assets", "img", "masthead")

SRC_DESKTOP_BG = os.path.join(SRC_DIR, "desktop-bg.png")
SRC_DESKTOP_FG = os.path.join(SRC_DIR, "desktop-fg.png")
SRC_PHONE_BG = os.path.join(SRC_DIR, "phone-bg.png")

DESKTOP_SIZE = (3200, 1800)
PHONE_SIZE = (1170, 2535)
BG_COLOR = (19, 18, 16)

BACK_QUALITY = 86
FRONT_QUALITY = 88
FRONT_ALPHA_QUALITY = 90
QUALITY_STEP = 2

TARGETS = {
    "tbs-desktop-back.jpg": 600,
    "tbs-desktop-front.webp": 450,
    "tbs-phone-back.jpg": 750,
}


def flatten(img, color):
    """Composite an image onto a solid background colour, returning RGB."""
    if img.mode == "RGBA":
        bg = Image.new("RGB", img.size, color)
        bg.paste(img, mask=img.getchannel("A"))
        return bg
    return img.convert("RGB")


def save_jpeg(img, path, quality=86):
    img.save(path, "JPEG", quality=quality, optimize=True, progressive=True)


def build_desktop_back():
    src = Image.open(SRC_DESKTOP_BG)
    flat = flatten(src, BG_COLOR)
    if flat.size != DESKTOP_SIZE:
        raise SystemExit("ERROR: desktop back source is %dx%d, expected %dx%d"
                         % (flat.size[0], flat.size[1],
                            DESKTOP_SIZE[0], DESKTOP_SIZE[1]))
    path = os.path.join(OUT_DIR, "tbs-desktop-back.jpg")
    limit = TARGETS["tbs-desktop-back.jpg"] * 1024
    quality = BACK_QUALITY
    save_jpeg(flat, path, quality)
    while os.path.getsize(path) > limit and quality > QUALITY_STEP:
        quality -= QUALITY_STEP
        save_jpeg(flat, path, quality)
    return path, flat.size, None, quality


def build_desktop_front():
    src = Image.open(SRC_DESKTOP_FG).convert("RGBA")

    r, g, b, a = src.split()
    a = a.filter(ImageFilter.MinFilter(5))
    src = Image.merge("RGBA", (r, g, b, a))

    if src.size != DESKTOP_SIZE:
        raise SystemExit("ERROR: desktop front source is %dx%d, expected %dx%d"
                         % (src.size[0], src.size[1],
                            DESKTOP_SIZE[0], DESKTOP_SIZE[1]))

    r, g, b, a = src.split()
    a = a.filter(ImageFilter.GaussianBlur(radius=0.6))
    src = Image.merge("RGBA", (r, g, b, a))

    path = os.path.join(OUT_DIR, "tbs-desktop-front.webp")
    limit = TARGETS["tbs-desktop-front.webp"] * 1024
    quality = FRONT_QUALITY
    src.save(path, "WEBP", quality=quality, method=6,
             alpha_quality=FRONT_ALPHA_QUALITY)
    while os.path.getsize(path) > limit and quality > QUALITY_STEP:
        quality -= QUALITY_STEP
        src.save(path, "WEBP", quality=quality, method=6,
                 alpha_quality=FRONT_ALPHA_QUALITY)
    bbox = src.getchannel("A").getbbox()
    return path, src.size, bbox, quality


def build_phone_back():
    src = Image.open(SRC_PHONE_BG)
    flat = flatten(src, BG_COLOR)
    out = flat.resize(PHONE_SIZE, Image.LANCZOS)
    path = os.path.join(OUT_DIR, "tbs-phone-back.jpg")
    save_jpeg(out, path)
    return path, out.size, None


def print_table(rows):
    print()
    print("%-24s %-11s %10s  %s" % ("file", "pixels", "size_kb", "note"))
    print("-" * 72)
    flagged = 0
    for name, size, path, note in rows:
        kb = os.path.getsize(path) / 1024.0
        target = TARGETS.get(name)
        if target is not None and kb >= target:
            flagged += 1
            status = "OVER target %d KB" % target
        else:
            status = "OK"
        label = note + "; " if note else ""
        print("%-24s %-11s %10.1f  %s%s"
              % (name, "%dx%d" % size, kb, label, status))
    print("-" * 72)
    if flagged:
        print("WARNING: %d file(s) exceed the size target (not a failure)." % flagged)
    else:
        print("All outputs within size targets.")


def main():
    for path in (SRC_DESKTOP_BG, SRC_DESKTOP_FG, SRC_PHONE_BG):
        if not os.path.exists(path):
            raise SystemExit("ERROR: missing source %s" % path)

    os.makedirs(OUT_DIR, exist_ok=True)

    back_path, back_size, _, back_quality = build_desktop_back()
    front_path, front_size, front_bbox, front_quality = build_desktop_front()
    phone_path, phone_size, _ = build_phone_back()

    if back_size != front_size:
        raise SystemExit("ERROR: desktop back %s and front %s differ in size"
                         % (back_size, front_size))

    rows = [
        ("tbs-desktop-back.jpg", back_size, back_path,
         "q%d" % back_quality),
        ("tbs-desktop-front.webp", front_size, front_path,
         "q%d alpha%d; alpha bbox %s"
         % (front_quality, FRONT_ALPHA_QUALITY, front_bbox)),
        ("tbs-phone-back.jpg", phone_size, phone_path, ""),
    ]
    print_table(rows)
    print("Wrote 3 files to %s" % OUT_DIR)


if __name__ == "__main__":
    main()
