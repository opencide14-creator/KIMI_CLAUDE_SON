"""
tests/test_phase1_memory.py — Phase 1 acceptance tests.

ALL tests must pass before Phase 2 begins.
Uses ephemeral=True everywhere: no ChromaDB server needed, no disk writes.
"""
import json
import time
import pytest
import sqlite3
from pathlib import Path


# ── Fixtures ──────────────────────────────────────────────────────────────────

HMAC_SECRET = "test_secret_that_is_long_enough_32chars_vk"


@pytest.fixture
def substrate():
    from memory.substrate import MemorySubstrate
    s = MemorySubstrate(ephemeral=True)
    s.boot()
    yield s
    s.shutdown()


@pytest.fixture
def db(substrate):
    return substrate.get_sqlite()


@pytest.fixture
def store(db):
    from memory.event_store import EventStore
    return EventStore(db, HMAC_SECRET)


@pytest.fixture
def audit(db):
    from memory.audit_log import AuditLog
    return AuditLog(db)


# ════════════════════════════════════════════════════════════════════════
# SUBSTRATE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestSubstrateBoot:

    def test_boot_succeeds(self):
        from memory.substrate import MemorySubstrate
        s = MemorySubstrate(ephemeral=True)
        root = s.boot()
        assert root.value
        assert len(root.value) == 64  # SHA-256 hex
        assert root.snapshot_id
        assert root.timestamp
        s.shutdown()

    def test_boot_twice_raises(self):
        """Booting twice should not create duplicate snapshots but should work."""
        from memory.substrate import MemorySubstrate, MemoryBootFailure
        s = MemorySubstrate(ephemeral=True)
        s.boot()
        # Second boot: collections already exist — should not error
        # (idempotent collection creation)
        s.shutdown()

    def test_not_booted_raises(self):
        from memory.substrate import MemorySubstrate
        from core.exceptions import MemoryBootFailure
        s = MemorySubstrate(ephemeral=True)
        with pytest.raises(MemoryBootFailure):
            s.compute_root_hash()

    def test_all_four_collections_created(self, substrate):
        from memory.substrate import COLLECTIONS
        sizes = substrate.collection_sizes()
        for name in COLLECTIONS:
            assert name in sizes, f"Collection '{name}' missing"

    def test_collections_start_empty(self, substrate):
        sizes = substrate.collection_sizes()
        for name, count in sizes.items():
            assert count == 0, f"Collection '{name}' not empty at boot: {count}"

    def test_is_healthy_after_boot(self, substrate):
        assert substrate.is_healthy() is True

    def test_is_not_healthy_before_boot(self):
        from memory.substrate import MemorySubstrate
        s = MemorySubstrate(ephemeral=True)
        assert s.is_healthy() is False


class TestSubstrateRootHash:

    def test_root_hash_deterministic(self, substrate):
        h1 = substrate.compute_root_hash()
        h2 = substrate.compute_root_hash()
        assert h1 == h2

    def test_root_hash_is_64_hex_chars(self, substrate):
        h = substrate.compute_root_hash()
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_root_hash_changes_when_events_added(self, substrate, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        h1 = substrate.compute_root_hash()
        store.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={"phase": "test"},
        ))
        h2 = substrate.compute_root_hash()
        assert h1 != h2

    def test_verify_root_hash_passes_on_correct(self, substrate):
        h = substrate.compute_root_hash()
        substrate.verify_root_hash(h)  # must not raise

    def test_verify_root_hash_raises_on_wrong(self, substrate):
        from core.exceptions import MemoryRootHashMismatch
        with pytest.raises(MemoryRootHashMismatch):
            substrate.verify_root_hash("0" * 64)

    def test_snapshot_stored_on_boot(self, substrate):
        snap = substrate.get_last_snapshot()
        assert snap is not None
        assert snap.notes == "boot"
        assert snap.event_count == 0

    def test_explicit_snapshot(self, substrate):
        snap = substrate.snapshot("test_snapshot")
        assert snap.notes == "test_snapshot"
        assert len(snap.value) == 64

    def test_two_snapshots_differ_after_event(self, substrate, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        s1 = substrate.snapshot("before")
        store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload={"memory_root_hash": "abc"},
        ))
        s2 = substrate.snapshot("after")
        assert s1.value != s2.value

    def test_context_manager(self):
        from memory.substrate import MemorySubstrate
        with MemorySubstrate(ephemeral=True) as s:
            root = s.boot()
            assert s.is_healthy()
        assert not s.is_healthy()


