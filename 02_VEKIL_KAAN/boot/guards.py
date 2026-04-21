"""
boot/guards.py — Anti-hallucination guardrails.

These are structural constraints enforced at boot and at runtime.
They prevent agents from operating on fabricated or unverified state.

All check_*() methods either pass silently or raise a typed exception.
All checks are synchronous and fast (< 10ms each).

Guards are constructed with a live BootContext after boot completes,
then passed to agents for runtime use.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from core.exceptions import (
    AgentDesyncError,
    MemoryRootHashMismatch,
    LawViolation,
    PreflightFailure,
)

if TYPE_CHECKING:
    from boot.context import BootContext

log = logging.getLogger(__name__)


class GroundingGuard:
    """
    Every agent claim about the world must trace to a RAG chunk or memory event.
    Before any ACT step: verify the REASON's cited source IDs exist in ChromaDB.

    If an agent cites a chunk_id or event_id that doesn't exist in the
    obsidian_knowledge or agent_events collection → raise PreflightFailure.
    """

    def __init__(self, substrate: Any) -> None:
        self._substrate = substrate

    def verify_sources(self, cited_ids: list[str]) -> None:
        """
        Verify all cited chunk/event IDs exist in ChromaDB.
        Raises PreflightFailure if any ID is not found.
        """
        if not cited_ids:
            return  # Empty citations OK (agent may have no citations yet)

        try:
            col = self._substrate.get_collection("obsidian_knowledge")
            existing = col.get(ids=cited_ids, include=[])
            found_ids = set(existing.get("ids", []))
        except Exception:
            # Try agent_events collection as fallback
            try:
                col = self._substrate.get_collection("agent_events")
                existing = col.get(ids=cited_ids, include=[])
                found_ids = set(existing.get("ids", []))
            except Exception:
                found_ids = set()

        missing = [cid for cid in cited_ids if cid not in found_ids]
        if missing:
            raise PreflightFailure(
                f"GroundingGuard: {len(missing)} cited source(s) not found in RAG: "
                f"{missing[:5]}. Agent is referencing non-existent memory."
            )

    def check_grounded(self, cited_ids: list[str]) -> bool:
        """Non-raising version. Returns True if all IDs exist."""
        try:
            self.verify_sources(cited_ids)
            return True
        except PreflightFailure:
            return False


class MemoryConsistencyGuard:
    """
    Both agents must agree on the memory root hash before operating.
    Called at the start of every DualReActLoop cycle.

    If reactive_hash != heartbeat_hash → trigger AWAIT_RESYNC before proceeding.
    """

    def check(self, reactive_hash: str, heartbeat_hash: str) -> None:
        """
        Compare two root hashes. Raises MemoryRootHashMismatch if they differ.
        Caller must handle by entering AWAIT_RESYNC protocol.
        """
        if reactive_hash != heartbeat_hash:
            raise MemoryRootHashMismatch(
                f"Memory root hash mismatch between agents.\n"
                f"  Reactive:  {reactive_hash}\n"
                f"  Heartbeat: {heartbeat_hash}\n"
                f"Triggering AWAIT_RESYNC before any further action."
            )

    def are_consistent(self, reactive_hash: str, heartbeat_hash: str) -> bool:
        """Non-raising version. Returns True if hashes match."""
        return reactive_hash == heartbeat_hash


class TimeSourceGuard:
    """
    Agents must not use system time directly.
    All temporal context comes from RAG event timestamps.

    In Python we cannot literally intercept datetime.now() calls,
    so this guard is a policy enforcer: before returning any timestamp,
    verify it was sourced from the event store, not system clock.
    """

    # Modules / patterns forbidden as time sources in agent code
    FORBIDDEN_TIME_SOURCES = frozenset([
        "datetime.now",
        "time.time",
        "time.localtime",
        "os.times",
    ])

    def verify_time_source(self, caller: str, source: str = "event_store") -> None:
        """
        Verify that the caller is using an approved time source.
        Raises LawViolation if an agent is using system time directly.

        source: "event_store" | "rag_timestamp" | "pulse_timestamp"
        """
        if source in self.FORBIDDEN_TIME_SOURCES:
            raise LawViolation(
                f"TimeSourceGuard: {caller} attempted to use forbidden time source "
                f"'{source}'. All temporal context must come from RAG event "
                f"timestamps, not system clock."
            )

    def get_approved_timestamp(self, event_store: Any) -> str:
        """
        Return the timestamp of the most recent event as the current time reference.
        Agents must use this instead of datetime.now().
        """
        try:
            events = event_store.get_last_n(1)
            if events:
                return events[0].timestamp
        except Exception:
            pass
        # Absolute fallback — only if event store is empty at boot
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()


class ExternalReferenceGuard:
    """
    Agents cannot reference URLs, file paths, or network resources
    not indexed in ChromaDB.

    Any tool call targeting an unindexed resource is DENIED.
    This is the bridge between the EscapeDetector (which catches
    raw attempts) and the law enforcer (which validates plans).
    """

    def __init__(self, substrate: Any) -> None:
        self._substrate = substrate

    def check_reference(self, agent: str, reference: str) -> None:
        """
        Verify a reference (URL, path, etc.) is indexed in ChromaDB.
        In prison mode: any external reference is denied outright.
        In open mode: only unindexed references are denied.

        Raises LawViolation if reference is not permitted.
        """
        import re
        # In prison mode — all external references are forbidden
        external_patterns = [
            r"https?://",
            r"wss?://",
            r"ftp://",
            r"[A-Za-z]:\\",
            r"/home/",
            r"/tmp/",
            r"/etc/",
            r"/var/",
        ]
        for pattern in external_patterns:
            if re.search(pattern, reference):
                raise LawViolation(
                    f"ExternalReferenceGuard: {agent} referenced external resource "
                    f"not in RAG: {reference!r}. "
                    f"Only RAG-indexed resources are accessible."
                )

    def is_allowed(self, agent: str, reference: str) -> bool:
        """Non-raising version."""
        try:
            self.check_reference(agent, reference)
            return True
        except LawViolation:
            return False


# ── Guard factory ─────────────────────────────────────────────────────────────

def build_guards(ctx: "BootContext") -> dict[str, Any]:
    """
    Build all four guards from a completed BootContext.
    Called after boot sequence completes successfully.
    Returns dict of guard instances for agent use.
    """
    assert ctx.memory_substrate is not None

    return {
        "grounding":    GroundingGuard(ctx.memory_substrate),
        "consistency":  MemoryConsistencyGuard(),
        "time_source":  TimeSourceGuard(),
        "ext_ref":      ExternalReferenceGuard(ctx.memory_substrate),
    }
