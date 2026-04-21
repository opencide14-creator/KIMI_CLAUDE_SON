"""
AgentStateMachine — complete agent state machine implementation
for the Ultra Orchestrator.

Manages state transitions with validation, retry logic, dependency tracking,
and persistence via SQLiteStateStore.

State diagram::

    PENDING → QUEUED → SPAWNING → RUNNING → VALIDATING → APPROVED
                                                |
                                                └→ REJECTED → RETRY → QUEUED
                                                              |
                                                              └→ DEAD_LETTER

    Any non-terminal → BLOCKED  (parent task in DEAD_LETTER)

Terminal states: APPROVED, DEAD_LETTER, BLOCKED

Usage::

    from orchestrator.state_machine import AgentStateMachine, AgentStatus, Priority
    from infrastructure.state_store import SQLiteStateStore

    store = SQLiteStateStore("orchestrator.db")
    sm = AgentStateMachine(store, max_retries=3)

    result = await sm.transition("st-1", AgentStatus.QUEUED)
    print(result)  # {"subtask_id": "st-1", "old_state": "PENDING", ...}
"""

from __future__ import annotations

import json
import logging
import time
from enum import Enum
from typing import Any, Callable, Coroutine

from infrastructure.state_store import SQLiteStateStore

logger = logging.getLogger("ultra_orchestrator.state_machine")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class AgentStatus(str, Enum):
    """Every possible state an agent (subtask) can be in.

    Stored as strings in the database — the ``str`` mixin guarantees that
    ``AgentStatus.PENDING == "PENDING"`` and ``str(AgentStatus.PENDING)``
    returns the expected value.
    """

    PENDING = "PENDING"               # Initial state, waiting for dependencies
    QUEUED = "QUEUED"                 # Waiting for execution slot
    SPAWNING = "SPAWNING"             # Thread / coroutine starting
    RUNNING = "RUNNING"               # API call in progress
    VALIDATING = "VALIDATING"         # Quality gate running
    APPROVED = "APPROVED"             # Output approved
    REJECTED = "REJECTED"             # Quality gate rejected
    RETRY = "RETRY"                   # Preparing retry
    DEAD_LETTER = "DEAD_LETTER"       # Max retries exceeded
    BLOCKED = "BLOCKED"               # Parent task in DEAD_LETTER, can't proceed


