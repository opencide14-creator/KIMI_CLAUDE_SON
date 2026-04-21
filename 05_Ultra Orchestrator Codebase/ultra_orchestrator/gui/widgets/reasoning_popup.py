"""
ReasoningPopup dialog for the Ultra Orchestrator GUI.

A modal dialog displaying agent reasoning, output details,
and quality gate (QG) layer-by-layer pass/fail results.
"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QPlainTextEdit,
    QScrollArea,
    QWidget,
    QSizePolicy,
    QFrame,
    QSpacerItem,
)


class ReasoningPopup(QDialog):
    """
    Modal dialog showing detailed agent reasoning and output.

    Displays three sections:
        - THINKING: Scrollable read-only text area for reasoning.
        - OUTPUT: Monospace read-only text area for code/output.
        - QG RESULT: Grid of quality gate layer pass/fail indicators.
    """

    # ── Theme Colors ────────────────────────────────────────────────────
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

    def __init__(
        self,
        subtask_id: str,
        title: str,
        reasoning: str = "",
        output: str = "",
        qg_result: dict = None,
        parent=None,
    ):
        """
        Initialize the ReasoningPopup dialog.

        Args:
            subtask_id: Unique identifier for the subtask (e.g., "ST-001").
            title: Human-readable title for the subtask.
            reasoning: Agent thinking/reasoning text.
            output: Code or output produced by the agent.
            qg_result: Quality gate result dictionary.
                       Expected format:
                       {
                           "overall": True/False,
                           "layers": {
                               "0": {"name": "Existence", "passed": True},
                               "1": {"name": "Anti-Smell", "passed": False},
                               ...
                           }
                       }
            parent: Parent widget.
        """
        super().__init__(parent)

        self._subtask_id = subtask_id
        self._title = title
        self._reasoning = reasoning
        self._output = output
        self._qg_result = qg_result or {}

        self.setObjectName("reasoningPopup")
        self.setWindowTitle(f"Subtask Details - {subtask_id}")
        self.setMinimumSize(700, 550)
        self.setModal(True)

        self._setup_ui()
        self._apply_stylesheet()

    # ── Public Methods ──────────────────────────────────────────────────

    def set_data(
        self,
        reasoning: str = None,
        output: str = None,
        qg_result: dict = None,
    ):
        """
        Update displayed data dynamically.

        Args:
            reasoning: New reasoning text, or None to keep current.
            output: New output text, or None to keep current.
            qg_result: New quality gate result dict, or None to keep current.
        """
        if reasoning is not None:
            self._reasoning = reasoning
            self._thinking_edit.setPlainText(reasoning)

        if output is not None:
            self._output = output
            self._output_edit.setPlainText(output)

        if qg_result is not None:
            self._qg_result = qg_result
            self._refresh_qg_display()

    # ── UI Setup ────────────────────────────────────────────────────────

    def _setup_ui(self):
        """Create all layouts, widgets, and assemble the dialog."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ── Title Bar ──
        title_bar = self._create_title_bar()
        main_layout.addWidget(title_bar)

        # ── Scrollable Content ──
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")

        self._content_widget = QWidget()
        content_layout = QVBoxLayout(self._content_widget)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(16)

        # THINKING section
        content_layout.addWidget(self._create_thinking_section())

        # OUTPUT section
        content_layout.addWidget(self._create_output_section())

        # QG RESULT section
        content_layout.addWidget(self._create_qg_section())

        content_layout.addStretch()
        scroll.setWidget(self._content_widget)
        main_layout.addWidget(scroll)

    def _create_title_bar(self) -> QFrame:
        """Create the top title bar with subtask ID, title, and close button."""
        title_frame = QFrame()
        title_frame.setObjectName("titleBar")
        title_frame.setFixedHeight(48)
        title_layout = QHBoxLayout(title_frame)
        title_layout.setContentsMargins(16, 0, 8, 0)
        title_layout.setSpacing(10)

        # Status indicator dot
        dot = QLabel()
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(
            f"background-color: {self.ACCENT}; border-radius: 5px;"
        )
        title_layout.addWidget(dot)

        # Subtask ID label
        id_label = QLabel(self._subtask_id)
        id_label.setFont(QFont("JetBrains Mono", 12, QFont.Weight.Bold))
        id_label.setStyleSheet(f"color: {self.TEXT_PRIMARY}; background: transparent;")
        title_layout.addWidget(id_label)

        # Separator
        sep = QLabel("|")
        sep.setStyleSheet(f"color: {self.BORDER}; background: transparent;")
        title_layout.addWidget(sep)

        # Title label
        title_label = QLabel(self._title)
        title_label.setFont(QFont("Segoe UI", 11))
        title_label.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        title_label.setElideMode(Qt.TextElideMode.ElideRight)
        title_layout.addWidget(title_label, stretch=1)

        # Close button
        close_btn = QPushButton("✕")
        close_btn.setFont(QFont("Segoe UI", 12, QFont.Weight.Bold))
        close_btn.setFixedSize(32, 32)
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setStyleSheet(
            f"QPushButton {{ background-color: transparent; "
            f"color: {self.TEXT_SECONDARY}; border: none; "
            f"border-radius: 4px; font-size: 14px; }}"
            f"QPushButton:hover {{ background-color: #E81123; color: #ffffff; }}"
        )
        close_btn.clicked.connect(self.close)
        title_layout.addWidget(close_btn)

        return title_frame

    def _create_thinking_section(self) -> QFrame:
        """Create the THINKING collapsible section."""
        section = QFrame()
        section.setObjectName("thinkingSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Section header
        header = QLabel("THINKING")
        header.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.WARNING}; background: transparent;")
        layout.addWidget(header)

        # Divider line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self.BORDER};")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Text edit
        self._thinking_edit = QPlainTextEdit()
        self._thinking_edit.setPlainText(self._reasoning)
        self._thinking_edit.setReadOnly(True)
        self._thinking_edit.setFont(QFont("JetBrains Mono", 10))
        self._thinking_edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self._thinking_edit.setMinimumHeight(120)
        self._thinking_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {self.BG_MAIN}; "
            f"color: {self.TEXT_PRIMARY}; border: 1px solid {self.BORDER}; "
            f"border-radius: 4px; padding: 8px; }}"
        )
        layout.addWidget(self._thinking_edit)

        return section

    def _create_output_section(self) -> QFrame:
        """Create the OUTPUT section with monospace font."""
        section = QFrame()
        section.setObjectName("outputSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Section header
        header = QLabel("OUTPUT")
        header.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.ACCENT}; background: transparent;")
        layout.addWidget(header)

        # Divider line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self.BORDER};")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # Monospace output editor
        self._output_edit = QPlainTextEdit()
        self._output_edit.setPlainText(self._output)
        self._output_edit.setReadOnly(True)
        self._output_edit.setFont(QFont("JetBrains Mono", 10))
        self._output_edit.setLineWrapMode(
            QPlainTextEdit.LineWrapMode.WidgetWidth
        )
        self._output_edit.setMinimumHeight(150)
        self._output_edit.setStyleSheet(
            f"QPlainTextEdit {{ background-color: {self.BG_MAIN}; "
            f"color: {self.TEXT_PRIMARY}; border: 1px solid {self.BORDER}; "
            f"border-radius: 4px; padding: 8px; }}"
        )
        layout.addWidget(self._output_edit)

        return section

    def _create_qg_section(self) -> QFrame:
        """Create the Quality Gate result section."""
        section = QFrame()
        section.setObjectName("qgSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        # Section header
        header = QLabel("QUALITY GATE RESULT")
        header.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
        header.setStyleSheet(f"color: {self.SUCCESS}; background: transparent;")
        layout.addWidget(header)

        # Divider line
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self.BORDER};")
        divider.setFixedHeight(1)
        layout.addWidget(divider)

        # QG result grid container
        self._qg_container = QWidget()
        self._qg_container_layout = QVBoxLayout(self._qg_container)
        self._qg_container_layout.setContentsMargins(0, 4, 0, 4)
        self._qg_container_layout.setSpacing(4)

        # Populate initial QG display
        qg_widget = self._create_qg_display(self._qg_result)
        self._qg_container_layout.addWidget(qg_widget)

        layout.addWidget(self._qg_container)

        return section

    def _create_qg_display(self, qg_result: dict) -> QWidget:
        """
        Create a quality gate result display widget.

        Args:
            qg_result: Dict with optional "overall" bool and "layers" dict.
                       Layers format: {"0": {"name": "...", "passed": True}, ...}

        Returns:
            QWidget containing the QG result grid.
        """
        widget = QWidget()
        grid = QGridLayout(widget)
        grid.setContentsMargins(4, 4, 4, 4)
        grid.setSpacing(8)
        grid.setColumnStretch(1, 1)

        row = 0

        # ── Header row ──
        header_layer = QLabel("Layer")
        header_layer.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Bold))
        header_layer.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        grid.addWidget(header_layer, row, 0)

        header_check = QLabel("Result")
        header_check.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Bold))
        header_check.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        grid.addWidget(header_check, row, 1)

        header_status = QLabel("Status")
        header_status.setFont(QFont("JetBrains Mono", 9, QFont.Weight.Bold))
        header_status.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent;")
        header_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(header_status, row, 2)
        row += 1

        # Divider
        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet(f"color: {self.BORDER};")
        grid.addWidget(divider, row, 0, 1, 3)
        row += 1

        # ── Layer rows ──
        layers = qg_result.get("layers", {})
        if not layers:
            no_data = QLabel("No quality gate data available.")
            no_data.setFont(QFont("JetBrains Mono", 10))
            no_data.setStyleSheet(f"color: {self.TEXT_SECONDARY}; background: transparent; padding: 8px;")
            grid.addWidget(no_data, row, 0, 1, 3)
            row += 1
        else:
            for layer_key in sorted(layers.keys(), key=lambda k: int(k) if k.isdigit() else k):
                layer_info = layers[layer_key]
                layer_name = layer_info.get("name", f"Layer {layer_key}")
                passed = layer_info.get("passed", False)

                # Layer number/name label
                layer_label = QLabel(f"Layer {layer_key}")
                layer_label.setFont(QFont("JetBrains Mono", 10))
                layer_label.setStyleSheet(
                    f"color: {self.TEXT_PRIMARY}; background: transparent;"
                )
                grid.addWidget(layer_label, row, 0)

                # Layer test name
                name_label = QLabel(layer_name)
                name_label.setFont(QFont("JetBrains Mono", 10))
                name_label.setStyleSheet(
                    f"color: {self.TEXT_PRIMARY}; background: transparent;"
                )
                grid.addWidget(name_label, row, 1)

                # Pass/fail indicator
                if passed:
                    status_label = QLabel("✓  PASS")
                    status_label.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
                    status_label.setStyleSheet(
                        f"color: {self.SUCCESS}; background: transparent;"
                    )
                else:
                    status_label = QLabel("✗  FAIL")
                    status_label.setFont(QFont("JetBrains Mono", 10, QFont.Weight.Bold))
                    status_label.setStyleSheet(
                        f"color: {self.ERROR}; background: transparent;"
                    )
                status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(status_label, row, 2)
                row += 1

        # ── Overall result row ──
        overall_passed = qg_result.get("overall", False)
        if layers:
            # Divider before overall
            divider2 = QFrame()
            divider2.setFrameShape(QFrame.Shape.HLine)
            divider2.setStyleSheet(f"color: {self.BORDER};")
            grid.addWidget(divider2, row, 0, 1, 3)
            row += 1

            overall_label = QLabel("OVERALL")
            overall_label.setFont(QFont("JetBrains Mono", 11, QFont.Weight.Bold))
            overall_label.setStyleSheet(f"color: {self.TEXT_PRIMARY}; background: transparent;")
            grid.addWidget(overall_label, row, 0)

            spacer = QLabel("")
            grid.addWidget(spacer, row, 1)

            if overall_passed:
                overall_status = QLabel("✓  PASSED")
                overall_status.setFont(QFont("JetBrains Mono", 11, QFont.Weight.Bold))
                overall_status.setStyleSheet(
                    f"color: {self.SUCCESS}; background: transparent;"
                )
            else:
                overall_status = QLabel("✗  FAILED")
                overall_status.setFont(QFont("JetBrains Mono", 11, QFont.Weight.Bold))
                overall_status.setStyleSheet(
                    f"color: {self.ERROR}; background: transparent;"
                )
            overall_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(overall_status, row, 2)
            row += 1

        return widget

    def _refresh_qg_display(self):
        """Refresh the QG result display with current data."""
        # Remove old QG widget
        while self._qg_container_layout.count():
            child = self._qg_container_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Add new QG widget
        new_qg_widget = self._create_qg_display(self._qg_result)
        self._qg_container_layout.addWidget(new_qg_widget)

    def _apply_stylesheet(self):
        """Apply the dialog-level dark theme stylesheet."""
        self.setStyleSheet(f"""
            QDialog#reasoningPopup {{
                background-color: {self.BG_MAIN};
                border: 1px solid {self.BORDER};
                border-radius: 8px;
            }}
            QFrame#titleBar {{
                background-color: {self.BG_PANEL};
                border-bottom: 1px solid {self.BORDER};
            }}
            QFrame#thinkingSection,
            QFrame#outputSection,
            QFrame#qgSection {{
                background-color: {self.BG_CARD};
                border: 1px solid {self.BORDER};
                border-radius: 6px;
            }}
            QScrollArea > QWidget > QWidget {{
                background-color: transparent;
            }}
        """)

    # ── Event Handlers ──────────────────────────────────────────────────

    def keyPressEvent(self, event):
        """Close dialog on Escape key press."""
        if event.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)
