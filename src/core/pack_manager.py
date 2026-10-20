"""Loads bundled starter regional trail packs (data/packs/*.json) and user-downloaded
regional packs (~/.aegishiker/packs/*.json) into the local SQLite database.
"""
import json
import logging
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple

from src.hiking.gear import generate_gear_checklist
from src.hiking.models import DestinationPack
from src.hiking.routing import route_from_trail_segment

from . import paths
from .database import Database

logger = logging.getLogger(__name__)


def load_destinations_catalog() -> List[Dict[str, Any]]:
    try:
        raw = json.loads(paths.DESTINATIONS_CATALOG_PATH.read_text(encoding="utf-8"))
        return list(raw.get("destinations") or [])
    except Exception as e:
        logger.warning(f"Could not read destinations catalog: {e}")
        return []


_HEADER_ID = re.compile(r'^\s*\{\s*"id"\s*:\s*"([^"]+)"')
_HEADER_VERSION = re.compile(r'"pack_version"\s*:\s*"([^"]*)"')


def _pack_header(json_file: Path) -> Optional[Tuple[str, str]]:
    """(id, pack_version) from the start of a pack file, without parsing its MBs of map data.
    Pack files are written with `id` first and `pack_version` before the trails."""
    with open(json_file, encoding="utf-8") as f:
        head = f.read(16384)
    m_id, m_ver = _HEADER_ID.search(head), _HEADER_VERSION.search(head)
    return (m_id.group(1), m_ver.group(1)) if m_id and m_ver else None


def seed_packs_into_db(db: Database) -> List[DestinationPack]:
    """Ensure all bundled regional packs and any user-downloaded packs are indexed in SQLite."""
    existing_versions = {p.id: p.pack_version for p in db.list_destination_packs()}
    seen_ids: set = set()

    pack_dirs = [paths.BUNDLED_PACKS_DIR, paths.PACKS_USER_DIR]
    for folder in pack_dirs:
        if not folder.exists():
            continue
        for json_file in sorted(folder.glob("*.json")):
            try:
                header = _pack_header(json_file)
                if header and (header[0] in seen_ids or existing_versions.get(header[0]) == header[1]):
                    seen_ids.add(header[0])
                    continue  # already imported at this version
                pack = DestinationPack.model_validate_json(json_file.read_text(encoding="utf-8"))
                if pack.id in seen_ids:
                    continue
                seen_ids.add(pack.id)
                # Re-import when the pack file version changed (e.g. map details were added)
                if existing_versions.get(pack.id) != pack.pack_version:
                    db.upsert_destination_pack(pack)
                    existing_versions[pack.id] = pack.pack_version
            except Exception as e:
                logger.error(f"Failed to load regional pack {json_file}: {e}")

    # Ensure at least one default composed route exists on first run
    if not db.list_routes():
        wm = db.get_destination_pack("white_mountains_nh")
        if wm and wm.trails:
            # Prefer Franconia Ridge Loop if present
            loop_seg = next(
                (t for t in wm.trails if "franconia_ridge_loop" in t.id),
                wm.trails[0],
            )
            default_route = route_from_trail_segment(loop_seg)
            default_route.gear_checklist = generate_gear_checklist(default_route)
            default_route.safety_card.ranger_dispatch = wm.emergency_contact
            db.save_route(default_route, label="Initial Route")

    return db.list_destination_packs()


ensure_starter_packs_loaded = seed_packs_into_db

