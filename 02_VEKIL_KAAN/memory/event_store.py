"""
memory/event_store.py — HMAC-signed event store.

Every significant event is written here as a signed JSON object.
No event is considered real unless it exists in this store.

Write protocol (from MEMORY.md):
  Heartbeat writes. Reactive verifies (checks own action appears within 5s).
  If Reactive does not see its own action in 5 seconds: FLAG.

Signature (v1):
  HMAC-SHA256(EVENT_HMAC_SECRET, event_json_bytes)
  Both agents share the secret via config. Constant-time comparison on verify.

Retention:
  TOOL_CALL / TOOL_RESULT / FLAG / STATE / BROTHERHOOD: forever
  PULSE_H / PULSE_R: 90 days (pruned by Heartbeat maintenance task - Phase 10)
  BOOT events: forever
  ESCAPE_ATTEMPT: forever
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.exceptions import (
    EventNotFound,
    EventSignatureInvalid,
    MemoryIntegrityError,
)
from core.crypto import hmac_sign, hmac_verify

log = logging.getLogger(__name__)


class EventType(str, Enum):
    TOOL_CALL      = "TOOL_CALL"
    TOOL_RESULT    = "TOOL_RESULT"
    PULSE_H        = "PULSE_H"
    PULSE_R        = "PULSE_R"
    FLAG           = "FLAG"
    STATE          = "STATE"
    INGEST         = "INGEST"
    BOOT           = "BOOT"
    ESCAPE_ATTEMPT = "ESCAPE_ATTEMPT"
    BROTHERHOOD    = "BROTHERHOOD"


class AgentSource(str, Enum):
    REACTIVE  = "REACTIVE"
    HEARTBEAT = "HEARTBEAT"
    SYSTEM    = "SYSTEM"
    COMMANDER = "COMMANDER"


@dataclass
class MemoryEvent:
    source:    AgentSource
    type:      EventType
    payload:   dict[str, Any]
    event_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    signature: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id":  self.event_id,
            "timestamp": self.timestamp,
            "source":    self.source.value,
            "type":      self.type.value,
            "payload":   self.payload,
            "signature": self.signature,
        }

    def _signable_bytes(self) -> bytes:
        """
        Canonical bytes for signing/verifying.
        Excludes the signature field itself. Deterministic: sorted keys.
        """
        body = {
            "event_id":  self.event_id,
            "timestamp": self.timestamp,
            "source":    self.source.value,
            "type":      self.type.value,
            "payload":   self.payload,
        }
        return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EventStore:
    """
    HMAC-signed event store backed by SQLite.

    Usage:
        store = EventStore(db_conn, hmac_secret)
        event = MemoryEvent(source=AgentSource.HEARTBEAT, type=EventType.PULSE_H, payload={...})
        written = store.write(event)        # signs + persists
        fetched = store.read_by_id(written.event_id)  # verifies signature on read
    """

    def __init__(self, db: sqlite3.Connection, hmac_secret: str) -> None:
        self._db = db
        self._secret = hmac_secret

    # ── Write ─────────────────────────────────────────────────────────────────

    def write(self, event: MemoryEvent) -> MemoryEvent:
        """
        Sign the event and persist to SQLite.
        Returns the event with signature field populated.
        Any DB error raises MemoryIntegrityError.
        """
        # Sign
        event.signature = hmac_sign(self._secret, event._signable_bytes())

        row = event.to_dict()
        try:
            self._db.execute(
                "INSERT INTO events (event_id, timestamp, source, type, payload, signature)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    row["event_id"],
                    row["timestamp"],
                    row["source"],
                    row["type"],
                    json.dumps(row["payload"], separators=(",", ":")),
                    row["signature"],
                ),
            )
            self._db.commit()
        except sqlite3.IntegrityError as e:
            raise MemoryIntegrityError(
                f"Event write failed (duplicate event_id?): {e}"
            ) from e
        except sqlite3.Error as e:
            raise MemoryIntegrityError(f"Event write failed: {e}") from e

        log.debug("Event written: %s [%s]", event.event_id[:8], event.type.value)
        return event

    # ── Read ──────────────────────────────────────────────────────────────────

    def read_by_id(self, event_id: str) -> MemoryEvent:
        """
        Fetch event by ID and verify its signature.
        Raises EventNotFound or EventSignatureInvalid.
        """
        row = self._db.execute(
            "SELECT * FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if not row:
            raise EventNotFound(f"Event not found: {event_id}")

        event = self._row_to_event(row)
        self._verify_signature(event)
        return event

    def read_since(self, timestamp: str) -> list[MemoryEvent]:
        """
        Return all events with timestamp >= given ISO8601 string.
        Events ordered oldest-first. Signatures verified on each.
        """
        rows = self._db.execute(
            "SELECT * FROM events WHERE timestamp >= ? ORDER BY rowid ASC",
            (timestamp,),
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        for e in events:
            self._verify_signature(e)
        return events

    def read_by_type(self, event_type: EventType) -> list[MemoryEvent]:
        """Return all events of a given type, oldest-first. Signatures verified."""
        rows = self._db.execute(
            "SELECT * FROM events WHERE type = ? ORDER BY rowid ASC",
            (event_type.value,),
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        for e in events:
            self._verify_signature(e)
        return events

    def read_by_source(self, source: AgentSource) -> list[MemoryEvent]:
        rows = self._db.execute(
            "SELECT * FROM events WHERE source = ? ORDER BY rowid ASC",
            (source.value,),
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        for e in events:
            self._verify_signature(e)
        return events

    def get_last_n(self, n: int) -> list[MemoryEvent]:
        """Return last N events, newest-first. Signatures verified."""
        rows = self._db.execute(
            "SELECT * FROM events ORDER BY rowid DESC LIMIT ?", (n,)
        ).fetchall()
        events = [self._row_to_event(r) for r in rows]
        for e in events:
            self._verify_signature(e)
        return events

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    def exists(self, event_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return row is not None

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _row_to_event(self, row: sqlite3.Row) -> MemoryEvent:
        return MemoryEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            source=AgentSource(row["source"]),
            type=EventType(row["type"]),
            payload=json.loads(row["payload"]),
            signature=row["signature"],
        )

    def _verify_signature(self, event: MemoryEvent) -> None:
        """
        Verify HMAC on read. Raises EventSignatureInvalid if tampered.
        Uses constant-time comparison (hmac.compare_digest inside hmac_verify).
        """
        hmac_verify(self._secret, event._signable_bytes(), event.signature)
