"""SOVEREIGN Proxy Engine — mitmproxy integration via Python API.

Runs mitmproxy in a background thread. Emits captured traffic as Qt signals.
No subprocess — we use mitmproxy's Python API directly.
"""
from __future__ import annotations
import asyncio
import json
import logging
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Set

from PyQt6.QtCore import QObject, pyqtSignal

from src.constants import (
    Protocol, Direction, InterceptAction, ServiceStatus,
    KNOWN_AI_HOSTS, AI_API_PATHS, DEFAULT_PROXY_PORT, CERT_DIR,
)
from src.core.cert.authority import CertificateAuthority
from src.utils.shutdown import ShutdownProtocol
from src.models.traffic import (
    TrafficRequest, TrafficResponse, TrafficEntry, HttpHeaders,
    WsFrame, WsConnection,
)
from src.models.state import get_state, SK

log = logging.getLogger(__name__)


class SovereignAddon:
    """mitmproxy addon — intercepts every request and response."""

    def __init__(self, on_request: Callable, on_response: Callable,
                 on_ws_connect: Callable, on_ws_frame: Callable,
                 on_ws_close: Callable,
                 intercept_hosts: Set[str],
                 routes: dict):
        self._on_request    = on_request
        self._on_response   = on_response
        self._on_ws_connect = on_ws_connect
        self._on_ws_frame   = on_ws_frame
        self._on_ws_close   = on_ws_close
        self.intercept_hosts = intercept_hosts
        self.routes          = routes           # host -> redirect_url
        self._flow_lock      = threading.Lock() # Protects _flow_id_map and _active_entries
        self._active_entries: dict = {}         # str(uuid4)[:8] -> TrafficEntry
        self._flow_id_map: dict = {}             # id(flow) -> str(uuid4)[:8]
        self._paused         = False
        self._blocked_hosts: Set[str] = set()

    # ── mitmproxy hooks ─────────────────────────────────────────────

    def request(self, flow):
        """Called for every HTTP request through the proxy."""
        try:
            self._handle_request(flow)
        except Exception as e:
            log.error("SovereignAddon.request error: %s", e, exc_info=True)

    def response(self, flow):
        """Called when the response is received."""
        try:
            self._handle_response(flow)
        except Exception as e:
            log.error("SovereignAddon.response error: %s", e, exc_info=True)

    def websocket_start(self, flow):
        try:
            self._handle_ws_start(flow)
        except Exception as e:
            log.error("websocket_start error: %s", e, exc_info=True)

    def websocket_message(self, flow):
        try:
            self._handle_ws_message(flow)
        except Exception as e:
            log.error("websocket_message error: %s", e, exc_info=True)

    def websocket_end(self, flow):
        try:
            conn_id = str(id(flow))
            self._on_ws_close(conn_id)
        except Exception as e:
            log.error("websocket_end error: %s", e, exc_info=True)

    # ── Internal handlers ──────────────────────────────────────────

    def _handle_request(self, flow):
        req_obj = flow.request
        host = req_obj.pretty_host
        port = req_obj.port
        path = req_obj.path

        # Detect protocol
        proto = Protocol.HTTPS if req_obj.scheme == "https" else Protocol.HTTP

        # Build headers
        headers = HttpHeaders.from_dict(dict(req_obj.headers))

        # Build TrafficRequest
        t_req = TrafficRequest(
            id        = str(uuid.uuid4())[:8],
            timestamp = datetime.now(),
            protocol  = proto,
            method    = req_obj.method,
            host      = host,
            port      = port,
            path      = path.split("?")[0],
            query     = path.split("?")[1] if "?" in path else "",
            headers   = headers,
            body      = req_obj.content or b"",
        )

        # Detect AI API
        if host in KNOWN_AI_HOSTS:
            t_req.is_ai_api   = True
            t_req.ai_provider = KNOWN_AI_HOSTS[host].value
        for ai_path in AI_API_PATHS:
            if t_req.path.startswith(ai_path):
                t_req.is_ai_api = True
                break

        # Apply routing rules
        if host in self.routes:
            redirect = self.routes[host]
            from urllib.parse import urlparse
            parsed = urlparse(redirect)
            req_obj.host = parsed.hostname or req_obj.host
            req_obj.port = parsed.port or (443 if parsed.scheme == "https" else 80)
            req_obj.scheme = parsed.scheme or req_obj.scheme
            req_obj.headers["X-Sovereign-Original-Host"] = host
            t_req.action   = InterceptAction.FORWARD
            t_req.modified = True
            log.info("Routing %s → %s", host, redirect)

        # Block check
        if host in self._blocked_hosts:
            from mitmproxy.http import Response as MitmResponse
            flow.response = MitmResponse.make(
                403, b"Blocked by SOVEREIGN", {"Content-Type": "text/plain"}
            )
            t_req.action = InterceptAction.BLOCK
            log.info("Blocked: %s", host)

        entry = TrafficEntry(request=t_req)
        with self._flow_lock:
            self._flow_id_map[id(flow)] = t_req.id
            self._active_entries[t_req.id] = entry
        self._on_request(entry)

    def _handle_response(self, flow):
        # Lock access to shared dictionaries to prevent race conditions
        with self._flow_lock:
            flow_uuid = self._flow_id_map.pop(id(flow), None)
            entry = self._active_entries.pop(flow_uuid, None)
        if not entry:
            return
        res_obj = flow.response
        duration_ms = (time.time() - flow.request.timestamp_start) * 1000

        t_res = TrafficResponse(
            request_id  = entry.id,
            timestamp   = datetime.now(),
            status_code = res_obj.status_code,
            reason      = res_obj.reason or "",
            headers     = HttpHeaders.from_dict(dict(res_obj.headers)),
            body        = res_obj.content or b"",
            duration_ms = duration_ms,
        )
        entry.response = t_res
        self._on_response(entry)

    def _handle_ws_start(self, flow):
        req_obj = flow.request
        conn = WsConnection(
            id          = str(id(flow))[:8],
            opened_at   = datetime.now(),
            client_host = flow.client_conn.peername[0] if flow.client_conn.peername else "",
            client_port = flow.client_conn.peername[1] if flow.client_conn.peername else 0,
            target_host = req_obj.pretty_host,
            target_port = req_obj.port,
            path        = req_obj.path,
        )
        self._on_ws_connect(conn)

    def _handle_ws_message(self, flow):
        msg = flow.websocket.messages[-1] if flow.websocket.messages else None
        if not msg:
            return
        frame = WsFrame(
            connection_id = str(id(flow))[:8],
            timestamp     = datetime.now(),
            direction     = Direction.WS_SEND if msg.from_client else Direction.WS_RECV,
            opcode        = msg.type if hasattr(msg, "type") else 1,
            payload       = msg.content if isinstance(msg.content, bytes) else msg.content.encode(),
        )
        # Detect MCP
        try:
            parsed = json.loads(frame.payload)
            if "jsonrpc" in parsed:
                frame.is_mcp   = True
                frame.mcp_method = parsed.get("method", "")
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass  # Not JSON or not UTF-8 — not an MCP frame, treat as raw binary
        self._on_ws_frame(str(id(flow))[:8], frame)


