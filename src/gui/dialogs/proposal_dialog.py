"""Modal dialog to preview and accept/reject AI ProposalEnvelope route edits."""
from typing import Optional
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.hiking.models import ComposedRoute, ProposalEnvelope
from src.hiking.units import format_distance, format_duration, format_duration_min


class ProposalDialog(QDialog):
    def __init__(
        self,
        envelope: ProposalEnvelope,
        current_route: ComposedRoute,
        proposed_route: ComposedRoute,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.envelope = envelope
        self.current_route = current_route
        self.proposed_route = proposed_route

        self.setWindowTitle(f"Review AI Route Proposal — {envelope.summary}")
        self.resize(560, 380)

        layout = QVBoxLayout(self)
        title = QLabel(f"<b>{envelope.summary}</b>")
        layout.addWidget(title)

        rationale = QLabel(envelope.rationale)
        rationale.setWordWrap(True)
        rationale.setStyleSheet("color: #98B2A1;")
        layout.addWidget(rationale)

        diff_list = QListWidget()
        diff_list.addItem(
            f"Distance: {format_distance(current_route.distance_m)}  →  {format_distance(proposed_route.distance_m)}"
        )
        diff_list.addItem(
            f"Elevation Gain: +{current_route.elevation_gain_m:.0f} m  →  +{proposed_route.elevation_gain_m:.0f} m"
        )
        diff_list.addItem(
            f"Estimated Duration: {format_duration_min(current_route.estimated_duration_min)}  →  "
            f"{format_duration_min(proposed_route.estimated_duration_min)}"
        )
        for op in envelope.operations:
            diff_list.addItem(f"Operation [{op.op_type}]: {op.reason or op.parameters}")
        layout.addWidget(diff_list, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        reject_btn = QPushButton("Reject")
        reject_btn.clicked.connect(self.reject)
        accept_btn = QPushButton("Apply Proposed Route")
        accept_btn.setObjectName("PrimaryButton")
        accept_btn.clicked.connect(self.accept)
        btn_row.addWidget(reject_btn)
        btn_row.addWidget(accept_btn)
        layout.addLayout(btn_row)

