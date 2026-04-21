"""
Quality Stats Panel — Ultra Orchestrator GUI

Displays quality gate statistics including approved, rejected, retrying, and
dead-letter counts along with average retries and approval rate.
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QFrame,
)


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


class QualityStatsPanel(QWidget):
    """Panel showing quality gate pass/fail statistics."""

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("qualityStatsPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # ---- Title ----------------------------------------------------------
        self._title_label = QLabel("Quality Gate Stats")
        self._title_label.setObjectName("titleLabel")
        main_layout.addWidget(self._title_label)

        # ---- Stats grid -----------------------------------------------------
        grid_widget = QFrame()
        grid_widget.setObjectName("statsGrid")
        grid_layout = QGridLayout(grid_widget)
        grid_layout.setContentsMargins(12, 12, 12, 12)
        grid_layout.setSpacing(16)

        # Row 0: Approved
        self._approved_name = QLabel("Approved")
        self._approved_name.setObjectName("statNameApproved")
        grid_layout.addWidget(self._approved_name, 0, 0)

        self._approved_count = QLabel("0")
        self._approved_count.setObjectName("statCountApproved")
        grid_layout.addWidget(self._approved_count, 0, 1)

        self._approved_pct = QLabel("(0%)")
        self._approved_pct.setObjectName("statPctApproved")
        grid_layout.addWidget(self._approved_pct, 0, 2)

        # Row 1: Rejected
        self._rejected_name = QLabel("Rejected")
        self._rejected_name.setObjectName("statNameRejected")
        grid_layout.addWidget(self._rejected_name, 1, 0)

        self._rejected_count = QLabel("0")
        self._rejected_count.setObjectName("statCountRejected")
        grid_layout.addWidget(self._rejected_count, 1, 1)

        self._rejected_pct = QLabel("(0%)")
        self._rejected_pct.setObjectName("statPctRejected")
        grid_layout.addWidget(self._rejected_pct, 1, 2)

        # Row 2: Retrying
        self._retrying_name = QLabel("Retrying")
        self._retrying_name.setObjectName("statNameRetrying")
        grid_layout.addWidget(self._retrying_name, 2, 0)

        self._retrying_count = QLabel("0")
        self._retrying_count.setObjectName("statCountRetrying")
        grid_layout.addWidget(self._retrying_count, 2, 1)

        self._retrying_pct = QLabel("(0%)")
        self._retrying_pct.setObjectName("statPctRetrying")
        grid_layout.addWidget(self._retrying_pct, 2, 2)

        # Row 3: Dead Letter
        self._dead_name = QLabel("Dead Letter")
        self._dead_name.setObjectName("statNameDead")
        grid_layout.addWidget(self._dead_name, 3, 0)

        self._dead_count = QLabel("0")
        self._dead_count.setObjectName("statCountDead")
        grid_layout.addWidget(self._dead_count, 3, 1)

        self._dead_pct = QLabel("(0%)")
        self._dead_pct.setObjectName("statPctDead")
        grid_layout.addWidget(self._dead_pct, 3, 2)

        main_layout.addWidget(grid_widget)
        main_layout.addStretch()

        # ---- Summary row ----------------------------------------------------
        self._avg_retries_label = QLabel("Avg Retries: 0.0")
        self._avg_retries_label.setObjectName("avgRetriesLabel")
        main_layout.addWidget(self._avg_retries_label)

        self._approval_rate_label = QLabel("Approval Rate: 0.0%")
        self._approval_rate_label.setObjectName("approvalRateLabel")
        main_layout.addWidget(self._approval_rate_label)

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#qualityStatsPanel {{
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
            QFrame#statsGrid {{
                background-color: {BG_CARD};
                border: 1px solid {BORDER};
                border-radius: 6px;
            }}
            QLabel[objectName^="statName"] {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-weight: bold;
            }}
            QLabel[objectName^="statCount"] {{
                color: {TEXT_PRIMARY};
                font-size: 14px;
                font-weight: bold;
                min-width: 40px;
            }}
            QLabel#statCountApproved {{
                color: {SUCCESS};
            }}
            QLabel#statCountRejected {{
                color: {ERROR};
            }}
            QLabel#statCountRetrying {{
                color: {WARNING};
            }}
            QLabel#statCountDead {{
                color: {ERROR};
            }}
            QLabel[objectName^="statPct"] {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
            }}
            QLabel#avgRetriesLabel, QLabel#approvalRateLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-weight: bold;
                padding-top: 4px;
                border-top: 1px solid {BORDER};
            }}
        """)

    # ── Public API ───────────────────────────────────────────────────────────
    def update_stats(self, stats: dict) -> None:
        """Update all labels from the provided statistics dictionary.

        Args:
            stats: Dictionary with keys:
                - approved: int
                - rejected: int
                - retrying: int
                - dead_letter: int
                - total: int
                - avg_retries: float
                - approval_rate: float (0.0 – 1.0)
        """
        approved = int(stats.get("approved", 0))
        rejected = int(stats.get("rejected", 0))
        retrying = int(stats.get("retrying", 0))
        dead_letter = int(stats.get("dead_letter", 0))
        total = int(stats.get("total", 0))
        avg_retries = float(stats.get("avg_retries", 0.0))
        approval_rate = float(stats.get("approval_rate", 0.0))

        # Update counts
        self._approved_count.setText(str(approved))
        self._rejected_count.setText(str(rejected))
        self._retrying_count.setText(str(retrying))
        self._dead_count.setText(str(dead_letter))

        # Update percentages
        if total > 0:
            self._approved_pct.setText(f"({approved / total * 100:.1f}%)")
            self._rejected_pct.setText(f"({rejected / total * 100:.1f}%)")
            self._retrying_pct.setText(f"({retrying / total * 100:.1f}%)")
            self._dead_pct.setText(f"({dead_letter / total * 100:.1f}%)")
        else:
            self._approved_pct.setText("(0%)")
            self._rejected_pct.setText("(0%)")
            self._retrying_pct.setText("(0%)")
            self._dead_pct.setText("(0%)")

        # Update summary
        self._avg_retries_label.setText(f"Avg Retries: {avg_retries:.1f}")
        self._approval_rate_label.setText(
            f"Approval Rate: {approval_rate * 100:.1f}%"
        )

    def clear_stats(self) -> None:
        """Reset all statistics labels to zero."""
        self._approved_count.setText("0")
        self._rejected_count.setText("0")
        self._retrying_count.setText("0")
        self._dead_count.setText("0")
        self._approved_pct.setText("(0%)")
        self._rejected_pct.setText("(0%)")
        self._retrying_pct.setText("(0%)")
        self._dead_pct.setText("(0%)")
        self._avg_retries_label.setText("Avg Retries: 0.0")
        self._approval_rate_label.setText("Approval Rate: 0.0%")
