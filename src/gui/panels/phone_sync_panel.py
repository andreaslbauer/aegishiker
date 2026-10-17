"""Mobile Companion & Offline Field Sync panel with QR code pairing."""
import io
from typing import Optional
import segno
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.widgets import SectionHeader, StatCard
from src.hiking.models import HikeSession, TelemetryPoint


class PhoneSyncPanel(QWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state

        layout = QVBoxLayout(self)
        header = SectionHeader(
            "Mobile Companion & Offline Field Sync",
            "Pair your phone over home Wi-Fi to cache regional trail packs in IndexedDB for zero-signal trailhead use.",
        )
        layout.addWidget(header)

        cards_row = QHBoxLayout()
        self.server_card = StatCard("LAN Server Status", "Stopped")
        self.dest_card = StatCard("Active Offline Pack", "--")
        self.route_card = StatCard("Active Route", "--")
        cards_row.addWidget(self.server_card)
        cards_row.addWidget(self.dest_card)
        cards_row.addWidget(self.route_card)
        layout.addLayout(cards_row)

        body = QHBoxLayout()

        # Left: QR code frame
        qr_frame = QFrame()
        qr_frame.setObjectName("CardFrame")
        qr_col = QVBoxLayout(qr_frame)
        qr_col.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lbl = QLabel("Start the LAN companion server to generate a pairing QR code")
        self.qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lbl.setMinimumSize(240, 240)
        qr_col.addWidget(self.qr_lbl)

        self.url_edit = QLineEdit()
        self.url_edit.setReadOnly(True)
        self.url_edit.setPlaceholderText("http://<lan-ip>:<port>/h/<token>/hiker.html")
        qr_col.addWidget(self.url_edit)

        btn_row = QHBoxLayout()
        self.toggle_btn = QPushButton("Start Mobile Companion Server")
        self.toggle_btn.setObjectName("PrimaryButton")
        self.toggle_btn.clicked.connect(self.toggle_server)
        sim_btn = QPushButton("Simulate Mobile GPS Telemetry Sync")
        sim_btn.clicked.connect(self.simulate_mobile_sync)
        btn_row.addWidget(self.toggle_btn)
        btn_row.addWidget(sim_btn)
        qr_col.addLayout(btn_row)
        body.addWidget(qr_frame, 5)

        # Right: Offline architecture instructions
        info_frame = QFrame()
        info_frame.setObjectName("CardFrame")
        info_col = QVBoxLayout(info_frame)
        info_lbl = QLabel(
            "<h3>How Offline Trailhead Mode Works</h3>"
            "<ol>"
            "<li><b>Before Leaving Home (On Wi-Fi):</b> Click <i>Start Mobile Companion Server</i> and scan the QR code "
            "with your phone's camera.</li>"
            "<li><b>Cache Regional Pack Offline:</b> Tap <i>Cache Pack Offline</i> in the mobile web app. "
            "The full trail graph, elevation profiles, and POIs are saved in browser IndexedDB + Service Worker cache.</li>"
            "<li><b>At the Trailhead (Zero Cell Signal):</b> Open the cached companion app to filter out muddy, icy, or "
            "closed trails, view your live GPS dot on the vector map, and get off-trail alerts (&gt;45m).</li>"
            "<li><b>Back Home:</b> Tap <i>Sync Telemetry to Desktop</i> to upload your recorded GPS breadcrumbs into "
            "AegisHiker's Telemetry &amp; Re-experience studios.</li>"
            "</ol>"
        )
        info_lbl.setWordWrap(True)
        info_col.addWidget(info_lbl)
        info_col.addStretch(1)
        body.addWidget(info_frame, 5)

        layout.addLayout(body, 1)

        self.app_state.destination_changed.connect(lambda _: self.refresh_labels())
        self.app_state.route_changed.connect(lambda _: self.refresh_labels())
        self.refresh_labels()

    def refresh_labels(self) -> None:
        dest = self.app_state.get_active_destination()
        route = self.app_state.get_active_route()
        self.dest_card.set_value(dest.name if dest else "--", dest.region if dest else "")
        self.route_card.set_value(route.name if route else "--")
        if self.app_state.trail_server.is_running:
            url = self.app_state.trail_server.get_pairing_url()
            self.server_card.set_value("Running (LAN)", f"Port {self.app_state.trail_server.port}")
            self.url_edit.setText(url)
            self._render_qr(url)
            self.toggle_btn.setText("Stop Mobile Companion Server")
        else:
            self.server_card.set_value("Stopped", "Click Start below")
            self.toggle_btn.setText("Start Mobile Companion Server")

    def toggle_server(self) -> None:
        if self.app_state.trail_server.is_running:
            self.app_state.trail_server.stop()
        else:
            self.app_state.trail_server.start()
        self.refresh_labels()

    def _render_qr(self, url: str) -> None:
        qr = segno.make(url, error="m")
        buf = io.BytesIO()
        qr.save(buf, kind="png", scale=6, dark="#2ECC71", light="#0F1B14", border=2)
        img = QImage.fromData(buf.getvalue(), "PNG")
        self.qr_lbl.setPixmap(QPixmap.fromImage(img))

    def simulate_mobile_sync(self) -> None:
        route = self.app_state.get_active_route()
        if not route or not route.coordinates:
            return
        session = HikeSession(
            route_id=route.id,
            destination_id=route.destination_id,
            status="completed",
            actual_distance_m=route.distance_m,
            actual_elevation_gain_m=route.elevation_gain_m,
            actual_duration_min=route.estimated_duration_min,
            notes="Simulated mobile GPS breadcrumb sync",
        )
        self.app_state.db.save_session(session)
        pts = [
            TelemetryPoint(
                session_id=session.id,
                timestamp=f"2026-10-17T09:{idx:02d}:00",
                lat=c[0],
                lon=c[1],
                ele_m=c[2],
                accuracy_m=4.5,
                speed_mps=1.3,
            )
            for idx, c in enumerate(route.coordinates[:55])
        ]
        self.app_state.db.save_telemetry_points(pts)
        self.app_state.set_active_session(session.id)
        self.app_state.status_message.emit(
            f"Simulated mobile sync of {len(pts)} GPS points for '{route.name}'."
        )

