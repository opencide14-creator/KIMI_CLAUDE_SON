"""SovereignToolExecutor — the tool bridge that ReactiveAgent calls.
Real tools. Real execution. Nothing mocked.
"""
from __future__ import annotations
import logging
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from src.core.vault.store import VaultStore
from src.models.state import get_state, SK
from src.constants import ServiceStatus

log = logging.getLogger(__name__)


class SovereignToolExecutor:
    """Execute SOVEREIGN tools on behalf of ReactiveAgent."""

    def __init__(self, proxy=None, gateway=None, discovery=None):
        self._proxy     = proxy
        self._gateway   = gateway
        self._discovery = discovery

    def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Dispatch to the correct tool. Returns string result."""
        handlers = {
            "nmap_scan":         self._nmap_scan,
            "proxy_control":     self._proxy_control,
            "gateway_add_route": self._gateway_add_route,
            "vault_read":        self._vault_read,
            "read_file":         self._read_file,
            "write_file":        self._write_file,
            "sovereign_status":  self._sovereign_status,
            "search_memory":     self._search_memory,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return f"Unknown tool: {tool_name}"
        try:
            return handler(**args)
        except TypeError as e:
            return f"Tool arg error ({tool_name}): {e}"
        except Exception as e:
            log.error("Tool %s error: %s", tool_name, e)
            return f"Tool error: {e}"

    # ── Tools ──────────────────────────────────────────────────────

    def _nmap_scan(self, target: str, ports: str = "1-1024",
                   args: str = "-sV -T4") -> str:
        import shutil
        if not shutil.which("nmap"):
            return "nmap not installed. Install from Tools tab in Discover panel."
        cmd = ["nmap"] + args.split() + ["-p", ports, "-oN", "-", target]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode == 0 and r.stdout:
                lines = [l for l in r.stdout.splitlines() if l.strip()]
                return "\n".join(lines[:80])
            return r.stderr[:300] or "No output"
        except subprocess.TimeoutExpired:
            return "nmap timed out (120s)"
        except Exception as e:
            return str(e)

    def _proxy_control(self, action: str, port: int = 8080) -> str:
        if not self._proxy:
            return "Proxy engine not available"
        if action == "start":
            if self._proxy.status == ServiceStatus.RUNNING:
                return f"Proxy already running on port {self._proxy.port}"
            self._proxy._port = port
            self._proxy.start()
            # Wait up to 3s for RUNNING status — no UI-blocking sleep
            import time as _t
            deadline = _t.time() + 3.0
            while _t.time() < deadline:
                if self._proxy.status == ServiceStatus.RUNNING:
                    break
                _t.sleep(0.1)
            return f"Proxy started on port {port} — status: {self._proxy.status.value}"
        elif action == "stop":
            self._proxy.stop()
            return "Proxy stopped"
        elif action == "status":
            entries = get_state().get(SK.TRAFFIC_ENTRIES, [])
            return (f"Proxy: {self._proxy.status.value} | "
                    f"port: {self._proxy.port} | "
                    f"captured: {len(entries)} requests")
        return f"Unknown action: {action}"

    def _gateway_add_route(self, source_host: str, target_url: str,
                           target_model: str = "", inject_key: str = "") -> str:
        from src.models.gateway import GatewayRoute
        from src.constants import ModelProvider
        route = GatewayRoute(
            name        = f"Agent: {source_host}→{target_url}",
            source_host = source_host,
            target_url  = target_url,
            target_model= target_model,
            inject_key  = inject_key,
            enabled     = True,
        )
        if self._gateway:
            self._gateway.add_route(route)
        get_state().set("gateway.save_route", route)
        return f"Route added: {source_host} → {target_url} model={target_model or 'auto'}"

    def _vault_read(self, name: str) -> str:
        if not VaultStore.is_unlocked():
            VaultStore.unlock("")
        val = VaultStore.get_key(name)
        if val:
            return f"Vault '{name}': {val[:8]}…{val[-4:]} (masked for security)"
        return f"No vault entry named '{name}'"

    def _read_file(self, path: str) -> str:
        try:
            p = Path(path)
            if not p.exists():
                return f"File not found: {path}"
            content = p.read_text(errors="replace")
            if len(content) > 4000:
                return content[:4000] + f"\n…[truncated — {len(content)} chars total]"
            return content
        except PermissionError:
            return f"Permission denied: {path}"
        except Exception as e:
            return str(e)

    def _write_file(self, path: str, content: str) -> str:
        try:
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Written {len(content)} chars to {path}"
        except PermissionError:
            return f"Permission denied: {path}"
        except Exception as e:
            return str(e)

    def _sovereign_status(self) -> str:
        lines = ["=== SOVEREIGN STATUS ==="]
        if self._proxy:
            entries = get_state().get(SK.TRAFFIC_ENTRIES, [])
            ai_calls = sum(1 for e in entries if e.request.is_ai_api)
            lines.append(f"Proxy:   {self._proxy.status.value} | port {self._proxy.port} | {len(entries)} captured ({ai_calls} AI)")
        if self._gateway:
            routes = get_state().get(SK.GATEWAY_ROUTES, [])
            req_count = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0)
            lines.append(f"Gateway: {self._gateway.status.value} | port {self._gateway.port} | {len(routes)} routes | {req_count} routed")
        services = get_state().get(SK.DISCOVER_SERVICES, [])
        lines.append(f"Discover: {len(services)} services known")
        ws_conns = get_state().get(SK.WS_CONNECTIONS, [])
        active_ws = sum(1 for c in ws_conns if c.is_active)
        lines.append(f"Streams: {len(ws_conns)} WS connections ({active_ws} active)")
        if VaultStore.is_unlocked():
            vault_entries = VaultStore.list_entries()
            lines.append(f"Vault:   {len(vault_entries)} entries (unlocked)")
        else:
            lines.append("Vault:   locked")
        return "\n".join(lines)

    def _search_memory(self, query: str) -> str:
        from src.core.agents.memory import get_memory
        memory = get_memory()
        if not memory.ready:
            return "Memory not yet booted — boot agents first"
        results = memory.search_semantic(query, n=6)
        if not results:
            return f"No memory found for: {query}"
        lines = [f"=== MEMORY SEARCH: {query} ==="]
        for r in results:
            meta = r.get("meta", {})
            lines.append(f"[{meta.get('type','?')} | {meta.get('ts','?')[:19]}] {r['text'][:200]}")
        return "\n".join(lines)
