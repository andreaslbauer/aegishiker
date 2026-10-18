"""Interactive vector topographic map canvas with shaded relief, contours, OpenStreetMap
basemap detail (paths, water, peaks, shelters, ...), trail graph, POIs, click-to-snap
waypoints, photo markers, and animated hike replay cursor.
"""
import math
from typing import Dict, List, Optional, Tuple
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPolygonF,
    QTransform,
    QWheelEvent,
)
from PySide6.QtWidgets import QToolTip, QWidget

from src.hiking.geo import haversine_m
from src.hiking.models import ComposedRoute, DestinationPack, MapFeature, POI, TelemetryPoint, TrailPhotoPin, TrailSegment
from .base_chart import BaseChartWidget
from .photo_preview import PhotoPreviewPopup, format_time, photo_pixmap

M_PER_DEG_LAT = 110540.0
FT_PER_M = 3.28084

# Line/area basemap styles: kind -> (pen color, width px, dash, fill color or None)
LINE_STYLES: Dict[str, Tuple[str, float, Optional[Qt.PenStyle]]] = {
    "river": ("#4C9BD6", 2.2, None),
    "stream": ("#3F86BE", 1.0, None),
    "road_major": ("#D9B877", 2.6, None),
    "road_minor": ("#A99C84", 1.4, None),
    "track": ("#A88B64", 1.2, Qt.PenStyle.DashLine),
    "path": ("#E3D3A6", 1.3, Qt.PenStyle.DashLine),
    "path_hard": ("#E58F6B", 1.4, Qt.PenStyle.DotLine),
    "cliff": ("#90C9BBA0", 1.1, None),
    "aerialway": ("#B9B9C8", 1.2, Qt.PenStyle.DashDotLine),
    "water_edge": ("#4C9BD6", 1.0, None),
    "reserve": ("#A064C080", 1.3, Qt.PenStyle.DashDotLine),
}
AREA_STYLES: Dict[str, Tuple[str, str]] = {  # kind -> (fill rgba hex, outline rgba hex)
    "water": ("#C8235F86", "#E04C9BD6"),
    "wetland": ("#40407F6B", "#00000000"),
    "bare_rock": ("#38A8A090", "#00000000"),
    "scree": ("#30B0A890", "#00000000"),
    "glacier": ("#70DCEBF5", "#A0FFFFFF"),
    "forest": ("#3A2F7A44", "#00000000"),
    "open": ("#34B8AE6A", "#00000000"),
    "reserve": ("#00000000", "#A064C080"),  # boundary only
}
HARD_SAC = ("SAC T3", "SAC T4", "SAC T5", "SAC T6")

# Point features: kind -> minimum zoom detail level (0 = always, 1 = mid, 2 = close)
POINT_DETAIL = {
    "peak": 0, "hut": 0, "shelter": 1, "campsite": 1, "spring": 1, "drinking_water": 1,
    "trailhead": 1, "parking": 1, "ranger_station": 1, "emergency_phone": 1, "waterfall": 1,
    "viewpoint": 2, "saddle": 2, "toilets": 2, "guidepost": 2, "cave": 2,
    "picnic": 1, "gate": 2, "rock": 2, "historic": 2,
}
KIND_LABELS = {
    "peak": "Summit", "saddle": "Saddle / col", "spring": "Spring", "drinking_water": "Drinking water",
    "hut": "Mountain hut", "shelter": "Shelter", "campsite": "Campsite", "viewpoint": "Viewpoint",
    "waterfall": "Waterfall", "parking": "Parking", "trailhead": "Trailhead", "toilets": "Toilets",
    "ranger_station": "Ranger station", "guidepost": "Signpost / junction", "emergency_phone": "Emergency phone",
    "cave": "Cave", "path": "Trail", "track": "Track / fire road", "cliff": "Cliff",
    "picnic": "Picnic site", "gate": "Gate / barrier", "rock": "Rock / boulder", "historic": "Historic site",
    "reserve": "Reserve boundary",
}


