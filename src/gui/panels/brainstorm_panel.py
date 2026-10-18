"""AI Trail Brainstormer & Guided Interview panel."""
from typing import Dict, List, Optional
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.gui.app_state import AppState
from src.gui.busy import busy
from src.gui.widgets import Card, SectionHeader, add_form_row, form_layout, page_layout
from src.hiking.interview import INTERVIEW_QUESTIONS
from src.hiking.models import ComposedRoute
from src.hiking.units import format_distance, format_duration, format_duration_min, format_elevation


class BrainstormPanel(QWidget):
    route_selected_for_composer = Signal(str)  # route_id

    def __init__(self, app_state: AppState, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.app_state = app_state
        self.candidates: List[ComposedRoute] = []

        layout = page_layout(self)
        header = SectionHeader(
            "AI Brainstorm",
            "Answer a few questions about your group and the day — AegisHiker ranks the best routes in this region.",
        )
        layout.addWidget(header)

        body = QHBoxLayout()
        body.setSpacing(16)

        # Left: the interview
        form_card = Card("Your hike", "Who's going and what you're after")
        form = form_layout()
        self.combos: Dict[str, QComboBox] = {}
        for q in INTERVIEW_QUESTIONS:
            combo = QComboBox()
            for opt in q.options:
                combo.addItem(opt, opt)
            idx = combo.findData(q.default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            self.combos[q.id] = combo
            add_form_row(form, q.prompt.split(". ", 1)[-1].rstrip(":"), combo)
        form_card.body.addLayout(form)
        form_card.body.addStretch(1)
        run_btn = QPushButton("Recommend Trails")
        run_btn.setObjectName("PrimaryButton")
        run_btn.clicked.connect(self.run_brainstorm)
        form_card.body.addWidget(run_btn)
        body.addWidget(form_card, 4)

        # Right: ranked routes + briefing
        results = Card("Recommended routes", "Best matches first — open one in the Trail Composer to fine-tune it")
        self.cand_table = QTableWidget(0, 5)
        self.cand_table.setHorizontalHeaderLabels(["Route", "Distance", "Ascent", "Est. Time", "Difficulty"])
        hdr = self.cand_table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in range(1, 5):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.cand_table.verticalHeader().setVisible(False)
        self.cand_table.setShowGrid(False)
        self.cand_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        results.body.addWidget(self.cand_table, 3)

        self.summary_browser = QTextBrowser()
        self.summary_browser.setObjectName("Inset")
        self.summary_browser.setMaximumHeight(120)
        results.body.addWidget(self.summary_browser, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        send_btn = QPushButton("Open in Trail Composer  →")
        send_btn.setObjectName("PrimaryButton")
        send_btn.clicked.connect(self._send_selected_to_composer)
        btn_row.addWidget(send_btn)
        results.body.addLayout(btn_row)
        body.addWidget(results, 6)
        layout.addLayout(body, 1)

        self.app_state.destination_changed.connect(lambda _: self.run_brainstorm())
        self.run_brainstorm()

    def collect_answers(self) -> Dict[str, str]:
        return {qid: str(combo.currentData()) for qid, combo in self.combos.items()}

    def run_brainstorm(self) -> None:
        answers = self.collect_answers()
        with busy(self, "Brainstorming trail candidates…", show_dialog=False):
            result = self.app_state.interview_engine.run_interview(
                answers=answers,
                destination_id=self.app_state.active_destination_id,
                use_llm=False,
            )
        self.candidates = result["candidates"]
        unit = str(self.app_state.settings.get("unit_system", "metric"))

        self.cand_table.setRowCount(len(self.candidates))
        for idx, route in enumerate(self.candidates):
            self.cand_table.setItem(idx, 0, QTableWidgetItem(f"#{idx + 1}  {route.name}"))
            self.cand_table.setItem(idx, 1, QTableWidgetItem(format_distance(route.distance_m, unit)))
            self.cand_table.setItem(idx, 2, QTableWidgetItem(f"+{format_elevation(route.elevation_gain_m, unit)}"))
            self.cand_table.setItem(idx, 3, QTableWidgetItem(format_duration_min(route.estimated_duration_min)))
            self.cand_table.setItem(idx, 4, QTableWidgetItem(route.difficulty.value.capitalize()))

        if self.candidates:
            self.cand_table.selectRow(0)
        self.summary_browser.setPlainText(result["summary"])

    def _send_selected_to_composer(self) -> None:
        if not self.candidates:
            return
        rows = self.cand_table.selectionModel().selectedRows()
        idx = rows[0].row() if rows else 0
        chosen = self.candidates[min(idx, len(self.candidates) - 1)]
        self.app_state.db.upsert_route(chosen)
        self.app_state.set_active_route(chosen.id)
        self.route_selected_for_composer.emit(chosen.id)
