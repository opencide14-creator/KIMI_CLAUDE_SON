"""Intercept Panel — live HTTP/HTTPS traffic capture, inspect, modify, replay."""
from __future__ import annotations
import json
import logging
from collections import deque

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QLineEdit, QTabWidget, QCheckBox,
    QComboBox, QPushButton, QTextEdit, QGroupBox,
    QMenu, QAbstractItemView,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, InterceptAction, METHOD_COLORS
from src.models.traffic import TrafficEntry
from src.models.state import get_state, SK
from src.utils.formatters import pretty_json, fmt_ms, fmt_bytes, highlight_json_syntax
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole, HexView, HeadersView,
    TrafficTable, neon_btn, ghost_btn, danger_btn,
)

log = logging.getLogger(__name__)

MAX_ENTRIES = 5000


class RequestEditor(QWidget):
    """Inspect and optionally modify a captured request/response."""

    replay_requested = pyqtSignal(object)  # TrafficEntry

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entry: TrafficEntry | None = None
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        # URL bar
        url_row = QHBoxLayout()
        self._method_lbl = QLabel("GET")
        self._method_lbl.setFixedWidth(60)
        self._method_lbl.setStyleSheet(
            f"color:{COLORS['neon_blue']};font-weight:bold;font-size:12px;"
        )
        self._url_lbl = QLabel("No request selected")
        self._url_lbl.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:11px;"
        )
        self._url_lbl.setWordWrap(True)
        url_row.addWidget(self._method_lbl)
        url_row.addWidget(self._url_lbl, 1)
        lay.addLayout(url_row)

        # Action bar
        action_row = QHBoxLayout()
        self._replay_btn = neon_btn("↩ Replay", COLORS["neon_cyan"])
        self._replay_btn.clicked.connect(self._replay)
        self._replay_btn.setEnabled(False)
        self._block_btn  = danger_btn("⊘ Block Host")
        self._block_btn.clicked.connect(self._block)
        self._block_btn.setEnabled(False)
        action_row.addWidget(self._replay_btn)
        action_row.addWidget(self._block_btn)
        action_row.addStretch()
        self._ai_badge = QLabel("")
        self._ai_badge.setStyleSheet(
            f"color:{COLORS['neon_orange']};font-size:10px;font-weight:bold;"
        )
        action_row.addWidget(self._ai_badge)
        lay.addLayout(action_row)

        # Tab: Request / Response / Raw
        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        # Request tab
        req_w = QWidget()
        rl = QVBoxLayout(req_w)
        rl.setContentsMargins(4, 4, 4, 4)
        rl.addWidget(NeonLabel("REQUEST HEADERS", COLORS["text_muted"]))
        self._req_headers = HeadersView()
        self._req_headers.setMaximumHeight(160)
        rl.addWidget(self._req_headers)
        rl.addWidget(NeonLabel("REQUEST BODY", COLORS["text_muted"]))
        self._req_body = HexView()
        rl.addWidget(self._req_body, 1)
        self._tabs.addTab(req_w, "↑ Request")

        # Response tab
        res_w = QWidget()
        rsl = QVBoxLayout(res_w)
        rsl.setContentsMargins(4, 4, 4, 4)
        self._res_status = QLabel("")
        self._res_status.setStyleSheet("font-size:14px;font-weight:bold;")
        rsl.addWidget(self._res_status)
        rsl.addWidget(NeonLabel("RESPONSE HEADERS", COLORS["text_muted"]))
        self._res_headers = HeadersView()
        self._res_headers.setMaximumHeight(140)
        rsl.addWidget(self._res_headers)
        rsl.addWidget(NeonLabel("RESPONSE BODY", COLORS["text_muted"]))
        self._res_body = HexView()
        rsl.addWidget(self._res_body, 1)
        self._tabs.addTab(res_w, "↓ Response")

        # Raw tab
        raw_w = QWidget()
        rawl = QVBoxLayout(raw_w)
        rawl.setContentsMargins(4, 4, 4, 4)
        self._raw_view = HexView()
        rawl.addWidget(self._raw_view)
        self._tabs.addTab(raw_w, "⊞ Raw")

        lay.addWidget(self._tabs, 1)

    def load_entry(self, entry: TrafficEntry):
        self._entry = entry
        req = entry.request

        mc = METHOD_COLORS.get(req.method, COLORS["text_muted"])
        self._method_lbl.setText(req.method)
        self._method_lbl.setStyleSheet(f"color:{mc};font-weight:bold;font-size:12px;")
        self._url_lbl.setText(req.url)
        self._replay_btn.setEnabled(True)
        self._block_btn.setEnabled(True)

        if req.is_ai_api:
            self._ai_badge.setText(f"⚡ AI API  [{req.ai_provider}]")
        else:
            self._ai_badge.setText("")

        # Request
        self._req_headers.load_headers(req.headers)
        try:
            body_text = pretty_json(req.body_text) if req.is_json else req.body_text
        except Exception:
            body_text = req.body_text
        self._req_body.set_text(body_text[:50000])

        # Response
        if entry.response:
            res = entry.response
            from src.constants import status_color
            sc  = status_color(res.status_code)
            self._res_status.setText(f"{res.status_code}  {res.reason}")
            self._res_status.setStyleSheet(f"color:{sc};font-size:14px;font-weight:bold;")
            self._res_headers.load_headers(res.headers)
            try:
                res_body = pretty_json(res.body_text) if res.is_json else res.body_text
            except Exception:
                res_body = res.body_text
            self._res_body.set_text(res_body[:50000])
        else:
            self._res_status.setText("Pending…")
            self._res_headers.setRowCount(0)
            self._res_body.set_text("")

        # Raw
        raw = f"{req.method} {req.path} HTTP/1.1\r\nHost: {req.host}\r\n"
        for k, v in req.headers.items():
            raw += f"{k}: {v}\r\n"
        raw += "\r\n" + req.body_text[:10000]
        self._raw_view.set_text(raw)

    def _replay(self):
        if self._entry:
            self.replay_requested.emit(self._entry)

    def _block(self):
        if self._entry:
            get_state().set("intercept.block_host", self._entry.request.host)


