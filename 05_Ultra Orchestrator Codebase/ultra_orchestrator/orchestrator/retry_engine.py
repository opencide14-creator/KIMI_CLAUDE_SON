"""
RetryEngine — handles rejected outputs with escalating retry strategies.

This module is the central retry-and-escalation component of the Ultra
Orchestrator. When a subtask's output fails quality-gate validation, the
engine:

1. Decides whether to retry (based on ``max_retries``).
2. Builds an escalating, context-aware retry prompt.
3. Persists the revised context via :class:`SQLiteStateStore`.
4. If retries are exhausted, dead-letters the subtask and blocks all
dependents.

Typical usage::

    engine = RetryEngine(state_store, template_engine, max_retries=3)
    result = await engine.handle_rejection(
        subtask_id="st-42",
        output=output_text,
        qg_result={"failed_layer": 1, "reason": "Contains 'pass' statements"},
        subtask=subtask_dict,
    )
"""

from __future__ import annotations

import logging
import time
from typing import Any

from infrastructure.state_store import SQLiteStateStore
from infrastructure.template_engine import Jinja2TemplateEngine

logger = logging.getLogger("ultra_orchestrator.retry_engine")

# ---------------------------------------------------------------------------
# Layer-to-name mapping used in log messages and prompts
# ---------------------------------------------------------------------------

_LAYER_NAMES: dict[int, str] = {
    0: "STRUCTURAL",
    1: "STATIC_ANALYSIS",
    2: "ACCEPTANCE_CRITERIA",
    3: "EXECUTION",
    4: "DIVERSITY",
}


# ---------------------------------------------------------------------------
# RetryEngine
# ---------------------------------------------------------------------------


