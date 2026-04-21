"""Shared GUI widgets for SOVEREIGN — neon dark theme."""
from __future__ import annotations
import json
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout,
    QPlainTextEdit, QTextEdit, QPushButton, QFrame,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QLineEdit, QSizePolicy,
)
from src.constants import COLORS, ServiceStatus


# ── Neon status pulse dot ──────────────────────────────────────────

class PulseDot(QWidget):
    """Animated pulsing dot indicating service status."""
    STATUS_COLORS = {
        ServiceStatus.RUNNING:  COLORS["neon_green"],
        ServiceStatus.STARTING: COLORS["neon_yellow"],
        ServiceStatus.STOPPED:  COLORS["text_muted"],
        ServiceStatus.ERROR:    COLORS["neon_red"],
    }

    def __init__(self, status: ServiceStatus = ServiceStatus.STOPPED, size: int = 10, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size   = size
        self._color  = self.STATUS_COLORS[status]
        self._alpha  = 255
        self._pulsing = False
        self._timer  = QTimer(self)
        self._timer.timeout.connect(self._pulse)
        self._direction = -1

    def set_status(self, status: ServiceStatus):
        # Defer timer operations to main thread to avoid "Timers cannot be started from another thread" error
        QTimer.singleShot(0, lambda: self._update_status(status))

    def _update_status(self, status: ServiceStatus):
        # All timer operations are safe here in main thread
        if status == ServiceStatus.RUNNING:
            self._pulsing = True
            if self._timer.isActive():
                self._timer.stop()
            self._timer.start(40)
        else:
            self._pulsing = False
            if self._timer.isActive():
                self._timer.stop()
            self._alpha = 255
        self.update()

    def _pulse(self):
        self._alpha += self._direction * 8
        if self._alpha <= 80:
            self._direction = 1
        if self._alpha >= 255:
            self._direction = -1
        self.update()

    def paintEvent(self, event):
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        col = QColor(self._color)
        col.setAlpha(self._alpha)
        # Glow
        glow = QColor(self._color)
        glow.setAlpha(40)
        p.setBrush(glow)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(0, 0, self._size, self._size)
        # Core dot
        p.setBrush(col)
        m = 2
        p.drawEllipse(m, m, self._size - 2*m, self._size - 2*m)
        p.end()


# ── Section headers ────────────────────────────────────────────────

class NeonLabel(QLabel):
    """Bright neon-colored section header."""
    def __init__(self, text: str, color: str = None, size: int = 10, parent=None):
        super().__init__(text, parent)
        color = color or COLORS["neon_blue"]
        self.setStyleSheet(
            f"color:{color};font-weight:bold;font-size:{size}px;"
            f"letter-spacing:1px;text-transform:uppercase;"
            f"background:transparent;"
        )


class DimLabel(QLabel):
    """Muted secondary label."""
    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;background:transparent;")


# ── Code / log viewer ──────────────────────────────────────────────

class HexView(QPlainTextEdit):
    """Monospace read-only viewer with syntax-aware coloring."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        f = QFont("JetBrains Mono", 10)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['bg_void']};
                color: {COLORS['neon_green']};
                border: 1px solid {COLORS['border']};
                border-radius: 2px;
                padding: 4px;
                selection-background-color: {COLORS['neon_blue']};
            }}
        """)

    def set_text(self, text: str):
        self.setPlainText(text)
        self.moveCursor(self.textCursor().MoveOperation.Start)

    def set_json(self, data):
        try:
            if isinstance(data, (bytes, str)):
                data = json.loads(data)
            text = json.dumps(data, indent=2, ensure_ascii=False)
        except (json.JSONDecodeError, TypeError):
            text = str(data)
        self.set_text(text)


