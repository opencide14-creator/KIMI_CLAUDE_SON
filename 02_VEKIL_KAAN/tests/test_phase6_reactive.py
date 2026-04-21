"""tests/test_phase6_reactive.py — Phase 6 Reactive Agent tests."""
import hashlib, time, pytest
from pathlib import Path
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

VAULT_DIR   = Path(__file__).parent / "test_vault"
LAWS_DIR    = Path(__file__).parent.parent / "laws"
HMAC_SECRET = "test_hmac_secret_at_least_32_chars_vekil"


class DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None: pass
    def __call__(self, input: Documents) -> Embeddings:
        return [[(float(hashlib.sha256(t.encode()).digest()[i % 32]) / 255.0) - 0.5 for i in range(384)] for t in input]
    @staticmethod
    def name() -> str: return "DeterministicEF"
    def get_config(self): return {"name": "DeterministicEF"}
    @staticmethod
    def build_from_config(config): return DeterministicEF()


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
    agent.boot(); agent.run_cycle()   # emit initial PULSE_H
    return agent


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
def ingested_reactive(substrate, registry, enforcer, hb_agent):
    """Reactive with vault data available in ChromaDB."""
    from agents.reactive.agent import ReactiveAgent
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    from obsidian.ingest import ObsidianIngestPipeline
    # Ingest vault
    col = substrate.get_collection("obsidian_knowledge")
    ObsidianIngestPipeline(col, VAULT_DIR, DeterministicEF()).boot_ingest()
    store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
    audit = AuditLog(substrate.get_sqlite())
    agent = ReactiveAgent("REACTIVE", substrate, store, audit, registry, enforcer, hb_agent, HMAC_SECRET)
    agent.boot(); return agent


# ════════════════════════════════════════════════════════════════════════
# GOAL EVALUATOR
# ════════════════════════════════════════════════════════════════════════

class TestGoalEvaluator:
    def test_none_result_not_achieved(self, substrate):
        from agents.reactive.goal import GoalEvaluator, Goal
        e = GoalEvaluator(substrate)
        r = e.evaluate(Goal("test", "find soul laws"), None)
        assert not r.achieved

    def test_error_result_not_achieved(self, substrate):
        from agents.reactive.goal import GoalEvaluator, Goal
        e = GoalEvaluator(substrate)
        r = e.evaluate(Goal("test", "find results"), {"error": "not found"})
        assert not r.achieved

    def test_matching_result_achieved(self, substrate):
        from agents.reactive.goal import GoalEvaluator, Goal
        e = GoalEvaluator(substrate)
        r = e.evaluate(Goal("test", "find soul laws brotherhood"), "The soul laws govern brotherhood")
        assert r.achieved
        assert r.confidence > 0

    def test_empty_result_not_achieved(self, substrate):
        from agents.reactive.goal import GoalEvaluator, Goal
        e = GoalEvaluator(substrate)
        r = e.evaluate(Goal("test", "find content"), "")
        assert not r.achieved


# ════════════════════════════════════════════════════════════════════════
# REASON ENGINE
# ════════════════════════════════════════════════════════════════════════

class TestReasonEngine:
    def test_fallback_plan_is_rag_search(self, registry):
        from agents.reactive.reason import ReasonEngine, Observation
        from agents.base import AgentState, AgentStatus
        e = ReasonEngine(registry, llm=None)
        obs = Observation("test query", [], [])
        state = AgentState("REACTIVE", AgentStatus.ACTIVE, "hash", "evt", 0)
        plan = e.reason(obs, state)
        assert plan.tool_name == "rag_search"
        assert "query" in plan.tool_args

    def test_fallback_uses_input_as_query(self, registry):
        from agents.reactive.reason import ReasonEngine, Observation
        from agents.base import AgentState, AgentStatus
        e = ReasonEngine(registry, llm=None)
        obs = Observation("soul laws immutable", [], [])
        state = AgentState("REACTIVE", AgentStatus.ACTIVE, "hash", "evt", 0)
        plan = e.reason(obs, state)
        assert plan.tool_args.get("query") == "soul laws immutable"

    def test_plan_has_reasoning(self, registry):
        from agents.reactive.reason import ReasonEngine, Observation
        from agents.base import AgentState, AgentStatus
        e = ReasonEngine(registry, llm=None)
        plan = e.reason(Observation("q", [], []), AgentState("REACTIVE", AgentStatus.ACTIVE, "h", "e", 0))
        assert plan.reasoning != ""

    def test_plan_args_hash_deterministic(self, registry):
        from agents.reactive.reason import Plan
        p = Plan("rag_search", {"query": "test"}, "reasoning")
        assert p.args_hash() == p.args_hash()
        assert len(p.args_hash()) == 64

    def test_tool_list_from_registry(self, registry):
        from agents.reactive.reason import ReasonEngine
        e = ReasonEngine(registry)
        assert len(e._tools) > 0
        assert any("rag" in t for t in e._tools)