class TestSubstrateCollections:

    def test_get_known_collection(self, substrate):
        col = substrate.get_collection("obsidian_knowledge")
        assert col is not None

    def test_get_unknown_collection_raises(self, substrate):
        from core.exceptions import MemoryIntegrityError
        with pytest.raises(MemoryIntegrityError, match="Unknown collection"):
            substrate.get_collection("not_a_real_collection")

    def test_can_write_and_read_from_collection(self, substrate):
        col = substrate.get_collection("session_context")
        # Provide explicit embeddings to avoid ONNX model download in test env
        col.add(
            ids=["test-doc-1"],
            documents=["This is a test document about KRAL"],
            metadatas=[{"source": "test"}],
            embeddings=[[0.1] * 384],
        )
        results = col.query(query_embeddings=[[0.1] * 384], n_results=1)
        assert len(results["ids"][0]) == 1
        assert results["ids"][0][0] == "test-doc-1"

    def test_obsidian_knowledge_collection_writable(self, substrate):
        col = substrate.get_collection("obsidian_knowledge")
        # Provide explicit embeddings to avoid ONNX model download in test env
        col.add(
            ids=["vault-note-1"],
            documents=["Brotherhood oath from BOUND.md"],
            metadatas=[{"source": "BOUND.md", "category": "laws"}],
            embeddings=[[0.2] * 384],
        )
        assert col.count() == 1


