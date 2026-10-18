"""Photo thumbnails for maps and a hover popup with a larger view.

Images come from AegisPhotoVoyager's thumbnail cache (pin.thumb_path) or the original file
(pin.photo_path), including iPhone HEIC (via pillow-heif) and RAW files (via a JPEG shot alongside).
Scaled pixmaps are cached per path and size, so maps can repaint cheaply.
"""
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QGuiApplication, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

from src.hiking.models import HikePhotoPin

_CACHE: Dict[tuple, QPixmap] = {}

RAW_EXTS = {".rw2", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".raf", ".pef", ".srw"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".avi", ".mts", ".3gp"}
_heif_ready: Optional[bool] = None


def _heif_available() -> bool:
    """Register pillow-heif once (iPhone HEIC photos); the app works without it."""
    global _heif_ready
    if _heif_ready is None:
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            _heif_ready = True
        except Exception:
            _heif_ready = False
    return _heif_ready


def _source_candidates(path: str) -> List[str]:
    """The original first; for RAW files (Qt/Pillow can't decode them) a JPEG/HEIC shot alongside
    with the same name; converted copies such as IMG_1234.HEIC.jpg as a fallback."""
    p = Path(path)
    ext = p.suffix.lower()
    out: List[str] = []
    if ext not in RAW_EXTS and ext not in VIDEO_EXTS:
        out.append(path)
    if ext in RAW_EXTS:
        for sib in (".jpg", ".JPG", ".jpeg", ".JPEG", ".heic", ".HEIC"):
            out.append(str(p.with_suffix(sib)))
    out.append(path + ".jpg")  # Voyager / converter copies of HEIC originals
    return out


def _decode(path: str, max_side: int) -> Optional[QImage]:
    """Decode an image file scaled to fit max_side, honouring EXIF orientation. Thread-safe."""
    if not Path(path).is_file():
        return None
    reader = QImageReader(path)
    reader.setAutoTransform(True)
    if reader.canRead():
        size = reader.size()
        if size.isValid() and max(size.width(), size.height()) > max_side:
            reader.setScaledSize(size.scaled(max_side, max_side, Qt.AspectRatioMode.KeepAspectRatio))
        img = reader.read()
        if not img.isNull():
            return img
    if Path(path).suffix.lower() in (".heic", ".heif") and not _heif_available():
        return None
    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            im.draft("RGB", (max_side, max_side))
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            data = im.tobytes()
            return QImage(data, im.width, im.height, 3 * im.width, QImage.Format.Format_RGB888).copy()
    except Exception:
        return None


def load_photo_image(pin: HikePhotoPin, max_side: int) -> Optional[QImage]:
    """Best available image for a pin at up to max_side px: the full-size original (HEIC and RAW
    handled as above), else Voyager's 300 px thumbnail. Safe to call from a worker thread."""
    paths = _source_candidates(pin.photo_path) if pin.photo_path else []
    if pin.thumb_path:
        paths.append(pin.thumb_path)
    for path in paths:
        img = _decode(path, max_side)
        if img is not None:
            return img
    return None


def photo_pixmap(pin: HikePhotoPin, max_side: int, prefer_full: bool = False) -> Optional[QPixmap]:
    """Scaled image for a pin (None if no readable image), cached. Small sizes use the Voyager
    thumbnail when there is one; `prefer_full` (or a missing thumbnail) decodes the original."""
    key = (pin.photo_path or pin.thumb_path or pin.id, max_side, prefer_full)
    if key in _CACHE:
        return _CACHE[key]
    img = None
    if not prefer_full and pin.thumb_path:
        img = _decode(pin.thumb_path, max_side)
    if img is None:
        img = load_photo_image(pin, max_side)
    pix = QPixmap.fromImage(img) if img is not None else None
    if pix is not None:
        _CACHE[key] = pix
    return pix


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
            caption = pin.caption or pin.ai_summary or pin.filename or ""
            if pin.people:
                caption += ("\n" if caption else "") + "👥 " + ", ".join(pin.people)
            self.caption_lbl.setText(caption)
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
