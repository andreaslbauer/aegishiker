"""Read-only bridge to AegisPhotoVoyager's SQLite database (voyager.db / photos.db).

Used by the "I Hiked This Trail" feature to discover geotagged photos taken along a
traced hike trail, enriched with AegisPhotoVoyager's AI vision summaries, tags, mood,
and identified people (faces/persons).
"""
import json
import re
import logging
import os
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from src.hiking.geo import compute_bbox, expand_bbox_meters
from src.hiking.models import HikePhotoPin
from src.hiking.hike_info import is_placeholder_person  # noqa: F401  (re-exported)
from src.hiking.photo_matcher import match_photos_to_trail

from . import settings

logger = logging.getLogger(__name__)




def find_voyager_db(configured_path: Optional[str] = None) -> Optional[str]:
    """Locate AegisPhotoVoyager's SQLite database across standard, Snap, and QSettings paths.

    Priority order:
    1. Explicit `configured_path` argument or `AEGIS_VOYAGER_DB` environment variable
    2. User-configured `voyager_db_path` in `~/.aegishiker/settings.json`
    3. Standard Voyager data dir: `~/.AegisPhotoVoyager/voyager.db` or `photos.db`
    4. Snap confinement paths: `$SNAP_USER_COMMON/.AegisPhotoVoyager/voyager.db` and
       `~/snap/aegisphotovoyager/common/.AegisPhotoVoyager/voyager.db`
    5. `QSettings("Aegis", "PhotoVoyager")` probe (`app_data_dir`)
    6. Legacy XDG paths (`~/.local/share/AegisPhotoVoyager/voyager.db` or `photos.db`)
    """
    candidates: List[Path] = []

    if configured_path and configured_path.strip():
        candidates.append(Path(configured_path.strip()).expanduser())

    env_path = os.environ.get("AEGIS_VOYAGER_DB", "").strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    try:
        saved = str(settings.get("voyager_db_path") or "").strip()
        if saved:
            candidates.append(Path(saved).expanduser())
    except Exception:
        pass

    home = Path.home()
    candidates.extend(
        [
            home / ".AegisPhotoVoyager" / "voyager.db",
            home / ".AegisPhotoVoyager" / "photos.db",
        ]
    )

    snap_common = os.environ.get("SNAP_USER_COMMON", "").strip()
    if snap_common:
        candidates.extend(
            [
                Path(snap_common) / ".AegisPhotoVoyager" / "voyager.db",
                Path(snap_common) / ".AegisPhotoVoyager" / "photos.db",
            ]
        )

    candidates.extend(
        [
            home / "snap" / "aegisphotovoyager" / "common" / ".AegisPhotoVoyager" / "voyager.db",
            home / "snap" / "aegisphotovoyager" / "common" / ".AegisPhotoVoyager" / "photos.db",
            home / "snap" / "aegisphotovoyager" / "current" / ".AegisPhotoVoyager" / "voyager.db",
        ]
    )

    # Probe Voyager QSettings if PySide6 is available
    try:
        from PySide6.QtCore import QSettings

        qs = QSettings("Aegis", "PhotoVoyager")
        app_data = str(qs.value("app_data_dir", "") or "").strip()
        if app_data:
            for name in ("voyager.db", "photos.db"):
                candidates.append(Path(app_data) / name)
    except Exception:
        pass

    candidates.extend(
        [
            home / ".local" / "share" / "AegisPhotoVoyager" / "voyager.db",
            home / ".local" / "share" / "AegisPhotoVoyager" / "photos.db",
            home / "Library" / "Application Support" / "AegisPhotoVoyager" / "voyager.db",
        ]
    )

    for cand in candidates:
        try:
            if cand.exists() and cand.is_file() and cand.stat().st_size > 0:
                return str(cand.resolve())
        except OSError:
            continue

    return None


