"""Qt-native multi-page PDF exporter using QPdfWriter and QPainter."""
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QMarginsF, QRectF
from PySide6.QtGui import QColor, QFont, QPageLayout, QPageSize, QPainter, QPdfWriter, QPen

from src.hiking.models import ComposedRoute, Destination, GearItem, HikeSession, TrailPhotoPin
from src.hiking.units import format_distance, format_duration, format_duration_min, format_elevation


def export_trailhead_pdf(
    output_path: str | Path,
    route: ComposedRoute,
    destination: Optional[Destination] = None,
    gear_items: Optional[List[GearItem]] = None,
    session: Optional[HikeSession] = None,
    photos: Optional[List[TrailPhotoPin]] = None,
    emergency_contact: str = "",
    unit_system: str = "metric",
) -> Path:
    """Render a clean multi-section Trailhead Sheet & Leave-Behind Safety PDF."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    writer = QPdfWriter(str(out))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.Letter))
    writer.setResolution(300)
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)

    painter = QPainter(writer)
    try:
        w = writer.width()
        h = writer.height()

        title_font = QFont("Sans Serif", 16, QFont.Weight.Bold)
        h2_font = QFont("Sans Serif", 12, QFont.Weight.Bold)
        body_font = QFont("Sans Serif", 9)

        y = 80
        painter.setFont(title_font)
        painter.setPen(QColor("#1E8449"))
        painter.drawText(60, y, f"AegisHiker Trailhead & Safety Sheet — {route.name}")

        y += 80
        painter.setFont(body_font)
        painter.setPen(QColor("#222222"))
        dest_label = f"{destination.name} ({destination.region})" if destination else route.destination_id
        painter.drawText(60, y, f"Destination: {dest_label}   |   Difficulty: {route.difficulty.value.upper()}")

        y += 65
        dist_str = format_distance(route.distance_m, unit_system)
        gain_str = format_elevation(route.elevation_gain_m, unit_system)
        loss_str = format_elevation(route.elevation_loss_m, unit_system)
        dur_str = format_duration_min(route.estimated_duration_min)
        painter.drawText(
            60,
            y,
            f"Distance: {dist_str}   |   Ascent: +{gain_str}   |   Descent: -{loss_str}   |   Est. Time: {dur_str}",
        )

        # Draw mini elevation profile box
        y += 70
        box_rect = QRectF(60, y, w - 120, 420)
        painter.setPen(QPen(QColor("#1E8449"), 3))
        painter.drawRect(box_rect)

        prof = route.elevation_profile
        if len(prof) >= 2:
            max_d = max(1.0, prof[-1].distance_m)
            min_e = min(p.ele_m for p in prof)
            max_e = max(p.ele_m for p in prof)
            span_e = max(10.0, max_e - min_e)
            prev_pt = None
            painter.setPen(QPen(QColor("#27AE60"), 5))
            for sample in prof:
                px = box_rect.left() + 30 + (sample.distance_m / max_d) * (box_rect.width() - 60)
                py = box_rect.bottom() - 30 - ((sample.ele_m - min_e) / span_e) * (box_rect.height() - 60)
                if prev_pt is not None:
                    painter.drawLine(int(prev_pt[0]), int(prev_pt[1]), int(px), int(py))
                prev_pt = (px, py)

        y += 500
        painter.setFont(h2_font)
        painter.setPen(QColor("#1E8449"))
        painter.drawText(60, y, "Leave-Behind Safety & Emergency Protocol")

        y += 65
        painter.setFont(body_font)
        painter.setPen(QColor("#222222"))
        contact_line = emergency_contact or "Notify local park ranger dispatch if party has not checked in by 20:00."
        painter.drawText(60, y, f"Emergency Contact / Overdue Action: {contact_line}")

        if route.coordinates:
            start = route.coordinates[0]
            end = route.coordinates[-1]
            y += 55
            painter.drawText(
                60,
                y,
                f"Start Coords: ({start[0]:.5f}, {start[1]:.5f})   |   End Coords: ({end[0]:.5f}, {end[1]:.5f})",
            )

        if gear_items:
            y += 90
            painter.setFont(h2_font)
            painter.setPen(QColor("#1E8449"))
            painter.drawText(60, y, "10 Essentials & Gear Checklist")
            painter.setFont(body_font)
            painter.setPen(QColor("#222222"))
            for item in gear_items[:12]:
                y += 50
                if y > h - 150:
                    break
                chk = "[x]" if item.checked else "[ ]"
                painter.drawText(80, y, f"{chk} {item.name} ({item.category}) — {item.weight_g:.0f} g")

        if session or photos:
            writer.newPage()
            y = 90
            painter.setFont(title_font)
            painter.setPen(QColor("#1E8449"))
            painter.drawText(60, y, f"Post-Hike Summary & Photo Story — {route.name}")
            y += 80
            painter.setFont(body_font)
            painter.setPen(QColor("#222222"))
            if session:
                painter.drawText(
                    60,
                    y,
                    f"Session Status: {session.status}   |   Actual Distance: {format_distance(session.actual_distance_m, unit_system)}",
                )
                y += 60
                if session.notes:
                    painter.drawText(60, y, f"Notes: {session.notes[:110]}")
                    y += 60
            if photos:
                y += 40
                painter.setFont(h2_font)
                painter.setPen(QColor("#1E8449"))
                painter.drawText(60, y, f"Matched Trail Photos ({len(photos)})")
                painter.setFont(body_font)
                painter.setPen(QColor("#222222"))
                for pin in photos[:15]:
                    y += 55
                    if y > h - 120:
                        break
                    painter.drawText(
                        80,
                        y,
                        f"• {pin.caption} @ {format_distance(pin.distance_along_route_m, unit_system)} ({pin.lat:.4f}, {pin.lon:.4f})",
                    )
    finally:
        painter.end()

    return out
