"""Streams Panel — real-time WebSocket frame monitor and MCP message inspector."""
from __future__ import annotations
import json
import logging
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QCheckBox, QLineEdit, QTreeWidget,
    QTreeWidgetItem,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, Direction
from src.models.traffic import WsFrame, WsConnection, McpMessage
from src.models.state import get_state, SK
from src.utils.formatters import fmt_bytes, fmt_time, pretty_json
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole, HexView,
    neon_btn, ghost_btn,
)

log = logging.getLogger(__name__)

MAX_FRAME_ROWS = 3000
MAX_MCP_ROWS   = 2000


class ConnectionList(QTableWidget):
    COLS = ["#", "Client", "Target", "Path", "Frames", "Duration", "MCP", "Active"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for i in (0, 1, 4, 5, 6, 7):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self._conns: list[WsConnection] = []

    def load_connections(self, conns: list[WsConnection]):
        self._conns = conns
        self.setRowCount(0)
        for conn in conns:
            r = self.rowCount()
            self.insertRow(r)
            self.setItem(r, 0, QTableWidgetItem(conn.id))
            self.setItem(r, 1, QTableWidgetItem(f"{conn.client_host}:{conn.client_port}"))
            host_item = QTableWidgetItem(conn.target_host)
            host_item.setForeground(QColor(COLORS["neon_cyan"]))
            self.setItem(r, 2, host_item)
            self.setItem(r, 3, QTableWidgetItem(conn.path))
            self.setItem(r, 4, QTableWidgetItem(str(conn.frame_count)))
            self.setItem(r, 5, QTableWidgetItem(conn.duration_str))
            mcp_item = QTableWidgetItem("✓" if conn.is_mcp else "")
            mcp_item.setForeground(QColor(COLORS["neon_orange"] if conn.is_mcp else COLORS["text_muted"]))
            self.setItem(r, 6, mcp_item)
            active_item = QTableWidgetItem("●" if conn.is_active else "○")
            active_item.setForeground(QColor(COLORS["neon_green"] if conn.is_active else COLORS["text_muted"]))
            self.setItem(r, 7, active_item)

    def selected_conn(self) -> WsConnection | None:
        row = self.currentRow()
        if 0 <= row < len(self._conns):
            return self._conns[row]
        return None


class FrameTable(QTableWidget):
    COLS = ["Dir", "Opcode", "Size", "MCP Method", "Time", "Preview"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for i in (0, 1, 2, 3, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        self._frames: list[WsFrame] = []

    def add_frame(self, frame: WsFrame):
        if self.rowCount() >= MAX_FRAME_ROWS:
            self.removeRow(0)
            if self._frames:
                self._frames.pop(0)
        self._frames.append(frame)
        r = self.rowCount()
        self.insertRow(r)

        is_send = frame.direction in (Direction.WS_SEND, Direction.REQUEST)
        dir_sym  = "↑" if is_send else "↓"
        dir_color= COLORS["neon_blue"] if is_send else COLORS["neon_green"]
        dir_item = QTableWidgetItem(dir_sym)
        dir_item.setForeground(QColor(dir_color))
        self.setItem(r, 0, dir_item)
        self.setItem(r, 1, QTableWidgetItem(frame.opcode_name))

        size_item = QTableWidgetItem(frame.size_str)
        size_item.setForeground(QColor(COLORS["text_muted"]))
        self.setItem(r, 2, size_item)

        mcp_item = QTableWidgetItem(frame.mcp_method if frame.is_mcp else "")
        if frame.is_mcp:
            mcp_item.setForeground(QColor(COLORS["neon_orange"]))
        self.setItem(r, 3, mcp_item)

        time_item = QTableWidgetItem(fmt_time(frame.timestamp))
        time_item.setForeground(QColor(COLORS["text_muted"]))
        self.setItem(r, 4, time_item)

        preview = frame.payload_text[:120].replace("\n", " ")
        self.setItem(r, 5, QTableWidgetItem(preview))
        self.scrollToBottom()

    def selected_frame(self) -> WsFrame | None:
        row = self.currentRow()
        if 0 <= row < len(self._frames):
            return self._frames[row]
        return None

    def clear_frames(self):
        self.setRowCount(0)
        self._frames.clear()


class McpTable(QTableWidget):
    COLS = ["Dir", "Type", "Method/ID", "Summary", "Time"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for i in (0, 1, 2, 4):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(20)
        self._msgs: list[McpMessage] = []

    def add_message(self, msg: McpMessage):
        if self.rowCount() >= MAX_MCP_ROWS:
            self.removeRow(0)
            if self._msgs:
                self._msgs.pop(0)
        self._msgs.append(msg)
        r = self.rowCount()
        self.insertRow(r)

        is_req = msg.is_request or msg.is_notification
        dir_sym = "→" if is_req else "←"
        dir_col = COLORS["neon_blue"] if is_req else COLORS["neon_green"]
        d_item = QTableWidgetItem(dir_sym)
        d_item.setForeground(QColor(dir_col))
        self.setItem(r, 0, d_item)

        if msg.is_request:
            type_text = "request"
            type_col  = COLORS["neon_blue"]
        elif msg.is_notification:
            type_text = "notify"
            type_col  = COLORS["neon_yellow"]
        else:
            type_text = "response"
            type_col  = COLORS["neon_green"]
        t_item = QTableWidgetItem(type_text)
        t_item.setForeground(QColor(type_col))
        self.setItem(r, 1, t_item)

        id_text = str(msg.msg_id or "")
        self.setItem(r, 2, QTableWidgetItem(msg.method or id_text))
        self.setItem(r, 3, QTableWidgetItem(msg.summary[:100]))

        time_item = QTableWidgetItem(fmt_time(msg.timestamp))
        time_item.setForeground(QColor(COLORS["text_muted"]))
        self.setItem(r, 4, time_item)
        self.scrollToBottom()

    def selected_msg(self) -> McpMessage | None:
        row = self.currentRow()
        if 0 <= row < len(self._msgs):
            return self._msgs[row]
        return None


class StreamsPanel(QWidget):
    """WebSocket frame monitor + MCP JSON-RPC inspector."""

    def __init__(self, stream_monitor=None, parent=None):
        super().__init__(parent)
        self._monitor = stream_monitor
        self._selected_conn: WsConnection | None = None
        self._build()
        self._setup_subscriptions()

        # Refresh connection list every 3s
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_connections)
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
        hl.addWidget(NeonLabel("🔵  STREAMS", COLORS["neon_cyan"]))
        hl.addStretch()
        self._conn_count_lbl = DimLabel("0 connections")
        self._frame_count_lbl= DimLabel("0 frames total")
        hl.addWidget(self._conn_count_lbl)
        hl.addWidget(self._frame_count_lbl)
        self._clear_btn = ghost_btn("✕ Clear All")
        self._clear_btn.clicked.connect(self._clear_all)
        hl.addWidget(self._clear_btn)
        lay.addWidget(hdr)

        # Main splitter: connection list | frame detail
        main_split = QSplitter(Qt.Orientation.Vertical)

        # Top: connection list
        conn_w = QWidget()
        conn_l = QVBoxLayout(conn_w)
        conn_l.setContentsMargins(4, 4, 4, 4)
        conn_l.addWidget(NeonLabel("CONNECTIONS", COLORS["neon_cyan"]))
        self._conn_list = ConnectionList()
        self._conn_list.cellClicked.connect(self._on_conn_selected)
        conn_l.addWidget(self._conn_list)
        main_split.addWidget(conn_w)

        # Bottom: frame detail + MCP inspector side by side
        bottom = QWidget()
        bl     = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)

        frame_tabs = QTabWidget()

        # Frame list tab
        frame_w = QWidget()
        fl = QVBoxLayout(frame_w)
        fl.setContentsMargins(4, 4, 4, 4)

        filter_row = QHBoxLayout()
        self._frame_filter = QLineEdit()
        self._frame_filter.setPlaceholderText("Filter frames…")
        self._frame_filter.setFixedWidth(200)
        self._mcp_only_cb = QCheckBox("MCP only")
        self._mcp_only_cb.setStyleSheet(f"color:{COLORS['neon_orange']};")
        filter_row.addWidget(NeonLabel("FRAMES", COLORS["text_muted"]))
        filter_row.addWidget(self._frame_filter)
        filter_row.addWidget(self._mcp_only_cb)
        filter_row.addStretch()
        fl.addLayout(filter_row)

        self._frame_table = FrameTable()
        self._frame_table.cellClicked.connect(self._on_frame_selected)
        fl.addWidget(self._frame_table, 1)
        frame_tabs.addTab(frame_w, "⬆⬇ Frames")

        # MCP tab
        mcp_w = QWidget()
        ml    = QVBoxLayout(mcp_w)
        ml.setContentsMargins(4, 4, 4, 4)
        ml.addWidget(NeonLabel("MCP JSON-RPC 2.0", COLORS["neon_orange"]))
        self._mcp_table = McpTable()
        self._mcp_table.cellClicked.connect(self._on_mcp_selected)
        ml.addWidget(self._mcp_table, 1)
        frame_tabs.addTab(mcp_w, "🔮 MCP")

        bl.addWidget(frame_tabs, 1)

        # Payload detail
        detail_w = QWidget()
        dl = QVBoxLayout(detail_w)
        dl.setContentsMargins(4, 4, 4, 4)
        dl.addWidget(NeonLabel("PAYLOAD", COLORS["text_muted"]))
        self._payload_view = HexView()
        self._payload_view.setMinimumWidth(300)
        dl.addWidget(self._payload_view, 1)

        # MCP tree
        dl.addWidget(NeonLabel("MCP STRUCTURE", COLORS["text_muted"]))
        self._mcp_tree = QTreeWidget()
        self._mcp_tree.setHeaderLabels(["Key", "Value"])
        self._mcp_tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._mcp_tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._mcp_tree.setMaximumHeight(180)
        dl.addWidget(self._mcp_tree)
        bl.addWidget(detail_w)

        main_split.addWidget(bottom)
        main_split.setSizes([200, 500])
        lay.addWidget(main_split, 1)

    def _setup_subscriptions(self):
        get_state().subscribe(SK.WS_CONNECTIONS, self._on_connections_update)
        get_state().subscribe(SK.WS_FRAMES_NEW, self._on_new_frame)
        get_state().subscribe(SK.MCP_NEW, self._on_new_mcp)
        if self._monitor:
            self._monitor.connection_added.connect(self._on_conn_added)
            self._monitor.frame_received.connect(self._on_frame_received)
            self._monitor.mcp_message.connect(self._on_mcp_received)
            self._monitor.connection_closed.connect(self._on_conn_closed)

    def _on_connections_update(self, conns: list):
        self._conn_list.load_connections(conns)
        active = sum(1 for c in conns if c.is_active)
        self._conn_count_lbl.setText(f"{len(conns)} connections  ({active} active)")

    def _on_conn_added(self, conn: WsConnection):
        conns = get_state().get(SK.WS_CONNECTIONS, [])
        self._conn_list.load_connections(conns)

    def _on_new_frame(self, frame: WsFrame):
        # Only add frame if it belongs to selected connection
        if self._selected_conn and frame.connection_id == self._selected_conn.id:
            if self._mcp_only_cb.isChecked() and not frame.is_mcp:
                return
            self._frame_table.add_frame(frame)
        total = sum(c.frame_count for c in get_state().get(SK.WS_CONNECTIONS, []))
        self._frame_count_lbl.setText(f"{total} frames total")

    def _on_frame_received(self, conn_id: str, frame: WsFrame):
        if self._selected_conn and conn_id == self._selected_conn.id:
            self._frame_table.add_frame(frame)

    def _on_new_mcp(self, msg: McpMessage):
        self._mcp_table.add_message(msg)

    def _on_mcp_received(self, msg: McpMessage):
        self._mcp_table.add_message(msg)

    def _on_conn_closed(self, conn_id: str):
        """Mark connection as closed in the UI."""
        conns = get_state().get(SK.WS_CONNECTIONS, [])
        self._conn_list.load_connections(conns)
        total_active = sum(1 for c in conns if c.is_active)
        self._frame_count_lbl.setText(
            f"{total_active} active / {len(conns)} total connections"
        )

    def _on_conn_selected(self, row: int, col: int):
        conn = self._conn_list.selected_conn()
        if not conn:
            return
        self._selected_conn = conn
        self._frame_table.clear_frames()
        for frame in conn.frames[-500:]:
            if self._mcp_only_cb.isChecked() and not frame.is_mcp:
                continue
            self._frame_table.add_frame(frame)
        get_state().set(SK.WS_SELECTED_CONN, conn)

    def _on_frame_selected(self, row: int, col: int):
        frame = self._frame_table.selected_frame()
        if not frame:
            return
        try:
            text = pretty_json(frame.payload)
        except Exception:
            text = frame.payload_text
        self._payload_view.set_text(text)
        if frame.is_mcp:
            self._populate_mcp_tree(frame.payload_text)

    def _on_mcp_selected(self, row: int, col: int):
        msg = self._mcp_table.selected_msg()
        if not msg:
            return
        self._payload_view.set_text(pretty_json(msg.raw) if msg.raw else "")
        self._populate_mcp_tree(msg.raw)

    def _populate_mcp_tree(self, raw: str):
        self._mcp_tree.clear()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        def _add_items(parent, d):
            if isinstance(d, dict):
                for k, v in d.items():
                    item = QTreeWidgetItem([str(k), ""])
                    item.setForeground(0, QColor(COLORS["neon_blue"]))
                    if isinstance(v, (dict, list)):
                        _add_items(item, v)
                    else:
                        item.setText(1, str(v)[:200])
                        item.setForeground(1, QColor(COLORS["neon_green"]))
                    parent.addChild(item)
            elif isinstance(d, list):
                for i, v in enumerate(d):
                    item = QTreeWidgetItem([f"[{i}]", ""])
                    if isinstance(v, (dict, list)):
                        _add_items(item, v)
                    else:
                        item.setText(1, str(v)[:200])
                    parent.addChild(item)

        for k, v in data.items():
            root_item = QTreeWidgetItem(self._mcp_tree, [str(k), ""])
            root_item.setForeground(0, QColor(COLORS["neon_blue"]))
            if isinstance(v, (dict, list)):
                _add_items(root_item, v)
            else:
                root_item.setText(1, str(v)[:200])
                root_item.setForeground(1, QColor(COLORS["neon_green"]))
        self._mcp_tree.expandAll()

    def _refresh_connections(self):
        conns = get_state().get(SK.WS_CONNECTIONS, [])
        self._conn_list.load_connections(conns)

    def _clear_all(self):
        if self._monitor:
            self._monitor.clear()
        self._conn_list.setRowCount(0)
        self._frame_table.clear_frames()
        self._mcp_table.setRowCount(0)
        self._mcp_tree.clear()
        self._payload_view.set_text("")
        self._conn_count_lbl.setText("0 connections")
        self._frame_count_lbl.setText("0 frames total")
