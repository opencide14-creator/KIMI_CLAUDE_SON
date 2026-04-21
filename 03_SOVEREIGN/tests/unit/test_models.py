"""Unit tests for SOVEREIGN data models and core utilities."""
import json
import os
import sys
import tempfile
import pytest
from pathlib import Path
from datetime import datetime

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])


# ── Constants ──────────────────────────────────────────────────────

class TestConstants:
    def test_panel_ids_all_present(self):
        from src.constants import PanelID
        ids = {p.value for p in PanelID}
        assert "intercept" in ids
        assert "forge"     in ids
        assert "gateway"   in ids
        assert "streams"   in ids
        assert "discover"  in ids
        assert "vault"     in ids
        assert "intel"     in ids

    def test_known_ai_hosts(self):
        from src.constants import KNOWN_AI_HOSTS, ModelProvider
        assert "api.anthropic.com" in KNOWN_AI_HOSTS
        assert KNOWN_AI_HOSTS["api.anthropic.com"] == ModelProvider.ANTHROPIC
        assert "api.openai.com" in KNOWN_AI_HOSTS

    def test_status_color_ranges(self):
        from src.constants import status_color, COLORS
        assert status_color(200) == COLORS["neon_green"]
        assert status_color(301) == COLORS["neon_cyan"]
        assert status_color(404) == COLORS["neon_yellow"]
        assert status_color(500) == COLORS["neon_red"]

    def test_provider_urls_not_empty(self):
        from src.constants import PROVIDER_URLS, ModelProvider
        for provider in (ModelProvider.ANTHROPIC, ModelProvider.KIMI, ModelProvider.OPENAI):
            assert provider in PROVIDER_URLS
            assert PROVIDER_URLS[provider].startswith("http")


# ── TrafficRequest ─────────────────────────────────────────────────

class TestTrafficRequest:
    def test_url_construction_https(self):
        from src.models.traffic import TrafficRequest, HttpHeaders
        from src.constants import Protocol
        req = TrafficRequest(
            protocol=Protocol.HTTPS, method="POST",
            host="api.anthropic.com", port=443,
            path="/v1/messages", query="",
        )
        assert req.url == "https://api.anthropic.com/v1/messages"

    def test_url_construction_custom_port(self):
        from src.models.traffic import TrafficRequest
        from src.constants import Protocol
        req = TrafficRequest(
            protocol=Protocol.HTTP, method="GET",
            host="localhost", port=4000, path="/health",
        )
        assert "4000" in req.url
        assert "localhost" in req.url

    def test_body_text_utf8(self):
        from src.models.traffic import TrafficRequest
        req = TrafficRequest(body=b'{"model":"claude"}')
        assert '"model"' in req.body_text

    def test_body_text_binary(self):
        from src.models.traffic import TrafficRequest
        # bytes(range(256)) decoded with errors="replace" produces a long string
        req = TrafficRequest(body=bytes(range(256)))
        text = req.body_text
        assert isinstance(text, str)
        assert len(text) > 0  # always returns a string, never raises

    def test_is_json_detection(self):
        from src.models.traffic import TrafficRequest, HttpHeaders
        h = HttpHeaders()
        h.set("content-type", "application/json")
        req = TrafficRequest(headers=h)
        assert req.is_json is True

    def test_summary(self):
        from src.models.traffic import TrafficRequest
        req = TrafficRequest(method="POST", host="api.anthropic.com", path="/v1/messages")
        assert "POST" in req.summary()
        assert "api.anthropic.com" in req.summary()


# ── HttpHeaders ────────────────────────────────────────────────────

class TestHttpHeaders:
    def test_case_insensitive(self):
        from src.models.traffic import HttpHeaders
        h = HttpHeaders()
        h.set("Content-Type", "application/json")
        assert h.get("content-type") == "application/json"
        assert h.get("CONTENT-TYPE") == "application/json"

    def test_from_dict(self):
        from src.models.traffic import HttpHeaders
        h = HttpHeaders.from_dict({"Authorization": "Bearer sk-xxx", "Accept": "*/*"})
        assert h.get("authorization") == "Bearer sk-xxx"

    def test_default_value(self):
        from src.models.traffic import HttpHeaders
        h = HttpHeaders()
        assert h.get("missing", "fallback") == "fallback"


