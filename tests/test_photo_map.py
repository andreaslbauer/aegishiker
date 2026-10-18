"""Re-experience map: photo thumbnails on the map with time taken, hover preview, click select."""
import os
from pathlib import Path


def test_thumbnails_hover_preview_and_click(tmp_path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    from src.gui.charts.map_canvas import MapCanvas
    from src.gui.charts.photo_preview import format_time
    from src.hiking.models import ComposedRoute, HikePhotoPin

    app = QApplication.instance() or QApplication([])
    thumb = tmp_path / "t.jpg"
    Image.new("RGB", (300, 200), (200, 120, 60)).save(thumb)
    route = ComposedRoute(name="r", coords_3d=[[-71.0 + i * 0.001, 44.0, 500.0] for i in range(20)])
    pins = [
        HikePhotoPin(id="a", lat=44.0, lon=-70.995, date_taken=1755170000, thumb_path=str(thumb)),
        HikePhotoPin(id="b", lat=44.0, lon=-70.985, date_taken=1755173600),  # no image file
    ]
    canvas = MapCanvas()
    canvas.show_photo_thumbnails = True
    canvas.resize(900, 600)
    canvas.show()
    canvas.set_scene_data(trails=[], route=route, photo_pins=pins)
    canvas.fit_when_shown(route.coordinates + [(p.lat, p.lon) for p in pins], margin_px=95.0)
    canvas.grab()  # paint → lays out thumbnails

    assert {p.id for _r, p in canvas._photo_hits} == {"a", "b"}
    assert format_time(pins[0]) and ":" in format_time(pins[0])

    box, pin = next((r, p) for r, p in canvas._photo_hits if p.id == "a" and r.width() > 30)
    move = QMouseEvent(QEvent.Type.MouseMove, box.center(), canvas.mapToGlobal(box.center()),
                       Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(canvas, move)
    app.processEvents()
    assert canvas._preview is not None and canvas._preview.isVisible()
    assert canvas._preview.image_lbl.pixmap() is not None and not canvas._preview.image_lbl.pixmap().isNull()

    clicked = []
    canvas.photo_clicked.connect(clicked.append)
    for etype in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
        ev = QMouseEvent(etype, box.center(), canvas.mapToGlobal(box.center()), Qt.MouseButton.LeftButton,
                         Qt.MouseButton.LeftButton if etype == QEvent.Type.MouseButtonPress else Qt.MouseButton.NoButton,
                         Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(canvas, ev)
    assert clicked == ["a"]
    canvas.leaveEvent(QEvent(QEvent.Type.Leave))
    assert not canvas._preview.isVisible()
    canvas.hide()
