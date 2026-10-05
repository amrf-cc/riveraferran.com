#!/usr/bin/env python3
"""One-shot asset pipeline. Reads projects.json, compresses the selected
photos (sips) and videos (ffmpeg) from the Selected Works volume into the
repo, and writes assets-manifest.json. Idempotent: safe to re-run.

Usage:
  python3 prepare_assets.py --check   # verify every source file exists
  python3 prepare_assets.py           # compress + write manifest
"""
import argparse
import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE_ROOT = Path("/Volumes/personal_folder/Selected Works")
FFMPEG = shutil.which("ffmpeg") or "/Users/adrianriveraferran/.hermes/tools/ffmpeg-9.0.1-darwin-arm64/ffmpeg"
FULL_MAX_PX, FULL_Q = "2000", "72"
THUMB_MAX_PX, THUMB_Q = "640", "70"
VIDEO_TARGET_MB = 15


def load_projects():
    with open(ROOT / "projects.json") as f:
        return json.load(f)["projects"]


def photo_src(rel_name):
    return SOURCE_ROOT / "Photos" / rel_name


def video_src(rel_path):
    return SOURCE_ROOT / rel_path


def check_sources(projects):
    """Return list of missing source paths (empty = all good)."""
    missing = []
    for p in projects:
        for photo in p["photos"]:
            s = photo_src(photo["src"])
            if not s.exists():
                missing.append(str(s))
        for v in p["videos"]:
            s = video_src(v["src"])
            if not s.exists():
                missing.append(str(s))
    return missing


def sips(src, dst, max_px, quality):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["sips", "-Z", max_px, "-s", "format", "jpeg",
         "-s", "formatOptions", quality, str(src), "--out", str(dst)],
        check=True, capture_output=True)


def compress_video(src, dst):
    """H.264/aac, max 1920px wide, escalating CRF until under target size."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    size_mb = None
    for crf in ("24", "28", "31", "34"):
        subprocess.run(
            [FFMPEG, "-y", "-i", str(src),
             "-vf", "scale='min(1920,iw)':-2",
             "-c:v", "libx264", "-crf", crf, "-preset", "medium",
             "-c:a", "aac", "-b:a", "128k",
             "-movflags", "+faststart", str(dst)],
            check=True, capture_output=True)
        size_mb = dst.stat().st_size / (1024 * 1024)
        if size_mb <= VIDEO_TARGET_MB:
            break
    return size_mb


def video_poster(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-i", str(src), "-frames:v", "1",
         "-vf", "scale='min(1280,iw)':-2",
         "-q:v", "4", str(dst)],
        check=True, capture_output=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if not SOURCE_ROOT.exists():
        sys.exit("ERROR: %s not mounted. Mount the volume and retry." % SOURCE_ROOT)

    projects = load_projects()
    missing = check_sources(projects)
    if missing:
        print("MISSING SOURCE FILES:")
        for m in missing:
            print("  " + m)
        sys.exit(1)
    print("All %d project sources present." % len(projects))
    if args.check:
        print("CHECK_OK")
        return

    manifest = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source_root": str(SOURCE_ROOT),
        "projects": {},
    }
    for p in projects:
        slug = p["slug"]
        entry = {"photos": [], "videos": []}
        for i, photo in enumerate(p["photos"], start=1):
            idx = "%02d" % i
            full = "assets/img/%s/%s.jpg" % (slug, idx)
            thumb = "assets/img/%s/thumbs/%s.jpg" % (slug, idx)
            sips(photo_src(photo["src"]), ROOT / full, FULL_MAX_PX, FULL_Q)
            sips(photo_src(photo["src"]), ROOT / thumb, THUMB_MAX_PX, THUMB_Q)
            entry["photos"].append({"full": full, "thumb": thumb, "alt": photo["alt"]})
            print("photo  %s -> %s" % (photo["src"], full))
        for i, v in enumerate(p["videos"], start=1):
            idx = "v%02d" % i
            mp4 = "assets/video/%s-%s.mp4" % (slug, idx)
            poster = "assets/video/posters/%s-%s.jpg" % (slug, idx)
            size_mb = compress_video(video_src(v["src"]), ROOT / mp4)
            video_poster(video_src(v["src"]), ROOT / poster)
            entry["videos"].append({"title": v["title"], "mp4": mp4,
                                    "poster": poster, "livid": v.get("livid", ""),
                                    "ratio": v.get("ratio", "")})
            print("video  %s -> %s (%.1f MB)" % (v["src"], mp4, size_mb))
        manifest["projects"][slug] = entry

    out = ROOT / "assets-manifest.json"
    out.write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(f.stat().st_size for f in (ROOT / "assets").rglob("*") if f.is_file())
    print("Wrote %s. assets/ total: %.1f MB. PREPARE_OK" % (out.name, total / 1e6))


if __name__ == "__main__":
    main()
