"""Time of day for the 3D replays: when the hiker was where along the route, where the sun stood
then, and the light that gives the scene (sun direction for the terrain shading, sky colours and a
tint over the whole scene for golden hour, dusk and night), dimmed, greyed and closed in by the
hike's weather (clouds, fog, rain, snow; see weather_fx).

An approximation that keeps the replays fast: the sun comes from the NOAA low-precision formulas
(well under a degree off), and the light is worked out at a handful of keyframes along the hike
(one per LIGHT_STEP_S of hiking time). The terrain is re-shaded per keyframe; sky and tint blend
smoothly between them."""
import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

from .models import HikePhotoPin, HikeSession
from .weather_fx import CALM_KMH, Sky, WeatherTrack

LIGHT_STEP_S = 900.0  # a keyframe per 15 minutes of hiking (the sun moves ~4°)
MAX_KEYS = 32
WALK_MPS = 1.1  # pace to extrapolate with when the hike's own pace is unknown

FOG_START_M, FOG_END_M = 1800.0, 5200.0  # haze of a clear day

RGB = Tuple[int, int, int]


@dataclass
class Light:
    along_m: float  # where along the route this light applies
    sun: Tuple[float, float, float]  # unit vector toward the sun: x east, y north, z up
    ambient: float  # terrain brightness facing away from the sun
    diffuse: float  # extra brightness facing the sun
    sky_top: RGB
    sky_horizon: RGB  # also the fog colour
    tint: RGB  # multiplied over the scene (255, 255, 255: none)
    elevation: float = 40.0  # sun above the horizon, degrees
    ts: Optional[float] = None  # UTC time, when known
    # Weather
    cloud: float = 0.0  # 0 clear .. 1 overcast (also hides the sun's glow)
    fog_start: float = FOG_START_M  # terrain fades into the horizon colour from here ..
    fog_end: float = FOG_END_M  # .. to here, and isn't drawn beyond
    rain: float = 0.0  # 0 .. 1
    snow: float = 0.0  # 0 .. 1 (also whitens the ground)
    wind_kmh: float = CALM_KMH
    wind_from: float = 270.0  # degrees
    storm: bool = False

    def to_json(self) -> dict:
        return {"along": round(self.along_m, 1), "sun": [round(v, 4) for v in self.sun],
                "amb": round(self.ambient, 3), "dif": round(self.diffuse, 3), "top": list(self.sky_top),
                "hor": list(self.sky_horizon), "tint": list(self.tint), "elev": round(self.elevation, 1),
                "ts": None if self.ts is None else int(self.ts), "cloud": round(self.cloud, 3),
                "fog": [round(self.fog_start), round(self.fog_end)], "rain": round(self.rain, 3),
                "snow": round(self.snow, 3), "wind": round(self.wind_kmh, 1), "windFrom": round(self.wind_from),
                "storm": self.storm}


# The look without a time: a sun from the north-west, 40° up, in a clear sky
DEFAULT_LIGHT = Light(0.0, (-0.54, 0.54, 0.64), 0.45, 0.85, (88, 140, 196), (196, 214, 226), (255, 255, 255))

# Sun elevation (degrees) → sky top, sky horizon, scene tint
_SKY_TABLE: List[Tuple[float, RGB, RGB, RGB]] = [
    (-12.0, (8, 12, 30), (28, 36, 60), (58, 64, 100)),
    (-6.0, (28, 38, 82), (104, 88, 120), (112, 112, 160)),
    (-1.0, (62, 82, 140), (236, 146, 98), (205, 160, 150)),
    (4.0, (84, 118, 178), (244, 190, 136), (255, 214, 172)),
    (12.0, (88, 136, 194), (224, 212, 196), (255, 240, 222)),
    (22.0, (88, 140, 196), (196, 214, 226), (255, 255, 255)),
]


def sun_position(ts: float, lat: float, lon: float) -> Tuple[float, float]:
    """(azimuth clockwise from north, elevation) in degrees of the sun at UTC time `ts`."""
    jd = ts / 86400.0 + 2440587.5
    t = (jd - 2451545.0) / 36525.0
    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360.0
    m = math.radians(357.52911 + t * (35999.05029 - 0.0001537 * t))
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)
    c = (math.sin(m) * (1.914602 - t * (0.004817 + 0.000014 * t)) + math.sin(2 * m) * (0.019993 - 0.000101 * t)
         + math.sin(3 * m) * 0.000289)
    omega = math.radians(125.04 - 1934.136 * t)
    app = math.radians(l0 + c - 0.00569 - 0.00478 * math.sin(omega))
    eps = math.radians(23.0 + (26.0 + (21.448 - t * (46.815 + t * (0.00059 - t * 0.001813))) / 60.0) / 60.0
                       + 0.00256 * math.cos(omega))
    decl = math.asin(math.sin(eps) * math.sin(app))
    y = math.tan(eps / 2) ** 2
    l0r = math.radians(l0)
    eq_time = 4 * math.degrees(y * math.sin(2 * l0r) - 2 * e * math.sin(m) + 4 * e * y * math.sin(m) * math.cos(2 * l0r)
                               - 0.5 * y * y * math.sin(4 * l0r) - 1.25 * e * e * math.sin(2 * m))  # minutes
    minutes = (ts % 86400.0) / 60.0
    hour_angle = math.radians(((minutes + eq_time + 4 * lon) / 4.0) - 180.0)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(hour_angle)
    zen = math.acos(max(-1.0, min(1.0, cos_zen)))
    az = math.degrees(math.atan2(math.sin(hour_angle),
                                 math.cos(hour_angle) * math.sin(phi) - math.tan(decl) * math.cos(phi))) + 180.0
    return az % 360.0, 90.0 - math.degrees(zen)