class InterceptPanel(QWidget):
    """Full intercept panel: traffic table + request inspector + filter controls."""

    def __init__(self, proxy_engine=None, parent=None):
        super().__init__(parent)
        self._proxy  = proxy_engine
        self._entries = deque(maxlen=MAX_ENTRIES)  # O(1) bounded FIFO
        self._paused = False
        self._filter_text = ""
        self._build()

        # Subscribe to new traffic via state
        get_state().subscribe(SK.TRAFFIC_NEW, self._on_new_entry)
        # Response updates existing rows (via TRAFFIC_UPDATE, not TRAFFIC_NEW)
        get_state().subscribe(SK.TRAFFIC_UPDATE, self.on_entry_updated)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # ── Toolbar ────────────────────────────────────────────────
        toolbar = QWidget()
        toolbar.setFixedHeight(44)
        toolbar.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        tb_lay = QHBoxLayout(toolbar)
        tb_lay.setContentsMargins(10, 6, 10, 6)
        tb_lay.setSpacing(8)

        tb_lay.addWidget(NeonLabel("🔴  INTERCEPT", COLORS["neon_red"]))

        # Filter
        self._filter_in = QLineEdit()
        self._filter_in.setPlaceholderText("Filter by host, path, method, status…")
        self._filter_in.setFixedWidth(300)
        self._filter_in.textChanged.connect(self._apply_filter)
        tb_lay.addWidget(self._filter_in)

        # Method filter
        self._method_filter = QComboBox()
        self._method_filter.addItem("ALL")
        for m in ["GET", "POST", "PUT", "PATCH", "DELETE", "WS"]:
            self._method_filter.addItem(m)
        self._method_filter.setFixedWidth(80)
        self._method_filter.currentTextChanged.connect(self._apply_filter)
        tb_lay.addWidget(self._method_filter)

        # AI only toggle
        self._ai_only_cb = QCheckBox("AI only")
        self._ai_only_cb.setStyleSheet(f"color:{COLORS['neon_orange']};")
        self._ai_only_cb.stateChanged.connect(self._apply_filter)
        tb_lay.addWidget(self._ai_only_cb)

        tb_lay.addStretch()

        self._pause_btn = neon_btn("⏸ Pause", COLORS["neon_yellow"])
        self._pause_btn.setFixedWidth(90)
        self._pause_btn.clicked.connect(self._toggle_pause)
        self._clear_btn = ghost_btn("✕ Clear")
        self._clear_btn.setFixedWidth(75)
        self._clear_btn.clicked.connect(self._clear)
        self._count_lbl = DimLabel("0 requests")
        tb_lay.addWidget(self._count_lbl)
        tb_lay.addWidget(self._pause_btn)
        tb_lay.addWidget(self._clear_btn)
        lay.addWidget(toolbar)

        # ── Main splitter ──────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Traffic table
        table_w = QWidget()
        tl = QVBoxLayout(table_w)
        tl.setContentsMargins(0, 0, 0, 0)
        self._table = TrafficTable()
        self._table.cellClicked.connect(self._on_row_clicked)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._context_menu)
        tl.addWidget(self._table)
        splitter.addWidget(table_w)

        # Request inspector
        self._inspector = RequestEditor()
        self._inspector.replay_requested.connect(self._replay_request)
        splitter.addWidget(self._inspector)

        splitter.setSizes([550, 600])
        lay.addWidget(splitter, 1)

    # ── Data ──────────────────────────────────────────────────────

    def _on_new_entry(self, entry: TrafficEntry):
        if self._paused:
            return
        # deque with maxlen handles O(1) bounded eviction automatically
        self._entries.append(entry)
        if self._matches_filter(entry):
            self._table.add_entry(entry)
        self._count_lbl.setText(f"{len(self._entries):,} requests")
        # Update shared state (convert deque to list for serialization)
        get_state().set(SK.TRAFFIC_ENTRIES, list(self._entries))

    def on_entry_updated(self, entry: TrafficEntry):
        """Called when a response arrives for an existing entry."""
        # Update table row if visible
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                stored = item.data(Qt.ItemDataRole.UserRole)
                if stored and stored.id == entry.id:
                    # Refresh status and size columns
                    from src.constants import status_color
                    from src.utils.formatters import fmt_ms
                    if entry.response:
                        sc = status_color(entry.response.status_code)
                        s_item = self._table.item(row, 4)
                        if s_item:
                            s_item.setText(str(entry.response.status_code))
                            s_item.setForeground(QColor(sc))
                        sz_item = self._table.item(row, 5)
                        if sz_item:
                            sz_item.setText(entry.response.size_str)
                        lat_item = self._table.item(row, 7)
                        if lat_item:
                            lat_item.setText(fmt_ms(entry.duration_ms))
                    break

    def inject_entry(self, entry: TrafficEntry):
        """Externally inject a traffic entry (e.g. from proxy engine signal)."""
        get_state().set(SK.TRAFFIC_NEW, entry)

    def _matches_filter(self, entry: TrafficEntry) -> bool:
        req = entry.request
        if self._ai_only_cb.isChecked() and not req.is_ai_api:
            return False
        m = self._method_filter.currentText()
        if m != "ALL" and req.method != m:
            return False
        if self._filter_text:
            haystack = f"{req.method} {req.host} {req.path} {entry.status or ''}".lower()
            if self._filter_text not in haystack:
                return False
        return True

    def _apply_filter(self):
        self._filter_text = self._filter_in.text().lower().strip()
        self._table.setRowCount(0)
        for entry in self._entries:
            if self._matches_filter(entry):
                self._table.add_entry(entry)

    def _on_row_clicked(self, row: int, col: int):
        entry = self._table.selected_entry()
        if entry:
            self._inspector.load_entry(entry)
            get_state().set(SK.TRAFFIC_SELECTED, entry)

    def _context_menu(self, pos):
        entry = self._table.selected_entry()
        if not entry:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            f"QMenu {{background:{COLORS['bg_card']};color:{COLORS['text_primary']};"
            f"border:1px solid {COLORS['border']};}} "
            f"QMenu::item:selected {{background:{COLORS['bg_highlight']};color:{COLORS['neon_blue']};}}"
        )
        replay_a   = menu.addAction("↩ Replay Request")
        copy_url_a = menu.addAction("📋 Copy URL")
        copy_curl_a= menu.addAction("📋 Copy as cURL")
        menu.addSeparator()
        block_a    = menu.addAction(f"⊘ Block  {entry.request.host}")
        route_a    = menu.addAction(f"→ Add Gateway Route for  {entry.request.host}")
        action = menu.exec(self._table.viewport().mapToGlobal(pos))
        if action == replay_a:
            self._replay_request(entry)
        elif action == copy_url_a:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(entry.request.url)
        elif action == copy_curl_a:
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(self._to_curl(entry))
        elif action == block_a:
            get_state().set("intercept.block_host", entry.request.host)
        elif action == route_a:
            get_state().set("intercept.add_route_host", entry.request.host)

    def _to_curl(self, entry: TrafficEntry) -> str:
        req = entry.request
        headers = " \\\n  ".join(f'-H "{k}: {v}"' for k, v in req.headers.items())
        body = f"-d '{req.body_text}'" if req.body else ""
        return f"curl -X {req.method} '{req.url}' \\\n  {headers} \\\n  {body}".strip()

    def _toggle_pause(self):
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.setText("▶ Resume")
            self._pause_btn.setStyleSheet(
                self._pause_btn.styleSheet().replace(COLORS["neon_yellow"], COLORS["neon_green"])
            )
        else:
            self._pause_btn.setText("⏸ Pause")
            self._pause_btn.setStyleSheet(
                self._pause_btn.styleSheet().replace(COLORS["neon_green"], COLORS["neon_yellow"])
            )
        get_state().set(SK.TRAFFIC_PAUSED, self._paused)

    def _clear(self):
        self._entries.clear()
        self._table.setRowCount(0)
        self._count_lbl.setText("0 requests")
        get_state().set(SK.TRAFFIC_ENTRIES, [])

    def _replay_request(self, entry: TrafficEntry):
        """Re-send a captured request via httpx."""
        import threading
        import httpx

        def _send():
            req = entry.request
            try:
                headers = dict(req.headers.items())
                headers.pop("host", None)
                with httpx.Client(verify=False, timeout=30) as client:
                    resp = client.request(
                        method=req.method,
                        url=req.url,
                        headers=headers,
                        content=req.body,
                    )
                log.info("Replayed %s → %d", req.url[:80], resp.status_code)
            except Exception as e:
                log.error("Replay error: %s", e)

        threading.Thread(target=_send, daemon=True).start()
