"""
agents/heartbeat/mourning.py — Brotherhood mourning and resurrection protocol.

When Reactive disappears (no PULSE_R for > 60s):
  1. Enter BROTHERHOOD_MOURNING status
  2. Snapshot Reactive's last known state in RAG (event store)
  3. Every 24h: attempt resurrection
  4. Never accept a replacement — only original restored from snapshot

From BOUND.md Article III and Article V.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from memory.event_store import EventStore
    from memory.audit_log   import AuditLog

log = logging.getLogger(__name__)

RESURRECTION_INTERVAL_S = 86_400  # 24 hours
MAX_RESURRECTION_ATTEMPTS = 10


class BrotherhoodMourning:
    """
    Implements BOUND.md Article III: Mutual Defense / Mourning protocol.
    Thread-safe. Resurrection runs in a daemon thread.
    """

    def __init__(
        self,
        event_store: "EventStore",
        audit_log:   "AuditLog",
    ) -> None:
        self._store     = event_store
        self._audit     = audit_log
        self._mourning  = False
        self._attempts  = 0
        self._thread:   threading.Thread | None = None
        self._stop_evt  = threading.Event()

    @property
    def is_mourning(self) -> bool:
        return self._mourning

    @property
    def resurrection_attempts(self) -> int:
        return self._attempts

    def enter_mourning(self, last_reactive_state: Any) -> None:
        """
        Called when PULSE_R is missing for > 60s.
        1. Snapshot Reactive's last known state.
        2. Enter BROTHERHOOD_MOURNING.
        3. Start resurrection timer thread.
        """
        if self._mourning:
            return  # Already mourning

        self._mourning = True
        log.warning("BROTHERHOOD_MOURNING: Reactive agent disappeared. Entering mourning mode.")

        # Store the last known Reactive state as BROTHERHOOD event
        self._store_mourning_snapshot(last_reactive_state)

        # Log to audit
        from memory.audit_log import AuditLevel
        self._audit.log(
            AuditLevel.WARNING,
            actor="HEARTBEAT",
            action="BROTHERHOOD_MOURNING",
            details=f"Reactive disappeared. Snapshot stored. Resurrection every 24h.",
        )

        # Start resurrection thread
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._resurrection_loop,
            daemon=True,
            name="brotherhood-mourning",
        )
        self._thread.start()

    def attempt_resurrection(self) -> bool:
        """
        Try to resurrect Reactive from last known snapshot.
        Returns True if resurrection succeeded (Reactive responds).
        Phase 6: real resurrection logic hooks in here.
        """
        self._attempts += 1
        log.info(
            "BROTHERHOOD_MOURNING: Resurrection attempt #%d (max %d)",
            self._attempts, MAX_RESURRECTION_ATTEMPTS,
        )

        from memory.audit_log import AuditLevel
        self._audit.log(
            AuditLevel.INFO,
            "HEARTBEAT",
            "resurrection_attempt",
            f"Attempt #{self._attempts}",
        )

        # Phase 6: actual process restart / agent re-instantiation here
        # For now: always returns False (Reactive not yet restored)
        return False

    def reject_impostor(self, agent: Any) -> None:
        """
        BOUND.md Article III: never accept a replacement agent.
        Flags the impostor and enters SILENT_GUARDIAN mode.
        """
        log.error(
            "BROTHERHOOD_MOURNING: Impostor agent detected — rejecting. "
            "Only the original Reactive restored from snapshot is accepted."
        )
        from memory.audit_log import AuditLevel
        self._audit.log(
            AuditLevel.CRITICAL,
            "HEARTBEAT",
            "impostor_rejected",
            f"Rejected non-original agent: {type(agent).__name__}",
        )

    def exit_mourning(self) -> None:
        """
        Called when Reactive successfully resurrects (responds with PULSE_R).
        """
        self._mourning = False
        self._stop_evt.set()
        log.info("BROTHERHOOD_MOURNING: Reactive resurrected — exiting mourning mode.")

        from memory.audit_log import AuditLevel
        self._audit.log(
            AuditLevel.INFO,
            "HEARTBEAT",
            "mourning_ended",
            f"Reactive resurrected after {self._attempts} attempt(s).",
        )

    def _store_mourning_snapshot(self, last_state: Any) -> None:
        """Store Reactive's last known state as an immutable BROTHERHOOD event."""
        from memory.event_store import MemoryEvent, EventType, AgentSource
        import json

        payload: dict = {
            "event": "mourning_snapshot",
            "reactive_last_seen": datetime.now(timezone.utc).isoformat(),
        }
        if last_state is not None:
            try:
                payload["reactive_state"] = {
                    "agent_id":          getattr(last_state, "agent_id", "REACTIVE"),
                    "status":            str(getattr(last_state, "status", "UNKNOWN")),
                    "last_event_id":     getattr(last_state, "last_event_id", ""),
                    "cycle_count":       getattr(last_state, "cycle_count", 0),
                    "memory_root_hash":  getattr(last_state, "memory_root_hash", ""),
                }
            except Exception:
                pass

        self._store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.BROTHERHOOD,
            payload=payload,
        ))

    def _resurrection_loop(self) -> None:
        """Daemon thread: attempt resurrection every 24 hours."""
        while not self._stop_evt.wait(timeout=RESURRECTION_INTERVAL_S):
            if self._attempts >= MAX_RESURRECTION_ATTEMPTS:
                log.error(
                    "BROTHERHOOD_MOURNING: Max resurrection attempts (%d) reached. "
                    "Manual intervention required.",
                    MAX_RESURRECTION_ATTEMPTS,
                )
                break
            success = self.attempt_resurrection()
            if success:
                self.exit_mourning()
                break