def _mix(a: RGB, b: RGB, f: float) -> RGB:
    return tuple(int(round(x + (y - x) * f)) for x, y in zip(a, b))  # type: ignore[return-value]


def _smooth(e0: float, e1: float, x: float) -> float:
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def light_for_sun(azimuth: float, elevation: float, along_m: float = 0.0, ts: Optional[float] = None) -> Light:
    """The scene's light for a sun at this azimuth / elevation (degrees)."""
    rows = _SKY_TABLE
    if elevation <= rows[0][0]:
        top, hor, tint = rows[0][1:]
    elif elevation >= rows[-1][0]:
        top, hor, tint = rows[-1][1:]
    else:
        i = next(k for k in range(1, len(rows)) if elevation < rows[k][0])
        f = (elevation - rows[i - 1][0]) / (rows[i][0] - rows[i - 1][0])
        top, hor, tint = (_mix(rows[i - 1][j], rows[i][j], f) for j in (1, 2, 3))
    az, el = math.radians(azimuth), math.radians(max(elevation, 0.5))
    sun = (math.sin(az) * math.cos(el), math.cos(az) * math.cos(el), math.sin(el))
    # Low sun: flat ground stays fairly bright and slopes facing the sun light up; below the horizon only
    # the (tinted) ambient light is left
    diffuse = 0.85 * min(1.6, 0.64 / max(sun[2], 0.4)) * _smooth(-2.0, 4.0, elevation)
    return Light(along_m, sun, 0.45, diffuse, top, hor, tint, elevation, ts)


def _grey(c: RGB, k: float = 1.0) -> RGB:
    lum = (0.3 * c[0] + 0.59 * c[1] + 0.11 * c[2]) * k
    return (int(min(255, lum * 0.97)), int(min(255, lum * 0.99)), int(min(255, lum * 1.03)))


def with_weather(light: Light, sky: Optional[Sky]) -> Light:
    """The light under this sky: clouds soften the sun's shading and grey the sky, fog and falling rain
    or snow close in the view."""
    if sky is None:
        return light
    c, wet = sky.cloud, max(sky.rain, sky.snow)
    murk = max(sky.fog, 0.35 * sky.rain, 0.55 * sky.snow)
    dim = 1.0 - 0.18 * c - 0.15 * sky.rain - (0.1 if sky.storm else 0.0)
    top = _mix(light.sky_top, _grey(light.sky_horizon, 0.9 * dim), 0.85 * c)
    hor = _mix(light.sky_horizon, _grey(light.sky_horizon, 0.97 * dim), 0.75 * c)
    hor = _mix(hor, _grey(light.sky_horizon, 1.04), 0.6 * sky.fog)  # fog glows pale
    tint = _mix(light.tint, _grey(light.tint, dim), 0.7 * c)
    # The chase camera is ~1.4 km from the hiker: even dense fog leaves the hiker's surroundings in sight,
    # misty from a few hundred metres
    fog_end = max(2300.0, FOG_END_M * (1.0 - 0.6 * murk) * (1.0 - 0.15 * wet))
    fog_start = fog_end * (FOG_START_M / FOG_END_M - 0.25 * murk)
    return replace(light, diffuse=light.diffuse * (1.0 - 0.8 * c), ambient=light.ambient + 0.12 * c,
                   sky_top=top, sky_horizon=hor, tint=tint, cloud=c, fog_start=fog_start,
                   fog_end=fog_end, rain=sky.rain, snow=sky.snow, wind_kmh=sky.wind_kmh, wind_from=sky.wind_from,
                   storm=sky.storm)


def _parse_ts(iso: str) -> Optional[float]:
    """UTC seconds for a stored time; times without a zone are the computer's local time."""
    try:
        return datetime.fromisoformat(iso).timestamp() if iso else None
    except ValueError:
        return None


