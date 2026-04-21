"""WebSocket and MCP Stream Monitor.

Maintains connection registry, parses MCP (JSON-RPC 2.0) frames,
and emits real-time signals to the GUI.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from src.constants import Direction
from src.models.traffic import WsFrame, WsConnection, McpMessage
from src.models.state import get_state, SK

log = logging.getLogger(__name__)

MAX_CONNECTIONS = 500
MAX_FRAMES_PER_CONNECTION = 2000
MAX_MCP_MESSAGES = 5000


class StreamMonitor(QObject):
    """Receives WebSocket events from ProxyEngine and organizes them."""

    connection_added   = pyqtSignal(object)      # WsConnection
    connection_closed  = pyqtSignal(str)         # conn_id
    frame_received     = pyqtSignal(str, object) # conn_id, WsFrame
    mcp_message        = pyqtSignal(object)      # McpMessage

    def __init__(self):
        super().__init__()
        self._connections: Dict[str, WsConnection] = {}
        self._mcp_messages: List[McpMessage]       = []

    # ── Connection events (wired from ProxyEngine signals) ─────────

    def on_ws_connect(self, conn: WsConnection):
        if len(self._connections) >= MAX_CONNECTIONS:
            # Remove oldest closed connection
            closed = [c for c in self._connections.values() if not c.is_active]
            if closed:
                oldest = min(closed, key=lambda c: c.opened_at)
                del self._connections[oldest.id]
        self._connections[conn.id] = conn
        get_state().set(SK.WS_CONNECTIONS, list(self._connections.values()))
        self.connection_added.emit(conn)
        log.info("WS connected: %s → %s:%d%s", conn.client_host,
                 conn.target_host, conn.target_port, conn.path)

    def on_ws_close(self, conn_id: str):
        conn = self._connections.get(conn_id)
        if conn:
            conn.closed_at = datetime.now()
            get_state().set(SK.WS_CONNECTIONS, list(self._connections.values()))
        self.connection_closed.emit(conn_id)

    def on_ws_frame(self, conn_id: str, frame: WsFrame):
        conn = self._connections.get(conn_id)
        if conn:
            if len(conn.frames) >= MAX_FRAMES_PER_CONNECTION:
                conn.frames.pop(0)
            conn.frames.append(frame)
            conn.last_seen = datetime.now()

        get_state().set(SK.WS_FRAMES_NEW, frame)
        self.frame_received.emit(conn_id, frame)

        # Parse MCP if detected
        if frame.is_mcp:
            conn.is_mcp = True   # Mark connection as MCP
            mcp = self._parse_mcp(frame, conn_id)
            if mcp:
                self._store_mcp(mcp)

    # ── MCP parsing ────────────────────────────────────────────────

    def _parse_mcp(self, frame: WsFrame, conn_id: str) -> Optional[McpMessage]:
        try:
            data = json.loads(frame.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if "jsonrpc" not in data:
            return None

        direction = frame.direction
        mcp = McpMessage(
            connection_id = conn_id,
            timestamp     = frame.timestamp,
            direction     = direction,
            jsonrpc       = data.get("jsonrpc", "2.0"),
            method        = data.get("method"),
            msg_id        = data.get("id"),
            params        = data.get("params"),
            result        = data.get("result"),
            error         = data.get("error"),
            raw           = frame.payload_text,
        )
        return mcp

    def _store_mcp(self, mcp: McpMessage):
        if len(self._mcp_messages) >= MAX_MCP_MESSAGES:
            self._mcp_messages.pop(0)
        self._mcp_messages.append(mcp)
        get_state().set(SK.MCP_MESSAGES, list(self._mcp_messages))
        get_state().set(SK.MCP_NEW, mcp)
        self.mcp_message.emit(mcp)
        log.debug("MCP %s: %s", "→" if mcp.is_request else "←", mcp.summary)

    # ── Queries ────────────────────────────────────────────────────

    @property
    def active_connections(self) -> List[WsConnection]:
        return [c for c in self._connections.values() if c.is_active]

    @property
    def all_connections(self) -> List[WsConnection]:
        return list(self._connections.values())

    def get_connection(self, conn_id: str) -> Optional[WsConnection]:
        return self._connections.get(conn_id)

    @property
    def mcp_messages(self) -> List[McpMessage]:
        return list(self._mcp_messages)

    def clear(self):
        self._connections.clear()
        self._mcp_messages.clear()
        get_state().set(SK.WS_CONNECTIONS, [])
        get_state().set(SK.MCP_MESSAGES, [])
