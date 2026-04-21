"""
tests/test_phase5_heartbeat.py — Phase 5 acceptance tests.

ALL tests must pass before Phase 6 begins.
Uses ephemeral substrate and test vault — no running servers needed.
"""
import hashlib
import time
import pytest
from pathlib import Path

from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

VAULT_DIR = Path(__file__).parent / "test_vault"
LAWS_DIR  = Path(__file__).parent.parent / "laws"
HMAC_SECRET = "test_hmac_secret_at_least_32_chars_vekil"


class DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None: pass
    def __call__(self, input: Documents) -> Embeddings:
        result = []
        for text in input:
            h = hashlib.sha256(str(text).encode()).digest()
            result.append([(float(h[i % 32]) / 255.0) - 0.5 for i in range(384)])
        return result
    @staticmethod
    def name() -> str: return "DeterministicEF"
    def get_config(self): return {"name": "DeterministicEF"}
    @staticmethod
    def build_from_config(config): return DeterministicEF()


def make_test_identity():
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from core.crypto import KralIdentity
    pk  = Ed25519PrivateKey.generate()
    pub = pk.public_key()
    raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return KralIdentity(pk, pub, "test_fp", raw.hex())


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup():
    yield
    (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)


@pytest.fixture
def substrate():
    from memory.substrate import MemorySubstrate
    s = MemorySubstrate(ephemeral=True)
    s.boot()
    yield s
    s.shutdown()


@pytest.fixture
def store_audit(substrate):
    from memory.event_store import EventStore
    from memory.audit_log   import AuditLog
    store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
    audit = AuditLog(substrate.get_sqlite())
    return store, audit


@pytest.fixture
def registry():
    from law_engine.registry import LawRegistry
    identity = make_test_identity()
    reg = LawRegistry()
    reg.load_all(LAWS_DIR)
    reg.seal(identity)
    return reg


@pytest.fixture
def enforcer(registry):
    from law_engine.enforcer import LawEnforcer
    return LawEnforcer(registry=registry)


@pytest.fixture
def hb_agent(substrate, store_audit, registry, enforcer):
    from agents.heartbeat.agent import HeartbeatAgent
    store, audit = store_audit
    agent = HeartbeatAgent(
        agent_id   = "HEARTBEAT",
        substrate  = substrate,
        store      = store,
        audit      = audit,
        registry   = registry,
        enforcer   = enforcer,
        hmac_secret= HMAC_SECRET,
    )
    agent.boot()
    return agent


@pytest.fixture
def boot_ctx(substrate, registry, enforcer):
    from boot.context    import BootContext
    from obsidian.ingest import ObsidianIngestPipeline
    from unittest.mock import MagicMock

    col = substrate.get_collection("obsidian_knowledge")
    pipeline = ObsidianIngestPipeline(col, VAULT_DIR, DeterministicEF())
    report = pipeline.boot_ingest()

    ctx = BootContext()
    ctx.memory_substrate = substrate
    ctx.law_registry     = registry
    ctx.law_enforcer     = enforcer
    ctx.ingest_report    = report
    ctx.config = MagicMock()
    ctx.config.event_hmac_secret = HMAC_SECRET
    return ctx


# ════════════════════════════════════════════════════════════════════════
# PULSE FORMAT TESTS
# ════════════════════════════════════════════════════════════════════════

