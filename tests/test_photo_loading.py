"""Full-size photo loading: HEIC (iPhone), RAW + JPEG pairs, EXIF rotation, readable-copy preference."""
import os
from pathlib import Path

import pytest
from PIL import Image

from src.hiking.models import HikePhotoPin
from src.hiking.photo_matcher import dedupe_pins


@pytest.fixture(autouse=True)
def _qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    yield QApplication.instance() or QApplication([])


def _pin(path, thumb=None, **kw):
    return HikePhotoPin(lat=44.0, lon=-71.0, photo_path=str(path), thumb_path=str(thumb) if thumb else None, **kw)


def test_heic_original_loads_at_full_size(tmp_path: Path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    from src.gui.charts.photo_preview import load_photo_image

    heic = tmp_path / "IMG_0001.HEIC"
    try:
        pillow_heif.from_pillow(Image.new("RGB", (2400, 1600), (90, 140, 200))).save(str(heic), quality=60)
    except Exception as e:  # pragma: no cover - encoder not built into this wheel
        pytest.skip(f"HEIF encoder unavailable: {e}")
    thumb = tmp_path / "thumb_300.jpg"
    Image.new("RGB", (300, 200)).save(thumb)
    img = load_photo_image(_pin(heic, thumb), 2000)
    assert img is not None and (img.width(), img.height()) == (2000, 1333), "decoded original, not thumbnail"


def test_raw_uses_jpeg_alongside_and_exif_rotation(tmp_path: Path) -> None:
    from src.gui.charts.photo_preview import load_photo_image

    raw = tmp_path / "P1000123.RW2"
    raw.write_bytes(b"not decodable")
    jpg = tmp_path / "P1000123.JPG"
    exif = Image.Exif()
    exif[0x0112] = 6  # orientation: rotate 90° clockwise to display
    Image.new("RGB", (1600, 1200), (200, 100, 50)).save(jpg, exif=exif)
    img = load_photo_image(_pin(raw), 4000)
    assert img is not None and (img.width(), img.height()) == (1200, 1600), "JPEG twin, upright"


def test_dedupe_prefers_readable_copy_and_keeps_details() -> None:
    heic = _pin("/p/IMG_1.HEIC", is_starred=True, ai_summary="Summit", date_taken=100, voyager_photo_id=1)
    jpg = _pin("/p/IMG_1.HEIC.jpg", date_taken=100, voyager_photo_id=2)  # converted copy: same shot
    (winner,) = dedupe_pins([heic, jpg])
    assert winner.photo_path.endswith(".jpg") and winner.is_starred and winner.ai_summary == "Summit"