def hike_clock(route_len: float, pins: Sequence[HikePhotoPin],
               session: Optional[HikeSession] = None) -> List[Tuple[float, float]]:
    """(distance along the route, UTC time) anchors: the hike's start and end and its photos'
    times; a photo placed back along the route from the one before it is left out. Empty when
    nothing says when the hike was."""
    timed = [p for p in pins if p.date_taken]
    if timed:  # photos of one hike only (a saved route can carry several hikes' photos)
        main = Counter(p.session_id for p in timed).most_common(1)[0][0]
        timed = [p for p in timed if p.session_id == main]
    anchors: List[Tuple[float, float]] = []
    for p in sorted(timed, key=lambda p: p.date_taken):
        d, t = max(0.0, min(route_len, p.distance_along_m)), float(p.date_taken)
        if not anchors or (t >= anchors[-1][1] and d >= anchors[-1][0]):
            anchors.append((d, t))
    start = _parse_ts(session.started_at) if session and session.started_at else None
    end = _parse_ts(session.ended_at) if session and session.ended_at else None
    if start is not None and (not anchors or start <= anchors[0][1]) and (end is None or start < end):
        anchors.insert(0, (0.0, start))
    if end is not None and anchors and end >= anchors[-1][1] and anchors[-1][0] < route_len:
        anchors.append((route_len, end))
    return anchors


def time_at(anchors: Sequence[Tuple[float, float]], d: float) -> float:
    """UTC time at distance d along the route, interpolated (and extrapolated) between the anchors."""
    if len(anchors) == 1:
        return anchors[0][1] + (d - anchors[0][0]) / WALK_MPS
    span_d, span_t = anchors[-1][0] - anchors[0][0], anchors[-1][1] - anchors[0][1]
    pace = span_t / span_d if span_d > 1 and span_t > 0 else 1.0 / WALK_MPS  # s per m
    if d <= anchors[0][0]:
        return anchors[0][1] - (anchors[0][0] - d) * pace
    if d >= anchors[-1][0]:
        return anchors[-1][1] + (d - anchors[-1][0]) * pace
    for (d0, t0), (d1, t1) in zip(anchors, anchors[1:]):
        if d <= d1:
            return t0 if d1 - d0 < 1e-6 else t0 + (t1 - t0) * (d - d0) / (d1 - d0)
    return anchors[-1][1]


def light_keys(route_len: float, lat: float, lon: float, anchors: Sequence[Tuple[float, float]],
               weather: Optional[WeatherTrack] = None) -> List[Light]:
    """Keyframes of light along the route, one per LIGHT_STEP_S of hiking (at most MAX_KEYS);
    just DEFAULT_LIGHT (under the hike's weather, when known) when the hike's time is unknown."""
    if not anchors:
        return [with_weather(DEFAULT_LIGHT, weather.at(None) if weather else None)]
    t0, t1 = time_at(anchors, 0.0), time_at(anchors, route_len)
    n = max(1, min(MAX_KEYS - 1, int(math.ceil(abs(t1 - t0) / LIGHT_STEP_S))))
    keys = []
    for k in range(n + 1):
        d = route_len * k / n
        ts = time_at(anchors, d)
        az, el = sun_position(ts, lat, lon)
        keys.append(with_weather(light_for_sun(az, el, d, ts), weather.at(ts) if weather else None))
    return keys


def key_index(keys: Sequence[Light], d: float) -> int:
    """The keyframe nearest distance d (its terrain shading is used)."""
    if len(keys) < 2:
        return 0
    step = keys[-1].along_m / (len(keys) - 1) or 1.0
    return max(0, min(len(keys) - 1, int(round(d / step))))


def light_at(keys: Sequence[Light], d: float) -> Light:
    """Sky, tint and sun at distance d, blended between the keyframes around it."""
    if len(keys) < 2:
        return keys[0]
    step = keys[-1].along_m / (len(keys) - 1) or 1.0
    i = max(0, min(len(keys) - 2, int(d / step)))
    a, b = keys[i], keys[i + 1]
    f = max(0.0, min(1.0, (d - a.along_m) / step))
    sun = tuple(x + (y - x) * f for x, y in zip(a.sun, b.sun))

    def lerp(u: float, v: float) -> float:
        return u + (v - u) * f

    turn = ((b.wind_from - a.wind_from + 540.0) % 360.0) - 180.0
    return Light(d, sun, lerp(a.ambient, b.ambient), lerp(a.diffuse, b.diffuse),  # type: ignore[arg-type]
                 _mix(a.sky_top, b.sky_top, f), _mix(a.sky_horizon, b.sky_horizon, f), _mix(a.tint, b.tint, f),
                 lerp(a.elevation, b.elevation), None if a.ts is None or b.ts is None else lerp(a.ts, b.ts),
                 lerp(a.cloud, b.cloud), lerp(a.fog_start, b.fog_start), lerp(a.fog_end, b.fog_end),
                 lerp(a.rain, b.rain), lerp(a.snow, b.snow), lerp(a.wind_kmh, b.wind_kmh),
                 (a.wind_from + turn * f) % 360.0, a.storm if f < 0.5 else b.storm)