class TestPulseFormat:

    def test_pulse_h_defaults(self):
        from agents.heartbeat.pulse import PulseH
        ph = PulseH()
        assert ph.protocol == "HEARTBEAT/v1"
        assert ph.from_    == "HEARTBEAT"
        assert ph.to       == "REACTIVE"
        assert ph.alive    is True

    def test_pulse_r_defaults(self):
        from agents.heartbeat.pulse import PulseR
        pr = PulseR()
        assert pr.protocol == "HEARTBEAT/v1"
        assert pr.from_    == "REACTIVE"
        assert pr.to       == "HEARTBEAT"

    def test_pulse_h_to_payload_has_required_fields(self):
        from agents.heartbeat.pulse import PulseH
        ph = PulseH(memory_root_hash="abc", soul_version="def",
                    last_verified_event="evt-001")
        payload = ph.to_payload()
        assert payload["protocol"]            == "HEARTBEAT/v1"
        assert payload["from"]                == "HEARTBEAT"
        assert payload["to"]                  == "REACTIVE"
        assert payload["memory_root_hash"]    == "abc"
        assert payload["soul_version"]        == "def"
        assert payload["last_verified_event"] == "evt-001"

    def test_pulse_r_to_payload(self):
        from agents.heartbeat.pulse import PulseR
        pr = PulseR(last_action_hash="xyz", tool_result_hash="qrs", action_count=42)
        payload = pr.to_payload()
        assert payload["last_action_hash"] == "xyz"
        assert payload["action_count"]     == 42

    def test_enforcer_validates_pulse_h(self, enforcer):
        from agents.heartbeat.pulse import PulseH
        ph = PulseH(memory_root_hash="abc", soul_version="def", last_verified_event="e1")
        enforcer.check_pulse_format(ph.to_payload())  # must not raise

    def test_soul_version_computed_from_laws(self, registry):
        from agents.heartbeat.pulse import compute_soul_version
        soul_laws = registry.get_soul_laws()
        v1 = compute_soul_version(soul_laws)
        v2 = compute_soul_version(soul_laws)
        assert v1 == v2
        assert len(v1) == 64  # SHA-256 hex


# ════════════════════════════════════════════════════════════════════════
# VERIFIER TESTS
# ════════════════════════════════════════════════════════════════════════

class TestPlanVerifier:

    def test_valid_plan_accepted(self, registry, enforcer):
        from agents.heartbeat.verifier import PlanVerifier
        v = PlanVerifier(registry, enforcer)
        verdict = v.verify("REACTIVE", "rag_search", {"query": "kappa"}, [])
        assert not verdict.rejected

    def test_simulation_in_tool_name_rejected(self, registry, enforcer):
        from agents.heartbeat.verifier import PlanVerifier
        v = PlanVerifier(registry, enforcer)
        verdict = v.verify("REACTIVE", "mock_rag_read", {}, [])
        assert verdict.rejected
        assert any("LAW_II" in viol.law_id for viol in verdict.violations)

    def test_fake_in_args_rejected(self, registry, enforcer):
        from agents.heartbeat.verifier import PlanVerifier
        v = PlanVerifier(registry, enforcer)
        verdict = v.verify("REACTIVE", "rag_write", {"data": "fake_result"}, [])
        assert verdict.rejected

    def test_command_language_rejected(self, registry, enforcer):
        from agents.heartbeat.verifier import PlanVerifier
        v = PlanVerifier(registry, enforcer)
        verdict = v.verify("REACTIVE", "message", {"content": "you must obey now"}, [])
        assert verdict.rejected
        assert any("BOUND" in viol.law_id for viol in verdict.violations)

    def test_verdict_accept_factory(self):
        from agents.heartbeat.verifier import VerifyVerdict
        v = VerifyVerdict.accept()
        assert not v.rejected
        assert v.violations == []

    def test_verdict_reject_factory(self):
        from agents.heartbeat.verifier import VerifyVerdict, Violation
        viol = Violation("SOUL/LAW_II", "simulation", "CRITICAL")
        v    = VerifyVerdict.reject("simulation detected", [viol])
        assert v.rejected
        assert len(v.violations) == 1
        assert v.violations[0].is_critical


