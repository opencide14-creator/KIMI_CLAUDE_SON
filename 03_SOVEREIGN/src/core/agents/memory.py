"""MEMORY.md — Shared RAG. Heartbeat writes. Reactive consumes.
No action without memory. No memory without audit.
ChromaDB for semantic search. SQLite for events. JSON-lines for audit.
"""
from __future__ import annotations
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.constants import APP_DIR
from src.utils.shutdown import ShutdownProtocol

log = logging.getLogger(__name__)

MEMORY_DIR = APP_DIR / "agents" / "memory"
AUDIT_LOG  = MEMORY_DIR / "audit.jsonl"
SQLITE_DB  = MEMORY_DIR / "rag.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class AgentMemory:
    """
    Shared memory between HeartbeatAgent and ReactiveAgent.
    ChromaDB: semantic similarity search.
    SQLite:   structured event log.
    JSONL:    immutable audit trail.
    """

    def __init__(self):
        MEMORY_DIR.mkdir(parents=True, exist_ok=True)
        self._db    = self._init_sqlite()
        self._chroma = None          # lazy — only init if chromadb installed
        self._chroma_client = None   # ChromaDB client reference for cleanup
        self._ready  = False
        self._root_hash: str = ""
        self._root_hash_valid = False  # Cache validity flag
        self._write_lock = threading.Lock()  # S-15: protect concurrent SQLite writes

    # ── Boot sequence ──────────────────────────────────────────────

    def boot(self) -> bool:
        """Initialize memory subsystem. Returns True if ready."""
        try:
            self._chroma = self._init_chroma()
        except Exception as e:
            log.warning("ChromaDB not available: %s — using SQLite only", e)
            self._chroma = None

        self._root_hash = self._compute_root_hash_uncached()
        self._root_hash_valid = True
        self._ready = True
        self._write_audit("SYSTEM", "MEMORY_BOOT", {"root_hash": self._root_hash})
        log.info("AgentMemory booted. root_hash=%s  chroma=%s",
                 self._root_hash, self._chroma is not None)
        return True

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def root_hash(self) -> str:
        return self._root_hash

    # ── Write (Heartbeat is primary writer) ───────────────────────

    def write_event(self, source: str, event_type: str,
                    payload: Dict[str, Any], priority: str = "HIGH") -> str:
        """Write a structured event. Returns event_id."""
        event_id = str(uuid.uuid4())
        ts        = _now_iso()
        sig       = _sha256(json.dumps(payload, sort_keys=True))

        # SQLite — serialized writes from multiple threads
        with self._write_lock:
            return self._write_event_locked(event_id, ts, source, event_type, priority, payload, sig)

    def _write_event_locked(self, event_id, ts, source, event_type, priority, payload, sig) -> str:
        """Internal write — called with _write_lock held."""
        self._db.execute(
            """INSERT INTO events
               (id, timestamp, source, type, priority, payload_json, signature)
               VALUES (?,?,?,?,?,?,?)""",
            (event_id, ts, source, event_type, priority,
             json.dumps(payload), sig)
        )
        self._db.commit()
        self._write_audit(source, event_type, payload, event_id=event_id)
        if self._chroma:
            try:
                text = f"{source} {event_type} {json.dumps(payload)}"
                self._chroma.add(
                    documents=[text],
                    metadatas=[{"source": source, "type": event_type,
                                "ts": ts, "event_id": event_id}],
                    ids=[event_id],
                )
            except Exception as e:
                log.debug("ChromaDB ingest skipped: %s", e)
        self._invalidate_hash()  # Invalidate cache - hash will recompute on next access
        return event_id

    def write_tool_call(self, agent: str, tool: str,
                        args: dict, result: str) -> str:
        """Convenience: log a complete tool call + result."""
        return self.write_event(
            source=agent,
            event_type="TOOL_CALL",
            payload={"tool": tool, "args": args,
                     "result_preview": result[:200]},
            priority="HIGH",
        )

    def write_pulse(self, source: str, data: dict) -> str:
        return self.write_event(source, "PULSE", data, priority="MEDIUM")

    def write_flag(self, source: str, reason: str, context: dict) -> str:
        return self.write_event(
            source, "FLAG",
            {"reason": reason, "context": context},
            priority="CRITICAL"
        )

    # ── Read (Reactive is primary consumer) ───────────────────────

    def search_semantic(self, query: str, n: int = 5) -> List[Dict]:
        """Semantic similarity search via ChromaDB."""
        if not self._chroma:
            return self.search_text(query, n)
        try:
            result = self._chroma.query(
                query_texts=[query],
                n_results=min(n, 10),
            )
            docs  = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            return [{"text": d, "meta": m} for d, m in zip(docs, metas)]
        except Exception as e:
            log.debug("Chroma search failed: %s", e)
            return self.search_text(query, n)

    def search_text(self, query: str, n: int = 5) -> List[Dict]:
        """SQLite FTS fallback — LIKE search on payload."""
        rows = self._db.execute(
            """SELECT id, timestamp, source, type, payload_json
               FROM events WHERE payload_json LIKE ?
               ORDER BY timestamp DESC LIMIT ?""",
            (f"%{query}%", n)
        ).fetchall()
        return [
            {"text": r[4], "meta": {"event_id": r[0], "ts": r[1],
                                    "source": r[2], "type": r[3]}}
            for r in rows
        ]

    def get_recent(self, n: int = 20, source: str = None) -> List[Dict]:
        """Get N most recent events."""
        if source:
            rows = self._db.execute(
                "SELECT id,timestamp,source,type,payload_json FROM events "
                "WHERE source=? ORDER BY timestamp DESC LIMIT ?",
                (source, n)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT id,timestamp,source,type,payload_json FROM events "
                "ORDER BY timestamp DESC LIMIT ?", (n,)
            ).fetchall()
        return [{"id": r[0], "ts": r[1], "source": r[2],
                 "type": r[3], "payload": json.loads(r[4])}
                for r in rows]

    def get_context_for_reasoning(self, query: str) -> str:
        """Build context string for Reactive Agent's REASON step."""
        results = self.search_semantic(query, n=8)
        if not results:
            results = self.search_text(query, n=8)
        if not results:
            # Last resort: return most recent events
            recent = self.get_recent(n=6)
            if not recent:
                return "(no relevant memory)"
            lines = ["=== RECENT MEMORY ==="]
            for r in recent:
                p = r.get("payload", {})
                lines.append(f"[{r['type']} | {r['ts'][:19]}] {json.dumps(p)[:180]}")
            return "\n".join(lines)
        lines = ["=== RELEVANT MEMORY ==="]
        for r in results:
            meta = r.get("meta", {})
            text = r.get("text", "")
            lines.append(f"[{meta.get('type','?')} | {meta.get('ts','?')[:19]}] {text[:200]}")
        return "\n".join(lines)

    def get_active_context(self, n: int = 15) -> List[Dict]:
        """Last N events — the active session context."""
        return self.get_recent(n)

    # ── Internal ──────────────────────────────────────────────────

    def _init_sqlite(self) -> sqlite3.Connection:
        """Initialize SQLite database with proper settings."""
        db = sqlite3.connect(
            str(SQLITE_DB),
            timeout=30.0,
            check_same_thread=False,
            isolation_level=None  # Autocommit mode - we manage transactions
        )

        # CRITICAL: Enable WAL mode for crash resistance
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")  # Safe with WAL

        # Other optimizations
        db.execute("PRAGMA cache_size=-64000")  # 64MB cache
        db.execute("PRAGMA temp_store=MEMORY")

        # Create schema
        db.execute("""CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            type TEXT NOT NULL,
            priority TEXT DEFAULT 'HIGH',
            payload_json TEXT,
            signature TEXT
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_ts ON events(timestamp)")
        db.execute("CREATE INDEX IF NOT EXISTS idx_type ON events(type)")
        db.commit()
        return db

    def _init_chroma(self):
        import chromadb
        client = chromadb.Client()
        self._chroma_client = client
        return client.get_or_create_collection("sovereign_agents")

    def _write_audit(self, source: str, event_type: str,
                     payload: dict, event_id: str = None):
        entry = {
            "event_id":  event_id or str(uuid.uuid4()),
            "timestamp": _now_iso(),
            "source":    source,
            "type":      event_type,
            "payload":   payload,
        }
        with self._write_lock:
            with open(AUDIT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    # ── Cleanup ───────────────────────────────────────────────────

    async def shutdown_async(self):
        """Async graceful shutdown using ShutdownProtocol."""
        await ShutdownProtocol("AgentMemory", timeout=5.0).shutdown(
            self._chroma_client,
            self._db,
        )

    def close(self):
        """Properly clean up all resources."""
        with self._write_lock:
            # Close ChromaDB client
            if hasattr(self, '_chroma_client') and self._chroma_client:
                try:
                    self._chroma_client.reset()  # Clear collections
                except:
                    pass
                self._chroma_client = None
                self._chroma = None

            # Close SQLite connection
            if hasattr(self, '_db') and self._db:
                try:
                    self._db.close()
                except:
                    pass
                self._db = None

            log.info("AgentMemory closed and resources released")

    def __del__(self):
        """Ensure cleanup on garbage collection."""
        try:
            self.close()
        except:
            pass

    def _compute_root_hash(self) -> str:
        """Cache root hash - only recompute when needed."""
        if not self._root_hash_valid:
            self._root_hash = self._compute_root_hash_uncached()
            self._root_hash_valid = True
        return self._root_hash

    def _compute_root_hash_uncached(self) -> str:
        """Actual root hash computation - queries database."""
        row = self._db.execute(
            "SELECT id FROM events ORDER BY timestamp DESC LIMIT 100"
        ).fetchall()
        content = "|".join(r[0] for r in row)
        return _sha256(content)

    def _invalidate_hash(self):
        """Call this when hash needs recomputation."""
        self._root_hash_valid = False


# Singleton — both agents share the same memory instance
_memory: Optional[AgentMemory] = None


def get_memory() -> AgentMemory:
    global _memory
    if _memory is None:
        _memory = AgentMemory()
    return _memory
