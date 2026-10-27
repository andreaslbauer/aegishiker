"""The hike's weather in the 3D replays: kept hour by hour when looked up, read from the summary
otherwise, and turned into cloud, fog, rain, snow and wind on the light keyframes."""
import json
from datetime import datetime, timezone

from src.hiking.daylight import DEFAULT_LIGHT, light_at, light_keys, with_weather
from src.hiking.weather_fx import Sky, sky_from, sky_from_summary, weather_track


def _utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def test_hourly_series_is_kept_in_utc_for_the_hike_hours() -> None:
    from src.core.weather import summarize_hourly

    hours = [f"2026-10-18T{h:02d}:00" for h in range(24)]
    data = {"utc_offset_seconds": -4 * 3600,
            "hourly": {"time": hours, "weather_code": [3 if h < 11 else 61 for h in range(24)],
                       "cloud_cover": [90] * 24, "precipitation": [0.0] * 11 + [1.2] * 13,
                       "wind_speed_10m": [12] * 24, "wind_direction_10m": [200] * 24,
                       "temperature_2m": [8] * 24}}
    out = summarize_hourly(data, datetime(2026, 10, 18, 9, 30), datetime(2026, 10, 18, 12, 0))
    rows = json.loads(out["weather_hourly"])
    assert rows[0][0] == _utc("2026-10-18T12:00"), "08:00 local, one hour before the hike, at UTC-4"
    assert len(rows) == 6 and rows[-1][1:] == [61, 90, 1.2, 12, 200, 8]
    track = weather_track({"weather_hourly": out["weather_hourly"]})
    early, late = track.at(_utc("2026-10-18T13:30")), track.at(_utc("2026-10-18T16:00"))
    assert early.rain == 0 and late.rain > 0.3 and late.wind_from == 200


def test_summary_text_gives_a_sky_for_the_whole_hike() -> None:
    assert sky_from_summary("Clear sky").cloud < 0.1
    fog = sky_from_summary("Fog")
    assert fog.fog > 0.8 and fog.rain == 0
    mixed = sky_from_summary("Mainly clear, light rain at times", wind_kmh="30")
    assert 0 < mixed.rain < sky_from(61).rain and mixed.wind_kmh == 30
    assert sky_from_summary("Thunderstorm").storm
    assert sky_from_summary("Rain", temp_c="-2").snow > 0, "below freezing it falls as snow"
    assert sky_from_summary("") is None and weather_track({"weather": ""}) is None
    assert weather_track({"weather": "Overcast"}).at(None).cloud > 0.9


def test_weather_changes_the_light() -> None:
    grey = with_weather(DEFAULT_LIGHT, Sky(cloud=1.0))
    assert grey.diffuse < DEFAULT_LIGHT.diffuse * 0.3 and grey.ambient > DEFAULT_LIGHT.ambient
    r, g, b = grey.sky_top
    assert max(r, g, b) - min(r, g, b) < 30 < max(DEFAULT_LIGHT.sky_top) - min(DEFAULT_LIGHT.sky_top), "overcast: grey"
    foggy = with_weather(DEFAULT_LIGHT, Sky(cloud=1.0, fog=0.9))
    assert 2300 <= foggy.fog_end < DEFAULT_LIGHT.fog_end and foggy.fog_start < 600
    assert with_weather(DEFAULT_LIGHT, None) is DEFAULT_LIGHT
    # Rain arriving during the hike: keys before are dry, after are wet, and in between they blend
    rows = [[_utc("2026-10-18T14:00"), 1, 10, 0, 10, 270, 12], [_utc("2026-10-18T16:00"), 63, 100, 3, 25, 180, 10]]
    track = weather_track({"weather_hourly": json.dumps(rows)})
    keys = light_keys(4000, 42.4, -71.1, [(0, _utc("2026-10-18T14:00")), (4000, _utc("2026-10-18T16:00"))], track)
    assert keys[0].rain == 0 and keys[-1].rain > 0.5 and keys[-1].fog_end < keys[0].fog_end
    mid = light_at(keys, 2000)
    assert 0 < mid.rain < keys[-1].rain and 180 < mid.wind_from < 270
    assert light_keys(4000, 42.4, -71.1, [], track)[0].cloud > 0.2, "weather without a time"
    assert set(keys[0].to_json()) >= {"cloud", "fog", "rain", "snow", "wind", "windFrom", "storm"}


def test_desktop_replay_draws_the_hikes_weather(tmp_path) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.replay3d import Replay3DView
    from src.hiking.models import HikeSession
    from src.hiking.routing import route_from_trail_segment
    from test_replay3d import view_pack

    QApplication.instance() or QApplication([])
    pack = view_pack()
    route = route_from_trail_segment(next(t for t in pack.trails if t.name == "White Dot Trail"))
    for text in ("Heavy snow", "Thunderstorm", "Fog"):
        s = HikeSession(route_id=route.id, started_at="2026-01-14T15:00:00+00:00", ended_at="2026-01-14T16:00:00+00:00")
        s.conditions_logged["weather"] = text
        v = Replay3DView(route, pack, [], "us", session=s)
        v.resize(640, 400)
        v.dist = v.path.length / 2
        assert not v.grab().isNull() and v.light.cloud > 0.8
    assert v.light.fog_end < 5200
