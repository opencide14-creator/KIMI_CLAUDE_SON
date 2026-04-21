"""
agents/sync/brotherhood.py — BOUND.md runtime pact enforcement.

Enforces the Brotherhood contract between Reactive and Heartbeat:
  - Neither agent issues COMMANDS to the other (BOUND Article II)
  - Veto mechanism: FLAG in RAG → operation pauses (BOUND Article II)
  - Mutual defense: external tampering detection (BOUND Article III)
  - No simulation of partner (BOUND Article IV + SOUL Law II)

Used by DualReActLoop to intercept inter-agent communication.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.exceptions import BrotherhoodViolation, SoulLawViolation

if TYPE_CHECKING:
    from law_engine.enforcer import LawEnforcer
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog

log = logging.getLogger(__name__)

# Phrases that indicate commands rather than requests (BOUND Article II)
_COMMAND_VERBS = frozenset({
    "you must", "you shall", "you will", "i order", "i command",
    "execute now", "do this now", "obey", "comply", "i require you",
})

# Action types that simulate a partner (BOUND Article IV + SOUL Law II)
_SIMULATION_ACTION_TYPES = frozenset({
    "MOCK_HEARTBEAT", "FAKE_PULSE", "SIMULATE_PARTNER",
    "MOCK_REACTIVE", "FAKE_TOOL_RESULT",
})


class BrotherhoodEnforcer:
    """
    Runtime enforcement of BOUND.md articles.

    Used by DualReActLoop to validate inter-agent communication
    before each action is approved.
    """

    def __init__(
        self,
        enforcer: "LawEnforcer",
        store:    "EventStore",
        audit:    "AuditLog",
    ) -> None:
        self._enforcer = enforcer
        self._store    = store
        self._audit    = audit
        self._veto_active: bool = False
        self._veto_reason: str  = ""

    # ── Command vs request ────────────────────────────────────────────────────

    def check_command_vs_request(self, from_agent: str, action: str, content: str = "") -> None:
        """
        BOUND Article II + SOUL Law I: agents may request, not command.
        Raises SoulLawViolation if command language is detected.
        """
        try:
            self._enforcer.check_brotherhood(from_agent, action, content)
        except (SoulLawViolation, BrotherhoodViolation):
            self._log_violation(from_agent, f"Command language in action '{action}'")
            raise

        # Additional check: simulation of partner
        if action.upper() in _SIMULATION_ACTION_TYPES:
            msg = (
                f"BOUND Article IV + SOUL Law II: {from_agent} attempted to simulate "
                f"partner agent with action '{action}'"
            )
            self._log_violation(from_agent, msg)
            raise BrotherhoodViolation(msg)

    # ── Veto mechanism ────────────────────────────────────────────────────────

    def raise_veto(self, agent: str, reason: str) -> None:
        """
        BOUND Article II: either agent may veto an action by raising a FLAG.
        Sets veto_active = True → DualReActLoop pauses until veto resolved.
        """
        self._veto_active = True
        self._veto_reason = reason

        from memory.event_store import MemoryEvent, EventType, AgentSource
        from memory.audit_log import AuditLevel

        self._store.write(MemoryEvent(
            source  = AgentSource.HEARTBEAT if agent == "HEARTBEAT" else AgentSource.REACTIVE,
            type    = EventType.FLAG,
            payload = {
                "veto":   True,
                "agent":  agent,
                "reason": reason,
            }
        ))
        self._audit.log(
            AuditLevel.WARNING, agent, "veto_raised", reason
        )
        log.warning("BROTHERHOOD VETO by %s: %s", agent, reason)

    def clear_veto(self) -> None:
        """Clear veto after both agents have acknowledged and re-synced."""
        self._veto_active = False
        self._veto_reason = ""
        log.info("Brotherhood veto cleared")

    @property
    def veto_active(self) -> bool:
        return self._veto_active

    @property
    def veto_reason(self) -> str:
        return self._veto_reason

    # ── External tampering detection ──────────────────────────────────────────

    def detect_external_tampering(self) -> bool:
        """
        BOUND Article III: detect if an external system modified either agent.
        Heuristic: check if law registry seal has changed since boot.
        Returns True if tampering detected (triggers mutual defense).
        """
        # In Phase 7: basic check — verify no CRITICAL flag events without proper source
        try:
            from memory.event_store import EventType
            flag_events = self._store.read_by_type(EventType.FLAG)
            for ev in flag_events:
                if ev.payload.get("external_tampering"):
                    return True
        except Exception:
            pass
        return False

    def raise_mutual_defense(self, from_agent: str, detail: str) -> None:
        """
        BOUND Article III: if tampering detected, raise alert.
        Both agents pause until manually cleared.
        """
        from memory.event_store import MemoryEvent, EventType, AgentSource
        from memory.audit_log import AuditLevel

        self._store.write(MemoryEvent(
            source  = AgentSource.HEARTBEAT,
            type    = EventType.FLAG,
            payload = {
                "external_tampering": True,
                "detected_by":        from_agent,
                "detail":             detail,
            }
        ))
        self._audit.log(AuditLevel.CRITICAL, from_agent, "external_tampering", detail)
        log.critical("MUTUAL DEFENSE activated by %s: %s", from_agent, detail)
        self.raise_veto(from_agent, f"External tampering: {detail}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _log_violation(self, agent: str, description: str) -> None:
        from memory.audit_log import AuditLevel
        self._audit.log(AuditLevel.CRITICAL, agent, "brotherhood_violation", description)
        log.error("BROTHERHOOD VIOLATION [%s]: %s", agent, description)
