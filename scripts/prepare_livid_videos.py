#!/usr/bin/env python3
"""Prepare upload-ready encodes + posters for the nine Livid videos.

Reads sources READ-ONLY from MEDIA_ROOT and writes everything to OUT_DIR
(which lives inside the media folder, never inside the repo).

Usage:
    /usr/bin/python3 scripts/prepare_livid_videos.py --list   # dry run
    /usr/bin/python3 scripts/prepare_livid_videos.py          # real run
"""

import argparse
import json
import os
import shutil
import subprocess
import sys

MEDIA_ROOT = "/Volumes/Scratch Disk/Projects/Websites/riveraferran.com-media"
OUT_DIR = os.path.join(MEDIA_ROOT, "_livid-upload")
POSTER_DIR = os.path.join(OUT_DIR, "posters")
MANIFEST = os.path.join(OUT_DIR, "upload-list.json")

# Do not touch: /Volumes/Scratch Disk/Projects/Websites/portfolio/

MAX_CRF = 32
DEFAULT_CAP_MB = 200.0
TOTAL_CAP_MB = 800.0
EXISTING_USED_MB = 44.0
FREE_PLAN_MB = 1024.0

VIDEO_SCALE = "scale=w=1920:h=1920:force_original_aspect_ratio=decrease:force_divisible_by=2"
POSTER_SCALE = "scale=w=1280:h=1280:force_original_aspect_ratio=decrease:force_divisible_by=2"

# (slug, id, source path relative to MEDIA_ROOT, display title, ratio)
VIDEOS = [
    ("acai-express", "v01", "Acai Express/Commercial FINAL.mp4",
     "Commercial", "16 / 9", 22, DEFAULT_CAP_MB),
    ("acai-express", "v02", "Acai Express/COVID19 - Lifestyle.mp4",
     "COVID-19 Lifestyle", "16 / 9", 22, DEFAULT_CAP_MB),
    ("acai-express", "v03", "Acai Express/Lifestyle - Jiu Jitsu Revised.mp4",
     "Lifestyle \u2014 Jiu Jitsu", "16 / 9", 22, DEFAULT_CAP_MB),
    ("acai-express", "v04", "Acai Express/Truck animation REVISED 4.m4v",
     "Truck animation", "9 / 16", 22, DEFAULT_CAP_MB),
    ("blue-moon", "v01", "Blue Moon/Blue Moon x Checkin Mela blurred.mov",
     "Blue Moon x Checkin Mela", "16 / 9", 22, DEFAULT_CAP_MB),
    ("chef-mia-castro", "v01", "Chef Mia Castro/CUTDOWN 2 - Episode Preview (9x16) v3.mp4",
     "Episode preview", "9 / 16", 22, DEFAULT_CAP_MB),
    ("chef-mia-castro", "v02", "Chef Mia Castro/Ep1 Sabor y Sazon v7.mp4",
     "Sabor y Saz\u00f3n \u2014 Episode 1", "16 / 9", 26, 450.0),
    ("joe-and-joe", "v01", "Joe & Joe/Joe&Joe 25 de julio REVISADO.mp4",
     "Joe & Joe", "16 / 9", 22, DEFAULT_CAP_MB),
    ("sobao-cidrines", "v02", "Sobao by Los Cidrines/BOD-22-0245 Grabacion BOD febrero REV.mp4",
     "BOD \u2014 Grabaci\u00f3n febrero", "16 / 9", 22, DEFAULT_CAP_MB),
]


def find_tool(name):
    path = shutil.which(name)
    if path:
        return path
    fallback = os.path.join("/opt/homebrew/bin", name)
    if os.path.exists(fallback):
        return fallback
    return None


def build_items():
    items = []
    for slug, vid, rel, title, ratio, start_crf, cap_mb in VIDEOS:
        items.append({
            "slug": slug,
            "id": vid,
            "rel": rel,
            "source": os.path.join(MEDIA_ROOT, rel),
            "title": title,
            "ratio": ratio,
            "start_crf": start_crf,
            "cap_mb": cap_mb,
            "name": "%s-%s" % (slug, vid),
        })
    return items


