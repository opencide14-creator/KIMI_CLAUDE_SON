"""
AgentCard widget for the Ultra Orchestrator GUI.

Displays a subtask's status, ID, API key, duration, and action buttons
in a small card with a colored left border indicating status.
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QColor
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QSizePolicy,
)


class AgentCard(QFrame):
    """
    Custom card widget representing a single agent/subtask.

    Displays subtask ID, status with colored dot, assigned API key,
    execution duration, and REASON / OUTPUT action buttons.

    Signals:
        reasoning_requested(str): Emitted when REASON button is clicked.
        output_requested(str): Emitted when OUTPUT button is clicked.
        detail_requested(str): Emitted on double-click.
    """

    reasoning_requested = pyqtSignal(str)
    output_requested = pyqtSignal(str)
    detail_requested = pyqtSignal(str)

    # ── Theme Colors ────────────────────────────────────────────────────
    BG_CARD = "#2d2d2d"
    BG_PANEL = "#252525"
    TEXT_PRIMARY = "#e0e0e0"
    TEXT_SECONDARY = "#a0a0a0"
    BORDER = "#3d3d3d"
    ACCENT = "#0d7377"

    # Status colors
    STATUS_COLORS = {
        "APPROVED": "#4CAF50",
        "RUNNING": "#2196F3",
        "VALIDATING": "#FFC107",
        "REJECTED": "#F44336",
        "PENDING": "#9E9E9E",
        "QUEUED": "#9E9E9E",
        "SPAWNING": "#00BCD4",
        "DEAD_LETTER": "#B71C1C",
        "BLOCKED": "#9C27B0",
    }

    # Hover effect: slightly lighter background
    BG_HOVER = "#353535"

    def __init__(self, subtask_data: dict = None, parent=None):
        """
        Initialize the AgentCard.

        Args:
            subtask_data: Dictionary with keys:
                - subtask_id (str)
                - status (str): One of the STATUS_COLORS keys
                - assigned_key (str): API key label, e.g. "K2"
                - started_at (float, optional): Timestamp
                - completed_at (float, optional): Timestamp
            parent: Parent widget.
        """
        super().__init__(parent)

        self._subtask_id = ""
        self._status = "PENDING"
        self._assigned_key = "--"
        self._duration = "--"

        self.setObjectName("agentCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Raised)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._setup_ui()
        self._apply_base_style()

        if subtask_data:
            self.update_data(subtask_data)

    # ── Public Methods ──────────────────────────────────────────────────

    def update_data(self, subtask_data: dict):
        """
        Update all displayed fields from a subtask dictionary.

        Args:
            subtask_data: Dict with keys subtask_id, status, assigned_key,
                          started_at, completed_at.
        """
        self._subtask_id = str(subtask_data.get("subtask_id", self._subtask_id))
        self._status = subtask_data.get("status", self._status)
        self._assigned_key = str(subtask_data.get("assigned_key", "--"))

        # Compute duration
        started = subtask_data.get("started_at")
        completed = subtask_data.get("completed_at")
        if started is not None and completed is not None:
            try:
                dur = float(completed) - float(started)
                if dur < 0:
                    self._duration = "--"
                elif dur < 1.0:
                    self._duration = f"{dur * 1000:.0f}ms"
                elif dur < 60.0:
                    self._duration = f"{dur:.1f}s"
                else:
                    mins = int(dur // 60)
                    secs = dur % 60
                    self._duration = f"{mins}m {secs:.0f}s"
            except (ValueError, TypeError):
                self._duration = "--"
        else:
            self._duration = "--"

        # Update labels
        self._id_label.setText(self._subtask_id)
        self._status_label.setText(f"  {self._status}")
        self._key_label.setText(f"Key: {self._assigned_key}")
        self._duration_label.setText(self._duration)

        # Update status dot color
        color = self._get_status_color(self._status)
        self._status_dot.setStyleSheet(
            f"background-color: {color}; border-radius: 5px;"
        )

        # Enable/disable OUTPUT button
        self._output_btn.setEnabled(self._status == "APPROVED")
        if self._status == "APPROVED":
            self._output_btn.setStyleSheet(
                f"QPushButton {{ background-color: {self.STATUS_COLORS['APPROVED']}; "
                f"color: #ffffff; border: none; border-radius: 4px; "
                f"padding: 4px 12px; font-size: 11px; font-weight: bold; }}"
                f"QPushButton:hover {{ background-color: #43A047; }}"
                f"QPushButton:pressed {{ background-color: #388E3C; }}"
            )
        else:
            self._output_btn.setStyleSheet(
                f"QPushButton {{ background-color: #424242; "
                f"color: {self.TEXT_SECONDARY}; border: none; border-radius: 4px; "
                f"padding: 4px 12px; font-size: 11px; }}"
                f"QPushButton:disabled {{ background-color: #424242; "
                f"color: #616161; }}"
            )

        # Apply status-based left border styling
        self._apply_status_style(self._status)

    # ── UI Setup ────────────────────────────────────────────────────────

    def _setup_ui(self):
        """Create layouts, labels, and buttons."""
        # Main vertical layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(12, 8, 12, 8)
        main_layout.setSpacing(6)

        # ── Top row: ID + status dot + status text ──
        top_row = QHBoxLayout()
        top_row.setSpacing(6)
        top_row.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Subtask ID label
        self._id_label = QLabel("ST-000")
        self._id_label.setFont(QFont("JetBrains Mono", 11, QFont.Weight.Bold))
        self._id_label.setStyleSheet(f"color: {self.TEXT_PRIMARY}; background: transparent;")
        top_row.addWidget(self._id_label)

        top_row.addSpacing(8)

        # Status colored dot
        self._status_dot = QLabel()
        self._status_dot.setFixedSize(10, 10)
        self._status_dot.setStyleSheet("background-color: #9E9E9E; border-radius: 5px;")
        top_row.addWidget(self._status_dot)

        # Status text label
        self._status_label = QLabel("  PENDING")
        self._status_label.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Medium))
        self._status_label.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        top_row.addWidget(self._status_label)

        top_row.addStretch()
        main_layout.addLayout(top_row)

        # ── Info row: API key + duration ──
        info_row = QHBoxLayout()
        info_row.setSpacing(12)

        # API key label
        self._key_label = QLabel("Key: --")
        self._key_label.setFont(QFont("JetBrains Mono", 9))
        self._key_label.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        info_row.addWidget(self._key_label)

        info_row.addStretch()

        # Duration label
        self._duration_label = QLabel("--")
        self._duration_label.setFont(QFont("JetBrains Mono", 9))
        self._duration_label.setStyleSheet(f"color: {self.ACCENT}; background: transparent;")
        self._duration_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        info_row.addWidget(self._duration_label)

        main_layout.addLayout(info_row)

        # ── Buttons row ──
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.setAlignment(Qt.AlignmentFlag.AlignRight)

        # REASON button
        self._reason_btn = QPushButton("REASON")
        self._reason_btn.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Bold))
        self._reason_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._reason_btn.setFixedHeight(26)
        self._reason_btn.setStyleSheet(
            f"QPushButton {{ background-color: #37474F; "
            f"color: {self.TEXT_PRIMARY}; border: none; border-radius: 4px; "
            f"padding: 4px 12px; font-size: 11px; font-weight: bold; }}"
            f"QPushButton:hover {{ background-color: #455A64; }}"
            f"QPushButton:pressed {{ background-color: #546E7A; }}"
        )
        self._reason_btn.clicked.connect(self._on_reason_clicked)
        btn_row.addWidget(self._reason_btn)

        # OUTPUT button
        self._output_btn = QPushButton("OUTPUT")
        self._output_btn.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Bold))
        self._output_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._output_btn.setFixedHeight(26)
        self._output_btn.setEnabled(False)
        self._output_btn.setStyleSheet(
            f"QPushButton {{ background-color: #424242; "
            f"color: {self.TEXT_SECONDARY}; border: none; border-radius: 4px; "
            f"padding: 4px 12px; font-size: 11px; }}"
            f"QPushButton:disabled {{ background-color: #424242; color: #616161; }}"
        )
        self._output_btn.clicked.connect(self._on_output_clicked)
        btn_row.addWidget(self._output_btn)

        main_layout.addLayout(btn_row)

        # Size policy
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.setFixedHeight(100)

    # ── Styling ─────────────────────────────────────────────────────────

    def _apply_base_style(self):
        """Apply the base card stylesheet."""
        self.setStyleSheet(f"""
            QFrame#agentCard {{
                background-color: {self.BG_CARD};
                border: 1px solid {self.BORDER};
                border-left: 4px solid #9E9E9E;
                border-radius: 6px;
            }}
            QFrame#agentCard:hover {{
                background-color: {self.BG_HOVER};
                border: 1px solid #505050;
                border-left: 4px solid #9E9E9E;
            }}
        """)

    def _apply_status_style(self, status: str):
        """
        Apply color scheme based on subtask status.

        Updates the left border color to reflect the current status.
        """
        color = self._get_status_color(status)
        self.setStyleSheet(f"""
            QFrame#agentCard {{
                background-color: {self.BG_CARD};
                border: 1px solid {self.BORDER};
                border-left: 4px solid {color};
                border-radius: 6px;
            }}
            QFrame#agentCard:hover {{
                background-color: {self.BG_HOVER};
                border: 1px solid #505050;
                border-left: 4px solid {color};
            }}
        """)

    def _get_status_color(self, status: str) -> str:
        """
        Return the hex color string for a given status.

        Args:
            status: Subtask status string.

        Returns:
            Hex color code. Falls back to gray for unknown statuses.
        """
        return self.STATUS_COLORS.get(status, "#9E9E9E")

    # ── Event Handlers ──────────────────────────────────────────────────

    def mouseDoubleClickEvent(self, event):
        """Emit detail_requested signal on double-click."""
        self.detail_requested.emit(self._subtask_id)
        super().mouseDoubleClickEvent(event)

    def _on_reason_clicked(self):
        """Handle REASON button click."""
        if self._subtask_id:
            self.reasoning_requested.emit(self._subtask_id)

    def _on_output_clicked(self):
        """Handle OUTPUT button click."""
        if self._subtask_id:
            self.output_requested.emit(self._subtask_id)

    # ── Properties ──────────────────────────────────────────────────────

    @property
    def subtask_id(self) -> str:
        """Return the current subtask ID."""
        return self._subtask_id

    @property
    def status(self) -> str:
        """Return the current subtask status."""
        return self._status
