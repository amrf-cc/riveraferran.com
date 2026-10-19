#!/usr/bin/env python3
"""Append new client photos from MEDIA_ROOT into projects.json and
assets-manifest.json.

Append-only and idempotent: existing photo entries, existing assets and
everything under MEDIA_ROOT are never modified or regenerated. Running it a
second time prints "nothing to do".

Usage:
  python3 ingest_media.py
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MEDIA_ROOT = Path("/Volumes/Scratch Disk/Projects/Websites/riveraferran.com-media")

FULL_MAX_PX = 2000
FULL_Q = 72
THUMB_MAX_PX = 640
THUMB_Q = 70

IMAGE_EXTS = (".jpg", ".jpeg", ".png")

FOLDERS = {
    "chip-city": "Chip City",
    "massis": "Massi's Homemade Sandwiches",
    "brasa-astoria": "Brasa",
    "sobao-cidrines": "Sobao by Los Cidrines",
    "acai-express": "Acai Express",
    "chef-mia-castro": "Chef Mia Castro",
    "gyro-king": "Gyro King",
}

NOISE_PATTERNS = (
    r"[\s\-.]+edit$",
    r"[\s\-.]+enhanced-nr$",
    r"[\s\-.]+enhanced$",
    r"[\s\-.]+nr$",
    r"[\s\-.]+copy$",
    r"[\s\-.]+v\d+$",
    r"[\s\-.]+[0-9]+x[0-9]+$",
    r"[\s\-]+[0-9]+$",
)


def natural_key(name):
    """Case-insensitive natural sort key: "x-2" sorts before "x-10"."""
    return tuple(
        (1, int(part), "") if part.isdigit() else (0, 0, part)
        for part in re.split(r"(\d+)", name.lower())
    )


def make_alt(stem, client):
    """Alt text for a file stem (rule 8). Never empty, slug/camera-safe."""
    name = stem.replace("_", " ")
    changed = True
    while changed:
        changed = False
        for pattern in NOISE_PATTERNS:
            match = re.search(pattern, name, re.IGNORECASE)
            if match and match.end() == len(name):
                name = name[:match.start()]
                changed = True
                break
    name = re.sub(r"\s+", " ", name).strip()
    lowered = name.lower()
    letters = sum(1 for ch in name if ch.isalpha())
    generic = (
        name == ""
        or letters < 3
        or lowered.startswith(("img", "dsc", "dscf", "amrf"))
        or re.fullmatch(r"[0-9\-\s]+", name) is not None
        or "photographer" in lowered
    )
    if generic:
        return "%s photograph" % client
    return "%s, %s" % (name, client)


def _file_md5(path):
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def list_candidates(folder_path):
    """Photo files (*.jpg/*.jpeg/*.png, case-insensitive) directly inside the
    folder, dotfiles and subfolders (e.g. "Selected videos") excluded."""
    folder = Path(folder_path)
    candidates = []
    if not folder.is_dir():
        return candidates
    for entry in folder.iterdir():
        if entry.name.startswith("."):
            continue
        if not entry.is_file():
            continue
        if entry.suffix.lower() in IMAGE_EXTS:
            candidates.append(entry)
    candidates.sort(key=lambda p: natural_key(p.name))
    return candidates


def dedupe(paths):
    """Group by md5, keep the shortest filename per group. Returns
    (kept, skipped), both in natural order."""
    groups = {}
    for path in paths:
        groups.setdefault(_file_md5(path), []).append(path)
    kept = []
    skipped = []
    for group in groups.values():
        group = sorted(group, key=lambda p: (len(p.name), natural_key(p.name)))
        kept.append(group[0])
        skipped.extend(group[1:])
    kept.sort(key=lambda p: natural_key(p.name))
    skipped.sort(key=lambda p: natural_key(p.name))
    return kept, skipped


def _index_str(number):
    return "%02d" % number if number < 100 else "%03d" % number


def plan_with_stats(projects, manifest, media_root=MEDIA_ROOT):
    project_list = projects["projects"] if isinstance(projects, dict) else projects
    by_slug = {}
    for project in project_list:
        by_slug[project["slug"]] = project

    media_root = Path(media_root)
    plan_entries = []
    stats_rows = []
    for slug, folder_name in FOLDERS.items():
        project = by_slug.get(slug)
        folder_path = media_root / folder_name
        candidates = list_candidates(folder_path)
        kept, dupes = dedupe(candidates)

        existing = set()
        removed = set()
        if project is not None:
            for photo in project.get("photos", []):
                existing.add(str(photo.get("src", "")).split("/")[-1])
            # Photos Adrian deleted in the content manager. Their originals
            # still sit in the media folder, so without this list an
            # append-only ingest would quietly bring them back.
            for src in project.get("removed_photos", []):
                removed.add(str(src).split("/")[-1])

        already = [p for p in kept if p.name in existing]
        appended = [p for p in kept if p.name not in existing and p.name not in removed]

        current_total = len(project.get("photos", [])) if project is not None else 0
        client = project.get("client", folder_name) if project is not None else folder_name
        items = []
        for offset, path in enumerate(appended):
            items.append({
                "src_path": path,
                "rel_src": "%s/%s" % (folder_name, path.name),
                "alt": make_alt(path.stem, client),
                "index": current_total + 1 + offset,
            })

        if items and project is not None:
            plan_entries.append({
                "slug": slug,
                "folder": folder_name,
                "client": client,
                "items": items,
            })

        stats_rows.append({
            "slug": slug,
            "folder": folder_name,
            "folder_photos": len(candidates),
            "skipped_dupe": len(dupes),
            "skipped_existing": len(already),
            "appended": len(items),
            "final_total": current_total + len(items),
        })

    return plan_entries, stats_rows


def plan(projects, manifest, media_root=MEDIA_ROOT):
    return plan_with_stats(projects, manifest, media_root)[0]


def _pixel_size(path):
    result = subprocess.run(
        ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)],
        check=True, capture_output=True, text=True)
    width = height = None
    for line in result.stdout.splitlines():
        line = line.strip()
        if line.startswith("pixelWidth:"):
            width = int(line.split(":", 1)[1].strip())
        elif line.startswith("pixelHeight:"):
            height = int(line.split(":", 1)[1].strip())
    return width, height


def process_image(src, dst, max_px, quality):
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    args = ["sips"]
    width, height = _pixel_size(src)
    if width and height and max(width, height) > int(max_px):
        args += ["-Z", str(max_px)]
    args += ["-s", "format", "jpeg", "-s", "formatOptions", str(quality),
             str(src), "--out", str(dst)]
    subprocess.run(args, check=True, capture_output=True)


def load_canonical(name):
    path = ROOT / name
    text = path.read_text(encoding="utf-8")
    obj = json.loads(text)
    if json.dumps(obj, indent=2, ensure_ascii=False) + "\n" != text:
        print("ERROR: %s is not in canonical json.dumps(..., indent=2, "
              "ensure_ascii=False) format; refusing to edit." % name)
        sys.exit(1)
    return obj


def _print_table(stats_rows):
    print("%-16s | %-13s | %-13s | %-25s | %-8s | %-11s" % (
        "slug", "folder photos", "skipped(dupe)", "skipped(already on site)",
        "appended", "final total"))
    for row in stats_rows:
        print("%-16s | %-13d | %-13d | %-25d | %-8d | %-11d" % (
            row["slug"], row["folder_photos"], row["skipped_dupe"],
            row["skipped_existing"], row["appended"], row["final_total"]))


def _print_not_in_folder(projects):
    print("Existing photos not in new folder:")
    project_list = projects["projects"] if isinstance(projects, dict) else projects
    by_slug = {}
    for project in project_list:
        by_slug[project["slug"]] = project
    for slug, folder_name in FOLDERS.items():
        project = by_slug.get(slug)
        if project is None:
            continue
        names = set(p.name for p in list_candidates(MEDIA_ROOT / folder_name))
        for photo in project.get("photos", []):
            src = str(photo.get("src", ""))
            if "/" in src:
                continue
            if src not in names:
                print("  %s: %s" % (slug, src))


def main():
    projects = load_canonical("projects.json")
    manifest = load_canonical("assets-manifest.json")

    if not MEDIA_ROOT.exists():
        print("ERROR: %s does not exist. Mount the volume and retry." % MEDIA_ROOT)
        sys.exit(1)

    plan_entries, stats_rows = plan_with_stats(projects, manifest)

    if not plan_entries:
        print("nothing to do")
    else:
        by_slug = {}
        for project in projects["projects"]:
            by_slug[project["slug"]] = project
        mprojects = manifest.setdefault("projects", {})
        for entry in plan_entries:
            slug = entry["slug"]
            project = by_slug[slug]
            for item in entry["items"]:
                idx = _index_str(item["index"])
                full_rel = "assets/img/%s/%s.jpg" % (slug, idx)
                thumb_rel = "assets/img/%s/thumbs/%s.jpg" % (slug, idx)
                print("photo  %s -> %s" % (item["rel_src"], full_rel), flush=True)
                process_image(item["src_path"], ROOT / full_rel, FULL_MAX_PX, FULL_Q)
                process_image(item["src_path"], ROOT / thumb_rel, THUMB_MAX_PX, THUMB_Q)
                project.setdefault("photos", []).append({
                    "src": item["rel_src"], "alt": item["alt"]})
                mentry = mprojects.setdefault(slug, {"photos": [], "videos": []})
                mentry.setdefault("photos", []).append({
                    "full": full_rel, "thumb": thumb_rel, "alt": item["alt"]})

    _print_table(stats_rows)
    _print_not_in_folder(projects)

    if not plan_entries:
        return

    (ROOT / "projects.json").write_text(
        json.dumps(projects, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (ROOT / "assets-manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print("INGEST_OK")


if __name__ == "__main__":
    main()