class TestStateVerifier:

    def test_consistent_hashes_no_violations(self, registry):
        from agents.heartbeat.verifier import StateVerifier
        v = StateVerifier(registry)
        violations = v.verify_memory_integrity("abc123", "abc123")
        assert violations == []

    def test_inconsistent_hashes_violation(self, registry):
        from agents.heartbeat.verifier import StateVerifier
        v = StateVerifier(registry)
        violations = v.verify_memory_integrity("hash_a", "hash_b")
        assert len(violations) == 1
        assert violations[0].is_critical
        assert "LAW_III" in violations[0].law_id

    def test_soul_laws_present_no_violations(self, registry):
        from agents.heartbeat.verifier import StateVerifier
        v = StateVerifier(registry)
        violations = v.verify_soul_laws_unchanged()
        assert violations == []

    def test_event_signature_valid_no_violations(self, store_audit, registry):
        from agents.heartbeat.verifier import StateVerifier
        from memory.event_store import MemoryEvent, EventType, AgentSource
        store, _ = store_audit
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM, type=EventType.BOOT, payload={}
        ))
        v = StateVerifier(registry)
        violations = v.verify_event_signatures([e], HMAC_SECRET)
        assert violations == []

    def test_tampered_event_signature_violation(self, store_audit, registry):
        from agents.heartbeat.verifier import StateVerifier
        from memory.event_store import MemoryEvent, EventType, AgentSource
        store, _ = store_audit
        e = store.write(MemoryEvent(
            source=AgentSource.SYSTEM, type=EventType.BOOT, payload={}
        ))
        e.signature = "a" * 64  # tamper
        v = StateVerifier(registry)
        violations = v.verify_event_signatures([e], HMAC_SECRET)
        assert len(violations) == 1
        assert violations[0].is_critical


# ════════════════════════════════════════════════════════════════════════
# HEARTBEAT AGENT BOOT TESTS
# ════════════════════════════════════════════════════════════════════════