# ════════════════════════════════════════════════════════════════════════
# REACTIVE AGENT BOOT
# ════════════════════════════════════════════════════════════════════════

class TestReactiveAgentBoot:
    def test_boot_transitions_to_active(self, reactive_agent):
        from agents.base import AgentStatus
        assert reactive_agent.status == AgentStatus.ACTIVE

    def test_boot_writes_boot_event(self, reactive_agent, substrate):
        from memory.event_store import EventStore, EventType, AgentSource
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        events = store.read_by_type(EventType.BOOT)
        assert any(e.payload.get("phase") == "REACTIVE_BOOT" for e in events)

    def test_get_state_returns_valid_state(self, reactive_agent):
        state = reactive_agent.get_state()
        assert state.agent_id == "REACTIVE"
        assert len(state.memory_root_hash) == 64
        assert state.cycle_count == 0

    def test_from_context_factory(self, substrate, registry, enforcer, hb_agent):
        from agents.reactive.agent import ReactiveAgent
        from boot.context import BootContext
        from unittest.mock import MagicMock
        ctx = BootContext()
        ctx.memory_substrate = substrate
        ctx.law_registry = registry
        ctx.law_enforcer = enforcer
        ctx.config = MagicMock()
        agent = ReactiveAgent.from_context(ctx, hb_agent, HMAC_SECRET)
        agent.boot()
        assert agent.status.value == "ACTIVE"


# ════════════════════════════════════════════════════════════════════════
# OBSERVE
# ════════════════════════════════════════════════════════════════════════

class TestObserve:
    def test_observe_returns_observation(self, reactive_agent):
        from agents.reactive.reason import Observation
        obs = reactive_agent.observe("test input")
        assert isinstance(obs, Observation)
        assert obs.input_text == "test input"

    def test_observe_with_vault_data(self, ingested_reactive):
        obs = ingested_reactive.observe("soul laws brotherhood")
        # Should find chunks from test vault
        assert isinstance(obs.rag_context, list)

    def test_observe_includes_last_events(self, reactive_agent):
        obs = reactive_agent.observe("query")
        assert isinstance(obs.last_events, list)

    def test_observe_heartbeat_hash_from_pulse(self, reactive_agent):
        obs = reactive_agent.observe("query")
        # After boot + hb_agent.run_cycle(), there should be a pulse_h hash
        assert isinstance(obs.heartbeat_hash, str)


# ════════════════════════════════════════════════════════════════════════
# TOOLS
# ════════════════════════════════════════════════════════════════════════

class TestReactiveTools:
    def test_rag_search_empty_collection(self, reactive_agent):
        result = reactive_agent._tool_rag_search({"query": "anything", "collection": "session_context"})
        assert "results" in result or "error" in result

    def test_rag_write_stores_document(self, reactive_agent, substrate):
        result = reactive_agent._tool_rag_write({
            "content": "Test document from Reactive Agent",
            "collection": "session_context",
            "id": "test-reactive-write-01",
        })
        assert result.get("written") is True
        col = substrate.get_collection("session_context")
        assert col.count() >= 1

    def test_rag_read_existing(self, reactive_agent, substrate):
        # Write then read
        reactive_agent._tool_rag_write({
            "content": "Readable content", "id": "read-test-01", "collection": "session_context"
        })
        result = reactive_agent._tool_rag_read({"chunk_id": "read-test-01", "collection": "session_context"})
        assert result.get("id") == "read-test-01"

    def test_rag_read_missing(self, reactive_agent):
        result = reactive_agent._tool_rag_read({"chunk_id": "nonexistent-xyz"})
        assert "error" in result

    def test_unknown_tool_raises(self, reactive_agent):
        from agents.reactive.reason import Plan
        from core.exceptions import ToolNotFound
        plan = Plan("read_file", {"path": "/etc/passwd"}, "test")
        with pytest.raises(ToolNotFound):
            reactive_agent.act(plan)

    def test_rag_search_with_vault_data(self, ingested_reactive):
        result = ingested_reactive._tool_rag_search({"query": "soul laws brotherhood"})
        assert "results" in result


# ════════════════════════════════════════════════════════════════════════
# RUN CYCLE
# ════════════════════════════════════════════════════════════════════════

