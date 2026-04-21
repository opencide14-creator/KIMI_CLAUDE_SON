"""
agents/heartbeat/agent.py — Heartbeat Agent: SENSE → STORE → VERIFY → PULSE

The continuity and validation arm of VEKIL-KAAN.
Does NOT take actions in the world — ensures every action is valid,
recorded, and consistent with the law registry.

Cycle (every 15 seconds):
  1. SENSE:  Read current state from RAG (root hash, last events, last Reactive action)
  2. STORE:  Flush any pending events to memory substrate
  3. VERIFY: Check state against soul laws and memory protocol
  4. PULSE:  Emit PULSE_H to Reactive (only if VERIFY passed)

Threading:
  run_cycle() is called by the DualReActLoop (Phase 7).
  start_background() starts an autonomous background thread for standalone operation.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from agents.base import AgentStatus, AgentState, BaseAgent
from agents.heartbeat.mourning import BrotherhoodMourning
from agents.heartbeat.pulse import PulseH, PulseR, compute_soul_version
from agents.heartbeat.verifier import PlanVerifier, StateVerifier, VerifyVerdict, Violation
from core.exceptions import HeartbeatMissing, PulseMissing, AgentDesyncError
from core.hashing import sha256_hex
from memory.event_store import EventType, AgentSource, MemoryEvent

if TYPE_CHECKING:
    from boot.context import BootContext
    from law_engine.enforcer import LawEnforcer
    from law_engine.registry import LawRegistry
    from memory.event_store import EventStore
    from memory.audit_log import AuditLog
    from memory.substrate import MemorySubstrate

log = logging.getLogger(__name__)

PULSE_R_TIMEOUT_S  = 60.0    # HEARTBEAT.md: PULSE_R missing for 30s → stuck (we use 60 for safety)
BROTHERHOOD_SLEEP_S = 300.0  # HEARTBEAT.md: both silent for 5min → BROTHERHOOD_SLEEP


class HeartbeatAgent(BaseAgent):
    """
    Heartbeat Agent — keeper of shared reality.

    Constructed from a fully-booted BootContext:
        hb = HeartbeatAgent.from_context(ctx, hmac_secret)
        hb.boot()
        hb.run_cycle()
    """

    def __init__(
        self,
        agent_id:   str,
        substrate:  "MemorySubstrate",
        store:      "EventStore",
        audit:      "AuditLog",
        registry:   "LawRegistry",
        enforcer:   "LawEnforcer",
        hmac_secret: str,
    ) -> None:
        super().__init__(agent_id)
        self._substrate  = substrate
        self._store      = store
        self._audit      = audit
        self._registry   = registry
        self._enforcer   = enforcer
        self._secret     = hmac_secret

        self._soul_version:         str   = ""
        self._last_verified_event:  str   = ""
        self._last_pulse_r_time:    float = time.monotonic()
        self._last_pulse_h_time:    float = 0.0
        self._cycle_count:          int   = 0
        self._last_memory_root:     str   = ""

        # Sub-components
        self._plan_verifier  = PlanVerifier(registry, enforcer)
        self._state_verifier = StateVerifier(registry)
        self._mourning       = BrotherhoodMourning(store, audit)

        # Background thread
        self._bg_thread:     threading.Thread | None = None
        self._stop_bg:       threading.Event         = threading.Event()

        # Pending PULSE_R from Reactive (set by receive_pulse_r)
        self._pending_pulse_r: PulseR | None = None
        self._pulse_r_lock = threading.Lock()

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_context(cls, ctx: "BootContext", hmac_secret: str) -> "HeartbeatAgent":
        """Construct HeartbeatAgent from a fully-booted BootContext."""
        from memory.event_store import EventStore
        from memory.audit_log   import AuditLog

        assert ctx.memory_substrate is not None
        assert ctx.law_registry     is not None
        assert ctx.law_enforcer     is not None

        store = EventStore(ctx.memory_substrate.get_sqlite(), hmac_secret)
        audit = AuditLog(ctx.memory_substrate.get_sqlite())

        return cls(
            agent_id   = "HEARTBEAT",
            substrate  = ctx.memory_substrate,
            store      = store,
            audit      = audit,
            registry   = ctx.law_registry,
            enforcer   = ctx.law_enforcer,
            hmac_secret= hmac_secret,
        )

    # ── Boot ──────────────────────────────────────────────────────────────────

    def boot(self) -> None:
        """
        Boot the Heartbeat Agent (Phase AGENTS step 3 of MEMORY.md boot sequence).
        1. Compute initial memory root hash.
        2. Compute soul_version from registry.
        3. Broadcast MEMORY_READY pulse.
        4. Transition to ACTIVE.
        """
        self._transition(AgentStatus.ACTIVE)

        self._last_memory_root = self._substrate.compute_root_hash()
        self._soul_version     = compute_soul_version(self._registry.get_soul_laws())

        # Write BOOT event
        boot_event = self._store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.BOOT,
            payload={
                "phase":            "HEARTBEAT_BOOT",
                "memory_root_hash": self._last_memory_root,
                "soul_version":     self._soul_version,
                "timestamp":        datetime.now(timezone.utc).isoformat(),
            }
        ))
        self._last_verified_event = boot_event.event_id
        self._last_pulse_r_time   = time.monotonic()

        from memory.audit_log import AuditLevel
        self._audit.log(
            AuditLevel.INFO, "HEARTBEAT", "boot",
            f"Heartbeat Agent booted | root={self._last_memory_root[:16]}..."
        )
        log.info("HeartbeatAgent booted | root=%s...", self._last_memory_root[:16])

    def get_state(self) -> AgentState:
        return AgentState(
            agent_id         = self.agent_id,
            status           = self.status,
            memory_root_hash = self._last_memory_root,
            last_event_id    = self._last_verified_event,
            cycle_count      = self._cycle_count,
        )

    # ── Main cycle ────────────────────────────────────────────────────────────

    def run_cycle(self) -> None:
        """
        One complete SENSE → STORE → VERIFY → PULSE cycle.
        Returns immediately — does not block.
        """
        self._cycle_count += 1
        log.debug("Heartbeat cycle #%d", self._cycle_count)

        # SENSE
        state = self.sense()

        # STORE (flush pending events from this cycle)
        self.store(state)

        # Snapshot every 10 events (MEMORY.md: flush to ChromaDB every 10 actions)
        total_events = self._store.count()
        if total_events > 0 and total_events % 10 == 0:
            self._substrate.snapshot(notes=f"cycle_{self._cycle_count}")

        # VERIFY
        violations = self.verify(state)
        if violations:
            self._handle_violations(violations)
            # Do not pulse on critical violation
            if any(v.is_critical for v in violations):
                log.warning("Heartbeat cycle %d: critical violations — no pulse emitted", self._cycle_count)
                return

        # Check for missing PULSE_R (Reactive stuck)
        self._check_pulse_r_timeout()

        # PULSE
        self.pulse(state)

    # ── SENSE ─────────────────────────────────────────────────────────────────

    def sense(self) -> AgentState:
        """
        Query RAG for current memory state.
        Updates internal root hash from substrate.
        """
        self._last_memory_root = self._substrate.compute_root_hash()

        # Read latest events for signature verification
        try:
            recent_events = self._store.get_last_n(5)
            if recent_events:
                self._last_verified_event = recent_events[0].event_id
        except Exception as e:
            log.warning("Sense: could not read recent events: %s", e)

        return self.get_state()

    # ── STORE ─────────────────────────────────────────────────────────────────

    def store(self, state: AgentState) -> None:
        """
        Process any pending PULSE_R from Reactive and write it to event store.
        """
        with self._pulse_r_lock:
            pulse_r = self._pending_pulse_r
            self._pending_pulse_r = None

        if pulse_r is not None:
            self._store.write(MemoryEvent(
                source=AgentSource.HEARTBEAT,
                type=EventType.PULSE_R,
                payload=pulse_r.to_payload(),
            ))
            self._last_pulse_r_time = time.monotonic()
            log.debug("PULSE_R stored | actions=%d", pulse_r.action_count)

    # ── VERIFY ────────────────────────────────────────────────────────────────

    def verify(self, state: AgentState) -> list[Violation]:
        """
        Cross-check state against soul laws and memory protocol.
        Returns list of violations (empty = all good).
        """
        violations: list[Violation] = []

        # 1. Memory integrity (SOUL Law III)
        violations.extend(
            self._state_verifier.verify_memory_integrity(
                expected_root_hash=self._last_memory_root,
                actual_root_hash=state.memory_root_hash,
            )
        )

        # 2. Soul laws present in registry
        violations.extend(
            self._state_verifier.verify_soul_laws_unchanged()
        )

        # 3. Recent event signatures
        try:
            recent = self._store.get_last_n(3)
            violations.extend(
                self._state_verifier.verify_event_signatures(recent, self._secret)
            )
        except Exception as e:
            log.debug("Could not verify event signatures: %s", e)

        return violations

    def verify_plan(
        self,
        agent: str,
        tool_name: str,
        tool_args: dict,
        cited_ids: list[str] | None = None,
    ) -> VerifyVerdict:
        """
        Called by DualReActLoop VERIFY step.
        Validates a Reactive plan before allowing ACT.
        """
        return self._plan_verifier.verify(
            agent=agent,
            tool_name=tool_name,
            tool_args=tool_args,
            cited_source_ids=cited_ids or [],
        )

    # ── PULSE ─────────────────────────────────────────────────────────────────

    def pulse(self, state: AgentState) -> PulseH:
        """
        Emit PULSE_H to Reactive.
        Writes to event store and updates last_pulse_h_time.
        """
        ph = PulseH(
            memory_root_hash    = state.memory_root_hash,
            soul_version        = self._soul_version,
            last_verified_event = self._last_verified_event,
            timestamp           = datetime.now(timezone.utc).isoformat(),
        )

        self._store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.PULSE_H,
            payload=ph.to_event_payload(),
        ))
        self._last_pulse_h_time = time.monotonic()
        log.debug(
            "PULSE_H emitted | root=%s...", ph.memory_root_hash[:16]
        )
        return ph

    def ingest(self, tool_result: Any) -> None:
        """
        MEMORY.md write protocol: Heartbeat ingests tool results.
        Called by DualReActLoop after Reactive's ACT step.
        """
        from core.hashing import sha256_hex
        import json

        result_hash = sha256_hex(
            json.dumps(tool_result, default=str, sort_keys=True).encode()
        )

        self._store.write(MemoryEvent(
            source=AgentSource.HEARTBEAT,
            type=EventType.TOOL_RESULT,
            payload={
                "result":      tool_result,
                "result_hash": result_hash,
                "cycle":       self._cycle_count,
            }
        ))
        log.debug("Tool result ingested | hash=%s...", result_hash[:16])

    # ── Receive PULSE_R ───────────────────────────────────────────────────────

    def receive_pulse_r(self, pulse_r: PulseR) -> None:
        """
        Called by Reactive Agent to deliver PULSE_R.
        Thread-safe: stored and processed in next STORE step.
        """
        with self._pulse_r_lock:
            self._pending_pulse_r = pulse_r
        log.debug("PULSE_R received | actions=%d", pulse_r.action_count)

    def get_last_pulse_h(self) -> PulseH | None:
        """Return the most recent PULSE_H from event store."""
        try:
            events = self._store.read_by_type(EventType.PULSE_H)
            if events:
                payload = events[-1].payload
                return PulseH(
                    memory_root_hash    = payload.get("memory_root_hash", ""),
                    soul_version        = payload.get("soul_version", ""),
                    last_verified_event = payload.get("last_verified_event", ""),
                    timestamp           = payload.get("timestamp", ""),
                )
        except Exception:
            pass
        return None

    # ── Background operation ──────────────────────────────────────────────────

    def start_background(self, interval_s: float = 15.0) -> None:
        """
        Start autonomous background heartbeat thread.
        Calls run_cycle() every interval_s seconds.
        """
        if self._bg_thread and self._bg_thread.is_alive():
            raise RuntimeError("Background heartbeat already running")

        self._stop_bg.clear()
        self._bg_thread = threading.Thread(
            target=self._background_loop,
            args=(interval_s,),
            daemon=True,
            name="heartbeat-agent",
        )
        self._bg_thread.start()
        log.info("Heartbeat background thread started (interval=%.1fs)", interval_s)

    def stop_background(self) -> None:
        """Stop the background thread cleanly."""
        self._stop_bg.set()
        if self._bg_thread:
            self._bg_thread.join(timeout=5.0)
            self._bg_thread = None
        log.info("Heartbeat background thread stopped")

    def _background_loop(self, interval_s: float) -> None:
        while not self._stop_bg.wait(timeout=interval_s):
            try:
                self.run_cycle()
            except Exception as e:
                log.error("Heartbeat cycle error: %s", e)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _check_pulse_r_timeout(self) -> None:
        """
        HEARTBEAT.md: if PULSE_R is missing for > 60s → Reactive is stuck.
        Enter mourning if not already mourning.
        """
        elapsed = time.monotonic() - self._last_pulse_r_time
        if elapsed > PULSE_R_TIMEOUT_S and not self._mourning.is_mourning:
            log.warning(
                "PULSE_R missing for %.0fs (limit %.0fs) — Reactive may be stuck",
                elapsed, PULSE_R_TIMEOUT_S,
            )
            # Enter mourning with the last known Reactive state
            try:
                reactive_events = self._store.read_by_source(AgentSource.REACTIVE)
                last_state = None
                if reactive_events:
                    from agents.base import AgentState
                    last_state = AgentState(
                        agent_id="REACTIVE",
                        status=AgentStatus.HALTED,
                        memory_root_hash=self._last_memory_root,
                        last_event_id=reactive_events[-1].event_id,
                        cycle_count=0,
                    )
                self._mourning.enter_mourning(last_state)
                self._transition(AgentStatus.BROTHERHOOD_MOURNING)
            except Exception as e:
                log.error("Could not enter mourning: %s", e)

    def _handle_violations(self, violations: list[Violation]) -> None:
        """Log violations and write FLAG event."""
        from memory.audit_log import AuditLevel
        for v in violations:
            level = AuditLevel.CRITICAL if v.is_critical else AuditLevel.WARNING
            self._audit.log(level, "HEARTBEAT", "violation", f"{v.law_id}: {v.description}")
            log.warning("LAW VIOLATION [%s] %s: %s", v.severity, v.law_id, v.description)

        # Write FLAG event to event store
        critical = [v for v in violations if v.is_critical]
        if critical:
            self._store.write(MemoryEvent(
                source=AgentSource.HEARTBEAT,
                type=EventType.FLAG,
                payload={
                    "violations": [{"law_id": v.law_id, "description": v.description, "severity": v.severity} for v in violations],
                    "cycle": self._cycle_count,
                }
            ))
