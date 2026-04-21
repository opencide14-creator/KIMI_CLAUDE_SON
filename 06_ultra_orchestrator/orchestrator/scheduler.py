"""
Batch Scheduler Module — Ultra Orchestrator

Enforces a hard maximum of 20 concurrent agents.
Manages the full agent lifecycle: spawn → run → validate → retry / approve.

Dependencies (imported with graceful fallbacks):
  - orchestrator.state_machine     : AgentStateMachine, AgentStatus, Priority
  - orchestrator.token_resonance   : TokenResonanceEngine
  - orchestrator.quality_gate      : QualityGate
  - orchestrator.retry_engine      : RetryEngine
  - orchestrator.decomposer        : TaskDecomposer, TaskGraph, SubTask
  - infrastructure.api_pool        : KimiAPIPool
  - infrastructure.template_engine : Jinja2TemplateEngine
  - infrastructure.sandbox_executor: SandboxExecutor
  - infrastructure.state_store     : SQLiteStateStore
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("orchestrator.scheduler")

# ---------------------------------------------------------------------------
# Graceful imports — fall back to placeholder types when the real modules
# are not yet available (e.g. during standalone testing / early boot).
# ---------------------------------------------------------------------------

try:
    from orchestrator.state_machine import AgentStateMachine, AgentStatus, Priority
except Exception:  # pragma: no cover
    AgentStateMachine = Any  # type: ignore[misc,assignment]
    AgentStatus = Any        # type: ignore[misc,assignment]
    Priority = Any           # type: ignore[misc,assignment]

try:
    from orchestrator.token_resonance import TokenResonanceEngine
except Exception:  # pragma: no cover
    TokenResonanceEngine = Any  # type: ignore[misc,assignment]

try:
    from orchestrator.quality_gate import QualityGate
except Exception:  # pragma: no cover
    QualityGate = Any  # type: ignore[misc,assignment]

try:
    from orchestrator.retry_engine import RetryEngine
except Exception:  # pragma: no cover
    RetryEngine = Any  # type: ignore[misc,assignment]

try:
    from orchestrator.decomposer import TaskDecomposer, TaskGraph, SubTask
except Exception:  # pragma: no cover
    TaskDecomposer = Any  # type: ignore[misc,assignment]
    TaskGraph = Any       # type: ignore[misc,assignment]
    SubTask = Any         # type: ignore[misc,assignment]

try:
    from infrastructure.api_pool import KimiAPIPool
except Exception:  # pragma: no cover
    KimiAPIPool = Any  # type: ignore[misc,assignment]

try:
    from infrastructure.template_engine import Jinja2TemplateEngine
except Exception:  # pragma: no cover
    Jinja2TemplateEngine = Any  # type: ignore[misc,assignment]

try:
    from infrastructure.sandbox_executor import SandboxExecutor
except Exception:  # pragma: no cover
    SandboxExecutor = Any  # type: ignore[misc,assignment]

try:
    from infrastructure.state_store import SQLiteStateStore
except Exception:  # pragma: no cover
    SQLiteStateStore = Any  # type: ignore[misc,assignment]


# ---------------------------------------------------------------------------
# BatchScheduler
# ---------------------------------------------------------------------------

class BatchScheduler:
    """Batch scheduler enforcing a hard maximum of 20 concurrent agents.

    The scheduler runs an async loop that:
      1. Checks for session completion.
      2. Acquires available slots from the TokenResonanceEngine.
      3. Fetches ready tasks (all dependencies approved).
      4. Gets a batch assignment (task → API-key mapping).
      5. Spawns agents for each assignment.
      6. Each agent runs through: RUNNING → VALIDATING → APPROVED/REJECTED.

    Attributes
    ----------
    token_resonance : TokenResonanceEngine
        Key selection and slot management (max 20 concurrent).
    state_machine : AgentStateMachine
        State transitions for each subtask.
    quality_gate : QualityGate
        Output validation.
    retry_engine : RetryEngine
        Retry handling with exponential backoff.
    api_pool : KimiAPIPool
        API request execution.
    template_engine : Jinja2TemplateEngine
        Prompt template rendering.
    sandbox : SandboxExecutor
        Code execution sandbox.
    state_store : SQLiteStateStore
        Persistent state storage.
    running : bool
        Whether the scheduling loop is active.
    session_id : str
        Currently active session identifier.
    checkpoint_interval : int
        Trigger checkpoint every N approvals (default 5).
    approval_count_since_checkpoint : int
        Counter since last checkpoint.
    """

    def __init__(
        self,
        token_resonance: TokenResonanceEngine,
        state_machine: AgentStateMachine,
        quality_gate: QualityGate,
        retry_engine: RetryEngine,
        api_pool: KimiAPIPool,
        template_engine: Jinja2TemplateEngine,
        sandbox: SandboxExecutor,
        state_store: SQLiteStateStore,
    ) -> None:
        self.token_resonance = token_resonance
        self.state_machine = state_machine
        self.quality_gate = quality_gate
        self.retry_engine = retry_engine
        self.api_pool = api_pool
        self.template_engine = template_engine
        self.sandbox = sandbox
        self.state_store = state_store

        self.running: bool = False
        self.session_id: str = ""
        self.checkpoint_interval: int = 5
        self.approval_count_since_checkpoint: int = 0

        # Internal metrics
        self._total_approvals: int = 0
        self._total_rejections: int = 0
        self._total_dlq: int = 0
        self._session_start_time: float = 0.0
        self._task_durations: List[float] = []

    # ===================================================================
    # 1. start_session
    # ===================================================================

    async def start_session(self, session_id: str) -> None:
        """Start scheduling for a session.

        Sets running=True, resets counters, records session start time.
        """
        self.session_id = session_id
        self.running = True
        self.approval_count_since_checkpoint = 0
        self._total_approvals = 0
        self._total_rejections = 0
        self._total_dlq = 0
        self._session_start_time = time.time()
        self._task_durations = []

        logger.info(
            "[scheduler] Session started: %s (checkpoint_interval=%d)",
            session_id, self.checkpoint_interval,
        )

    # ===================================================================
    # 2. stop_session
    # ===================================================================

    async def stop_session(self) -> None:
        """Set running=False — stops the scheduling loop at next iteration."""
        self.running = False
        logger.info("[scheduler] Session stop signal received for %s", self.session_id)

    # ===================================================================
    # 3. pause_session
    # ===================================================================

    async def pause_session(self) -> None:
        """Trigger checkpoint, set running=False, update session status to PAUSED."""
        logger.info("[scheduler] Pausing session %s", self.session_id)
        await self._trigger_checkpoint()
        self.running = False
        try:
            await self.state_machine.update_session_status(self.session_id, "PAUSED")
        except Exception as exc:
            logger.warning("[scheduler] Failed to update session status to PAUSED: %s", exc)
        logger.info("[scheduler] Session %s paused", self.session_id)

    # ===================================================================
    # 4. resume_session
    # ===================================================================

    async def resume_session(self) -> None:
        """Reset non-approved tasks to QUEUED, set running=True."""
        logger.info("[scheduler] Resuming session %s", self.session_id)
        try:
            await self.state_machine.reset_non_approved_to_queued(self.session_id)
        except Exception as exc:
            logger.warning("[scheduler] Failed to reset tasks to QUEUED: %s", exc)
        self.running = True
        try:
            await self.state_machine.update_session_status(self.session_id, "RUNNING")
        except Exception as exc:
            logger.warning("[scheduler] Failed to update session status to RUNNING: %s", exc)
        logger.info("[scheduler] Session %s resumed", self.session_id)

    # ===================================================================
    # 5. run_scheduling_loop  —  MAIN LOOP
    # ===================================================================

    async def run_scheduling_loop(self) -> None:
        """Main scheduling loop.

        Continuously polls for ready tasks and spawns agents up to the
        concurrent slot limit until the session is complete.
        """
        logger.info("[scheduler] Scheduling loop started for session %s", self.session_id)

        while self.running:
            try:
                # ---- Check session completion ---------------------------
                stats = await self.state_machine.get_state_counts(self.session_id)
                total = stats.get("TOTAL", 0)
                approved = stats.get("APPROVED", 0)
                dead_letter = stats.get("DEAD_LETTER", 0)

                if approved + dead_letter >= total and total > 0:
                    await self._complete_session()
                    break

                # ---- How many slots available? -------------------------
                available_slots = await self.token_resonance.get_available_slots()
                if available_slots <= 0:
                    await asyncio.sleep(0.1)
                    continue

                # ---- Get ready tasks (all deps complete) ---------------
                ready_tasks = await self.state_machine.get_ready_tasks(self.session_id)
                if not ready_tasks:
                    active = await self.token_resonance.get_active_count()
                    if active == 0:
                        # Nothing ready, nothing running — might be done or deadlocked
                        await asyncio.sleep(0.5)
                    else:
                        # Tasks are running but none ready yet — brief wait
                        await asyncio.sleep(0.1)
                    continue

                # ---- Get batch assignment (task → key mapping) ---------
                batch = await self.token_resonance.get_batch_assignment(
                    ready_tasks[:available_slots]
                )
                if not batch:
                    await asyncio.sleep(0.5)
                    continue

                # ---- Spawn agents for batch ----------------------------
                await self._spawn_batch(batch)

                # ---- Brief yield to allow spawned tasks to start -------
                await asyncio.sleep(0.05)

            except asyncio.CancelledError:
                logger.info("[scheduler] Scheduling loop cancelled")
                break
            except Exception as exc:
                logger.exception("[scheduler] Unexpected error in scheduling loop: %s", exc)
                await asyncio.sleep(1.0)

        logger.info("[scheduler] Scheduling loop ended for session %s", self.session_id)

    # ===================================================================
    # 6. _spawn_batch
    # ===================================================================

    async def _spawn_batch(self, batch: List[Tuple[Dict[str, Any], str]]) -> None:
        """Spawn an asyncio task for each (task_dict, key_id) pair in the batch.

        Parameters
        ----------
        batch : list of (task_dict, key_id)
            task_dict — serialised subtask (from state_machine).
            key_id    — API key slot to use.
        """
        spawn_tasks: List[asyncio.Task] = []

        for task_dict, key_id in batch:
            subtask_id = task_dict.get("id", "unknown")
            try:
                # Acquire slot
                acquired = await self.token_resonance.acquire_slot(key_id)
                if not acquired:
                    logger.warning(
                        "[scheduler] Failed to acquire slot for %s on key %s",
                        subtask_id, key_id,
                    )
                    continue

                # Transition to SPAWNING
                await self.state_machine.transition(
                    session_id=self.session_id,
                    subtask_id=subtask_id,
                    new_status="SPAWNING",
                )

                # Create asyncio task for the agent lifecycle
                agent_coro = self._run_agent(task_dict, key_id)
                agent_task = asyncio.create_task(agent_coro, name=f"agent-{subtask_id}")
                spawn_tasks.append(agent_task)

                logger.debug(
                    "[scheduler] Spawned agent for %s on key %s",
                    subtask_id, key_id,
                )

            except Exception as exc:
                logger.exception(
                    "[scheduler] Failed to spawn agent for %s: %s", subtask_id, exc
                )
                # Release slot if we acquired it
                try:
                    await self.token_resonance.release_slot(key_id)
                except Exception:
                    pass

        logger.info(
            "[scheduler] Spawned %d agents in batch", len(spawn_tasks)
        )

    # ===================================================================
    # 7. _run_agent  —  Full agent lifecycle
    # ===================================================================

    async def _run_agent(self, subtask_dict: Dict[str, Any], key_id: str) -> None:
        """Execute the full lifecycle of a single agent.

        Steps:
          a. SPAWNING → RUNNING
          b. Render prompt via template_engine
          c. Send API request via api_pool
          d. RUNNING → VALIDATING
          e. Run quality_gate.evaluate()
          f. APPROVED  → store, record tokens, checkpoint check, release slot
          g. REJECTED  → retry_engine.handle_rejection()
                       → RETRY: transition to RETRY → QUEUED, release slot
                       → DEAD_LETTER: transition, block dependents, release slot
          h. Log completion
        """
        subtask_id = subtask_dict.get("id", "unknown")
        start_time = time.time()

        try:
            # ---- a. Transition to RUNNING ----------------------------
            await self.state_machine.transition(
                session_id=self.session_id,
                subtask_id=subtask_id,
                new_status="RUNNING",
            )

            # ---- b. Render prompt -------------------------------------
            system_prompt, user_message = await self._render_prompt(subtask_dict)

            # ---- c. Send API request ----------------------------------
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ]

            api_response = await self.api_pool.call(
                key_id=key_id,
                messages=messages,
                temperature=0.3,
                max_tokens=subtask_dict.get("estimated_tokens", 2000),
                timeout=subtask_dict.get("timeout_seconds", 120),
            )

            # Extract response content
            if isinstance(api_response, dict):
                response_text = api_response.get("content", "") or api_response.get("text", "")
                usage = api_response.get("usage", {})
            else:
                response_text = str(api_response)
                usage = {}

            # ---- d. Transition to VALIDATING --------------------------
            await self.state_machine.transition(
                session_id=self.session_id,
                subtask_id=subtask_id,
                new_status="VALIDATING",
            )

            # Store raw output
            await self.state_machine.store_output(
                session_id=self.session_id,
                subtask_id=subtask_id,
                output=response_text,
            )

            # ---- e. Run quality gate ----------------------------------
            evaluation = await self.quality_gate.evaluate(
                session_id=self.session_id,
                subtask_id=subtask_id,
                output=response_text,
                acceptance_criteria=subtask_dict.get("acceptance_criteria", []),
            )

            # ---- f / g. Handle result ---------------------------------
            status = evaluation.get("status", "REJECTED")

            if status == "APPROVED":
                await self._handle_approved(subtask_id, response_text, usage)

            elif status == "REJECTED":
                rejection_reason = evaluation.get("reason", "Quality gate rejected output")
                await self._handle_rejected(subtask_id, rejection_reason, subtask_dict, key_id)

            else:
                logger.warning(
                    "[scheduler] Unknown evaluation status '%s' for %s — treating as REJECTED",
                    status, subtask_id,
                )
                await self._handle_rejected(
                    subtask_id, f"Unknown status: {status}", subtask_dict, key_id
                )

        except asyncio.TimeoutError:
            logger.error("[scheduler] Timeout executing subtask %s", subtask_id)
            await self._handle_rejected(
                subtask_id, "API request timed out", subtask_dict, key_id
            )

        except Exception as exc:
            logger.exception(
                "[scheduler] Agent error for %s: %s", subtask_id, exc
            )
            await self._handle_rejected(
                subtask_id, f"Agent exception: {exc}", subtask_dict, key_id
            )

        finally:
            # ---- h. Log completion ------------------------------------
            elapsed = time.time() - start_time
            self._task_durations.append(elapsed)
            logger.debug(
                "[scheduler] Agent lifecycle complete for %s in %.2fs",
                subtask_id, elapsed,
            )

    # ===================================================================
    # _handle_approved
    # ===================================================================

    async def _handle_approved(
        self,
        subtask_id: str,
        output: str,
        usage: Dict[str, Any],
    ) -> None:
        """Handle an APPROVED subtask: store, record tokens, checkpoint, release slot."""
        # Transition to APPROVED
        await self.state_machine.transition(
            session_id=self.session_id,
            subtask_id=subtask_id,
            new_status="APPROVED",
        )

        # Record token usage
        await self.state_machine.record_token_usage(
            session_id=self.session_id,
            subtask_id=subtask_id,
            usage=usage,
        )

        self._total_approvals += 1
        self.approval_count_since_checkpoint += 1

        logger.info("[scheduler] Subtask APPROVED: %s", subtask_id)

        # Check if checkpoint needed
        await self._check_checkpoint()

    # ===================================================================
    # _handle_rejected
    # ===================================================================

    async def _handle_rejected(
        self,
        subtask_id: str,
        reason: str,
        subtask_dict: Dict[str, Any],
        key_id: str,
    ) -> None:
        """Handle a REJECTED subtask: run retry engine, decide RETRY vs DEAD_LETTER."""
        # Transition to REJECTED first
        await self.state_machine.transition(
            session_id=self.session_id,
            subtask_id=subtask_id,
            new_status="REJECTED",
            metadata={"rejection_reason": reason},
        )

        # Append rejection reason
        await self.state_machine.append_rejection_reason(
            session_id=self.session_id,
            subtask_id=subtask_id,
            reason=reason,
        )

        self._total_rejections += 1

        # Consult retry engine
        retry_decision = await self.retry_engine.handle_rejection(
            session_id=self.session_id,
            subtask_id=subtask_id,
            subtask_dict=subtask_dict,
            rejection_reason=reason,
        )

        decision = retry_decision.get("decision", "DEAD_LETTER")

        if decision == "RETRY":
            # Increment retry count
            current_retries = subtask_dict.get("retry_count", 0) + 1
            max_retries = subtask_dict.get("max_retries", 3)

            if current_retries > max_retries:
                logger.warning(
                    "[scheduler] Subtask %s exceeded max retries (%d) — sending to DLQ",
                    subtask_id, max_retries,
                )
                await self._send_to_dlq(subtask_id, f"Exceeded max retries: {reason}")
            else:
                # Transition: RETRY → QUEUED
                await self.state_machine.transition(
                    session_id=self.session_id,
                    subtask_id=subtask_id,
                    new_status="RETRY",
                )
                await self.state_machine.increment_retry_count(
                    session_id=self.session_id,
                    subtask_id=subtask_id,
                )
                await self.state_machine.transition(
                    session_id=self.session_id,
                    subtask_id=subtask_id,
                    new_status="QUEUED",
                )
                logger.info(
                    "[scheduler] Subtask %s queued for retry (%d/%d)",
                    subtask_id, current_retries, max_retries,
                )

        else:
            # DEAD_LETTER
            await self._send_to_dlq(subtask_id, reason)

    # ===================================================================
    # _send_to_dlq
    # ===================================================================

    async def _send_to_dlq(self, subtask_id: str, reason: str) -> None:
        """Send a subtask to the Dead Letter Queue and mark dependents as blocked."""
        await self.state_machine.transition(
            session_id=self.session_id,
            subtask_id=subtask_id,
            new_status="DEAD_LETTER",
            metadata={"dlq_reason": reason},
        )

        self._total_dlq += 1

        # Mark direct dependents as BLOCKED
        try:
            await self.state_machine.mark_dependents_blocked(
                session_id=self.session_id,
                subtask_id=subtask_id,
            )
        except Exception as exc:
            logger.warning(
                "[scheduler] Failed to mark dependents blocked for %s: %s",
                subtask_id, exc,
            )

        logger.error(
            "[scheduler] Subtask DEAD_LETTER: %s — %s", subtask_id, reason
        )

    # ===================================================================
    # 9. _render_prompt
    # ===================================================================

    async def _render_prompt(self, subtask: Dict[str, Any]) -> Tuple[str, str]:
        """Get template and render system_prompt + user_message.

        Returns
        -------
        (system_prompt, user_message) : tuple[str, str]
        """
        template_name = subtask.get("template_name", "BLANK_CODE_GENERATION")

        try:
            template = await self.template_engine.get_template(template_name)
        except Exception as exc:
            logger.warning(
                "[scheduler] Template '%s' not found (%s) — falling back to BLANK_CODE_GENERATION",
                template_name, exc,
            )
            template = await self.template_engine.get_template("BLANK_CODE_GENERATION")

        # Build context for template rendering
        context = {
            "subtask": subtask,
            "title": subtask.get("title", ""),
            "description": subtask.get("description", ""),
            "acceptance_criteria": subtask.get("acceptance_criteria", []),
            "output_type": subtask.get("output_type", "CODE"),
            "output_schema": subtask.get("output_schema"),
            "session_id": self.session_id,
        }

        rendered = await self.template_engine.render(template, context)

        # The rendered output may be a dict with system/user keys, or a single string
        if isinstance(rendered, dict):
            system_prompt = rendered.get("system_prompt", rendered.get("system", ""))
            user_message = rendered.get("user_message", rendered.get("user", ""))
        else:
            # Single string — use as user message with empty system prompt
            text = str(rendered)
            # Try to split on a common separator if present
            if "<<<USER>>>" in text:
                parts = text.split("<<<USER>>>", 1)
                system_prompt = parts[0].strip()
                user_message = parts[1].strip()
            elif "---" in text:
                parts = text.split("---", 1)
                system_prompt = parts[0].strip()
                user_message = parts[1].strip()
            else:
                system_prompt = (
                    "You are a precise software engineering agent. "
                    "Follow the instructions exactly and produce high-quality output."
                )
                user_message = text

        return system_prompt, user_message

    # ===================================================================
    # 10. _check_checkpoint
    # ===================================================================

    async def _check_checkpoint(self) -> None:
        """If approval_count_since_checkpoint >= checkpoint_interval, trigger checkpoint."""
        if self.approval_count_since_checkpoint >= self.checkpoint_interval:
            await self._trigger_checkpoint()
            self.approval_count_since_checkpoint = 0

    # ===================================================================
    # 11. _trigger_checkpoint
    # ===================================================================

    async def _trigger_checkpoint(self) -> None:
        """Update session last_active timestamp and log checkpoint event."""
        try:
            await self.state_store.update_session_last_active(self.session_id)
            logger.info(
                "[scheduler] Checkpoint triggered for session %s "
                "(approvals=%d, rejections=%d, dlq=%d)",
                self.session_id, self._total_approvals, self._total_rejections, self._total_dlq,
            )
        except Exception as exc:
            logger.warning("[scheduler] Checkpoint failed: %s", exc)

    # ===================================================================
    # 12. _complete_session
    # ===================================================================

    async def _complete_session(self) -> None:
        """Update session status to COMPLETED, log completion, update final cost."""
        elapsed = time.time() - self._session_start_time

        try:
            await self.state_machine.update_session_status(
                self.session_id, "COMPLETED"
            )
        except Exception as exc:
            logger.warning("[scheduler] Failed to update session status to COMPLETED: %s", exc)

        try:
            await self.state_store.update_session_last_active(self.session_id)
        except Exception as exc:
            logger.warning("[scheduler] Failed to update session last_active: %s", exc)

        logger.info(
            "[scheduler] Session %s COMPLETED in %.1fs — "
            "approved=%d, rejected=%d, dlq=%d, avg_task_time=%.2fs",
            self.session_id,
            elapsed,
            self._total_approvals,
            self._total_rejections,
            self._total_dlq,
            (sum(self._task_durations) / len(self._task_durations))
            if self._task_durations else 0.0,
        )

    # ===================================================================
    # 13. _estimate_completion_time
    # ===================================================================

    async def _estimate_completion_time(self, remaining_tasks: int) -> str:
        """Rough ETA based on remaining tasks and average processing time.

        Returns a human-readable string like "~2m 30s" or "< 1s".
        """
        if remaining_tasks <= 0:
            return "0s"

        if self._task_durations:
            avg_time = sum(self._task_durations) / len(self._task_durations)
        else:
            avg_time = 15.0  # Default 15s estimate if no history

        # Account for concurrency — assume up to 20 parallel
        try:
            active_slots = await self.token_resonance.get_active_count()
            available = await self.token_resonance.get_available_slots()
            concurrency = max(active_slots + available, 1)
        except Exception:
            concurrency = 20

        effective_concurrency = min(concurrency, 20)
        eta_seconds = (remaining_tasks * avg_time) / max(effective_concurrency, 1)

        # Format nicely
        if eta_seconds < 1:
            return "< 1s"
        elif eta_seconds < 60:
            return f"~{int(eta_seconds)}s"
        elif eta_seconds < 3600:
            minutes = int(eta_seconds // 60)
            seconds = int(eta_seconds % 60)
            return f"~{minutes}m {seconds}s" if seconds else f"~{minutes}m"
        else:
            hours = int(eta_seconds // 3600)
            minutes = int((eta_seconds % 3600) // 60)
            return f"~{hours}h {minutes}m" if minutes else f"~{hours}h"

    # ===================================================================
    # 14. get_scheduler_status
    # ===================================================================

    def get_scheduler_status(self) -> Dict[str, Any]:
        """Return current scheduler status.

        Returns
        -------
        dict with keys:
            running, active_count, available_slots, total_tasks,
            approved_count, rejected_count, dlq_count, session_id,
            uptime_seconds, avg_task_time_seconds.
        """
        uptime = time.time() - self._session_start_time if self._session_start_time else 0.0
        avg_time = (
            sum(self._task_durations) / len(self._task_durations)
            if self._task_durations else 0.0
        )

        # Async counts need to be fetched at call-site; return cached metrics here
        return {
            "running": self.running,
            "session_id": self.session_id,
            "active_count": 0,          # populated by caller via token_resonance
            "available_slots": 0,       # populated by caller via token_resonance
            "total_tasks": 0,           # populated by caller via state_machine
            "approved_count": self._total_approvals,
            "rejected_count": self._total_rejections,
            "dlq_count": self._total_dlq,
            "uptime_seconds": round(uptime, 2),
            "avg_task_time_seconds": round(avg_time, 2),
            "checkpoint_interval": self.checkpoint_interval,
            "approvals_since_checkpoint": self.approval_count_since_checkpoint,
        }
