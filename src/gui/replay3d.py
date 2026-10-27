"""3D hike replay animation (software-rendered with QPainter).

A chase camera follows the hiker along the route over a shaded terrain mesh built from the
region's elevation model. A HUD shows the trail, distance, altitude, an elevation profile and a
mini map. When the hiker reaches a photo, the view zooms from the photo's spot into the photo
until it fills the frame, lingers, zooms back out and the hike continues.
"""
import math
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from operator import itemgetter
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QLineF, QPointF, QRectF, QSize, Qt, QTimer, Signal
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
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import (
    QCheckBox,
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
from src.hiking.daylight import Light, hike_clock, key_index, light_at, light_keys
from src.hiking.hike_report import hike_details
from src.hiking.mood import NEUTRAL, Mood, mix_rgb, mood_at, stop_mood
from src.hiking.models import ComposedRoute, DestinationPack, HikePhotoPin, HikeSession
from src.hiking.photo_matcher import dedupe_pins
from src.hiking.replay_plan import plan_photo_stops
from src.hiking.terrain3d import (
    LocalFrame,
    RoutePath,
    TerrainMesh,
    blend_cameras,
    build_mesh,
    chase_camera,
    lerp_angle,
    look_camera,
    replay_map_layers,
    route_path,
    shade_mesh,
    smoothstep,
)
from src.hiking.units import format_distance, format_elevation
from src.hiking.vegetation import build_vegetation
from src.hiking.weather_fx import CLOUD_TEX_M, CLOUD_TEX_N, cloud_noise, cloud_shadow_cover, weather_track
from src.gui.vegetation_sprites import GROUND_FRAC, make_sprites

VIEW_RADIUS_M = 4200.0  # terrain drawn around the hiker
SWAY_MIN_PX, SWAY_FAR = 14.0, 1600.0  # plants this tall on screen and this near bend in the wind
PRECIP_MAX = 520
CLOUD_PUFFS = 16  # clouds in the sky when overcast; fewer as it clears
CLOUD_SKY_M = 22000.0  # how far out the sky's clouds hang  # rain streaks / snowflakes on screen at full intensity
FOG_END = 5200.0  # a clear day's haze; the hike's weather closes it in (Light.fog_start / fog_end)
Z_EXAGGERATION = 1.5
# Map layers on the terrain: kind -> (rgb, pen width, fades out beyond this depth in metres)
LAYER_PENS = {
    "water_edge": ((76, 155, 214), 1.6, FOG_END),
    "river": ((76, 155, 214), 2.8, FOG_END),
    "stream": ((110, 182, 232), 1.3, 3600.0),
    "trail": ((214, 178, 112), 1.8, 4200.0),  # tan: apart from the hike's own white / orange line
    "path": ((196, 168, 120), 1.0, 2400.0),  # dense: only near the hiker
}

# Vegetation: plants are drawn taller than life so they read from the chase camera (~1.5 km back)
VEG_SCALE = 1.6
VEG_BOOST = {"krummholz": 2.0, "shrub": 2.4, "sage": 2.4, "grass": 4.0, "boulder": 1.6}  # small plants, more so
SMALL_PLANTS = frozenset(VEG_BOOST)
STILL_PLANTS = frozenset({"boulder"})  # don't sway
VEG_NEAR, VEG_FAR = 1300.0, 2700.0  # plants sorted back to front within this / none beyond
VEG_MIN_PX, VEG_FULL_PX = 1.5, 6.0  # a plant fades in from this drawn height to fully opaque at that

BASE_DURATION_S = 75.0  # whole route at 1x (photos excluded)

# Smooth photo stops: slow down and turn toward the photo, blend it in, cross-fade through the
# stop's photos, blend back out, turn back to the trail and speed up again.
APPROACH_M = 300.0  # start slowing / turning toward a stop this far before it
DEPART_M = 250.0  # speed back up / turn back to the trail over this distance after it
EASE_SHARE = 0.06  # on short hikes both shrink to this share of the route, so it doesn't crawl throughout
MIN_SPEED_FACTOR = 0.12  # crawl speed right at a stop (never a dead stop)
BLEND_IN_S = 1.0  # photo grows from its spot and fades in while the scene blurs
BLEND_OUT_S = 0.9
FIRST_HOLD_S = 3.0  # first photo of a stop holds this long…
NEXT_HOLD_S = 1.5  # …further photos of the same stop
XFADE_S = 0.5  # cross-fade between photos of one stop
LINGER_S = FIRST_HOLD_S  # (kept for callers/tests)


@dataclass
class PhotoItem:
    pin: HikePhotoPin
    pixmap: Optional[QPixmap]  # thumbnail (scene marker, placeholder until the full image loads)
    full: Optional[QPixmap] = None  # full-resolution image, loaded in the background
    loading: Optional[Future] = None


@dataclass
class PhotoStop:
    """One stop of the replay: one or more photos taken close together."""

    items: List[PhotoItem]
    along_m: float
    world: Tuple[float, float, float]  # where the first photo was taken
    hidden: int = 0  # further photos here not shown ("+N more")

    @property
    def pin(self) -> HikePhotoPin:
        return self.items[0].pin

    @property
    def pixmap(self) -> Optional[QPixmap]:
        return self.items[0].pixmap

    def hold_s(self, k: int) -> float:
        return FIRST_HOLD_S if k == 0 else NEXT_HOLD_S


def _cloud_sprite(variant: int, white: int) -> QPixmap:
    """A soft cloud: overlapping round puffs, flat-bottomed, fading at the edges; `white` 255 for a
    fair-weather cloud, lower for a grey one."""
    rng = random.Random(100 + variant)
    pix = QPixmap(320, 140)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setPen(Qt.PenStyle.NoPen)
    for _ in range(14):
        cx, r = rng.uniform(60, 260), rng.uniform(26, 52)
        cy = 112 - r * rng.uniform(0.5, 1.0)
        g = QRadialGradient(QPointF(cx, cy), r)
        g.setColorAt(0.0, QColor(white, white, min(255, white + 4), 200))
        g.setColorAt(0.6, QColor(white - 7, white - 5, white - 3, 120))
        g.setColorAt(1.0, QColor(white - 15, white - 11, white - 7, 0))
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), r, r)
    p.end()
    return pix


def _ease(t: float) -> float:
    return smoothstep(t)


