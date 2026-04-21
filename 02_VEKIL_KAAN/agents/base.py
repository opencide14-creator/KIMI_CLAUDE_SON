"""
agents/base.py — BaseAgent abstract base class.
Both Reactive and Heartbeat inherit from this.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any


class AgentStatus(str, Enum):
    INITIALIZING       = "INITIALIZING"
    ACTIVE             = "ACTIVE"
    SAFE_MODE          = "SAFE_MODE"          # Heartbeat missing — no actions
    AWAIT_RESYNC       = "AWAIT_RESYNC"       # Hash mismatch — waiting for sync
    BROTHERHOOD_MOURNING = "BROTHERHOOD_MOURNING"  # Peer agent disappeared
    SILENT_GUARDIAN    = "SILENT_GUARDIAN"    # Simulation detected — wait only
    HALTED             = "HALTED"


@dataclass
class AgentState:
    agent_id: str
    status: AgentStatus
    memory_root_hash: str
    last_event_id: str
    cycle_count: int
    extra: dict = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.extra is None:
            self.extra = {}


class BaseAgent(ABC):
    """
    Abstract base for both agents.
    Phase 5/6: implement in HeartbeatAgent and ReactiveAgent.
    """

    def __init__(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self._status = AgentStatus.INITIALIZING
        self._cycle_count = 0

    @property
    def status(self) -> AgentStatus:
        return self._status

    @abstractmethod
    def boot(self) -> None:
        """Initialize agent from memory substrate. Called during boot PHASE 5."""
        ...

    @abstractmethod
    def get_state(self) -> AgentState:
        """Return current agent state snapshot."""
        ...

    def _transition(self, new_status: AgentStatus) -> None:
        """Log status transition to memory."""
        old = self._status
        self._status = new_status
        # Phase 5/6: write STATE event to event store
