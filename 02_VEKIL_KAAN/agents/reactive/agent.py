"""
agents/reactive/agent.py — Reactive Agent: THINK → DECIDE → ACT → FEED

The action arm of VEKIL-KAAN.
Receives goals, reasons about them, executes tools, feeds results to Heartbeat.

Cycle per input:
  1. OBSERVE: scan input + query RAG for context
  2. Wait for PULSE_H from Heartbeat (safe mode if missing)
  3. THINK/REASON: produce a Plan via LLM or fallback
  4. REQUEST: send tool request to Heartbeat (7-step protocol)
  5. ACT: execute the approved tool
  6. FEED: emit PULSE_R every N actions, verify result written to memory

Heartbeat dependency:
  - Reactive FREEZES if no PULSE_H within PULSE_H_TIMEOUT_S
  - Reactive emits PULSE_R every PULSE_R_EVERY_N_ACTIONS
  - Every tool call must be logged by Heartbeat (7-step protocol)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agents.base import AgentState, AgentStatus, BaseAgent
from agents.reactive.goal import Goal, GoalEvaluator, GoalResult
from agents.reactive.reason import Observation, Plan, ReasonEngine
from agents.heartbeat.pulse import PulseR
from core.exceptions import HeartbeatMissing
from core.hashing import sha256_hex
from memory.event_store import AgentSource, EventType, MemoryEvent

if TYPE_CHECKING:
    from agents.heartbeat.agent import HeartbeatAgent
    from boot.context import BootContext
    from law_engine.enforcer import LawEnforcer
    from law_engine.registry import LawRegistry
    from llm.base import BaseLLMInterface
    from memory.audit_log import AuditLog
    from memory.event_store import EventStore
    from memory.substrate import MemorySubstrate

log = logging.getLogger(__name__)

PULSE_H_TIMEOUT_S       = 30.0   # safe mode if no PULSE_H for this long
PULSE_R_EVERY_N_ACTIONS = 5      # emit PULSE_R every N actions
MAX_REASON_RETRIES      = 3      # max re-reasons if Heartbeat rejects plan


class ReactiveAgent(BaseAgent):
    """
    Reactive Agent — action arm of VEKIL-KAAN.

    Usage:
        agent = ReactiveAgent.from_context(ctx, heartbeat_agent, hmac_secret, llm=None)
        agent.boot()
        result = agent.run_cycle("What do you know about soul laws?")
    """

    def __init__(
        self,
        agent_id:   str,
        substrate:  "MemorySubstrate",
        store:      "EventStore",
        audit:      "AuditLog",
        registry:   "LawRegistry",
        enforcer:   "LawEnforcer",
        heartbeat:  "HeartbeatAgent",
        hmac_secret: str,
        llm:        "BaseLLMInterface | None" = None,
    ) -> None:
        super().__init__(agent_id)
        self._substrate  = substrate
        self._store      = store
        self._audit      = audit
        self._registry   = registry
        self._enforcer   = enforcer
        self._heartbeat  = heartbeat
        self._secret     = hmac_secret
        self._llm        = llm

        self._action_count:    int   = 0
        self._last_pulse_h:    Any   = None       # most recent PulseH received
        self._last_pulse_h_ts: float = 0.0        # monotonic time of last PULSE_H
        self._last_result:     Any   = None

        # Sub-components
        self._reason_engine = ReasonEngine(registry, llm, substrate)
        self._goal_evaluator = GoalEvaluator(substrate)

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_context(
        cls,
        ctx:       "BootContext",
        heartbeat: "HeartbeatAgent",
        hmac_secret: str,
        llm:       "BaseLLMInterface | None" = None,
    ) -> "ReactiveAgent":
        from memory.event_store import EventStore
        from memory.audit_log   import AuditLog

        assert ctx.memory_substrate is not None
        assert ctx.law_registry     is not None
        assert ctx.law_enforcer     is not None

        store = EventStore(ctx.memory_substrate.get_sqlite(), hmac_secret)
        audit = AuditLog(ctx.memory_substrate.get_sqlite())

        return cls(
            agent_id    = "REACTIVE",
            substrate   = ctx.memory_substrate,
            store       = store,
            audit       = audit,
            registry    = ctx.law_registry,
            enforcer    = ctx.law_enforcer,
            heartbeat   = heartbeat,
            hmac_secret = hmac_secret,
            llm         = llm,
        )

    # ── Boot ──────────────────────────────────────────────────────────────────

    def boot(self) -> None:
        """
        Boot Reactive. Waits for first PULSE_H from Heartbeat (step 4 of MEMORY.md).
        """
        self._transition(AgentStatus.ACTIVE)

        # Write BOOT event
        self._store.write(MemoryEvent(
            source=AgentSource.REACTIVE,
            type=EventType.BOOT,
            payload={
                "phase":     "REACTIVE_BOOT",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        ))

        from memory.audit_log import AuditLevel
        self._audit.log(AuditLevel.INFO, "REACTIVE", "boot", "Reactive Agent booted")
        log.info("ReactiveAgent booted")

        # Try to get initial PULSE_H (non-blocking — heartbeat may not have run yet)
        pulse = self._heartbeat.get_last_pulse_h()
        if pulse:
            self._last_pulse_h    = pulse
            self._last_pulse_h_ts = time.monotonic()

    def get_state(self) -> AgentState:
        return AgentState(
            agent_id         = self.agent_id,
            status           = self.status,
            memory_root_hash = self._substrate.compute_root_hash(),
            last_event_id    = self._get_last_own_event_id(),
            cycle_count      = self._action_count,
        )

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_cycle(self, input_data: str, goal: Goal | None = None) -> Any:
        """
        Full THINK→DECIDE→ACT→FEED cycle for one input.

        Returns the tool result, or None if safe mode / heartbeat missing.
        """
        # OBSERVE: build observation from input + RAG context
        obs = self.observe(input_data)

        # Wait for PULSE_H — freeze if missing (HEARTBEAT.md: safe mode)
        pulse = self._get_valid_pulse_h()
        if pulse is None:
            self._transition(AgentStatus.SAFE_MODE)
            raise HeartbeatMissing(
                f"No PULSE_H received within {PULSE_H_TIMEOUT_S}s — entering safe mode"
            )
        self._transition(AgentStatus.ACTIVE)

        # THINK/REASON loop: retry if Heartbeat rejects plan
        result = None
        for attempt in range(MAX_REASON_RETRIES):
            plan = self.reason(obs, self._heartbeat.get_state(), attempt)

            # REQUEST: 7-step tool call protocol
            # Step 1: Write TOOL_CALL intent
            self._write_tool_call_intent(plan)

            # Step 2-3: Heartbeat verifies and grants (in practice: Heartbeat runs VERIFY)
            verdict = self._heartbeat.verify_plan(
                agent     = self.agent_id,
                tool_name = plan.tool_name,
                tool_args = plan.tool_args,
                cited_ids = plan.cited_source_ids,
            )

            if verdict.rejected:
                log.info(
                    "Reactive plan rejected by Heartbeat (attempt %d): %s",
                    attempt + 1, verdict.reason
                )
                self._store.write(MemoryEvent(
                    source  = AgentSource.REACTIVE,
                    type    = EventType.FLAG,
                    payload = {"plan_rejected": True, "reason": verdict.reason, "attempt": attempt + 1},
                ))
                continue  # re-reason

            # Step 4: ACT — execute tool
            result = self.act(plan)

            # Steps 5-7: Heartbeat ingests result (called by DualReActLoop in Phase 7)
            # For standalone operation: we call it directly
            self._heartbeat.ingest(result)

            self._action_count += 1
            self._last_result = result

            # FEED: emit PULSE_R every N actions
            if self._action_count % PULSE_R_EVERY_N_ACTIONS == 0:
                self._emit_pulse_r(result, plan)

            break  # plan accepted and executed

        # Goal evaluation
        if goal is not None:
            eval_result = self._goal_evaluator.evaluate(goal, result)
            log.info(
                "Goal eval: achieved=%s confidence=%.2f",
                eval_result.achieved, eval_result.confidence
            )

        return result

    # ── OBSERVE ───────────────────────────────────────────────────────────────

    def observe(self, input_text: str) -> Observation:
        """
        Build observation: semantic search RAG for context + last events.
        """
        rag_context: list[dict] = []
        try:
            col = self._substrate.get_collection("obsidian_knowledge")
            if col.count() > 0:
                # Use deterministic EF if available, else skip
                results = col.query(
                    query_embeddings=[self._simple_embed(input_text)],
                    n_results=min(5, col.count()),
                )
                for i, doc_id in enumerate(results["ids"][0]):
                    rag_context.append({
                        "id":       doc_id,
                        "text":     results["documents"][0][i],
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                    })
        except Exception as e:
            log.debug("RAG context query failed: %s", e)

        last_events = []
        try:
            last_events = self._store.get_last_n(10)
        except Exception:
            pass

        return Observation(
            input_text     = input_text,
            rag_context    = rag_context,
            last_events    = last_events,
            heartbeat_hash = self._last_pulse_h.memory_root_hash if self._last_pulse_h else "",
        )

    # ── REASON ────────────────────────────────────────────────────────────────

    def reason(
        self,
        obs:             Observation,
        heartbeat_state: AgentState,
        iteration:       int = 0,
    ) -> Plan:
        return self._reason_engine.reason(obs, heartbeat_state, iteration)

    # ── ACT ───────────────────────────────────────────────────────────────────

    def act(self, plan: Plan) -> Any:
        """
        Execute the tool specified in the plan.
        Currently wires rag_search, rag_read, rag_write against live ChromaDB.
        Phase 9: ToolSandbox will wrap this.
        """
        t0 = time.monotonic()
        result = self._dispatch_tool(plan.tool_name, plan.tool_args)
        elapsed_ms = (time.monotonic() - t0) * 1000

        # Latency enforcement (REACT_LOOP sync rule: 500ms)
        try:
            self._enforcer.check_latency(self.agent_id, elapsed_ms)
        except Exception as e:
            log.warning("Latency violation: %s", e)

        log.debug("ACT %s → %.0fms", plan.tool_name, elapsed_ms)
        return result

    def _dispatch_tool(self, tool_name: str, args: dict) -> Any:
        """Route tool call to ChromaDB or raise ToolNotFound."""
        from core.exceptions import ToolNotFound

        name = tool_name.lower().strip()

        if name == "rag_search":
            return self._tool_rag_search(args)
        elif name == "rag_read":
            return self._tool_rag_read(args)
        elif name == "rag_write":
            return self._tool_rag_write(args)
        elif name == "rag_ingest":
            return self._tool_rag_ingest(args)
        else:
            raise ToolNotFound(f"Tool '{tool_name}' not in RAG tool set")

    # ── Tools ─────────────────────────────────────────────────────────────────

    def _tool_rag_search(self, args: dict) -> dict:
        query    = args.get("query", "")
        n        = int(args.get("n_results", 5))
        col_name = args.get("collection", "obsidian_knowledge")
        try:
            col = self._substrate.get_collection(col_name)
            if col.count() == 0:
                return {"results": [], "query": query, "count": 0}
            # Use pre-computed embedding to avoid ONNX download in network-restricted envs
            embedding = self._simple_embed(query)
            results = col.query(
                query_embeddings=[embedding],
                n_results=min(n, col.count()),
            )
            hits = []
            for i, doc_id in enumerate(results["ids"][0]):
                hits.append({
                    "id":       doc_id,
                    "text":     results["documents"][0][i][:500],
                    "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                })
            return {"results": hits, "query": query, "count": len(hits)}
        except Exception as e:
            return {"error": str(e), "query": query}

    def _tool_rag_read(self, args: dict) -> dict:
        chunk_id = args.get("chunk_id", args.get("id", ""))
        col_name = args.get("collection", "obsidian_knowledge")
        try:
            col    = self._substrate.get_collection(col_name)
            result = col.get(ids=[chunk_id])
            if result["ids"]:
                return {
                    "id":       chunk_id,
                    "text":     result["documents"][0] if result["documents"] else "",
                    "metadata": result["metadatas"][0] if result["metadatas"] else {},
                }
            return {"error": f"Chunk '{chunk_id}' not found"}
        except Exception as e:
            return {"error": str(e)}

    def _tool_rag_write(self, args: dict) -> dict:
        content  = str(args.get("content", ""))
        metadata = args.get("metadata", {})
        col_name = args.get("collection", "session_context")
        doc_id   = args.get("id", sha256_hex(content.encode())[:16])
        try:
            col = self._substrate.get_collection(col_name)
            safe_meta = metadata if isinstance(metadata, dict) and metadata else {}
            safe_meta = {**safe_meta, "source": "reactive_agent", "col": col_name}
            # Generate deterministic embedding to avoid ONNX download in network-restricted envs
            embedding = self._simple_embed(content)
            col.add(
                ids        = [doc_id],
                documents  = [content],
                metadatas  = [safe_meta],
                embeddings = [embedding],
            )
            return {"id": doc_id, "written": True, "bytes": len(content)}
        except Exception as e:
            return {"error": str(e)}

    def _tool_rag_ingest(self, args: dict) -> dict:
        return self._tool_rag_write({**args, "collection": "session_context"})

    # ── PULSE_R emission ──────────────────────────────────────────────────────

    def _emit_pulse_r(self, result: Any, plan: Plan) -> None:
        """
        Emit PULSE_R to Heartbeat every PULSE_R_EVERY_N_ACTIONS actions.
        HEARTBEAT.md: PULSE_R carries last_action_hash + tool_result_hash.
        Written directly to event store AND queued for Heartbeat processing.
        """
        import json
        result_hash = sha256_hex(
            json.dumps(result, default=str, sort_keys=True).encode()
        )
        pr = PulseR(
            last_action_hash = plan.args_hash(),
            tool_result_hash = result_hash,
            action_count     = self._action_count,
            timestamp        = datetime.now(timezone.utc).isoformat(),
        )
        # Write directly to event store (REACTIVE source — per MEMORY.md PULSE_R writer=REACTIVE)
        self._store.write(MemoryEvent(
            source  = AgentSource.REACTIVE,
            type    = EventType.PULSE_R,
            payload = pr.to_payload(),
        ))
        # Also queue for Heartbeat's STORE step
        self._heartbeat.receive_pulse_r(pr)
        log.debug("PULSE_R emitted | actions=%d", self._action_count)

    # ── Pulse H handling ──────────────────────────────────────────────────────

    def refresh_pulse_h(self) -> None:
        """Fetch the latest PULSE_H from Heartbeat's event store."""
        pulse = self._heartbeat.get_last_pulse_h()
        if pulse:
            self._last_pulse_h    = pulse
            self._last_pulse_h_ts = time.monotonic()

    def _get_valid_pulse_h(self) -> Any:
        """
        Return the current PULSE_H if fresh enough, else None.
        Always tries to refresh first.
        """
        self.refresh_pulse_h()
        if self._last_pulse_h is None:
            return None
        elapsed = time.monotonic() - self._last_pulse_h_ts
        if elapsed > PULSE_H_TIMEOUT_S:
            log.warning(
                "PULSE_H is %.0fs old (timeout=%.0fs)", elapsed, PULSE_H_TIMEOUT_S
            )
            return None
        return self._last_pulse_h

    # ── Goal evaluation ───────────────────────────────────────────────────────

    def goal_achieved(self, result: Any, goal: Goal | None = None) -> bool:
        """Quick binary check for DualReActLoop (Phase 7)."""
        if goal is None:
            # No explicit goal: any non-error result is "achieved"
            if result is None:
                return False
            return "error" not in str(result).lower()
        eval_result = self._goal_evaluator.evaluate(goal, result)
        return eval_result.achieved

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _simple_embed(self, text: str) -> list[float]:
        """Deterministic 384-dim embedding for network-restricted environments."""
        import hashlib
        h = hashlib.sha256(text.encode()).digest()
        return [(float(h[i % 32]) / 255.0) - 0.5 for i in range(384)]

    def _write_tool_call_intent(self, plan: Plan) -> None:
        """Step 1 of 7-step protocol: write TOOL_CALL intent to event store."""
        self._store.write(MemoryEvent(
            source  = AgentSource.REACTIVE,
            type    = EventType.TOOL_CALL,
            payload = {
                "tool_name":  plan.tool_name,
                "tool_args":  plan.tool_args,
                "reasoning":  plan.reasoning[:200],
                "iteration":  plan.iteration,
                "args_hash":  plan.args_hash(),
            }
        ))

    def _get_last_own_event_id(self) -> str:
        try:
            events = self._store.read_by_source(AgentSource.REACTIVE)
            return events[-1].event_id if events else ""
        except Exception:
            return ""
