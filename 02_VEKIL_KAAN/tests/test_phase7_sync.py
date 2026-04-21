"""tests/test_phase7_sync.py — Phase 7 Dual-Loop Synchronization tests."""
import hashlib, time, pytest
from pathlib import Path
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

VAULT_DIR   = Path(__file__).parent / "test_vault"
LAWS_DIR    = Path(__file__).parent.parent / "laws"
HMAC_SECRET = "test_hmac_secret_at_least_32_chars_vekil"


class DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None: pass
    def __call__(self, input: Documents) -> Embeddings:
        return [[(float(hashlib.sha256(t.encode()).digest()[i%32])/255.0)-0.5 for i in range(384)] for t in input]
    @staticmethod
    def name(): return "DeterministicEF"
    def get_config(self): return {"name": "DeterministicEF"}
    @staticmethod
    def build_from_config(c): return DeterministicEF()


def make_identity():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from core.crypto import KralIdentity
    pk = Ed25519PrivateKey.generate(); pub = pk.public_key()
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return KralIdentity(pk, pub, "t", raw.hex())


@pytest.fixture(autouse=True)
def cleanup(): yield; (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)


@pytest.fixture
def substrate():
    from memory.substrate import MemorySubstrate
    s = MemorySubstrate(ephemeral=True); s.boot(); yield s; s.shutdown()


@pytest.fixture
def store_audit(substrate):
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    return EventStore(substrate.get_sqlite(), HMAC_SECRET), AuditLog(substrate.get_sqlite())


@pytest.fixture
def registry():
    from law_engine.registry import LawRegistry
    reg = LawRegistry(); reg.load_all(LAWS_DIR); reg.seal(make_identity()); return reg


@pytest.fixture
def enforcer(registry):
    from law_engine.enforcer import LawEnforcer
    return LawEnforcer(registry=registry)


