"""SQLite WAL database layer for AegisHiker.

Stores regional destination packs, spatial bounding-box indexed trail segments, FTS5 full-text
search index, POIs, composed routes with coalesced revision history, recorded/traced hike sessions,
AegisPhotoVoyager matched photo pins, GPS/barometric telemetry points, AI brainstorm briefs,
and chat history.
"""
from datetime import datetime
import json
import logging
from pathlib import Path
import sqlite3
import threading
from typing import Any, Dict, List, Optional, Tuple

from src.hiking.photo_matcher import dedupe_pins, photo_keys
from src.hiking.models import (
    ComposedRoute,
    ContourLine,
    DEMGrid,
    MapFeature,
    DestinationPack,
    HikeBrief,
    HikePhotoPin,
    HikeSession,
    HikeStats,
    POI,
    TelemetryPoint,
    TrailSegment,
    now_iso,
)

logger = logging.getLogger(__name__)

REVISION_COALESCE_SECONDS = 120


class Database:
    def __init__(self, db_path: Optional[Path | str] = None):
        if db_path is None:
            from . import paths

            paths.ensure_app_dirs()
            db_path = paths.profile_db_path()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._map_cache: Dict[str, Any] = {}  # pack_id -> (raw map JSON, parsed objects)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._create_tables()

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    def _create_tables(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS destination_pack (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                state_or_region TEXT NOT NULL,
                agency TEXT DEFAULT '',
                license TEXT NOT NULL,
                summary TEXT DEFAULT '',
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                base_ele_m REAL DEFAULT 0.0,
                max_ele_m REAL DEFAULT 0.0,
                emergency_contact TEXT DEFAULT '911',
                pack_version TEXT DEFAULT '1.0',
                installed_at TEXT NOT NULL,
                data JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trail_segment (
                id TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL REFERENCES destination_pack(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                code TEXT DEFAULT '',
                difficulty TEXT NOT NULL,
                surface TEXT NOT NULL,
                trail_type TEXT NOT NULL,
                distance_m REAL NOT NULL,
                elevation_gain_m REAL NOT NULL,
                elevation_loss_m REAL NOT NULL,
                min_ele_m REAL NOT NULL,
                max_ele_m REAL NOT NULL,
                max_grade_pct REAL NOT NULL,
                dogs_allowed INTEGER DEFAULT 1,
                water_available INTEGER DEFAULT 0,
                condition_status TEXT DEFAULT 'open',
                condition_note TEXT DEFAULT '',
                condition_updated TEXT DEFAULT '',
                min_lat REAL NOT NULL,
                max_lat REAL NOT NULL,
                min_lon REAL NOT NULL,
                max_lon REAL NOT NULL,
                coords_3d JSON NOT NULL,
                tags JSON DEFAULT '[]',
                description TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_trail_pack ON trail_segment(pack_id);
            CREATE INDEX IF NOT EXISTS idx_trail_bbox ON trail_segment(min_lat, max_lat, min_lon, max_lon);

            CREATE VIRTUAL TABLE IF NOT EXISTS trail_fts USING fts5(
                trail_id UNINDEXED,
                pack_id UNINDEXED,
                name,
                description,
                tags,
                condition_note
            );

            CREATE TABLE IF NOT EXISTS poi (
                id TEXT PRIMARY KEY,
                pack_id TEXT NOT NULL REFERENCES destination_pack(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                ele_m REAL NOT NULL,
                note TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_poi_pack ON poi(pack_id);

            CREATE TABLE IF NOT EXISTS composed_route (
                id TEXT PRIMARY KEY,
                pack_id TEXT,
                name TEXT NOT NULL,
                status TEXT DEFAULT 'planned',
                rating INTEGER DEFAULT 0,
                tags TEXT DEFAULT '',
                created TEXT NOT NULL,
                modified TEXT NOT NULL,
                parent_id TEXT,
                notes TEXT DEFAULT '',
                data JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS composed_route_revision (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id TEXT NOT NULL REFERENCES composed_route(id) ON DELETE CASCADE,
                created TEXT NOT NULL,
                label TEXT NOT NULL,
                data JSON NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hike_session (
                id TEXT PRIMARY KEY,
                pack_id TEXT,
                route_id TEXT REFERENCES composed_route(id) ON DELETE SET NULL,
                route_snapshot JSON,
                title TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'completed',
                device_source TEXT DEFAULT 'phone',
                stats JSON NOT NULL,
                conditions_logged JSON DEFAULT '{}',
                interactive_map_path TEXT DEFAULT '',
                interactive_map_data JSON,
                notes TEXT DEFAULT '',
                created TEXT NOT NULL,
                modified TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS hike_photo_pin (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL REFERENCES hike_session(id) ON DELETE CASCADE,
                voyager_photo_id INTEGER,
                photo_path TEXT NOT NULL,
                thumb_path TEXT,
                filename TEXT NOT NULL,
                date_taken INTEGER,
                date_iso TEXT DEFAULT '',
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                ele_m REAL NOT NULL,
                distance_along_m REAL NOT NULL,
                cross_track_m REAL DEFAULT 0.0,
                camera_model TEXT DEFAULT '',
                is_starred INTEGER DEFAULT 0,
                ai_summary TEXT DEFAULT '',
                ai_tags JSON DEFAULT '[]',
                ai_mood TEXT DEFAULT '',
                people JSON DEFAULT '[]',
                caption TEXT DEFAULT ''
            );

            CREATE INDEX IF NOT EXISTS idx_photo_pin_session ON hike_photo_pin(session_id);

            -- Photos the user removed from a hike: a rescan of AegisPhotoVoyager must not add them back
            CREATE TABLE IF NOT EXISTS hike_photo_excluded (
                session_id TEXT NOT NULL REFERENCES hike_session(id) ON DELETE CASCADE,
                photo_key TEXT NOT NULL,
                PRIMARY KEY (session_id, photo_key)
            );

            CREATE TABLE IF NOT EXISTS telemetry_point (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL REFERENCES hike_session(id) ON DELETE CASCADE,
                seq INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                gps_ele_m REAL NOT NULL,
                baro_ele_m REAL,
                horizontal_accuracy_m REAL DEFAULT 5.0,
                speed_mps REAL DEFAULT 0.0,
                grade_pct REAL DEFAULT 0.0
            );

            CREATE INDEX IF NOT EXISTS idx_telemetry_session ON telemetry_point(session_id, seq);

            CREATE TABLE IF NOT EXISTS waypoint_note (
                id TEXT PRIMARY KEY,
                session_id TEXT REFERENCES hike_session(id) ON DELETE CASCADE,
                route_id TEXT REFERENCES composed_route(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                kind TEXT DEFAULT 'note',
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                ele_m REAL NOT NULL,
                created_at TEXT NOT NULL,
                photo_filename TEXT,
                note TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS hike_brief (
                id TEXT PRIMARY KEY,
                route_id TEXT,
                data JSON NOT NULL,
                created TEXT NOT NULL,
                modified TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chat_message (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                route_id TEXT,
                channel TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created TEXT NOT NULL
            );
            """
        )
        self._conn.commit()

    # ── Regional Destination Packs & Trails ────────────────────────────────────

    def upsert_destination_pack(self, pack: DestinationPack) -> None:
        with self._lock:
            extra_data = {
                "contours": [c.model_dump() for c in pack.contours],
                "dem": pack.dem.model_dump() if pack.dem else None,
                "contour_interval_m": pack.contour_interval_m,
                "features": [f.model_dump(exclude_defaults=True) for f in pack.features],
                "map_attribution": pack.map_attribution,
            }
            self._conn.execute(
                """
                INSERT INTO destination_pack (
                    id, name, state_or_region, agency, license, summary,
                    min_lat, max_lat, min_lon, max_lon, base_ele_m, max_ele_m,
                    emergency_contact, pack_version, installed_at, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    state_or_region=excluded.state_or_region,
                    agency=excluded.agency,
                    license=excluded.license,
                    summary=excluded.summary,
                    min_lat=excluded.min_lat,
                    max_lat=excluded.max_lat,
                    min_lon=excluded.min_lon,
                    max_lon=excluded.max_lon,
                    base_ele_m=excluded.base_ele_m,
                    max_ele_m=excluded.max_ele_m,
                    emergency_contact=excluded.emergency_contact,
                    pack_version=excluded.pack_version,
                    data=excluded.data
                """,
                (
                    pack.id,
                    pack.name,
                    pack.state_or_region,
                    pack.agency,
                    pack.license,
                    pack.summary,
                    pack.min_lat,
                    pack.max_lat,
                    pack.min_lon,
                    pack.max_lon,
                    pack.base_ele_m,
                    pack.max_ele_m,
                    pack.emergency_contact,
                    pack.pack_version,
                    pack.installed_at,
                    json.dumps(extra_data),
                ),
            )

            # Preserve any user-edited condition flags before re-inserting trail rows
            existing_conditions: Dict[str, tuple] = {}
            for r in self._conn.execute(
                "SELECT id, condition_status, condition_note, condition_updated FROM trail_segment WHERE pack_id=?",
                (pack.id,),
            ).fetchall():
                existing_conditions[r["id"]] = (
                    r["condition_status"],
                    r["condition_note"],
                    r["condition_updated"],
                )

            self._conn.execute("DELETE FROM trail_segment WHERE pack_id=?", (pack.id,))
            self._conn.execute("DELETE FROM trail_fts WHERE pack_id=?", (pack.id,))
            self._conn.execute("DELETE FROM poi WHERE pack_id=?", (pack.id,))

            for t in pack.trails:
                cond_status, cond_note, cond_updated = existing_conditions.get(
                    t.id, (t.condition_status, t.condition_note, t.condition_updated)
                )
                self._conn.execute(
                    """
                    INSERT INTO trail_segment (
                        id, pack_id, name, code, difficulty, surface, trail_type,
                        distance_m, elevation_gain_m, elevation_loss_m, min_ele_m, max_ele_m,
                        max_grade_pct, dogs_allowed, water_available,
                        condition_status, condition_note, condition_updated,
                        min_lat, max_lat, min_lon, max_lon, coords_3d, tags, description
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.id,
                        pack.id,
                        t.name,
                        t.code,
                        t.difficulty,
                        t.surface,
                        t.trail_type,
                        t.distance_m,
                        t.elevation_gain_m,
                        t.elevation_loss_m,
                        t.min_ele_m,
                        t.max_ele_m,
                        t.max_grade_pct,
                        1 if t.dogs_allowed else 0,
                        1 if t.water_available else 0,
                        cond_status,
                        cond_note,
                        cond_updated,
                        t.min_lat,
                        t.max_lat,
                        t.min_lon,
                        t.max_lon,
                        json.dumps(t.coords_3d),
                        json.dumps(t.tags),
                        t.description,
                    ),
                )
                self._conn.execute(
                    "INSERT INTO trail_fts (trail_id, pack_id, name, description, tags, condition_note) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        t.id,
                        pack.id,
                        t.name,
                        t.description,
                        " ".join(t.tags),
                        cond_note,
                    ),
                )

            for p in pack.pois:
                self._conn.execute(
                    "INSERT INTO poi (id, pack_id, name, kind, lat, lon, ele_m, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (p.id, pack.id, p.name, p.kind, p.lat, p.lon, p.ele_m, p.note),
                )

            self._conn.commit()

    def _parsed_map_data(self, pack_id: str, raw: str):
        """Parse a pack's multi-MB map JSON (contours, DEM, features) once per stored version.
        Every region dropdown loads all packs, so re-parsing on each call made switching slow.
        The parsed objects are shared between callers and must be treated as read-only."""
        cached = self._map_cache.get(pack_id)
        if cached is not None and cached[0] == raw:
            return cached[1]
        extra = json.loads(raw)
        parsed = (
            [ContourLine.model_validate(c) for c in (extra.get("contours") or [])],
            DEMGrid.model_validate(extra["dem"]) if extra.get("dem") else None,
            [MapFeature.model_validate(f) for f in (extra.get("features") or [])],
            {k: extra.get(k) for k in ("contour_interval_m", "map_attribution")},
        )
        self._map_cache[pack_id] = (raw, parsed)
        return parsed

    def list_destination_packs(self) -> List[DestinationPack]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id FROM destination_pack ORDER BY name ASC"
            ).fetchall()
            return [p for r in rows if (p := self.get_destination_pack(r["id"])) is not None]

    def get_destination_pack(self, pack_id: str) -> Optional[DestinationPack]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM destination_pack WHERE id=?", (pack_id,)
            ).fetchone()
            if not row:
                return None
            contours, dem, features, extra = self._parsed_map_data(pack_id, row["data"] or "{}")
            trails = self.list_trails(pack_id)
            pois = self.list_pois(pack_id)
            return DestinationPack(
                id=row["id"],
                name=row["name"],
                state_or_region=row["state_or_region"],
                agency=row["agency"],
                license=row["license"],
                summary=row["summary"],
                min_lat=row["min_lat"],
                max_lat=row["max_lat"],
                min_lon=row["min_lon"],
                max_lon=row["max_lon"],
                base_ele_m=row["base_ele_m"],
                max_ele_m=row["max_ele_m"],
                emergency_contact=row["emergency_contact"],
                pack_version=row["pack_version"],
                installed_at=row["installed_at"],
                trails=trails,
                pois=pois,
                contours=contours,
                contour_interval_m=float(extra.get("contour_interval_m") or 0.0),
                dem=dem,
                features=features,
                map_attribution=str(extra.get("map_attribution") or ""),
            )

    def _row_to_trail(self, r: sqlite3.Row) -> TrailSegment:
        return TrailSegment(
            id=r["id"],
            pack_id=r["pack_id"],
            name=r["name"],
            code=r["code"],
            difficulty=r["difficulty"],
            surface=r["surface"],
            trail_type=r["trail_type"],
            distance_m=r["distance_m"],
            elevation_gain_m=r["elevation_gain_m"],
            elevation_loss_m=r["elevation_loss_m"],
            min_ele_m=r["min_ele_m"],
            max_ele_m=r["max_ele_m"],
            max_grade_pct=r["max_grade_pct"],
            dogs_allowed=bool(r["dogs_allowed"]),
            water_available=bool(r["water_available"]),
            condition_status=r["condition_status"],
            condition_note=r["condition_note"],
            condition_updated=r["condition_updated"],
            min_lat=r["min_lat"],
            max_lat=r["max_lat"],
            min_lon=r["min_lon"],
            max_lon=r["max_lon"],
            coords_3d=json.loads(r["coords_3d"] or "[]"),
            tags=json.loads(r["tags"] or "[]"),
            description=r["description"],
        )

    def list_trails(self, pack_id: Optional[str] = None) -> List[TrailSegment]:
        with self._lock:
            if pack_id:
                rows = self._conn.execute(
                    "SELECT * FROM trail_segment WHERE pack_id=? ORDER BY name ASC",
                    (pack_id,),
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM trail_segment ORDER BY name ASC").fetchall()
            return [self._row_to_trail(r) for r in rows]

    def search_trails_fts(
        self,
        query: str,
        pack_id: Optional[str] = None,
    ) -> List[TrailSegment]:
        """Full-Text Search across trail names, descriptions, tags, and condition notes."""
        clean = " ".join(
            f'"{tok}"*' for tok in query.replace('"', " ").split() if tok.strip()
        )
        if not clean:
            return self.list_trails(pack_id)
        with self._lock:
            try:
                if pack_id:
                    rows = self._conn.execute(
                        """
                        SELECT t.* FROM trail_fts f
                        JOIN trail_segment t ON t.id = f.trail_id
                        WHERE trail_fts MATCH ? AND f.pack_id = ?
                        ORDER BY rank
                        """,
                        (clean, pack_id),
                    ).fetchall()
                else:
                    rows = self._conn.execute(
                        """
                        SELECT t.* FROM trail_fts f
                        JOIN trail_segment t ON t.id = f.trail_id
                        WHERE trail_fts MATCH ?
                        ORDER BY rank
                        """,
                        (clean,),
                    ).fetchall()
                return [self._row_to_trail(r) for r in rows]
            except sqlite3.OperationalError:
                return [
                    t
                    for t in self.list_trails(pack_id)
                    if query.lower() in (t.name + " " + t.description).lower()
                ]

    def query_trails_in_bbox(
        self,
        min_lat: float,
        max_lat: float,
        min_lon: float,
        max_lon: float,
        pack_id: Optional[str] = None,
    ) -> List[TrailSegment]:
        """Spatial bounding-box intersection query against indexed coordinates."""
        with self._lock:
            sql = """
                SELECT * FROM trail_segment
                WHERE max_lat >= ? AND min_lat <= ?
                  AND max_lon >= ? AND min_lon <= ?
            """
            params: List[Any] = [min_lat, max_lat, min_lon, max_lon]
            if pack_id:
                sql += " AND pack_id = ?"
                params.append(pack_id)
            sql += " ORDER BY name ASC"
            rows = self._conn.execute(sql, params).fetchall()
            return [self._row_to_trail(r) for r in rows]

    def update_trail_condition(
        self,
        trail_id: str,
        condition_status: str,
        condition_note: str = "",
    ) -> None:
        with self._lock:
            ts = now_iso()[:10]
            self._conn.execute(
                """
                UPDATE trail_segment
                SET condition_status=?, condition_note=?, condition_updated=?
                WHERE id=?
                """,
                (condition_status, condition_note, ts, trail_id),
            )
            self._conn.execute(
                "UPDATE trail_fts SET condition_note=? WHERE trail_id=?",
                (condition_note, trail_id),
            )
            self._conn.commit()

    def list_pois(self, pack_id: Optional[str] = None) -> List[POI]:
        with self._lock:
            if pack_id:
                rows = self._conn.execute(
                    "SELECT * FROM poi WHERE pack_id=? ORDER BY name ASC", (pack_id,)
                ).fetchall()
            else:
                rows = self._conn.execute("SELECT * FROM poi ORDER BY name ASC").fetchall()
            return [
                POI(
                    id=r["id"],
                    pack_id=r["pack_id"],
                    name=r["name"],
                    kind=r["kind"],
                    lat=r["lat"],
                    lon=r["lon"],
                    ele_m=r["ele_m"],
                    note=r["note"],
                )
                for r in rows
            ]

    # ── Composed Routes & Revision Coalescing ──────────────────────────────────

    def save_route(self, route: ComposedRoute, label: str = "Autosave") -> ComposedRoute:
        with self._lock:
            now = now_iso()
            route.modified = now
            payload = json.dumps(route.model_dump())
            self._conn.execute(
                """
                INSERT INTO composed_route (
                    id, pack_id, name, status, rating, tags, created, modified, parent_id, notes, data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    pack_id=excluded.pack_id,
                    name=excluded.name,
                    status=excluded.status,
                    rating=excluded.rating,
                    tags=excluded.tags,
                    modified=excluded.modified,
                    parent_id=excluded.parent_id,
                    notes=excluded.notes,
                    data=excluded.data
                """,
                (
                    route.id,
                    route.pack_id,
                    route.name,
                    route.status,
                    route.rating,
                    ",".join(route.tags),
                    route.created,
                    route.modified,
                    route.parent_id,
                    route.notes,
                    payload,
                ),
            )

            # 2-minute autosave revision coalescing
            last_rev = self._conn.execute(
                "SELECT id, created, label FROM composed_route_revision WHERE route_id=? ORDER BY id DESC LIMIT 1",
                (route.id,),
            ).fetchone()
            coalesce = False
            if last_rev and label == "Autosave" and last_rev["label"] == "Autosave":
                try:
                    t_prev = datetime.fromisoformat(last_rev["created"])
                    t_now = datetime.fromisoformat(now)
                    if (t_now - t_prev).total_seconds() < REVISION_COALESCE_SECONDS:
                        coalesce = True
                except Exception:
                    pass

            if coalesce and last_rev:
                self._conn.execute(
                    "UPDATE composed_route_revision SET created=?, data=? WHERE id=?",
                    (now, payload, last_rev["id"]),
                )
            else:
                self._conn.execute(
                    "INSERT INTO composed_route_revision (route_id, created, label, data) VALUES (?, ?, ?, ?)",
                    (route.id, now, label, payload),
                )

            self._conn.commit()
            return route

    def get_route(self, route_id: str) -> Optional[ComposedRoute]:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM composed_route WHERE id=?", (route_id,)
            ).fetchone()
            return ComposedRoute.model_validate_json(row["data"]) if row else None

    def list_routes(self, pack_id: Optional[str] = None) -> List[ComposedRoute]:
        with self._lock:
            if pack_id:
                rows = self._conn.execute(
                    "SELECT data FROM composed_route WHERE pack_id=? ORDER BY modified DESC",
                    (pack_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT data FROM composed_route ORDER BY modified DESC"
                ).fetchall()
            return [ComposedRoute.model_validate_json(r["data"]) for r in rows]

    def list_route_revisions(self, route_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, created, label, data FROM composed_route_revision WHERE route_id=? ORDER BY id DESC",
                (route_id,),
            ).fetchall()
            return [
                {
                    "id": r["id"],
                    "created": r["created"],
                    "label": r["label"],
                    "route": ComposedRoute.model_validate_json(r["data"]),
                }
                for r in rows
            ]

    def delete_route(self, route_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM composed_route WHERE id=?", (route_id,))
            self._conn.commit()

    # ── Hike Sessions, Telemetry Points & Photo Pins ───────────────────────────

    def save_session(
        self,
        session: HikeSession,
        telemetry: Optional[List[TelemetryPoint]] = None,
        photo_pins: Optional[List[HikePhotoPin]] = None,
    ) -> HikeSession:
        with self._lock:
            session.modified = now_iso()
            if photo_pins is not None:
                session.stats.photo_count = len(photo_pins)
            self._conn.execute(
                """
                INSERT INTO hike_session (
                    id, pack_id, route_id, route_snapshot, title, started_at, ended_at,
                    status, device_source, stats, conditions_logged,
                    interactive_map_path, interactive_map_data, notes, created, modified
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    pack_id=excluded.pack_id,
                    route_id=excluded.route_id,
                    route_snapshot=excluded.route_snapshot,
                    title=excluded.title,
                    started_at=excluded.started_at,
                    ended_at=excluded.ended_at,
                    status=excluded.status,
                    device_source=excluded.device_source,
                    stats=excluded.stats,
                    conditions_logged=excluded.conditions_logged,
                    interactive_map_path=excluded.interactive_map_path,
                    interactive_map_data=excluded.interactive_map_data,
                    notes=excluded.notes,
                    modified=excluded.modified
                """,
                (
                    session.id,
                    session.pack_id,
                    session.route_id,
                    json.dumps(session.route_snapshot) if session.route_snapshot else None,
                    session.title,
                    session.started_at,
                    session.ended_at,
                    session.status,
                    session.device_source,
                    json.dumps(session.stats.model_dump()),
                    json.dumps(session.conditions_logged),
                    session.interactive_map_path,
                    json.dumps(session.interactive_map_data)
                    if session.interactive_map_data
                    else None,
                    session.notes,
                    session.created,
                    session.modified,
                ),
            )

            if telemetry is not None:
                self._conn.execute(
                    "DELETE FROM telemetry_point WHERE session_id=?", (session.id,)
                )
                self._conn.executemany(
                    """
                    INSERT INTO telemetry_point (
                        session_id, seq, timestamp, lat, lon, gps_ele_m, baro_ele_m,
                        horizontal_accuracy_m, speed_mps, grade_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            session.id,
                            pt.seq,
                            pt.timestamp,
                            pt.lat,
                            pt.lon,
                            pt.gps_ele_m,
                            pt.baro_ele_m,
                            pt.horizontal_accuracy_m,
                            pt.speed_mps,
                            pt.grade_pct,
                        )
                        for pt in telemetry
                    ],
                )

            if photo_pins is not None:
                self._conn.execute(
                    "DELETE FROM hike_photo_pin WHERE session_id=?", (session.id,)
                )
                for pin in photo_pins:
                    pin.session_id = session.id
                    self._conn.execute(
                        """
                        INSERT INTO hike_photo_pin (
                            id, session_id, voyager_photo_id, photo_path, thumb_path, filename,
                            date_taken, date_iso, lat, lon, ele_m, distance_along_m, cross_track_m,
                            camera_model, is_starred, ai_summary, ai_tags, ai_mood, people, caption
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pin.id,
                            session.id,
                            pin.voyager_photo_id,
                            pin.photo_path,
                            pin.thumb_path,
                            pin.filename,
                            pin.date_taken,
                            pin.date_iso,
                            pin.lat,
                            pin.lon,
                            pin.ele_m,
                            pin.distance_along_m,
                            pin.cross_track_m,
                            pin.camera_model,
                            1 if pin.is_starred else 0,
                            pin.ai_summary,
                            json.dumps(pin.ai_tags),
                            pin.ai_mood,
                            json.dumps(pin.people),
                            pin.caption,
                        ),
                    )

            self._conn.commit()
            return session

    def _row_to_session(self, r: sqlite3.Row) -> HikeSession:
        return HikeSession(
            id=r["id"],
            pack_id=r["pack_id"],
            route_id=r["route_id"],
            route_snapshot=json.loads(r["route_snapshot"]) if r["route_snapshot"] else None,
            title=r["title"],
            started_at=r["started_at"],
            ended_at=r["ended_at"],
            status=r["status"],
            device_source=r["device_source"],
            stats=HikeStats.model_validate_json(r["stats"]),
            conditions_logged=json.loads(r["conditions_logged"] or "{}"),
            interactive_map_path=r["interactive_map_path"] or "",
            interactive_map_data=json.loads(r["interactive_map_data"])
            if r["interactive_map_data"]
            else None,
            notes=r["notes"] or "",
            created=r["created"],
            modified=r["modified"],
        )

    def get_session(self, session_id: str) -> Optional[HikeSession]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM hike_session WHERE id=?", (session_id,)
            ).fetchone()
            return self._row_to_session(row) if row else None

    def list_sessions(self, pack_id: Optional[str] = None) -> List[HikeSession]:
        with self._lock:
            if pack_id:
                rows = self._conn.execute(
                    "SELECT * FROM hike_session WHERE pack_id=? ORDER BY started_at DESC",
                    (pack_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM hike_session ORDER BY started_at DESC"
                ).fetchall()
            return [self._row_to_session(r) for r in rows]

    def route_hike_stats(self) -> Dict[str, Tuple[int, str]]:
        """route_id -> (times hiked, started_at of the latest hike), counting recorded hikes only
        (not planned or still-tracking sessions)."""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT route_id, COUNT(*), MAX(started_at) FROM hike_session
                WHERE route_id IS NOT NULL AND status NOT IN ('planned', 'tracking')
                GROUP BY route_id
                """
            ).fetchall()
            return {r[0]: (r[1], r[2] or "") for r in rows}

    def get_telemetry(self, session_id: str) -> List[TelemetryPoint]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM telemetry_point WHERE session_id=? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
            return [
                TelemetryPoint(
                    seq=r["seq"],
                    timestamp=r["timestamp"],
                    lat=r["lat"],
                    lon=r["lon"],
                    gps_ele_m=r["gps_ele_m"],
                    baro_ele_m=r["baro_ele_m"],
                    horizontal_accuracy_m=r["horizontal_accuracy_m"],
                    speed_mps=r["speed_mps"],
                    grade_pct=r["grade_pct"],
                )
                for r in rows
            ]

    def get_photo_pins(self, session_id: str) -> List[HikePhotoPin]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM hike_photo_pin WHERE session_id=? ORDER BY distance_along_m ASC",
                (session_id,),
            ).fetchall()
            return [
                HikePhotoPin(
                    id=r["id"],
                    session_id=r["session_id"],
                    voyager_photo_id=r["voyager_photo_id"],
                    photo_path=r["photo_path"],
                    thumb_path=r["thumb_path"],
                    filename=r["filename"],
                    date_taken=r["date_taken"],
                    date_iso=r["date_iso"] or "",
                    lat=r["lat"],
                    lon=r["lon"],
                    ele_m=r["ele_m"],
                    distance_along_m=r["distance_along_m"],
                    cross_track_m=r["cross_track_m"],
                    camera_model=r["camera_model"] or "",
                    is_starred=bool(r["is_starred"]),
                    ai_summary=r["ai_summary"] or "",
                    ai_tags=json.loads(r["ai_tags"] or "[]"),
                    ai_mood=r["ai_mood"] or "",
                    people=json.loads(r["people"] or "[]"),
                    caption=r["caption"] or "",
                )
                for r in rows
            ]

    def delete_session(self, session_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM hike_session WHERE id=?", (session_id,))
            self._conn.commit()

    def remove_photos_from_session(self, session_id: str, pins: List[HikePhotoPin]) -> None:
        """Drop photos from a hike and remember them, so a rescan of AegisPhotoVoyager leaves them
        out. The photo files themselves are never touched."""
        with self._lock:
            removed_keys = set()
            for pin in pins:
                self._conn.execute("DELETE FROM hike_photo_pin WHERE id=? AND session_id=?", (pin.id, session_id))
                for key in photo_keys(pin):
                    removed_keys.add(key)
                    self._conn.execute(
                        "INSERT OR IGNORE INTO hike_photo_excluded (session_id, photo_key) VALUES (?, ?)",
                        (session_id, json.dumps(list(key))),
                    )
            # Copies of the same photo saved for this hike (shown once, deduplicated) go too
            for other in self.get_photo_pins(session_id):
                if removed_keys.intersection(photo_keys(other)):
                    self._conn.execute("DELETE FROM hike_photo_pin WHERE id=?", (other.id,))
            count = self._conn.execute(
                "SELECT COUNT(*) FROM hike_photo_pin WHERE session_id=?", (session_id,)
            ).fetchone()[0]
            row = self._conn.execute("SELECT stats FROM hike_session WHERE id=?", (session_id,)).fetchone()
            if row:
                stats = HikeStats.model_validate_json(row["stats"])
                stats.photo_count = int(count)
                self._conn.execute(
                    "UPDATE hike_session SET stats=?, modified=? WHERE id=?",
                    (stats.model_dump_json(), now_iso(), session_id),
                )
            self._conn.commit()

    def excluded_photo_keys(self, session_id: str) -> set:
        """Identities (see photo_keys) of photos removed from this hike."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT photo_key FROM hike_photo_excluded WHERE session_id=?", (session_id,)
            ).fetchall()
        return {tuple(json.loads(r[0])) for r in rows}

    # ── AI Hike Brief & Chat Messages ──────────────────────────────────────────

    def save_brief(self, brief: HikeBrief, route_id: Optional[str] = None) -> None:
        with self._lock:
            now = now_iso()
            self._conn.execute(
                """
                INSERT INTO hike_brief (id, route_id, data, created, modified)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    route_id=excluded.route_id,
                    data=excluded.data,
                    modified=excluded.modified
                """,
                (brief.id, route_id, json.dumps(brief.model_dump()), now, now),
            )
            self._conn.commit()

    def load_latest_brief(self) -> HikeBrief:
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM hike_brief ORDER BY modified DESC LIMIT 1"
            ).fetchone()
            return HikeBrief.model_validate_json(row["data"]) if row else HikeBrief()

    def add_chat_message(
        self,
        channel: str,
        role: str,
        content: str,
        route_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO chat_message (route_id, channel, role, content, created) VALUES (?, ?, ?, ?, ?)",
                (route_id, channel, role, content, now_iso()),
            )
            self._conn.commit()

    def get_chat_messages(self, channel: str, limit: int = 50) -> List[Dict[str, str]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content, created FROM chat_message WHERE channel=? ORDER BY id ASC",
                (channel,),
            ).fetchall()
            out = [{"role": r["role"], "content": r["content"], "created": r["created"]} for r in rows]
            return out[-limit:]

    # ── Convenience Aliases for GUI & Self-Test ───────────────────────────────

    def list_destinations(self) -> List[DestinationPack]:
        return self.list_destination_packs()

    def get_destination(self, pack_id: str) -> Optional[DestinationPack]:
        return self.get_destination_pack(pack_id)

    def import_destination_pack(self, pack: DestinationPack) -> None:
        self.upsert_destination_pack(pack)

    def search_trails(self, query: str, pack_id: Optional[str] = None) -> List[TrailSegment]:
        return self.search_trails_fts(query, pack_id)

    def upsert_route(self, route: ComposedRoute) -> ComposedRoute:
        return self.save_route(route)

    def save_photo_pin(self, pin: HikePhotoPin) -> None:
        with self._lock:
            session_id = pin.session_id
            if not session_id:
                sessions = self.list_sessions()
                if sessions:
                    session_id = sessions[0].id
                else:
                    s = HikeSession(route_id=pin.route_id or None, title="Photo Hike Session")
                    self.save_session(s)
                    session_id = s.id
                pin.session_id = session_id

            # The same photo saved again for this hike (e.g. "Rescan Voyager Photos") updates the
            # existing pin instead of adding a duplicate
            if pin.voyager_photo_id is not None or pin.date_taken:
                existing = self._conn.execute(
                    """
                    SELECT id FROM hike_photo_pin
                    WHERE session_id=? AND id<>? AND (
                        (voyager_photo_id IS NOT NULL AND voyager_photo_id=?)
                        OR (date_taken IS NOT NULL AND date_taken=? AND abs(lat-?)<1e-5 AND abs(lon-?)<1e-5)
                    ) LIMIT 1
                    """,
                    (pin.session_id, pin.id, pin.voyager_photo_id, pin.date_taken, pin.lat, pin.lon),
                ).fetchone()
                if existing:
                    pin.id = existing["id"]

            self._conn.execute(
                """
                INSERT OR REPLACE INTO hike_photo_pin (
                    id, session_id, voyager_photo_id, photo_path, thumb_path, filename,
                    date_taken, date_iso, lat, lon, ele_m, distance_along_m, cross_track_m,
                    camera_model, is_starred, ai_summary, ai_tags, ai_mood, people, caption
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pin.id,
                    pin.session_id,
                    pin.voyager_photo_id,
                    pin.photo_path,
                    pin.thumb_path,
                    pin.filename,
                    pin.date_taken,
                    pin.date_iso,
                    pin.lat,
                    pin.lon,
                    pin.ele_m,
                    pin.distance_along_m,
                    pin.cross_track_m,
                    pin.camera_model,
                    1 if pin.is_starred else 0,
                    pin.ai_summary,
                    json.dumps(pin.ai_tags),
                    pin.ai_mood,
                    json.dumps(pin.people),
                    pin.caption,
                ),
            )
            self._conn.commit()

    def list_photo_pins(
        self,
        session_id: Optional[str] = None,
        route_id: Optional[str] = None,
    ) -> List[HikePhotoPin]:
        if session_id:
            return self.get_photo_pins(session_id)
        if route_id:
            sessions = [s for s in self.list_sessions() if s.route_id == route_id]
            out: List[HikePhotoPin] = []
            for s in sessions:
                out.extend(self.get_photo_pins(s.id))
            return dedupe_pins(out)  # a photo can belong to several hikes of the same route
        return []

    def save_telemetry_points(self, points: List[TelemetryPoint]) -> None:
        if not points:
            return
        with self._lock:
            for idx, pt in enumerate(points):
                s_id = pt.session_id
                if not s_id:
                    continue
                self._conn.execute(
                    """
                    INSERT INTO telemetry_point (
                        session_id, seq, timestamp, lat, lon, gps_ele_m, baro_ele_m,
                        horizontal_accuracy_m, speed_mps, grade_pct
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        s_id,
                        pt.seq or idx,
                        pt.timestamp,
                        pt.lat,
                        pt.lon,
                        pt.gps_ele_m,
                        pt.baro_ele_m,
                        pt.horizontal_accuracy_m,
                        pt.speed_mps,
                        pt.grade_pct,
                    ),
                )
            self._conn.commit()

    def list_telemetry_points(self, session_id: str) -> List[TelemetryPoint]:
        return self.get_telemetry(session_id)

    def list_gear(self, route_id: str) -> List:
        route = self.get_route(route_id)
        return list(route.gear_checklist) if route else []

    def replace_gear_for_route(self, route_id: str, items: List) -> None:
        route = self.get_route(route_id)
        if route:
            route.gear_checklist = list(items)
            self.save_route(route)

