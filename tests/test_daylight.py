"""Time of day in the 3D replays: the sun's position, the hike's clock along the route and the light
keyframes both replays draw with."""
import os
from datetime import datetime, timezone
from pathlib import Path

from src.hiking.daylight import (
    DEFAULT_LIGHT,
    hike_clock,
    key_index,
    light_at,
    light_keys,
    sun_position,
    time_at,
)
from src.hiking.models import HikePhotoPin, HikeSession

BOSTON = (42.36, -71.06)


def _utc(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).timestamp()


def test_sun_position_matches_known_days() -> None:
    az, el = sun_position(_utc("2026-06-21T16:42"), *BOSTON)  # solar noon at the solstice
    assert abs(az - 180) < 4 and abs(el - 71.1) < 0.7
    _az, el = sun_position(_utc("2026-10-18T11:05"), *BOSTON)  # sunrise (07:05 EDT)
    assert abs(el) < 1.5
    az, el = sun_position(_utc("2026-10-18T21:55"), *BOSTON)  # sunset, in the west-south-west
    assert abs(el) < 1.5 and 240 < az < 270
    assert sun_position(_utc("2026-10-19T03:00"), *BOSTON)[1] < -30


def test_clock_runs_from_start_through_photos_to_end() -> None:
    start, end = "2026-10-18T09:30:00+00:00", "2026-10-18T11:30:00+00:00"
    s = HikeSession(route_id="r", started_at=start, ended_at=end)
    pins = [HikePhotoPin(lat=0, lon=0, session_id=s.id, distance_along_m=d, date_taken=int(_utc(t)))
            for d, t in ((3000, "2026-10-18T10:00"), (6000, "2026-10-18T11:00"),
                         (4500, "2026-10-18T12:30"))]  # the last one is out of order and left out
    pins.append(HikePhotoPin(lat=0, lon=0, session_id="another-hike", distance_along_m=5000,
                             date_taken=int(_utc("2025-01-01T08:00"))))
    clock = hike_clock(8000, pins, s)
    assert [d for d, _t in clock] == [0, 3000, 6000, 8000]
    assert time_at(clock, 1500) == _utc("2026-10-18T09:45")
    assert time_at(clock, 4500) == _utc("2026-10-18T10:30")
    assert hike_clock(8000, [], None) == []
    # One photo only: walk on from it at an ordinary pace
    one = hike_clock(8000, pins[:1], None)
    assert time_at(one, 4100) > time_at(one, 3000)


def test_light_keys_follow_the_sun_into_dusk() -> None:
    assert light_keys(5000, *BOSTON, []) == [DEFAULT_LIGHT]
    clock = [(0.0, _utc("2026-10-18T20:30")), (6000.0, _utc("2026-10-18T22:30"))]  # 4:30–6:30 pm EDT
    keys = light_keys(6000, *BOSTON, clock)
    assert len(keys) == 9 and keys[-1].along_m == 6000
    assert keys[0].elevation > 8 and keys[-1].elevation < -5
    assert keys[0].tint[0] > keys[-1].tint[0] and keys[-1].diffuse == 0, "night: only (tinted) ambient light"
    assert keys[0].sun[0] < 0 and keys[0].sun[2] > 0, "afternoon sun in the west, above the horizon"
    mid = light_at(keys, 375)
    assert keys[0].sky_top != mid.sky_top or keys[0].tint != mid.tint or mid.ts > keys[0].ts
    assert key_index(keys, 760) == 1 and key_index(keys, 6000) == 8


def test_desktop_replay_is_lit_for_the_hikes_time(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.replay3d import Replay3DView
    from src.hiking.routing import route_from_trail_segment
    from test_replay3d import view_pack

    QApplication.instance() or QApplication([])
    pack = view_pack()
    route = route_from_trail_segment(next(t for t in pack.trails if t.name == "White Dot Trail"))
    noon = HikeSession(route_id=route.id, started_at="2026-08-14T16:00:00+00:00", ended_at="2026-08-14T17:00:00+00:00")
    dusk = HikeSession(route_id=route.id, started_at="2026-08-14T23:20:00+00:00", ended_at="2026-08-15T00:40:00+00:00")
    day_view = Replay3DView(route, pack, [], "us", session=noon)
    dusk_view = Replay3DView(route, pack, [], "us", session=dusk)
    plain = Replay3DView(route, pack, [], "us")
    for v in (day_view, dusk_view, plain):
        v.resize(640, 400)
        v.dist = v.path.length * 0.9
        assert not v.grab().isNull()
    assert plain.light is plain.light_keys[0] is DEFAULT_LIGHT
    assert day_view.light.tint == (255, 255, 255) and dusk_view.light.tint[2] < 200
    assert dusk_view.light.ts is not None and len(dusk_view._lit) >= 1
    lum = lambda img: sum(img.pixelColor(x, y).value() for x in range(0, 640, 40) for y in range(0, 400, 40))  # noqa: E731
    assert lum(dusk_view.grab().toImage()) < lum(day_view.grab().toImage()), "dusk is darker"
