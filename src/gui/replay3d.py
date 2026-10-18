"""3D hike replay animation (software-rendered with QPainter).

A chase camera follows the hiker along the route over a shaded terrain mesh built from the
region's elevation model. A HUD shows the trail, distance, altitude, an elevation profile and a
mini map. When the hiker reaches a photo, the view zooms from the photo's spot into the photo
until it fills the frame, lingers, zooms back out and the hike continues.
"""
import math
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetricsF,
    QKeyEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src.gui.charts.photo_preview import format_datetime, load_photo_image, photo_pixmap
from src.hiking.models import ComposedRoute, DestinationPack, HikePhotoPin
from src.hiking.photo_matcher import dedupe_pins
from src.hiking.terrain3d import (
    LocalFrame,
    RoutePath,
    TerrainMesh,
    build_mesh,
    chase_camera,
    lerp_angle,
    route_path,
)
from src.hiking.units import format_distance, format_elevation

SKY_TOP = QColor(88, 140, 196)
SKY_HORIZON = (196, 214, 226)
VIEW_RADIUS_M = 4200.0  # terrain drawn around the hiker
FOG_START, FOG_END = 1800.0, 5200.0
Z_EXAGGERATION = 1.5

ZOOM_S = 1.4  # zoom into / out of a photo
LINGER_S = 5.0  # photo fills the frame this long
BASE_DURATION_S = 75.0  # whole route at 1x (photos excluded)


@dataclass
class PhotoStop:
    pin: HikePhotoPin
    along_m: float
    world: Tuple[float, float, float]
    pixmap: Optional[QPixmap]  # small (thumbnail) for the marker in the scene
    full: Optional[QPixmap] = None  # full-resolution image for the zoom, loaded in the background
    loading: Optional[Future] = None


def _ease(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3 - 2 * t)


