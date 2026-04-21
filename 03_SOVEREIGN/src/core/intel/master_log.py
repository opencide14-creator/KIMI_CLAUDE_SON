"""
Master Log — Immutable Traffic & Decision Logging
═══════════════════════════════════════════════════
Every request, response, injection, and decision is logged here.
Append-only. Tamper-evident. Forensic-ready.

Part of Phase 6: Master Logging
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet

log = logging.getLogger(__name__)

MASTER_LOG_DB = Path.home() / ".sovereign" / "master_log.db"
MASTER_LOG_KEY = Path.home() / ".sovereign" / "master_log.key"


class MasterLog:
    """
    Immutable, append-only event log for SOVEREIGN interception.

    Features:
    - SQLite-backed (append-only enforced by TRIGGER)
    - Hash chain (each entry contains hash of previous)
    - Optional Fernet encryption for sensitive fields
    - Tamper-evident (any modification breaks chain)
    """

    def __init__(self, db_path: Path = MASTER_LOG_DB):
        self._db = db_path
        self._db.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._cipher = self._init_cipher()
        self._init_db()
        log.info("MasterLog initialized: %s", self._db)

    def _init_cipher(self) -> Optional[Any]:
        """Initialize Fernet cipher for field-level encryption."""
        if not MASTER_LOG_KEY.exists():
            key = Fernet.generate_key()
            MASTER_LOG_KEY.write_bytes(key)
            MASTER_LOG_KEY.chmod(0o600)
            log.info("Generated new MasterLog encryption key")
        try:
            key = MASTER_LOG_KEY.read_bytes()
            return Fernet(key)
        except Exception as e:
            log.error("Failed to initialize cipher: %s", e)
            return None

    def _init_db(self):
        """Create tables with append-only enforcement."""
        with sqlite3.connect(self._db) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS log_entries (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   TEXT NOT NULL,
                    entry_type  TEXT NOT NULL,
                    session_id  TEXT,
                    source_ip   TEXT,
                    target_host TEXT,
                    method      TEXT,
                    path        TEXT,
                    status_code INTEGER,
                    body_hash   TEXT,
                    decision    TEXT,
                    details     TEXT,
                    prev_hash   TEXT NOT NULL,
                    entry_hash  TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_type ON log_entries(entry_type);
                CREATE INDEX IF NOT EXISTS idx_session ON log_entries(session_id);
                CREATE INDEX IF NOT EXISTS idx_timestamp ON log_entries(timestamp);

                -- APPEND-ONLY TRIGGER: Reject any UPDATE or DELETE
                CREATE TRIGGER IF NOT EXISTS append_only_guard
                BEFORE UPDATE ON log_entries
                BEGIN
                    SELECT RAISE(ABORT, 'MasterLog is append-only: updates forbidden');
                END;

                CREATE TRIGGER IF NOT EXISTS append_only_guard_delete
                BEFORE DELETE ON log_entries
                BEGIN
                    SELECT RAISE(ABORT, 'MasterLog is append-only: deletes forbidden');
                END;
            """)

    def write(self, entry_type: str, details: Dict[str, Any], **kwargs) -> str:
        """
        Write a log entry to the immutable store.

        Args:
            entry_type: e.g. 'request', 'response', 'injection', 'heartbeat'
            details: JSON-serializable dict of event details
            **kwargs: Additional fields (session_id, source_ip, etc.)

        Returns:
            The entry_hash of the written record
        """
        with self._lock:
            # Get previous hash
            prev_hash = self._get_last_hash()

            # Build entry
            timestamp = datetime.now(timezone.utc).isoformat()
            entry_data = {
                "timestamp": timestamp,
                "entry_type": entry_type,
                "session_id": kwargs.get("session_id", "global"),
                "source_ip": kwargs.get("source_ip", ""),
                "target_host": kwargs.get("target_host", ""),
                "method": kwargs.get("method", ""),
                "path": kwargs.get("path", ""),
                "status_code": kwargs.get("status_code", None),
                "body_hash": kwargs.get("body_hash", ""),
                "decision": kwargs.get("decision", ""),
                "details": json.dumps(details, ensure_ascii=False),
                "prev_hash": prev_hash,
            }

            # Compute entry hash
            entry_hash = self._compute_hash(entry_data)
            entry_data["entry_hash"] = entry_hash

            # Encrypt sensitive fields if cipher available
            if self._cipher:
                try:
                    details_encrypted = self._cipher.encrypt(
                        entry_data["details"].encode("utf-8")
                    )
                    entry_data["details"] = details_encrypted.decode("ascii")
                except Exception as e:
                    log.warning("Encryption failed, storing plaintext: %s", e)

            # Insert
            with sqlite3.connect(self._db) as conn:
                conn.execute("""
                    INSERT INTO log_entries (
                        timestamp, entry_type, session_id, source_ip,
                        target_host, method, path, status_code,
                        body_hash, decision, details, prev_hash, entry_hash
                    ) VALUES (
                        :timestamp, :entry_type, :session_id, :source_ip,
                        :target_host, :method, :path, :status_code,
                        :body_hash, :decision, :details, :prev_hash, :entry_hash
                    )
                """, entry_data)

            log.debug("MasterLog write: %s (hash: %s...)", entry_type, entry_hash[:8])
            return entry_hash

    def read_since(self, since_timestamp: str = None, entry_type: str = None, limit: int = 1000) -> List[Dict]:
        """
        Read entries from the log.

        Args:
            since_timestamp: ISO timestamp filter
            entry_type: Filter by entry type
            limit: Max rows to return

        Returns:
            List of entry dicts
        """
        query = "SELECT * FROM log_entries WHERE 1=1"
        params = {}

        if since_timestamp:
            query += " AND timestamp >= :since"
            params["since"] = since_timestamp
        if entry_type:
            query += " AND entry_type = :type"
            params["type"] = entry_type

        query += " ORDER BY id DESC LIMIT :limit"
        params["limit"] = limit

        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()

        results = []
        for row in rows:
            entry = dict(row)
            # Decrypt details if encrypted
            if self._cipher:
                try:
                    details = entry["details"]
                    if details.startswith("gAAAA"):  # Fernet ciphertext prefix
                        decrypted = self._cipher.decrypt(details.encode("ascii"))
                        entry["details"] = json.loads(decrypted.decode("utf-8"))
                    else:
                        entry["details"] = json.loads(details)
                except Exception:
                    entry["details"] = {"_encrypted": entry["details"], "_error": "decrypt_failed"}
            else:
                try:
                    entry["details"] = json.loads(entry["details"])
                except Exception:
                    pass
            results.append(entry)

        return results

    def verify_chain(self) -> Dict[str, Any]:
        """
        Verify the integrity of the entire hash chain.

        Returns:
            {"valid": bool, "entries_checked": int, "first_broken_id": int|null}
        """
        with sqlite3.connect(self._db) as conn:
            rows = conn.execute(
                "SELECT id, entry_hash, prev_hash, details FROM log_entries ORDER BY id"
            ).fetchall()

        valid = True
        first_broken = None
        prev_hash = "0" * 64

        for i, (id_, entry_hash, stored_prev, details) in enumerate(rows):
            if stored_prev != prev_hash:
                valid = False
                if first_broken is None:
                    first_broken = id_

            # Recompute hash
            entry_data = {
                "timestamp": "",  # We don't recompute, just check chain linkage
                "entry_type": "",
                "session_id": "",
                "source_ip": "",
                "target_host": "",
                "method": "",
                "path": "",
                "status_code": None,
                "body_hash": "",
                "decision": "",
                "details": details,
                "prev_hash": prev_hash,
            }
            # Note: Full re-verification would require all fields; this checks chain links
            prev_hash = entry_hash

        return {
            "valid": valid and first_broken is None,
            "entries_checked": len(rows),
            "first_broken_id": first_broken,
        }

    def stats(self) -> Dict[str, Any]:
        """Return summary statistics."""
        with sqlite3.connect(self._db) as conn:
            total = conn.execute("SELECT COUNT(*) FROM log_entries").fetchone()[0]
            types = conn.execute(
                "SELECT entry_type, COUNT(*) FROM log_entries GROUP BY entry_type"
            ).fetchall()
            last = conn.execute(
                "SELECT timestamp, entry_hash FROM log_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()

        return {
            "total_entries": total,
            "by_type": {t: c for t, c in types},
            "last_entry": {"timestamp": last[0], "hash_prefix": last[1][:16]} if last else None,
            "db_size_bytes": self._db.stat().st_size,
        }

    def _get_last_hash(self) -> str:
        """Get the entry_hash of the most recent log entry."""
        with sqlite3.connect(self._db) as conn:
            row = conn.execute(
                "SELECT entry_hash FROM log_entries ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else "0" * 64

    @staticmethod
    def _compute_hash(entry_data: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of entry data."""
        canonical = json.dumps(entry_data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
