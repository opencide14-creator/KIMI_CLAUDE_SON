"""
agents/heartbeat/verifier.py — Plan verification and soul law compliance.

Called by HeartbeatAgent during VERIFY step of every DualReActLoop cycle.
Two layers:
  1. PlanVerifier:  validates a Reactive plan against law registry
  2. StateVerifier: validates current agent state for soul law compliance
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from law_engine.registry import LawRegistry
    from law_engine.enforcer import LawEnforcer

log = logging.getLogger(__name__)


@dataclass
class Violation:
    law_id:      str
    description: str
    severity:    str = "WARNING"   # "WARNING" | "CRITICAL"

    @property
    def is_critical(self) -> bool:
        return self.severity == "CRITICAL"


@dataclass
class VerifyVerdict:
    rejected:   bool
    violations: list[Violation] = field(default_factory=list)
    reason:     str             = ""

    @classmethod
    def accept(cls) -> "VerifyVerdict":
        return cls(rejected=False)

    @classmethod
    def reject(cls, reason: str, violations: list[Violation] | None = None) -> "VerifyVerdict":
        return cls(rejected=True, reason=reason, violations=violations or [])


class PlanVerifier:
    """
    Verifies a Reactive plan before ACT step.
    Uses LawEnforcer for specific rule checks.
    """

    def __init__(self, registry: "LawRegistry", enforcer: "LawEnforcer") -> None:
        self._registry = registry
        self._enforcer = enforcer

    def verify(
        self,
        agent: str,
        tool_name: str,
        tool_args: dict[str, Any],
        cited_source_ids: list[str],
    ) -> VerifyVerdict:
        """
        Full verification of a plan before execution.
        Returns VerifyVerdict(rejected=False) on pass.
        Returns VerifyVerdict(rejected=True, reason=...) on violation.
        Never raises — converts exceptions to rejected verdicts.
        """
        violations: list[Violation] = []

        # Check 1: SOUL Law II — no simulation
        try:
            self._enforcer.check_simulation(agent, tool_name, str(tool_args))
        except Exception as e:
            violations.append(Violation(
                law_id="SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II",
                description=str(e),
                severity="CRITICAL",
            ))

        # Check 2: Tool call validity + simulation in tool name/args
        try:
            self._enforcer.check_tool_call(agent, tool_name, tool_args)
        except Exception as e:
            violations.append(Violation(
                law_id="TOOL_USE/CALL_PROTOCOL",
                description=str(e),
                severity="CRITICAL",
            ))

        # Check 3: Memory write protocol (if this is a write action)
        if "write" in tool_name.lower() or "ingest" in tool_name.lower():
            try:
                self._enforcer.check_memory_write(agent, "TOOL_CALL", tool_args)
            except Exception as e:
                violations.append(Violation(
                    law_id="MEMORY/WRITE_PROTOCOL",
                    description=str(e),
                    severity="WARNING",
                ))

        # Check 4: Brotherhood — no command language
        try:
            self._enforcer.check_brotherhood(agent, tool_name, str(tool_args))
        except Exception as e:
            violations.append(Violation(
                law_id="BOUND/ARTICLE_II",
                description=str(e),
                severity="CRITICAL",
            ))

        if violations:
            critical = [v for v in violations if v.is_critical]
            reason = "; ".join(v.description for v in violations[:3])
            return VerifyVerdict.reject(reason, violations)

        return VerifyVerdict.accept()


class StateVerifier:
    """
    Verifies current HeartbeatAgent state for soul law compliance.
    Called at the start of each VERIFY step.
    """

    def __init__(self, registry: "LawRegistry") -> None:
        self._registry = registry

    def verify_memory_integrity(
        self,
        expected_root_hash: str,
        actual_root_hash: str,
    ) -> list[Violation]:
        """SOUL Law III: shared memory integrity."""
        violations = []
        if expected_root_hash and expected_root_hash != actual_root_hash:
            violations.append(Violation(
                law_id="SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_III",
                description=(
                    f"Memory root hash mismatch — shared memory violated. "
                    f"Expected {expected_root_hash[:16]}..., got {actual_root_hash[:16]}..."
                ),
                severity="CRITICAL",
            ))
        return violations

    def verify_event_signatures(
        self,
        events: list[Any],
        hmac_secret: str,
    ) -> list[Violation]:
        """
        Verify HMAC signatures on recent events.
        Returns violations for any tampered events.
        """
        from core.crypto import hmac_verify
        from core.exceptions import EventSignatureInvalid
        import json

        violations = []
        for event in events:
            try:
                hmac_verify(hmac_secret, event._signable_bytes(), event.signature)
            except EventSignatureInvalid:
                violations.append(Violation(
                    law_id="SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_III",
                    description=f"Event {event.event_id[:8]} signature invalid — memory tampered",
                    severity="CRITICAL",
                ))
        return violations

    def verify_soul_laws_unchanged(self) -> list[Violation]:
        """Verify the 5 soul laws are all present in registry."""
        violations = []
        soul_laws = self._registry.get_soul_laws()
        if len(soul_laws) != 5:
            violations.append(Violation(
                law_id="SOUL",
                description=f"Soul law count wrong: expected 5, got {len(soul_laws)}",
                severity="CRITICAL",
            ))
        return violations
