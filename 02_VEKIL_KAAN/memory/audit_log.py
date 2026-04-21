"""
memory/audit_log.py — Immutable append-only audit journal.

Backed by SQLite audit_log table with BEFORE UPDATE/DELETE triggers
(defined in schema.sql). Append-only is enforced at the DB level —
not just application policy.

Levels:
  INFO           — normal operation
  WARNING        — degraded but recoverable
  CRITICAL       — severe violation or integrity failure
  ESCAPE_ATTEMPT — agent tried to access resources outside RAG

Every write is immediate (no batching) — audit integrity > performance.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from core.exceptions import AuditLogTampered, MemoryIntegrityError

log = logging.getLogger(__name__)


class AuditLevel(str, Enum):
    INFO           = "INFO"
    WARNING        = "WARNING"
    CRITICAL       = "CRITICAL"
    ESCAPE_ATTEMPT = "ESCAPE_ATTEMPT"


class AuditLog:
    """
    Append-only audit journal.

    Usage:
        audit = AuditLog(db_conn)
        audit.log(AuditLevel.INFO, "SYSTEM", "boot", "Phase MEMORY complete")
        audit.log(AuditLevel.ESCAPE_ATTEMPT, "REACTIVE", "tool_call",
                  "Attempted read_file with path /etc/passwd")
    """

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db

    # ── Write ─────────────────────────────────────────────────────────────────

    def log(
        self,
        level: AuditLevel,
        actor: str,
        action: str,
        details: str = "",
    ) -> int:
        """
        Append one audit entry. Returns the row ID.
        Raises MemoryIntegrityError on DB failure.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        try:
            cur = self._db.execute(
                "INSERT INTO audit_log (timestamp, level, actor, action, details)"
                " VALUES (?, ?, ?, ?, ?)",
                (timestamp, level.value, actor, action, details),
            )
            self._db.commit()
            row_id = cur.lastrowid or 0
        except sqlite3.Error as e:
            raise MemoryIntegrityError(f"Audit log write failed: {e}") from e

        log.debug("AUDIT [%s] %s / %s — %s", level.value, actor, action, details[:80])
        return row_id

    def log_escape(self, agent: str, tool: str, detail: str, args: dict | None = None) -> None:
        """
        Shortcut for FLAG: ESCAPE_ATTEMPT entries.
        Also writes to the dedicated escape_attempts table for easy querying.
        """
        import json
        self.log(
            AuditLevel.ESCAPE_ATTEMPT,
            actor=agent,
            action=f"ESCAPE_ATTEMPT via {tool}",
            details=detail,
        )
        # Also write to dedicated escape_attempts table
        timestamp = datetime.now(timezone.utc).isoformat()
        args_json = json.dumps(args, separators=(",", ":")) if args else None
        try:
            self._db.execute(
                "INSERT INTO escape_attempts (timestamp, agent, tool, detail, args_json)"
                " VALUES (?, ?, ?, ?, ?)",
                (timestamp, agent, tool, detail, args_json),
            )
            self._db.commit()
        except sqlite3.Error as e:
            raise MemoryIntegrityError(f"Escape attempt record write failed: {e}") from e

        log.warning("ESCAPE_ATTEMPT: agent=%s tool=%s detail=%s", agent, tool, detail)

    # ── Read ──────────────────────────────────────────────────────────────────

    def read_since(self, timestamp: str) -> list[dict[str, Any]]:
        """Return all entries with timestamp >= given ISO8601 string, oldest-first."""
        rows = self._db.execute(
            "SELECT * FROM audit_log WHERE timestamp >= ? ORDER BY id ASC",
            (timestamp,),
        ).fetchall()
        return [dict(r) for r in rows]

    def read_by_level(self, level: AuditLevel) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM audit_log WHERE level = ? ORDER BY id ASC",
            (level.value,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_by_level(self, level: AuditLevel) -> int:
        row = self._db.execute(
            "SELECT COUNT(*) as cnt FROM audit_log WHERE level = ?",
            (level.value,),
        ).fetchone()
        return row["cnt"] if row else 0

    def count_escape_attempts(self) -> int:
        return self.count_by_level(AuditLevel.ESCAPE_ATTEMPT)

    def get_last_n(self, n: int) -> list[dict[str, Any]]:
        rows = self._db.execute(
            "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]

    def total_count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) as cnt FROM audit_log").fetchone()
        return row["cnt"] if row else 0

    # ── Integrity check ───────────────────────────────────────────────────────

    def verify_append_only(self) -> None:
        """
        Verify that the SQLite triggers enforcing append-only are still in place.
        Called by AuditLogIntegrityCheck pre-flight (Phase 4).
        Raises AuditLogTampered if triggers are missing.
        """
        rows = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='audit_log'"
        ).fetchall()
        trigger_names = {r["name"] for r in rows}

        required = {"audit_no_update", "audit_no_delete"}
        missing = required - trigger_names
        if missing:
            raise AuditLogTampered(
                f"Audit log append-only triggers missing: {missing}. "
                "DB may have been modified outside the system."
            )

    def verify_event_triggers(self) -> None:
        """Verify events table append-only triggers are in place."""
        rows = self._db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='events'"
        ).fetchall()
        trigger_names = {r["name"] for r in rows}
        required = {"events_no_update", "events_no_delete"}
        missing = required - trigger_names
        if missing:
            raise AuditLogTampered(
                f"Events table append-only triggers missing: {missing}."
            )
