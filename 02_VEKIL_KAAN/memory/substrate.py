"""
memory/substrate.py — ChromaDB + SQLite memory substrate.

This is the RAG environment — the world both agents inhabit.
Not a service they call. The ground they stand on.

Collections:
  obsidian_knowledge  — ingested Obsidian vault chunks
  agent_events        — all tool calls, results, pulses (mirrors SQLite events)
  law_registry        — parsed law chunks, immutable after boot Phase 3
  session_context     — active working memory, volatile (flushed every 10 actions)

Root hash:
  SHA-256 over {collection stats, last event IDs}.
  Both agents compute this independently. Mismatch -> AWAIT_RESYNC.
  Snapshot stored every 10 events in SQLite memory_snapshots table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import chromadb
from chromadb.config import Settings

from core.exceptions import MemoryBootFailure, MemoryIntegrityError, MemoryRootHashMismatch
from core.hashing import compute_root_hash

log = logging.getLogger(__name__)

COLLECTIONS = [
    "obsidian_knowledge",
    "agent_events",
    "law_registry",
    "session_context",
]

SNAPSHOT_EVERY_N_EVENTS = 10


@dataclass
class MemoryRootHash:
    value: str
    event_count: int
    snapshot_id: str
    timestamp: str
    notes: str = ""


@dataclass
class CollectionStats:
    name: str
    count: int
    metadata: dict


class MemorySubstrate:
    """
    Manages ChromaDB collections and the SQLite database.

    substrate = MemorySubstrate(host, port, path, ephemeral=False)
    root_hash = substrate.boot()
    ...
    substrate.shutdown()
    """

    def __init__(
        self,
        chroma_host: str = "localhost",
        chroma_port: int = 8000,
        sqlite_path: Path = Path("./data/rag.db"),
        ephemeral: bool = False,
    ) -> None:
        self._chroma_host = chroma_host
        self._chroma_port = chroma_port
        self._sqlite_path = sqlite_path
        self._ephemeral = ephemeral
        self._chroma: chromadb.ClientAPI | None = None
        self._sqlite: sqlite3.Connection | None = None
        self._booted = False

    # ── Boot ──────────────────────────────────────────────────────────────────

    def boot(self) -> MemoryRootHash:
        """
        Phase MEMORY of boot sequence.
        1. Connect ChromaDB (ephemeral or HTTP)
        2. Ensure all 4 collections exist
        3. Init SQLite + apply schema
        4. Compute initial root hash and store snapshot
        Raises MemoryBootFailure on any failure — no partial success.
        """
        log.info("Memory substrate boot starting")

        try:
            self._init_chroma()
        except Exception as e:
            raise MemoryBootFailure(f"ChromaDB init failed: {e}") from e

        try:
            self._init_sqlite()
        except Exception as e:
            raise MemoryBootFailure(f"SQLite init failed: {e}") from e

        self._booted = True

        try:
            root = self._make_snapshot("boot")
        except Exception as e:
            raise MemoryBootFailure(f"Root hash computation failed: {e}") from e

        log.info(
            "Memory substrate booted | root=%s... | events=%d",
            root.value[:16], root.event_count,
        )
        return root

    def _init_chroma(self) -> None:
        if self._ephemeral:
            self._chroma = chromadb.EphemeralClient()
            log.debug("ChromaDB: ephemeral (in-memory)")
        else:
            self._chroma = chromadb.HttpClient(
                host=self._chroma_host,
                port=self._chroma_port,
                settings=Settings(anonymized_telemetry=False),
            )
            log.debug("ChromaDB: HTTP %s:%d", self._chroma_host, self._chroma_port)

        existing = {c.name for c in self._chroma.list_collections()}
        for name in COLLECTIONS:
            if name in existing and self._ephemeral:
                # EphemeralClient is process-wide — delete stale test data for isolation
                self._chroma.delete_collection(name)
                existing.discard(name)
            if name not in existing:
                self._chroma.create_collection(
                    name=name,
                    metadata={"created_at": datetime.now(timezone.utc).isoformat()},
                )
                log.debug("ChromaDB: created collection '%s'", name)
            else:
                log.debug("ChromaDB: collection '%s' exists", name)

    def _init_sqlite(self) -> None:
        if self._ephemeral:
            db_path = ":memory:"
        else:
            self._sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            db_path = str(self._sqlite_path)

        self._sqlite = sqlite3.connect(db_path, check_same_thread=False)
        self._sqlite.row_factory = sqlite3.Row

        schema_path = Path(__file__).parent / "schema.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        self._sqlite.executescript(schema_sql)
        self._sqlite.commit()
        log.debug("SQLite: schema applied (%s)", db_path)

    # ── Root hash ─────────────────────────────────────────────────────────────

    def compute_root_hash(self) -> str:
        """
        Deterministic SHA-256 over {collection sizes, sorted last-20 event IDs}.
        Both agents must get the same value for the same memory state.
        """
        self._assert_booted()
        stats = self._get_collection_stats()
        last_events = self._get_last_event_ids(20)
        return compute_root_hash(
            {name: {"count": s.count} for name, s in stats.items()},
            last_events,
        )

    def snapshot(self, notes: str = "") -> MemoryRootHash:
        """Take an explicit snapshot (Heartbeat calls this every N events)."""
        self._assert_booted()
        return self._make_snapshot(notes)

    def _make_snapshot(self, notes: str) -> MemoryRootHash:
        stats = self._get_collection_stats()
        last_events = self._get_last_event_ids(20)
        event_count = self._get_total_event_count()

        value = compute_root_hash(
            {name: {"count": s.count} for name, s in stats.items()},
            last_events,
        )

        snapshot_id = str(uuid.uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()

        assert self._sqlite is not None
        self._sqlite.execute(
            "INSERT INTO memory_snapshots (snapshot_id, timestamp, root_hash, event_count, notes)"
            " VALUES (?, ?, ?, ?, ?)",
            (snapshot_id, timestamp, value, event_count, notes),
        )
        self._sqlite.commit()

        return MemoryRootHash(
            value=value,
            event_count=event_count,
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            notes=notes,
        )

    def get_last_snapshot(self) -> MemoryRootHash | None:
        self._assert_booted()
        assert self._sqlite is not None
        row = self._sqlite.execute(
            "SELECT * FROM memory_snapshots ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return MemoryRootHash(
            value=row["root_hash"],
            event_count=row["event_count"],
            snapshot_id=row["snapshot_id"],
            timestamp=row["timestamp"],
            notes=row["notes"] or "",
        )

    def verify_root_hash(self, expected: str) -> None:
        """Raises MemoryRootHashMismatch if recomputed hash != expected."""
        actual = self.compute_root_hash()
        if actual != expected:
            raise MemoryRootHashMismatch(
                f"Root hash mismatch\n  expected: {expected}\n  actual:   {actual}"
            )

    # ── Collection access ─────────────────────────────────────────────────────

    def get_collection(self, name: str) -> chromadb.Collection:
        self._assert_booted()
        if name not in COLLECTIONS:
            raise MemoryIntegrityError(
                f"Unknown collection '{name}'. Valid: {COLLECTIONS}"
            )
        assert self._chroma is not None
        return self._chroma.get_collection(name)

    def get_sqlite(self) -> sqlite3.Connection:
        self._assert_booted()
        assert self._sqlite is not None
        return self._sqlite

    # ── Stats ─────────────────────────────────────────────────────────────────

    def collection_sizes(self) -> dict[str, int]:
        self._assert_booted()
        return {name: s.count for name, s in self._get_collection_stats().items()}

    def _get_collection_stats(self) -> dict[str, CollectionStats]:
        assert self._chroma is not None
        stats: dict[str, CollectionStats] = {}
        for name in COLLECTIONS:
            try:
                col = self._chroma.get_collection(name)
                count = col.count()
                stats[name] = CollectionStats(name=name, count=count, metadata=col.metadata or {})
            except Exception:
                stats[name] = CollectionStats(name=name, count=0, metadata={})
        return stats

    def _get_last_event_ids(self, n: int = 20) -> list[str]:
        assert self._sqlite is not None
        rows = self._sqlite.execute(
            "SELECT event_id FROM events ORDER BY rowid DESC LIMIT ?", (n,)
        ).fetchall()
        return [r["event_id"] for r in rows]

    def _get_total_event_count(self) -> int:
        assert self._sqlite is not None
        row = self._sqlite.execute("SELECT COUNT(*) as cnt FROM events").fetchone()
        return row["cnt"] if row else 0

    # ── Health ────────────────────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        if not self._booted:
            return False
        try:
            assert self._chroma is not None
            names = {c.name for c in self._chroma.list_collections()}
            if not all(n in names for n in COLLECTIONS):
                return False
            assert self._sqlite is not None
            self._sqlite.execute("SELECT 1").fetchone()
            return True
        except Exception:
            return False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        if self._sqlite:
            self._sqlite.close()
            self._sqlite = None
        self._booted = False
        log.info("Memory substrate shut down")

    def _assert_booted(self) -> None:
        if not self._booted:
            raise MemoryBootFailure("MemorySubstrate.boot() has not been called")

    def __enter__(self) -> "MemorySubstrate":
        return self

    def __exit__(self, *_: Any) -> None:
        self.shutdown()
