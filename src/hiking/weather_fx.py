"""The hike's weather as the 3D replays draw it: how cloudy, foggy, rainy, snowy and windy it was at
any moment. From the hour-by-hour series kept when the weather was looked up (see
core.weather.hourly_rows), else from the weather summary typed or fetched for the hike, which then
holds for the whole hike."""
import json
import random
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

CALM_KMH = 8.0
CLOUD_TEX_N = 64  # cloud shadow field: CLOUD_TEX_N x CLOUD_TEX_N texels ..
CLOUD_TEX_M = 170.0  # .. this many metres apart, tiling every ~11 km


@dataclass
class Sky:
    cloud: float = 0.0  # 0 clear .. 1 overcast
    fog: float = 0.0  # 0 none .. 1 dense
    rain: float = 0.0  # 0 .. 1 intensity
    snow: float = 0.0  # 0 .. 1 intensity
    wind_kmh: float = CALM_KMH
    wind_from: float = 270.0  # degrees, where the wind blows from (meteorological)
    storm: bool = False


CLEAR = Sky()

# WMO code → (cloud, fog, rain, snow)
_CODE_FX: Dict[int, tuple] = {
    0: (0.03, 0, 0, 0), 1: (0.2, 0, 0, 0), 2: (0.5, 0, 0, 0), 3: (0.95, 0.08, 0, 0),
    45: (1.0, 0.85, 0, 0), 48: (1.0, 0.85, 0, 0),
    51: (0.9, 0.3, 0.18, 0), 53: (0.95, 0.35, 0.28, 0), 55: (1.0, 0.4, 0.38, 0),
    56: (0.95, 0.35, 0.28, 0), 57: (1.0, 0.4, 0.38, 0),
    61: (0.9, 0.15, 0.35, 0), 63: (0.95, 0.22, 0.6, 0), 65: (1.0, 0.3, 0.9, 0),
    66: (0.95, 0.25, 0.5, 0), 67: (1.0, 0.3, 0.8, 0),
    71: (0.9, 0.25, 0, 0.35), 73: (0.95, 0.35, 0, 0.6), 75: (1.0, 0.5, 0, 0.9), 77: (0.9, 0.25, 0, 0.3),
    80: (0.75, 0.1, 0.4, 0), 81: (0.85, 0.15, 0.65, 0), 82: (0.95, 0.2, 0.9, 0),
    85: (0.85, 0.3, 0, 0.5), 86: (0.95, 0.45, 0, 0.85),
    95: (1.0, 0.15, 0.85, 0), 96: (1.0, 0.15, 0.9, 0), 99: (1.0, 0.15, 1.0, 0),
}

# Words of a weather summary → WMO code (most specific first)
_TEXT_CODES = [
    (r"thunder", 95), (r"heavy snow", 75), (r"light snow|snow grains|flurr", 71), (r"snow", 73),
    (r"freezing fog|fog|mist", 45), (r"heavy (rain|showers)|downpour", 65), (r"light (rain|showers)", 61),
    (r"drizzle", 53), (r"rain|showers|wet", 63), (r"overcast|cloudy skies|grey|gray", 3),
    (r"partly cloudy|some clouds", 2), (r"cloud", 3), (r"mainly clear|mostly clear|mostly sunny", 1),
    (r"clear|sunny|blue sky", 0),
]


def sky_from(code: Optional[float], cloud_pct: Optional[float] = None, precip_mm: Optional[float] = None,
             wind_kmh: Optional[float] = None, wind_from: Optional[float] = None,
             temp_c: Optional[float] = None) -> Sky:
    cloud, fog, rain, snow = _CODE_FX.get(int(code), (0.4, 0, 0, 0)) if code is not None else (0.2, 0, 0, 0)
    if cloud_pct is not None:
        cloud = max(cloud * 0.5, cloud_pct / 100.0) if code is not None and int(code) >= 45 else cloud_pct / 100.0
    if precip_mm:  # an hour's measured rain lifts a weak code (showers are often coded as clouds)
        wet = min(1.0, precip_mm / 4.0)
        if snow > 0 or (temp_c is not None and temp_c <= 0.5):
            snow = max(snow, wet)
        else:
            rain = max(rain, wet)
    if temp_c is not None and temp_c <= 0.0 and rain > 0:
        snow, rain = max(snow, rain), 0.0
    return Sky(min(1.0, cloud), fog, rain, snow, CALM_KMH if wind_kmh is None else float(wind_kmh),
               270.0 if wind_from is None else float(wind_from), code is not None and int(code) >= 95)


def _code_of(text: str) -> Optional[int]:
    t = text.lower()
    for pattern, code in _TEXT_CODES:
        if re.search(pattern, t):
            return code
    return None


