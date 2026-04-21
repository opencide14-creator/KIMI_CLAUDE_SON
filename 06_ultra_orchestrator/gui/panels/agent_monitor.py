"""
Agent Monitor Panel — Ultra Orchestrator GUI

A panel showing active agent cards in a scrollable grid. Displays a progress bar
and summary statistics.  Cards are dynamically created / updated / removed based
on the current subtask list.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
)

# AgentCard is provided by the widgets package; import guard for standalone use.
try:
    from gui.widgets.agent_card import AgentCard
except ImportError:
    AgentCard = None  # type: ignore[misc, assignment]


# ---------------------------------------------------------------------------
# Dark theme colours
# ---------------------------------------------------------------------------
BG_MAIN = "#1e1e1e"
BG_CARD = "#2d2d2d"
BG_PANEL = "#252525"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#a0a0a0"
BORDER = "#3d3d3d"
ACCENT = "#0d7377"
SUCCESS = "#4CAF50"
ERROR = "#F44336"
WARNING = "#FFC107"

# Grid columns for agent cards
_GRID_COLUMNS = 4


class AgentMonitor(QWidget):
    """Panel that displays agent cards in a scrollable grid with progress."""

    # ── Signals (forwarded from AgentCard) ───────────────────────────────────
    agent_detail_requested = pyqtSignal(str)
    """Emitted when a user requests detail for a specific agent/subtask.

    Parameter:
        subtask_id – the subtask identifier.
    """

    agent_reasoning_requested = pyqtSignal(str)
    """Emitted when a user requests reasoning for a specific agent/subtask.

    Parameter:
        subtask_id – the subtask identifier.
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._agent_cards: dict[str, QWidget] = {}
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("agentMonitor")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ---- Header: title + active count -----------------------------------
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        self._title_label = QLabel("Agent Monitor")
        self._title_label.setObjectName("titleLabel")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        self._active_count_label = QLabel("0/20 Active")
        self._active_count_label.setObjectName("activeCountLabel")
        header_layout.addWidget(self._active_count_label)

        main_layout.addLayout(header_layout)

        # ---- Progress bar ---------------------------------------------------
        self._progress_bar = QProgressBar()
        self._progress_bar.setObjectName("progressBar")
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.setFormat("%p%")
        self._progress_bar.setMinimumHeight(22)
        main_layout.addWidget(self._progress_bar)

        # ---- Summary label --------------------------------------------------
        self._summary_label = QLabel("0 approved / 0 total (0%) ETA: -- min")
        self._summary_label.setObjectName("summaryLabel")
        main_layout.addWidget(self._summary_label)

        # ---- Scroll area for agent cards ------------------------------------
        self._scroll_area = QScrollArea()
        self._scroll_area.setObjectName("scrollArea")
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(
            QSizePolicy.Policy.Fixed.value  # hide horizontal scrollbar conceptually
        )
        # Correct scroll-bar policy enum usage:
        from PyQt6.QtCore import Qt
        self._scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)

        # Container widget inside scroll area
        self._grid_container = QWidget()
        self._grid_container.setObjectName("gridContainer")
        self._grid_layout = self._create_grid_layout()
        self._grid_container.setLayout(self._grid_layout)
        self._scroll_area.setWidget(self._grid_container)

        main_layout.addWidget(self._scroll_area)

    # ── Grid layout helper ───────────────────────────────────────────────────
    def _create_grid_layout(self) -> QGridLayout:
        """Create the QGridLayout used to arrange agent cards."""
        grid = QGridLayout()
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setSpacing(10)
        grid.setAlignment(
            # PyQt6 alignment uses AlignmentFlag
            __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignTop
            | __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.AlignmentFlag.AlignLeft,
        )
        return grid

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#agentMonitor {{
                background-color: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
            }}
            QLabel#titleLabel {{
                font-size: 14px;
                font-weight: bold;
                color: {TEXT_PRIMARY};
            }}
            QLabel#activeCountLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
            }}
            QLabel#summaryLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
            }}
            QProgressBar#progressBar {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                text-align: center;
                font-size: 12px;
            }}
            QProgressBar#progressBar::chunk {{
                background-color: {ACCENT};
                border-radius: 6px;
            }}
            QScrollArea#scrollArea {{
                background-color: transparent;
                border: none;
            }}
            QWidget#gridContainer {{
                background-color: transparent;
            }}
        """)

    # ── Public API ───────────────────────────────────────────────────────────
    def update_agent_cards(self, subtasks: list[dict]) -> None:
        """Refresh all agent cards from the given subtask list.

        * Creates new cards for subtask IDs not yet present.
        * Updates existing cards when the subtask data changed.
        * Removes cards for subtask IDs no longer in the list.

        Args:
            subtasks: List of dicts, each containing at least 'subtask_id' and
                      other fields needed by AgentCard.
        """
        if AgentCard is None:
            return  # graceful degradation when AgentCard is unavailable

        current_ids = {str(st.get("subtask_id", "")) for st in subtasks}

        # 1) Remove stale cards
        stale_ids = [sid for sid in self._agent_cards if sid not in current_ids]
        for sid in stale_ids:
            card = self._agent_cards.pop(sid, None)
            if card is not None:
                card.deleteLater()

        # 2) Create or update cards
        for st in subtasks:
            sid = str(st.get("subtask_id", ""))
            if not sid:
                continue
            if sid not in self._agent_cards:
                card = AgentCard(sid, st)
                # Forward card signals
                if hasattr(card, "detail_requested"):
                    card.detail_requested.connect(self.agent_detail_requested.emit)
                if hasattr(card, "reasoning_requested"):
                    card.reasoning_requested.connect(self.agent_reasoning_requested.emit)
                self._agent_cards[sid] = card
            else:
                card = self._agent_cards[sid]
                if hasattr(card, "update_data"):
                    card.update_data(st)

        # 3) Re-layout grid
        self._rebuild_grid()

        # 4) Update active count label
        active = sum(
            1 for st in subtasks
            if st.get("status", "").upper() in ("RUNNING", "PENDING", "RETRYING")
        )
        self._active_count_label.setText(f"{active}/20 Active")

    def _rebuild_grid(self) -> None:
        """Clear and repopulate the grid layout with current agent cards."""
        # Remove all widgets from grid
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)

        # Re-add in order
        for idx, (sid, card) in enumerate(sorted(self._agent_cards.items())):
            row = idx // _GRID_COLUMNS
            col = idx % _GRID_COLUMNS
            self._grid_layout.addWidget(card, row, col)

    def update_progress(self, approved: int, total: int) -> None:
        """Update the progress bar and summary label.

        Args:
            approved: Number of approved/completed subtasks.
            total:    Total number of subtasks.
        """
        if total > 0:
            pct = int((approved / total) * 100)
        else:
            pct = 0

        self._progress_bar.setValue(min(pct, 100))

        # Estimate ETA (placeholder heuristic: 30 sec per remaining task)
        remaining = total - approved
        eta_min = max(1, int(remaining * 0.5)) if remaining > 0 else 0
        eta_str = f"{eta_min} min" if eta_min > 0 else "done"

        self._summary_label.setText(
            f"{approved} approved / {total} total ({pct}%)  ETA: {eta_str}"
        )

    def clear_all(self) -> None:
        """Remove all agent cards and reset progress."""
        for card in self._agent_cards.values():
            card.deleteLater()
        self._agent_cards.clear()
        self._rebuild_grid()
        self._active_count_label.setText("0/20 Active")
        self._progress_bar.setValue(0)
        self._summary_label.setText("0 approved / 0 total (0%) ETA: -- min")
