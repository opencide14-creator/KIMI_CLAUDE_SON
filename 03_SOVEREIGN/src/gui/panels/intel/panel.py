"""Intel Panel — traffic analytics, latency stats, error tracking, HAR export."""
from __future__ import annotations
import json
import logging
from collections import defaultdict, deque
from datetime import datetime
from typing import List

# Bounded memory: Intel panel keeps last 1000 entries for analytics
MAX_INTEL_ENTRIES = 1000

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QComboBox,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, METHOD_COLORS, status_color
from src.models.traffic import TrafficEntry
from src.models.state import get_state, SK
from src.utils.formatters import fmt_bytes, fmt_ms
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole,
    neon_btn, ghost_btn,
)

log = logging.getLogger(__name__)


class StatCard(QGroupBox):
    """A single statistic card: label + big number."""
    def __init__(self, title: str, color: str = None, parent=None):
        super().__init__(title, parent)
        color = color or COLORS["neon_blue"]
        self.setStyleSheet(
            f"QGroupBox {{color:{COLORS['text_muted']};border:1px solid {COLORS['border']};"
            f"border-radius:3px;margin-top:12px;padding-top:6px;"
            f"font-size:10px;letter-spacing:1px;}}"
            f"QGroupBox::title {{subcontrol-origin:margin;left:8px;color:{COLORS['text_muted']};"
            f"font-size:9px;letter-spacing:1px;text-transform:uppercase;}}"
        )
        lay = QVBoxLayout(self)
        self._val_lbl = QLabel("0")
        self._val_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._val_lbl.setStyleSheet(
            f"color:{color};font-size:28px;font-weight:bold;"
            f"font-family:'JetBrains Mono','Consolas',monospace;"
        )
        self._sub_lbl = QLabel("")
        self._sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._sub_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
        lay.addWidget(self._val_lbl)
        lay.addWidget(self._sub_lbl)

    def update(self, value: str, sub: str = ""):
        self._val_lbl.setText(value)
        self._sub_lbl.setText(sub)


