"""AI Trail Guide DockWidget with quick-action chips and ProposalEnvelope preview."""
from typing import Optional
from PySide6.QtWidgets import (
    QDockWidget,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.busy import busy
from src.gui.dialogs.proposal_dialog import ProposalDialog


class ChatDock(QDockWidget):
    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__("AI Trail Guide & Route Co-Pilot", parent)
        self.app_state = app_state
        self.setMinimumWidth(310)

        container = QWidget()
        layout = QVBoxLayout(container)

        # Quick-action prompt buttons
        chips_row = QHBoxLayout()
        for label, prompt in [
            ("Close Loop", "Close this route into a loop"),
            ("Reverse", "Reverse the route direction"),
            ("Water Needs", "How much water and calories should I bring?"),
        ]:
            btn = QPushButton(label)
            btn.clicked.connect(lambda _, p=prompt: self.send_prompt(p))
            chips_row.addWidget(btn)
        layout.addLayout(chips_row)

        self.transcript = QTextBrowser()
        self.transcript.setOpenExternalLinks(False)
        self.transcript.append(
            "<b>AegisHiker Trail Guide:</b> Ask me to close your route into a loop, reverse direction, "
            "filter out muddy/icy trails, or calculate water &amp; calorie needs."
        )
        layout.addWidget(self.transcript, 1)

        input_row = QHBoxLayout()
        self.input_edit = QLineEdit()
        self.input_edit.setPlaceholderText("Ask Trail Guide (e.g. 'Make this a loop')...")
        self.input_edit.returnPressed.connect(self._on_send_clicked)
        send_btn = QPushButton("Ask")
        send_btn.setObjectName("PrimaryButton")
        send_btn.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.input_edit, 1)
        input_row.addWidget(send_btn)
        layout.addLayout(input_row)

        self.setWidget(container)

    def _on_send_clicked(self) -> None:
        text = self.input_edit.text().strip()
        if not text:
            return
        self.input_edit.clear()
        self.send_prompt(text)

    def send_prompt(self, prompt: str) -> None:
        self.transcript.append(f"<br><b>You:</b> {prompt}")
        with busy(self, "Trail Guide is working on your request…", title="Trail Guide"):
            result = self.app_state.agent_engine.process_user_prompt(
                user_prompt=prompt,
                active_route_id=self.app_state.active_route_id,
                active_destination_id=self.app_state.active_destination_id,
                use_llm=False,
            )
        self.transcript.append(f"<b>Trail Guide:</b> {result['reply']}")

        proposal = result.get("proposal")
        proposed_route = result.get("proposed_route")
        current_route = self.app_state.get_active_route()
        if proposal and proposed_route and current_route:
            dlg = ProposalDialog(proposal, current_route, proposed_route, self)
            if dlg.exec():
                self.app_state.db.upsert_route(proposed_route)
                self.app_state.set_active_route(proposed_route.id)
                self.transcript.append(
                    f"<i>Applied proposal: {proposal.summary}</i>"
                )

