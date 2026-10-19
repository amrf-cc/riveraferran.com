#!/usr/bin/env python3
"""Rebuild every gallery thumbnail from its own full-size photo.

The ingest/prepare pipelines make 640px thumbnails, which are soft on retina
grids. This script re-encodes each thumbnail from the full photo at a higher
long edge (1280px by default) and quality 78, so grids stay sharp without
shipping the full-size originals into the grid.

It skips a photo whose thumbnail already has a long edge >= the target (unless
--force), which makes re-runs cheap and lets it upgrade only NEW uploads: the
content manager still writes 640px thumbs. Missing full files are reported and
do not stop the run. Run scripts/stamp_metadata.py afterwards to (re)stamp
captions/rights; build.py then re-hashes the URLs.

Usage:
    /usr/bin/python3 scripts/regen_thumbs.py [--root PATH] [--max-px 1280]
        [--quality 78] [--force] [--dry-run]
"""

import argparse
import json
import os
import sys
from io import BytesIO

try:
    from PIL import Image
except ImportError:  # environment dependent; main() reports and exits 2
    Image = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_MAX_PX = 1280
DEFAULT_QUALITY = 78


def _long_edge(size):
    return max(size[0], size[1])


def _target_size(width, height, target):
    """Long edge == target, never upscaling (returns the input size if smaller)."""
    long_edge = max(width, height)
    if long_edge <= target:
        return (width, height)
    scale = target / float(long_edge)
    new_width = max(1, int(round(width * scale)))
    new_height = max(1, int(round(height * scale)))
    return (new_width, new_height)


def _encode_jpeg(im, quality, icc):
    buf = BytesIO()
    kwargs = {"quality": quality, "optimize": True, "progressive": True}
    if icc:
        kwargs["icc_profile"] = icc
    im.save(buf, "JPEG", **kwargs)
    return buf.getvalue()


def _note_largest(stats, rel, nbytes):
    if nbytes > stats["largest_thumb_bytes"]:
        stats["largest_thumb_bytes"] = nbytes
        stats["largest_thumb"] = rel


def regen(root, max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY,
          force=False, dry_run=False):
    """Rebuild thumbnails under ``root``. Returns a stats dict.

    Reads ``<root>/assets-manifest.json`` and walks every project's photos.
    Writes nothing when ``dry_run`` is true.
    """
    if Image is None:
        raise RuntimeError("Pillow (PIL) is required to regenerate thumbnails")

    manifest_path = os.path.join(root, "assets-manifest.json")
    with open(manifest_path) as f:
        manifest = json.load(f)

    stats = {
        "regenerated": 0,
        "skipped": 0,
        "missing": 0,
        "bytes_before": 0,
        "bytes_after": 0,
        "largest_thumb": None,
        "largest_thumb_bytes": 0,
        "changes": [],
    }

    for entry in manifest.get("projects", {}).values():
        for photo in entry.get("photos", []):
            full_rel = photo["full"]
            thumb_rel = photo["thumb"]
            full = os.path.join(root, full_rel)
            thumb = os.path.join(root, thumb_rel)

            if not os.path.exists(full):
                stats["missing"] += 1
                print("WARNING: missing full photo %s" % full_rel)
                continue

            with Image.open(full) as im:
                src = im.convert("RGB")
                icc = im.info.get("icc_profile")
            target = min(max_px, _long_edge(src.size))

            before = os.path.getsize(thumb) if os.path.exists(thumb) else 0
            stats["bytes_before"] += before

            if not force and os.path.exists(thumb):
                try:
                    with Image.open(thumb) as t:
                        thumb_edge = _long_edge(t.size)
                except Exception:
                    thumb_edge = 0
                if thumb_edge >= target:
                    stats["skipped"] += 1
                    stats["bytes_after"] += before
                    _note_largest(stats, thumb_rel, before)
                    continue

            new_size = _target_size(src.size[0], src.size[1], target)
            out = src if new_size == src.size else src.resize(
                new_size, Image.LANCZOS)
            data = _encode_jpeg(out, quality, icc)

            if dry_run:
                print("would regenerate %s (%d->%d px, %d bytes)"
                      % (thumb_rel, _long_edge(src.size), target, len(data)))
            else:
                os.makedirs(os.path.dirname(thumb), exist_ok=True)
                tmp = thumb + ".tmp"
                with open(tmp, "wb") as f:
                    f.write(data)
                os.replace(tmp, thumb)
                print("regenerated %s (%d->%d px, %d bytes)"
                      % (thumb_rel, _long_edge(src.size), target, len(data)))

            stats["regenerated"] += 1
            stats["bytes_after"] += len(data)
            stats["changes"].append(thumb_rel)
            _note_largest(stats, thumb_rel, len(data))

    return stats


def format_summary(stats):
    largest = "-"
    if stats["largest_thumb"]:
        largest = "%s %d" % (stats["largest_thumb"], stats["largest_thumb_bytes"])
    return ("regenerated=%d skipped=%d missing=%d  bytes_before=%d "
            "bytes_after=%d  largest_thumb=%s"
            % (stats["regenerated"], stats["skipped"], stats["missing"],
               stats["bytes_before"], stats["bytes_after"], largest))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Rebuild gallery thumbnails from the full-size photos.")
    parser.add_argument("--root", default=ROOT,
                        help="repo root (parent of scripts/); default: %(default)s")
    parser.add_argument("--max-px", type=int, default=DEFAULT_MAX_PX,
                        help="maximum long edge in pixels (default: %(default)s)")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY,
                        help="JPEG quality (default: %(default)s)")
    parser.add_argument("--force", action="store_true",
                        help="regenerate even when the thumb is already big enough")
    parser.add_argument("--dry-run", action="store_true", dest="dry_run",
                        help="print what would change, write nothing")
    args = parser.parse_args(argv)

    if Image is None:
        sys.stderr.write("ERROR: Pillow (PIL) is required. Install it with "
                         "'/usr/bin/python3 -m pip install Pillow'.\n")
        return 2

    stats = regen(args.root, max_px=args.max_px, quality=args.quality,
                  force=args.force, dry_run=args.dry_run)
    print(format_summary(stats))
    return 1 if stats["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