# ── WsFrame ────────────────────────────────────────────────────────

class TestWsFrame:
    def test_text_frame(self):
        from src.models.traffic import WsFrame
        frame = WsFrame(opcode=1, payload=b'{"jsonrpc":"2.0"}')
        assert frame.opcode_name == "TEXT"
        assert "jsonrpc" in frame.payload_text

    def test_binary_size_str(self):
        from src.models.traffic import WsFrame
        frame = WsFrame(payload=b"x" * 2048)
        assert "KB" in frame.size_str

    def test_mcp_detection(self):
        from src.models.traffic import WsFrame
        payload = json.dumps({"jsonrpc": "2.0", "method": "tools/list", "id": 1}).encode()
        frame = WsFrame(opcode=1, payload=payload)
        # Not auto-detected here — detection happens in stream monitor
        assert frame.payload_text  # just verify it's readable


# ── GatewayRoute ──────────────────────────────────────────────────

class TestGatewayRoute:
    def test_summary(self):
        from src.models.gateway import GatewayRoute
        from src.constants import ModelProvider
        route = GatewayRoute(
            source_host="api.anthropic.com",
            target_url="http://localhost:4000",
            target_provider=ModelProvider.KIMI,
        )
        # summary is a @property returning a str
        assert "api.anthropic.com" in route.summary
        assert "localhost:4000" in route.summary

    def test_defaults(self):
        from src.models.gateway import GatewayRoute
        route = GatewayRoute(source_host="x.com", target_url="http://y.com")
        assert route.enabled is True
        assert route.request_count == 0
        assert route.error_count == 0


# ── DiscoveredService ─────────────────────────────────────────────

class TestDiscoveredService:
    def test_address(self):
        from src.models.gateway import DiscoveredService
        svc = DiscoveredService(host="127.0.0.1", port=8080)
        assert svc.address == "127.0.0.1:8080"

    def test_base_url_http(self):
        from src.models.gateway import DiscoveredService
        svc = DiscoveredService(host="127.0.0.1", port=8080)
        assert svc.base_url == "http://127.0.0.1:8080"

    def test_base_url_https(self):
        from src.models.gateway import DiscoveredService
        svc = DiscoveredService(host="127.0.0.1", port=443)
        assert svc.base_url == "https://127.0.0.1:443"

    def test_is_alive(self):
        from src.models.gateway import DiscoveredService
        from src.constants import HostStatus
        svc = DiscoveredService(status=HostStatus.OPEN)
        assert svc.is_alive is True
        svc2 = DiscoveredService(status=HostStatus.CLOSED)
        assert svc2.is_alive is False


# ── CertRecord ────────────────────────────────────────────────────

class TestCertRecord:
    def test_primary_domain(self):
        from src.models.gateway import CertRecord
        rec = CertRecord(domains=["api.anthropic.com", "www.anthropic.com"])
        assert rec.primary_domain == "api.anthropic.com"

    def test_days_remaining(self):
        from src.models.gateway import CertRecord
        from datetime import timedelta
        rec = CertRecord(expires_at=datetime.now() + timedelta(days=30))
        assert rec.days_remaining is not None
        assert rec.days_remaining > 0

    def test_expired(self):
        from src.models.gateway import CertRecord
        from datetime import timedelta
        rec = CertRecord(expires_at=datetime.now() - timedelta(days=1))
        assert rec.is_expired is True


# ── VaultEntry ────────────────────────────────────────────────────

