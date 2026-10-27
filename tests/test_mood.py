"""How the hike felt: photo moods grade the replay's scene, slow the walk and widen the view."""
import os
from pathlib import Path

from src.hiking.mood import FAMILIES, NEUTRAL, mood_at, mood_of, stop_mood


def test_mood_words_map_to_families() -> None:
    assert mood_of("serene").name == "calm" and mood_of("Dramatic").name == "dramatic"
    assert mood_of("['tranquil', 'serene', 'dreamy']").name == "calm"
    assert mood_of("vibrant and lively").name == "joyful"
    both = mood_of("serene, melancholic")
    calm, moody = FAMILIES["calm"], FAMILIES["moody"]
    assert both.vignette == (calm.vignette + moody.vignette) / 2
    assert mood_of("neutral") is None and mood_of("") is None
    assert stop_mood(["", "happy", "happy, energetic"]).name == "joyful"


def test_mood_is_felt_around_its_stop() -> None:
    stops = [(1000.0, FAMILIES["dramatic"]), (3000.0, None)]
    assert mood_at(stops, 1050) == (FAMILIES["dramatic"], 1.0)
    mood, w = mood_at(stops, 1400)
    assert mood.name == "dramatic" and 0 < w < 1
    assert mood_at(stops, 2000) == (NEUTRAL, 0.0) and mood_at(stops, 3000) == (NEUTRAL, 0.0)


def test_desktop_replay_grades_slows_and_widens_at_a_dramatic_stop(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtWidgets import QApplication

    from src.gui.replay3d import Replay3DView
    from src.hiking.models import HikePhotoPin
    from src.hiking.routing import route_from_trail_segment
    from test_replay3d import view_pack

    QApplication.instance() or QApplication([])
    pack = view_pack()
    route = route_from_trail_segment(next(t for t in pack.trails if t.name == "White Dot Trail"))
    img = tmp_path / "p.jpg"
    Image.new("RGB", (300, 200), (90, 120, 160)).save(img)
    c = route.coords_3d[len(route.coords_3d) * 3 // 4]

    def view(mood: str) -> Replay3DView:
        pin = HikePhotoPin(lat=c[1], lon=c[0], photo_path=str(img), distance_along_m=route.distance_m * 0.75,
                           ai_mood=mood)
        v = Replay3DView(route, pack, [pin], "us")
        v.resize(640, 400)
        v.dist = v.photo_stops[0].along_m - 120
        return v

    grand, plain = view("dramatic, majestic"), view("neutral")
    assert grand.speed_factor() < plain.speed_factor() * 0.7
    assert grand._camera(640, 400).z > plain._camera(640, 400).z, "pulled back and up for the view"
    g, p = grand.grab().toImage(), plain.grab().toImage()
    assert grand.mood[0].name == "dramatic" and plain.mood[1] == 0
    grand._draw_mood = lambda *a: None  # the same view without its grade
    ungraded = grand.grab().toImage()
    assert g.pixelColor(8, 200).value() < ungraded.pixelColor(8, 200).value(), "darkened edges"