class RetryEngine:
    """Handles rejected outputs with escalating retry strategies.

    Attributes
    ----------
    max_retries:
        Maximum number of retry attempts before a subtask is sent to the
        dead-letter queue.  Defaults to ``3``.
    state_store:
        Reference to the :class:`SQLiteStateStore` used for persistence.
    template_engine:
        Reference to the :class:`Jinja2TemplateEngine` used for prompt
        rewriting.  May be ``None`` when prompt rewriting is not required.
    retry_delays:
        Delay in seconds before each retry attempt.  Defaults to
        ``[0, 5, 10]`` for the 1st, 2nd and 3rd retry respectively.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        state_store: SQLiteStateStore,
        template_engine: Jinja2TemplateEngine | None = None,
        max_retries: int = 3,
    ) -> None:
        """Initialise the retry engine.

        Parameters
        ----------
        state_store:
            The SQLite state-store instance used to persist retry context.
        template_engine:
            Optional Jinja2 template engine for prompt rewriting.
        max_retries:
            Maximum allowed retry attempts (default ``3``).
        """
        self.max_retries: int = max(1, max_retries)
        self.state_store: SQLiteStateStore = state_store
        self.template_engine: Jinja2TemplateEngine | None = template_engine
        self.retry_delays: list[int] = [0, 5, 10]
        logger.info(
            "RetryEngine initialised (max_retries=%d, delays=%s)",
            self.max_retries,
            self.retry_delays,
        )

    # ------------------------------------------------------------------
    # 1. Main entry point
    # ------------------------------------------------------------------

    async def handle_rejection(
        self,
        subtask_id: str,
        output: str,
        qg_result: dict,
        subtask: dict,
    ) -> dict:
        """Main entry point for handling a rejected output.

        Inspects the quality-gate result, checks whether the retry budget
        has been exhausted, and either prepares a retry or dead-letters
        the subtask.

        Parameters
        ----------
        subtask_id:
            Unique identifier of the failed subtask.
        output:
            The rejected output text.
        qg_result:
            Quality-gate evaluation dict.  Expected keys:
            ``failed_layer`` (``int``) and ``reason`` (``str``).
        subtask:
            The subtask record dict (as returned by the state store).

        Returns
        -------
        dict
            Either a ``RETRY`` action dict or a ``DEAD_LETTER`` action dict.
        """
        failed_layer: int = qg_result.get("failed_layer", -1)
        reason: str = qg_result.get("reason", "Unknown rejection reason")
        current_retry_count: int = subtask.get("retry_count", 0)

        logger.info(
            "handle_rejection [subtask=%s, layer=%d (%s), "
            "reason=%r, retry_count=%d/%d]",
            subtask_id,
            failed_layer,
            _LAYER_NAMES.get(failed_layer, "UNKNOWN"),
            reason,
            current_retry_count,
            self.max_retries,
        )

        if current_retry_count >= self.max_retries:
            logger.warning(
                "Retry budget exhausted for %s (%d >= %d) — dead-lettering",
                subtask_id,
                current_retry_count,
                self.max_retries,
            )
            return await self._send_to_dead_letter(subtask_id, qg_result, subtask)

        return await self._prepare_retry(
            subtask_id, output, qg_result, subtask
        )

    # ------------------------------------------------------------------
    # 2. Prepare retry
    # ------------------------------------------------------------------

    async def _prepare_retry(
        self,
        subtask_id: str,
        output: str,
        qg_result: dict,
        subtask: dict,
    ) -> dict:
        """Build the retry context and persist it.

        Parameters
        ----------
        subtask_id:
            Unique identifier of the failed subtask.
        output:
            The rejected output text.
        qg_result:
            Quality-gate evaluation dict.
        subtask:
            The subtask record dict.

        Returns
        -------
        dict
            ``RETRY`` action dict containing the revised context and
            scheduling metadata.
        """
        failed_layer: int = qg_result.get("failed_layer", -1)
        reason: str = qg_result.get("reason", "Unknown rejection reason")
        current_retry_count: int = subtask.get("retry_count", 0)
        retry_count: int = current_retry_count + 1
        session_id: str = subtask.get("session_id", "")

        # ---- Build layer-specific rejection context --------------------
        revised_prompt = self._build_rejection_context(
            failed_layer, reason
        )

        # ---- Build escalation message based on retry count ------------
        escalation_message = self._build_escalation(retry_count)

        # ---- Build the full retry prompt ------------------------------
        revised_context = await self._build_retry_prompt(
            subtask, output, qg_result, retry_count
        )

        # ---- Persist revised context in state store -------------------
        try:
            # Append the rejection context to the existing reasoning
            existing_reasoning: str = subtask.get("reasoning_text") or ""
            combined_reasoning = (
                f"{existing_reasoning}\n\n"
                f"--- Retry Attempt {retry_count}/{self.max_retries} ---\n"
                f"Rejection: Layer {failed_layer} — {reason}\n"
                f"Escalation: {escalation_message}\n"
                f"Revised prompt:\n{revised_context}"
            ).strip()

            await self.state_store.update_subtask_status(
                subtask_id=subtask_id,
                status="RETRY_PENDING",
                retry_count=retry_count,
                reasoning_text=combined_reasoning,
            )
        except Exception:
            logger.exception(
                "Failed to persist retry context for %s", subtask_id
            )
            raise

        # ---- Log retry preparation event ------------------------------
        try:
            await self.state_store.log_event(
                session_id=session_id,
                event_type="RETRY_PREPARED",
                severity="INFO",
                message=(
                    f"Retry {retry_count}/{self.max_retries} prepared for "
                    f"subtask {subtask_id} (layer {failed_layer})"
                ),
                subtask_id=subtask_id,
                payload={
                    "retry_count": retry_count,
                    "failed_layer": failed_layer,
                    "reason": reason,
                    "delay_seconds": self.retry_delays[
                        min(retry_count, len(self.retry_delays) - 1)
                    ],
                },
            )
        except Exception:
            logger.exception(
                "Failed to log retry preparation event for %s", subtask_id
            )

        delay_seconds = self.retry_delays[
            min(retry_count, len(self.retry_delays) - 1)
        ]

        logger.info(
            "Retry prepared [subtask=%s, attempt=%d/%d, delay=%ds]",
            subtask_id,
            retry_count,
            self.max_retries,
            delay_seconds,
        )

        return {
            "action": "RETRY",
            "subtask_id": subtask_id,
            "retry_count": retry_count,
            "delay_seconds": delay_seconds,
            "revised_context": revised_context,
            "rejection_reason": reason,
            "failed_layer": failed_layer,
        }

    # ------------------------------------------------------------------
    # 3. Dead letter
    # ------------------------------------------------------------------

    async def _send_to_dead_letter(
        self,
        subtask_id: str,
        qg_result: dict,
        subtask: dict,
    ) -> dict:
        """Send a subtask to the dead-letter queue after exhausting retries.

        Marks the subtask as ``DEAD_LETTER``, logs the failure event,
        persists failure information, and blocks all dependent tasks.

        Parameters
        ----------
        subtask_id:
            Unique identifier of the failed subtask.
        qg_result:
            Quality-gate evaluation dict.
        subtask:
            The subtask record dict.

        Returns
        -------
        dict
            ``DEAD_LETTER`` action dict containing the final reason and
            the list of blocked dependent task IDs.
        """
        reason: str = qg_result.get("reason", "Unknown rejection reason")
        failed_layer: int = qg_result.get("failed_layer", -1)
        retry_count: int = subtask.get("retry_count", 0)
        session_id: str = subtask.get("session_id", "")

        # ---- Build final reason summary -------------------------------
        final_reason = (
            f"Dead-letter after {retry_count} retries. "
            f"Final failure: Layer {failed_layer} "
            f"({_LAYER_NAMES.get(failed_layer, 'UNKNOWN')}) — {reason}"
        )

        # ---- Log dead-letter event ------------------------------------
        try:
            await self.state_store.log_event(
                session_id=session_id,
                event_type="DEAD_LETTER",
                severity="ERROR",
                message=final_reason,
                subtask_id=subtask_id,
                payload={
                    "failed_layer": failed_layer,
                    "reason": reason,
                    "retry_count": retry_count,
                },
            )
        except Exception:
            logger.exception(
                "Failed to log dead-letter event for %s", subtask_id
            )

        # ---- Store failure info in state store ------------------------
        try:
            existing_rejections: list = subtask.get("rejection_reasons") or []
            if isinstance(existing_rejections, str):
                existing_rejections = [existing_rejections]
            updated_rejections = existing_rejections + [final_reason]

            await self.state_store.update_subtask_status(
                subtask_id=subtask_id,
                status="DEAD_LETTER",
                rejection_reasons=updated_rejections,
                reasoning_text=(
                    f"{subtask.get('reasoning_text') or ''}\n\n"
                    f"=== DEAD LETTER ===\n{final_reason}"
                ).strip(),
            )
        except Exception:
            logger.exception(
                "Failed to persist dead-letter status for %s", subtask_id
            )
            raise

        # ---- Find and block dependent tasks ---------------------------
        blocked_dependents: list[str] = []
        try:
            blocked_dependents = await self._find_blocked_dependents(
                subtask_id, session_id
            )
            for dep_id in blocked_dependents:
                try:
                    await self.state_store.update_subtask_status(
                        subtask_id=dep_id,
                        status="BLOCKED",
                    )
                    await self.state_store.log_event(
                        session_id=session_id,
                        event_type="DEPENDENT_BLOCKED",
                        severity="WARNING",
                        message=(
                            f"Task {dep_id} blocked because dependency "
                            f"{subtask_id} was dead-lettered"
                        ),
                        subtask_id=dep_id,
                        payload={"blocked_by": subtask_id},
                    )
                except Exception:
                    logger.exception(
                        "Failed to block dependent task %s", dep_id
                    )
        except Exception:
            logger.exception(
                "Failed to find/block dependents for %s", subtask_id
            )

        logger.error(
            "Dead-letter [subtask=%s, reason=%s, blocked=%d dependents]",
            subtask_id,
            final_reason,
            len(blocked_dependents),
        )

        return {
            "action": "DEAD_LETTER",
            "subtask_id": subtask_id,
            "final_reason": final_reason,
            "retry_count": retry_count,
            "blocked_dependents": blocked_dependents,
        }

    # ------------------------------------------------------------------
    # 4. Build retry prompt
    # ------------------------------------------------------------------

    async def _build_retry_prompt(
        self,
        subtask: dict,
        output: str,
        qg_result: dict,
        retry_count: int,
    ) -> str:
        """Build a context-aware retry prompt for the LLM.

        Parameters
        ----------
        subtask:
            The subtask record dict containing title, acceptance criteria,
            and other metadata.
        output:
            The rejected output text (may be referenced or truncated).
        qg_result:
            Quality-gate evaluation dict.
        retry_count:
            The current retry attempt number (1-based).

        Returns
        -------
        str
            The complete retry prompt string ready to be sent to the LLM.
        """
        title: str = subtask.get("title", "Untitled Task")
        failed_layer: int = qg_result.get("failed_layer", -1)
        reason: str = qg_result.get("reason", "Unknown rejection reason")
        layer_name: str = _LAYER_NAMES.get(failed_layer, "UNKNOWN")

        # ---- Escalation message ---------------------------------------
        escalation_message = self._build_escalation(retry_count)

        # ---- Acceptance criteria formatting ---------------------------
        acceptance_criteria = subtask.get("acceptance_criteria", [])
        if isinstance(acceptance_criteria, str):
            acceptance_criteria_formatted = acceptance_criteria
        elif isinstance(acceptance_criteria, list) and acceptance_criteria:
            lines = []
            for i, crit in enumerate(acceptance_criteria, 1):
                lines.append(f"  {i}. {crit}")
            acceptance_criteria_formatted = "\n".join(lines)
        else:
            acceptance_criteria_formatted = "  (none specified)"

        # ---- Dependency outputs ---------------------------------------
        dependency_outputs = self._format_dependency_outputs(subtask)

        # ---- Rejection-specific guidance ------------------------------
        rejection_guidance = self._build_rejection_context(
            failed_layer, reason
        )

        # ---- Assemble the prompt --------------------------------------
        lines: list[str] = []
        lines.append(f"TASK: {title}")
        lines.append("")
        lines.append(
            f"PREVIOUS ATTEMPT FAILED (Attempt "
            f"{retry_count}/{self.max_retries}):"
        )
        lines.append(f"Failure: Layer {failed_layer} — {layer_name}: {reason}")
        lines.append("")
        lines.append(escalation_message)
        lines.append("")
        lines.append(rejection_guidance)
        lines.append("")
        lines.append("ORIGINAL REQUIREMENTS:")
        lines.append(acceptance_criteria_formatted)

        if dependency_outputs:
            lines.append("")
            lines.append("DEPENDENCY OUTPUTS:")
            lines.append(dependency_outputs)

        lines.append("")
        lines.append("IMPLEMENT NOW — FULL WORKING CODE:")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 5. Find blocked dependents
    # ------------------------------------------------------------------

    async def _find_blocked_dependents(
        self, subtask_id: str, session_id: str
    ) -> list[str]:
        """Find all tasks that depend on *subtask_id*.

        Scans all subtasks in the same session and returns the IDs of
        those that list *subtask_id* in their ``input_dependencies``.

        Parameters
        ----------
        subtask_id:
            The dependency subtask ID to search for.
        session_id:
            The session to search within.

        Returns
        -------
        list[str]
            List of subtask IDs that depend on *subtask_id*.
        """
        blocked_ids: list[str] = []

        try:
            all_subtasks = await self.state_store.get_session_subtasks(
                session_id
            )
        except Exception:
            logger.exception(
                "Failed to retrieve session subtasks for %s", session_id
            )
            return blocked_ids

        for st in all_subtasks:
            deps = st.get("input_dependencies")
            if deps is None:
                # Also check inside acceptance_criteria or description
                # for any textual references — but primarily look at
                # the structured field.
                continue
            if isinstance(deps, str):
                try:
                    import json

                    deps = json.loads(deps)
                except (json.JSONDecodeError, TypeError):
                    deps = [deps]
            if isinstance(deps, list) and subtask_id in deps:
                blocked_ids.append(st.get("subtask_id", ""))

        logger.debug(
            "Found %d dependent(s) of %s: %s",
            len(blocked_ids),
            subtask_id,
            blocked_ids,
        )
        return blocked_ids

    # ------------------------------------------------------------------
    # 6. Should retry
    # ------------------------------------------------------------------

    async def should_retry(self, subtask_id: str, subtask: dict) -> bool:
        """Check whether the subtask is still within its retry budget.

        Parameters
        ----------
        subtask_id:
            Unique identifier of the subtask (for logging).
        subtask:
            The subtask record dict.

        Returns
        -------
        bool
            ``True`` if the subtask's ``retry_count`` is strictly less
            than ``max_retries``.
        """
        current_count: int = subtask.get("retry_count", 0)
        can_retry: bool = current_count < self.max_retries
        logger.debug(
            "should_retry [subtask=%s, count=%d, max=%d] -> %s",
            subtask_id,
            current_count,
            self.max_retries,
            can_retry,
        )
        return can_retry

    # ------------------------------------------------------------------
    # 7. Get retry delay
    # ------------------------------------------------------------------

    async def get_retry_delay(self, retry_count: int) -> int:
        """Return the delay in seconds before the given retry attempt.

        Parameters
        ----------
        retry_count:
            The retry attempt number (1-based).

        Returns
        -------
        int
            Delay in seconds, clamped to the last entry in
            ``retry_delays`` if *retry_count* exceeds the list length.
        """
        idx = min(retry_count, len(self.retry_delays) - 1)
        delay: int = self.retry_delays[idx]
        logger.debug(
            "get_retry_delay [retry_count=%d] -> %ds (index=%d)",
            retry_count,
            delay,
            idx,
        )
        return delay

    # ------------------------------------------------------------------
    # 8. Get retry stats
    # ------------------------------------------------------------------

    async def get_retry_stats(self, session_id: str) -> dict:
        """Compute retry statistics for a session.

        Parameters
        ----------
        session_id:
            The session identifier to gather statistics for.

        Returns
        -------
        dict
            Dictionary with keys ``total_retries``,
            ``tasks_with_retries``, ``max_retries_for_any_task``, and
            ``avg_retries``.
        """
        try:
            all_subtasks = await self.state_store.get_session_subtasks(
                session_id
            )
        except Exception:
            logger.exception(
                "Failed to retrieve subtasks for stats [session=%s]",
                session_id,
            )
            return {
                "total_retries": 0,
                "tasks_with_retries": 0,
                "max_retries_for_any_task": 0,
                "avg_retries": 0.0,
            }

        total_retries = 0
        tasks_with_retries = 0
        max_retries_for_any_task = 0

        for st in all_subtasks:
            rc: int = st.get("retry_count", 0)
            total_retries += rc
            if rc > 0:
                tasks_with_retries += 1
            if rc > max_retries_for_any_task:
                max_retries_for_any_task = rc

        task_count = len(all_subtasks)
        avg_retries = (
            total_retries / task_count if task_count > 0 else 0.0
        )

        stats = {
            "total_retries": total_retries,
            "tasks_with_retries": tasks_with_retries,
            "max_retries_for_any_task": max_retries_for_any_task,
            "avg_retries": round(avg_retries, 2),
        }

        logger.debug(
            "Retry stats [session=%s]: %s", session_id, stats
        )
        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_rejection_context(self, failed_layer: int, reason: str) -> str:
        """Return a layer-specific rejection guidance message.

        Parameters
        ----------
        failed_layer:
            The quality-gate layer number that failed (0-4).
        reason:
            Human-readable rejection reason.

        Returns
        -------
        str
            Layer-specific guidance text for the retry prompt.
        """
        if failed_layer == 0:
            return (
                "Output was empty or invalid. "
                "Please provide complete implementation."
            )
        elif failed_layer == 1:
            return (
                f"Code contains banned patterns: {reason}. "
                "Fix these issues: NO pass, NO TODO, NO mock objects, "
                "NO placeholder code."
            )
        elif failed_layer == 2:
            return (
                f"Output did not meet acceptance criteria: {reason}. "
                "Address each criterion explicitly."
            )
        elif failed_layer == 3:
            return (
                f"Code failed to execute: {reason}. "
                "Fix syntax/runtime errors."
            )
        elif failed_layer == 4:
            return (
                "Output was too similar to previous result. "
                "Provide a different implementation approach."
            )
        else:
            return f"Rejection reason: {reason}. Please revise accordingly."

    def _build_escalation(self, retry_count: int) -> str:
        """Return the escalation instruction for a given retry attempt.

        Parameters
        ----------
        retry_count:
            The current retry attempt number (1-based).

        Returns
        -------
        str
            Escalation message tailored to the retry level.
        """
        if retry_count == 1:
            return (
                "Fix the specific issues identified above. "
                "Keep your working approach but address the rejection reason."
            )
        elif retry_count == 2:
            return (
                "Take a completely different approach. "
                "Rewrite from scratch with the rejection issues in mind."
            )
        elif retry_count >= 3:
            return (
                "This is your final attempt. "
                "Provide the most robust, complete implementation possible."
            )
        return ""

    def _format_dependency_outputs(self, subtask: dict) -> str:
        """Format dependency outputs for inclusion in the retry prompt.

        Parameters
        ----------
        subtask:
            The subtask record dict.

        Returns
        -------
        str
            Formatted dependency outputs text, or an empty string if no
            dependencies are present.
        """
        dep_outputs = subtask.get("dependency_outputs")
        if dep_outputs is None:
            return ""

        if isinstance(dep_outputs, dict):
            lines = []
            for dep_id, dep_output in dep_outputs.items():
                lines.append(f"  [{dep_id}]:")
                # Truncate very long outputs
                output_text = str(dep_output)
                if len(output_text) > 2000:
                    output_text = output_text[:2000] + "\n  ... (truncated)"
                lines.append(f"    {output_text}")
            return "\n".join(lines)

        if isinstance(dep_outputs, list):
            lines = []
            for item in dep_outputs:
                output_text = str(item)
                if len(output_text) > 2000:
                    output_text = output_text[:2000] + "\n  ... (truncated)"
                lines.append(f"  - {output_text}")
            return "\n".join(lines)

        output_text = str(dep_outputs)
        if len(output_text) > 2000:
            output_text = output_text[:2000] + "\n  ... (truncated)"
        return f"  {output_text}"
