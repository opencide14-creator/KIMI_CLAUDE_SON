"""Reactive state management for SOVEREIGN — observer pattern via Qt signals."""
from __future__ import annotations
import logging
import threading
from typing import Any, Callable, Dict, List, Set, Type

from PyQt6.QtCore import QObject, pyqtSignal

from src.constants import ServiceStatus

log = logging.getLogger(__name__)


class StateValidationError(Exception):
    """Raised when state validation fails."""

    def __init__(self, key: str, value: Any, reason: str = ""):
        self.key = key
        self.value = value
        self.reason = reason
        msg = f"Validation failed for '{key}'"
        if reason:
            msg += f": {reason}"
        super().__init__(msg)


class StateManager(QObject):
    """Central reactive store. All UI components subscribe to the keys they need."""

    # Single key-value change signal
    state_changed = pyqtSignal(str, object)
    # Batch change signal - emits dict of all changed keys
    batch_state_changed = pyqtSignal(dict)

    def __init__(self):
        super().__init__()
        self._state:       Dict[str, Any]              = {}
        self._subscribers: Dict[str, List[Callable]]   = {}
        self._lock         = threading.RLock()  # S-14: thread safety for cross-thread writes
        # Validation rules: key -> callable(value) -> bool
        self._validators: Dict[str, Callable[[Any], bool]] = {}
        self._setup_default_validators()

    def _setup_default_validators(self):
        """Register default validation rules for known state keys."""
        # Port numbers must be positive integers
        self.register_validator(SK.PROXY_PORT, lambda v: isinstance(v, int) and 1 <= v <= 65535)
        self.register_validator(SK.GATEWAY_PORT, lambda v: isinstance(v, int) and 1 <= v <= 65535)

        # Status must be a valid ServiceStatus
        self.register_validator(SK.PROXY_STATUS, self._validate_service_status)
        self.register_validator(SK.GATEWAY_STATUS, self._validate_service_status)

        # Boolean flags
        self.register_validator(SK.TRAFFIC_PAUSED, lambda v: isinstance(v, bool))
        self.register_validator(SK.VAULT_UNLOCKED, lambda v: isinstance(v, bool))
        self.register_validator(SK.DISCOVER_SCANNING, lambda v: isinstance(v, bool))
        self.register_validator(SK.SIDEBAR_COLLAPSED, lambda v: isinstance(v, bool))

        # Numeric counters must be non-negative
        for key in (SK.PROXY_INTERCEPTED, SK.GATEWAY_REQUEST_COUNT, SK.INTEL_AI_CALLS, SK.INTEL_ERRORS):
            self.register_validator(key, lambda v: isinstance(v, int) and v >= 0)

        # Latency must be non-negative float
        self.register_validator(SK.INTEL_LATENCY_AVG, lambda v: isinstance(v, (int, float)) and v >= 0)

        # List types should be lists
        for key in (SK.GATEWAY_ROUTES, SK.GATEWAY_MODELS, SK.TRAFFIC_ENTRIES,
                    SK.WS_CONNECTIONS, SK.MCP_MESSAGES, SK.CERT_LIST, SK.HOSTS_ENTRIES,
                    SK.VAULT_ENTRIES, SK.DISCOVER_SERVICES, SK.NOTIFICATIONS):
            self.register_validator(key, lambda v: v is None or isinstance(v, list))

        # Progress must be 0-100
        self.register_validator(SK.DISCOVER_PROGRESS, lambda v: isinstance(v, int) and 0 <= v <= 100)

    def _validate_service_status(self, value: Any) -> bool:
        """Check if value is a valid ServiceStatus enum member."""
        if value is None:
            return True
        try:
            return isinstance(value, ServiceStatus)
        except Exception:
            return False

    def register_validator(self, key: str, validator: Callable[[Any], bool]) -> None:
        """Register a validation function for a state key.

        Args:
            key: The state key to validate
            validator: Callable that takes a value and returns True if valid
        """
        with self._lock:
            self._validators[key] = validator

    def remove_validator(self, key: str) -> None:
        """Remove the validator for a specific key."""
        with self._lock:
            self._validators.pop(key, None)

    def _validate(self, key: str, value: Any) -> bool:
        """Validate a state value against registered validators.

        Args:
            key: The state key
            value: The value to validate

        Returns:
            True if no validator registered or validation passes
        """
        with self._lock:
            validator = self._validators.get(key)
        if validator is None:
            return True
        try:
            return validator(value)
        except Exception as e:
            log.warning("Validator for '%s' raised exception: %s", key, e)
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a state value by key.

        Supports nested access via dot notation (e.g. 'a.b.c').
        Also supports list indexing (e.g. 'a.b.1').
        If any segment is missing, returns default.
        """
        with self._lock:
            parts = key.split(".")
            val = self._state.get(parts[0], default)
            for part in parts[1:]:
                if isinstance(val, dict) and part in val:
                    val = val[part]
                elif isinstance(val, list):
                    try:
                        idx = int(part)
                        val = val[idx]
                    except (ValueError, IndexError):
                        return default
                else:
                    return default
            return val

    def set(self, key: str, value: Any):
        """Set a state value by key.

        Supports nested access via dot notation (e.g. 'a.b.c').
        Creates intermediate dicts as needed.
        """
        with self._lock:
            parts = key.split(".")
            if len(parts) == 1:
                self._state[key] = value
            else:
                # If the first part already exists and is NOT a dict,
                # overwrite it with a dict so we can nest into it.
                if parts[0] in self._state and not isinstance(self._state[parts[0]], dict):
                    self._state[parts[0]] = {}
                d = self._state.setdefault(parts[0], {})
                for part in parts[1:-1]:
                    if part in d and not isinstance(d[part], dict):
                        d[part] = {}
                    d = d.setdefault(part, {})
                d[parts[-1]] = value
            callbacks = list(self._subscribers.get(key, []))
        # Emit Qt signal and call subscribers OUTSIDE the lock to prevent deadlock
        self.state_changed.emit(key, value)
        for cb in callbacks:
            try:
                cb(value)
            except Exception as e:
                log.error("State subscriber error for '%s': %s", key, e)

    def subscribe(self, key: str, callback: Callable):
        with self._lock:
            self._subscribers.setdefault(key, []).append(callback)

    def unsubscribe(self, key: str, callback: Callable):
        with self._lock:
            if key in self._subscribers:
                self._subscribers[key] = [c for c in self._subscribers[key] if c != callback]

    def update(self, updates: Dict[str, Any]):
        """Non-atomic update - updates each key individually.

        Note: Use update_batch() for atomic all-or-nothing updates.
        """
        for k, v in updates.items():
            self.set(k, v)

    def update_batch(self, updates: Dict[str, Any]) -> None:
        """Atomically update multiple state keys.

        All-or-nothing semantics: if any key fails validation, NO keys are updated.

        Args:
            updates: Dictionary of key-value pairs to update

        Raises:
            StateValidationError: If any key fails validation
        """
        if not updates:
            return

        with self._lock:
            # Phase 1: Validate ALL updates first
            validated: Dict[str, Any] = {}
            failed_keys: List[tuple] = []

            for key, value in updates.items():
                if not self._validate(key, value):
                    failed_keys.append((key, value))

            # If any validation failed, raise error without modifying anything
            if failed_keys:
                key, value = failed_keys[0]
                raise StateValidationError(
                    key, value,
                    f"Validation failed for key '{key}'"
                )

            # Phase 2: All valid - apply atomically
            for key, value in updates.items():
                self._state[key] = value

            # Copy for emission outside lock
            validated = dict(updates)

        # Phase 3: Emit ONE notification with all changes (outside lock)
        self.batch_state_changed.emit(validated)

    def get_validated(self, key: str, default: Any = None) -> Any:
        """Get a value and validate it against current state rules.

        Useful for debugging/monitoring state integrity.
        """
        value = self.get(key, default)
        if not self._validate(key, value):
            log.warning("State key '%s' has invalid value: %s", key, value)
        return value

    def get_all(self) -> Dict[str, Any]:
        """Return a shallow copy of entire state dict.

        Note: For observability/debugging only. Updates to returned dict
        do not affect internal state.
        """
        with self._lock:
            return dict(self._state)


# ── Singleton ──────────────────────────────────────────────────────
_state: StateManager | None = None


def get_state() -> StateManager:
    global _state
    if _state is None:
        _state = StateManager()
    return _state


# ── All typed state keys ───────────────────────────────────────────
class SK:
    """State Keys — namespaced with dots."""

    # ── Proxy service ──────────────────────────────────────────────
    PROXY_STATUS      = "proxy.status"        # ServiceStatus
    PROXY_PORT        = "proxy.port"           # int
    PROXY_INTERCEPTED = "proxy.intercepted"    # int — total count
    PROXY_CERT_DIR    = "proxy.cert_dir"       # str

    # ── Gateway service ────────────────────────────────────────────
    GATEWAY_STATUS    = "gateway.status"       # ServiceStatus
    GATEWAY_PORT      = "gateway.port"         # int
    GATEWAY_ROUTES    = "gateway.routes"       # List[GatewayRoute]
    GATEWAY_MODELS    = "gateway.models"       # List[GatewayModel]
    GATEWAY_REQUEST_COUNT = "gateway.requests" # int

    # ── Traffic ───────────────────────────────────────────────────
    TRAFFIC_ENTRIES   = "traffic.entries"      # List[TrafficEntry]
    TRAFFIC_NEW       = "traffic.new"          # TrafficEntry (new request)
    TRAFFIC_UPDATE    = "traffic.update"       # TrafficEntry (response arrived)
    TRAFFIC_SELECTED  = "traffic.selected"     # Optional[TrafficEntry]
    TRAFFIC_FILTER    = "traffic.filter"       # str (search text)
    TRAFFIC_PAUSED    = "traffic.paused"       # bool

    # ── WebSocket / MCP ────────────────────────────────────────────
    WS_CONNECTIONS    = "ws.connections"       # List[WsConnection]
    WS_FRAMES_NEW     = "ws.frames_new"        # WsFrame (latest)
    WS_SELECTED_CONN  = "ws.selected_conn"     # Optional[WsConnection]
    MCP_MESSAGES      = "mcp.messages"         # List[McpMessage]
    MCP_NEW           = "mcp.new"              # McpMessage (latest)

    # ── Discovery ──────────────────────────────────────────────────
    DISCOVER_SERVICES = "discover.services"    # List[DiscoveredService]
    DISCOVER_SCANNING = "discover.scanning"    # bool
    DISCOVER_PROGRESS = "discover.progress"    # int (0-100)
    DISCOVER_RANGE    = "discover.range"       # str (e.g. "127.0.0.1:1-9999")

    # ── Forge / Certificates ───────────────────────────────────────
    CERT_LIST         = "cert.list"            # List[CertRecord]
    CERT_SELECTED     = "cert.selected"        # Optional[CertRecord]
    HOSTS_ENTRIES     = "hosts.entries"        # List[HostsEntry]

    # ── Vault ─────────────────────────────────────────────────────
    VAULT_ENTRIES     = "vault.entries"        # List[VaultEntry]
    VAULT_UNLOCKED    = "vault.unlocked"       # bool

    # ── UI ─────────────────────────────────────────────────────────
    ACTIVE_PANEL      = "ui.active_panel"      # PanelID
    SIDEBAR_COLLAPSED = "ui.sidebar_collapsed" # bool
    NOTIFICATIONS     = "ui.notifications"     # List[str]

    # ── Intel / Stats ──────────────────────────────────────────────
    INTEL_STATS       = "intel.stats"          # dict of counters
    INTEL_AI_CALLS    = "intel.ai_calls"       # int
    INTEL_ERRORS      = "intel.errors"         # int
    INTEL_LATENCY_AVG = "intel.latency_avg"    # float ms
