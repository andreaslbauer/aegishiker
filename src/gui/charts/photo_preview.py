"""Photo thumbnails for maps and a hover popup with a larger view.

Images come from AegisPhotoVoyager's thumbnail cache (pin.thumb_path) or the original file
(pin.photo_path). Scaled pixmaps are cached per path and size, so maps can repaint cheaply.
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from src.hiking.models import HikePhotoPin

_CACHE: Dict[Tuple[str, int], QPixmap] = {}


def photo_pixmap(pin: HikePhotoPin, max_side: int, prefer_full: bool = False) -> Optional[QPixmap]:
    """Scaled image for a pin (None if no readable image). `prefer_full` uses the original photo
    when available (sharper large previews); otherwise the small Voyager thumbnail comes first."""
    paths = [pin.photo_path, pin.thumb_path] if prefer_full else [pin.thumb_path, pin.photo_path]
    for path in paths:
        if not path:
            continue
        key = (path, max_side)
        if key in _CACHE:
            return _CACHE[key]
        if not Path(path).is_file():
            continue
        pix = QPixmap(path)
        if pix.isNull():
            continue
        scaled = pix.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.SmoothTransformation)
        _CACHE[key] = scaled
        return scaled
    return None


def taken_local(pin: HikePhotoPin) -> Optional[datetime]:
    if pin.date_taken:
        return datetime.fromtimestamp(int(pin.date_taken))
    if pin.date_iso:
        try:
            return datetime.fromisoformat(pin.date_iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
        except ValueError:
            return None
    return None


def format_time(pin: HikePhotoPin) -> str:
    """'8:42 AM' (no leading zero, portable across platforms)."""
    when = taken_local(pin)
    return f"{when:%I:%M %p}".lstrip("0") if when else ""


def format_datetime(pin: HikePhotoPin) -> str:
    when = taken_local(pin)
    return f"{when:%a %b %d, %Y} · {f'{when:%I:%M %p}'.lstrip('0')}" if when else "Time unknown"


class PhotoPreviewPopup(QFrame):
    """Frameless tooltip-style window with a larger photo, its time and caption."""

    MAX_SIDE = 460

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setStyleSheet(
            "QFrame { background: #0B1610; border: 1px solid #F39C12; border-radius: 6px; }"
            "QLabel { border: none; color: #ECF5EE; }"
        )
        col = QVBoxLayout(self)
        col.setContentsMargins(6, 6, 6, 6)
        col.setSpacing(4)
        self.image_lbl = QLabel()
        self.image_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_lbl = QLabel()
        self.time_lbl.setStyleSheet("font-weight: 700; color: #F4D03F;")
        self.caption_lbl = QLabel()
        self.caption_lbl.setWordWrap(True)
        self.caption_lbl.setMaximumWidth(self.MAX_SIDE)
        col.addWidget(self.image_lbl)
        col.addWidget(self.time_lbl)
        col.addWidget(self.caption_lbl)
        self.pin_id: Optional[str] = None

    def show_pin(self, pin: HikePhotoPin, global_pos: QPoint) -> None:
        if pin.id != self.pin_id:
            self.pin_id = pin.id
            pix = photo_pixmap(pin, self.MAX_SIDE, prefer_full=True)
            if pix is not None:
                self.image_lbl.setPixmap(pix)
                self.image_lbl.setText("")
            else:
                self.image_lbl.setPixmap(QPixmap())
                self.image_lbl.setText("📷  Image file not available on this computer")
            self.time_lbl.setText(format_datetime(pin))
            self.caption_lbl.setText(pin.caption or pin.ai_summary or pin.filename or "")
            self.adjustSize()
        # Beside the cursor, kept on screen
        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry() if screen else None
        x, y = global_pos.x() + 18, global_pos.y() + 18
        if area is not None:
            if x + self.width() > area.right():
                x = global_pos.x() - self.width() - 18
            if y + self.height() > area.bottom():
                y = max(area.top(), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()

    def hide_preview(self) -> None:
        self.pin_id = None
        self.hide()