class TestVaultEntry:
    def test_masked_long_key(self):
        from src.models.gateway import VaultEntry
        e = VaultEntry(name="test", value="sk-kimi-abcdef1234567890")
        m = e.masked()
        assert "…" in m
        assert e.value[:8] in m
        assert e.value[-4:] in m

    def test_masked_short_key(self):
        from src.models.gateway import VaultEntry
        e = VaultEntry(name="test", value="short")
        assert e.masked() == "***"


# ── StateManager ─────────────────────────────────────────────────

class TestStateManager:
    def test_set_get(self):
        from src.models.state import StateManager
        sm = StateManager()
        sm.set("x.y", 42)
        assert sm.get("x.y") == 42

    def test_default(self):
        from src.models.state import StateManager
        sm = StateManager()
        assert sm.get("missing", "default") == "default"

    def test_subscribe(self):
        from src.models.state import StateManager
        sm = StateManager()
        received = []
        sm.subscribe("k", received.append)
        sm.set("k", "hello")
        assert received == ["hello"]

    def test_unsubscribe(self):
        from src.models.state import StateManager
        sm = StateManager()
        received = []
        cb = received.append
        sm.subscribe("k", cb)
        sm.unsubscribe("k", cb)
        sm.set("k", 1)
        assert received == []

    def test_subscriber_error_does_not_crash(self):
        from src.models.state import StateManager
        sm = StateManager()
        def bad_cb(v):
            raise ValueError("intentional error")
        sm.subscribe("k", bad_cb)
        sm.set("k", 1)  # Must not raise


# ── HostsManager (read-only tests) ────────────────────────────────

class TestHostsManager:
    def test_parse_simple_entry(self, tmp_path):
        from src.core.cert.hosts import HostsManager
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(
            "127.0.0.1 localhost\n"
            "::1 localhost\n"
            "# comment\n"
            "192.168.1.1 router\n"
        )
        mgr = HostsManager()
        mgr._path = hosts_file
        entries = mgr.read_all()
        assert any(e.host == "localhost" and e.ip == "127.0.0.1" for e in entries)
        assert any(e.host == "router" for e in entries)

    def test_sovereign_block_detection(self, tmp_path):
        from src.core.cert.hosts import HostsManager, SOVEREIGN_MARKER_START, SOVEREIGN_MARKER_END
        hosts_file = tmp_path / "hosts"
        hosts_file.write_text(
            "127.0.0.1 localhost\n"
            f"{SOVEREIGN_MARKER_START}\n"
            "127.0.0.1 api.anthropic.com\n"
            f"{SOVEREIGN_MARKER_END}\n"
        )
        mgr = HostsManager()
        mgr._path   = hosts_file
        mgr._backup = tmp_path / "hosts.bak"
        entries = mgr.read_all()
        sovereign = [e for e in entries if e.managed]
        assert len(sovereign) == 1
        assert sovereign[0].host == "api.anthropic.com"


# ── Formatters ─────────────────────────────────────────────────────

class TestFormatters:
    def test_fmt_bytes(self):
        from src.utils.formatters import fmt_bytes
        assert fmt_bytes(0)       == "0 B"
        assert fmt_bytes(1023)    == "1023 B"
        assert fmt_bytes(1024)    == "1.0 KB"
        assert fmt_bytes(1048576) == "1.0 MB"

    def test_fmt_ms(self):
        from src.utils.formatters import fmt_ms
        assert fmt_ms(0.5)   == "<1ms"
        assert fmt_ms(500)   == "500ms"
        assert fmt_ms(1500)  == "1.50s"

    def test_pretty_json(self):
        from src.utils.formatters import pretty_json
        result = pretty_json({"key": "value"})
        assert '"key"' in result
        assert '"value"' in result

    def test_mask_key(self):
        from src.utils.formatters import mask_key
        key = "sk-kimi-abcdef1234567890"
        m = mask_key(key)
        assert "…" in m
        assert key not in m

    def test_truncate(self):
        from src.utils.formatters import truncate
        assert truncate("hello", 3) == "he…"
        assert truncate("hi", 10)   == "hi"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
