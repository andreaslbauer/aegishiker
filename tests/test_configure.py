"""Configure dialog and the 3D walkthrough preferences: people, similar photos, playback speed."""
import os
import sqlite3
import zlib
from array import array
from pathlib import Path

from src.core.database import Database
from src.core.settings import Settings
from src.hiking.models import HikePhotoPin
from src.hiking.replay_plan import VisualSignature, collapse_similar


def _pin(pid: int, t: int, lat: float = 44.0, **kw) -> HikePhotoPin:
    return HikePhotoPin(voyager_photo_id=pid, filename=f"IMG_{pid}.jpg", photo_path=f"/p/IMG_{pid}.jpg",
                        date_taken=t, lat=lat, lon=-71.0, **kw)


def _vec(*v: float):
    n = sum(x * x for x in v) ** 0.5
    return [x / n for x in v]


def test_collapse_similar_keeps_the_best_of_each_burst() -> None:
    burst = [_pin(1, 1000), _pin(2, 1002), _pin(3, 1005)]
    other_view = _pin(4, 1010)  # same spot, different scene
    later = _pin(5, 5000)  # looks the same but much later
    far = _pin(6, 1012, lat=44.01)  # looks the same, ~1 km away
    unknown = _pin(7, 1003)  # no Voyager data: always kept
    look = _vec(1, 0.1, 0)
    sigs = {
        1: VisualSignature(look, quality=50),
        2: VisualSignature(_vec(1, 0.12, 0), quality=80),
        3: VisualSignature(_vec(1, 0.11, 0.01), quality=60),
        4: VisualSignature(_vec(0, 1, 0)),
        5: VisualSignature(look),
        6: VisualSignature(look),
    }
    pins = burst + [other_view, later, far, unknown]
    kept = collapse_similar(pins, sigs)
    assert [p.voyager_photo_id for p in kept] == [2, 4, 5, 6, 7], "best quality of the burst, input order"

    starred = [_pin(1, 1000, is_starred=True), _pin(2, 1002)]
    assert [p.voyager_photo_id for p in collapse_similar(starred, sigs)] == [1], "a starred photo wins"

    # Without embeddings, perceptual hashes a few bits apart are alike
    hashed = {1: VisualSignature(phash=0xF0F0), 2: VisualSignature(phash=0xF0F1), 3: VisualSignature(phash=0x0F0F)}
    assert [p.voyager_photo_id for p in collapse_similar(burst, hashed)] == [1, 3]
    assert collapse_similar(burst, {}) == burst


def test_voyager_visual_signatures(mock_voyager_db: Path) -> None:
    from src.core.voyager_bridge import VoyagerBridge

    conn = sqlite3.connect(str(mock_voyager_db))
    conn.executescript("""
        ALTER TABLE photos ADD COLUMN phash TEXT;
        CREATE TABLE photo_embeddings (photo_id INTEGER PRIMARY KEY, embedding_blob BLOB);
        CREATE TABLE photo_quality (photo_id INTEGER PRIMARY KEY, overall INTEGER);
        UPDATE photos SET phash = '8000d6579edef8f8' WHERE id = 501;
        UPDATE photos SET phash = '0000000000000000' WHERE id = 503;
        INSERT INTO photo_quality VALUES (501, 72);
    """)
    conn.execute("INSERT INTO photo_embeddings VALUES (?, ?)", (501, zlib.compress(array("f", [3.0, 4.0]).tobytes())))
    conn.execute("INSERT INTO photo_embeddings VALUES (?, ?)", (502, array("f", [0.0, 2.0]).tobytes()))
    conn.commit()
    conn.close()

    sigs = VoyagerBridge(str(mock_voyager_db)).visual_signatures([501, 502, 503])
    assert sigs[501].embedding == [0.6, 0.8] and sigs[501].phash == 0x8000D6579EDEF8F8 and sigs[501].quality == 72
    assert sigs[502].embedding == [0.0, 1.0] and sigs[502].phash is None
    assert 503 not in sigs, "an all-zero hash and no embedding: nothing to compare"


def test_configure_dialog_saves_preferences(seeded_db: Database, test_settings: Settings) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from src.gui.app_state import AppState
    from src.gui.dialogs.configure_dialog import ConfigureDialog

    QApplication.instance() or QApplication([])
    state = AppState(db=seeded_db, settings=test_settings)
    dlg = ConfigureDialog(state)
    assert [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())] == ["General", "3D Walkthrough"]
    assert dlg.dark_radio.isChecked() and dlg.people_chk.isChecked() and not dlg.cluster_chk.isChecked()
    assert dlg.speed_combo.currentData() == 1.0

    dlg.light_radio.setChecked(True)
    dlg.people_chk.setChecked(False)
    dlg.cluster_chk.setChecked(True)
    dlg.speed_combo.setCurrentIndex(dlg.speed_combo.findData(2.0))
    dlg.accept()
    dlg.deleteLater()

    again = Settings(settings_file=test_settings._file)
    assert again.get("theme") == "light" and again.get("replay_include_people") is False
    assert again.get("replay_one_per_cluster") is True and again.get("replay_speed") == 2.0

    reopened = ConfigureDialog(state)
    assert reopened.light_radio.isChecked() and not reopened.people_chk.isChecked()
    assert reopened.cluster_chk.isChecked() and reopened.speed_combo.currentData() == 2.0
    reopened.reject()
    reopened.deleteLater()

    # Replays leave out photos with people in them
    alone, together = _pin(1, 1000), _pin(2, 2000, people=["Alice"])
    assert state.replay_photos([alone, together]) == [alone]
    state.settings.set("replay_include_people", True)
    assert len(state.replay_photos([alone, together])) == 2
    state.close()
