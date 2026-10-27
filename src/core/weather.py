"""Weather on the day of a hike, from Open-Meteo (free, no account). Opt-in: only called when the
user asks for it; every request is logged to ~/.aegishiker/logs/network.jsonl.

Recent days (about the last three months) come from the forecast service's past data, older ones
from the ERA5 historical archive.
"""
import json
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional

import requests

from . import llm_log

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
USER_AGENT = "AegisHiker/1.0 (offline hiking planner; hike weather)"
RECENT_DAYS = 85

# WMO weather interpretation codes (as used by Open-Meteo)
WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers", 85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}


def describe_code(code: Optional[int]) -> str:
    return WMO_CODES.get(int(code), "") if code is not None else ""


def _hours_window(hours: List[str], start: Optional[datetime], end: Optional[datetime]) -> List[int]:
    """Indices of the hourly values during the hike (the daytime hours when the times are unknown)."""
    if start and end and end > start:
        lo = start.replace(minute=0, second=0, microsecond=0)
        hi = end
    else:
        lo = hi = None
    out = []
    for i, h in enumerate(hours):
        t = datetime.fromisoformat(h)
        if lo is not None and hi is not None:
            if lo <= t <= hi:
                out.append(i)
        elif 9 <= t.hour <= 17:
            out.append(i)
    return out


HOURLY_FIELDS = ("weather_code", "cloud_cover", "precipitation", "wind_speed_10m", "wind_direction_10m", "temperature_2m")


def hourly_rows(data: Dict, idx: List[int]) -> List[List[float]]:
    """The hike's hours (and one either side) as [UTC seconds, *HOURLY_FIELDS] rows (None where
    unknown): the 3D replays draw the clouds, rain, snow and wind of each hour from them."""
    hourly = data.get("hourly") or {}
    hours = hourly.get("time") or []
    offset = float(data.get("utc_offset_seconds") or 0)
    rows = []
    for i in range(max(0, min(idx) - 1), min(len(hours), max(idx) + 2)):
        ts = datetime.fromisoformat(hours[i]).replace(tzinfo=timezone.utc).timestamp() - offset
        row: List = [int(ts)]
        for key in HOURLY_FIELDS:
            series = hourly.get(key) or []
            v = series[i] if i < len(series) else None
            row.append(None if v is None else round(float(v), 1))
        rows.append(row)
    return rows


def summarize_hourly(data: Dict, start: Optional[datetime], end: Optional[datetime]) -> Dict[str, str]:
    """Hike-time weather from an Open-Meteo response: conditions, temperature range, rain, wind, and
    the hour-by-hour series for the replays."""
    hourly = data.get("hourly") or {}
    hours = hourly.get("time") or []
    idx = _hours_window(hours, start, end) or list(range(len(hours)))
    if not idx:
        return {}

    def vals(key: str) -> List[float]:
        series = hourly.get(key) or []
        return [float(series[i]) for i in idx if i < len(series) and series[i] is not None]

    temps, rain, wind, codes = vals("temperature_2m"), vals("precipitation"), vals("wind_speed_10m"), vals("weather_code")
    out: Dict[str, str] = {}
    if codes:
        # The most significant condition that held for at least an hour (WMO codes rise with severity)
        worst = max(int(c) for c in codes)
        common = max(set(int(c) for c in codes), key=lambda c: codes.count(float(c)))
        main = describe_code(common)
        out["weather"] = main if worst == common or worst < 51 else f"{main}, {describe_code(worst).lower()} at times"
    if temps:
        out["temp_low_c"] = f"{min(temps):.1f}"
        out["temp_high_c"] = f"{max(temps):.1f}"
    if rain:
        out["precip_mm"] = f"{sum(rain):.1f}"
    if wind:
        out["wind_kmh"] = f"{max(wind):.0f}"
    if any(hourly.get(k) for k in HOURLY_FIELDS):
        out["weather_hourly"] = json.dumps(hourly_rows(data, idx), separators=(",", ":"))
    return out


def fetch_hike_weather(
    lat: float, lon: float, day: date,
    start: Optional[datetime] = None, end: Optional[datetime] = None,
    session: Optional[requests.Session] = None, timeout: float = 20.0,
) -> Dict[str, str]:
    """Weather during the hike as hike-detail strings (see hike_report.DETAIL_KEYS)."""
    recent = (date.today() - day) <= timedelta(days=RECENT_DAYS)
    url = FORECAST_URL if recent else ARCHIVE_URL
    params = {
        "latitude": f"{lat:.4f}",
        "longitude": f"{lon:.4f}",
        "start_date": day.isoformat(),
        "end_date": day.isoformat(),
        "hourly": "temperature_2m,precipitation,weather_code,wind_speed_10m,cloud_cover,wind_direction_10m",
        "timezone": "auto",
    }
    http = session or requests.Session()
    http.headers["User-Agent"] = USER_AGENT
    try:
        resp = http.get(url, params=params, timeout=timeout)
        llm_log.log_network_request("open_meteo_weather", url, status_code=resp.status_code, bytes_received=len(resp.content))
        resp.raise_for_status()
    except requests.RequestException as e:
        llm_log.log_network_request("open_meteo_weather", url, error=str(e))
        raise RuntimeError(f"Could not get the weather from Open-Meteo: {e}") from e
    out = summarize_hourly(resp.json(), start, end)
    if not out:
        raise RuntimeError("Open-Meteo has no weather data for that day yet.")
    out["weather_source"] = "Open-Meteo" + (" (ERA5 archive)" if not recent else "")
    return out