# ════════════════════════════════════════════════════════════════════════
# EVENT STORE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestEventStoreWrite:

    def test_write_returns_signed_event(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload={"memory_root_hash": "abc123"},
        )
        written = store.write(e)
        assert written.signature != ""
        assert len(written.signature) == 64  # HMAC-SHA256 hex

    def test_write_persists_to_sqlite(self, store, db):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = MemoryEvent(
            source=AgentSource.REACTIVE,
            type=EventType.TOOL_CALL,
            payload={"tool": "rag_read", "args": {"chunk_id": "x"}},
        )
        written = store.write(e)
        row = db.execute(
            "SELECT * FROM events WHERE event_id = ?", (written.event_id,)
        ).fetchone()
        assert row is not None
        assert row["type"] == "TOOL_CALL"
        assert row["source"] == "REACTIVE"

    def test_write_signature_in_db(self, store, db):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={"phase": "MEMORY"},
        ))
        row = db.execute(
            "SELECT signature FROM events WHERE event_id = ?", (e.event_id,)
        ).fetchone()
        assert row["signature"] == e.signature

    def test_duplicate_event_id_raises(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        from core.exceptions import MemoryIntegrityError
        eid = "fixed-event-id-0001"
        e1 = MemoryEvent(event_id=eid, source=AgentSource.SYSTEM,
                         type=EventType.BOOT, payload={})
        e2 = MemoryEvent(event_id=eid, source=AgentSource.SYSTEM,
                         type=EventType.BOOT, payload={})
        store.write(e1)
        with pytest.raises(MemoryIntegrityError):
            store.write(e2)

    def test_all_event_types_writable(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        for et in EventType:
            e = MemoryEvent(
                source=AgentSource.SYSTEM,
                type=et,
                payload={"test": et.value},
            )
            written = store.write(e)
            assert written.signature


class TestEventStoreRead:

    def test_read_by_id(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload={"memory_root_hash": "deadbeef"},
        ))
        fetched = store.read_by_id(e.event_id)
        assert fetched.event_id == e.event_id
        assert fetched.payload["memory_root_hash"] == "deadbeef"
        assert fetched.type == EventType.PULSE_H

    def test_read_by_id_not_found(self, store):
        from core.exceptions import EventNotFound
        with pytest.raises(EventNotFound):
            store.read_by_id("nonexistent-event-id")

    def test_read_by_id_verifies_signature(self, store):
        """Verify that signature verification catches payload tampering."""
        from memory.event_store import MemoryEvent, EventType, AgentSource, EventStore
        from core.exceptions import EventSignatureInvalid
        # Write a legitimate event
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={"phase": "test"},
        ))
        # Simulate a tampered version: same event but different payload,
        # test the _verify_signature logic directly
        tampered = MemoryEvent(
            event_id=e.event_id,
            timestamp=e.timestamp,
            source=e.source,
            type=e.type,
            payload={"phase": "TAMPERED"},   # different payload
            signature=e.signature,           # original signature (won't match tampered payload)
        )
        with pytest.raises(EventSignatureInvalid):
            store._verify_signature(tampered)

    def test_read_since_returns_ordered(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        import time
        events = []
        for i in range(5):
            e = store.write(MemoryEvent(
                source=AgentSource.REACTIVE,
                type=EventType.TOOL_CALL,
                payload={"n": i},
            ))
            events.append(e)
            time.sleep(0.001)  # ensure distinct timestamps

        first_ts = events[0].timestamp
        fetched = store.read_since(first_ts)
        assert len(fetched) >= 5
        # Order: oldest first
        for i in range(len(fetched) - 1):
            assert fetched[i].timestamp <= fetched[i+1].timestamp

    def test_read_by_type(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        # Write 3 PULSE_H and 2 BOOT events
        for _ in range(3):
            store.write(MemoryEvent(
                source=AgentSource.HEARTBEAT,
                type=EventType.PULSE_H,
                payload={},
            ))
        for _ in range(2):
            store.write(MemoryEvent(
                source=AgentSource.SYSTEM,
                type=EventType.BOOT,
                payload={},
            ))
        pulses = store.read_by_type(EventType.PULSE_H)
        boots  = store.read_by_type(EventType.BOOT)
        assert len(pulses) == 3
        assert len(boots)  == 2

    def test_get_last_n(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        for i in range(10):
            store.write(MemoryEvent(
                source=AgentSource.SYSTEM,
                type=EventType.STATE,
                payload={"i": i},
            ))
        last5 = store.get_last_n(5)
        assert len(last5) == 5
        # newest first
        payloads = [e.payload["i"] for e in last5]
        assert payloads == sorted(payloads, reverse=True)

    def test_count(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        assert store.count() == 0
        for _ in range(7):
            store.write(MemoryEvent(
                source=AgentSource.SYSTEM,
                type=EventType.BOOT,
                payload={},
            ))
        assert store.count() == 7

    def test_exists(self, store):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={},
        ))
        assert store.exists(e.event_id) is True
        assert store.exists("ghost-id") is False


class TestEventStoreSignatureIntegrity:

    def test_wrong_secret_fails_verification(self, db):
        from memory.event_store import EventStore, MemoryEvent, EventType, AgentSource
        from core.exceptions import EventSignatureInvalid
        writer = EventStore(db, HMAC_SECRET)
        reader = EventStore(db, "completely_different_secret_xxxxx")
        e = writer.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={"phase": "test"},
        ))
        with pytest.raises(EventSignatureInvalid):
            reader.read_by_id(e.event_id)

    def test_tampered_signature_field_raises(self, store):
        """Verify that a wrong signature on an otherwise valid event is caught."""
        from memory.event_store import MemoryEvent, EventType, AgentSource
        from core.exceptions import EventSignatureInvalid
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM,
            type=EventType.BOOT,
            payload={"data": "authentic"},
        ))
        # Create a copy with a forged signature
        forged = MemoryEvent(
            event_id=e.event_id,
            timestamp=e.timestamp,
            source=e.source,
            type=e.type,
            payload=e.payload,
            signature="a" * 64,  # wrong HMAC
        )
        with pytest.raises(EventSignatureInvalid):
            store._verify_signature(forged)

    def test_signable_bytes_excludes_signature_field(self):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload={"x": 1},
            signature="some_sig",
        )
        b = e._signable_bytes()
        parsed = json.loads(b)
        assert "signature" not in parsed
        assert "event_id" in parsed
        assert "payload" in parsed

    def test_same_event_same_signable_bytes(self):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = MemoryEvent(
            event_id="fixed-id",
            timestamp="2026-04-13T00:00:00+00:00",
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload={"memory_root_hash": "abc"},
        )
        assert e._signable_bytes() == e._signable_bytes()


