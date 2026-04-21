"""
OpenCode SDK Client — Programmatic Control
═══════════════════════════════════════════
Wraps OpenCode's HTTP API for full programmatic access.

Part of Phase 7: SDK Dominion
"""
from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger(__name__)

DEFAULT_OPENCODE_BASE_URL = "http://127.0.0.1"
OPENCODE_PROCESS_NAMES = ["opencode.exe", "opencode-cli.exe"]


class OpencodeClient:
    """
    SDK client for OpenCode local server.

    Provides:
    - Session management (list, create, delete)
    - Message sending (sync + streaming)
    - Config access (read)
    - Event subscription (SSE)
    - Process discovery (find running OpenCode port)
    """

    def __init__(self, base_url: str = None):
        self._base_url = base_url
        self._client = httpx.Client(timeout=120.0)
        self._async_client = httpx.AsyncClient(timeout=120.0)
        if not self._base_url:
            self._base_url = self._discover_server()

    def _discover_server(self) -> str:
        """Auto-discover OpenCode server port from running process."""
        import psutil
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            if proc.info['name'] in OPENCODE_PROCESS_NAMES:
                cmdline = proc.info['cmdline'] or []
                # Parse port from args: opencode serve --port 1234
                port = None
                for i, arg in enumerate(cmdline):
                    if arg in ('--port', '-p') and i + 1 < len(cmdline):
                        port = int(cmdline[i + 1])
                        break
                    if arg.startswith('--port='):
                        port = int(arg.split('=')[1])
                        break
                if port is None:
                    port = self._probe_ports()
                if port:
                    url = f"{DEFAULT_OPENCODE_BASE_URL}:{port}"
                    log.info("Discovered OpenCode server at %s (PID: %s)", url, proc.info['pid'])
                    return url
        log.warning("OpenCode server not found. Is it running?")
        return f"{DEFAULT_OPENCODE_BASE_URL}:4096"  # Default fallback

    def _probe_ports(self, start: int = 4000, end: int = 5000) -> Optional[int]:
        """Probe ports looking for OpenCode /health endpoint."""
        for port in range(start, end):
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.5)
                if r.status_code == 200:
                    return port
            except Exception:
                continue
        return None

    # ── Session Management ────────────────────────────────────────

    def list_sessions(self) -> List[Dict[str, Any]]:
        """Return all OpenCode sessions."""
        try:
            r = self._client.get(f"{self._base_url}/session")
            r.raise_for_status()
            data = r.json()
            return data.get("sessions", [])
        except Exception as e:
            log.error("list_sessions failed: %s", e)
            return []

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific session by ID."""
        try:
            r = self._client.get(f"{self._base_url}/session/{session_id}")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error("get_session failed: %s", e)
            return None

    def create_session(self, name: str = None) -> Optional[str]:
        """Create a new session and return its ID."""
        try:
            body = {}
            if name:
                body["name"] = name
            r = self._client.post(f"{self._base_url}/session", json=body)
            r.raise_for_status()
            data = r.json()
            return data.get("id")
        except Exception as e:
            log.error("create_session failed: %s", e)
            return None

    def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        try:
            r = self._client.delete(f"{self._base_url}/session/{session_id}")
            return r.status_code in (200, 204)
        except Exception as e:
            log.error("delete_session failed: %s", e)
            return False

    # ── Messaging ─────────────────────────────────────────────────

    def send_message(self, session_id: str, text: str) -> Optional[Dict[str, Any]]:
        """
        Send a text message to a session (synchronous, non-streaming).

        Args:
            session_id: Target session ID
            text: Message content

        Returns:
            Response dict or None on failure
        """
        try:
            body = {
                "parts": [{"type": "text", "text": text}]
            }
            r = self._client.post(
                f"{self._base_url}/session/{session_id}/message",
                json=body
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error("send_message failed: %s", e)
            return None

    def send_message_stream(self, session_id: str, text: str):
        """
        Send a message and stream the response.

        Yields response chunks as they arrive.
        """
        try:
            body = {
                "parts": [{"type": "text", "text": text}]
            }
            with self._client.stream(
                "POST",
                f"{self._base_url}/session/{session_id}/message",
                json=body
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_text():
                    if chunk.strip():
                        yield chunk
        except Exception as e:
            log.error("send_message_stream failed: %s", e)
            yield json.dumps({"error": str(e)})

    # ── Config Access ─────────────────────────────────────────────

    def get_config(self) -> Optional[Dict[str, Any]]:
        """Read current OpenCode configuration."""
        try:
            r = self._client.get(f"{self._base_url}/config")
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.error("get_config failed: %s", e)
            return None

    # ── Event Subscription ────────────────────────────────────────

    def subscribe_events(self, session_id: str = None):
        """
        Subscribe to real-time events via SSE.

        Yields event dicts.
        """
        try:
            url = f"{self._base_url}/event"
            if session_id:
                url += f"?session={session_id}"
            with self._client.stream("GET", url, headers={"Accept": "text/event-stream"}) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if line.startswith("data: "):
                        data = line[6:]
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            yield {"raw": data}
        except Exception as e:
            log.error("subscribe_events failed: %s", e)
            yield {"error": str(e)}

    # ── Process Management ────────────────────────────────────────

    def is_running(self) -> bool:
        """Check if OpenCode server is reachable."""
        try:
            r = self._client.get(f"{self._base_url}/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def get_server_version(self) -> Optional[str]:
        """Get OpenCode server version."""
        try:
            r = self._client.get(f"{self._base_url}/health", timeout=2.0)
            if r.status_code == 200:
                data = r.json()
                return data.get("version")
        except Exception:
            pass
        return None

    def close(self):
        """Close HTTP clients."""
        self._client.close()

    @property
    def base_url(self) -> str:
        return self._base_url

    @base_url.setter
    def base_url(self, value: str):
        self._base_url = value.rstrip("/")
