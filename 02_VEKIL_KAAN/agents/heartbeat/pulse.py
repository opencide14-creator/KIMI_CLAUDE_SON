"""
agents/heartbeat/pulse.py — Pulse format, emission, and validation.

PULSE_H: Heartbeat → Reactive (every 15 seconds)
PULSE_R: Reactive → Heartbeat (every 5 actions)

Both pulses are written to the event store and the ChromaDB agent_events collection.
Format is spec-compliant with HEARTBEAT.md PULSE FORMAT section.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class PulseH:
    """PULSE_H: Heartbeat → Reactive. Emitted every 15 seconds."""
    protocol:            str = "HEARTBEAT/v1"
    from_:               str = "HEARTBEAT"
    to:                  str = "REACTIVE"
    memory_root_hash:    str = ""
    soul_version:        str = ""    # SHA-256 of concatenated soul law hashes
    last_verified_event: str = ""    # event_id of last successfully verified event
    timestamp:           str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    alive:               bool = True

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol":            self.protocol,
            "from":                self.from_,
            "to":                  self.to,
            "memory_root_hash":    self.memory_root_hash,
            "soul_version":        self.soul_version,
            "last_verified_event": self.last_verified_event,
            "timestamp":           self.timestamp,
            "alive":               self.alive,
        }

    def to_event_payload(self) -> dict[str, Any]:
        """For writing to EventStore."""
        return self.to_payload()


@dataclass
class PulseR:
    """PULSE_R: Reactive → Heartbeat. Emitted every 5 actions."""
    protocol:         str = "HEARTBEAT/v1"
    from_:            str = "REACTIVE"
    to:               str = "HEARTBEAT"
    last_action_hash: str = ""    # SHA-256 of last tool call args
    tool_result_hash: str = ""    # SHA-256 of last tool result
    action_count:     int = 0     # total actions since boot
    timestamp:        str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_payload(self) -> dict[str, Any]:
        return {
            "protocol":         self.protocol,
            "from":             self.from_,
            "to":               self.to,
            "last_action_hash": self.last_action_hash,
            "tool_result_hash": self.tool_result_hash,
            "action_count":     self.action_count,
            "timestamp":        self.timestamp,
        }


def compute_soul_version(soul_laws: list[Any]) -> str:
    """
    Compute a stable hash representing the current soul law state.
    Changes if any soul law content is modified.
    """
    from core.hashing import sha256_hex
    combined = "|".join(sorted(l.hash for l in soul_laws))
    return sha256_hex(combined.encode("utf-8"))