class MethodBarChart(QWidget):
    """Horizontal bar chart showing request count per HTTP method."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: dict = {}
        self.setMinimumHeight(120)

    def update_data(self, data: dict):
        self._data = data
        self.update()

    def paintEvent(self, event):
        from PyQt6.QtGui import QPainter, QFont
        if not self._data:
            return
        p    = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w    = self.width()
        h    = self.height()
        total= max(sum(self._data.values()), 1)
        bar_h= max(16, (h - 8) // max(len(self._data), 1) - 4)
        y    = 4
        font = QFont("JetBrains Mono", 9)
        p.setFont(font)
        for method, count in sorted(self._data.items(), key=lambda x: -x[1]):
            color = QColor(METHOD_COLORS.get(method, COLORS["text_muted"]))
            bar_w = int((count / total) * (w - 90))
            # Bar
            p.fillRect(80, y, bar_w, bar_h - 2, color)
            # Label
            p.setPen(QColor(COLORS["text_muted"]))
            p.drawText(2, y, 76, bar_h, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, method)
            # Count
            p.setPen(QColor(COLORS["text_primary"]))
            p.drawText(86 + bar_w, y, 60, bar_h, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, str(count))
            y += bar_h + 4
        p.end()


class HostTable(QTableWidget):
    """Top hosts by request count."""
    COLS = ["Host", "Requests", "Errors", "Avg Latency", "Total Size"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in (1, 2, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

    def load_stats(self, host_stats: dict):
        self.setRowCount(0)
        for host, stats in sorted(host_stats.items(), key=lambda x: -x[1]["count"]):
            r = self.rowCount()
            self.insertRow(r)
            h_item = QTableWidgetItem(host)
            h_item.setForeground(QColor(COLORS["neon_blue"]))
            self.setItem(r, 0, h_item)
            self.setItem(r, 1, QTableWidgetItem(str(stats["count"])))
            err_item = QTableWidgetItem(str(stats.get("errors", 0)))
            if stats.get("errors", 0) > 0:
                err_item.setForeground(QColor(COLORS["neon_red"]))
            self.setItem(r, 2, err_item)
            avg_lat = stats.get("total_ms", 0) / max(stats["count"], 1)
            self.setItem(r, 3, QTableWidgetItem(fmt_ms(avg_lat)))
            self.setItem(r, 4, QTableWidgetItem(fmt_bytes(stats.get("total_bytes", 0))))


class IntelPanel(QWidget):
    """Traffic analytics: stats, method breakdown, host table, HAR export."""

    def __init__(self, parent=None):
        super().__init__(parent)
        # Use deque for bounded memory (only keep last 1000 for analytics)
        self._entries = deque(maxlen=MAX_INTEL_ENTRIES)
        self._build()

        # S-17: Load ALL historical traffic that arrived before this panel was opened.
        # The deque maxlen=MAX_INTEL_ENTRIES will automatically discard oldest.
        existing = get_state().get(SK.TRAFFIC_ENTRIES, [])
        for entry in existing:
            self._entries.append(entry)
        if self._entries:
            self._recompute()

        # Subscribe to new traffic going forward
        get_state().subscribe(SK.TRAFFIC_NEW,    self._on_new_entry)
        get_state().subscribe(SK.TRAFFIC_UPDATE, self._on_entry_updated)

        # Refresh every 3s
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._recompute)
        self._refresh_timer.start(3000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(NeonLabel("📊  INTEL", COLORS["neon_blue"]))
        hl.addStretch()
        export_btn = ghost_btn("💾 Export HAR")
        export_btn.clicked.connect(self._export_har)
        clear_btn  = ghost_btn("✕ Clear Stats")
        clear_btn.clicked.connect(self._clear)
        hl.addWidget(export_btn)
        hl.addWidget(clear_btn)
        lay.addWidget(hdr)

        # Stat cards row
        cards = QWidget()
        cards.setFixedHeight(100)
        cl = QHBoxLayout(cards)
        cl.setContentsMargins(8, 6, 8, 6)
        cl.setSpacing(8)

        self._card_total    = StatCard("REQUESTS",     COLORS["neon_blue"])
        self._card_ai       = StatCard("AI CALLS",     COLORS["neon_orange"])
        self._card_ws       = StatCard("WS FRAMES",    COLORS["neon_cyan"])
        self._card_errors   = StatCard("ERRORS",       COLORS["neon_red"])
        self._card_avg_lat  = StatCard("AVG LATENCY",  COLORS["neon_green"])
        self._card_data     = StatCard("DATA XFER",    COLORS["neon_purple"])

        for card in (self._card_total, self._card_ai, self._card_ws,
                     self._card_errors, self._card_avg_lat, self._card_data):
            cl.addWidget(card)
        lay.addWidget(cards)

        # Main area
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left column: method chart + host table
        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(8, 4, 4, 4)

        ll.addWidget(NeonLabel("REQUESTS BY METHOD", COLORS["text_muted"]))
        self._method_chart = MethodBarChart()
        self._method_chart.setFixedHeight(140)
        ll.addWidget(self._method_chart)

        ll.addWidget(NeonLabel("TOP HOSTS", COLORS["text_muted"]))
        self._host_table = HostTable()
        ll.addWidget(self._host_table, 1)
        splitter.addWidget(left)

        # Right column: status code distribution + error log
        right = QWidget()
        rl    = QVBoxLayout(right)
        rl.setContentsMargins(4, 4, 8, 4)

        rl.addWidget(NeonLabel("STATUS CODES", COLORS["text_muted"]))
        self._status_table = QTableWidget(0, 3)
        self._status_table.setHorizontalHeaderLabels(["Code", "Count", "Description"])
        sh = self._status_table.horizontalHeader()
        sh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        sh.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._status_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._status_table.setShowGrid(False)
        self._status_table.verticalHeader().setVisible(False)
        self._status_table.setMaximumHeight(200)
        rl.addWidget(self._status_table)

        rl.addWidget(NeonLabel("ERROR LOG", COLORS["neon_red"]))
        self._error_log = LogConsole()
        rl.addWidget(self._error_log, 1)
        splitter.addWidget(right)

        splitter.setSizes([600, 400])
        lay.addWidget(splitter, 1)

    def _on_entry_updated(self, entry: TrafficEntry):
        """Response arrived — update existing entry in our list."""
        for i, e in enumerate(self._entries):
            if e.id == entry.id:
                self._entries[i] = entry
                return
        # Entry not in our list yet — add it
        self._entries.append(entry)

    def _on_new_entry(self, entry: TrafficEntry):
        self._entries.append(entry)
        if entry.response and entry.response.status_code >= 400:
            self._error_log.log(
                f"{entry.request.method} {entry.request.host}{entry.request.path} "
                f"→ {entry.response.status_code}  ({fmt_ms(entry.duration_ms)})",
                "ERROR"
            )

    def _recompute(self):
        if not self._entries:
            return

        total  = len(self._entries)
        ai_cnt = sum(1 for e in self._entries if e.request.is_ai_api)
        errors = sum(1 for e in self._entries if e.response and e.response.status_code >= 400)
        ws_cnt = get_state().get(SK.WS_CONNECTIONS, [])
        ws_frames = sum(c.frame_count for c in ws_cnt)

        completed = [e for e in self._entries if e.response]
        avg_lat = (sum(e.duration_ms for e in completed) / len(completed)) if completed else 0
        total_bytes = sum(len(e.response.body) for e in completed if e.response)

        self._card_total.update(f"{total:,}")
        self._card_ai.update(f"{ai_cnt:,}")
        self._card_ws.update(f"{ws_frames:,}")
        self._card_errors.update(f"{errors:,}")
        self._card_avg_lat.update(fmt_ms(avg_lat))
        self._card_data.update(fmt_bytes(total_bytes))

        # Method distribution
        method_counts: dict = defaultdict(int)
        for e in self._entries:
            method_counts[e.request.method] += 1
        self._method_chart.update_data(dict(method_counts))

        # Host stats
        host_stats: dict = defaultdict(lambda: {"count": 0, "errors": 0, "total_ms": 0, "total_bytes": 0})
        for e in self._entries:
            h = e.request.host
            host_stats[h]["count"] += 1
            if e.response:
                if e.response.status_code >= 400:
                    host_stats[h]["errors"] += 1
                host_stats[h]["total_ms"]    += e.duration_ms
                host_stats[h]["total_bytes"] += len(e.response.body)
        self._host_table.load_stats(dict(host_stats))

        # Status codes
        status_counts: dict = defaultdict(int)
        for e in self._entries:
            if e.response:
                status_counts[e.response.status_code] += 1
        self._status_table.setRowCount(0)
        STATUS_DESCRIPTIONS = {
            200:"OK", 201:"Created", 204:"No Content", 301:"Moved",
            302:"Found", 304:"Not Modified", 400:"Bad Request",
            401:"Unauthorized", 403:"Forbidden", 404:"Not Found",
            429:"Too Many Requests", 500:"Internal Server Error",
            502:"Bad Gateway", 503:"Service Unavailable",
        }
        for code, count in sorted(status_counts.items()):
            r = self._status_table.rowCount()
            self._status_table.insertRow(r)
            code_item = QTableWidgetItem(str(code))
            code_item.setForeground(QColor(status_color(code)))
            self._status_table.setItem(r, 0, code_item)
            self._status_table.setItem(r, 1, QTableWidgetItem(str(count)))
            self._status_table.setItem(r, 2, QTableWidgetItem(
                STATUS_DESCRIPTIONS.get(code, "")
            ))

        get_state().update({
            SK.INTEL_STATS:     {"total": total, "ai": ai_cnt, "errors": errors},
            SK.INTEL_AI_CALLS:  ai_cnt,
            SK.INTEL_ERRORS:    errors,
            SK.INTEL_LATENCY_AVG: avg_lat,
        })

    def _export_har(self, path: Path = None):
        """Export HAR with streaming to prevent OOM."""
        from PyQt6.QtWidgets import QFileDialog
        if path is None:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export HAR", "sovereign.har", "HAR files (*.har)"
            )
        if not path:
            return

        log.info(f"Exporting HAR to {path}")

        path = Path(path)
        entry_count = 0
        first_entry = True

        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{\n')
                f.write('  "log": {\n')
                f.write('    "version": "1.2",\n')
                f.write('    "creator": {"name": "SOVEREIGN", "version": "1.0.0"},\n')
                f.write('    "entries": [\n')

                for entry in self._entries:
                    if entry.response is None:
                        continue  # Skip pending

                    if not first_entry:
                        f.write(',\n')
                    first_entry = False

                    # Convert entry to HAR format
                    req = entry.request
                    res = entry.response
                    har_entry = {
                        "startedDateTime": req.timestamp.isoformat(),
                        "time": entry.duration_ms,
                        "request": {
                            "method": req.method,
                            "url": req.url,
                            "headers": [{"name": k, "value": v} for k, v in req.headers.items()],
                            "postData": {"text": req.body_text} if req.body else {},
                        },
                        "response": {
                            "status": res.status_code,
                            "statusText": res.reason,
                            "headers": [{"name": k, "value": v} for k, v in res.headers.items()],
                            "content": {"text": res.body_text, "size": len(res.body)},
                        },
                        "timings": {"send": 0, "wait": entry.duration_ms, "receive": 0},
                    }

                    # Write entry as compact JSON on single line
                    f.write('    ' + json.dumps(har_entry, indent=None, default=str))
                    entry_count += 1

                    # Flush periodically to avoid buffering too much
                    if entry_count % 100 == 0:
                        f.flush()

                f.write('\n  ]\n')
                f.write('  }\n')
                f.write('}\n')

            log.info(f"HAR export complete: {path} ({entry_count} entries)")
            self._error_log.log(f"Exported {entry_count} entries to {path}", "OK")
        except OSError as e:
            log.error(f"HAR export failed: {e}")
            self._error_log.log(f"Export failed: {e}", "ERROR")

    def _clear(self):
        self._entries.clear()
        self._host_table.setRowCount(0)
        self._status_table.setRowCount(0)
        self._error_log.clear()
        self._method_chart.update_data({})
        for card in (self._card_total, self._card_ai, self._card_ws,
                     self._card_errors, self._card_avg_lat, self._card_data):
            card.update("0")