def _hypso(ele: float, lo: float, hi: float) -> Tuple[float, float, float]:
    """Dark-theme elevation tint: forest green → olive → alpine grey-brown → pale rock."""
    t = 0.0 if hi <= lo else max(0.0, min(1.0, (ele - lo) / (hi - lo)))
    stops = [(0.0, (16, 34, 23)), (0.45, (30, 48, 30)), (0.75, (52, 52, 40)), (1.0, (78, 76, 68))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return tuple(c0[i] + (c1[i] - c0[i]) * f for i in range(3))  # type: ignore[return-value]
    return stops[-1][1]  # type: ignore[return-value]


# Built basemaps (relief image + world-space paths) shared by every MapCanvas: Explore, Composer
# and Re-Experience all show the same region, and building one takes a noticeable moment.
_BASEMAP_CACHE: Dict[tuple, dict] = {}
_BASEMAP_FIELDS = (
    "_kx", "_attribution", "_relief", "_relief_rect", "_contour_paths", "_contour_labels",
    "_contour_interval", "_line_paths", "_area_paths", "_point_features", "_hover_lines",
)


class MapCanvas(BaseChartWidget):
    waypoint_clicked = Signal(float, float)  # (lat, lon)
    trail_selected = Signal(str)  # trail_id
    photo_clicked = Signal(str)  # photo_pin_id
    hover_distance_changed = Signal(float)  # distance_along_route_m
    hover_geo = Signal(float, float)  # (lat, lon) under the cursor while not dragging

    DIFFICULTY_COLORS = {
        "easy": "#2ECC71",
        "moderate": "#3498DB",
        "hard": "#F39C12",
        "strenuous": "#E74C3C",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Select a destination pack to view its trail network", parent)
        self.setMinimumHeight(300)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.trails: List[TrailSegment] = []
        self.pois: List[POI] = []
        self.active_route: Optional[ComposedRoute] = None
        self.photo_pins: List[TrailPhotoPin] = []
        self.telemetry: List[TelemetryPoint] = []
        self.waypoints: List[Tuple[float, float]] = []
        self.excluded_conditions: set[str] = set()
        self.highlight_distance_m: Optional[float] = None
        self.preview_coords: List[Tuple[float, float]] = []  # (lat, lon) of a candidate segment
        self.candidate_paths: List[List[Tuple[float, float]]] = []  # pickable next segments
        # Photo thumbnails pinned on the map (Re-experience); otherwise small "P" markers
        self.show_photo_thumbnails = False
        self.selected_photo_id: Optional[str] = None
        self._photo_hits: List[Tuple[QRectF, TrailPhotoPin]] = []  # screen rects from the last paint
        self._preview: Optional[PhotoPreviewPopup] = None
        self._pending_fit: Optional[List[Tuple[float, ...]]] = None
        self.units: str = "us"
        self.zoom: float = 1.0
        self.pan_offset = QPointF(0.0, 0.0)
        self._bbox: Tuple[float, float, float, float] = (44.10, -71.75, 44.22, -71.60)
        self._press_pos: Optional[QPointF] = None
        self._dragging = False

        # Basemap (from the destination pack's downloaded map details)
        self.basemap_pack_id: Optional[str] = None
        self._kx: float = math.cos(math.radians(44.0))
        self._relief: Optional[QImage] = None
        self._relief_rect = QRectF()
        self._contour_paths: Tuple[QPainterPath, QPainterPath] = (QPainterPath(), QPainterPath())
        self._contour_labels: List[Tuple[float, float, float]] = []  # (lon, lat, ele_m) on index contours
        self._contour_interval = 0.0
        self._line_paths: Dict[str, QPainterPath] = {}
        self._area_paths: Dict[str, QPainterPath] = {}
        self._point_features: List[MapFeature] = []
        self._hover_lines: List[Tuple[Tuple[float, float, float, float], MapFeature]] = []
        self._attribution = ""

    # ── Data ────────────────────────────────────────────────────────────────

    def set_scene_data(
        self,
        trails: List[TrailSegment],
        pois: Optional[List[POI]] = None,
        route: Optional[ComposedRoute] = None,
        photo_pins: Optional[List[TrailPhotoPin]] = None,
        telemetry: Optional[List[TelemetryPoint]] = None,
    ) -> None:
        self.trails = list(trails or [])
        self.pois = list(pois or [])
        self.active_route = route
        self.photo_pins = list(photo_pins or [])
        self.telemetry = list(telemetry or [])
        self._recompute_bbox()
        self.update()

    def set_basemap(self, pack: Optional[DestinationPack]) -> None:
        """Build cached relief image and world-space paths for the pack's map details."""
        self.basemap_pack_id = pack.id if pack else None
        self._relief = None
        self._contour_paths = (QPainterPath(), QPainterPath())
        self._contour_labels = []
        self._line_paths = {}
        self._area_paths = {}
        self._point_features = []
        self._hover_lines = []
        self._attribution = ""
        self.zoom = 1.0
        self.pan_offset = QPointF(0.0, 0.0)
        if pack is None:
            self.update()
            return
        key = (pack.id, pack.pack_version, pack.installed_at, len(pack.features), len(pack.contours))
        cached = _BASEMAP_CACHE.get(key)
        if cached is None:
            self._kx = math.cos(math.radians(0.5 * (pack.min_lat + pack.max_lat)))
            self._attribution = pack.map_attribution
            # Only shade downloaded terrain; the synthetic starter grids are too coarse to look like relief
            if pack.dem and pack.dem.rows >= 3 and pack.dem.cols >= 3 and pack.map_attribution:
                self._build_relief(pack)
            self._build_contours(pack)
            self._build_features(pack.features)
            cached = {name: getattr(self, name) for name in _BASEMAP_FIELDS}
            _BASEMAP_CACHE[key] = cached
            while len(_BASEMAP_CACHE) > 4:  # a few regions' worth of paths and relief images
                _BASEMAP_CACHE.pop(next(iter(_BASEMAP_CACHE)))
        for name, value in cached.items():
            setattr(self, name, value)
        self.update()

    def sync_basemap(self, db, pack_id: Optional[str]) -> None:
        """Load the pack's map details only when the destination changes (packs can be MBs)."""
        if pack_id != self.basemap_pack_id:
            self.set_basemap(db.get_destination_pack(pack_id) if pack_id else None)

    def _w(self, lon: float, lat: float) -> QPointF:
        return QPointF(lon * self._kx, -lat)

    def _build_relief(self, pack: DestinationPack) -> None:
        dem = pack.dem
        assert dem is not None
        g = dem.elevations_m
        rows, cols = dem.rows, dem.cols
        lo = min(min(r) for r in g)
        hi = max(max(r) for r in g)
        dx = (dem.max_lon - dem.min_lon) / (cols - 1) * M_PER_DEG_LAT * self._kx
        dy = (dem.max_lat - dem.min_lat) / (rows - 1) * M_PER_DEG_LAT
        # Light from the north-west, 45° altitude; slight vertical exaggeration for low relief
        az, alt = math.radians(315.0), math.radians(45.0)
        lx, ly, lz = math.sin(az) * math.cos(alt), math.cos(az) * math.cos(alt), math.sin(alt)
        zf = 1.5 if hi - lo < 600 else 1.0
        # Elevation tint via a 256-step lookup and raw BGRA bytes: per-pixel QColor/setPixel calls
        # made this take ~1.7 s for a 560x265 grid
        span = max(1e-6, hi - lo)
        lut = [_hypso(lo + span * i / 255.0, lo, hi) for i in range(256)]
        buf = bytearray(cols * rows * 4)
        two_dx, two_dy = 2 * dx / zf, 2 * dy / zf
        for r in range(rows):
            up, dn = g[min(rows - 1, r + 1)], g[max(0, r - 1)]
            row = g[r]
            o = (rows - 1 - r) * cols * 4  # image row 0 = north
            for c in range(cols):
                dzdx = (row[c + 1 if c + 1 < cols else c] - row[c - 1 if c else 0]) / two_dx
                dzdy = (up[c] - dn[c]) / two_dy
                shade = (-dzdx * lx - dzdy * ly + lz) / math.sqrt(dzdx * dzdx + dzdy * dzdy + 1.0)
                f = 0.35 + 0.95 * (shade if shade > 0.0 else 0.0)
                cr, cg, cb = lut[int((row[c] - lo) / span * 255.0)]
                buf[o] = min(255, int(cb * f))
                buf[o + 1] = min(255, int(cg * f))
                buf[o + 2] = min(255, int(cr * f))
                buf[o + 3] = 255
                o += 4
        img = QImage(bytes(buf), cols, rows, cols * 4, QImage.Format.Format_RGB32).copy()
        self._relief = img
        tl = self._w(dem.min_lon, dem.max_lat)
        br = self._w(dem.max_lon, dem.min_lat)
        self._relief_rect = QRectF(tl, br)

    def _build_contours(self, pack: DestinationPack) -> None:
        interval = pack.contour_interval_m
        if not interval and len(pack.contours) > 1:
            levels = sorted({c.ele_m for c in pack.contours})
            interval = min((b - a for a, b in zip(levels, levels[1:])), default=0.0)
        self._contour_interval = interval
        normal, index = QPainterPath(), QPainterPath()
        for line in pack.contours:
            if len(line.coords) < 2:
                continue
            is_index = interval > 0 and abs(round(line.ele_m / (interval * 5)) * interval * 5 - line.ele_m) < 0.5
            target = index if is_index else normal
            target.moveTo(self._w(line.coords[0][0], line.coords[0][1]))
            for lon, lat in line.coords[1:]:
                target.lineTo(self._w(lon, lat))
            if is_index and len(line.coords) >= 12:
                mid = line.coords[len(line.coords) // 2]
                self._contour_labels.append((mid[0], mid[1], line.ele_m))
        self._contour_paths = (normal, index)

    def _build_features(self, features: List[MapFeature]) -> None:
        for f in features:
            if f.geom == "point":
                if f.kind in POINT_DETAIL and f.coords:
                    self._point_features.append(f)
                continue
            if len(f.coords) < 2:
                continue
            if f.geom == "area" and f.kind in AREA_STYLES:
                path = self._area_paths.setdefault(f.kind, QPainterPath())
                path.addPolygon(QPolygonF([self._w(lon, lat) for lon, lat in f.coords]))
                path.closeSubpath()
                continue
            key = f.kind
            if f.kind == "path" and any(s in f.info for s in HARD_SAC):
                key = "path_hard"
            elif f.kind == "water":
                key = "water_edge"
            if key not in LINE_STYLES:
                continue
            path = self._line_paths.setdefault(key, QPainterPath())
            path.moveTo(self._w(f.coords[0][0], f.coords[0][1]))
            for lon, lat in f.coords[1:]:
                path.lineTo(self._w(lon, lat))
            if f.kind in ("path", "track", "cliff", "aerialway"):
                lons = [c[0] for c in f.coords]
                lats = [c[1] for c in f.coords]
                self._hover_lines.append(((min(lats), min(lons), max(lats), max(lons)), f))
        # Draw order for points: low-priority kinds first so peaks/huts end up on top
        self._point_features.sort(key=lambda f: -POINT_DETAIL.get(f.kind, 2))

    def fit_when_shown(self, coords: List[Tuple[float, ...]], margin_px: float = 50.0) -> None:
        """zoom_to_coordinates now if the map is on screen, else as soon as it is shown with its
        real size (a map in a hidden tab has a stale size)."""
        self._fit_margin = margin_px
        if self.isVisible() and self.width() > 100 and self.height() > 100:
            self._pending_fit = None
            self.zoom_to_coordinates(coords, margin_px)
        else:
            self._pending_fit = list(coords)

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event is not None:
            super().resizeEvent(event)
        if self._pending_fit and self.isVisible() and self.width() > 100 and self.height() > 100:
            coords, self._pending_fit = self._pending_fit, None
            self.zoom_to_coordinates(coords, getattr(self, "_fit_margin", 50.0))

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        if self._pending_fit:
            QTimer.singleShot(0, lambda: self.resizeEvent(None) if self._pending_fit else None)

    def zoom_to_coordinates(self, coords: List[Tuple[float, ...]], margin_px: float = 50.0) -> None:
        """Zoom and pan so the given (lat, lon, ...) points fill the view with a margin."""
        if not coords:
            return
        lats = [c[0] for c in coords]
        lons = [c[1] for c in coords]
        min_lat, min_lon, max_lat, max_lon = self._bbox
        w = max(10.0, float(self.width()) - 40.0)
        h = max(10.0, float(self.height()) - 40.0)
        base = min(w / max(1e-6, (max_lon - min_lon) * self._kx), h / max(1e-6, max_lat - min_lat))
        avail_w = max(20.0, float(self.width()) - 2 * margin_px)
        avail_h = max(20.0, float(self.height()) - 2 * margin_px)
        target = min(
            avail_w / max(1e-6, (max(lons) - min(lons)) * self._kx),
            avail_h / max(1e-6, max(lats) - min(lats)),
        )
        self.zoom = max(0.25, min(80.0, target / base))
        self.pan_offset = QPointF(0.0, 0.0)
        centre = self.project(0.5 * (min(lats) + max(lats)), 0.5 * (min(lons) + max(lons)))
        self.pan_offset = QPointF(self.width() / 2.0, self.height() / 2.0) - centre
        self.update()

    def set_candidates(self, paths: Optional[List[List[Tuple[float, float]]]]) -> None:
        """Show the segments the user can pick next (thin dashed lines)."""
        self.candidate_paths = [p for p in (paths or []) if len(p) >= 2]
        self.update()

    def metres_per_pixel(self) -> float:
        return 1.0 / max(1e-9, self._px_per_m())

    def set_preview(self, coords: Optional[List[Tuple[float, float]]]) -> None:
        """Show (or clear) a dashed preview of a segment the user is considering."""
        self.preview_coords = list(coords or [])
        self.update()

    def set_highlight_distance(self, distance_m: Optional[float]) -> None:
        self.highlight_distance_m = distance_m
        self.update()

    def _recompute_bbox(self) -> None:
        lats: List[float] = []
        lons: List[float] = []
        for t in self.trails:
            for c in t.coordinates:
                lats.append(c[0])
                lons.append(c[1])
        if self.active_route:
            for c in self.active_route.coordinates:
                lats.append(c[0])
                lons.append(c[1])
        for p in self.pois:
            lats.append(p.lat)
            lons.append(p.lon)
        if lats and lons:
            min_lat, max_lat = min(lats), max(lats)
            min_lon, max_lon = min(lons), max(lons)
            pad_lat = max(0.004, (max_lat - min_lat) * 0.12)
            pad_lon = max(0.004, (max_lon - min_lon) * 0.12)
            self._bbox = (min_lat - pad_lat, min_lon - pad_lon, max_lat + pad_lat, max_lon + pad_lon)
            if self.basemap_pack_id is None:  # cached basemap paths were built with the pack's kx
                self._kx = math.cos(math.radians(0.5 * (min_lat + max_lat)))

    # ── Projection (uniform scale, longitude corrected by cos(latitude)) ──────────

    def _view_transform(self) -> QTransform:
        min_lat, min_lon, max_lat, max_lon = self._bbox
        w = max(10.0, float(self.width()) - 40.0)
        h = max(10.0, float(self.height()) - 40.0)
        world_w = max(1e-6, (max_lon - min_lon) * self._kx)
        world_h = max(1e-6, max_lat - min_lat)
        s = min(w / world_w, h / world_h) * self.zoom
        wcx = 0.5 * (min_lon + max_lon) * self._kx
        wcy = -0.5 * (min_lat + max_lat)
        dx = self.width() / 2.0 - s * wcx + self.pan_offset.x()
        dy = self.height() / 2.0 - s * wcy + self.pan_offset.y()
        return QTransform(s, 0.0, 0.0, s, dx, dy)

    def _px_per_m(self) -> float:
        return self._view_transform().m11() / M_PER_DEG_LAT

    def project(self, lat: float, lon: float) -> QPointF:
        return self._view_transform().map(self._w(lon, lat))

    def unproject(self, pos: QPointF) -> Tuple[float, float]:
        inv, _ok = self._view_transform().inverted()
        p = inv.map(QPointF(pos))
        return (-p.y(), p.x() / self._kx)

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        if not self.trails and not self.active_route:
            self.draw_empty_state(painter)
            painter.end()
            return

        self.draw_background(painter, "#0B1610", "#223829")
        painter.setClipRect(self.rect().adjusted(2, 2, -2, -2))
        xf = self._view_transform()
        self._draw_relief(painter, xf)
        self._draw_areas(painter, xf)
        self._draw_topo_contours(painter, xf)
        self._draw_basemap_lines(painter, xf)
        self._draw_trails(painter, xf)
        self._draw_contour_labels(painter)
        self._draw_basemap_points(painter)
        self._draw_pois(painter)
        self._draw_active_route(painter, xf)
        self._draw_preview(painter, xf)
        self._draw_telemetry(painter)
        self._draw_photo_pins(painter)
        self._draw_waypoints(painter)
        self._draw_highlight_cursor(painter)
        self._draw_scale_and_legend(painter)
        painter.end()

    @staticmethod
    def _cosmetic(color: str | QColor, width: float, style: Optional[Qt.PenStyle] = None) -> QPen:
        pen = QPen(QColor(color), width)
        pen.setCosmetic(True)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        if style is not None:
            pen.setStyle(style)
        return pen

    def _draw_relief(self, painter: QPainter, xf: QTransform) -> None:
        if self._relief is None:
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setTransform(xf)
        painter.drawImage(self._relief_rect, self._relief)
        painter.restore()

    def _draw_areas(self, painter: QPainter, xf: QTransform) -> None:
        if not self._area_paths:
            return
        painter.save()
        painter.setTransform(xf)
        if self._relief is not None:
            # Land-cover polygons from OSM relations can reach past the downloaded region
            painter.setClipRect(self._relief_rect)
        for kind in ("forest", "open", "wetland", "scree", "bare_rock", "glacier", "water", "reserve"):
            path = self._area_paths.get(kind)
            if path is None:
                continue
            fill, edge = AREA_STYLES[kind]
            if kind == "reserve":
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(self._cosmetic(edge, 1.3, Qt.PenStyle.DashDotLine))
                painter.drawPath(path)
                continue
            painter.setBrush(QBrush(QColor(fill)))
            painter.setPen(self._cosmetic(edge, 0.8) if QColor(edge).alpha() else Qt.PenStyle.NoPen)
            painter.drawPath(path)
        painter.restore()

    def _draw_topo_contours(self, painter: QPainter, xf: Optional[QTransform] = None) -> None:
        normal, index = self._contour_paths
        if normal.isEmpty() and index.isEmpty():
            return
        xf = xf or self._view_transform()
        ppm = xf.m11() / M_PER_DEG_LAT
        painter.save()
        painter.setTransform(xf)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        # Thin contours fade out when zoomed far out so the index lines stay readable
        spacing_px = self._contour_interval * ppm * 8 if self._contour_interval else 10.0
        if spacing_px > 3.0:
            painter.setPen(self._cosmetic(QColor(176, 146, 98, 70 if spacing_px > 8 else 40), 0.7))
            painter.drawPath(normal)
        painter.setPen(self._cosmetic(QColor(196, 160, 104, 140), 1.2))
        painter.drawPath(index)
        painter.restore()

    def _draw_basemap_lines(self, painter: QPainter, xf: QTransform) -> None:
        if not self._line_paths:
            return
        ppm = xf.m11() / M_PER_DEG_LAT
        painter.save()
        painter.setTransform(xf)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        order = ["reserve", "water_edge", "stream", "river", "cliff", "road_minor", "road_major", "track", "aerialway", "path", "path_hard"]
        for key in order:
            path = self._line_paths.get(key)
            if path is None:
                continue
            color, width, style = LINE_STYLES[key]
            if key == "stream" and ppm < 1 / 40.0:
                width = 0.7
            if key == "road_major":
                painter.setPen(self._cosmetic("#3A2E1A", width + 1.6))
                painter.drawPath(path)
            painter.setPen(self._cosmetic(color, width, style))
            painter.drawPath(path)
        painter.restore()

    def _draw_trails(self, painter: QPainter, xf: Optional[QTransform] = None) -> None:
        xf = xf or self._view_transform()
        painter.save()
        painter.setTransform(xf)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for seg in self.trails:
            if len(seg.coordinates) < 2:
                continue
            cond = seg.condition.value.lower()
            excluded = cond in self.excluded_conditions or cond == "closed"
            color_hex = self.DIFFICULTY_COLORS.get(seg.difficulty.value.lower(), "#98B2A1")
            color = QColor(color_hex)
            if excluded:
                color.setAlpha(65)
                pen = self._cosmetic(color, 1.5, Qt.PenStyle.DashLine)
            else:
                # While picking next segments, fade the network so the options stand out
                color.setAlpha(70 if self.candidate_paths else 200)
                pen = self._cosmetic(color, 3.0)
            painter.setPen(pen)
            path = QPainterPath()
            path.moveTo(self._w(seg.coordinates[0][1], seg.coordinates[0][0]))
            for c in seg.coordinates[1:]:
                path.lineTo(self._w(c[1], c[0]))
            painter.drawPath(path)
        painter.restore()

    def _fmt_ele(self, ele_m: float) -> str:
        if self.units == "metric":
            return f"{ele_m:,.0f} m"
        return f"{ele_m * FT_PER_M:,.0f} ft"

    def _visible(self, pt: QPointF, margin: float = 20.0) -> bool:
        return -margin <= pt.x() <= self.width() + margin and -margin <= pt.y() <= self.height() + margin

    def _draw_contour_labels(self, painter: QPainter) -> None:
        if not self._contour_labels or self._px_per_m() < 1 / 30.0:
            return
        painter.setFont(QFont("Sans Serif", 7))
        fm = QFontMetricsF(painter.font())
        for lon, lat, ele in self._contour_labels:
            pt = self.project(lat, lon)
            if not self._visible(pt, 0):
                continue
            txt = self._fmt_ele(ele)
            r = fm.boundingRect(txt).adjusted(-2, -1, 2, 1)
            r.moveCenter(pt)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(11, 22, 16, 170))
            painter.drawRect(r)
            painter.setPen(QColor(214, 180, 120))
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, txt)

    def _detail_level(self) -> int:
        mpp = 1.0 / max(1e-9, self._px_per_m())  # metres per pixel
        if mpp < 9:
            return 2
        if mpp < 30:
            return 1
        return 0

    def _draw_symbol(self, painter: QPainter, kind: str, pt: QPointF) -> None:
        x, y = pt.x(), pt.y()
        painter.setPen(QPen(QColor("#0B1610"), 1.0))
        if kind == "peak":
            painter.setBrush(QColor("#E8E0CF"))
            painter.drawPolygon(QPolygonF([QPointF(x, y - 5.5), QPointF(x - 5, y + 3.5), QPointF(x + 5, y + 3.5)]))
        elif kind == "saddle":
            painter.setPen(QPen(QColor("#E8E0CF"), 1.4))
            painter.drawLine(QPointF(x - 4, y - 3), QPointF(x - 1.5, y))
            painter.drawLine(QPointF(x - 1.5, y), QPointF(x - 4, y + 3))
            painter.drawLine(QPointF(x + 4, y - 3), QPointF(x + 1.5, y))
            painter.drawLine(QPointF(x + 1.5, y), QPointF(x + 4, y + 3))
        elif kind in ("spring", "drinking_water"):
            painter.setBrush(QColor("#4FA8E8"))
            path = QPainterPath(QPointF(x, y - 6))
            path.cubicTo(QPointF(x + 1, y - 3), QPointF(x + 4.5, y), QPointF(x, y + 4))
            path.cubicTo(QPointF(x - 4.5, y), QPointF(x - 1, y - 3), QPointF(x, y - 6))
            painter.drawPath(path)
        elif kind == "waterfall":
            painter.setPen(QPen(QColor("#7CC4F2"), 1.6))
            for off in (-2.5, 0.0, 2.5):
                painter.drawLine(QPointF(x + off, y - 4), QPointF(x + off, y + 4))
        elif kind in ("hut", "shelter", "ranger_station"):
            painter.setBrush(QColor("#E67E22" if kind == "hut" else ("#C0A060" if kind == "shelter" else "#7F8C8D")))
            painter.drawPolygon(QPolygonF([
                QPointF(x, y - 6), QPointF(x + 5, y - 1.5), QPointF(x + 5, y + 4),
                QPointF(x - 5, y + 4), QPointF(x - 5, y - 1.5),
            ]))
        elif kind == "campsite":
            painter.setBrush(QColor("#58D68D"))
            painter.drawPolygon(QPolygonF([QPointF(x, y - 5), QPointF(x - 5.5, y + 4), QPointF(x + 5.5, y + 4)]))
            painter.setPen(QPen(QColor("#0B1610"), 1.2))
            painter.drawLine(QPointF(x, y - 1), QPointF(x, y + 4))
        elif kind == "viewpoint":
            painter.setPen(QPen(QColor("#F4D03F"), 1.3))
            painter.setBrush(QColor("#F4D03F"))
            painter.drawEllipse(pt, 2.2, 2.2)
            for a in range(0, 360, 60):
                rad = math.radians(a)
                painter.drawLine(QPointF(x + 3.5 * math.cos(rad), y + 3.5 * math.sin(rad)),
                                 QPointF(x + 6 * math.cos(rad), y + 6 * math.sin(rad)))
        elif kind in ("parking", "trailhead", "toilets", "emergency_phone"):
            glyph, bg = {"parking": ("P", "#2E86C1"), "trailhead": ("T", "#1E8449"),
                         "toilets": ("WC", "#5D6D7E"), "emergency_phone": ("SOS", "#C0392B")}[kind]
            painter.setFont(QFont("Sans Serif", 6, QFont.Weight.Bold))
            fm = QFontMetricsF(painter.font())
            w = max(10.0, fm.horizontalAdvance(glyph) + 4)
            r = QRectF(x - w / 2, y - 5, w, 10)
            painter.setBrush(QColor(bg))
            painter.setPen(QPen(QColor("#ECF5EE"), 0.8))
            painter.drawRoundedRect(r, 2, 2)
            painter.drawText(r, Qt.AlignmentFlag.AlignCenter, glyph)
        elif kind == "guidepost":
            painter.setBrush(QColor("#D4AC6E"))
            painter.drawRect(QRectF(x - 2.5, y - 2.5, 5, 5))
        elif kind == "picnic":
            painter.setPen(QPen(QColor("#E8C07A"), 1.4))
            painter.drawLine(QPointF(x - 5, y - 2), QPointF(x + 5, y - 2))
            painter.drawLine(QPointF(x - 3, y - 2), QPointF(x - 4.5, y + 4))
            painter.drawLine(QPointF(x + 3, y - 2), QPointF(x + 4.5, y + 4))
            painter.drawLine(QPointF(x - 5, y + 1.5), QPointF(x + 5, y + 1.5))
        elif kind == "gate":
            painter.setPen(QPen(QColor("#0B1610"), 3.0))
            painter.drawLine(QPointF(x - 4.5, y), QPointF(x + 4.5, y))
            painter.setPen(QPen(QColor("#D9C9A0"), 1.6))
            painter.drawLine(QPointF(x - 4.5, y), QPointF(x + 4.5, y))
        elif kind == "rock":
            painter.setBrush(QColor("#A9A49A"))
            painter.drawPolygon(QPolygonF([
                QPointF(x - 4.5, y + 3), QPointF(x - 3, y - 2), QPointF(x, y - 4),
                QPointF(x + 3.5, y - 2.5), QPointF(x + 4.5, y + 3),
            ]))
        elif kind == "historic":
            painter.setBrush(QColor("#B08D57"))
            painter.drawPolygon(QPolygonF([QPointF(x, y - 4.5), QPointF(x + 4.5, y), QPointF(x, y + 4.5), QPointF(x - 4.5, y)]))
        elif kind == "cave":
            painter.setBrush(QColor("#1C1C1C"))
            painter.setPen(QPen(QColor("#E8E0CF"), 1.0))
            painter.drawChord(QRectF(x - 5, y - 4, 10, 10), 0, 180 * 16)

    def _draw_basemap_points(self, painter: QPainter) -> None:
        if not self._point_features:
            return
        level = self._detail_level()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        label_font = QFont("Sans Serif", 7)
        fm = QFontMetricsF(label_font)
        poi_fm = QFontMetricsF(QFont("Sans Serif", 8))
        placed: List[QRectF] = []
        for poi in self.pois:
            pt = self.project(poi.lat, poi.lon)
            r = poi_fm.boundingRect(poi.name)
            r.moveBottomLeft(QPointF(pt.x() + 7, pt.y() + 3 + poi_fm.descent()))
            placed.append(r.adjusted(-6, -2, 2, 2))
        # Labels: peaks first (they orient hikers), then other named features at close zoom
        labelled = []
        for f in reversed(self._point_features):
            if POINT_DETAIL[f.kind] > level:
                continue
            pt = self.project(f.coords[0][1], f.coords[0][0])
            if not self._visible(pt):
                continue
            self._draw_symbol(painter, f.kind, pt)
            if f.name and (f.kind == "peak" or level >= 2 or (level == 1 and f.kind in ("hut", "shelter", "campsite"))):
                labelled.append((f, pt))
        painter.setFont(label_font)
        for f, pt in labelled:
            txt = f.name if f.kind != "peak" or f.ele_m is None else f"{f.name} {self._fmt_ele(f.ele_m)}"
            r = fm.boundingRect(txt)
            r.moveTopLeft(QPointF(pt.x() + 7, pt.y() - r.height() / 2))
            if any(r.intersects(o) for o in placed):
                continue
            placed.append(r)
            painter.setPen(QColor(11, 22, 16, 230))
            for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                painter.drawText(r.translated(dx, dy), Qt.AlignmentFlag.AlignLeft, txt)
            painter.setPen(QColor("#F2EBDD" if f.kind == "peak" else "#CFE3D6"))
            painter.drawText(r, Qt.AlignmentFlag.AlignLeft, txt)
        painter.restore()

    def _draw_pois(self, painter: QPainter) -> None:
        painter.setFont(QFont("Sans Serif", 8))
        for poi in self.pois:
            pt = self.project(poi.lat, poi.lon)
            painter.setPen(QPen(QColor("#ECF5EE"), 1.2))
            painter.setBrush(QBrush(QColor("#3498DB")))
            painter.drawEllipse(pt, 4.5, 4.5)
            painter.setPen(QColor("#98B2A1"))
            painter.drawText(QPointF(pt.x() + 7, pt.y() + 3), poi.name)

    def _draw_active_route(self, painter: QPainter, xf: Optional[QTransform] = None) -> None:
        if not self.active_route or len(self.active_route.coordinates) < 2:
            return
        xf = xf or self._view_transform()
        coords = self.active_route.coordinates
        path = QPainterPath()
        path.moveTo(self._w(coords[0][1], coords[0][0]))
        for c in coords[1:]:
            path.lineTo(self._w(c[1], c[0]))

        painter.save()
        painter.setTransform(xf)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self._cosmetic(QColor(46, 204, 113, 80), 8.0))
        painter.drawPath(path)
        painter.setPen(self._cosmetic("#2ECC71", 3.8))
        painter.drawPath(path)
        painter.restore()

        # Start and end markers
        p0 = self.project(coords[0][0], coords[0][1])
        end_pt = self.project(coords[-1][0], coords[-1][1])
        painter.setBrush(QBrush(QColor("#2ECC71")))
        painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
        painter.drawEllipse(p0, 6.0, 6.0)
        painter.setBrush(QBrush(QColor("#E74C3C")))
        painter.drawEllipse(end_pt, 6.0, 6.0)

    def _draw_preview(self, painter: QPainter, xf: QTransform) -> None:
        if self.candidate_paths:
            painter.save()
            painter.setTransform(xf)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            options = QPainterPath()
            for pts in self.candidate_paths:
                options.moveTo(self._w(pts[0][1], pts[0][0]))
                for lat, lon in pts[1:]:
                    options.lineTo(self._w(lon, lat))
            painter.setPen(self._cosmetic(QColor(11, 22, 16, 170), 5.0))
            painter.drawPath(options)
            painter.setPen(self._cosmetic(QColor(244, 208, 63, 220), 2.6, Qt.PenStyle.DashLine))
            painter.drawPath(options)
            painter.restore()
        if len(self.preview_coords) < 2:
            return
        path = QPainterPath()
        path.moveTo(self._w(self.preview_coords[0][1], self.preview_coords[0][0]))
        for lat, lon in self.preview_coords[1:]:
            path.lineTo(self._w(lon, lat))
        painter.save()
        painter.setTransform(xf)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(self._cosmetic(QColor(11, 22, 16, 200), 6.5))
        painter.drawPath(path)
        painter.setPen(self._cosmetic("#F4D03F", 3.5, Qt.PenStyle.DashLine))
        painter.drawPath(path)
        painter.restore()
        end = self.project(self.preview_coords[-1][0], self.preview_coords[-1][1])
        painter.setPen(QPen(QColor("#0B1610"), 1.2))
        painter.setBrush(QColor("#F4D03F"))
        painter.drawEllipse(end, 5.0, 5.0)

    def _draw_telemetry(self, painter: QPainter) -> None:
        if len(self.telemetry) < 2:
            return
        path = QPainterPath()
        p0 = self.project(self.telemetry[0].lat, self.telemetry[0].lon)
        path.moveTo(p0)
        for pt in self.telemetry[1:]:
            path.lineTo(self.project(pt.lat, pt.lon))
        painter.setPen(QPen(QColor("#F39C12"), 2.5, Qt.PenStyle.DotLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

    def _draw_photo_pins(self, painter: QPainter) -> None:
        self._photo_hits = []
        if self.show_photo_thumbnails:
            self._draw_photo_thumbnails(painter)
            return
        for pin in self.photo_pins:
            pt = self.project(pin.lat, pin.lon)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.5))
            painter.setBrush(QBrush(QColor("#F39C12")))
            painter.drawEllipse(pt, 6.5, 6.5)
            painter.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            painter.drawText(QRectF(pt.x() - 6, pt.y() - 6, 12, 12), Qt.AlignmentFlag.AlignCenter, "P")
            self._photo_hits.append((QRectF(pt.x() - 8, pt.y() - 8, 16, 16), pin))

    THUMB = 58  # px, longest side of a map thumbnail

    def _draw_photo_thumbnails(self, painter: QPainter) -> None:
        """Thumbnails on stems above their spot with the time taken; where they would overlap,
        try other sides, else fall back to a dot (hover still previews it)."""
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        time_font = QFont("Sans Serif", 7, QFont.Weight.Bold)
        fm = QFontMetricsF(time_font)
        placed: List[QRectF] = []
        dots: List[Tuple[QPointF, TrailPhotoPin]] = []
        bounds = QRectF(self.rect()).adjusted(4, 4, -4, -4)
        # Selected photo first so it always gets a thumbnail, then in time order
        order = sorted(self.photo_pins, key=lambda p: (p.id != self.selected_photo_id, p.date_taken or 0))
        for pin in order:
            pt = self.project(pin.lat, pin.lon)
            if not self._visible(pt, 80):
                continue
            pix = photo_pixmap(pin, self.THUMB)
            w, h = (pix.width(), pix.height()) if pix is not None else (self.THUMB, int(self.THUMB * 0.75))
            label = format_time(pin)
            box_h = h + 6 + (fm.height() + 2 if label else 0)
            box_w = max(w + 6, fm.horizontalAdvance(label) + 8)
            box = None
            for dx, dy in ((0, -1), (1, -1), (-1, -1), (1, 0), (-1, 0), (0, 1)):
                cx = pt.x() + dx * (box_w / 2 + 14)
                cy = pt.y() + dy * (box_h / 2 + 22) if dy else pt.y()
                cand = QRectF(cx - box_w / 2, cy - box_h / 2, box_w, box_h)
                if not bounds.contains(cand):
                    continue  # would be cut off by the map edge: try another side
                if not any(cand.adjusted(-3, -3, 3, 3).intersects(o) for o in placed):
                    box = cand
                    break
            if box is None:
                dots.append((pt, pin))
                continue
            placed.append(box)
            selected = pin.id == self.selected_photo_id
            accent = QColor("#F4D03F") if selected else QColor("#F39C12")
            # stem from the photo's spot to its card
            painter.setPen(QPen(accent, 1.6))
            painter.drawLine(pt, QPointF(box.center().x(), box.bottom() if box.center().y() < pt.y() else box.top()))
            painter.setBrush(QColor(11, 22, 16, 235))
            painter.setPen(QPen(accent, 2.4 if selected else 1.4))
            painter.drawRoundedRect(box, 4, 4)
            img_rect = QRectF(box.center().x() - w / 2, box.top() + 3, w, h)
            if pix is not None:
                painter.drawPixmap(img_rect.toRect(), pix)
            else:
                painter.setPen(QColor("#98B2A1"))
                painter.setFont(QFont("Sans Serif", 14))
                painter.drawText(img_rect, Qt.AlignmentFlag.AlignCenter, "📷")
            if label:
                painter.setFont(time_font)
                painter.setPen(QColor("#F4D03F"))
                painter.drawText(QRectF(box.left(), img_rect.bottom() + 1, box.width(), fm.height() + 2),
                                 Qt.AlignmentFlag.AlignCenter, label)
            painter.setPen(QPen(QColor("#FFFFFF"), 1.2))
            painter.setBrush(accent)
            painter.drawEllipse(pt, 4.0, 4.0)
            self._photo_hits.append((box, pin))
            self._photo_hits.append((QRectF(pt.x() - 6, pt.y() - 6, 12, 12), pin))
        for pt, pin in dots:
            painter.setPen(QPen(QColor("#FFFFFF"), 1.2))
            painter.setBrush(QColor("#F39C12"))
            painter.drawEllipse(pt, 5.0, 5.0)
            self._photo_hits.append((QRectF(pt.x() - 7, pt.y() - 7, 14, 14), pin))
        painter.restore()

    def _photo_at(self, pos: QPointF) -> Optional[TrailPhotoPin]:
        for rect, pin in reversed(self._photo_hits):  # last drawn = on top
            if rect.contains(pos):
                return pin
        return None

    def _draw_waypoints(self, painter: QPainter) -> None:
        for idx, (lat, lon) in enumerate(self.waypoints):
            pt = self.project(lat, lon)
            painter.setPen(QPen(QColor("#09180F"), 1.5))
            painter.setBrush(QBrush(QColor("#ECF5EE")))
            painter.drawEllipse(pt, 6.5, 6.5)
            painter.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            painter.drawText(QRectF(pt.x() - 6, pt.y() - 6, 12, 12), Qt.AlignmentFlag.AlignCenter, str(idx + 1))

    def _draw_highlight_cursor(self, painter: QPainter) -> None:
        if self.highlight_distance_m is None or not self.active_route:
            return
        coords = self.active_route.coordinates
        if not coords:
            return
        target_d = max(0.0, self.highlight_distance_m)
        cum = 0.0
        chosen = coords[0]
        for idx in range(1, len(coords)):
            seg_d = haversine_m(coords[idx - 1][0], coords[idx - 1][1], coords[idx][0], coords[idx][1])
            if cum + seg_d >= target_d and seg_d > 0:
                frac = (target_d - cum) / seg_d
                lat = coords[idx - 1][0] + frac * (coords[idx][0] - coords[idx - 1][0])
                lon = coords[idx - 1][1] + frac * (coords[idx][1] - coords[idx - 1][1])
                chosen = (lat, lon, coords[idx][2])
                break
            cum += seg_d
            chosen = coords[idx]

        pt = self.project(chosen[0], chosen[1])
        painter.setPen(QPen(QColor("#FFFFFF"), 2.2))
        painter.setBrush(QBrush(QColor("#F39C12")))
        painter.drawEllipse(pt, 8.5, 8.5)

    def _draw_scale_and_legend(self, painter: QPainter) -> None:
        painter.setFont(QFont("Sans Serif", 8))
        painter.setPen(QColor("#98B2A1"))
        painter.drawText(14, 20, "Click to add waypoint • Drag to pan • Scroll to zoom • Home to reset • Hover for details")

        # Scale bar (bottom-left), in the user's distance units
        ppm = self._px_per_m()
        if self.units == "metric":
            steps = [(m, f"{m:g} m") for m in (10, 20, 50, 100, 200, 500)] + [
                (k * 1000.0, f"{k:g} km") for k in (1, 2, 5, 10, 20, 50)]
        else:
            steps = [(f * 0.3048, f"{f:g} ft") for f in (50, 100, 200, 500, 1000)] + [
                (mi * 1609.344, f"{mi:g} mi") for mi in (0.25, 0.5, 1, 2, 5, 10, 20)]
        fitting = [(m * ppm, label) for m, label in steps if m * ppm <= 140]
        best = fitting[-1] if fitting else None
        y = self.height() - 18.0
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(11, 22, 16, 200))
        painter.drawRoundedRect(QRectF(8, y - 24, 200, 32), 4, 4)
        if best:
            px, label = best
            painter.setPen(QPen(QColor("#ECF5EE"), 2.0))
            painter.drawLine(QPointF(16, y), QPointF(16 + px, y))
            painter.drawLine(QPointF(16, y - 4), QPointF(16, y + 1))
            painter.drawLine(QPointF(16 + px, y - 4), QPointF(16 + px, y + 1))
            painter.drawText(QPointF(22 + px, y + 4), label)
        if self._contour_interval:
            painter.setPen(QColor("#B8A27A"))
            ci = self._contour_interval if self.units == "metric" else self._contour_interval * FT_PER_M
            painter.drawText(QPointF(16, y - 10), f"Contours every {ci:,.0f} {'m' if self.units == 'metric' else 'ft'}")

        # North arrow (top-right)
        nx, ny = self.width() - 22.0, 40.0
        painter.setPen(QPen(QColor("#ECF5EE"), 1.0))
        painter.setBrush(QColor("#ECF5EE"))
        painter.drawPolygon(QPolygonF([QPointF(nx, ny - 12), QPointF(nx - 5, ny), QPointF(nx + 5, ny)]))
        painter.drawText(QRectF(nx - 8, ny + 1, 16, 12), Qt.AlignmentFlag.AlignCenter, "N")

        if self._attribution:
            painter.setFont(QFont("Sans Serif", 7))
            painter.setPen(QColor(152, 178, 161, 200))
            painter.drawText(
                QRectF(0, self.height() - 16, self.width() - 10, 14),
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                self._attribution,
            )

    # ── Interaction ───────────────────────────────────────────────────────────

    def _feature_at(self, pos: QPointF) -> Optional[MapFeature]:
        level = self._detail_level()
        best, best_d = None, 10.0
        for f in self._point_features:
            if POINT_DETAIL[f.kind] > level:
                continue
            pt = self.project(f.coords[0][1], f.coords[0][0])
            d = math.hypot(pt.x() - pos.x(), pt.y() - pos.y())
            if d < best_d:
                best, best_d = f, d
        if best:
            return best
        lat, lon = self.unproject(pos)
        tol_deg = 6.0 / max(1e-9, self._view_transform().m11())
        best_d = 6.0
        for (a, b, c, d), f in self._hover_lines:
            if not (a - tol_deg <= lat <= c + tol_deg and b - tol_deg / self._kx <= lon <= d + tol_deg / self._kx):
                continue
            pts = [self.project(la, lo) for lo, la in f.coords]
            for p1, p2 in zip(pts, pts[1:]):
                vx, vy = p2.x() - p1.x(), p2.y() - p1.y()
                seg2 = vx * vx + vy * vy
                t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((pos.x() - p1.x()) * vx + (pos.y() - p1.y()) * vy) / seg2))
                dist = math.hypot(pos.x() - p1.x() - t * vx, pos.y() - p1.y() - t * vy)
                if dist < best_d:
                    best, best_d = f, dist
        return best

    def _tooltip_for(self, f: MapFeature) -> str:
        lines = [f"<b>{f.name}</b>" if f.name else ""]
        lines.append(KIND_LABELS.get(f.kind, f.kind.replace("_", " ").title()))
        if f.ele_m is not None:
            lines.append(self._fmt_ele(f.ele_m))
        if f.info:
            lines.append(f.info)
        return "<br>".join(x for x in lines if x)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self._press_pos = event.position()
        self._dragging = False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position()
        if self._press_pos is not None and event.buttons() != Qt.MouseButton.NoButton:
            delta = pos - self._press_pos
            if self._dragging or delta.manhattanLength() > 4:
                self._dragging = True
                self.pan_offset += delta
                self._press_pos = pos
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.update()
            return
        lat, lon = self.unproject(pos)
        self.hover_geo.emit(lat, lon)
        photo = self._photo_at(pos)
        if photo is not None:
            QToolTip.hideText()
            if self._preview is None:
                self._preview = PhotoPreviewPopup(self)
            self._preview.show_pin(photo, event.globalPosition().toPoint())
            return
        if self._preview is not None:
            self._preview.hide_preview()
        f = self._feature_at(pos) if (self._point_features or self._hover_lines) else None
        if f is not None:
            QToolTip.showText(event.globalPosition().toPoint(), self._tooltip_for(f), self)
        else:
            QToolTip.hideText()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        was_drag = self._dragging
        self._press_pos = None
        self._dragging = False
        self.unsetCursor()
        if was_drag or event.button() != Qt.MouseButton.LeftButton:
            return
        pos = event.position()
        # Check if user clicked a photo (thumbnail or marker) first
        photo = self._photo_at(pos)
        if photo is not None:
            self.photo_clicked.emit(photo.id)
            return
        lat, lon = self.unproject(pos)
        self.waypoint_clicked.emit(lat, lon)

    def leaveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._preview is not None:
            self._preview.hide_preview()
        super().leaveEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        pos = event.position()
        anchor_lat, anchor_lon = self.unproject(pos)
        if delta > 0:
            self.zoom = min(80.0, self.zoom * 1.2)
        else:
            self.zoom = max(0.25, self.zoom / 1.2)
        # Keep the point under the cursor fixed while zooming
        self.pan_offset += pos - self.project(anchor_lat, anchor_lon)
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Home, Qt.Key.Key_0):
            self.zoom = 1.0
            self.pan_offset = QPointF(0.0, 0.0)
            self.update()
        elif event.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self.zoom = min(80.0, self.zoom * 1.2)
            self.update()
        elif event.key() == Qt.Key.Key_Minus:
            self.zoom = max(0.25, self.zoom / 1.2)
            self.update()
        else:
            super().keyPressEvent(event)
