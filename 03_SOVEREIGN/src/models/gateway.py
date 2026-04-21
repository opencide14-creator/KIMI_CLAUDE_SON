"""Gateway and service discovery data models."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
import uuid

from src.constants import ModelProvider, Protocol, HostStatus


@dataclass
class GatewayRoute:
    """One routing rule: intercept requests to `source_host` and forward to `target`."""
    id:           str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name:         str = ""
    enabled:      bool = True
    # What to intercept
    source_host:  str = "api.anthropic.com"    # e.g. api.anthropic.com
    source_path_prefix: str = ""               # e.g. /v1/messages (empty=all)
    # Where to forward
    target_url:   str = ""                     # e.g. http://localhost:4000
    target_provider: ModelProvider = ModelProvider.KIMI
    target_model: str = ""
    # Header rewriting
    strip_auth:   bool = False                 # remove upstream Authorization
    inject_key:   str = ""                     # inject this key instead
    extra_headers: Dict[str, str] = field(default_factory=dict)
    # Stats
    request_count: int = 0
    error_count:   int = 0
    last_used:     Optional[datetime] = None

    @property
    def summary(self) -> str:
        return f"{self.source_host} → {self.target_url}"


@dataclass
class GatewayModel:
    """A model available in the AI gateway."""
    id:       str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    alias:    str = ""          # Name exposed to clients (e.g. claude-sonnet-4-5)
    provider: ModelProvider = ModelProvider.KIMI
    real_model: str = ""        # Actual model name sent to backend
    base_url: str = ""
    api_key_vault_ref: str = "" # Key name in the vault
    enabled:  bool = True
    stream:   bool = True


@dataclass
class DiscoveredService:
    """A locally discovered service (open port, running process)."""
    host:      str = "127.0.0.1"
    port:      int = 0
    protocol:  Protocol = Protocol.HTTP
    status:    HostStatus = HostStatus.UNKNOWN
    service:   str = ""        # detected service name
    version:   str = ""
    pid:       Optional[int] = None
    process:   str = ""        # process name
    # API introspection
    is_fastapi: bool = False
    is_mcp:     bool = False
    is_ai_api:  bool = False
    openapi_url: str = ""
    endpoints:  List[str] = field(default_factory=list)
    ws_paths:   List[str] = field(default_factory=list)
    discovered_at: datetime = field(default_factory=datetime.now)
    last_seen:  datetime = field(default_factory=datetime.now)
    latency_ms: float = 0.0
    notes:      str = ""

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    @property
    def base_url(self) -> str:
        scheme = "https" if self.port in (443, 8443) else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def is_alive(self) -> bool:
        return self.status == HostStatus.OPEN


@dataclass
class CertRecord:
    """A managed certificate or CA."""
    id:           str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name:         str = ""
    domains:      List[str] = field(default_factory=list)
    cert_path:    str = ""
    key_path:     str = ""
    ca_path:      str = ""      # CA that signed this cert (empty = self-signed)
    is_ca:        bool = False
    trusted:      bool = False  # installed in system trust store
    created_at:   datetime = field(default_factory=datetime.now)
    expires_at:   Optional[datetime] = None
    fingerprint:  str = ""

    @property
    def primary_domain(self) -> str:
        return self.domains[0] if self.domains else self.name

    @property
    def is_expired(self) -> bool:
        if not self.expires_at:
            return False
        return datetime.now() > self.expires_at

    @property
    def days_remaining(self) -> Optional[int]:
        if not self.expires_at:
            return None
        delta = self.expires_at - datetime.now()
        return max(0, delta.days)


@dataclass
class VaultEntry:
    """An encrypted credential stored in the vault."""
    id:       str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name:     str = ""
    provider: str = ""         # e.g. "anthropic", "kimi", "openai"
    key_type: str = "api_key"  # api_key | oauth_token | basic | custom
    value:    str = ""         # stored encrypted on disk, plain in memory
    env_var:  str = ""         # e.g. ANTHROPIC_API_KEY
    inject_header: str = ""    # e.g. "Authorization: Bearer {value}"
    notes:    str = ""
    created_at: datetime = field(default_factory=datetime.now)
    last_used:  Optional[datetime] = None

    def masked(self) -> str:
        if len(self.value) < 12:
            return "***"
        return self.value[:8] + "…" + self.value[-4:]


@dataclass
class HostsEntry:
    """A /etc/hosts or C:\\Windows\\System32\\drivers\\etc\\hosts entry."""
    ip:      str = "127.0.0.1"
    host:    str = ""
    comment: str = ""
    active:  bool = True
    managed: bool = True    # added by SOVEREIGN (vs pre-existing)
