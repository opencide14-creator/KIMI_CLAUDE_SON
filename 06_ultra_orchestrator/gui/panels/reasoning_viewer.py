"""
Reasoning Viewer Panel — Ultra Orchestrator GUI

A panel showing orchestrator reasoning / thought process as a scrollable log.
The capture can be toggled on/off via a checkbox to reduce overhead.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
)
from PyQt6.QtGui import QFont, QTextCursor


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


class ReasoningViewer(QWidget):
    """Displays orchestrator reasoning entries in a read-only scrollable text area."""

    # ── Signals ──────────────────────────────────────────────────────────────
    reasoning_toggled = pyqtSignal(bool)
    """Emitted when the user toggles the 'Enable Reasoning Capture' checkbox.

    Parameter:
        enabled – True if capture is now enabled, False otherwise.
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("reasoningViewer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ---- Header row: title + checkbox -----------------------------------
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        self._title_label = QLabel("Orchestrator Reasoning")
        self._title_label.setObjectName("titleLabel")
        header_layout.addWidget(self._title_label)

        header_layout.addStretch()

        self._enable_checkbox = QCheckBox("Enable Reasoning Capture")
        self._enable_checkbox.setObjectName("enableCheckbox")
        self._enable_checkbox.setChecked(False)
        self._enable_checkbox.stateChanged.connect(
            lambda state: self.reasoning_toggled.emit(state == 2)  # Qt.Checked == 2
        )
        header_layout.addWidget(self._enable_checkbox)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setObjectName("clearButton")
        self._clear_btn.setFixedWidth(70)
        self._clear_btn.clicked.connect(self.clear_display)
        header_layout.addWidget(self._clear_btn)

        main_layout.addLayout(header_layout)

        # ---- Plain text display ---------------------------------------------
        self._text_area = QPlainTextEdit()
        self._text_area.setObjectName("textArea")
        self._text_area.setReadOnly(True)
        self._text_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text_area.setPlaceholderText(
            "Reasoning capture is disabled. Enable it above to see the orchestrator's thought process."
        )
        # Use a monospace font for clean alignment
        mono_font = QFont("Consolas", 10)
        if not QFont(mono_font).exactMatch():
            mono_font = QFont("Courier New", 10)
        if not QFont(mono_font).exactMatch():
            mono_font = QFont("Monospace", 10)
        self._text_area.setFont(mono_font)
        main_layout.addWidget(self._text_area)

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#reasoningViewer {{
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
            QCheckBox {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {BORDER};
                border-radius: 3px;
                background-color: {BG_MAIN};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT};
                border: 1px solid {ACCENT};
            }}
            QPushButton#clearButton {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }}
            QPushButton#clearButton:hover {{
                background-color: {BORDER};
            }}
            QPushButton#clearButton:pressed {{
                background-color: {ACCENT};
            }}
            QPlainTextEdit#textArea {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
        """)

    # ── Public API ───────────────────────────────────────────────────────────
    def add_reasoning_entry(
        self,
        timestamp: str,
        subtask_id: str,
        message: str,
    ) -> None:
        """Append a formatted reasoning entry to the text area.

        The entry is formatted as:
            [timestamp] [subtask_id] message

        Args:
            timestamp:  ISO-style timestamp string.
            subtask_id: Identifier of the subtask being reasoned about.
            message:    The reasoning text.
        """
        line = f"[{timestamp}] [{subtask_id}] {message}\n"
        self._text_area.appendPlainText(line)
        # Auto-scroll to bottom
        scrollbar = self._text_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    def clear_display(self) -> None:
        """Clear all text from the reasoning display."""
        self._text_area.clear()

    def set_capture_enabled(self, enabled: bool) -> None:
        """Set the reasoning capture checkbox state programmatically.

        Args:
            enabled: True to check the box, False to uncheck.
        """
        self._enable_checkbox.setChecked(enabled)

    def is_capture_enabled(self) -> bool:
        """Return whether reasoning capture is currently enabled.

        Returns:
            True if the checkbox is checked, False otherwise.
        """
        return self._enable_checkbox.isChecked()
