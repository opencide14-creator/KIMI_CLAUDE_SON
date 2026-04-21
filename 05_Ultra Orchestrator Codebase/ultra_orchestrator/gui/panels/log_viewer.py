"""
Log Viewer Panel — Ultra Orchestrator GUI

A panel showing event logs with filtering by event type and export to CSV/LOG.
Log entries are colour-coded by severity level for quick visual scanning.
"""

import os
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QComboBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QFileDialog,
    QSizePolicy,
)
from PyQt6.QtGui import QFont, QTextCharFormat, QColor


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

# Severity colour mapping
_SEVERITY_COLOURS = {
    "CRITICAL": ERROR,
    "ERROR": ERROR,
    "WARNING": WARNING,
    "INFO": TEXT_PRIMARY,
    "DEBUG": "#808080",
}

# Filter options
_FILTER_OPTIONS = ["ALL", "ORCH", "SCHED", "AGENT", "QG", "KEY", "SYSTEM"]


class LogViewer(QWidget):
    """Panel that displays filtered, colour-coded event logs with export."""

    # ── Signals ──────────────────────────────────────────────────────────────
    export_requested = pyqtSignal(str, str)
    """Emitted when the user requests log export.

    Parameters:
        file_path – absolute path chosen by the user.
        format    – 'csv' or 'log'.
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._entries: list[dict] = []
        self._setup_ui()
        self._apply_styles()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("logViewer")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # ---- Header row: filter + export buttons + limit --------------------
        header_layout = QHBoxLayout()
        header_layout.setSpacing(12)

        self._filter_label = QLabel("Filter")
        self._filter_label.setObjectName("filterLabel")
        header_layout.addWidget(self._filter_label)

        self._filter_combo = QComboBox()
        self._filter_combo.setObjectName("filterCombo")
        self._filter_combo.addItems(_FILTER_OPTIONS)
        self._filter_combo.currentTextChanged.connect(self._on_filter_changed)
        header_layout.addWidget(self._filter_combo)

        header_layout.addStretch()

        self._limit_label = QLabel("Limit")
        self._limit_label.setObjectName("limitLabel")
        header_layout.addWidget(self._limit_label)

        self._limit_spin = QSpinBox()
        self._limit_spin.setObjectName("limitSpin")
        self._limit_spin.setRange(50, 2000)
        self._limit_spin.setSingleStep(50)
        self._limit_spin.setValue(500)
        self._limit_spin.valueChanged.connect(self._on_filter_changed)
        header_layout.addWidget(self._limit_spin)

        self._export_csv_btn = QPushButton("Export .CSV")
        self._export_csv_btn.setObjectName("exportCsvButton")
        self._export_csv_btn.clicked.connect(self._on_export_csv)
        header_layout.addWidget(self._export_csv_btn)

        self._export_log_btn = QPushButton("Export .LOG")
        self._export_log_btn.setObjectName("exportLogButton")
        self._export_log_btn.clicked.connect(self._on_export_log)
        header_layout.addWidget(self._export_log_btn)

        main_layout.addLayout(header_layout)

        # ---- Title ----------------------------------------------------------
        self._title_label = QLabel("Event Logs")
        self._title_label.setObjectName("titleLabel")
        main_layout.addWidget(self._title_label)

        # ---- Log display ----------------------------------------------------
        self._log_area = QPlainTextEdit()
        self._log_area.setObjectName("logArea")
        self._log_area.setReadOnly(True)
        self._log_area.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._log_area.setPlaceholderText("No log entries yet...")
        mono_font = QFont("Consolas", 10)
        if not QFont(mono_font).exactMatch():
            mono_font = QFont("Courier New", 10)
        if not QFont(mono_font).exactMatch():
            mono_font = QFont("Monospace", 10)
        self._log_area.setFont(mono_font)
        main_layout.addWidget(self._log_area)

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#logViewer {{
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
            QLabel#filterLabel, QLabel#limitLabel {{
                color: {TEXT_SECONDARY};
                font-size: 12px;
                font-weight: bold;
            }}
            QComboBox#filterCombo {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                min-width: 80px;
            }}
            QComboBox#filterCombo QAbstractItemView {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
            }}
            QSpinBox#limitSpin {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
                min-width: 70px;
            }}
            QPushButton#exportCsvButton, QPushButton#exportLogButton {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 5px 12px;
                font-size: 12px;
            }}
            QPushButton#exportCsvButton:hover, QPushButton#exportLogButton:hover {{
                background-color: {BORDER};
            }}
            QPushButton#exportCsvButton:pressed, QPushButton#exportLogButton:pressed {{
                background-color: {ACCENT};
            }}
            QPlainTextEdit#logArea {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 8px;
                font-size: 12px;
            }}
        """)

    # ── Slots ────────────────────────────────────────────────────────────────
    def _on_filter_changed(self) -> None:
        """Re-render displayed entries when filter or limit changes."""
        self._refresh_display()

    def _on_export_csv(self) -> None:
        """Open a save dialog and emit export_requested for CSV format."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs as CSV",
            os.path.join(os.path.expanduser("~"), "orchestrator_logs.csv"),
            "CSV Files (*.csv);;All Files (*)",
        )
        if file_path:
            self.export_requested.emit(file_path, "csv")

    def _on_export_log(self) -> None:
        """Open a save dialog and emit export_requested for LOG format."""
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs as LOG",
            os.path.join(os.path.expanduser("~"), "orchestrator_logs.log"),
            "Log Files (*.log);;All Files (*)",
        )
        if file_path:
            self.export_requested.emit(file_path, "log")

    # ── Formatting ───────────────────────────────────────────────────────────
    def _format_log_line(self, entry: dict) -> str:
        """Format a single log entry dict as a display string.

        Format:
            YYYY-MM-DD HH:MM:SS.mmm [TYPE] Message

        Args:
            entry: Dict with keys 'timestamp', 'event_type', 'severity',
                   and 'message'.

        Returns:
            Formatted log line string.
        """
        ts = entry.get("timestamp", "")
        evt = entry.get("event_type", "UNKNOWN")
        msg = entry.get("message", "")
        return f"{ts}  [{evt}]  {msg}"

    def _refresh_display(self) -> None:
        """Re-render the log area based on current filter and limit settings."""
        self._log_area.clear()
        filter_text = self._filter_combo.currentText()
        limit = self._limit_spin.value()

        filtered = self._entries
        if filter_text != "ALL":
            filtered = [
                e for e in filtered
                if e.get("event_type", "").upper() == filter_text
            ]

        # Apply limit (show most recent)
        if len(filtered) > limit:
            filtered = filtered[-limit:]

        for entry in filtered:
            line = self._format_log_line(entry)
            severity = entry.get("severity", "INFO").upper()
            colour = _SEVERITY_COLOURS.get(severity, TEXT_PRIMARY)

            # Insert with colour formatting
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            cursor = self._log_area.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.setCharFormat(fmt)
            cursor.insertText(line + "\n")

        # Scroll to bottom
        scrollbar = self._log_area.verticalScrollBar()
        if scrollbar is not None:
            scrollbar.setValue(scrollbar.maximum())

    # ── Public API ───────────────────────────────────────────────────────────
    def add_log_entry(
        self,
        timestamp: str,
        event_type: str,
        severity: str,
        message: str,
    ) -> None:
        """Append a single log entry and refresh the display.

        Args:
            timestamp:  ISO-style timestamp string.
            event_type: One of ORCH, SCHED, AGENT, QG, KEY, SYSTEM.
            severity:   One of CRITICAL, ERROR, WARNING, INFO, DEBUG.
            message:    The log message text.
        """
        entry = {
            "timestamp": timestamp,
            "event_type": event_type,
            "severity": severity,
            "message": message,
        }
        self._entries.append(entry)
        self._refresh_display()

    def set_log_entries(self, entries: list[dict]) -> None:
        """Replace all displayed log entries.

        Args:
            entries: List of dicts each with timestamp, event_type, severity,
                     and message keys.
        """
        self._entries = list(entries)
        self._refresh_display()

    def clear_logs(self) -> None:
        """Clear all log entries and the display."""
        self._entries.clear()
        self._log_area.clear()
