"""Service Discovery — real port scanner + FastAPI/MCP/WS fingerprinting.

Uses asyncio socket connections (no nmap dependency).
Fingerprints open ports by probing HTTP endpoints.
"""
from __future__ import annotations
import asyncio
import json
import logging
import socket
import time
from datetime import datetime
from typing import List, Optional

import httpx
from PyQt6.QtCore import QObject, QThread, pyqtSignal

from src.constants import Protocol, HostStatus, DEFAULT_PROXY_PORT, DEFAULT_GATEWAY_PORT
from src.models.gateway import DiscoveredService
from src.models.state import get_state, SK

log = logging.getLogger(__name__)

# Common ports to always include in scan
PRIORITY_PORTS = [
    80, 443, 3000, 4000, 4001, 5000, 5173, 7860, 8000, 8080,
    8088, 8443, 8888, 9000, 9090, 11434, DEFAULT_PROXY_PORT,
    DEFAULT_GATEWAY_PORT,
]


class ScanWorker(QThread):
    """Background port scanner + service fingerprinter."""

    service_found     = pyqtSignal(object)   # DiscoveredService
    scan_progress     = pyqtSignal(int)       # 0-100
    scan_finished     = pyqtSignal(list)      # List[DiscoveredService]

    def __init__(self, host: str = "127.0.0.1",
                 port_range: tuple = (1, 9999),
                 extra_ports: List[int] = None,
                 timeout_s: float = 0.3):
        super().__init__()
        self._host       = host
        self._range      = port_range
        self._extra      = extra_ports or []
        self._timeout    = timeout_s
        self._stop_flag  = False

    def stop(self):
        self._stop_flag = True

    def run(self):
        """Scan ports in a new event loop and emit results."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            services = loop.run_until_complete(self._scan_all())
        finally:
            loop.close()
        self.scan_finished.emit(services)

    async def _scan_all(self) -> List[DiscoveredService]:
        all_ports = list(range(self._range[0], self._range[1] + 1)) + \
                    [p for p in PRIORITY_PORTS if p not in range(self._range[0], self._range[1] + 1)] + \
                    self._extra
        all_ports = sorted(set(all_ports))
        total      = len(all_ports)
        open_ports = []
        done_count = 0
        # S-10: Semaphore caps concurrent TCP sockets — safe on Windows (FD limit)
        sem = asyncio.Semaphore(150)

        async def _check_with_sem(port: int) -> tuple:
            async with sem:
                return port, await self._check_port(port)

        tasks = [_check_with_sem(p) for p in all_ports]
        for coro in asyncio.as_completed(tasks):
            if self._stop_flag:
                break
            port, is_open = await coro
            if is_open:
                open_ports.append(port)
            done_count += 1
            if done_count % 500 == 0:
                self.scan_progress.emit(int(done_count / total * 80))

        self.scan_progress.emit(80)

        # Fingerprint open ports
        services = []
        async with httpx.AsyncClient(timeout=3.0) as client:
            tasks = [self._fingerprint(port, client) for port in open_ports]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for svc in results:
            if isinstance(svc, DiscoveredService):
                services.append(svc)
                self.service_found.emit(svc)

        self.scan_progress.emit(100)
        return services

    async def _check_port(self, port: int) -> bool:
        """Return True if port is open (TCP connect succeeds)."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, port),
                timeout=self._timeout
            )
            writer.close()
            try:
                await writer.wait_closed()
            except (OSError, ConnectionResetError):
                pass  # Some services reset immediately after accept
            return True
        except (OSError, asyncio.TimeoutError, ConnectionRefusedError):
            return False

    async def _fingerprint(self, port: int, client: httpx.AsyncClient) -> Optional[DiscoveredService]:
        """Probe an open port to determine what service is running."""
        svc = DiscoveredService(
            host       = self._host,
            port       = port,
            status     = HostStatus.OPEN,
            discovered_at = datetime.now(),
            last_seen  = datetime.now(),
        )
        # Determine protocol guess
        https_ports = {443, 8443, 9443}
        svc.protocol = Protocol.HTTPS if port in https_ports else Protocol.HTTP
        scheme = "https" if svc.protocol == Protocol.HTTPS else "http"
        base   = f"{scheme}://{self._host}:{port}"

        # Try PID / process name via psutil
        try:
            import psutil
            for conn in psutil.net_connections(kind="inet"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    svc.pid = conn.pid
                    if conn.pid:
                        try:
                            proc = psutil.Process(conn.pid)
                            svc.process = proc.name()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            svc.process = ""  # Process ended or no permission — skip name
                    break
        except (ImportError, Exception) as e:
            log.debug("psutil probe failed for port %d: %s", port, e)

        # HTTP probe — try common paths with short timeout per probe
        probe_paths = ["/", "/health", "/docs", "/openapi.json",
                       "/v1/models", "/v1/health", "/mcp"]
        for path in probe_paths:
            try:
                t0   = time.monotonic()
                resp = await client.get(
                    f"{base}{path}",
                    headers={"User-Agent": "SOVEREIGN/1.0"},
                    timeout=1.5,  # per-probe timeout; overall client timeout is 3.0
                )
                svc.latency_ms = (time.monotonic() - t0) * 1000

                # Detect FastAPI / OpenAPI
                if path == "/openapi.json" and resp.status_code == 200:
                    try:
                        data = resp.json()
                        svc.is_fastapi  = True
                        svc.openapi_url = f"{base}/openapi.json"
                        svc.service     = data.get("info", {}).get("title", "FastAPI")
                        svc.version     = data.get("info", {}).get("version", "")
                        svc.endpoints   = [
                            f"{method.upper()} {path}"
                            for path, methods in data.get("paths", {}).items()
                            for method in methods
                            if method.lower() not in ("head", "options")
                        ]
                        # Find WebSocket paths from schema
                        svc.ws_paths = [
                            p for p, methods in data.get("paths", {}).items()
                            if any("websocket" in str(v).lower() for v in methods.values())
                        ]
                    except (json.JSONDecodeError, KeyError) as e:
                        log.debug("OpenAPI schema parse error on port %d: %s", port, e)

                # Detect /docs (Swagger — sign of FastAPI)
                if path == "/docs" and resp.status_code == 200 and "swagger" in resp.text.lower():
                    svc.is_fastapi = True
                    if not svc.service:
                        svc.service = "FastAPI"

                # Detect AI API
                if path == "/v1/models" and resp.status_code == 200:
                    try:
                        data = resp.json()
                        if "data" in data or "models" in data:
                            svc.is_ai_api = True
                            if not svc.service:
                                svc.service = "AI API"
                    except json.JSONDecodeError as e:
                        log.debug("AI model list parse error on port %d: %s", port, e)

                # Detect Ollama
                if resp.status_code == 200 and "ollama" in resp.text.lower():
                    svc.service = "Ollama"
                    svc.is_ai_api = True

                # Detect MCP (SSE endpoint typically at /mcp or /sse)
                ct = resp.headers.get("content-type", "")
                if "text/event-stream" in ct:
                    svc.is_mcp = True
                    if not svc.service:
                        svc.service = "MCP Server"

                # Detect server from headers
                server_header = resp.headers.get("server", "")
                if server_header and not svc.service:
                    svc.service = server_header.split("/")[0]

                break  # found something, stop probing
            except (httpx.ConnectError, httpx.TimeoutException, OSError):
                continue
            except Exception as e:
                log.debug("Fingerprint probe error on port %d path %s: %s", port, path, e)
                continue

        if not svc.service:
            svc.service = "TCP"
        return svc


class ServiceDiscovery(QObject):
    """Manages scan workers and maintains the discovered service registry."""

    service_found   = pyqtSignal(object)   # DiscoveredService
    scan_started    = pyqtSignal()
    scan_finished   = pyqtSignal(list)     # List[DiscoveredService]
    progress        = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self._services: List[DiscoveredService] = []
        self._worker:   Optional[ScanWorker] = None

    @property
    def services(self) -> List[DiscoveredService]:
        return list(self._services)

    def scan(self, host: str = "127.0.0.1",
             start_port: int = 1, end_port: int = 9999):
        if self._worker and self._worker.isRunning():
            self._worker.stop()
            self._worker.wait()

        get_state().set(SK.DISCOVER_SCANNING, True)
        get_state().set(SK.DISCOVER_PROGRESS, 0)
        get_state().set(SK.DISCOVER_RANGE, f"{host}:{start_port}-{end_port}")
        self._services = []

        self._worker = ScanWorker(host, (start_port, end_port))
        self._worker.service_found.connect(self._on_service_found)
        self._worker.scan_progress.connect(self._on_progress)
        self._worker.scan_finished.connect(self._on_finished)
        self._worker.start()
        self.scan_started.emit()

    def stop_scan(self):
        if self._worker:
            self._worker.stop()

    def add_manual(self, svc: DiscoveredService):
        self._services = [s for s in self._services if s.address != svc.address]
        self._services.append(svc)
        get_state().set(SK.DISCOVER_SERVICES, list(self._services))
        self.service_found.emit(svc)

    def remove(self, host: str, port: int):
        self._services = [s for s in self._services
                          if not (s.host == host and s.port == port)]
        get_state().set(SK.DISCOVER_SERVICES, list(self._services))

    def _on_service_found(self, svc: DiscoveredService):
        self._services.append(svc)
        get_state().set(SK.DISCOVER_SERVICES, list(self._services))
        self.service_found.emit(svc)

    def _on_progress(self, pct: int):
        get_state().set(SK.DISCOVER_PROGRESS, pct)
        self.progress.emit(pct)

    def _on_finished(self, services: List[DiscoveredService]):
        get_state().set(SK.DISCOVER_SCANNING, False)
        self.scan_finished.emit(services)
        # Clean up worker to prevent memory leak
        if self._worker:
            self._worker.deleteLater()
            self._worker = None