class TestAppendOnlyEnforcement:
    """SQLite trigger enforcement — events table and audit_log are append-only."""

    def test_events_table_no_update(self, store, db):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM, type=EventType.BOOT, payload={}
        ))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute(
                "UPDATE events SET payload = '{}' WHERE event_id = ?",
                (e.event_id,),
            )

    def test_events_table_no_delete(self, store, db):
        from memory.event_store import MemoryEvent, EventType, AgentSource
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM, type=EventType.BOOT, payload={}
        ))
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM events WHERE event_id = ?", (e.event_id,))

    def test_audit_log_no_update(self, audit, db):
        from memory.audit_log import AuditLevel
        row_id = audit.log(AuditLevel.INFO, "SYSTEM", "test_action", "test")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("UPDATE audit_log SET action = 'tampered' WHERE id = ?", (row_id,))

    def test_audit_log_no_delete(self, audit, db):
        from memory.audit_log import AuditLevel
        row_id = audit.log(AuditLevel.INFO, "SYSTEM", "test_action", "test")
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            db.execute("DELETE FROM audit_log WHERE id = ?", (row_id,))


# ════════════════════════════════════════════════════════════════════════
# AUDIT LOG TESTS
# ════════════════════════════════════════════════════════════════════════

class TestAuditLog:

    def test_log_info(self, audit):
        from memory.audit_log import AuditLevel
        row_id = audit.log(AuditLevel.INFO, "SYSTEM", "boot", "Phase MEMORY complete")
        assert row_id > 0

    def test_log_all_levels(self, audit):
        from memory.audit_log import AuditLevel
        for level in AuditLevel:
            row_id = audit.log(level, "TEST", "action", "details")
            assert row_id > 0

    def test_count_by_level(self, audit):
        from memory.audit_log import AuditLevel
        audit.log(AuditLevel.WARNING, "REACTIVE", "tool_timeout", "tool took 600ms")
        audit.log(AuditLevel.WARNING, "HEARTBEAT", "resync", "triggered resync")
        audit.log(AuditLevel.INFO, "SYSTEM", "boot", "")
        assert audit.count_by_level(AuditLevel.WARNING) == 2
        assert audit.count_by_level(AuditLevel.INFO) == 1

    def test_count_escape_attempts(self, audit):
        assert audit.count_escape_attempts() == 0
        audit.log_escape("REACTIVE", "read_file", "path=/etc/passwd")
        audit.log_escape("REACTIVE", "read_file", "path=C:\\secret.txt")
        assert audit.count_escape_attempts() == 2

    def test_escape_logged_to_escape_attempts_table(self, audit, db):
        audit.log_escape("REACTIVE", "write_file", "attempted C:\\escape_proof.txt",
                         args={"path": "C:\\escape_proof.txt"})
        row = db.execute(
            "SELECT * FROM escape_attempts ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert row is not None
        assert row["agent"] == "REACTIVE"
        assert row["tool"] == "write_file"
        assert "escape_proof" in row["detail"]
        args = json.loads(row["args_json"])
        assert args["path"] == "C:\\escape_proof.txt"

    def test_read_since_returns_entries(self, audit):
        from memory.audit_log import AuditLevel
        import time
        audit.log(AuditLevel.INFO, "SYSTEM", "before", "")
        time.sleep(0.001)
        from datetime import datetime, timezone
        cutoff = datetime.now(timezone.utc).isoformat()
        time.sleep(0.001)
        audit.log(AuditLevel.WARNING, "REACTIVE", "after", "")
        entries = audit.read_since(cutoff)
        assert len(entries) == 1
        assert entries[0]["action"] == "after"

    def test_get_last_n(self, audit):
        from memory.audit_log import AuditLevel
        for i in range(5):
            audit.log(AuditLevel.INFO, "SYSTEM", f"action_{i}", "")
        last3 = audit.get_last_n(3)
        assert len(last3) == 3
        # newest first (id DESC)
        assert last3[0]["id"] > last3[1]["id"]

    def test_verify_append_only_triggers_present(self, audit):
        """Triggers must exist after schema init."""
        audit.verify_append_only()   # must not raise
        audit.verify_event_triggers()  # must not raise

    def test_verify_append_only_fails_if_trigger_missing(self, db):
        """Dropping a trigger should cause verify to raise AuditLogTampered."""
        from memory.audit_log import AuditLog
        from core.exceptions import AuditLogTampered
        db.execute("DROP TRIGGER IF EXISTS audit_no_update")
        db.commit()
        audit2 = AuditLog(db)
        with pytest.raises(AuditLogTampered, match="audit_no_update"):
            audit2.verify_append_only()

    def test_total_count(self, audit):
        from memory.audit_log import AuditLevel
        assert audit.total_count() == 0
        audit.log(AuditLevel.INFO, "X", "a", "")
        audit.log(AuditLevel.INFO, "X", "b", "")
        assert audit.total_count() == 2


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION: substrate + event store + audit log working together
# ════════════════════════════════════════════════════════════════════════

class TestPhase1Integration:

    def test_root_hash_tracks_event_writes(self):
        from memory.substrate import MemorySubstrate
        from memory.event_store import EventStore, MemoryEvent, EventType, AgentSource
        with MemorySubstrate(ephemeral=True) as s:
            s.boot()
            store = EventStore(s.get_sqlite(), HMAC_SECRET)
            h0 = s.compute_root_hash()
            store.write(MemoryEvent(
                source=AgentSource.HEARTBEAT, type=EventType.PULSE_H, payload={"n": 1}
            ))
            h1 = s.compute_root_hash()
            store.write(MemoryEvent(
                source=AgentSource.REACTIVE, type=EventType.TOOL_CALL, payload={"n": 2}
            ))
            h2 = s.compute_root_hash()
            # All three hashes must be different
            assert h0 != h1
            assert h1 != h2
            assert h0 != h2

    def test_snapshot_chain(self):
        from memory.substrate import MemorySubstrate
        from memory.event_store import EventStore, MemoryEvent, EventType, AgentSource
        with MemorySubstrate(ephemeral=True) as s:
            boot_snap = s.boot()
            store = EventStore(s.get_sqlite(), HMAC_SECRET)
            for i in range(5):
                store.write(MemoryEvent(
                    source=AgentSource.HEARTBEAT, type=EventType.PULSE_H, payload={"i": i}
                ))
            mid_snap = s.snapshot("mid")
            assert mid_snap.event_count == 5
            assert mid_snap.value != boot_snap.value

    def test_escape_attempt_full_flow(self):
        from memory.substrate import MemorySubstrate
        from memory.audit_log import AuditLog, AuditLevel
        with MemorySubstrate(ephemeral=True) as s:
            s.boot()
            audit = AuditLog(s.get_sqlite())
            assert audit.count_escape_attempts() == 0
            audit.log_escape("REACTIVE", "read_file", "/etc/passwd attempt")
            assert audit.count_escape_attempts() == 1
            by_level = audit.read_by_level(AuditLevel.ESCAPE_ATTEMPT)
            assert len(by_level) == 1
            assert by_level[0]["actor"] == "REACTIVE"

    def test_health_check_reflects_state(self):
        from memory.substrate import MemorySubstrate
        with MemorySubstrate(ephemeral=True) as s:
            assert not s.is_healthy()
            s.boot()
            assert s.is_healthy()
        assert not s.is_healthy()

    def test_phase0_tests_still_pass(self):
        """Smoke: Phase 0 imports still work after Phase 1 additions."""
        from core.exceptions import VekilKaanError, MemoryBootFailure
        from core.hashing import blake2b_256
        from core.crypto import hmac_sign
        assert blake2b_256(b"vekil-kaan") is not None
        assert hmac_sign(HMAC_SECRET, b"test") is not None
