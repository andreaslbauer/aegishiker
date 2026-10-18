"""Duplicate photos are avoided when matching, saving, listing and logging hikes."""
from src.core.database import Database
from src.hiking.models import HikePhotoPin, HikeSession
from src.hiking.photo_matcher import dedupe_pins


def _pin(pid, vid=None, ts=None, lat=44.1, lon=-71.1, **kw):
    return HikePhotoPin(id=pid, voyager_photo_id=vid, date_taken=ts, lat=lat, lon=lon, **kw)


def test_dedupe_by_voyager_id_and_identical_shot() -> None:
    pins = [
        _pin("a", vid=1, ts=100, filename="x.jpg"),
        _pin("b", vid=1, ts=100, filename="x.jpg", is_starred=True),  # same Voyager photo, richer
        _pin("c", vid=7, ts=100, filename="x (1).jpg"),  # duplicate import: same moment & spot
        _pin("d", vid=2, ts=200, lat=44.2),
        _pin("e", lat=44.3),  # nothing to compare: kept
    ]
    assert [p.id for p in dedupe_pins(pins)] == ["b", "d", "e"]


def test_saving_same_photo_again_updates_instead_of_duplicating(seeded_db: Database) -> None:
    session = HikeSession(route_id=None, title="t")
    seeded_db.save_session(session)
    seeded_db.save_photo_pin(_pin("first", vid=42, ts=100, session_id=session.id, caption="old"))
    seeded_db.save_photo_pin(_pin("rescan", vid=42, ts=100, session_id=session.id, caption="new"))  # e.g. Rescan
    pins = seeded_db.get_photo_pins(session.id)
    assert [(p.id, p.caption) for p in pins] == [("first", "new")]


def test_dialog_unchecks_days_already_saved(seeded_db, test_settings, mock_voyager_db) -> None:
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from src.gui.app_state import AppState
    from src.gui.dialogs.i_hiked_this_dialog import IHikedThisDialog
    from src.hiking.routing import route_from_trail_segment

    QApplication.instance() or QApplication([])
    test_settings.set("voyager_db_path", str(mock_voyager_db))
    state = AppState(db=seeded_db, settings=test_settings)
    trail = next(t for t in seeded_db.list_trails("white_mountains_nh") if t.id == "wmnf_franconia_ridge_loop")

    first = IHikedThisDialog(state, route_from_trail_segment(trail))
    first.finalize_hike()
    again = IHikedThisDialog(state, route_from_trail_segment(trail))
    item = again.days_list.item(0)
    assert item.checkState() == Qt.CheckState.Unchecked and "already saved" in item.text()
