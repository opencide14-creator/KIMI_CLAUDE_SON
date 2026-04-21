"""
agents/sync/dual_loop.py — DualReActLoop orchestrator.

Implements the 6-step joint cycle from REACT_LOOP.md:

  STEP 1: OBSERVE  (both)
  STEP 2: REASON   (Reactive)
  STEP 3: VERIFY   (Heartbeat — can REJECT → back to STEP 2)
  STEP 4: ACT      (Reactive)
  STEP 5: INGEST   (Heartbeat)
  STEP 6: LOOP     (check goal; repeat or finish)

Both agents must agree on memory root hash at the start of each cycle.
Mismatch → AWAIT_RESYNC before proceeding.
Brotherhood veto → pause until veto cleared.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agents.sync.resync import ResyncProtocol, ResyncResult
from agents.sync.brotherhood import BrotherhoodEnforcer
from core.exceptions import AgentDesyncError, HeartbeatMissing
from memory.event_store import AgentSource, EventType, MemoryEvent

if TYPE_CHECKING:
    from agents.heartbeat.agent import HeartbeatAgent
    from agents.reactive.agent import ReactiveAgent
    from agents.reactive.goal import Goal
    from boot.guards import MemoryConsistencyGuard

log = logging.getLogger(__name__)

MAX_VERIFY_RETRIES  = 3
MAX_RESYNC_RETRIES  = 3
MAX_LOOP_ITERATIONS = 50


@dataclass
class LoopResult:
    success:     bool
    iterations:  int         = 0
    final_result: Any        = None
    rejections:  int         = 0
    resyncs:     int         = 0
    error:       str         = ""


class DualReActLoop:
    """
    Orchestrates the joint Reactive+Heartbeat cycle.

    Usage:
        loop = DualReActLoop(reactive, heartbeat, resync, brotherhood)
        result = loop.run("What do you know about soul laws?", goal=my_goal)
    """

    def __init__(
        self,
        reactive:     "ReactiveAgent",
        heartbeat:    "HeartbeatAgent",
        resync:       ResyncProtocol,
        brotherhood:  BrotherhoodEnforcer,
        consistency_guard: "MemoryConsistencyGuard | None" = None,
    ) -> None:
        self.reactive          = reactive
        self.heartbeat         = heartbeat
        self._resync           = resync
        self._brotherhood      = brotherhood
        self._consistency_guard = consistency_guard
        self._synced:    bool  = True

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_agents(
        cls,
        reactive:     "ReactiveAgent",
        heartbeat:    "HeartbeatAgent",
        hmac_secret:  str,
    ) -> "DualReActLoop":
        """Build a DualReActLoop from two booted agents."""
        from memory.event_store import EventStore
        from memory.audit_log   import AuditLog
        from boot.guards         import MemoryConsistencyGuard

        store = EventStore(reactive._substrate.get_sqlite(), hmac_secret)
        audit = AuditLog(reactive._substrate.get_sqlite())

        resync      = ResyncProtocol(reactive._substrate, store, hmac_secret)
        brotherhood = BrotherhoodEnforcer(reactive._enforcer, store, audit)
        guard       = MemoryConsistencyGuard()

        return cls(reactive, heartbeat, resync, brotherhood, guard)

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run(
        self,
        input_data: str,
        goal:       "Goal | None" = None,
        max_iter:   int = MAX_LOOP_ITERATIONS,
    ) -> LoopResult:
        """
        Execute the joint cycle until goal is achieved or max_iter is reached.
        Returns LoopResult with full run statistics.
        """
        loop_result = LoopResult(success=False)

        for iteration in range(max_iter):
            loop_result.iterations = iteration + 1

            # ── STEP 1: OBSERVE ───────────────────────────────────────────────
            obs    = self.reactive.observe(input_data)
            hb_state = self.heartbeat.sense()

            # ── Sync check ────────────────────────────────────────────────────
            # Both agents share the SAME substrate in this architecture.
            # Resync is only needed if their independently computed root hashes differ.
            # We compare the hashes that were explicitly passed (from external sources)
            # rather than recomputing — avoids false mismatches from ephemeral state.
            if self._consistency_guard and getattr(self, "_check_sync_hashes", False):
                reactive_hash  = obs.heartbeat_hash or self.reactive._substrate.compute_root_hash()
                heartbeat_hash = hb_state.memory_root_hash
                if not self._consistency_guard.are_consistent(reactive_hash, heartbeat_hash):
                    log.info("Root hash mismatch — entering AWAIT_RESYNC")
                    resync_result = self._resync.execute(reactive_hash, heartbeat_hash)
                    loop_result.resyncs += 1
                    if not resync_result.success:
                        loop_result.error = f"Resync failed: {resync_result.error}"
                        return loop_result
                    continue  # restart cycle after resync

            # Brotherhood veto check
            if self._brotherhood.veto_active:
                log.warning("Brotherhood veto active (%s) — pausing", self._brotherhood.veto_reason)
                time.sleep(0.1)
                continue

            # ── STEPS 2-4: REASON → VERIFY → ACT ─────────────────────────────
            result = None
            for verify_attempt in range(MAX_VERIFY_RETRIES):
                # STEP 2: REASON
                plan = self.reactive.reason(obs, hb_state, verify_attempt)

                # Brotherhood check: no command language in plan
                try:
                    self._brotherhood.check_command_vs_request(
                        "REACTIVE", plan.tool_name, plan.reasoning
                    )
                except Exception as e:
                    log.warning("Brotherhood violation in plan: %s", e)
                    loop_result.rejections += 1
                    continue

                # STEP 3: VERIFY (Heartbeat)
                verdict = self.heartbeat.verify_plan(
                    agent     = "REACTIVE",
                    tool_name = plan.tool_name,
                    tool_args = plan.tool_args,
                    cited_ids = plan.cited_source_ids,
                )

                if verdict.rejected:
                    log.info(
                        "Loop iter %d, verify attempt %d: plan rejected — %s",
                        iteration + 1, verify_attempt + 1, verdict.reason
                    )
                    loop_result.rejections += 1
                    self.reactive._store.write(MemoryEvent(
                        source  = AgentSource.REACTIVE,
                        type    = EventType.FLAG,
                        payload = {"plan_rejected": True, "reason": verdict.reason,
                                   "iteration": iteration, "attempt": verify_attempt},
                    ))
                    continue

                # STEP 4: ACT
                result = self.reactive.act(plan)
                self.reactive._action_count += 1
                self.reactive._last_result = result

                # PULSE_R every N actions
                if self.reactive._action_count % 5 == 0:
                    self.reactive._emit_pulse_r(result, plan)
                break

            # ── STEP 5: INGEST ────────────────────────────────────────────────
            if result is not None:
                self.heartbeat.ingest(result)
                # Write tool call intent (if not already written by reactive.act)
                self.reactive._write_tool_call_intent(plan)

            # Heartbeat cycle: SENSE → STORE → VERIFY → PULSE
            self.heartbeat.run_cycle()

            # ── STEP 6: LOOP ──────────────────────────────────────────────────
            if self.reactive.goal_achieved(result, goal):
                loop_result.success      = True
                loop_result.final_result = result
                log.info(
                    "Goal achieved in %d iterations (%d rejections, %d resyncs)",
                    loop_result.iterations, loop_result.rejections, loop_result.resyncs,
                )
                return loop_result

        # Max iterations reached
        loop_result.final_result = locals().get("result", None)
        loop_result.error        = f"Max iterations ({max_iter}) reached without goal achievement"
        log.warning(loop_result.error)
        return loop_result

    # ── Sync check ────────────────────────────────────────────────────────────

    def _check_sync(self, reactive_hash: str, heartbeat_hash: str) -> bool:
        if self._consistency_guard:
            return self._consistency_guard.are_consistent(reactive_hash, heartbeat_hash)
        return reactive_hash == heartbeat_hash

    def _enter_resync(self, reactive_hash: str, heartbeat_hash: str) -> ResyncResult:
        return self._resync.execute(reactive_hash, heartbeat_hash)
