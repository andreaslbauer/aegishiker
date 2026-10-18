"""Review the photos matched to a hike: a thumbnail grid with a large preview, where each photo can
be kept or left out of the hike. Leaving a photo out never touches the file or AegisPhotoVoyager.

The dialog only decides; the caller saves (see `kept_pins` / `removed_pins`).
"""
from typing import Dict, List, Optional

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from src.gui.charts.photo_preview import format_datetime, format_time, photo_pixmap
from src.hiking.models import HikePhotoPin
from src.hiking.units import format_distance, format_elevation

THUMB = 150
ROLE_INDEX = Qt.ItemDataRole.UserRole


def _faded(pix: QPixmap) -> QPixmap:
    """A dimmed, struck-through copy for photos being left out."""
    out = QPixmap(pix.size())
    out.fill(QColor(11, 22, 16))
    p = QPainter(out)
    p.setOpacity(0.28)
    p.drawPixmap(0, 0, pix)
    p.setOpacity(1.0)
    p.setPen(QColor("#E74C3C"))
    p.drawLine(0, 0, out.width(), out.height())
    p.drawLine(out.width(), 0, 0, out.height())
    p.end()
    return out


class PhotoReviewDialog(QDialog):
    def __init__(self, pins: List[HikePhotoPin], units: str = "metric", title: str = "",
                 parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Review Photos — {title}" if title else "Review Photos")
        self.resize(1180, 760)
        self.units = units
        self.pins = sorted(pins, key=lambda p: (p.date_taken or 0, p.distance_along_m))
        self.keep: List[bool] = [True] * len(self.pins)
        self._thumbs: Dict[int, QPixmap] = {}
        self._next_thumb = 0

        col = QVBoxLayout(self)
        intro = QLabel(
            "Keep the photos that belong to this hike. Uncheck a photo (or select photos and press "
            "<b>Delete</b>) to leave it out — the photo file and AegisPhotoVoyager are never changed."
        )
        intro.setWordWrap(True)
        col.addWidget(intro)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.ViewMode.IconMode)
        self.grid.setIconSize(QSize(THUMB, int(THUMB * 0.75)))
        self.grid.setGridSize(QSize(THUMB + 22, int(THUMB * 0.75) + 46))
        self.grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.grid.setMovement(QListWidget.Movement.Static)
        self.grid.setUniformItemSizes(True)
        self.grid.setWordWrap(True)
        self.grid.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.grid.setMinimumWidth(420)
        placeholder = QPixmap(THUMB, int(THUMB * 0.75))
        placeholder.fill(QColor(22, 36, 28))
        for i, pin in enumerate(self.pins):
            item = QListWidgetItem(QIcon(placeholder), self._label(i))
            item.setData(ROLE_INDEX, i)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setToolTip(pin.caption or pin.filename)
            self.grid.addItem(item)
        self.grid.itemChanged.connect(self._on_item_changed)
        self.grid.currentItemChanged.connect(lambda cur, _prev: self._show(cur))
        self.grid.itemSelectionChanged.connect(lambda: self._show(self.grid.currentItem()))
        split.addWidget(self.grid)

        side = QWidget()
        side_col = QVBoxLayout(side)
        side_col.setContentsMargins(8, 0, 0, 0)
        self.preview = QLabel("Select a photo")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(420, 320)
        self.preview.setStyleSheet("background-color: #0B1610; border: 1px solid #223829; border-radius: 10px;")
        side_col.addWidget(self.preview, 1)
        self.meta = QLabel("")
        self.meta.setWordWrap(True)
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        self.meta.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        side_col.addWidget(self.meta)
        row = QHBoxLayout()
        self.toggle_btn = QPushButton("Leave Out")
        self.toggle_btn.clicked.connect(self._toggle_selected)
        prev_btn = QPushButton("◀")
        prev_btn.setToolTip("Previous photo (←)")
        prev_btn.clicked.connect(lambda: self._step(-1))
        next_btn = QPushButton("▶")
        next_btn.setToolTip("Next photo (→)")
        next_btn.clicked.connect(lambda: self._step(1))
        row.addWidget(prev_btn)
        row.addWidget(next_btn)
        row.addStretch(1)
        row.addWidget(self.toggle_btn)
        side_col.addLayout(row)
        split.addWidget(side)
        split.setSizes([620, 540])
        col.addWidget(split, 1)

        bottom = QHBoxLayout()
        self.count_lbl = QLabel()
        bottom.addWidget(self.count_lbl, 1)
        keep_all = QPushButton("Keep All")
        keep_all.setObjectName("FlatButton")
        keep_all.clicked.connect(lambda: self._set_all(True))
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        self.apply_btn = QPushButton("Apply")
        self.apply_btn.setObjectName("PrimaryButton")
        self.apply_btn.clicked.connect(self.accept)
        for b in (keep_all, cancel, self.apply_btn):
            bottom.addWidget(b)
        col.addLayout(bottom)

        for key in (QKeySequence.StandardKey.Delete, QKeySequence(Qt.Key.Key_Backspace)):
            QShortcut(key, self.grid, activated=lambda: self._set_selected(False))
        QShortcut(QKeySequence(Qt.Key.Key_Insert), self.grid, activated=lambda: self._set_selected(True))

        self._update_count()
        if self.pins:
            self.grid.setCurrentRow(0)
        # Thumbnails load a few at a time so the dialog opens at once even for hundreds of photos
        self._loader = QTimer(self)
        self._loader.setInterval(0)
        self._loader.timeout.connect(self._load_some)
        self._loader.start()

    # ── results ──────────────────────────────────────────────────────────
    def kept_pins(self) -> List[HikePhotoPin]:
        return [p for p, k in zip(self.pins, self.keep) if k]

    def removed_pins(self) -> List[HikePhotoPin]:
        return [p for p, k in zip(self.pins, self.keep) if not k]

    def set_kept(self, kept_ids: set) -> None:
        """Start with only these pins (by id) kept."""
        for i, pin in enumerate(self.pins):
            self._apply(i, pin.id in kept_ids)
        self._show(self.grid.currentItem())

    # ── internals ────────────────────────────────────────────────────────
    def _label(self, i: int) -> str:
        pin = self.pins[i]
        when = format_time(pin) or "no time"
        return f"{when} · {format_distance(pin.distance_along_m, self.units)}"

    def _load_some(self) -> None:
        for _ in range(6):
            i = self._next_thumb
            if i >= len(self.pins):
                self._loader.stop()
                return
            self._next_thumb += 1
            pix = photo_pixmap(self.pins[i], THUMB)
            if pix is not None:
                self._thumbs[i] = pix
                self._refresh_icon(i)

    def _refresh_icon(self, i: int) -> None:
        pix = self._thumbs.get(i)
        if pix is None:
            return
        item = self.grid.item(i)
        self.grid.blockSignals(True)
        item.setIcon(QIcon(pix if self.keep[i] else _faded(pix)))
        self.grid.blockSignals(False)

    def _apply(self, i: int, keep: bool) -> None:
        self.keep[i] = keep
        item = self.grid.item(i)
        self.grid.blockSignals(True)
        item.setCheckState(Qt.CheckState.Checked if keep else Qt.CheckState.Unchecked)
        item.setForeground(QColor("#ECF5EE") if keep else QColor("#7E8F84"))
        self.grid.blockSignals(False)
        self._refresh_icon(i)
        self._update_count()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        i = int(item.data(ROLE_INDEX))
        keep = item.checkState() == Qt.CheckState.Checked
        if keep != self.keep[i]:
            self._apply(i, keep)
            if item is self.grid.currentItem():
                self._show(item)

    def _selected_rows(self) -> List[int]:
        rows = sorted(self.grid.row(it) for it in self.grid.selectedItems())
        if not rows and self.grid.currentRow() >= 0:
            rows = [self.grid.currentRow()]
        return rows

    def _set_selected(self, keep: bool) -> None:
        for r in self._selected_rows():
            self._apply(r, keep)
        self._show(self.grid.currentItem())

    def _toggle_selected(self) -> None:
        rows = self._selected_rows()
        if rows:
            self._set_selected(not all(self.keep[r] for r in rows) if len(rows) > 1 else not self.keep[rows[0]])

    def _set_all(self, keep: bool) -> None:
        for i in range(len(self.pins)):
            self._apply(i, keep)
        self._show(self.grid.currentItem())

    def _step(self, d: int) -> None:
        if self.pins:
            self.grid.setCurrentRow((self.grid.currentRow() + d) % len(self.pins))

    def _update_count(self) -> None:
        kept = sum(self.keep)
        total = len(self.pins)
        left_out = total - kept
        self.count_lbl.setText(
            f"<b>{kept} of {total}</b> photos kept" + (f" · {left_out} left out" if left_out else "")
        )

    def _show(self, item: Optional[QListWidgetItem]) -> None:
        if item is None:
            return
        i = int(item.data(ROLE_INDEX))
        pin = self.pins[i]
        pix = photo_pixmap(pin, 1000, prefer_full=True)
        if pix is not None:
            self.preview.setPixmap(pix.scaled(self.preview.size() - QSize(8, 8), Qt.AspectRatioMode.KeepAspectRatio,
                                              Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(f"📷 {pin.filename}\n(image not readable)")
        parts = [f"<b style='color:#F4D03F'>{format_datetime(pin)}</b>"]
        if pin.caption and pin.caption != pin.filename:
            parts.append(f"<b>{_esc(pin.caption)}</b>")
        if pin.ai_summary and pin.ai_summary != pin.caption:
            parts.append(_esc(pin.ai_summary))
        facts = [f"{format_distance(pin.distance_along_m, self.units)} along the hike",
                 f"elevation {format_elevation(pin.ele_m, self.units)}"]
        if pin.cross_track_m > 25:
            facts.append(f"{format_distance(pin.cross_track_m, self.units)} off the trail")
        parts.append(" · ".join(facts))
        if pin.people:
            parts.append("👥 " + _esc(", ".join(pin.people)))
        parts.append(f"<span style='color:#98B2A1'>{_esc(pin.photo_path or pin.filename)}</span>")
        parts.append("<b style='color:#2ECC71'>Kept</b>" if self.keep[i] else "<b style='color:#E74C3C'>Left out</b>")
        self.meta.setText("<br>".join(parts))
        rows = self._selected_rows()
        if len(rows) > 1:
            self.toggle_btn.setText(f"Leave Out {len(rows)}" if any(self.keep[r] for r in rows) else f"Keep {len(rows)}")
        else:
            self.toggle_btn.setText("Leave Out" if self.keep[i] else "Keep")

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() == Qt.Key.Key_Left:
            self._step(-1)
        elif event.key() == Qt.Key.Key_Right:
            self._step(1)
        elif event.key() == Qt.Key.Key_Space:
            self._toggle_selected()
        else:
            super().keyPressEvent(event)


def _esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