class VoyagerBridge:
    """Read-only interface to AegisPhotoVoyager's SQLite database and thumbnail cache."""

    def __init__(self, db_path: Optional[str] = None):
        self._explicit_db_path = db_path or None
        self.db_path = find_voyager_db(db_path)
        self.thumbnail_dir: Optional[Path] = (
            Path(self.db_path).parent / "thumbnails" if self.db_path else None
        )

    @property
    def explicit_db_path(self) -> Optional[str]:
        return self._explicit_db_path

    @explicit_db_path.setter
    def explicit_db_path(self, val: Optional[str]) -> None:
        self._explicit_db_path = val
        self.db_path = find_voyager_db(val)
        self.thumbnail_dir = Path(self.db_path).parent / "thumbnails" if self.db_path else None

    def discover_db_path(self) -> Optional[Path]:
        found = find_voyager_db(self._explicit_db_path)
        if found:
            self.db_path = found
            self.thumbnail_dir = Path(found).parent / "thumbnails"
            return Path(found)
        return None

    def is_available(self) -> bool:
        if not self.db_path or not Path(self.db_path).exists():
            return False
        try:
            with self._conn() as conn:
                conn.execute("SELECT 1 FROM photos LIMIT 1")
            return True
        except Exception:
            return False

    def _conn(self) -> sqlite3.Connection:
        if not self.db_path:
            raise FileNotFoundError("AegisPhotoVoyager database path is not configured or found.")
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def get_thumb_path(self, voyager_photo_id: Optional[int]) -> Optional[str]:
        if voyager_photo_id is None or self.thumbnail_dir is None:
            return None
        p = self.thumbnail_dir / f"{voyager_photo_id}_300.jpg"
        return str(p) if p.exists() else None

    def count_geotagged_photos(self) -> int:
        if not self.is_available():
            return 0
        try:
            with self._conn() as conn:
                row = conn.execute(
                    """
                    SELECT COUNT(*) AS n
                    FROM photos
                    WHERE is_deleted = 0
                      AND lat IS NOT NULL AND lon IS NOT NULL
                    """
                ).fetchone()
                return int(row["n"]) if row else 0
        except Exception:
            return 0

    def _table_exists(self, conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        return row is not None

    def fetch_photos_in_bbox(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        since_ts: Optional[int] = None,
        until_ts: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Query geotagged photos inside the bounding box, joining AI analysis and face names."""
        if not self.is_available():
            return []

        try:
            with self._conn() as conn:
                has_ai = self._table_exists(conn, "ai_analysis")
                has_faces = self._table_exists(conn, "faces") and self._table_exists(conn, "persons")

                clauses = [
                    "p.is_deleted = 0",
                    "(p.is_hidden IS NULL OR p.is_hidden = 0)",
                    "p.lat IS NOT NULL",
                    "p.lon IS NOT NULL",
                    "p.lat >= ?",
                    "p.lat <= ?",
                    "p.lon >= ?",
                    "p.lon <= ?",
                ]
                params: List[Any] = [min_lat, max_lat, min_lon, max_lon]

                if since_ts is not None:
                    clauses.append("p.date_taken >= ?")
                    params.append(since_ts)
                if until_ts is not None:
                    clauses.append("p.date_taken <= ?")
                    params.append(until_ts)

                where_sql = " AND ".join(clauses)
                if has_ai:
                    sql = f"""
                        SELECT
                            p.id AS voyager_photo_id,
                            p.full_path AS photo_path,
                            p.name AS filename,
                            p.date_taken,
                            p.camera_model,
                            p.lat,
                            p.lon,
                            p.is_starred,
                            a.summary AS ai_summary,
                            a.tags_json,
                            a.mood
                        FROM photos p
                        LEFT JOIN ai_analysis a ON a.photo_id = p.id
                        WHERE {where_sql}
                        ORDER BY p.date_taken ASC
                    """
                else:
                    sql = f"""
                        SELECT
                            p.id AS voyager_photo_id,
                            p.full_path AS photo_path,
                            p.name AS filename,
                            p.date_taken,
                            p.camera_model,
                            p.lat,
                            p.lon,
                            p.is_starred,
                            '' AS ai_summary,
                            '[]' AS tags_json,
                            '' AS mood
                        FROM photos p
                        WHERE {where_sql}
                        ORDER BY p.date_taken ASC
                    """

                rows = conn.execute(sql, params).fetchall()
                results: List[Dict[str, Any]] = []
                photo_ids: List[int] = []
                for r in rows:
                    d = dict(r)
                    pid = int(d["voyager_photo_id"])
                    photo_ids.append(pid)
                    try:
                        d["tags"] = json.loads(d.get("tags_json") or "[]")
                    except Exception:
                        d["tags"] = []
                    d["thumb_path"] = self.get_thumb_path(pid)
                    d["people"] = []
                    results.append(d)

                if has_faces and photo_ids:
                    people_map = self._fetch_people_map(conn, photo_ids)
                    for d in results:
                        d["people"] = people_map.get(int(d["voyager_photo_id"]), [])

                return results
        except Exception as e:
            logger.error(f"VoyagerBridge.fetch_photos_in_bbox failed: {e}")
            return []

    def _fetch_people_map(
        self,
        conn: sqlite3.Connection,
        photo_ids: List[int],
    ) -> Dict[int, List[str]]:
        """Named people per photo. Faces of merged persons resolve to the person they were merged
        into; hidden persons and Voyager's auto-generated placeholders ("Person 1234") are skipped."""
        out: Dict[int, List[str]] = {}
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(persons)").fetchall()}
        merged = "is_merged_into" in cols
        hidden = "is_hidden" in cols
        name_sql = "COALESCE(m.name, pr.name)" if merged else "pr.name"
        join_sql = "LEFT JOIN persons m ON m.id = pr.is_merged_into" if merged else ""
        hidden_sql = "AND (pr.is_hidden IS NULL OR pr.is_hidden = 0)" if hidden else ""
        chunk_size = 450
        for start in range(0, len(photo_ids), chunk_size):
            chunk = photo_ids[start : start + chunk_size]
            placeholders = ",".join("?" * len(chunk))
            sql = f"""
                SELECT f.photo_id, {name_sql} AS name
                FROM faces f
                JOIN persons pr ON pr.id = f.person_id
                {join_sql}
                WHERE f.photo_id IN ({placeholders})
                  AND (f.is_ignored IS NULL OR f.is_ignored = 0)
                  AND f.person_id IS NOT NULL
                  {hidden_sql}
            """
            for row in conn.execute(sql, chunk).fetchall():
                pid, name = int(row["photo_id"]), str(row["name"] or "").strip()
                if name and not is_placeholder_person(name):
                    out.setdefault(pid, [])
                    if name not in out[pid]:
                        out[pid].append(name)
        return out

    def people_for_photo_ids(self, photo_ids: List[int]) -> Dict[int, List[str]]:
        """Named people for Voyager photo ids (to refresh photos saved before names were known)."""
        if not photo_ids or not self.is_available():
            return {}
        try:
            with self._conn() as conn:
                if not (self._table_exists(conn, "faces") and self._table_exists(conn, "persons")):
                    return {}
                return self._fetch_people_map(conn, [int(i) for i in photo_ids])
        except Exception as e:
            logger.error(f"VoyagerBridge.people_for_photo_ids failed: {e}")
            return {}

    def find_photos_for_traced_trail(
        self,
        coords_3d: List[List[float]],
        corridor_m: float = 250.0,
        date_filter: Optional[str] = None,
        session_id: str = "",
    ) -> List[HikePhotoPin]:
        """Find all photos in AegisPhotoVoyager taken within `corridor_m` of a traced 3D trail."""
        if not coords_3d or not self.is_available():
            return []
        min_lat, max_lat, min_lon, max_lon = compute_bbox(coords_3d)
        b_min_lat, b_max_lat, b_min_lon, b_max_lon = expand_bbox_meters(
            min_lat, max_lat, min_lon, max_lon, buffer_m=max(corridor_m * 1.5, 500.0)
        )
        candidates = self.fetch_photos_in_bbox(b_min_lat, b_max_lat, b_min_lon, b_max_lon)
        return match_photos_to_trail(
            coords_3d,
            candidates,
            corridor_m=corridor_m,
            date_filter=date_filter,
            session_id=session_id,
        )

    def match_photos_for_route(
        self,
        route: Any,
        session_id: Optional[str] = None,
        max_corridor_m: float = 250.0,
        date_prefix: Optional[str] = None,
    ) -> List[HikePhotoPin]:
        coords_3d = getattr(route, "coords_3d", [])
        pins = self.find_photos_for_traced_trail(
            coords_3d=coords_3d,
            corridor_m=max_corridor_m,
            date_filter=date_prefix,
            session_id=session_id or "",
        )
        r_id = getattr(route, "id", "")
        for p in pins:
            p.route_id = r_id
        return pins