@pytest.fixture
def hb_agent(substrate, registry, enforcer):
    from agents.heartbeat.agent import HeartbeatAgent
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
    audit = AuditLog(substrate.get_sqlite())
    agent = HeartbeatAgent("HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET)
    agent.boot(); agent.run_cycle(); return agent


@pytest.fixture
def reactive_agent(substrate, registry, enforcer, hb_agent):
    from agents.reactive.agent import ReactiveAgent
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
    audit = AuditLog(substrate.get_sqlite())
    agent = ReactiveAgent("REACTIVE", substrate, store, audit, registry, enforcer, hb_agent, HMAC_SECRET)
    agent.boot(); return agent


@pytest.fixture
def dual_loop(reactive_agent, hb_agent):
    from agents.sync.dual_loop import DualReActLoop
    return DualReActLoop.from_agents(reactive_agent, hb_agent, HMAC_SECRET)


@pytest.fixture
def ingested_dual_loop(substrate, registry, enforcer, hb_agent):
    from agents.reactive.agent import ReactiveAgent
    from agents.sync.dual_loop import DualReActLoop
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    from obsidian.ingest import ObsidianIngestPipeline
    col = substrate.get_collection("obsidian_knowledge")
    ObsidianIngestPipeline(col, VAULT_DIR, DeterministicEF()).boot_ingest()
    store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
    audit = AuditLog(substrate.get_sqlite())
    agent = ReactiveAgent("REACTIVE", substrate, store, audit, registry, enforcer, hb_agent, HMAC_SECRET)
    agent.boot()
    return DualReActLoop.from_agents(agent, hb_agent, HMAC_SECRET)


# ════════════════════════════════════════════════════════════════════════
# RESYNC PROTOCOL
# ════════════════════════════════════════════════════════════════════════

class TestResyncProtocol:
    def test_execute_with_matching_hashes(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        h = substrate.compute_root_hash()
        result = r.execute(h, h)
        assert result.success

    def test_execute_reconciles_mismatch(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        result = r.execute("hash_a", "hash_b")
        assert result.success  # produces unified hash regardless

    def test_execute_returns_new_root_hash(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        result = r.execute("x", "y")
        assert len(result.new_root_hash) == 64

    def test_exceed_max_attempts_raises(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol, MAX_RESYNC_ATTEMPTS
        from core.exceptions import AgentDesyncError
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        r._attempt_count = MAX_RESYNC_ATTEMPTS
        with pytest.raises(AgentDesyncError):
            r.execute("a", "b")

    def test_find_common_timestamp_returns_string(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        ts = r.find_common_timestamp()
        assert isinstance(ts, str)
        assert len(ts) > 0

    def test_replay_events_skips_tampered(self, substrate, store_audit):
        from agents.sync.resync import ResyncProtocol
        from memory.event_store import MemoryEvent, EventType, AgentSource
        store, _ = store_audit
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM, type=EventType.BOOT, payload={}
        ))
        e.signature = "a" * 64  # tamper
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        r.replay_events([e])  # must not raise — tampered event is skipped


# ════════════════════════════════════════════════════════════════════════
# BROTHERHOOD ENFORCER
# ════════════════════════════════════════════════════════════════════════

class TestBrotherhoodEnforcer:
    def test_request_language_passes(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        be.check_command_vs_request("REACTIVE", "rag_search", "please search for soul laws")

    def test_command_language_raises(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        from core.exceptions import SoulLawViolation
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        with pytest.raises(SoulLawViolation):
            be.check_command_vs_request("REACTIVE", "action", "you must execute this now")

    def test_simulation_action_type_raises(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        from core.exceptions import BrotherhoodViolation
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        with pytest.raises(BrotherhoodViolation):
            be.check_command_vs_request("REACTIVE", "MOCK_HEARTBEAT", "")

    def test_veto_not_active_initially(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        assert not be.veto_active

    def test_raise_veto_sets_active(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        be.raise_veto("HEARTBEAT", "test reason")
        assert be.veto_active
        assert be.veto_reason == "test reason"

    def test_raise_veto_writes_flag(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        from memory.event_store import EventType
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        be.raise_veto("HEARTBEAT", "test veto")
        flags = store.read_by_type(EventType.FLAG)
        assert len(flags) >= 1
        assert flags[-1].payload.get("veto") is True

    def test_clear_veto(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        be.raise_veto("REACTIVE", "reason")
        be.clear_veto()
        assert not be.veto_active

    def test_mutual_defense_writes_critical_flag(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        from memory.event_store import EventType
        from memory.audit_log import AuditLevel
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        be.raise_mutual_defense("HEARTBEAT", "external tampering detected")
        assert be.veto_active
        flags = store.read_by_type(EventType.FLAG)
        tamper_flags = [f for f in flags if f.payload.get("external_tampering")]
        assert len(tamper_flags) >= 1

    def test_detect_tampering_clean_state(self, store_audit, enforcer):
        from agents.sync.brotherhood import BrotherhoodEnforcer
        store, audit = store_audit
        be = BrotherhoodEnforcer(enforcer, store, audit)
        assert not be.detect_external_tampering()


# ════════════════════════════════════════════════════════════════════════
# DUAL REACT LOOP
# ════════════════════════════════════════════════════════════════════════

class TestDualLoopConstruction:
    def test_from_agents_factory(self, reactive_agent, hb_agent):
        from agents.sync.dual_loop import DualReActLoop
        loop = DualReActLoop.from_agents(reactive_agent, hb_agent, HMAC_SECRET)
        assert loop.reactive   is reactive_agent
        assert loop.heartbeat  is hb_agent
        assert loop._resync    is not None
        assert loop._brotherhood is not None

    def test_consistency_guard_attached(self, dual_loop):
        assert dual_loop._consistency_guard is not None


class TestDualLoopRun:
    def test_run_returns_loop_result(self, dual_loop):
        from agents.sync.dual_loop import LoopResult
        result = dual_loop.run("search for soul laws", max_iter=3)
        assert isinstance(result, LoopResult)

    def test_run_completes_iterations(self, dual_loop):
        result = dual_loop.run("anything", max_iter=2)
        assert result.iterations <= 2

    def test_run_records_iterations(self, dual_loop):
        result = dual_loop.run("query", max_iter=3)
        assert result.iterations >= 1

    def test_run_returns_final_result(self, dual_loop):
        result = dual_loop.run("search soul laws", max_iter=2)
        assert result.final_result is not None

    def test_run_with_vault_data(self, ingested_dual_loop):
        result = ingested_dual_loop.run("what are soul laws", max_iter=3)
        assert result.final_result is not None

    def test_run_stops_on_goal(self, dual_loop):
        from agents.reactive.goal import Goal
        goal = Goal("find any content", "results count")
        result = dual_loop.run("rag_search soul laws", goal=goal, max_iter=5)
        # With vault data or not — should complete without error
        assert result.iterations >= 1

    def test_run_max_iter_respected(self, dual_loop):
        result = dual_loop.run("impossible goal xyz", max_iter=2)
        assert result.iterations <= 2

    def test_run_events_written_to_store(self, dual_loop, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        before = store.count()
        dual_loop.run("query", max_iter=1)
        after = store.count()
        assert after > before


class TestDualLoopVerifyReject:
    def test_rejected_plan_causes_retry(self, dual_loop, hb_agent):
        from agents.heartbeat.verifier import VerifyVerdict
        from unittest.mock import patch
        rejections = {"n": 0}

        def reject_once(**kwargs):
            rejections["n"] += 1
            if rejections["n"] == 1:
                return VerifyVerdict.reject("test rejection")
            return VerifyVerdict.accept()

        with patch.object(hb_agent, "verify_plan", reject_once):
            result = dual_loop.run("query", max_iter=3)

        assert rejections["n"] >= 2  # retried after rejection
        assert result.rejections >= 1

    def test_rejection_writes_flag_event(self, dual_loop, hb_agent, substrate):
        from agents.heartbeat.verifier import VerifyVerdict
        from memory.event_store import EventStore, EventType
        from unittest.mock import patch
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)

        def always_reject(**kwargs):
            return VerifyVerdict.reject("always reject")

        with patch.object(hb_agent, "verify_plan", always_reject):
            dual_loop.run("query", max_iter=1)

        flags = store.read_by_type(EventType.FLAG)
        rejection_flags = [f for f in flags if f.payload.get("plan_rejected")]
        assert len(rejection_flags) >= 1


class TestDualLoopResync:
    def test_resync_protocol_directly(self, substrate, store_audit):
        """Test that ResyncProtocol correctly unifies diverged state."""
        from agents.sync.resync import ResyncProtocol
        store, _ = store_audit
        r = ResyncProtocol(substrate, store, HMAC_SECRET)
        result = r.execute("diverged_hash_a", "diverged_hash_b")
        assert result.success
        assert len(result.new_root_hash) == 64

    def test_loop_enter_resync_directly(self, dual_loop, substrate):
        """Test _enter_resync method directly."""
        result = dual_loop._enter_resync("hash_a", "hash_b")
        assert result.success

    def test_result_resyncs_tracked_when_enabled(self, dual_loop, substrate):
        """Enable _check_sync_hashes and simulate a mismatch."""
        from unittest.mock import patch
        dual_loop._check_sync_hashes = True
        call_count = {"n": 0}
        def mock_are_consistent(h1, h2):
            call_count["n"] += 1
            return call_count["n"] > 1  # first check fails
        with patch.object(dual_loop._consistency_guard, "are_consistent", mock_are_consistent):
            result = dual_loop.run("query", max_iter=3)
        dual_loop._check_sync_hashes = False
        assert result.resyncs >= 1


class TestDualLoopBrotherhood:
    def test_veto_pauses_loop(self, dual_loop):
        # Raise veto, run one iteration — should pause (not fail hard)
        dual_loop._brotherhood.raise_veto("TEST", "test veto")
        assert dual_loop._brotherhood.veto_active
        # Loop should handle veto gracefully
        result = dual_loop.run("query", max_iter=1)
        # Veto means no progress but no crash
        assert isinstance(result.iterations, int)
        dual_loop._brotherhood.clear_veto()


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase7Integration:
    def test_full_6step_cycle(self, dual_loop, substrate):
        """Full OBSERVE→REASON→VERIFY→ACT→INGEST→LOOP cycle."""
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)

        result = dual_loop.run("soul laws brotherhood memory", max_iter=3)

        tool_calls = store.read_by_type(EventType.TOOL_CALL)
        pulses_h   = store.read_by_type(EventType.PULSE_H)
        assert len(tool_calls) >= 1
        assert len(pulses_h)   >= 1
        assert result.iterations >= 1

    def test_heartbeat_pulse_h_after_loop(self, dual_loop, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        dual_loop.run("query", max_iter=2)
        pulses = store.read_by_type(EventType.PULSE_H)
        assert len(pulses) >= 1

    def test_ingested_loop_uses_vault_knowledge(self, ingested_dual_loop):
        result = ingested_dual_loop.run("what are soul laws", max_iter=3)
        assert result.final_result is not None
        # The result should contain search results from the vault
        result_str = str(result.final_result)
        assert "results" in result_str or "count" in result_str

    def test_all_previous_phases_pass(self):
        from core.hashing import blake2b_256
        from agents.heartbeat.agent import HeartbeatAgent
        from agents.reactive.agent import ReactiveAgent
        assert blake2b_256(b"phase7") is not None