class Replay3DView(QWidget):
    """The animation itself. Drive it with play()/pause()/seek(); it reports progress."""

    progress_changed = Signal(float)  # 0..1 along the route
    finished = Signal()
    state_changed = Signal(str)  # "playing" | "paused" | "photo" | "done"

    def __init__(self, route: ComposedRoute, pack: Optional[DestinationPack], photos: List[HikePhotoPin],
                 units: str = "us", parent: Optional[QWidget] = None, month: Optional[int] = None,
                 session: Optional[HikeSession] = None) -> None:
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
        # Other trails, paths, streams, rivers and lakes from the region's map
        self.layers = replay_map_layers(pack, self.mesh, route.trail_ids)
        # The region's life zones and land cover colour the terrain and grow its plants; the hike's
        # month (else its first photo's) sets the foliage
        if month is None:
            taken = [p.date_taken for p in photos if p.date_taken]
            month = datetime.fromtimestamp(min(taken), tz=timezone.utc).month if taken else None
        self.veg = build_vegetation(pack, self.mesh, self.path.pts, month)
        self._shader = shade_mesh(self.mesh, self.veg.ground_rgb)
        # Time of day: the sun where it stood as the hiker got there. The terrain is re-lit at a few
        # keyframes along the hike (cached); sky, fog and tint blend between them every frame
        self._clock = hike_clock(self.path.length, photos, session)
        self.weather = weather_track(hike_details(session)) if session else None
        self.light_keys: List[Light] = light_keys(self.path.length, self.frame.lat0, self.frame.lon0, self._clock,
                                                  self.weather)
        self._lit: Dict[int, list] = {}
        self._sway: Tuple[float, float, float, float, float] = (0.0, 0.0, 0.0, 1.0, 0.0)
        rng = random.Random(7)  # the same drops and clouds every time
        self._precip_seeds = [(rng.random(), rng.random(), rng.random()) for _ in range(PRECIP_MAX)]
        # Clouds: a drifting field whose shadows pass over the terrain, and puffs in the sky
        self._cloud_field = cloud_noise()
        self._cloud_puffs = [(rng.uniform(0, 2 * math.pi), rng.uniform(0.6, 4.5), rng.uniform(0.7, 1.5), rng.randrange(3))
                             for _ in range(CLOUD_PUFFS)]
        self._cloud_sprites = [_cloud_sprite(k, 255) for k in range(3)]
        self._cloud_sprites_grey = [_cloud_sprite(k, 168) for k in range(3)]  # heavy, rain-bearing
        self._lit_idx = -1
        self.light: Light = self.light_keys[0]
        self._sprites = make_sprites(self.veg.season, self.veg.biome.rock)
        self._plant_cells: Dict[Tuple[int, int], list] = {}
        self._plant_tallest: Dict[Tuple[int, int], float] = {}  # per cell: drawn height of its tallest plant
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
        for plan in plan_photo_stops(dedupe_pins(photos)):
            first = plan.shown[0]
            x, y = self.frame.to_xy(first.lat, first.lon)
            items = [PhotoItem(pin, photo_pixmap(pin, 300)) for pin in plan.shown]
            self.stops.append(PhotoStop(items, plan.along_m, (x, y, self.mesh.height_at(x, y) + 6.0), plan.hidden))
        self.photo_stops = self.stops  # all of them; `stops` is empty while photos are hidden
        # How the hike felt there: each stop's photos' mood grades the scene around it
        self._moods = [(st.along_m, stop_mood([it.pin.ai_mood for it in st.items])) for st in self.photo_stops]
        self.mood: Tuple[Mood, float] = (NEUTRAL, 0.0)
        # Full-size photos are decoded off the GUI thread just ahead of being shown
        self._loader = ThreadPoolExecutor(max_workers=2, thread_name_prefix="replay-photo")

        # Animation state
        self.dist = 0.0
        self.next_stop = 0  # the stop being approached / shown
        self.phase = "paused"  # paused | playing | blend_in | hold | blend_out | done
        self.phase_t = 0.0
        self.photo_k = 0  # photo of the current stop being held
        self.yaw = self.path.heading(0.0)
        self._last_tick = time.monotonic()
        self._frame: Optional[QPixmap] = None
        self._frame_key: Optional[tuple] = None  # what _frame shows; reused while a photo is up
        self._soft: Optional[QPixmap] = None  # blurred copy of _frame behind the photo
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self._tick)

    # ── control ──────────────────────────────────────────────────────────

    PHOTO_PHASES = ("blend_in", "hold", "blend_out")

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
        self.state_changed.emit("photo" if self.phase in self.PHOTO_PHASES else "playing")

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
        if self.phase in (*self.PHOTO_PHASES, "done"):
            self.phase = "playing" if self.timer.isActive() else "paused"
        self.photo_k = 0
        self.yaw = self.path.heading(self.dist)
        self.progress_changed.emit(self.dist / max(1.0, self.path.length))
        self.update()

    @property
    def show_photos(self) -> bool:
        return self.stops is self.photo_stops

    def set_show_photos(self, show: bool) -> None:
        """Show photos (stop, zoom in, linger) or hike straight through with no markers."""
        if show == self.show_photos:
            return
        if self.phase in self.PHOTO_PHASES:  # leave the photo being shown at once
            self.phase = "playing" if self.timer.isActive() else "paused"
            self.state_changed.emit(self.phase)
        self.stops = self.photo_stops if show else []
        self.next_stop = next((i for i, s in enumerate(self.stops) if s.along_m >= self.dist - 1.0), len(self.stops))
        self.photo_k = 0
        self._prefetch()
        self.update()

    def skip_photo(self) -> None:
        """Leave the current stop (blending out from wherever it is)."""
        if self.phase == "blend_in":
            # reverse from the current point of the blend so it doesn't jump
            done = min(1.0, self.phase_t / BLEND_IN_S)
            self.phase, self.phase_t = "blend_out", (1.0 - done) * BLEND_OUT_S
        elif self.phase == "hold":
            self.phase, self.phase_t = "blend_out", 0.0

    # ── animation ────────────────────────────────────────────────────────

    def _full_size_px(self) -> int:
        """Longest side to decode photos at: the view (or screen) in device pixels."""
        dpr = self.devicePixelRatioF() or 1.0
        side = max(self.width(), self.height())
        screen = self.screen()
        if screen is not None:
            side = max(side, max(screen.size().width(), screen.size().height()))
        return int(min(3840, max(1280, side * dpr)))

    def _upcoming_items(self) -> List[PhotoItem]:
        """Photos about to be shown: the current one, the next in this stop, the next stop's first."""
        out: List[PhotoItem] = []
        if self.next_stop < len(self.stops):
            items = self.stops[self.next_stop].items
            out += items[self.photo_k:self.photo_k + 2]
        if self.next_stop + 1 < len(self.stops):
            out.append(self.stops[self.next_stop + 1].items[0])
        return out

    def _prefetch(self) -> None:
        """Keep the next few full-size photos loading; free the ones no longer needed."""
        keep = {id(it) for it in self._upcoming_items()}
        if self.next_stop >= 1 and self.next_stop - 1 < len(self.stops):
            keep.update(id(it) for it in self.stops[self.next_stop - 1].items[-1:])  # blending out
        for stop in self.photo_stops:  # all stops, so hiding photos frees them too
            for it in stop.items:
                if id(it) not in keep:
                    it.full, it.loading = None, None  # memory: only what's about to be shown
                elif it.full is None and it.loading is None:
                    it.loading = self._loader.submit(load_photo_image, it.pin, self._full_size_px())

    def _full_pixmap(self, item) -> Optional[QPixmap]:
        """Full-size image if loaded (converted to a pixmap here, on the GUI thread)."""
        if isinstance(item, PhotoStop):
            item = item.items[0]
        if item.full is None and item.loading is not None and item.loading.done():
            img = item.loading.result() if not item.loading.cancelled() else None
            item.full = QPixmap.fromImage(img) if img is not None else None
            item.loading = None if item.full is not None else item.loading
        return item.full

    def wait_for_photo(self, index: int, timeout: float = 10.0, k: int = 0) -> Optional[QPixmap]:
        """Block until a stop's full image is loaded (tests and exports)."""
        item = self.stops[index].items[k]
        if item.full is None:
            if item.loading is None:
                item.loading = self._loader.submit(load_photo_image, item.pin, self._full_size_px())
            item.loading.result(timeout=timeout)
        return self._full_pixmap(item)

    def shutdown(self) -> None:
        self.timer.stop()
        self._loader.shutdown(wait=False, cancel_futures=True)

    def _ease_m(self, metres: float) -> float:
        return min(metres, max(40.0, self.path.length * EASE_SHARE))

    def _approach(self) -> float:
        """0 far from the next stop … 1 right at it (eased)."""
        if self.next_stop >= len(self.stops):
            return 0.0
        rem = self.stops[self.next_stop].along_m - self.dist
        return 1.0 - smoothstep(rem / self._ease_m(APPROACH_M)) if rem >= 0 else 1.0

    def _departure(self) -> float:
        """1 right after leaving the previous stop … 0 once back up to speed (eased)."""
        if self.next_stop == 0:
            return 0.0
        prev = self.stops[min(self.next_stop, len(self.stops)) - 1]
        gone = self.dist - prev.along_m
        return 1.0 - smoothstep(gone / self._ease_m(DEPART_M)) if gone >= 0 else 0.0

    def speed_factor(self) -> float:
        """Walking speed multiplier: eases down into a stop and back up after it (never zero)."""
        slow = max(self._approach(), self._departure())
        mood, w = mood_at(self._moods, self.dist)  # calm and grand moments are walked more slowly
        return max(MIN_SPEED_FACTOR, (1.0 - slow * (1.0 - MIN_SPEED_FACTOR)) * (1.0 - mood.slow * w))

    def advance(self, dt: float) -> None:
        """Advance the animation by dt seconds (also used by tests without a timer)."""
        self._prefetch()
        if self.phase == "playing":
            self.dist = min(self.path.length, self.dist + self.speed_mps * self.speed_factor() * dt)
            target_yaw = self.path.heading(self.dist)
            self.yaw = lerp_angle(self.yaw, target_yaw, min(1.0, dt * 1.8))
            if self.next_stop < len(self.stops) and self.dist >= self.stops[self.next_stop].along_m:
                self.phase, self.phase_t, self.photo_k = "blend_in", 0.0, 0
                self.state_changed.emit("photo")
            elif self.dist >= self.path.length:
                self.phase = "done"
                self.timer.stop()
                self.state_changed.emit("done")
                self.finished.emit()
            self.progress_changed.emit(self.dist / max(1.0, self.path.length))
        elif self.phase in self.PHOTO_PHASES:
            # Photo time runs in real time regardless of the hiking speed
            stop = self.stops[self.next_stop]
            self.phase_t += dt
            if self.phase == "blend_in" and self.phase_t >= BLEND_IN_S:
                self.phase, self.phase_t = "hold", 0.0
            elif self.phase == "hold" and self.phase_t >= stop.hold_s(self.photo_k):
                if self.photo_k + 1 < len(stop.items):
                    self.photo_k, self.phase_t = self.photo_k + 1, 0.0  # cross-fade already happened
                else:
                    self.phase, self.phase_t = "blend_out", 0.0
            elif self.phase == "blend_out" and self.phase_t >= BLEND_OUT_S:
                self.next_stop += 1
                self.photo_k = 0
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
        elif event.key() in (Qt.Key.Key_Right, Qt.Key.Key_Return) and self.phase in ("blend_in", "hold"):
            self.skip_photo()
        else:
            super().keyPressEvent(event)

    # ── rendering ────────────────────────────────────────────────────────

    def _camera(self, w: int, h: int):
        """Chase camera, blended toward a closer view of the photo spot around stops."""
        x, y, z, _ele = self.path.at(self.dist)
        hiker = (x, y, max(z, self.mesh.height_at(x, y)))
        mood, mw = mood_at(self._moods, self.dist)  # dramatic moments: pull back for the wider view
        chase = chase_camera(hiker, self.yaw, w, h, back_m=1300.0 + 600.0 * mood.wide * mw,
                             up_m=600.0 + 200.0 * mood.wide * mw, ground_at=self.mesh.height_at)
        if self.phase in self.PHOTO_PHASES:
            weight, stop = 1.0, self.stops[self.next_stop]
        else:
            a, d = self._approach(), self._departure()
            if a >= d and self.next_stop < len(self.stops):
                weight, stop = a, self.stops[self.next_stop]
            elif d > 0:
                weight, stop = d, self.stops[self.next_stop - 1]
            else:
                return chase
        if weight <= 0.0:
            return chase
        look = look_camera(hiker, stop.world, self.yaw, w, h, ground_at=self.mesh.height_at)
        return blend_cameras(chase, look, weight)

    def paintEvent(self, event: QPaintEvent) -> None:
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF() or 1.0
        if self._frame is None or self._frame.size() != QSize(int(w * dpr), int(h * dpr)):
            self._frame = QPixmap(int(w * dpr), int(h * dpr))
            self._frame.setDevicePixelRatio(dpr)
            self._frame_key = None
        x, y, z, ele = self.path.at(self.dist)
        cam = self._camera(w, h)
        project = cam.projector()

        # The camera stands still while a photo is up: draw the scene once, not every frame
        key = (self.dist, self.yaw, self.next_stop, self.phase in self.PHOTO_PHASES, self.show_photos)
        if key != self._frame_key:
            self._update_light()
            self._sway = self._sway_now()
            self.mood = mood_at(self._moods, self.dist)
            scene = QPainter(self._frame)
            self._draw_sky(scene, w, h, project, cam)
            self._draw_terrain(scene, cam, project)
            self._draw_tint(scene)
            self._draw_weather(scene, w, h)
            self._draw_mood(scene, w, h)
            self._draw_peaks(scene, project)
            self._draw_hiker(scene, project, (x, y, max(z, self.mesh.height_at(x, y)) + 6.0))
            self._draw_hud(scene, w, h, ele)
            scene.end()
            self._frame_key, self._soft = key, None

        p = QPainter(self)
        p.drawPixmap(0, 0, self._frame)
        if self.phase in self.PHOTO_PHASES and self.next_stop < len(self.stops):
            self._draw_photo_overlay(p, w, h, project, self.stops[self.next_stop])
        p.end()

    def _update_light(self) -> None:
        """Light at the hiker; the terrain takes the nearest keyframe's shading."""
        self.light = light_at(self.light_keys, self.dist)
        idx = key_index(self.light_keys, self.dist)
        if idx != self._lit_idx:
            if idx not in self._lit:
                k = self.light_keys[idx]
                self._lit[idx] = self._shader.colors(k.sun, k.ambient, k.diffuse, k.snow)
            self.mesh.color = self._lit[idx]
            self._lit_idx = idx

    def _draw_sky(self, p: QPainter, w: int, h: int, project, cam) -> None:
        light = self.light
        g = QLinearGradient(0, 0, 0, h * 0.6)
        g.setColorAt(0.0, QColor(*light.sky_top))
        g.setColorAt(1.0, QColor(*light.sky_horizon))
        p.fillRect(self.rect(), QBrush(g))
        if light.elevation > -3.0 and light.cloud < 0.9:  # the sun's glow, hidden by the terrain drawn over it
            sx, sy, sz = light.sun
            far = 30000.0
            s = project(cam.x + sx * far, cam.y + sy * far, cam.z + max(sz, 0.0) * far)
            if s:
                warm = max(0.0, min(1.0, 1.0 - light.elevation / 25.0))
                core = QColor(255, int(246 - 40 * warm), int(225 - 95 * warm), int(235 * (1.0 - light.cloud)))
                glow = QRadialGradient(QPointF(s[0], s[1]), 70.0 + 50.0 * warm)
                glow.setColorAt(0.0, core)
                glow.setColorAt(0.12, core)
                edge = QColor(core)
                edge.setAlpha(0)
                glow.setColorAt(1.0, edge)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QBrush(glow))
                p.drawEllipse(QPointF(s[0], s[1]), 70.0 + 50.0 * warm, 70.0 + 50.0 * warm)
        self._draw_clouds(p, project, cam)

    def _draw_clouds(self, p: QPainter, project, cam) -> None:
        """Clouds hanging low over the horizon all around, drifting with the wind; the terrain drawn
        afterwards hides those behind the hills. Overcast skies get more and greyer ones."""
        light = self.light
        n = int(round(CLOUD_PUFFS * min(1.0, light.cloud * 1.2)))
        if n == 0:
            return
        drift = time.monotonic() * (0.002 + 0.00012 * light.wind_kmh)  # radians: the sky turns slowly
        sprites = self._cloud_sprites_grey if light.cloud > 0.7 or light.rain > 0.2 else self._cloud_sprites
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        for a0, el, size, k in self._cloud_puffs[:n]:
            a = a0 + drift
            d = CLOUD_SKY_M
            s = project(cam.x + math.cos(a) * d, cam.y + math.sin(a) * d, cam.z + d * math.tan(math.radians(el)))
            if not s:
                continue
            w = cam.focal * 6000.0 * size / s[2]
            h = w * 140.0 / 320.0
            if s[0] + w < 0 or s[0] - w > self.width() or s[1] < -h:
                continue
            p.setOpacity(0.55 + 0.4 * light.cloud)
            p.drawPixmap(QRectF(s[0] - w / 2, s[1] - h * 0.8, w, h), sprites[k], QRectF(0, 0, 320, 140))
        p.setOpacity(1.0)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)

    def _sway_now(self) -> Tuple[float, float, float, float, float]:
        """(clock, lean, sway amplitude, downwind x, y) for the plants this frame: they lean with the wind
        across the view and sway more the stronger it blows."""
        light = self.light
        to = math.radians(light.wind_from + 180.0)
        dx, dy = math.sin(to), math.cos(to)
        across = dx * math.sin(self.yaw) - dy * math.cos(self.yaw)
        strength = max(0.12, min(1.0, light.wind_kmh / 40.0))
        return time.monotonic(), 0.07 * strength * across, 0.035 * strength, dx, dy

    def _draw_weather(self, p: QPainter, w: int, h: int) -> None:
        """Falling rain or snow, blown by the wind across the view, and lightning in a thunderstorm.
        Stateless: every drop's place follows from its seed and the clock, so nothing is simulated."""
        light = self.light
        if light.rain < 0.03 and light.snow < 0.03 and not light.storm:
            return
        t = time.monotonic()
        to = math.radians(light.wind_from + 180.0)  # where the wind blows to: x east, y north
        across = math.sin(to) * math.sin(self.yaw) - math.cos(to) * math.cos(self.yaw)  # + blows to the right
        push = across * min(1.0, light.wind_kmh / 45.0)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if light.rain >= 0.03:
            n = int(PRECIP_MAX * light.rain)
            lines = []
            for u, v, z in self._precip_seeds[:n]:
                fall = 950.0 * (0.75 + 0.5 * z)
                ln = 13.0 + 12.0 * z
                x = (u * w * 1.3 + t * fall * push * 0.6) % (w * 1.3) - w * 0.15
                y = (v * h * 1.2 + t * fall) % (h * 1.2) - h * 0.1
                lines.append(QLineF(x, y, x - push * ln * 0.6, y - ln))
            p.setPen(QPen(QColor(208, 218, 232, int(60 + 70 * light.rain)), 1.1))
            p.drawLines(lines)
        if light.snow >= 0.03:
            n = int(PRECIP_MAX * light.snow)
            sizes: Dict[int, List[QPointF]] = {}
            for u, v, z in self._precip_seeds[:n]:
                fall = 55.0 * (0.6 + 0.8 * z)
                x = (u * w * 1.3 + t * (fall * push * 2.2) + math.sin(t * 1.3 + u * 40.0) * 10.0) % (w * 1.3) - w * 0.15
                y = (v * h * 1.2 + t * fall) % (h * 1.2) - h * 0.1
                sizes.setdefault(int(z * 3), []).append(QPointF(x, y))
            for k, pts in sizes.items():
                pen = QPen(QColor(246, 248, 252, 215), 1.6 + 1.3 * k)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawPoints(pts)
        if light.storm:
            ph = t % 7.3
            if ph < 0.08 or 0.17 < ph < 0.23:
                p.fillRect(QRectF(0, 0, w, h), QColor(235, 240, 255, 95))
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _draw_mood(self, p: QPainter, w: int, h: int) -> None:
        """The colour grade of the mood felt here: a tint, a soft pale lift and a darkened edge."""
        mood, k = self.mood
        if k <= 0.01:
            return
        tint = mix_rgb((255, 255, 255), mood.tint, k)
        if tint != (255, 255, 255):
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(self.rect(), QColor(*tint))
        if mood.lift_alpha > 0:
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Screen)
            p.fillRect(self.rect(), QColor(*mood.lift, int(255 * mood.lift_alpha * k)))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if mood.vignette > 0:
            g = QRadialGradient(QPointF(w / 2, h * 0.45), math.hypot(w, h) * 0.62)
            g.setColorAt(0.45, QColor(0, 0, 0, 0))
            g.setColorAt(1.0, QColor(0, 0, 0, int(215 * mood.vignette * k)))
            p.fillRect(self.rect(), QBrush(g))

    def _draw_tint(self, p: QPainter) -> None:
        """Golden hour, dusk and night colour everything drawn so far (one multiply over the frame)."""
        if self.light.tint != (255, 255, 255):
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
            p.fillRect(self.rect(), QColor(*self.light.tint))
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

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
        fog_start, fog_end = self.light.fog_start, self.light.fog_end
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
                if depth > fog_end + 600:
                    continue
                cells.append((depth, r, c, a, b, cc, d))
        cells.sort(key=lambda t: -t[0])  # far to near

        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        sky_r, sky_g, sky_b = self.light.sky_horizon
        done_upto = self.path.at(self.dist)
        done_i = max(0, min(len(self._trail) - 2, int(self.dist / max(1.0, self.path.length) * (len(self._trail) - 1))))
        _ = done_upto
        trail_done_pen = QPen(QColor("#F39C12"), 3.2)
        trail_todo_pen = QPen(QColor(236, 245, 238, 220), 2.2)
        stop_cells = {}
        for i, s in enumerate(self.stops):
            stop_cells.setdefault(mesh.cell_of(s.world[0], s.world[1]), []).append(i)

        # Cloud shadows: the drifting cloud field, sampled at each cell's centre
        cover = cloud_shadow_cover(self.light.cloud)
        sun_share = min(1.0, self.light.diffuse / 0.85)
        clouds = cover > 0.0 and sun_share > 0.05
        if clouds:
            to = math.radians(self.light.wind_from + 180.0)
            run = time.monotonic() * (25.0 + 2.5 * self.light.wind_kmh)  # clouds race by in the fast replay
            field, tn, tm, cm = self._cloud_field, CLOUD_TEX_N, CLOUD_TEX_M, mesh.cell_m
            cx0 = mesh.x0 + 0.5 * cm - math.sin(to) * run
            cy0 = mesh.y0 + 0.5 * cm - math.cos(to) * run

        for depth, r, c, a, b, cc, d in cells:
            cr, cg, cb = mesh.color[r][c]
            shaded = False
            if clouds:
                n = field[(int((cy0 + r * cm) // tm) % tn) * tn + int((cx0 + c * cm) // tm) % tn]
                sh = (cover - n) * 16.0 + 0.5  # soft edge
                if sh > 0.0:
                    k = 1.0 - 0.38 * sun_share * min(1.0, sh)
                    cr, cg, cb = cr * k, cg * k, cb * k
                    shaded = sh > 0.5 and sun_share > 0.3
            f = 0.0 if depth <= fog_start else min(1.0, (depth - fog_start) / (fog_end - fog_start))
            col = QColor(int(cr + (sky_r - cr) * f), int(cg + (sky_g - cg) * f), int(cb + (sky_b - cb) * f))
            p.setPen(QPen(col, 1.0))
            p.setBrush(col)
            p.drawPolygon(QPolygonF([QPointF(a[0], a[1]), QPointF(b[0], b[1]), QPointF(cc[0], cc[1]), QPointF(d[0], d[1])]))
            water = self.layers.water.get((r, c))
            if water:
                self._draw_water(p, project, water, f, self.light.sky_horizon)
            layer_segs = self.layers.cells.get((r, c))
            if layer_segs:
                self._draw_layers(p, project, layer_segs, depth)
            if depth < VEG_FAR and depth < fog_end:
                self._draw_plants(p, project, cam.focal, r, c, depth, f, shaded)
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
                # the stop being shown is drawn by the overlay (it grows out of this marker)
                if si > self.next_stop or (si == self.next_stop and self.phase not in self.PHOTO_PHASES):
                    self._draw_photo_marker(p, project, self.stops[si])

    @staticmethod
    def _draw_water(p: QPainter, project, rings, fog: float, sky: Tuple[int, int, int]) -> None:
        """Lake / pond pieces on this cell, tinted toward the sky colour with distance like the terrain."""
        r, g, b = 58, 116, 168
        sr, sg, sb = sky
        col = QColor(int(r + (sr - r) * fog), int(g + (sg - g) * fog), int(b + (sb - b) * fog))
        p.setPen(QPen(col, 1.0))
        p.setBrush(col)
        for ring in rings:
            pts = [project(x, y, z) for x, y, z in ring]
            if all(pts):
                p.drawPolygon(QPolygonF([QPointF(q[0], q[1]) for q in pts]))

    def _draw_layers(self, p: QPainter, project, seg_ids: List[int], depth: float) -> None:
        """This cell's map lines (other trails, paths, water), fading out with distance: one pen and
        one drawLines call per kind of line."""
        segs, kinds = self.layers.segs, self.layers.kinds
        batches: Dict[str, List[QLineF]] = {}
        for i in seg_ids:
            kind = kinds[i]
            if depth > LAYER_PENS[kind][2]:
                continue
            x0, y0, z0, x1, y1, z1 = segs[i]
            s0 = project(x0, y0, z0)
            s1 = project(x1, y1, z1)
            if s0 and s1:
                batches.setdefault(kind, []).append(QLineF(s0[0], s0[1], s1[0], s1[1]))
        if not batches:
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        for kind, lines in batches.items():
            rgb, width, reach = LAYER_PENS[kind]
            fog_start, reach = self.light.fog_start, min(reach, self.light.fog_end)
            fade = 1.0 if depth <= fog_start else max(0.0, 1.0 - (depth - fog_start) / max(1.0, reach - fog_start))
            if fade >= 0.08:
                p.setPen(QPen(QColor(rgb[0], rgb[1], rgb[2], int(225 * fade)), width))
                p.drawLines(lines)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _draw_plants(self, p: QPainter, project, focal: float, r: int, c: int, depth: float, fog: float,
                     shaded: bool = False) -> None:
        """This cell's trees, shrubs, grass and boulders, back to front. Always the same plants: they
        only grow as the camera nears them, each fading in while still a few pixels tall, so none pop."""
        plants = self._plant_cells.get((r, c))
        if plants is None:
            # (x, y, z, drawn height in mesh units, sprite, sway: 0 rock / 1 tree / 2 small plant), once per cell
            k = self.frame.zx * VEG_SCALE
            plants = self._plant_cells[(r, c)] = [
                (x, y, z, k * height_m * VEG_BOOST.get(sp, 1.0), self._sprites[sp][variant],
                 0 if sp in STILL_PLANTS else 2 if sp in SMALL_PLANTS else 1)
                for x, y, z, sp, variant, height_m in self.veg.plants(r, c)
            ]
        if not plants:
            return
        tallest = self._plant_tallest.get((r, c))
        if tallest is None:
            tallest = self._plant_tallest[(r, c)] = max(pl[3] for pl in plants)
        if focal * tallest / max(1.0, depth - self.mesh.cell_m) < VEG_MIN_PX:
            return  # even its tallest plant at the cell's nearest corner is too small to see
        visible = []
        for x, y, z, hk, sprite, bend in plants:
            s = project(x, y, z)
            if s is not None:
                hpx = focal * hk / s[2]
                if hpx >= VEG_MIN_PX:
                    visible.append((s[2], s[0], s[1], hpx, sprite, x, y, bend))
        if not visible:
            return
        if depth < VEG_NEAR:  # farther away the order within a cell isn't noticeable
            visible.sort(key=itemgetter(0), reverse=True)
        alpha = (1.0 - fog * 0.9) * min(1.0, (VEG_FAR - depth) / 500.0)
        p.setOpacity(alpha)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        draw = p.drawPixmap
        fading = False
        sway = self._sway if depth < SWAY_FAR else None
        for _d, sx, sy, hpx, sprite, wx, wy, bend in visible:
            if hpx < VEG_FULL_PX:  # still tiny: fade in with size instead of appearing at once
                p.setOpacity(alpha * (hpx - VEG_MIN_PX) / (VEG_FULL_PX - VEG_MIN_PX))
                fading = True
            elif fading:
                p.setOpacity(alpha)
                fading = False
            pix, src = sprite.level_for(hpx, shaded)
            w = hpx * sprite.aspect
            if sway is not None and bend and hpx >= SWAY_MIN_PX:
                # Bend in the wind: shear the sprite about its ground point
                t, lean, amp, dx, dy = sway
                gust = 0.55 + 0.45 * math.sin(t * 0.8 - (wx * dx + wy * dy) * 0.004)  # gusts roll downwind
                small = bend == 2
                k = (lean * gust + amp * math.sin(t * (2.3 if small else 1.6) + wx * 0.05 + wy * 0.07)) * (1.6 if small else 1.0)
                p.setWorldTransform(QTransform(1.0, 0.0, -k, 1.0, sx, sy))
                draw(QRectF(-w * 0.5, -hpx * GROUND_FRAC, w, hpx), pix, src)
                p.resetTransform()
                continue
            draw(QRectF(sx - w * 0.5, sy - hpx * GROUND_FRAC, w, hpx), pix, src)  # ground point at (sx, sy)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        p.setOpacity(1.0)

    @staticmethod
    def _marker_rect(project, stop: PhotoStop) -> Optional[Tuple[QPointF, QRectF]]:
        """Where a stop's framed photo stands on the terrain: (ground point, picture rect)."""
        s = project(*stop.world)
        if not s:
            return None
        size = max(10.0, min(46.0, 26000.0 / s[2]))
        return QPointF(s[0], s[1]), QRectF(s[0] - size / 2, s[1] - size * 0.9 - size * 0.75, size, size * 0.75)

    def _draw_photo_marker(self, p: QPainter, project, stop: PhotoStop) -> None:
        spot = self._marker_rect(project, stop)
        if spot is None:
            return
        ground, rect = spot
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setPen(QPen(QColor("#F39C12"), 1.6))
        p.drawLine(ground, QPointF(ground.x(), rect.bottom()))
        if stop.pixmap is not None:
            p.drawPixmap(rect.toRect(), stop.pixmap)
        else:
            p.setBrush(QColor(11, 22, 16))
            p.drawRect(rect)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)
        n = len(stop.items) + stop.hidden
        if n > 1 and rect.width() >= 18:
            badge = QRectF(rect.right() - 6, rect.top() - 8, 16, 14)
            p.setBrush(QColor("#F39C12"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRoundedRect(badge, 6, 6)
            p.setPen(QColor("#0B1610"))
            p.setFont(QFont("Sans Serif", 7, QFont.Weight.Bold))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(n) if n < 100 else "99+")
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)

    def _draw_peaks(self, p: QPainter, project) -> None:
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        font = QFont("Sans Serif", 8, QFont.Weight.Bold)
        p.setFont(font)
        fm = QFontMetricsF(font)
        placed: List[QRectF] = []
        for name, x, y, z, ele in self.peaks:
            s = project(x, y, z)
            if not s or s[2] > self.light.fog_end or not (0 <= s[0] <= self.width() and 0 <= s[1] <= self.height()):
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
        """A hiker seen from behind (the chase camera's view): backpack, hat and trekking pole,
        legs and arms swinging with the distance walked (still while paused)."""
        s = project(*pos)
        if not s:
            return
        top = project(pos[0], pos[1], pos[2] + 70.0)  # sized like a 70 m figure, readable at any zoom
        hgt = max(26.0, min(64.0, (s[1] - top[1]) if top else 36.0))
        gx, gy = s[0], s[1]
        swing = math.sin(self.dist / 0.9)  # one stride every ~5.7 m
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # Shadow and a soft halo so the figure stands out on any terrain
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(231, 76, 60, 60))
        p.drawEllipse(QPointF(gx, gy), hgt * 0.42, hgt * 0.13)
        p.setBrush(QColor(0, 0, 0, 90))
        p.drawEllipse(QPointF(gx, gy), hgt * 0.22, hgt * 0.07)

        def at(dx: float, dy: float) -> QPointF:  # offsets in figure heights from the ground point
            return QPointF(gx + dx * hgt, gy - dy * hgt)

        cap = Qt.PenCapStyle.RoundCap
        # Legs: the stepping foot lifts and both shift with the stride
        legs = QPen(QColor("#2C3E50"), hgt * 0.12, Qt.PenStyle.SolidLine, cap)
        p.setPen(legs)
        for side, ph in ((-1, swing), (1, -swing)):
            p.drawLine(at(side * 0.07, 0.46), at(side * 0.09, 0.02 + max(0.0, ph) * 0.07))
        # Boots
        p.setPen(QPen(QColor("#5D4037"), hgt * 0.13, Qt.PenStyle.SolidLine, cap))
        for side, ph in ((-1, swing), (1, -swing)):
            foot = at(side * 0.09, 0.02 + max(0.0, ph) * 0.07)
            p.drawPoint(foot)
        # Arms swing opposite to the legs; the right hand holds the pole
        arm = QPen(QColor("#1F6FA8"), hgt * 0.09, Qt.PenStyle.SolidLine, cap)
        p.setPen(arm)
        l_hand, r_hand = at(-0.21, 0.50 + swing * 0.04), at(0.21, 0.50 - swing * 0.04)
        p.drawLine(at(-0.15, 0.76), l_hand)
        p.drawLine(at(0.15, 0.76), r_hand)
        p.setPen(QPen(QColor(220, 220, 220), max(1.5, hgt * 0.035), Qt.PenStyle.SolidLine, cap))
        p.drawLine(QPointF(r_hand.x(), r_hand.y() - hgt * 0.06), at(0.27 - swing * 0.05, 0.0))
        # Jacket, then the backpack over it (seen from behind)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#2E86C1"))
        p.drawRoundedRect(QRectF(gx - hgt * 0.16, gy - hgt * 0.82, hgt * 0.32, hgt * 0.40), hgt * 0.08, hgt * 0.08)
        p.setBrush(QColor("#E67E22"))
        p.drawRoundedRect(QRectF(gx - hgt * 0.14, gy - hgt * 0.86, hgt * 0.28, hgt * 0.36), hgt * 0.07, hgt * 0.07)
        p.setBrush(QColor("#B85E12"))
        p.drawRoundedRect(QRectF(gx - hgt * 0.10, gy - hgt * 0.66, hgt * 0.20, hgt * 0.10), hgt * 0.03, hgt * 0.03)
        p.setPen(QPen(QColor("#8E4A0E"), max(1.0, hgt * 0.025)))
        p.drawLine(at(-0.14, 0.62), at(0.14, 0.62))  # hip belt
        # Head and hat
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#E8B990"))
        p.drawEllipse(at(0.0, 0.92), hgt * 0.085, hgt * 0.09)
        p.setBrush(QColor("#6D4C41"))
        p.drawEllipse(at(0.0, 0.955), hgt * 0.14, hgt * 0.035)  # brim
        p.drawRoundedRect(QRectF(gx - hgt * 0.08, gy - hgt * 1.06, hgt * 0.16, hgt * 0.10), hgt * 0.04, hgt * 0.04)

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
        line = f"{format_distance(self.dist, self.units)} of {format_distance(self.path.length, self.units)}"
        if self.light.ts is not None:
            line += f"  ·  {f'{datetime.fromtimestamp(self.light.ts):%I:%M %p}'.lstrip('0')}"
        p.drawText(QPointF(26, 62), line)
        p.setFont(QFont("Sans Serif", 16, QFont.Weight.Bold))
        p.setPen(QColor("#F4D03F"))
        alt = f"▲ {format_elevation(ele, self.units)}"
        p.drawText(QPointF(26, 94), alt)
        # Life zone at the hiker (Spruce–fir forest, Krummholz, Alpine tundra, ...)
        x, y, _z, _e = self.path.at(self.dist)
        alt_w = QFontMetricsF(p.font()).horizontalAdvance(alt)
        p.setFont(QFont("Sans Serif", 9))
        p.setPen(QColor("#98B2A1"))
        fm = QFontMetricsF(p.font())
        room = max(0.0, 330 - 12 - (alt_w + 12))
        p.drawText(QPointF(26 + alt_w + 12, 93),
                   fm.elidedText(self.veg.zone_name_at(x, y), Qt.TextElideMode.ElideRight, room))

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

    def _blend(self) -> float:
        """0 = only the 3D scene … 1 = the photo fills the frame."""
        if self.phase == "blend_in":
            return smoothstep(self.phase_t / BLEND_IN_S)
        if self.phase == "blend_out":
            return 1.0 - smoothstep(self.phase_t / BLEND_OUT_S)
        return 1.0 if self.phase == "hold" else 0.0

    def _draw_photo_overlay(self, p: QPainter, w: int, h: int, project, stop: PhotoStop) -> None:
        """The scene blurs and dims while the photo grows out of its marker and fades in; within a
        stop the photos cross-fade; leaving reverses it back into the marker."""
        t = self._blend()
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Soft focus: a heavily downscaled copy of the scene, scaled back up, faded over it
        if t > 0.01 and self._frame is not None:
            if self._soft is None:
                self._soft = self._frame.scaled(max(8, w // 14), max(6, h // 14),
                                                Qt.AspectRatioMode.IgnoreAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
            small = self._soft
            p.setOpacity(min(1.0, t * 1.1))
            p.drawPixmap(QRectF(0, 0, w, h), small, QRectF(small.rect()))
            p.setOpacity(1.0)
            p.fillRect(self.rect(), QColor(5, 10, 7, int(150 * t)))

        k = min(self.photo_k, len(stop.items) - 1)
        if self.phase == "blend_out":
            k = len(stop.items) - 1
        item = stop.items[k]
        pix = self._full_pixmap(item) or item.pixmap
        spot = self._marker_rect(project, stop)
        start = spot[1] if spot else QRectF(w / 2 - 23, h / 2 - 17, 46, 34)
        full = self._fit_rect(pix, w, h)
        rect = QRectF(
            start.x() + (full.x() - start.x()) * t, start.y() + (full.y() - start.y()) * t,
            start.width() + (full.width() - start.width()) * t, start.height() + (full.height() - start.height()) * t,
        )
        # The marker was already visible, so the photo starts partly opaque: no pop-in
        self._paint_photo(p, pix, rect, opacity=0.45 + 0.55 * t, scale_t=t)

        # Cross-fade to the stop's next photo during the end of this one's hold
        caption_item, caption_alpha = item, 1.0
        if self.phase == "hold" and k + 1 < len(stop.items):
            remaining = stop.hold_s(k) - self.phase_t
            if remaining < XFADE_S:
                f = smoothstep(1.0 - remaining / XFADE_S)
                nxt = stop.items[k + 1]
                npix = self._full_pixmap(nxt) or nxt.pixmap
                self._paint_photo(p, npix, self._fit_rect(npix, w, h), opacity=f, scale_t=1.0)
                if f > 0.5:
                    caption_item, caption_alpha = nxt, (f - 0.5) * 2
                else:
                    caption_alpha = 1.0 - f * 2

        # Caption fades in last and out first
        cap = max(0.0, (t - 0.7) / 0.3) * caption_alpha
        if cap > 0.01:
            self._draw_caption(p, w, h, stop, caption_item, k, cap)
        p.setOpacity(1.0)

    @staticmethod
    def _fit_rect(pix: Optional[QPixmap], w: int, h: int) -> QRectF:
        aspect = (pix.width() / pix.height()) if pix is not None and pix.height() else 1.5
        fw, fh = (w, w / aspect) if w / h < aspect else (h * aspect, h)
        return QRectF((w - fw) / 2, (h - fh) / 2, fw, fh)

    def _paint_photo(self, p: QPainter, pix: Optional[QPixmap], rect: QRectF, opacity: float, scale_t: float) -> None:
        p.setOpacity(max(0.0, min(1.0, opacity)))
        if pix is not None:
            p.drawPixmap(rect, pix, QRectF(pix.rect()))
        else:
            p.setBrush(QColor(11, 22, 16))
            p.setPen(QPen(QColor("#F39C12"), 2.0))
            p.drawRect(rect)
            p.setPen(QColor("#98B2A1"))
            p.setFont(QFont("Sans Serif", max(8, int(28 * scale_t))))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, "📷")
        p.setOpacity(1.0)

    def _draw_caption(self, p: QPainter, w: int, h: int, stop: PhotoStop, item: PhotoItem, k: int, alpha: float) -> None:
        a = int(255 * max(0.0, min(1.0, alpha)))
        band = QRectF(0, h - 86, w, 72)
        p.fillRect(band, QColor(0, 0, 0, int(a * 0.6)))
        p.setPen(QColor(244, 208, 63, a))
        p.setFont(QFont("Sans Serif", 13, QFont.Weight.Bold))
        head = format_datetime(item.pin)
        total = len(stop.items)
        if total > 1 or stop.hidden:
            idx = stop.items.index(item) + 1
            head += f"     {idx} / {total}" + (f"  (+{stop.hidden} more here)" if stop.hidden else "")
        p.drawText(band.adjusted(24, 8, -24, -36), Qt.AlignmentFlag.AlignLeft, head)
        p.setPen(QColor(236, 245, 238, a))
        p.setFont(QFont("Sans Serif", 11))
        caption = item.pin.caption or item.pin.ai_summary or item.pin.filename
        if item.pin.people:
            caption += "   👥 " + ", ".join(item.pin.people)
        p.drawText(band.adjusted(24, 36, -24, -6), Qt.AlignmentFlag.AlignLeft, caption)


class Replay3DDialog(QDialog):
    """Window with the 3D replay and its playback controls."""

    def __init__(self, route: ComposedRoute, pack: Optional[DestinationPack], photos: List[HikePhotoPin],
                 units: str = "us", parent: Optional[QWidget] = None, month: Optional[int] = None,
                 show_photos: bool = True, session: Optional[HikeSession] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"3D Hike Replay — {route.name}")
        self.resize(1280, 800)
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        self.view = Replay3DView(route, pack, photos, units, month=month, session=session)
        col.addWidget(self.view, 1)

        bar = QHBoxLayout()
        self.play_btn = QPushButton("⏸ Pause")
        self.play_btn.setObjectName("PrimaryButton")
        self.play_btn.clicked.connect(self._toggle)
        self.skip_btn = QPushButton("Skip Photos ⏭")
        self.skip_btn.setEnabled(False)
        self.skip_btn.clicked.connect(self.view.skip_photo)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.sliderMoved.connect(lambda v: self.view.seek(v / 1000.0))
        self.speed_combo = QComboBox()
        for label, val in (("0.5×", 0.5), ("1×", 1.0), ("2×", 2.0), ("4×", 4.0)):
            self.speed_combo.addItem(label, val)
        self.speed_combo.setCurrentIndex(1)
        self.photos_check = QCheckBox("Show photos")
        self.photos_check.setToolTip("Stop at each photo and show it, or hike straight through")
        self.photos_check.setChecked(show_photos)
        self.photos_check.setEnabled(bool(self.view.photo_stops))
        self.view.set_show_photos(show_photos)
        self.photos_check.toggled.connect(self.view.set_show_photos)
        self.speed_combo.currentIndexChanged.connect(lambda _i: setattr(self.view, "speed", float(self.speed_combo.currentData())))
        bar.addWidget(self.play_btn)
        bar.addWidget(self.skip_btn)
        bar.addWidget(self.slider, 1)
        bar.addWidget(self.photos_check)
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