class Priority(str, Enum):
    """Execution priority for subtasks."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


# Numeric rank used for sorting (higher = more urgent)
_PRIORITY_RANK: dict[str, int] = {
    Priority.CRITICAL: 4,
    Priority.HIGH: 3,
    Priority.NORMAL: 2,
    Priority.LOW: 1,
}

# All valid forward transitions as (from_state, to_state) pairs.
_VALID_TRANSITIONS: set[tuple[str, str]] = {
    (AgentStatus.PENDING, AgentStatus.QUEUED),
    (AgentStatus.QUEUED, AgentStatus.SPAWNING),
    (AgentStatus.SPAWNING, AgentStatus.RUNNING),
    (AgentStatus.RUNNING, AgentStatus.VALIDATING),
    (AgentStatus.VALIDATING, AgentStatus.APPROVED),
    (AgentStatus.VALIDATING, AgentStatus.REJECTED),
    (AgentStatus.REJECTED, AgentStatus.RETRY),
    (AgentStatus.RETRY, AgentStatus.QUEUED),
    (AgentStatus.REJECTED, AgentStatus.DEAD_LETTER),
}

# Any non-terminal state may transition to BLOCKED (handled dynamically).
_TERMINAL_STATES: set[str] = {
    AgentStatus.APPROVED,
    AgentStatus.DEAD_LETTER,
    AgentStatus.BLOCKED,
}


# ---------------------------------------------------------------------------
# State-machine class
# ---------------------------------------------------------------------------


class AgentStateMachine:
    """Manages agent (subtask) state transitions with validation & persistence.

    Parameters
    ----------
    state_store:
        Reference to the SQLite persistence layer.
    max_retries:
        Maximum retry attempts before a subtask lands in ``DEAD_LETTER``.
        Defaults to ``3``.

    Attributes
    ----------
    state_store : SQLiteStateStore
        Persistent state backing.
    max_retries : int
        Retry ceiling.
    transition_callbacks : dict[tuple[str, str], list[callable]]
        Registered callbacks keyed by ``(from_state, to_state)``.
    """

    # ------------------------------------------------------------------
    # 1. __init__
    # ------------------------------------------------------------------

    def __init__(
        self,
        state_store: SQLiteStateStore,
        max_retries: int = 3,
    ) -> None:
        self.state_store: SQLiteStateStore = state_store
        self.max_retries: int = max(0, max_retries)
        # (from_state, to_state) -> list[callback]
        self.transition_callbacks: dict[
            tuple[str, str],
            list[Callable[..., Coroutine[Any, Any, None] | None]],
        ] = {}

    # ------------------------------------------------------------------
    # 2. is_valid_transition
    # ------------------------------------------------------------------

    def is_valid_transition(self, from_state: str, to_state: str) -> bool:
        """Check whether a transition is allowed.

        Parameters
        ----------
        from_state:
            Current state string.
        to_state:
            Desired next state string.

        Returns
        -------
        bool
            ``True`` if the transition is legal.
        """
        # Any non-terminal state may transition to BLOCKED.
        if to_state == AgentStatus.BLOCKED and from_state not in _TERMINAL_STATES:
            return True
        return (from_state, to_state) in _VALID_TRANSITIONS

    # ------------------------------------------------------------------
    # 3. is_terminal_state
    # ------------------------------------------------------------------

    def is_terminal_state(self, state: str) -> bool:
        """Check whether *state* is terminal (no further transitions).

        Parameters
        ----------
        state:
            State string to inspect.

        Returns
        -------
        bool
            ``True`` for ``APPROVED``, ``DEAD_LETTER``, and ``BLOCKED``.
        """
        return state in _TERMINAL_STATES

    # ------------------------------------------------------------------
    # 4. transition
    # ------------------------------------------------------------------

    async def transition(
        self,
        subtask_id: str,
        to_state: str,
        **kwargs: Any,
    ) -> dict:
        """Perform a validated state transition for a subtask.

        Steps:

        1. Fetch the current subtask from the store.
        2. Validate the transition (raises ``ValueError`` if illegal).
        3. If transitioning to ``REJECTED``, auto-decide between
           ``REJECTED → RETRY`` and ``REJECTED → DEAD_LETTER`` based on
           ``retry_count < max_retries``.
        4. If transitioning to ``RETRY``, bump ``retry_count``.
        5. Persist the new state plus any extra fields from *kwargs*.
        6. Log a transition event.
        7. Fire registered callbacks.

        Parameters
        ----------
        subtask_id:
            Target subtask identifier.
        to_state:
            Desired next state.
        **kwargs:
            Extra fields to persist (e.g. ``output_text``, ``tokens_used``,
            ``cost_usd``, ``reasoning_text``, ``rejection_reasons``,
            ``assigned_key``).

        Returns
        -------
        dict
            Transition record with keys ``subtask_id``, ``old_state``,
            ``new_state``, ``timestamp``.

        Raises
        ------
        ValueError
            If the transition is invalid or the subtask is not found.
        """
        # --- 4a: fetch current subtask ---
        subtask = await self.state_store.get_subtask(subtask_id)
        if subtask is None:
            raise ValueError(f"Subtask not found: {subtask_id}")

        old_state: str = subtask["status"]

        # If already terminal, reject any further move (except to same state).
        if old_state in _TERMINAL_STATES and old_state != to_state:
            raise ValueError(
                f"Cannot transition from terminal state {old_state!r} "
                f"to {to_state!r} for subtask {subtask_id}"
            )

        # --- 4b: validate ---
        if not self.is_valid_transition(old_state, to_state):
            raise ValueError(
                f"Invalid transition: {old_state!r} → {to_state!r} "
                f"for subtask {subtask_id}"
            )

        # --- 4c: retry logic when entering REJECTED ---
        resolved_to_state: str = to_state
        current_retry_count: int = subtask.get("retry_count", 0) or 0
        new_retry_count: int | None = None

        if to_state == AgentStatus.REJECTED:
            if await self.can_retry(subtask_id):
                resolved_to_state = AgentStatus.RETRY
                new_retry_count = current_retry_count + 1
                logger.debug(
                    "Subtask %s rejected — retry %d/%d",
                    subtask_id,
                    new_retry_count,
                    self.max_retries,
                )
            else:
                resolved_to_state = AgentStatus.DEAD_LETTER
                logger.warning(
                    "Subtask %s exceeded max retries (%d) — dead-letter",
                    subtask_id,
                    self.max_retries,
                )

        elif to_state == AgentStatus.RETRY:
            new_retry_count = current_retry_count + 1

        # --- 4d: build persistence kwargs ---
        update_kwargs: dict[str, Any] = {"status": resolved_to_state}

        # Pass through any extra fields from the caller
        for key in (
            "output_text",
            "tokens_used",
            "cost_usd",
            "reasoning_text",
            "assigned_key",
            "started_at",
            "completed_at",
        ):
            if key in kwargs:
                update_kwargs[key] = kwargs[key]

        if "rejection_reasons" in kwargs:
            reasons = kwargs["rejection_reasons"]
            update_kwargs["rejection_reasons"] = (
                reasons if isinstance(reasons, list) else [str(reasons)]
            )

        if new_retry_count is not None:
            update_kwargs["retry_count"] = new_retry_count

        # Timestamps
        now = time.time()
        if resolved_to_state == AgentStatus.RUNNING and subtask.get("started_at") is None:
            update_kwargs["started_at"] = now
        if resolved_to_state in (AgentStatus.APPROVED, AgentStatus.DEAD_LETTER):
            update_kwargs["completed_at"] = now

        # --- 4e: persist ---
        await self.state_store.update_subtask_status(
            subtask_id,
            **update_kwargs,
        )

        # --- 4f: log event ---
        session_id: str = subtask.get("session_id", "")
        try:
            await self.state_store.log_event(
                session_id=session_id,
                event_type="STATE_TRANSITION",
                severity="DEBUG",
                message=f"{subtask_id}: {old_state} -> {resolved_to_state}",
                subtask_id=subtask_id,
                payload={
                    "old_state": old_state,
                    "new_state": resolved_to_state,
                    "retry_count": new_retry_count,
                },
            )
        except Exception:
            logger.exception(
                "Failed to log transition event for %s", subtask_id
            )

        # --- 4g: callbacks ---
        transition_info: dict = {
            "subtask_id": subtask_id,
            "old_state": old_state,
            "new_state": resolved_to_state,
            "timestamp": now,
        }
        await self._run_callbacks(old_state, resolved_to_state, subtask_id, transition_info)

        # --- 4h: return ---
        return transition_info

    # ------------------------------------------------------------------
    # 5. can_retry
    # ------------------------------------------------------------------

    async def can_retry(self, subtask_id: str) -> bool:
        """Return ``True`` if the subtask may still be retried.

        Parameters
        ----------
        subtask_id:
            Subtask identifier.

        Returns
        -------
        bool
            ``True`` when ``retry_count < max_retries``.

        Raises
        ------
        ValueError
            If the subtask does not exist.
        """
        subtask = await self.state_store.get_subtask(subtask_id)
        if subtask is None:
            raise ValueError(f"Subtask not found: {subtask_id}")
        retry_count: int = subtask.get("retry_count", 0) or 0
        return retry_count < self.max_retries

    # ------------------------------------------------------------------
    # 6. get_current_state
    # ------------------------------------------------------------------

    async def get_current_state(self, subtask_id: str) -> str:
        """Return the current state of a subtask.

        Parameters
        ----------
        subtask_id:
            Subtask identifier.

        Returns
        -------
        str
            Current state string.

        Raises
        ------
        ValueError
            If the subtask does not exist.
        """
        subtask = await self.state_store.get_subtask(subtask_id)
        if subtask is None:
            raise ValueError(f"Subtask not found: {subtask_id}")
        return subtask["status"]

    # ------------------------------------------------------------------
    # 7. get_subtasks_in_state
    # ------------------------------------------------------------------

    async def get_subtasks_in_state(
        self, session_id: str, state: str
    ) -> list[dict]:
        """Get all subtasks in the given state for a session.

        Parameters
        ----------
        session_id:
            Session identifier.
        state:
            Status string to filter on.

        Returns
        -------
        list[dict]
            Matching subtask dicts.
        """
        return await self.state_store.get_session_subtasks(session_id, state)

    # ------------------------------------------------------------------
    # 8. are_dependencies_complete
    # ------------------------------------------------------------------

    async def are_dependencies_complete(
        self,
        subtask_id: str,
        session_subtasks: dict[str, dict],
    ) -> bool:
        """Check whether all *input_dependencies* of *subtask_id* are
        in ``APPROVED`` state.

        *session_subtasks* is a mapping ``subtask_id -> subtask dict`` so
        that dependency resolution is entirely in-memory (no extra DB
        round-trips).

        Parameters
        ----------
        subtask_id:
            Subtask whose dependencies are being checked.
        session_subtasks:
            Full mapping of subtask_id -> subtask dict for the session.

        Returns
        -------
        bool
            ``True`` when every dependency is ``APPROVED`` (or there are
            no dependencies).
        """
        subtask = session_subtasks.get(subtask_id)
        if subtask is None:
            return False

        # Resolve input_dependencies (may be a JSON string, a list, or absent).
        deps_raw = subtask.get("input_dependencies")
        if deps_raw is None:
            return True  # No dependencies == ready

        if isinstance(deps_raw, str):
            try:
                deps: list[str] = json.loads(deps_raw)
            except (json.JSONDecodeError, TypeError):
                logger.warning(
                    "Invalid JSON in input_dependencies for %s: %r",
                    subtask_id,
                    deps_raw,
                )
                return False
        elif isinstance(deps_raw, list):
            deps = deps_raw
        else:
            logger.warning(
                "Unexpected type for input_dependencies on %s: %s",
                subtask_id,
                type(deps_raw).__name__,
            )
            return False

        if not deps:
            return True

        for dep_id in deps:
            dep_subtask = session_subtasks.get(dep_id)
            if dep_subtask is None:
                logger.debug(
                    "Dependency %s of %s not found in session subtasks",
                    dep_id,
                    subtask_id,
                )
                return False
            if dep_subtask.get("status") != AgentStatus.APPROVED:
                return False

        return True

    # ------------------------------------------------------------------
    # 9. mark_dependencies_blocked
    # ------------------------------------------------------------------

    async def mark_dependencies_blocked(
        self,
        subtask_id: str,
        session_subtasks: dict[str, dict],
    ) -> list[str]:
        """Mark every subtask that depends on *subtask_id* as ``BLOCKED``.

        Walks all subtasks in *session_subtasks* and checks whether
        *subtask_id* appears in their ``input_dependencies`` list.
        For each match, if the dependent subtask is not already terminal,
        it is transitioned to ``BLOCKED``.

        Parameters
        ----------
        subtask_id:
            The subtask that has entered a terminal dead state.
        session_subtasks:
            Full mapping of subtask_id -> subtask dict for the session.

        Returns
        -------
        list[str]
            Subtask IDs that were marked as ``BLOCKED``.
        """
        affected: list[str] = []

        for other_id, other_subtask in session_subtasks.items():
            if other_id == subtask_id:
                continue

            other_status = other_subtask.get("status", "")
            if other_status in _TERMINAL_STATES:
                continue  # Don't override terminal states

            # Resolve dependencies
            deps_raw = other_subtask.get("input_dependencies")
            if deps_raw is None:
                continue

            if isinstance(deps_raw, str):
                try:
                    deps: list[str] = json.loads(deps_raw)
                except (json.JSONDecodeError, TypeError):
                    continue
            elif isinstance(deps_raw, list):
                deps = deps_raw
            else:
                continue

            if subtask_id in deps:
                try:
                    await self.transition(other_id, AgentStatus.BLOCKED)
                    affected.append(other_id)
                except ValueError as exc:
                    logger.warning(
                        "Could not block subtask %s: %s", other_id, exc
                    )

        if affected:
            logger.info(
                "Marked %d dependent subtask(s) as BLOCKED "
                "due to %s failure: %s",
                len(affected),
                subtask_id,
                affected,
            )

        return affected

    # ------------------------------------------------------------------
    # 10. get_ready_tasks
    # ------------------------------------------------------------------

    async def get_ready_tasks(self, session_id: str) -> list[dict]:
        """Get all ``PENDING`` subtasks whose dependencies are complete.

        Results are sorted by:

        1. Priority descending (CRITICAL > HIGH > NORMAL > LOW)
        2. Retry count ascending (fewer retries first)
        3. Created-at ascending (older first)

        Parameters
        ----------
        session_id:
            Session identifier.

        Returns
        -------
        list[dict]
            Ready subtasks, sorted for scheduling.
        """
        pending = await self.state_store.get_session_subtasks(
            session_id, AgentStatus.PENDING
        )
        if not pending:
            return []

        # Build a lookup dict for dependency checking
        # We need ALL subtasks in the session to resolve dependencies
        all_subtasks = await self.state_store.get_session_subtasks(session_id)
        session_map: dict[str, dict] = {
            st["subtask_id"]: st for st in all_subtasks
        }

        ready: list[dict] = [
            st
            for st in pending
            if await self.are_dependencies_complete(
                st["subtask_id"], session_map
            )
        ]

        # Sort: priority (desc), retry_count (asc), created_at (asc)
        ready.sort(
            key=lambda st: (
                -_PRIORITY_RANK.get(st.get("priority", "NORMAL"), 0),
                st.get("retry_count", 0) or 0,
                st.get("created_at", 0.0) or 0.0,
            )
        )

        return ready

    # ------------------------------------------------------------------
    # 11. get_next_batch_candidates
    # ------------------------------------------------------------------

    async def get_next_batch_candidates(
        self, session_id: str, limit: int = 20
    ) -> list[dict]:
        """Get tasks ready for the next scheduling batch.

        Combines ``PENDING`` tasks whose dependencies are complete with
        tasks already in ``QUEUED`` state, then sorts for scheduling.

        Parameters
        ----------
        session_id:
            Session identifier.
        limit:
            Maximum number of candidates to return. Defaults to ``20``.

        Returns
        -------
        list[dict]
            Candidates sorted by priority, retry count, and creation time.
        """
        ready = await self.get_ready_tasks(session_id)
        queued = await self.state_store.get_session_subtasks(
            session_id, AgentStatus.QUEUED
        )

        combined: list[dict] = ready + queued

        # Deduplicate by subtask_id (a task could theoretically be in both)
        seen: set[str] = set()
        deduped: list[dict] = []
        for st in combined:
            sid = st["subtask_id"]
            if sid not in seen:
                seen.add(sid)
                deduped.append(st)

        # Sort: priority (desc), retry_count (asc), created_at (asc)
        deduped.sort(
            key=lambda st: (
                -_PRIORITY_RANK.get(st.get("priority", "NORMAL"), 0),
                st.get("retry_count", 0) or 0,
                st.get("created_at", 0.0) or 0.0,
            )
        )

        return deduped[:limit]

    # ------------------------------------------------------------------
    # 12. register_transition_callback
    # ------------------------------------------------------------------

    def register_transition_callback(
        self,
        from_state: str,
        to_state: str,
        callback: Callable[..., Coroutine[Any, Any, None] | None],
    ) -> None:
        """Register a callback that fires after a specific transition.

        The callback receives ``(subtask_id, transition_info)`` where
        *transition_info* is the dict returned by :meth:`transition`.

        Parameters
        ----------
        from_state:
            Source state that triggers the callback.
        to_state:
            Destination state that triggers the callback.
        callback:
            Async or sync callable.
        """
        key = (from_state, to_state)
        if key not in self.transition_callbacks:
            self.transition_callbacks[key] = []
        self.transition_callbacks[key].append(callback)
        logger.debug(
            "Registered callback for %s -> %s", from_state, to_state
        )

    # ------------------------------------------------------------------
    # 13. _run_callbacks
    # ------------------------------------------------------------------

    async def _run_callbacks(
        self,
        from_state: str,
        to_state: str,
        subtask_id: str,
        transition_info: dict,
    ) -> None:
        """Execute all callbacks registered for ``from_state -> to_state``.

        Callback failures are logged but never raised, ensuring that a
        transition always completes even if a callback errors.

        Parameters
        ----------
        from_state:
            Previous state.
        to_state:
            New state.
        subtask_id:
            Subtask that transitioned.
        transition_info:
            Dict with ``subtask_id``, ``old_state``, ``new_state``,
            ``timestamp``.
        """
        key = (from_state, to_state)
        callbacks = self.transition_callbacks.get(key, [])
        if not callbacks:
            return

        for cb in callbacks:
            try:
                result = cb(subtask_id, transition_info)
                # Await if the callback is a coroutine
                if result is not None and hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.exception(
                    "Transition callback failed for %s -> %s on %s",
                    from_state,
                    to_state,
                    subtask_id,
                )

    # ------------------------------------------------------------------
    # 14. reset_for_resume
    # ------------------------------------------------------------------

    async def reset_for_resume(self, session_id: str) -> int:
        """Reset all non-terminal subtasks to ``QUEUED`` for session resume.

        Delegates to ``state_store.reset_non_approved_subtasks`` which
        clears transient execution fields (``assigned_key``,
        ``output_text``, ``reasoning_text``, ``started_at``,
        ``completed_at``) for every subtask not in ``APPROVED`` or
        ``DEAD_LETTER``.

        Parameters
        ----------
        session_id:
            Session identifier.

        Returns
        -------
        int
            Number of subtasks reset.
        """
        count = await self.state_store.reset_non_approved_subtasks(session_id)
        logger.info(
            "Reset %d subtasks to QUEUED for resume of session %s",
            count,
            session_id,
        )
        return count

    # ------------------------------------------------------------------
    # 15. get_state_counts
    # ------------------------------------------------------------------

    async def get_state_counts(self, session_id: str) -> dict[str, int]:
        """Return counts of subtasks per state.

        Parameters
        ----------
        session_id:
            Session identifier.

        Returns
        -------
        dict[str, int]
            Mapping ``state_string -> count``. All AgentStatus values
            appear as keys (with count ``0`` when absent).
        """
        all_subtasks = await self.state_store.get_session_subtasks(session_id)

        # Initialise all known states at 0
        counts: dict[str, int] = {
            status.value: 0 for status in AgentStatus
        }

        for st in all_subtasks:
            status = st.get("status", "")
            counts[status] = counts.get(status, 0) + 1

        return counts

    # ------------------------------------------------------------------
    # Dunder
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"AgentStateMachine(store={self.state_store!r}, "
            f"max_retries={self.max_retries})"
        )
