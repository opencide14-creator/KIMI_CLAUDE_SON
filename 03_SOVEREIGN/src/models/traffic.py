"""Traffic data models — HTTP requests, responses, WebSocket frames, MCP messages."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid

from src.constants import Protocol, Direction, InterceptAction


@dataclass
class HttpHeaders:
    """Case-insensitive header container."""
    _data: Dict[str, str] = field(default_factory=dict)

    def set(self, key: str, value: str):
        self._data[key.lower()] = value

    def get(self, key: str, default: str = "") -> str:
        return self._data.get(key.lower(), default)

    def items(self):
        return self._data.items()

    def to_dict(self) -> Dict[str, str]:
        return dict(self._data)

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "HttpHeaders":
        h = cls()
        for k, v in d.items():
            h.set(k, v)
        return h

    def __repr__(self):
        return f"HttpHeaders({self._data})"


@dataclass
class TrafficRequest:
    """Captured HTTP/HTTPS request."""
    id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:   datetime = field(default_factory=datetime.now)
    protocol:    Protocol = Protocol.HTTPS
    method:      str = "GET"
    host:        str = ""
    port:        int = 443
    path:        str = "/"
    query:       str = ""
    headers:     HttpHeaders = field(default_factory=HttpHeaders)
    body:        bytes = b""
    # Intercept state
    action:      InterceptAction = InterceptAction.PASSTHROUGH
    intercepted: bool = False
    modified:    bool = False
    # AI detection
    is_ai_api:   bool = False
    ai_model:    str = ""
    ai_provider: str = ""

    @property
    def url(self) -> str:
        scheme = self.protocol.value.lower()
        base   = f"{scheme}://{self.host}"
        if (self.protocol == Protocol.HTTPS and self.port != 443) or \
           (self.protocol == Protocol.HTTP  and self.port != 80):
            base += f":{self.port}"
        qs = f"?{self.query}" if self.query else ""
        return f"{base}{self.path}{qs}"

    @property
    def body_text(self) -> str:
        try:
            return self.body.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(self.body)} bytes>"

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type")

    @property
    def is_json(self) -> bool:
        return "json" in self.content_type

    def summary(self) -> str:
        return f"{self.method} {self.host}{self.path}"


@dataclass
class TrafficResponse:
    """Captured HTTP/HTTPS response."""
    request_id:   str = ""
    timestamp:    datetime = field(default_factory=datetime.now)
    status_code:  int = 200
    reason:       str = "OK"
    headers:      HttpHeaders = field(default_factory=HttpHeaders)
    body:         bytes = b""
    duration_ms:  float = 0.0
    modified:     bool = False

    @property
    def body_text(self) -> str:
        try:
            return self.body.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(self.body)} bytes>"

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type")

    @property
    def is_json(self) -> bool:
        return "json" in self.content_type

    @property
    def size_str(self) -> str:
        n = len(self.body)
        if n < 1024:    return f"{n} B"
        if n < 1048576: return f"{n/1024:.1f} KB"
        return f"{n/1048576:.1f} MB"


@dataclass
class TrafficEntry:
    """Complete request+response pair as shown in the intercept table."""
    request:  TrafficRequest
    response: Optional[TrafficResponse] = None
    tags:     List[str] = field(default_factory=list)
    notes:    str = ""

    @property
    def id(self) -> str:
        return self.request.id

    @property
    def duration_ms(self) -> float:
        if self.response:
            return self.response.duration_ms
        return 0.0

    @property
    def status(self) -> Optional[int]:
        return self.response.status_code if self.response else None


@dataclass
class WsFrame:
    """A single WebSocket frame."""
    id:           str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:    datetime = field(default_factory=datetime.now)
    connection_id:str = ""
    direction:    Direction = Direction.WS_SEND
    opcode:       int = 1    # 1=text, 2=binary, 8=close, 9=ping, 10=pong
    payload:      bytes = b""
    masked:       bool = False
    is_mcp:       bool = False
    mcp_method:   str = ""

    @property
    def payload_text(self) -> str:
        try:
            return self.payload.decode("utf-8", errors="replace")
        except Exception:
            return f"<binary {len(self.payload)} bytes>"

    @property
    def opcode_name(self) -> str:
        names = {1: "TEXT", 2: "BINARY", 8: "CLOSE", 9: "PING", 10: "PONG"}
        return names.get(self.opcode, f"OP{self.opcode}")

    @property
    def size_str(self) -> str:
        n = len(self.payload)
        if n < 1024: return f"{n} B"
        return f"{n/1024:.1f} KB"


@dataclass
class WsConnection:
    """An active or historical WebSocket connection."""
    id:          str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    opened_at:   datetime = field(default_factory=datetime.now)
    closed_at:   Optional[datetime] = None
    last_seen:   Optional[datetime] = None
    client_host: str = ""
    client_port: int = 0
    target_host: str = ""
    target_port: int = 0
    path:        str = "/"
    frames:      List[WsFrame] = field(default_factory=list)
    is_mcp:      bool = False

    @property
    def is_active(self) -> bool:
        return self.closed_at is None

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def duration_str(self) -> str:
        end = self.closed_at or datetime.now()
        delta = end - self.opened_at
        s = int(delta.total_seconds())
        if s < 60:   return f"{s}s"
        if s < 3600: return f"{s//60}m {s%60}s"
        return f"{s//3600}h {(s%3600)//60}m"


@dataclass
class McpMessage:
    """A parsed MCP (Model Context Protocol) message."""
    id:           str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp:    datetime = field(default_factory=datetime.now)
    connection_id:str = ""
    direction:    Direction = Direction.REQUEST
    jsonrpc:      str = "2.0"
    method:       Optional[str] = None    # None for responses
    msg_id:       Any = None
    params:       Optional[Dict] = None
    result:       Optional[Any] = None
    error:        Optional[Dict] = None
    raw:          str = ""

    @property
    def is_request(self) -> bool:
        return self.method is not None and self.msg_id is not None

    @property
    def is_notification(self) -> bool:
        return self.method is not None and self.msg_id is None

    @property
    def is_response(self) -> bool:
        return self.method is None

    @property
    def summary(self) -> str:
        if self.method:
            return self.method
        if self.error:
            return f"Error {self.error.get('code', '?')}"
        return "Response"