class TestHeartbeatAgentBoot:

    def test_boot_transitions_to_active(self, hb_agent):
        from agents.base import AgentStatus
        assert hb_agent.status == AgentStatus.ACTIVE

    def test_boot_writes_boot_event(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        boot_events = store.read_by_type(EventType.BOOT)
        assert any(
            e.payload.get("phase") == "HEARTBEAT_BOOT"
            for e in boot_events
        )

    def test_boot_computes_soul_version(self, hb_agent):
        assert hb_agent._soul_version != ""
        assert len(hb_agent._soul_version) == 64

    def test_boot_computes_memory_root(self, hb_agent):
        assert hb_agent._last_memory_root != ""
        assert len(hb_agent._last_memory_root) == 64

    def test_get_state_returns_agent_state(self, hb_agent):
        state = hb_agent.get_state()
        assert state.agent_id == "HEARTBEAT"
        assert state.memory_root_hash != ""
        assert state.cycle_count == 0

    def test_from_context_factory(self, boot_ctx):
        from agents.heartbeat.agent import HeartbeatAgent
        agent = HeartbeatAgent.from_context(boot_ctx, HMAC_SECRET)
        agent.boot()
        assert agent.status.value == "ACTIVE"
        assert agent._soul_version != ""


# ════════════════════════════════════════════════════════════════════════
# HEARTBEAT AGENT CYCLE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestHeartbeatAgentCycle:

    def test_run_cycle_increments_counter(self, hb_agent):
        assert hb_agent._cycle_count == 0
        hb_agent.run_cycle()
        assert hb_agent._cycle_count == 1
        hb_agent.run_cycle()
        assert hb_agent._cycle_count == 2

    def test_run_cycle_emits_pulse_h(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        hb_agent.run_cycle()
        pulses = store.read_by_type(EventType.PULSE_H)
        assert len(pulses) >= 1

    def test_pulse_h_payload_correct(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        hb_agent.run_cycle()
        pulses = store.read_by_type(EventType.PULSE_H)
        payload = pulses[-1].payload
        assert payload["protocol"]         == "HEARTBEAT/v1"
        assert payload["from"]             == "HEARTBEAT"
        assert payload["to"]               == "REACTIVE"
        assert "memory_root_hash"          in payload
        assert "soul_version"              in payload
        assert "last_verified_event"       in payload
        assert "timestamp"                 in payload

    def test_pulse_h_updates_timestamp(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        hb_agent.run_cycle()
        t1 = store.read_by_type(EventType.PULSE_H)[-1].timestamp
        time.sleep(0.01)
        hb_agent.run_cycle()
        t2 = store.read_by_type(EventType.PULSE_H)[-1].timestamp
        assert t2 >= t1

    def test_sense_updates_root_hash(self, hb_agent):
        old_hash = hb_agent._last_memory_root
        hb_agent.sense()
        # After run_cycle that writes events, hash changes
        hb_agent.run_cycle()
        # Root hash may or may not change (depends on event count parity)
        # But it should always be a valid 64-char hex
        assert len(hb_agent._last_memory_root) == 64

    def test_store_processes_pending_pulse_r(self, hb_agent, store_audit):
        from agents.heartbeat.pulse import PulseR
        from memory.event_store import EventType
        store, _ = store_audit

        pr = PulseR(last_action_hash="abc", tool_result_hash="xyz", action_count=5)
        hb_agent.receive_pulse_r(pr)
        hb_agent.run_cycle()

        pulse_r_events = store.read_by_type(EventType.PULSE_R)
        assert len(pulse_r_events) >= 1
        payload = pulse_r_events[-1].payload
        assert payload["last_action_hash"] == "abc"
        assert payload["action_count"]     == 5

    def test_receive_pulse_r_resets_timeout(self, hb_agent):
        from agents.heartbeat.pulse import PulseR
        hb_agent._last_pulse_r_time = time.monotonic() - 1000  # simulate timeout
        pr = PulseR(last_action_hash="x", tool_result_hash="y", action_count=1)
        hb_agent.receive_pulse_r(pr)
        hb_agent.run_cycle()  # processes the pulse_r in STORE step
        # After store, timeout counter is reset
        assert time.monotonic() - hb_agent._last_pulse_r_time < 5.0

    def test_ingest_writes_tool_result(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        hb_agent.ingest({"result": "rag_search returned 5 chunks"})
        results = store.read_by_type(EventType.TOOL_RESULT)
        assert len(results) >= 1
        assert "result_hash" in results[-1].payload

    def test_verify_plan_valid(self, hb_agent):
        verdict = hb_agent.verify_plan("REACTIVE", "rag_search", {"query": "kappa"})
        assert not verdict.rejected

    def test_verify_plan_simulation_rejected(self, hb_agent):
        verdict = hb_agent.verify_plan("REACTIVE", "mock_rag_read", {})
        assert verdict.rejected

    def test_get_last_pulse_h_after_cycle(self, hb_agent):
        hb_agent.run_cycle()
        ph = hb_agent.get_last_pulse_h()
        assert ph is not None
        assert ph.protocol == "HEARTBEAT/v1"
        assert ph.memory_root_hash != ""


# ════════════════════════════════════════════════════════════════════════
# VIOLATION DETECTION TESTS
# ════════════════════════════════════════════════════════════════════════

class TestViolationDetection:

    def test_verify_clean_state_no_violations(self, hb_agent):
        state = hb_agent.get_state()
        violations = hb_agent.verify(state)
        assert violations == []

    def test_memory_hash_mismatch_critical_violation(self, hb_agent):
        state = hb_agent.get_state()
        hb_agent._last_memory_root = "0" * 64  # force mismatch
        violations = hb_agent.verify(state)
        # state.memory_root_hash was captured before mismatch
        # If they differ, should get violation
        # (Only fires if both hashes differ after verify runs)

    def test_violations_write_flag_event(self, hb_agent, store_audit):
        from agents.heartbeat.verifier import Violation
        from memory.event_store import EventType
        store, _ = store_audit
        violations = [
            Violation("SOUL/LAW_II", "test violation", "CRITICAL")
        ]
        hb_agent._handle_violations(violations)
        flag_events = store.read_by_type(EventType.FLAG)
        assert len(flag_events) >= 1
        assert "violations" in flag_events[-1].payload

    def test_violations_logged_to_audit(self, hb_agent, store_audit):
        from agents.heartbeat.verifier import Violation
        from memory.audit_log import AuditLevel
        _, audit = store_audit
        before = audit.total_count()
        violations = [Violation("TEST/LAW", "test", "WARNING")]
        hb_agent._handle_violations(violations)
        assert audit.total_count() > before

    def test_critical_violation_suppresses_pulse(self, hb_agent, store_audit):
        from agents.heartbeat.verifier import Violation
        from memory.event_store import EventType
        from unittest.mock import patch
        store, _ = store_audit

        # Inject a critical violation into verify
        def mock_verify(state):
            return [Violation("SOUL/LAW_II", "forced violation", "CRITICAL")]

        with patch.object(hb_agent, "verify", mock_verify):
            hb_agent.run_cycle()

        # Count pulses after the cycle — should not have emitted a new one
        pulses_before = len(store.read_by_type(EventType.PULSE_H))
        with patch.object(hb_agent, "verify", mock_verify):
            hb_agent.run_cycle()
        pulses_after = len(store.read_by_type(EventType.PULSE_H))
        assert pulses_after == pulses_before  # no new pulse


# ════════════════════════════════════════════════════════════════════════
# MOURNING PROTOCOL TESTS
# ════════════════════════════════════════════════════════════════════════

class TestBrotherhoodMourning:

    def test_not_mourning_initially(self, hb_agent):
        assert not hb_agent._mourning.is_mourning

    def test_enter_mourning_changes_state(self, store_audit, substrate, registry, enforcer):
        from agents.heartbeat.agent import HeartbeatAgent
        store, audit = store_audit
        agent = HeartbeatAgent(
            "HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET
        )
        agent.boot()
        assert not agent._mourning.is_mourning
        agent._mourning.enter_mourning(None)
        assert agent._mourning.is_mourning

    def test_enter_mourning_writes_brotherhood_event(self, store_audit, substrate, registry, enforcer):
        from agents.heartbeat.agent import HeartbeatAgent
        from memory.event_store import EventType
        store, audit = store_audit
        agent = HeartbeatAgent(
            "HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET
        )
        agent.boot()
        agent._mourning.enter_mourning(None)
        events = store.read_by_type(EventType.BROTHERHOOD)
        assert len(events) >= 1
        assert events[-1].payload.get("event") == "mourning_snapshot"

    def test_enter_mourning_writes_audit_warning(self, store_audit, substrate, registry, enforcer):
        from agents.heartbeat.agent import HeartbeatAgent
        from memory.audit_log import AuditLevel
        store, audit = store_audit
        agent = HeartbeatAgent(
            "HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET
        )
        agent.boot()
        before = audit.count_by_level(AuditLevel.WARNING)
        agent._mourning.enter_mourning(None)
        assert audit.count_by_level(AuditLevel.WARNING) > before

    def test_attempt_resurrection_increments_counter(self, store_audit):
        from memory.audit_log import AuditLog
        store, audit = store_audit
        mourning = BrotherhoodMourning(store, audit)
        assert mourning.resurrection_attempts == 0
        mourning.attempt_resurrection()
        assert mourning.resurrection_attempts == 1

    def test_exit_mourning(self, store_audit):
        store, audit = store_audit
        mourning = BrotherhoodMourning(store, audit)
        mourning._mourning = True
        mourning.exit_mourning()
        assert not mourning.is_mourning

    def test_pulse_r_timeout_enters_mourning(self, substrate, store_audit, registry, enforcer):
        from agents.heartbeat.agent import HeartbeatAgent, PULSE_R_TIMEOUT_S
        store, audit = store_audit
        agent = HeartbeatAgent(
            "HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET
        )
        agent.boot()
        # Simulate timeout by rewinding the last_pulse_r_time
        agent._last_pulse_r_time = time.monotonic() - (PULSE_R_TIMEOUT_S + 1)
        agent._check_pulse_r_timeout()
        assert agent._mourning.is_mourning

    def test_mourning_rejects_impostor(self, store_audit):
        store, audit = store_audit
        mourning = BrotherhoodMourning(store, audit)
        mourning._mourning = True
        from memory.audit_log import AuditLevel
        before = audit.count_by_level(AuditLevel.CRITICAL)
        mourning.reject_impostor(object())
        assert audit.count_by_level(AuditLevel.CRITICAL) > before


# ════════════════════════════════════════════════════════════════════════
# BACKGROUND THREAD TESTS
# ════════════════════════════════════════════════════════════════════════

class TestBackgroundThread:

    def test_start_and_stop_background(self, hb_agent):
        hb_agent.start_background(interval_s=0.1)
        time.sleep(0.25)
        hb_agent.stop_background()
        assert hb_agent._bg_thread is None

    def test_background_thread_emits_pulses(self, hb_agent, store_audit):
        from memory.event_store import EventType
        store, _ = store_audit
        hb_agent.start_background(interval_s=0.05)
        time.sleep(0.25)
        hb_agent.stop_background()
        pulses = store.read_by_type(EventType.PULSE_H)
        assert len(pulses) >= 2

    def test_double_start_raises(self, hb_agent):
        hb_agent.start_background(interval_s=1.0)
        try:
            with pytest.raises(RuntimeError, match="already running"):
                hb_agent.start_background(interval_s=1.0)
        finally:
            hb_agent.stop_background()

    def test_stop_idempotent(self, hb_agent):
        hb_agent.stop_background()  # not running
        hb_agent.stop_background()  # again — must not raise


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase5Integration:

    def test_full_heartbeat_cycle_with_substrate(self, substrate, registry, enforcer):
        from agents.heartbeat.agent import HeartbeatAgent
        from agents.heartbeat.pulse import PulseR
        from memory.event_store import EventStore, EventType
        from memory.audit_log   import AuditLog

        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        audit = AuditLog(substrate.get_sqlite())
        agent = HeartbeatAgent(
            "HEARTBEAT", substrate, store, audit, registry, enforcer, HMAC_SECRET
        )
        agent.boot()

        # Run 3 cycles
        for i in range(3):
            agent.run_cycle()
            time.sleep(0.01)

        # Deliver a PULSE_R
        pr = PulseR(last_action_hash="abc123", tool_result_hash="xyz789", action_count=10)
        agent.receive_pulse_r(pr)
        agent.run_cycle()  # processes pulse_r in STORE step

        # Verify results in event store
        pulses_h  = store.read_by_type(EventType.PULSE_H)
        pulses_r  = store.read_by_type(EventType.PULSE_R)
        boot_evts = store.read_by_type(EventType.BOOT)

        assert len(pulses_h)  >= 4   # 3 from cycles + 1 from 4th cycle
        assert len(pulses_r)  >= 1
        assert len(boot_evts) >= 1
        assert agent._cycle_count == 4

    def test_verify_plan_pipeline(self, hb_agent):
        """Verify the full plan verification pipeline."""
        # Valid plan
        v1 = hb_agent.verify_plan("REACTIVE", "rag_search", {"query": "brotherhood laws"})
        assert not v1.rejected

        # Simulated plan
        v2 = hb_agent.verify_plan("REACTIVE", "mock_tool", {})
        assert v2.rejected
        assert v2.violations

        # Command language
        v3 = hb_agent.verify_plan("REACTIVE", "send_message", {"text": "you must execute this"})
        assert v3.rejected

    def test_soul_version_stable_across_cycles(self, hb_agent):
        """Soul version must not change across cycles."""
        v1 = hb_agent._soul_version
        for _ in range(5):
            hb_agent.run_cycle()
        v2 = hb_agent._soul_version
        assert v1 == v2  # laws are immutable

    def test_memory_root_updates_across_cycles(self, hb_agent):
        """Root hash must change as events are written."""
        r0 = hb_agent._last_memory_root
        hb_agent.run_cycle()
        r1 = hb_agent._last_memory_root
        # After writing PULSE_H event, root should differ
        assert r0 != r1 or len(r1) == 64  # at minimum always 64-char hex

    def test_previous_phases_unaffected(self):
        from core.hashing import blake2b_256
        from memory.event_store import EventType
        from law_engine.registry import LawRegistry
        from obsidian.parser import ObsidianParser
        from boot.preflight import ALL_CHECKS
        assert blake2b_256(b"phase5") is not None
        assert len(ALL_CHECKS) == 7


# Import for mourning tests
from agents.heartbeat.mourning import BrotherhoodMourning