class ProxyEngine(QObject):
    """Manages the mitmproxy event loop in a background thread.

    Emits Qt signals for every captured request, response, and WebSocket event.
    """

    # ── Qt signals ─────────────────────────────────────────────────
    request_captured   = pyqtSignal(object)   # TrafficEntry
    response_captured  = pyqtSignal(object)   # TrafficEntry (with response)
    ws_connected       = pyqtSignal(object)   # WsConnection
    ws_frame           = pyqtSignal(str, object)  # conn_id, WsFrame
    ws_closed          = pyqtSignal(str)       # conn_id
    status_changed     = pyqtSignal(object)    # ServiceStatus
    error_occurred     = pyqtSignal(str)

    def __init__(self, port: int = DEFAULT_PROXY_PORT):
        super().__init__()
        self._port    = self._validate_port(port)
        self._thread:  Optional[threading.Thread] = None
        self._loop:    Optional[asyncio.AbstractEventLoop] = None
        self._master   = None
        self._status   = ServiceStatus.STOPPED
        self._routes:  dict = {}        # host -> redirect_url
        self._blocked: Set[str] = set()
        self._addon:   Optional[SovereignAddon] = None

    # ── Properties ────────────────────────────────────────────────

    @property
    def status(self) -> ServiceStatus:
        return self._status

    @property
    def port(self) -> int:
        return self._port

    @port.setter
    def port(self, value: int):
        if self._status == ServiceStatus.STOPPED:
            self._port = self._validate_port(value)
        # else: ignored — cannot change port while running

    @staticmethod
    def _validate_port(port: int) -> int:
        """Validate and constrain port number.

        Args:
            port: The port number to validate.

        Returns:
            The validated port number.

        Raises:
            TypeError: If port is not an integer.
            ValueError: If port is < 1024, == 0, or > 65535.
        """
        if not isinstance(port, int):
            raise TypeError(f"Port must be int, got {type(port).__name__}")

        if port < 1024:
            raise ValueError(f"Port {port} requires root privileges")

        if port == 0:
            raise ValueError("Port 0 (dynamic allocation) not supported")

        if port > 65535:
            raise ValueError(f"Port {port} out of range (max 65535)")

        return port

    # ── Control ───────────────────────────────────────────────────

    def start(self, cert_dir: Optional[str] = None):
        if self._status == ServiceStatus.RUNNING:
            return
        self._set_status(ServiceStatus.STARTING)
        self._thread = threading.Thread(target=self._run_proxy,
                                         args=(cert_dir,), daemon=True)
        self._thread.start()

    def stop(self):
        """Stop the proxy server."""
        # Temporarily suppress logging to avoid mitmproxy event-loop-closed errors
        import logging
        old_level = log.level
        log.setLevel(logging.WARNING)

        log.info("Stopping proxy engine...")

        if self._master:
            try:
                self._master.shutdown()
            except Exception as e:
                log.debug("Proxy shutdown error: %s", e, exc_info=True)
        self._master = None

        if self._addon:
            self._addon = None

        # CRITICAL: Join the proxy thread to ensure clean shutdown
        if self._thread and self._thread.is_alive():
            log.debug("Waiting for proxy thread to finish...")
            self._thread.join(timeout=5.0)  # Wait max 5 seconds
            if self._thread.is_alive():
                log.warning("Proxy thread did not stop within timeout")

        self._set_status(ServiceStatus.STOPPED)
        log.setLevel(old_level)
        log.info("Proxy engine stopped")

    async def stop_async(self):
        """Async graceful shutdown of ProxyEngine."""
        await ShutdownProtocol("ProxyEngine", timeout=10.0).shutdown(
            self._master,
            self._addon,
            self._thread,
        )
        self._master = None
        self._addon = None
        self._set_status(ServiceStatus.STOPPED)

    def set_route(self, source_host: str, target_url: str):
        """Route all traffic to source_host → target_url."""
        self._routes[source_host] = target_url
        if self._addon:
            self._addon.routes[source_host] = target_url
        log.info("Route added: %s → %s", source_host, target_url)

    def remove_route(self, source_host: str):
        self._routes.pop(source_host, None)
        if self._addon:
            self._addon.routes.pop(source_host, None)

    def block_host(self, host: str):
        self._blocked.add(host)
        if self._addon:
            self._addon._blocked_hosts.add(host)

    def unblock_host(self, host: str):
        self._blocked.discard(host)
        if self._addon:
            self._addon._blocked_hosts.discard(host)

    # ── Internal ──────────────────────────────────────────────────

    def _set_status(self, status: ServiceStatus):
        self._status = status
        self.status_changed.emit(status)
        get_state().set(SK.PROXY_STATUS, status)

    def _run_proxy(self, cert_dir: Optional[str]):
        """Run mitmproxy DumpMaster in background thread."""
        from src.gui.widgets.progress import TaskTracker
        tracker = TaskTracker.get()
        task = tracker.start(
            name="Proxy Engine",
            detail=f"Starting MITM proxy on port {self._port}…",
            indeterminate=True,
        )
        try:
            from mitmproxy.options import Options
            from mitmproxy.tools.dump import DumpMaster

            options = Options(
                listen_host="127.0.0.1",
                listen_port=self._port,
                ssl_insecure=False,
            )
            if cert_dir:
                options.update(confdir=cert_dir)
                # Create combined mitmproxy-ca.pem so mitmproxy uses SOVEREIGN's CA
                # instead of generating its own self-signed CA.
                ca = CertificateAuthority(Path(cert_dir))
                mitmproxy_ca_pem = ca.get_mitmproxy_ca_pem()
                if mitmproxy_ca_pem:
                    mitmproxy_ca_path = Path(cert_dir) / "mitmproxy-ca.pem"
                    mitmproxy_ca_path.write_bytes(mitmproxy_ca_pem)
                    log.info("Wrote mitmproxy-ca.pem to %s", cert_dir)

            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

            # Thread-safe bridge: mitmproxy runs in asyncio thread,
            # Qt signals must be emitted from Qt thread.
            # We collect events in a thread-safe list and flush via a QTimer in the Qt thread.
            # For simplicity: emit directly — pyqtSignal is thread-safe when Qt is configured
            # with queued connections (auto connection type handles cross-thread).
            self._addon = SovereignAddon(
                on_request    = lambda e: self.request_captured.emit(e),
                on_response   = lambda e: self.response_captured.emit(e),
                on_ws_connect = lambda c: self.ws_connected.emit(c),
                on_ws_frame   = lambda cid, f: self.ws_frame.emit(cid, f),
                on_ws_close   = lambda cid: self.ws_closed.emit(cid),
                intercept_hosts = set(self._routes.keys()),
                routes          = dict(self._routes),
            )
            self._addon._blocked_hosts = self._blocked

            # DumpMaster requires a running event loop — create it inside an async wrapper
            async def _run_master():
                self._master = DumpMaster(
                    options,
                    with_termlog=False,
                    with_dumper=False,
                )
                self._master.addons.add(self._addon)
                self._set_status(ServiceStatus.RUNNING)
                tracker.done(task.id, result=f"Running on port {self._port}")
                log.info("Proxy running on 127.0.0.1:%d", self._port)
                await self._master.run()

            self._loop.run_until_complete(_run_master())

        except Exception as e:
            log.error("Proxy engine error: %s", e, exc_info=True)
            self._set_status(ServiceStatus.ERROR)
            self.error_occurred.emit(str(e))
            tracker.fail(task.id, str(e))
        finally:
            if self._loop and not self._loop.is_closed():
                try:
                    self._loop.close()
                except RuntimeError:
                    pass  # event loop may be closed by another thread during teardown
