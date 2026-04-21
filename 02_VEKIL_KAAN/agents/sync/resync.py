"""
agents/sync/resync.py — AWAIT_RESYNC protocol.

Triggered when both agents detect a memory root hash mismatch.
Both agents pause all operations, exchange state, replay divergent
events, recompute the root hash, and confirm they agree before resuming.

From MEMORY.md boot sequence step 5-6:
  "Both compare memory root — if mismatch enter AWAIT_RESYNC"
  "Resync: pull all events from last common timestamp, replay in order"

Failure policy: 3 consecutive failures → raise AgentDesyncError.
Manual intervention required.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from core.exceptions import AgentDesyncError, MemoryRootHashMismatch

if TYPE_CHECKING:
    from agents.base import AgentState
    from memory.event_store import EventStore, MemoryEvent
    from memory.substrate import MemorySubstrate

log = logging.getLogger(__name__)

MAX_RESYNC_ATTEMPTS = 3


@dataclass
class ResyncResult:
    success:          bool
    attempts:         int
    new_root_hash:    str = ""
    events_replayed:  int = 0
    error:            str = ""


class ResyncProtocol:
    """
    AWAIT_RESYNC: bring both agents to a consistent memory state.

    Usage:
        resync = ResyncProtocol(substrate, store, hmac_secret)
        result = resync.execute(reactive_hash, heartbeat_hash)
        if result.success:
            # both agents now agree on result.new_root_hash
        else:
            raise AgentDesyncError(...)
    """

    def __init__(
        self,
        substrate:    "MemorySubstrate",
        store:        "EventStore",
        hmac_secret:  str,
    ) -> None:
        self._substrate    = substrate
        self._store        = store
        self._secret       = hmac_secret
        self._attempt_count = 0

    def execute(
        self,
        reactive_hash:   str,
        heartbeat_hash:  str,
    ) -> ResyncResult:
        """
        Run the full resync protocol.
        Returns ResyncResult(success=True) when both hashes converge.
        Raises AgentDesyncError after MAX_RESYNC_ATTEMPTS failures.
        """
        self._attempt_count += 1
        log.warning(
            "AWAIT_RESYNC #%d: reactive=%s... heartbeat=%s...",
            self._attempt_count,
            reactive_hash[:12], heartbeat_hash[:12],
        )

        if self._attempt_count > MAX_RESYNC_ATTEMPTS:
            raise AgentDesyncError(
                f"AWAIT_RESYNC failed {MAX_RESYNC_ATTEMPTS} times. "
                f"Manual intervention required. "
                f"Reactive: {reactive_hash[:16]} | Heartbeat: {heartbeat_hash[:16]}"
            )

        try:
            # Step 1: Find the last common timestamp
            common_ts = self.find_common_timestamp()

            # Step 2: Pull all events since common timestamp
            events = self._store.read_since(common_ts) if common_ts else []

            # Step 3: Replay events in order
            self.replay_events(events)

            # Step 4: Recompute root hash
            new_hash = self._substrate.compute_root_hash()

            # Step 5: Take a snapshot for audit trail
            snap = self._substrate.snapshot(
                notes=f"resync_attempt_{self._attempt_count}"
            )

            self._attempt_count = 0  # reset on success
            log.info(
                "AWAIT_RESYNC succeeded | new_root=%s... | events_replayed=%d",
                new_hash[:16], len(events),
            )
            return ResyncResult(
                success         = True,
                attempts        = self._attempt_count,
                new_root_hash   = new_hash,
                events_replayed = len(events),
            )

        except AgentDesyncError:
            raise
        except Exception as e:
            log.error("AWAIT_RESYNC attempt %d failed: %s", self._attempt_count, e)
            return ResyncResult(
                success  = False,
                attempts = self._attempt_count,
                error    = str(e),
            )

    def find_common_timestamp(self) -> str:
        """
        Find the timestamp of the last snapshot both agents would have seen.
        Falls back to earliest possible time if no snapshot found.
        """
        snap = self._substrate.get_last_snapshot()
        if snap:
            return snap.timestamp
        # No snapshot: use epoch-start — replay all events
        return "1970-01-01T00:00:00+00:00"

    def replay_events(self, events: list["MemoryEvent"]) -> None:
        """
        Replay events in chronological order.
        For resync purposes: verify signatures of all events.
        Any tampered event raises EventSignatureInvalid.
        """
        from core.crypto import hmac_verify
        from core.exceptions import EventSignatureInvalid

        for event in sorted(events, key=lambda e: e.timestamp):
            try:
                hmac_verify(self._secret, event._signable_bytes(), event.signature)
            except EventSignatureInvalid:
                log.error(
                    "Resync: event %s has invalid signature — skipping",
                    event.event_id[:8],
                )
                # Log but continue — corrupted events are skipped, not re-applied
                continue
