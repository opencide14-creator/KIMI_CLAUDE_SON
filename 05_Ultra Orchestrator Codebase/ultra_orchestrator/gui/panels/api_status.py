"""
API Status Panel — Ultra Orchestrator GUI

Shows API key status with a progress bar per key indicating capacity usage,
along with circuit-breaker state, backoff, and cost information.
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
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


class ApiStatusPanel(QWidget):
    """Panel that displays per-API-key capacity and status information."""

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key_rows: list[dict] = []
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("apiStatusPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        # ---- Title ----------------------------------------------------------
        self._title_label = QLabel("API Key Status")
        self._title_label.setObjectName("titleLabel")
        main_layout.addWidget(self._title_label)

        # ---- Four key rows --------------------------------------------------
        for i in range(4):
            row = self._create_key_row(i + 1)
            self._key_rows.append(row)
            main_layout.addLayout(row["layout"])

        main_layout.addStretch()

        # ---- Total cost -----------------------------------------------------
        self._cost_label = QLabel("Total Cost: $0.00")
        self._cost_label.setObjectName("costLabel")
        main_layout.addWidget(self._cost_label)

    def _create_key_row(self, key_number: int) -> dict:
        """Create a single API key row and return its widget references.

        Returns:
            Dict with keys: layout, name_label, progress_bar, details_label.
        """
        layout = QHBoxLayout()
        layout.setSpacing(10)

        name_label = QLabel(f"Key {key_number}")
        name_label.setObjectName(f"keyNameLabel{key_number}")
        name_label.setFixedWidth(60)
        layout.addWidget(name_label)

        progress_bar = QProgressBar()
        progress_bar.setObjectName(f"keyProgress{key_number}")
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setTextVisible(True)
        progress_bar.setFormat("%p%")
        progress_bar.setMinimumHeight(18)
        layout.addWidget(progress_bar, stretch=2)

        details_label = QLabel("—")
        details_label.setObjectName(f"keyDetails{key_number}")
        details_label.setMinimumWidth(140)
        layout.addWidget(details_label, stretch=1)

        return {
            "layout": layout,
            "name_label": name_label,
            "progress_bar": progress_bar,
            "details_label": details_label,
        }

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#apiStatusPanel {{
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
            QLabel#costLabel {{
                color: {TEXT_SECONDARY};
                font-size: 13px;
                font-weight: bold;
                padding-top: 8px;
                border-top: 1px solid {BORDER};
            }}
            QLabel[keyNameLabel] {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-weight: bold;
            }}
            QProgressBar {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 4px;
                text-align: center;
                font-size: 11px;
            }}
            QProgressBar::chunk {{
                border-radius: 4px;
            }}
        """)

    # ── Colour helpers ───────────────────────────────────────────────────────
    def _get_color_for_ratio(self, ratio: float) -> str:
        """Return a colour string based on the capacity ratio.

        Args:
            ratio: A float between 0.0 and 1.0 representing capacity used.

        Returns:
            Hex colour: green if ratio > 0.5, yellow if > 0.2, red otherwise.
        """
        if ratio > 0.5:
            return SUCCESS
        elif ratio > 0.2:
            return WARNING
        else:
            return ERROR

    def _set_progress_bar_color(self, progress_bar: QProgressBar, color: str) -> None:
        """Apply a dynamic colour to a progress bar's chunk via inline stylesheet.

        Args:
            progress_bar: The QProgressBar to style.
            color: Hex colour string for the chunk.
        """
        progress_bar.setStyleSheet(f"""
            QProgressBar::chunk {{
                background-color: {color};
                border-radius: 4px;
            }}
        """)

    # ── Public API ───────────────────────────────────────────────────────────
    def update_key_status(self, key_statuses: list[dict]) -> None:
        """Update each key's display from the provided status list.

        Args:
            key_statuses: List of dicts, one per key, each containing:
                - key_id: str
                - capacity_ratio: float (0.0 – 1.0)
                - circuit_state: str (e.g. 'CLOSED', 'OPEN', 'HALF_OPEN')
                - backoff_remaining: int (seconds)
                - requests_in_flight: int
                - total_tokens: int
                - total_cost: float
        """
        for idx, status in enumerate(key_statuses):
            if idx >= len(self._key_rows):
                break

            row = self._key_rows[idx]
            ratio = float(status.get("capacity_ratio", 0.0))
            pct = int(ratio * 100)

            # Update progress bar
            row["progress_bar"].setValue(pct)
            color = self._get_color_for_ratio(ratio)
            self._set_progress_bar_color(row["progress_bar"], color)

            # Build details string
            circuit = status.get("circuit_state", "—")
            backoff = int(status.get("backoff_remaining", 0))
            in_flight = int(status.get("requests_in_flight", 0))
            tokens = int(status.get("total_tokens", 0))
            cost = float(status.get("total_cost", 0.0))

            backoff_str = f"{backoff}s" if backoff > 0 else "0s"
            details = f"{pct}% | {circuit} | {in_flight}× | ${cost:.2f}"
            row["details_label"].setText(details)

            # Update key name with key_id if available
            key_id = status.get("key_id", f"Key {idx + 1}")
            row["name_label"].setText(key_id)

    def update_total_cost(self, cost: float) -> None:
        """Update the total cost label.

        Args:
            cost: Total cost as a float (e.g. 12.34).
        """
        self._cost_label.setText(f"Total Cost: ${cost:.2f}")
