"""
Task Input Panel — Ultra Orchestrator GUI

Task entry panel with title input, description textarea, template selector,
and START button. Emits a signal when the user wants to begin task decomposition.
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTextEdit,
    QComboBox,
    QSpinBox,
    QPushButton,
    QSizePolicy,
)
from PyQt6.QtGui import QFont


# ---------------------------------------------------------------------------
# Dark theme colours (shared across all panels)
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


class TaskInputPanel(QWidget):
    """Panel for entering a new task title, description, template and constraints."""

    # ── Signals ──────────────────────────────────────────────────────────────
    start_task = pyqtSignal(str, str, str, int)
    """Emitted when the user clicks START.

    Parameters:
        title        – task title (non-empty)
        description  – task description (non-empty)
        template     – selected template name
        max_subtasks – maximum number of subtasks (1-300)
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("taskInputPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ---- Title row ------------------------------------------------------
        self._title_label = QLabel("Task Title")
        self._title_label.setObjectName("titleLabel")
        main_layout.addWidget(self._title_label)

        self._title_input = QLineEdit()
        self._title_input.setObjectName("titleInput")
        self._title_input.setMaxLength(100)
        self._title_input.setPlaceholderText("Enter a concise task title (max 100 chars)")
        main_layout.addWidget(self._title_input)

        # ---- Description row ------------------------------------------------
        self._desc_label = QLabel("Description")
        self._desc_label.setObjectName("descLabel")
        main_layout.addWidget(self._desc_label)

        self._desc_input = QTextEdit()
        self._desc_input.setObjectName("descInput")
        self._desc_input.setAcceptRichText(False)
        self._desc_input.setMinimumHeight(120)  # approx 5 lines
        self._desc_input.setPlaceholderText("Describe what the orchestrator should do...")
        main_layout.addWidget(self._desc_input)

        # ---- Template + Max Subtasks row ------------------------------------
        controls_layout = QHBoxLayout()
        controls_layout.setSpacing(12)

        # Template
        template_vbox = QVBoxLayout()
        template_vbox.setSpacing(4)
        self._template_label = QLabel("Template")
        self._template_label.setObjectName("templateLabel")
        template_vbox.addWidget(self._template_label)

        self._template_combo = QComboBox()
        self._template_combo.setObjectName("templateCombo")
        self._template_combo.addItems([
            "BLANK_CODE_GENERATION",
            "BLANK_POWERSHELL",
            "BLANK_ANALYSIS",
            "BLANK_STRUCTURED_DATA",
        ])
        template_vbox.addWidget(self._template_combo)
        controls_layout.addLayout(template_vbox, stretch=2)

        # Max Subtasks
        subtasks_vbox = QVBoxLayout()
        subtasks_vbox.setSpacing(4)
        self._subtasks_label = QLabel("Max Subtasks")
        self._subtasks_label.setObjectName("subtasksLabel")
        subtasks_vbox.addWidget(self._subtasks_label)

        self._subtasks_spin = QSpinBox()
        self._subtasks_spin.setObjectName("subtasksSpin")
        self._subtasks_spin.setRange(1, 300)
        self._subtasks_spin.setValue(50)
        subtasks_vbox.addWidget(self._subtasks_spin)
        controls_layout.addLayout(subtasks_vbox, stretch=1)

        main_layout.addLayout(controls_layout)

        # ---- START button ---------------------------------------------------
        self._start_btn = QPushButton("▶  START")
        self._start_btn.setObjectName("startButton")
        self._start_btn.setMinimumHeight(48)
        self._start_btn.setCursor(
            self._start_btn.cursor().shape().ArrowCursor
        )
        self._start_btn.setCursor(
            self._start_btn.cursor().shape().PointingHandCursor
        )
        self._start_btn.clicked.connect(self._on_start_clicked)
        main_layout.addWidget(self._start_btn)

        # ---- Status label ---------------------------------------------------
        self._status_label = QLabel("")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        main_layout.addWidget(self._status_label)

        main_layout.addStretch()

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#taskInputPanel {{
                background-color: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
            }}
            QLabel#titleLabel, QLabel#descLabel, QLabel#templateLabel,
            QLabel#subtasksLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-weight: bold;
            }}
            QLineEdit#titleInput, QTextEdit#descInput {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 13px;
            }}
            QLineEdit#titleInput:focus, QTextEdit#descInput:focus {{
                border: 1px solid {ACCENT};
            }}
            QComboBox#templateCombo {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QComboBox#templateCombo QAbstractItemView {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
            }}
            QSpinBox#subtasksSpin {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QPushButton#startButton {{
                background-color: {ACCENT};
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 16px;
                font-weight: bold;
                padding: 12px;
            }}
            QPushButton#startButton:hover {{
                background-color: #0e8a8f;
            }}
            QPushButton#startButton:pressed {{
                background-color: #0a5c5f;
            }}
            QPushButton#startButton:disabled {{
                background-color: #555555;
                color: #888888;
            }}
            QLabel#statusLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-style: italic;
                min-height: 18px;
            }}
        """)

    # ── Slots ────────────────────────────────────────────────────────────────
    def _on_start_clicked(self) -> None:
        """Validate inputs and emit start_task signal if valid."""
        title = self._title_input.text().strip()
        description = self._desc_input.toPlainText().strip()
        template = self._template_combo.currentText()
        max_subtasks = self._subtasks_spin.value()

        if not title:
            self.set_status("⚠  Task title is required.")
            self._title_input.setFocus()
            return

        if not description:
            self.set_status("⚠  Task description is required.")
            self._desc_input.setFocus()
            return

        self.set_status("Decomposing task... please wait.")
        self.start_task.emit(title, description, template, max_subtasks)

    # ── Public API ───────────────────────────────────────────────────────────
    def set_status(self, message: str) -> None:
        """Update the status label text."""
        self._status_label.setText(message)

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable all input controls."""
        self._title_input.setEnabled(enabled)
        self._desc_input.setEnabled(enabled)
        self._template_combo.setEnabled(enabled)
        self._subtasks_spin.setEnabled(enabled)
        self._start_btn.setEnabled(enabled)

    def reset(self) -> None:
        """Clear all input fields and status."""
        self._title_input.clear()
        self._desc_input.clear()
        self._template_combo.setCurrentIndex(0)
        self._subtasks_spin.setValue(50)
        self.set_status("")