class LogConsole(QPlainTextEdit):
    """Auto-scrolling real-time log console."""

    LEVEL_COLORS = {
        "INFO":  COLORS["text_primary"],
        "OK":    COLORS["neon_green"],
        "WARN":  COLORS["neon_yellow"],
        "ERROR": COLORS["neon_red"],
        "DEBUG": COLORS["text_muted"],
        "DATA":  COLORS["neon_cyan"],
        "ROUTE": COLORS["neon_orange"],
    }

    def __init__(self, max_lines: int = 2000, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(max_lines)
        f = QFont("JetBrains Mono", 10)
        f.setStyleHint(QFont.StyleHint.Monospace)
        self.setFont(f)
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background: {COLORS['bg_void']};
                color: {COLORS['text_primary']};
                border: none;
                padding: 4px;
            }}
        """)

    def log(self, text: str, level: str = "INFO"):
        color = self.LEVEL_COLORS.get(level.upper(), COLORS["text_primary"])
        self.appendHtml(
            f'<span style="color:{color};font-family:JetBrains Mono,Consolas,monospace;">'
            f'{text}</span>'
        )
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())


# ── Buttons ────────────────────────────────────────────────────────

def _make_btn(text: str, bg: str, fg: str = "#000", border: str = None) -> QPushButton:
    border_css = f"border:1px solid {border};" if border else "border:none;"
    b = QPushButton(text)
    b.setStyleSheet(f"""
        QPushButton {{
            background: {bg}; color: {fg};
            {border_css}
            border-radius: 2px;
            padding: 5px 14px;
            font-weight: bold;
            font-size: 11px;
            font-family: "JetBrains Mono","Consolas",monospace;
            letter-spacing: 0.5px;
        }}
        QPushButton:hover   {{ background: {bg}cc; }}
        QPushButton:pressed {{ background: {bg}88; }}
        QPushButton:disabled {{
            background: {COLORS['border']};
            color: {COLORS['text_muted']};
        }}
    """)
    return b


def neon_btn(text: str, color: str = None) -> QPushButton:
    color = color or COLORS["neon_blue"]
    return _make_btn(text, color, fg="#000")


def ghost_btn(text: str) -> QPushButton:
    b = QPushButton(text)
    b.setStyleSheet(f"""
        QPushButton {{
            background: transparent;
            color: {COLORS['text_muted']};
            border: 1px solid {COLORS['border']};
            border-radius: 2px;
            padding: 5px 14px;
            font-size: 11px;
            font-family: "JetBrains Mono","Consolas",monospace;
        }}
        QPushButton:hover   {{ color: {COLORS['text_primary']}; border-color: {COLORS['text_muted']}; }}
        QPushButton:pressed {{ background: {COLORS['bg_highlight']}; }}
        QPushButton:disabled {{ color: {COLORS['text_dim']}; border-color: {COLORS['border']}; }}
    """)
    return b


def danger_btn(text: str) -> QPushButton:
    return _make_btn(text, COLORS["neon_red"], fg="#fff")


def success_btn(text: str) -> QPushButton:
    return _make_btn(text, COLORS["neon_green"], fg="#000")


# ── Traffic table ──────────────────────────────────────────────────

class TrafficTable(QTableWidget):
    """Compact HTTP traffic log table."""
    ROW_COLS = ["#", "Method", "Host", "Path", "Status", "Size", "Time", "Latency"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.ROW_COLS), parent)
        self.setHorizontalHeaderLabels(self.ROW_COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for i in (0, 1, 4, 5, 6, 7):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setAlternatingRowColors(True)

    def add_entry(self, entry):
        from src.utils.formatters import fmt_bytes, fmt_ms, fmt_time
        from src.constants import METHOD_COLORS, status_color
        req = entry.request
        res = entry.response
        r   = self.rowCount()
        self.insertRow(r)

        def cell(text: str, color: str = None) -> QTableWidgetItem:
            item = QTableWidgetItem(str(text))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            if color:
                item.setForeground(QColor(color))
            return item

        self.setItem(r, 0, cell(f"#{r+1}", COLORS["text_muted"]))
        mc = METHOD_COLORS.get(req.method, COLORS["text_muted"])
        self.setItem(r, 1, cell(req.method, mc))
        hc = COLORS["neon_blue"] if req.is_ai_api else COLORS["text_primary"]
        self.setItem(r, 2, cell(req.host, hc))
        self.setItem(r, 3, cell(req.path))

        if res:
            sc = status_color(res.status_code)
            self.setItem(r, 4, cell(str(res.status_code), sc))
            self.setItem(r, 5, cell(res.size_str, COLORS["text_muted"]))
        else:
            self.setItem(r, 4, cell("…", COLORS["text_muted"]))
            self.setItem(r, 5, cell("…", COLORS["text_muted"]))

        self.setItem(r, 6, cell(fmt_time(req.timestamp), COLORS["text_muted"]))
        self.setItem(r, 7, cell(
            fmt_ms(entry.duration_ms) if entry.duration_ms else "…",
            COLORS["text_muted"]
        ))
        if self.rowCount() > 5000:
            self.removeRow(0)
        self.scrollToBottom()

    def selected_entry(self):
        row = self.currentRow()
        if row < 0:
            return None
        item = self.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None


# ── Key-value display ──────────────────────────────────────────────

class HeadersView(QTableWidget):
    """Read-only key-value display for HTTP headers."""
    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["Header", "Value"])
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)

    def load_headers(self, headers):
        self.setRowCount(0)
        items = headers.items() if hasattr(headers, "items") else headers
        for k, v in items:
            r = self.rowCount()
            self.insertRow(r)
            k_item = QTableWidgetItem(str(k))
            k_item.setForeground(QColor(COLORS["neon_blue"]))
            v_item = QTableWidgetItem(str(v))
            self.setItem(r, 0, k_item)
            self.setItem(r, 1, v_item)


# ── Service status bar ─────────────────────────────────────────────

class ServiceStatusRow(QWidget):
    """Compact row: [dot] [name] [status text] [start/stop buttons]"""

    toggle_requested = pyqtSignal(str, bool)  # service_name, start=True

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self._name   = name
        self._status = ServiceStatus.STOPPED
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(8)
        self._dot    = PulseDot()
        self._name_lbl   = QLabel(name.upper())
        self._name_lbl.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:10px;letter-spacing:1px;"
        )
        self._status_lbl = QLabel("STOPPED")
        self._status_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
        self._start_btn  = neon_btn("▶ START", COLORS["neon_green"])
        self._start_btn.setFixedWidth(80)
        self._start_btn.clicked.connect(lambda: self.toggle_requested.emit(self._name, True))
        self._stop_btn   = danger_btn("■ STOP")
        self._stop_btn.setFixedWidth(70)
        self._stop_btn.clicked.connect(lambda: self.toggle_requested.emit(self._name, False))
        self._stop_btn.setEnabled(False)
        lay.addWidget(self._dot)
        lay.addWidget(self._name_lbl)
        lay.addWidget(self._status_lbl)
        lay.addStretch()
        lay.addWidget(self._start_btn)
        lay.addWidget(self._stop_btn)

    def update_status(self, status: ServiceStatus, detail: str = ""):
        self._status = status
        self._dot.set_status(status)
        labels = {
            ServiceStatus.STOPPED:  ("STOPPED",  COLORS["text_muted"]),
            ServiceStatus.STARTING: ("STARTING…",COLORS["neon_yellow"]),
            ServiceStatus.RUNNING:  ("RUNNING",  COLORS["neon_green"]),
            ServiceStatus.ERROR:    ("ERROR",     COLORS["neon_red"]),
        }
        text, color = labels.get(status, ("?", COLORS["text_muted"]))
        if detail:
            text += f"  {detail}"
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color:{color};font-size:10px;")
        self._start_btn.setEnabled(status == ServiceStatus.STOPPED)
        self._stop_btn.setEnabled(status == ServiceStatus.RUNNING)