class Replay3DView(QWidget):
    """The animation itself. Drive it with play()/pause()/seek(); it reports progress."""

    progress_changed = Signal(float)  # 0..1 along the route
    finished = Signal()
    state_changed = Signal(str)  # "playing" | "paused" | "photo" | "done"

    def __init__(self, route: ComposedRoute, pack: Optional[DestinationPack], photos: List[HikePhotoPin],
                 units: str = "us", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.route_model = route
        self.units = units
        self.speed = 1.0

        coords = route.coords_3d
        lats = [c[1] for c in coords]
        lons = [c[0] for c in coords]
        self.frame = LocalFrame(sum(lats) / len(lats), sum(lons) / len(lons), Z_EXAGGERATION)
        self.path: RoutePath = route_path(coords, self.frame)
        xs = [p[0] for p in self.path.pts]
        ys = [p[1] for p in self.path.pts]
        pad = VIEW_RADIUS_M + 800.0
        fallback = min(self.path.ele) if self.path.ele else 0.0
        self.mesh: TerrainMesh = build_mesh(
            pack.dem if pack else None, self.frame,
            (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad),
            cell_m=120.0, fallback_ele=fallback,
        )
        # Drape the trail slightly above the mesh so it stays visible on slopes
        self._trail = [(x, y, max(z, self.mesh.height_at(x, y)) + 6.0) for x, y, z in self.path.pts]
        self._trail_cells: Dict[Tuple[int, int], List[int]] = {}
        for i in range(len(self._trail) - 1):
            mx = (self._trail[i][0] + self._trail[i + 1][0]) / 2
            my = (self._trail[i][1] + self._trail[i + 1][1]) / 2
            self._trail_cells.setdefault(self.mesh.cell_of(mx, my), []).append(i)

        self.peaks: List[Tuple[str, float, float, float, float]] = []  # name, x, y, z, true ele
        if pack:
            for f in pack.features:
                if f.kind == "peak" and f.name and f.geom == "point":
                    x, y = self.frame.to_xy(f.coords[0][1], f.coords[0][0])
                    ele = f.ele_m if f.ele_m is not None else self.mesh.height_at(x, y) / Z_EXAGGERATION
                    self.peaks.append((f.name, x, y, self.mesh.height_at(x, y), ele))

        self.stops: List[PhotoStop] = []
        for pin in sorted(dedupe_pins(photos), key=lambda p: (p.distance_along_m, p.date_taken or 0)):
            x, y = self.frame.to_xy(pin.lat, pin.lon)
            self.stops.append(PhotoStop(pin, pin.distance_along_m, (x, y, self.mesh.height_at(x, y) + 6.0),
                                        photo_pixmap(pin, 300)))
        # Full-size photos are decoded off the GUI thread just ahead of each stop
        self._loader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="replay-photo")

        # Animation state
        self.dist = 0.0
        self.next_stop = 0
        self.phase = "paused"  # paused | playing | zoom_in | linger | zoom_out | done
        self.phase_t = 0.0
        self._resume_after_photo = True
        self.yaw = self.path.heading(0.0)
        self._last_tick = time.monotonic()
        self._stop_screen = QRectF()
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    # ── control ──────────────────────────────────────────────────────────

    @property
    def speed_mps(self) -> float:
        return max(1.0, self.path.length / BASE_DURATION_S) * self.speed

    def play(self) -> None:
        if self.phase == "done":
            self.seek(0.0)
        if self.phase in ("paused", "done"):
            self.phase = "playing"
        self._last_tick = time.monotonic()
        self._prefetch()
        self.timer.start()
        self.state_changed.emit("playing")

    def pause(self) -> None:
        self.timer.stop()
        if self.phase == "playing":
            self.phase = "paused"
        self.state_changed.emit("paused")
        self.update()

    def is_running(self) -> bool:
        return self.timer.isActive()

    def seek(self, fraction: float) -> None:
        self.dist = max(0.0, min(1.0, fraction)) * self.path.length
        self.next_stop = next((i for i, s in enumerate(self.stops) if s.along_m >= self.dist - 1.0), len(self.stops))
        if self.phase in ("zoom_in", "linger", "zoom_out", "done"):
            self.phase = "playing" if self.timer.isActive() else "paused"
        self.yaw = self.path.heading(self.dist)
        self.progress_changed.emit(self.dist / max(1.0, self.path.length))
        self.update()

    def skip_photo(self) -> None:
        if self.phase in ("zoom_in", "linger"):
            self.phase, self.phase_t = "zoom_out", 0.0

    # ── animation ────────────────────────────────────────────────────────

    def _full_size_px(self) -> int:
        """Longest side to decode photos at: the view (or screen) in device pixels."""
        dpr = self.devicePixelRatioF() or 1.0
        side = max(self.width(), self.height())
        screen = self.screen()
        if screen is not None:
            side = max(side, max(screen.size().width(), screen.size().height()))
        return int(min(3840, max(1280, side * dpr)))

    def _prefetch(self) -> None:
        """Keep the full-size image of the next two stops loading; free those already passed."""
        for i, stop in enumerate(self.stops):
            if i < self.next_stop - 1 or i > self.next_stop + 1:
                stop.full, stop.loading = None, None  # memory: only keep what's about to be shown
            elif stop.full is None and stop.loading is None:
                stop.loading = self._loader.submit(load_photo_image, stop.pin, self._full_size_px())

    def _full_pixmap(self, stop: PhotoStop) -> Optional[QPixmap]:
        """Full-size image if loaded (converted to a pixmap here, on the GUI thread)."""
        if stop.full is None and stop.loading is not None and stop.loading.done():
            img = stop.loading.result() if not stop.loading.cancelled() else None
            stop.full = QPixmap.fromImage(img) if img is not None else None
            stop.loading = None if stop.full is not None else stop.loading
        return stop.full

    def wait_for_photo(self, index: int, timeout: float = 10.0) -> Optional[QPixmap]:
        """Block until a stop's full image is loaded (tests and exports)."""
        stop = self.stops[index]
        if stop.full is None:
            if stop.loading is None:
                stop.loading = self._loader.submit(load_photo_image, stop.pin, self._full_size_px())
            stop.loading.result(timeout=timeout)
        return self._full_pixmap(stop)

    def shutdown(self) -> None:
        self.timer.stop()
        self._loader.shutdown(wait=False, cancel_futures=True)

    def advance(self, dt: float) -> None:
        """Advance the animation by dt seconds (also used by tests without a timer)."""
        self._prefetch()
        if self.phase == "playing":
            self.dist = min(self.path.length, self.dist + self.speed_mps * dt)
            target_yaw = self.path.heading(self.dist)
            self.yaw = lerp_angle(self.yaw, target_yaw, min(1.0, dt * 1.8))
            if self.next_stop < len(self.stops) and self.dist >= self.stops[self.next_stop].along_m:
                self.dist = max(self.dist, self.stops[self.next_stop].along_m)
                self.phase, self.phase_t = "zoom_in", 0.0
                self.state_changed.emit("photo")
            elif self.dist >= self.path.length:
                self.phase = "done"
                self.timer.stop()
                self.state_changed.emit("done")
                self.finished.emit()
            self.progress_changed.emit(self.dist / max(1.0, self.path.length))
        elif self.phase in ("zoom_in", "linger", "zoom_out"):
            # Photo time runs in real time regardless of the hiking speed
            self.phase_t += dt
            if self.phase == "zoom_in" and self.phase_t >= ZOOM_S:
                self.phase, self.phase_t = "linger", 0.0
            elif self.phase == "linger" and self.phase_t >= LINGER_S:
                self.phase, self.phase_t = "zoom_out", 0.0
            elif self.phase == "zoom_out" and self.phase_t >= ZOOM_S:
                self.next_stop += 1
                self.phase = "playing"
                self.state_changed.emit("playing")
        self.update()

    def _tick(self) -> None:
        now = time.monotonic()
        dt, self._last_tick = min(0.2, now - self._last_tick), now
        self.advance(dt)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Space:
            self.pause() if self.timer.isActive() else self.play()
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return) and self.phase in ("zoom_in", "linger"):
            self.skip_photo()
        else:
            super().keyPressEvent(event)

    # ── rendering ────────────────────────────────────────────────────────

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        w, h = self.width(), self.height()
        x, y, z, ele = self.path.at(self.dist)
        cam = chase_camera((x, y, max(z, self.mesh.height_at(x, y))), self.yaw, w, h,
                           ground_at=self.mesh.height_at)
        project = cam.projector()

        self._draw_sky(p, w, h)
        self._draw_terrain(p, cam, project)
        self._draw_peaks(p, project)
        self._draw_hiker(p, project, (x, y, max(z, self.mesh.height_at(x, y)) + 6.0))
        self._draw_hud(p, w, h, ele)

        if self.phase in ("zoom_in", "linger", "zoom_out") and self.next_stop < len(self.stops):
            self._draw_photo_zoom(p, w, h, project, self.stops[self.next_stop])
        p.end()

    def _draw_sky(self, p: QPainter, w: int, h: int) -> None:
        g = QLinearGradient(0, 0, 0, h * 0.6)
        g.setColorAt(0.0, SKY_TOP)
        g.setColorAt(1.0, QColor(*SKY_HORIZON))
        p.fillRect(self.rect(), QBrush(g))

    def _draw_terrain(self, p: QPainter, cam, project) -> None:
        mesh = self.mesh
        r_cells = int(VIEW_RADIUS_M / mesh.cell_m)
        hr, hc = mesh.cell_of(cam.x + math.cos(cam.yaw) * VIEW_RADIUS_M * 0.45,
                              cam.y + math.sin(cam.yaw) * VIEW_RADIUS_M * 0.45)
        r0, r1 = max(0, hr - r_cells), min(mesh.rows - 2, hr + r_cells)
        c0, c1 = max(0, hc - r_cells), min(mesh.cols - 2, hc + r_cells)
        proj_cache: Dict[Tuple[int, int], Optional[Tuple[float, float, float]]] = {}

        def pv(r: int, c: int):
            key = (r, c)
            if key not in proj_cache:
                vx, vy, vz = mesh.vertex(r, c)
                proj_cache[key] = project(vx, vy, vz)
            return proj_cache[key]

        cells = []
        w, h = self.width(), self.height()
        for r in range(r0, r1 + 1):
            for c in range(c0, c1 + 1):
                a, b, cc, d = pv(r, c), pv(r, c + 1), pv(r + 1, c + 1), pv(r + 1, c)
                if a is None or b is None or cc is None or d is None:
                    continue
                xs = (a[0], b[0], cc[0], d[0])
                ys = (a[1], b[1], cc[1], d[1])
                if max(xs) < 0 or min(xs) > w or max(ys) < 0 or min(ys) > h:
                    continue
                depth = (a[2] + b[2] + cc[2] + d[2]) / 4.0
                if depth > FOG_END + 600:
                    continue
                cells.append((depth, r, c, a, b, cc, d))
        cells.sort(key=lambda t: -t[0])  # far to near

        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        sky_r, sky_g, sky_b = SKY_HORIZON
        done_upto = self.path.at(self.dist)
        done_i = max(0, min(len(self._trail) - 2, int(self.dist / max(1.0, self.path.length) * (len(self._trail) - 1))))
        _ = done_upto
        trail_done_pen = QPen(QColor("#F39C12"), 3.2)
        trail_todo_pen = QPen(QColor(236, 245, 238, 220), 2.2)
        stop_cells = {}
        for i, s in enumerate(self.stops):
            stop_cells.setdefault(mesh.cell_of(s.world[0], s.world[1]), []).append(i)

        for depth, r, c, a, b, cc, d in cells:
            cr, cg, cb = mesh.color[r][c]
            f = 0.0 if depth <= FOG_START else min(1.0, (depth - FOG_START) / (FOG_END - FOG_START))
            col = QColor(int(cr + (sky_r - cr) * f), int(cg + (sky_g - cg) * f), int(cb + (sky_b - cb) * f))
            p.setPen(QPen(col, 1.0))
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(a[0], a[1]), QPointF(b[0], b[1]), QPointF(cc[0], cc[1]), QPointF(d[0], d[1])]))
            # Trail and photo markers on this cell are drawn now, so nearer terrain hides them
            for i in self._trail_cells.get((r, c), ()):
                s0 = project(*self._trail[i])
                s1 = project(*self._trail[i + 1])
                if s0 and s1:
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                    p.setPen(trail_done_pen if i < done_i else trail_todo_pen)
                    p.drawLine(QPointF(s0[0], s0[1]), QPointF(s1[0], s1[1]))
                    p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
            for si in stop_cells.get((r, c), ()):
                if si >= self.next_stop:
                    self._draw_photo_marker(p, project, self.stops[si])

    def _draw_photo_marker(self, p: QPainter, project, stop: PhotoStop) -> None:
        s = project(*stop.world)
        if not s:
            return
        size = max(10.0, min(46.0, 26000.0 / s[2]))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#F39C12"), 1.6))
        p.drawLine(QPointF(s[0], s[1]), QPointF(s[0], s[1] - size * 0.9))
        rect = QRectF(s[0] - size / 2, s[1] - size * 0.9 - size * 0.75, size, size * 0.75)
        if stop.pixmap is not None:
            p.drawPixmap(rect.toRect(), stop.pixmap)
        else:
            p.setBrush(QColor(11, 22, 16))
            p.drawRect(rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _draw_peaks(self, p: QPainter, project) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Sans Serif", 8, QFont.Weight.Bold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        placed: List[QRectF] = []
        for name, x, y, z, ele in self.peaks:
            s = project(x, y, z)
            if not s or s[2] > FOG_END or not (0 <= s[0] <= self.width() and 0 <= s[1] <= self.height()):
                continue
            label = f"{name}  {format_elevation(ele, self.units)}"
            rect = fm.boundingRect(label).translated(s[0] - fm.horizontalAdvance(label) / 2, s[1] - 14)
            if any(rect.intersects(o) for o in placed):
                continue
            placed.append(rect)
            p.setPen(QPen(QColor(255, 255, 255), 1.0))
            p.setBrush(QColor(255, 255, 255))
            p.drawPolygon(QPolygonF([QPointF(s[0], s[1] - 6), QPointF(s[0] - 4, s[1]), QPointF(s[0] + 4, s[1])]))
            p.setPen(QColor(20, 30, 24))
            p.drawText(rect.translated(1, 1), label)
            p.setPen(QColor(255, 255, 255))
            p.drawText(rect, label)

    def _draw_hiker(self, p: QPainter, project, pos: Tuple[float, float, float]) -> None:
        s = project(*pos)
        if not s:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pole_top = project(pos[0], pos[1], pos[2] + 110.0)
        if pole_top:
            p.setPen(QPen(QColor(255, 255, 255, 200), 2.0))
            p.drawLine(QPointF(s[0], s[1]), QPointF(pole_top[0], pole_top[1]))
        p.setPen(QPen(QColor("#FFFFFF"), 2.0))
        p.setBrush(QColor("#E74C3C"))
        p.drawEllipse(QPointF(s[0], s[1]), 7.0, 7.0)
        p.setBrush(QColor(231, 76, 60, 70))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(s[0], s[1]), 16.0, 16.0)

    def _draw_hud(self, p: QPainter, w: int, h: int, ele: float) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Top-left: trail, distance, altitude
        box = QRectF(14, 14, 330, 92)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(11, 22, 16, 200))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(QColor("#ECF5EE"))
        p.setFont(QFont("Sans Serif", 10, QFont.Weight.Bold))
        name = self.route_model.name
        fm = QFontMetricsF(p.font())
        p.drawText(QRectF(26, 20, 310, 22), Qt.AlignmentFlag.AlignLeft, fm.elidedText(name, Qt.TextElideMode.ElideRight, 306))
        p.setFont(QFont("Sans Serif", 9))
        p.setPen(QColor("#98B2A1"))
        p.drawText(QPointF(26, 62), f"{format_distance(self.dist, self.units)} of {format_distance(self.path.length, self.units)}")
        p.setFont(QFont("Sans Serif", 16, QFont.Weight.Bold))
        p.setPen(QColor("#F4D03F"))
        p.drawText(QPointF(26, 94), f"▲ {format_elevation(ele, self.units)}")

        self._draw_profile(p, w, h, ele)
        self._draw_minimap(p, w, h)

    def _draw_profile(self, p: QPainter, w: int, h: int, ele: float) -> None:
        rect = QRectF(14, h - 104, w - 28, 90)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(11, 22, 16, 190))
        p.drawRoundedRect(rect, 8, 8)
        inner = rect.adjusted(12, 10, -12, -16)
        eles = self.path.ele
        lo, hi = min(eles), max(eles)
        span = max(1.0, hi - lo)
        n = len(eles)
        step = max(1, n // 400)
        pts = [QPointF(inner.left() + inner.width() * self.path.cum[i] / max(1.0, self.path.length),
                       inner.bottom() - inner.height() * (eles[i] - lo) / span) for i in range(0, n, step)]
        area = QPainterPath(QPointF(inner.left(), inner.bottom()))
        for pt in pts:
            area.lineTo(pt)
        area.lineTo(QPointF(inner.right(), inner.bottom()))
        area.closeSubpath()
        p.setBrush(QColor(46, 204, 113, 70))
        p.setPen(QPen(QColor("#2ECC71"), 1.6))
        p.drawPath(area)
        for s in self.stops:
            sx = inner.left() + inner.width() * s.along_m / max(1.0, self.path.length)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#F39C12"))
            p.drawEllipse(QPointF(sx, inner.bottom() + 7), 3.0, 3.0)
        cx = inner.left() + inner.width() * self.dist / max(1.0, self.path.length)
        cy = inner.bottom() - inner.height() * (ele - lo) / span
        p.setPen(QPen(QColor("#F4D03F"), 1.4, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(cx, inner.top()), QPointF(cx, inner.bottom()))
        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
        p.setBrush(QColor("#E74C3C"))
        p.drawEllipse(QPointF(cx, cy), 5.0, 5.0)
        p.setPen(QColor("#98B2A1"))
        p.setFont(QFont("Sans Serif", 7))
        p.drawText(QPointF(inner.left(), rect.top() + 9), format_elevation(hi, self.units))
        p.drawText(QPointF(inner.left(), inner.bottom() + 14), format_elevation(lo, self.units))

    def _draw_minimap(self, p: QPainter, w: int, h: int) -> None:
        size = 170.0
        rect = QRectF(w - size - 14, 14, size, size)
        p.setPen(QPen(QColor(236, 245, 238, 120), 1.0))
        p.setBrush(QColor(11, 22, 16, 200))
        p.drawRoundedRect(rect, 8, 8)
        xs = [q[0] for q in self.path.pts]
        ys = [q[1] for q in self.path.pts]
        span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
        inner = rect.adjusted(14, 14, -14, -14)
        sc = inner.width() / span
        ox = inner.center().x() - (min(xs) + max(xs)) / 2 * sc
        oy = inner.center().y() + (min(ys) + max(ys)) / 2 * sc

        def m(x: float, y: float) -> QPointF:
            return QPointF(ox + x * sc, oy - y * sc)

        step = max(1, len(self.path.pts) // 300)
        path = QPainterPath(m(*self.path.pts[0][:2]))
        for q in self.path.pts[::step]:
            path.lineTo(m(q[0], q[1]))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(QColor(236, 245, 238, 160), 2.0))
        p.drawPath(path)
        x, y, _z, _e = self.path.at(self.dist)
        here = m(x, y)
        # view direction wedge
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(244, 208, 63, 90))
        wedge = QPainterPath(here)
        for da in (-0.45, 0.45):
            wedge.lineTo(QPointF(here.x() + 26 * math.cos(self.yaw + da), here.y() - 26 * math.sin(self.yaw + da)))
        wedge.closeSubpath()
        p.drawPath(wedge)
        p.setPen(QPen(QColor("#FFFFFF"), 1.5))
        p.setBrush(QColor("#E74C3C"))
        p.drawEllipse(here, 5.0, 5.0)
        p.setPen(QColor("#ECF5EE"))
        p.setFont(QFont("Sans Serif", 8, QFont.Weight.Bold))
        p.drawText(QRectF(rect.right() - 22, rect.top() + 4, 16, 14), Qt.AlignmentFlag.AlignCenter, "N")

    def _draw_photo_zoom(self, p: QPainter, w: int, h: int, project, stop: PhotoStop) -> None:
        """Zoom from the photo's spot on the terrain to fill the frame (and back)."""
        if self.phase == "zoom_in":
            t = _ease(self.phase_t / ZOOM_S)
        elif self.phase == "zoom_out":
            t = 1.0 - _ease(self.phase_t / ZOOM_S)
        else:
            t = 1.0
        s = project(*stop.world)
        start_c = QPointF(s[0], s[1] - 30) if s else QPointF(w / 2, h / 2)
        # Full-size photo once loaded (usually well before the stop); the thumbnail meanwhile
        pix = self._full_pixmap(stop) or stop.pixmap
        aspect = (pix.width() / pix.height()) if pix is not None and pix.height() else 1.5
        full_w, full_h = (w, w / aspect) if w / h < aspect else (h * aspect, h)
        start_w = 46.0
        cur_w = start_w + (full_w - start_w) * t
        cur_h = cur_w / aspect
        cx = start_c.x() + (w / 2 - start_c.x()) * t
        cy = start_c.y() + (h / 2 - start_c.y()) * t
        target = QRectF(cx - cur_w / 2, cy - cur_h / 2, cur_w, cur_h)

        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.fillRect(self.rect(), QColor(5, 10, 7, int(235 * t)))
        if pix is not None:
            p.drawPixmap(target, pix, QRectF(pix.rect()))
        else:
            p.setBrush(QColor(11, 22, 16))
            p.setPen(QPen(QColor("#F39C12"), 2.0))
            p.drawRect(target)
            p.setPen(QColor("#98B2A1"))
            p.setFont(QFont("Sans Serif", max(8, int(28 * t))))
            p.drawText(target, Qt.AlignmentFlag.AlignCenter, "📷")
        if t > 0.85:
            alpha = int(255 * (t - 0.85) / 0.15)
            band = QRectF(0, h - 86, w, 72)
            p.fillRect(band, QColor(0, 0, 0, int(alpha * 0.6)))
            p.setPen(QColor(244, 208, 63, alpha))
            p.setFont(QFont("Sans Serif", 13, QFont.Weight.Bold))
            p.drawText(band.adjusted(24, 8, -24, -36), Qt.AlignmentFlag.AlignLeft, format_datetime(stop.pin))
            p.setPen(QColor(236, 245, 238, alpha))
            p.setFont(QFont("Sans Serif", 11))
            caption = stop.pin.caption or stop.pin.ai_summary or stop.pin.filename
            if stop.pin.people:
                caption += "   👥 " + ", ".join(stop.pin.people)
            p.drawText(band.adjusted(24, 36, -24, -6), Qt.AlignmentFlag.AlignLeft, caption)


class Replay3DDialog(QDialog):
    """Window with the 3D replay and its playback controls."""

    def __init__(self, route: ComposedRoute, pack: Optional[DestinationPack], photos: List[HikePhotoPin],
                 units: str = "us", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"3D Hike Replay — {route.name}")
        self.resize(1280, 800)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        self.view = Replay3DView(route, pack, photos, units)
        col.addWidget(self.view, 1)

        bar = QHBoxLayout()
        self.play_btn = QPushButton("⏸ Pause")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.clicked.connect(self._toggle)
        self.skip_btn = QPushButton("Skip Photo ⏭")
        self.skip_btn.setEnabled(False)
        self.skip_btn.clicked.connect(self.view.skip_photo)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(lambda v: self.view.seek(v / 1000.0))
        self.speed_combo = QComboBox()
        for label, val in (("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("4×", 4.0)):
            self.speed_combo.addItem(label, val)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.currentIndexChanged.connect(lambda _i: setattr(self.view, "speed", float(self.speed_combo.currentData())))
        bar.addWidget(self.play_btn)
        bar.addWidget(self.skip_btn)
        bar.addWidget(self.slider, 1)
        bar.addWidget(QLabel("Speed:"))
        bar.addWidget(self.speed_combo)
        hint = QLabel("Space: play/pause · →: skip photo")
        hint.setStyleSheet("color: #98B2A1; font-size: 8.5pt;")
        bar.addWidget(hint)
        col.addLayout(bar)

        self.view.progress_changed.connect(self._on_progress)
        self.view.state_changed.connect(self._on_state)
        self.view.setFocus()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        QTimer.singleShot(400, self.view.play)  # start once the window is on screen

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self.view.shutdown()
        super().closeEvent(event)

    def done(self, result: int) -> None:  # Esc / reject also stops loading photos
        self.view.shutdown()
        super().done(result)

    def _toggle(self) -> None:
        self.view.pause() if self.view.is_running() else self.view.play()

    def _on_progress(self, frac: float) -> None:
        if not self.slider.isSliderDown():
            self.slider.blockSignals(True)
            self.slider.setValue(int(frac * 1000))
            self.slider.blockSignals(False)

    def _on_state(self, state: str) -> None:
        running = self.view.is_running()
        self.play_btn.setText("⏸ Pause" if running else ("↺ Replay" if state == "done" else "▶ Play"))
        self.skip_btn.setEnabled(state == "photo")