def sky_from_summary(text: str, precip_mm: str = "", wind_kmh: str = "", temp_c: str = "") -> Optional[Sky]:
    """The whole hike's sky from a summary like "Partly cloudy, light rain at times"; what held only
    "at times" counts half."""
    clauses = [c for c in (text or "").split(",") if c.strip()]
    codes = [_code_of(c) for c in clauses]
    if not codes or codes[0] is None:
        return None

    def num(v: str) -> Optional[float]:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    sky = sky_from(codes[0], wind_kmh=num(wind_kmh), temp_c=num(temp_c))
    for code in codes[1:]:
        if code is not None:
            extra = sky_from(code, temp_c=num(temp_c))
            sky.rain, sky.snow = max(sky.rain, extra.rain * 0.5), max(sky.snow, extra.snow * 0.5)
            sky.fog = max(sky.fog, extra.fog * 0.5)
            sky.cloud = max(sky.cloud, (sky.cloud + extra.cloud) / 2)
            sky.storm = sky.storm or extra.storm
    return sky


@dataclass
class WeatherTrack:
    """The sky through the hike: hourly (ts, Sky) samples, or one Sky for the whole hike."""

    samples: List[tuple]

    def at(self, ts: Optional[float]) -> Sky:
        s = self.samples
        if len(s) == 1 or ts is None:
            return s[len(s) // 2][1]
        if ts <= s[0][0]:
            return s[0][1]
        if ts >= s[-1][0]:
            return s[-1][1]
        for (t0, a), (t1, b) in zip(s, s[1:]):
            if ts <= t1:
                return mix_sky(a, b, (ts - t0) / max(1.0, t1 - t0))
        return s[-1][1]


def mix_sky(a: Sky, b: Sky, f: float) -> Sky:
    turn = ((b.wind_from - a.wind_from + 540.0) % 360.0) - 180.0
    return Sky(a.cloud + (b.cloud - a.cloud) * f, a.fog + (b.fog - a.fog) * f, a.rain + (b.rain - a.rain) * f,
               a.snow + (b.snow - a.snow) * f, a.wind_kmh + (b.wind_kmh - a.wind_kmh) * f,
               (a.wind_from + turn * f) % 360.0, a.storm if f < 0.5 else b.storm)


def weather_track(details: Optional[Dict[str, str]]) -> Optional[WeatherTrack]:
    """The hike's weather from its details (see hike_report.DETAIL_KEYS); None when unknown."""
    if not details:
        return None
    raw = details.get("weather_hourly") or ""
    if raw:
        try:
            rows: Sequence = json.loads(raw)
            samples = [(float(r[0]), sky_from(*r[1:7])) for r in rows if len(r) >= 7]
            if samples:
                return WeatherTrack(samples)
        except (ValueError, TypeError):
            pass
    sky = sky_from_summary(details.get("weather", ""), details.get("precip_mm", ""), details.get("wind_kmh", ""),
                           details.get("temp_low_c", ""))
    return WeatherTrack([(0.0, sky)]) if sky else None


def cloud_noise(n: int = CLOUD_TEX_N, seed: int = 11) -> List[float]:
    """A tiling field of puffy cloud shapes, row by row, equalised to 0..1 so that the texels below
    `cover` are the shadows of a sky that much covered."""
    rng = random.Random(seed)
    field = [0.0] * (n * n)
    for freq, weight in ((4, 1.0), (8, 0.5), (16, 0.25)):
        grid = [[rng.random() for _ in range(freq)] for _ in range(freq)]
        step = n / freq
        for y in range(n):
            gy, fy = divmod(y / step, 1.0)
            y0, y1 = int(gy) % freq, (int(gy) + 1) % freq
            sy = fy * fy * (3 - 2 * fy)
            for x in range(n):
                gx, fx = divmod(x / step, 1.0)
                x0, x1 = int(gx) % freq, (int(gx) + 1) % freq
                sx = fx * fx * (3 - 2 * fx)
                top = grid[y0][x0] + (grid[y0][x1] - grid[y0][x0]) * sx
                bot = grid[y1][x0] + (grid[y1][x1] - grid[y1][x0]) * sx
                field[y * n + x] += weight * (top + (bot - top) * sy)
    order = sorted(range(n * n), key=field.__getitem__)
    out = [0.0] * (n * n)
    for rank, i in enumerate(order):
        out[i] = rank / (n * n - 1)
    return out


def cloud_shadow_cover(cloud: float) -> float:
    """How much of the ground lies in cloud shadow: none under a clear sky, and an overcast sky
    shades everything evenly (its dimming is in the light itself), so no patches either."""
    if cloud <= 0.08 or cloud >= 0.92:
        return 0.0
    return min(0.75, cloud * 0.85)