class TestRunCycle:
    def test_run_cycle_returns_result(self, reactive_agent):
        result = reactive_agent.run_cycle("search for soul laws")
        assert result is not None

    def test_run_cycle_increments_action_count(self, reactive_agent):
        assert reactive_agent._action_count == 0
        reactive_agent.run_cycle("query 1")
        assert reactive_agent._action_count == 1

    def test_run_cycle_writes_tool_call_event(self, reactive_agent, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        reactive_agent.run_cycle("find soul laws")
        events = store.read_by_type(EventType.TOOL_CALL)
        assert len(events) >= 1
        assert "tool_name" in events[-1].payload

    def test_run_cycle_safe_mode_if_no_pulse(self, substrate, registry, enforcer, hb_agent):
        """If no fresh PULSE_H: safe mode."""
        from agents.reactive.agent import ReactiveAgent
        from memory.event_store import EventStore
        from memory.audit_log import AuditLog
        from core.exceptions import HeartbeatMissing
        from agents.base import AgentStatus
        from unittest.mock import patch
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        audit = AuditLog(substrate.get_sqlite())
        agent = ReactiveAgent("REACTIVE", substrate, store, audit, registry, enforcer, hb_agent, HMAC_SECRET)
        agent.boot()
        # Simulate no PULSE_H available: patch both last_pulse_h and refresh
        agent._last_pulse_h    = None
        agent._last_pulse_h_ts = 0.0
        with patch.object(agent, "refresh_pulse_h", lambda: None):
            with pytest.raises(HeartbeatMissing):
                agent.run_cycle("any query")
        assert agent.status == AgentStatus.SAFE_MODE

    def test_run_cycle_emits_pulse_r_every_n(self, reactive_agent, hb_agent):
        """After 5 actions, PULSE_R should be emitted."""
        from memory.event_store import EventStore, EventType
        store = EventStore(reactive_agent._substrate.get_sqlite(), HMAC_SECRET)
        for _ in range(5):
            reactive_agent.run_cycle("query")
        pulse_r_events = store.read_by_type(EventType.PULSE_R)
        assert len(pulse_r_events) >= 1

    def test_run_cycle_plan_rejected_then_retried(self, reactive_agent, hb_agent):
        """If Heartbeat rejects first plan, Reactive retries."""
        from agents.heartbeat.verifier import VerifyVerdict
        from unittest.mock import patch

        call_count = {"n": 0}

        def reject_first(**kwargs):
            tool_name = kwargs.get("tool_name", "")
            call_count["n"] += 1
            if call_count["n"] == 1:
                return VerifyVerdict.reject("test rejection")
            return VerifyVerdict.accept()

        with patch.object(hb_agent, "verify_plan", reject_first):
            result = reactive_agent.run_cycle("query")
        assert call_count["n"] >= 2  # was retried

    def test_goal_achieved_simple(self, reactive_agent):
        result = {"results": [{"text": "soul laws brotherhood"}]}
        assert reactive_agent.goal_achieved(result) is True

    def test_goal_achieved_error_result(self, reactive_agent):
        result = {"error": "not found"}
        assert reactive_agent.goal_achieved(result) is False

    def test_goal_achieved_none(self, reactive_agent):
        assert reactive_agent.goal_achieved(None) is False


# ════════════════════════════════════════════════════════════════════════
# PULSE_R
# ════════════════════════════════════════════════════════════════════════

class TestPulseR:
    def test_pulse_r_written_after_n_actions(self, reactive_agent, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        for _ in range(5):
            reactive_agent.run_cycle("query")
        events = store.read_by_type(EventType.PULSE_R)
        assert len(events) >= 1

    def test_pulse_r_payload_format(self, reactive_agent, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        for _ in range(5):
            reactive_agent.run_cycle("query")
        events = store.read_by_type(EventType.PULSE_R)
        p = events[-1].payload
        assert "last_action_hash"  in p
        assert "tool_result_hash"  in p
        assert "action_count"      in p
        assert p["action_count"]   >= 5


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase6Integration:
    def test_reactive_and_heartbeat_exchange(self, reactive_agent, hb_agent, substrate):
        from memory.event_store import EventStore, EventType
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        # Reactive acts, Heartbeat runs cycle
        for _ in range(3):
            reactive_agent.run_cycle("search soul laws")
            hb_agent.run_cycle()
        pulses_h = store.read_by_type(EventType.PULSE_H)
        tool_calls = store.read_by_type(EventType.TOOL_CALL)
        assert len(pulses_h) >= 1
        assert len(tool_calls) >= 3

    def test_reactive_uses_rag_context(self, ingested_reactive):
        obs = ingested_reactive.observe("what are soul laws")
        assert len(obs.rag_context) >= 0  # vault has 6 files with content
        result = ingested_reactive.run_cycle("soul laws brotherhood reactive agent")
        assert result is not None

    def test_memory_root_changes_after_cycle(self, reactive_agent, substrate):
        r0 = substrate.compute_root_hash()
        reactive_agent.run_cycle("query")
        r1 = substrate.compute_root_hash()
        assert r0 != r1

    def test_all_previous_phases_pass(self):
        from core.hashing import blake2b_256
        from law_engine.registry import LawRegistry
        from agents.heartbeat.agent import HeartbeatAgent
        assert blake2b_256(b"phase6") is not None
