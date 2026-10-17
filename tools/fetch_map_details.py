#!/usr/bin/env python3
"""Download real map detail (terrain, contours, OpenStreetMap features) into destination packs.

  .venv/bin/python tools/fetch_map_details.py                  # all bundled packs in data/packs/
  .venv/bin/python tools/fetch_map_details.py white_mountains_nh zion_canyon
  .venv/bin/python tools/fetch_map_details.py --pack-file ~/.aegishiker/packs/custom_x.json

Bumps each enriched pack's `pack_version` so existing installs re-import it on next launch.
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.core.map_details import enrich_pack  # noqa: E402
from src.hiking.models import DestinationPack  # noqa: E402

PACKS_DIR = REPO_ROOT / "data" / "packs"
DETAIL_VERSION_SUFFIX = "-map3"
TRAIL_SPECS = REPO_ROOT / "data" / "osm_trails.json"
CATALOG = REPO_ROOT / "data" / "destinations_catalog.json"


def write_pack(pack: DestinationPack, target: Path) -> None:
    # Compact JSON: indenting thousands of coordinate pairs would multiply the file size
    target.write_text(pack.model_dump_json(exclude_defaults=False) + "\n", encoding="utf-8")


def update_catalog(pack: DestinationPack) -> None:
    if not CATALOG.exists():
        return
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    for entry in catalog.get("destinations", []):
        if entry.get("id") == pack.id:
            entry["trail_count"] = len(pack.trails)
            entry["poi_count"] = len(pack.pois)
    CATALOG.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("pack_ids", nargs="*", help="bundled pack ids (default: all)")
    ap.add_argument("--pack-file", action="append", default=[], help="enrich an arbitrary pack JSON file")
    ap.add_argument("--no-osm", action="store_true", help="terrain/contours only")
    ap.add_argument("--contour-units", choices=("ft", "m"), default="ft", help="round contour interval in feet or metres")
    args = ap.parse_args()

    files = [Path(p).expanduser() for p in args.pack_file]
    if args.pack_ids:
        files += [PACKS_DIR / f"{pid}.json" for pid in args.pack_ids]
    elif not files:
        files = sorted(PACKS_DIR.glob("*.json"))

    specs = json.loads(TRAIL_SPECS.read_text(encoding="utf-8")) if TRAIL_SPECS.exists() else {}
    failures = 0
    for i, f in enumerate(files):
        pack = DestinationPack.model_validate_json(f.read_text(encoding="utf-8"))
        print(f"[{pack.id}] {pack.name}")
        t0 = time.time()
        try:
            enrich_pack(pack, progress=lambda m: print(f"    {m}", flush=True), fetch_osm=not args.no_osm,
                        contour_units=args.contour_units, trail_specs=specs.get(pack.id))
        except Exception as e:
            print(f"    FAILED: {e}")
            failures += 1
            continue
        base = re.sub(r"(-map\d+)+$", "", pack.pack_version)
        pack.pack_version = base + DETAIL_VERSION_SUFFIX
        write_pack(pack, f)
        update_catalog(pack)
        kinds: dict = {}
        for feat in pack.features:
            kinds[feat.kind] = kinds.get(feat.kind, 0) + 1
        print(
            f"    {len(pack.contours)} contour lines @ {pack.contour_interval_m:.1f} m, "
            f"DEM {pack.dem.cols}x{pack.dem.rows}, {len(pack.features)} features "
            f"({', '.join(f'{k} {v}' for k, v in sorted(kinds.items()))}) → "
            f"{f.stat().st_size / 1e6:.1f} MB in {time.time() - t0:.0f}s"
        )
        if i < len(files) - 1:
            time.sleep(5)  # be polite to the public Overpass instance
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