def run(cmd, capture=True):
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def probe(path):
    """Return a dict with duration, video codec/dims/rotation and audio presence."""
    info = {
        "duration": None,
        "vcodec": None,
        "width": None,
        "height": None,
        "rotation": 0,
        "has_audio": False,
    }
    ffprobe = find_tool("ffprobe")
    cmd = [
        ffprobe, "-v", "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height:"
        "stream_side_data=rotation:stream_tags=rotate",
        "-of", "json", path,
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        return None
    try:
        info["duration"] = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        info["duration"] = None
    for stream in data.get("streams", []):
        ctype = stream.get("codec_type")
        if ctype == "audio":
            info["has_audio"] = True
        elif ctype == "video" and info["vcodec"] is None:
            info["vcodec"] = stream.get("codec_name")
            info["width"] = stream.get("width")
            info["height"] = stream.get("height")
            rotation = 0
            for sd in stream.get("side_data_list", []) or []:
                if "rotation" in sd:
                    try:
                        rotation = int(float(sd["rotation"]))
                    except (TypeError, ValueError):
                        rotation = 0
            if not rotation:
                tag = stream.get("tags", {}) or {}
                if tag.get("rotate"):
                    try:
                        rotation = int(float(tag["rotate"]))
                    except (TypeError, ValueError):
                        rotation = 0
            info["rotation"] = rotation
    return info


def displayed_dims(info):
    w, h = info["width"], info["height"]
    if info["rotation"] and abs(info["rotation"]) % 180 == 90:
        w, h = h, w
    return w, h


def encode(item, crf):
    """Encode one source to OUT_DIR/<name>.mp4 at the given crf. Returns path."""
    ffmpeg = find_tool("ffmpeg")
    out_path = os.path.join(OUT_DIR, item["name"] + ".mp4")
    tmp_path = out_path + ".part.mp4"
    cmd = [
        ffmpeg, "-y", "-i", item["source"],
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-vf", VIDEO_SCALE,
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        tmp_path,
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("ERROR: ffmpeg failed encoding %s" % item["rel"])
    os.replace(tmp_path, out_path)
    return out_path


def encode_with_ladder(item):
    """Encode respecting the per-video cap; returns (path, crf, escalations)."""
    escalations = []
    crf = item["start_crf"]
    cap_bytes = item["cap_mb"] * 1024 * 1024
    while True:
        path = encode(item, crf)
        size = os.path.getsize(path)
        if size <= cap_bytes or crf >= MAX_CRF:
            if size > cap_bytes:
                sys.stderr.write(
                    "WARNING: %s still %.1f MB at max crf %d (cap %.0f MB)\n"
                    % (item["name"], size / 1048576.0, crf, item["cap_mb"])
                )
            return path, crf, escalations
        new_crf = min(crf + 3, MAX_CRF)
        escalations.append((crf, new_crf))
        crf = new_crf


def decode_test(path, full):
    ffmpeg = find_tool("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", path]
    if not full:
        cmd += ["-t", "60"]
    cmd += ["-f", "null", "-"]
    proc = run(cmd)
    err = proc.stderr.decode("utf-8", "replace")
    return proc.returncode == 0 and err.strip() == ""


def verify(item, out_path, info_source, full_decode):
    """Verify an output; raise loudly on any failure. Returns probe info of output."""
    info = probe(out_path)
    if info is None or info["duration"] is None:
        raise SystemExit("VERIFY FAIL: %s is not a readable video" % out_path)
    if info["vcodec"] != "h264":
        raise SystemExit("VERIFY FAIL: %s codec is %s, expected h264"
                         % (out_path, info["vcodec"]))
    if info_source["has_audio"] and not info["has_audio"]:
        raise SystemExit("VERIFY FAIL: %s is missing the source audio track" % out_path)
    if abs(info["duration"] - info_source["duration"]) > 1.5:
        raise SystemExit(
            "VERIFY FAIL: %s duration %.2fs differs from source %.2fs"
            % (out_path, info["duration"], info_source["duration"]))
    w, h = displayed_dims(info)
    if item["ratio"] == "9 / 16" and not h > w:
        raise SystemExit("VERIFY FAIL: %s expected portrait, got %dx%d"
                         % (out_path, w, h))
    if item["ratio"] == "16 / 9" and not w > h:
        raise SystemExit("VERIFY FAIL: %s expected landscape, got %dx%d"
                         % (out_path, w, h))
    if not decode_test(out_path, full_decode):
        raise SystemExit("VERIFY FAIL: decode test failed for %s" % out_path)
    return info


def make_poster(item, out_path, duration):
    poster = os.path.join(POSTER_DIR, item["name"] + ".jpg")
    if os.path.exists(poster):
        return poster
    ffmpeg = find_tool("ffmpeg")
    t = min(2.0, 0.1 * duration)
    cmd = [
        ffmpeg, "-y", "-ss", "%.3f" % t, "-i", out_path,
        "-frames:v", "1", "-vf", POSTER_SCALE, "-q:v", "4", poster,
    ]
    proc = run(cmd)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr.decode("utf-8", "replace"))
        raise SystemExit("ERROR: poster failed for %s" % item["name"])
    return poster


def print_list(items):
    print("%-17s %-4s  %-34s %-8s %s" % ("slug", "id", "source", "ratio", "exists"))
    print("-" * 100)
    missing = 0
    for it in items:
        ok = os.path.exists(it["source"])
        if not ok:
            missing += 1
        print("%-17s %-4s  %-34s %-8s %s"
              % (it["slug"], it["id"], it["rel"], it["ratio"],
                 "yes" if ok else "MISSING"))
    print("-" * 100)
    print("%d video(s); %d source path(s) missing." % (len(items), missing))
    if missing:
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true",
                        help="dry run: print table and verify source paths, no encoding")
    args = parser.parse_args()

    items = build_items()

    if args.list:
        print_list(items)
        return

    for tool in ("ffmpeg", "ffprobe"):
        if find_tool(tool) is None:
            raise SystemExit("ERROR: %s not found" % tool)

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(POSTER_DIR, exist_ok=True)

    sources = {}
    results = {}
    escalations = {}
    used_crf = {}
    for it in items:
        src_info = probe(it["source"])
        if src_info is None:
            raise SystemExit("ERROR: cannot ffprobe source %s" % it["rel"])
        sources[it["name"]] = src_info

        out_path = os.path.join(OUT_DIR, it["name"] + ".mp4")
        if os.path.exists(out_path):
            try:
                verify(it, out_path, src_info, full_decode=True)
                print("skip   %s (exists and verified)" % it["name"])
                results[it["name"]] = out_path
                used_crf[it["name"]] = it["start_crf"]
                continue
            except SystemExit:
                print("redo   %s (existing output failed verification)" % it["name"])
        print("encode %s (crf %d)" % (it["name"], it["start_crf"]))
        out_path, crf, esc = encode_with_ladder(it)
        used_crf[it["name"]] = crf
        if esc:
            escalations[it["name"]] = esc
        results[it["name"]] = out_path

    # Hard total cap: re-encode the largest outputs until it fits.
    def total_mb():
        return sum(os.path.getsize(results[it["name"]]) for it in items) / 1048576.0

    while total_mb() > TOTAL_CAP_MB:
        candidates = [it for it in items if used_crf[it["name"]] < MAX_CRF]
        if not candidates:
            sys.stderr.write(
                "WARNING: total still %.1f MB with all videos at max crf %d\n"
                % (total_mb(), MAX_CRF))
            break
        largest = max(candidates, key=lambda it: os.path.getsize(results[it["name"]]))
        cur_crf = used_crf[largest["name"]]
        new_crf = min(cur_crf + 3, MAX_CRF)
        print("total %.1f MB > %.0f MB: re-encoding %s at crf %d"
              % (total_mb(), TOTAL_CAP_MB, largest["name"], new_crf))
        used_crf[largest["name"]] = new_crf
        escalations.setdefault(largest["name"], []).append((cur_crf, new_crf))
        results[largest["name"]] = encode(largest, new_crf)

    # Determine the five smallest outputs for the full decode test.
    by_size = sorted(items, key=lambda it: os.path.getsize(results[it["name"]]))
    full_decode_names = set(it["name"] for it in by_size[:5])

    manifest = []
    for idx, it in enumerate(items, start=1):
        out_path = results[it["name"]]
        info = verify(it, out_path, sources[it["name"]],
                      full_decode=it["name"] in full_decode_names)
        w, h = displayed_dims(info)
        poster = make_poster(it, out_path, info["duration"])
        manifest.append({
            "slug": it["slug"],
            "id": it["id"],
            "upload_file": os.path.basename(out_path),
            "poster": os.path.basename(poster),
            "title": it["title"],
            "ratio": it["ratio"],
            "size_mb": round(os.path.getsize(out_path) / 1048576.0, 1),
            "duration_s": round(info["duration"], 1),
            "width": w,
            "height": h,
            "source": it["rel"],
        })

    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    total = round(sum(m["size_mb"] for m in manifest), 1)
    print()
    print("%-3s %-17s %-4s %-30s %8s %9s %11s %6s" %
          ("#", "slug", "id", "title", "size_mb", "dur_s", "dims", "ratio"))
    print("-" * 100)
    for i, m in enumerate(manifest, start=1):
        print("%-3d %-17s %-4s %-30s %8.1f %9.1f %11s %6s" %
              (i, m["slug"], m["id"], m["title"], m["size_mb"],
               m["duration_s"], "%dx%d" % (m["width"], m["height"]), m["ratio"]))
    print("-" * 100)
    print("TOTAL: %.1f MB" % total)
    print("Free-plan headroom: 1024 - 44 (existing) - %.1f = %.1f MB"
          % (total, FREE_PLAN_MB - EXISTING_USED_MB - total))
    if escalations:
        print("CRF escalations:")
        for name in sorted(escalations):
            for old, new in escalations[name]:
                print("  %s: crf %d -> %d" % (name, old, new))
    print("Manifest: %s" % MANIFEST)


if __name__ == "__main__":
    main()
