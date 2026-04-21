"""
Token Resonance Engine — API Key Selection & Capacity Management

Manages 4 API keys and selects the optimal key for each request based on
capacity ratio. Acts as the intelligent traffic director between the scheduler
and the underlying API pool, enforcing hard concurrency limits and providing
comprehensive health visibility.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from infrastructure.api_pool import KimiAPIPool

logger = logging.getLogger(__name__)


class TokenResonanceEngine:
    """
    Intelligent API key selector that "resonates" with the key most capable
    of absorbing the next workload.  Manages a hard cap of 20 concurrent
    agents and 4 rotating API keys.

    Attributes
    ----------
    api_pool : KimiAPIPool
        Reference to the shared API pool managing token buckets & circuit
        breakers for each key.
    max_concurrent : int
        Hard limit of concurrent agents (≤ 20).
    active_count : int
        Current number of in-flight requests / active agents.
    lock : asyncio.Lock
        Guards ``active_count`` and slot bookkeeping.
    safety_margin : float
        Fraction of theoretical max capacity we are willing to use (0.0–1.0).
        Passed down to ``api_pool``.
    """

    HARD_MAX_CONCURRENT: int = 20  # absolute ceiling, cannot be overridden

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        api_pool: KimiAPIPool,
        max_concurrent: int = 20,
        safety_margin: float = 0.80,
    ) -> None:
        if max_concurrent > self.HARD_MAX_CONCURRENT:
            raise ValueError(
                f"max_concurrent ({max_concurrent}) exceeds hard limit "
                f"of {self.HARD_MAX_CONCURRENT}"
            )
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be ≥ 1, got {max_concurrent}")

        self.api_pool: KimiAPIPool = api_pool
        self.max_concurrent: int = max_concurrent
        self.safety_margin: float = safety_margin
        self.active_count: int = 0
        self.lock: asyncio.Lock = asyncio.Lock()

        logger.info(
            "TokenResonanceEngine initialised — max_concurrent=%d, "
            "safety_margin=%.2f",
            max_concurrent,
            safety_margin,
        )

    # ------------------------------------------------------------------ #
    # Key selection
    # ------------------------------------------------------------------ #

    async def get_optimal_key_for_task(
        self, estimated_tokens: int = 1000
    ) -> Optional[str]:
        """
        Return the ID of the API key best suited to handle a task of the
        given estimated token size, or ``None`` when no key is available.

        Parameters
        ----------
        estimated_tokens : int
            Predicted token consumption for the upcoming request.

        Returns
        -------
        str | None
            Selected ``key_id`` or ``None``.
        """
        key_id: Optional[str] = await self.api_pool.get_optimal_key(
            estimated_tokens=estimated_tokens
        )
        if key_id is None:
            logger.debug(
                "No optimal key found for estimated_tokens=%d", estimated_tokens
            )
        else:
            logger.debug(
                "Selected key '%s' for estimated_tokens=%d", key_id, estimated_tokens
            )
        return key_id

    # ------------------------------------------------------------------ #
    # Slot lifecycle
    # ------------------------------------------------------------------ #

    async def acquire_slot(self, key_id: str) -> bool:
        """
        Attempt to reserve one of the concurrent agent slots.

        Returns ``True`` when the slot was successfully acquired and the
        in-flight counter for *key_id* has been incremented.
        """
        async with self.lock:
            if self.active_count >= self.max_concurrent:
                logger.warning(
                    "Slot acquisition rejected — at capacity (%d/%d)",
                    self.active_count,
                    self.max_concurrent,
                )
                return False

            self.active_count += 1
            await self.api_pool.buckets[key_id].increment_requests_in_flight()

            logger.debug(
                "Slot acquired — active=%d/%d, key=%s",
                self.active_count,
                self.max_concurrent,
                key_id,
            )
            return True

    async def release_slot(self, key_id: str) -> None:
        """
        Release a previously acquired slot and decrement the in-flight
        counter for the given key.
        """
        async with self.lock:
            self.active_count = max(0, self.active_count - 1)
            await self.api_pool.buckets[key_id].decrement_requests_in_flight()

            logger.debug(
                "Slot released — active=%d/%d, key=%s",
                self.active_count,
                self.max_concurrent,
                key_id,
            )

    # ------------------------------------------------------------------ #
    # Capacity introspection
    # ------------------------------------------------------------------ #

    async def get_available_slots(self) -> int:
        """Return the number of remaining concurrent slots."""
        async with self.lock:
            return self.max_concurrent - self.active_count

    async def get_active_count(self) -> int:
        """Return the current number of active / in-flight agents."""
        return self.active_count

    async def get_concurrent_utilization(self) -> float:
        """Return the concurrency utilisation ratio (0.0 – 1.0)."""
        return self.active_count / self.max_concurrent if self.max_concurrent else 0.0

    async def can_spawn_more(self) -> bool:
        """
        ``True`` when there is at least one free slot *and* at least one
        API key is available to accept work.
        """
        if self.active_count >= self.max_concurrent:
            return False
        optimal = await self.get_optimal_key_for_task()
        return optimal is not None

    # ------------------------------------------------------------------ #
    # Status & health
    # ------------------------------------------------------------------ #

    async def get_key_status_summary(self) -> list[dict]:
        """
        Enhanced status for every key, augmented with the number of
        engine-level slots still available.

        Returns
        -------
        list[dict]
            Each dict contains the fields returned by
            ``api_pool.get_all_key_status()`` plus ``available_slots``.
        """
        available_slots = await self.get_available_slots()
        statuses = await self.api_pool.get_all_key_status()
        for entry in statuses:
            entry["available_slots"] = available_slots
        return statuses

    async def get_all_key_health(self) -> dict:
        """
        Comprehensive health snapshot for each managed API key.

        Returns
        -------
        dict
            Mapping ``key_id → health_dict`` with keys:
            ``key_id``, ``capacity_ratio``, ``circuit_state``,
            ``backoff_remaining_sec``, ``requests_in_flight``,
            ``total_tokens``, ``total_cost``, ``is_healthy``.
        """
        health: dict = {}
        for key_id, bucket in self.api_pool.buckets.items():
            health[key_id] = {
                "key_id": key_id,
                "capacity_ratio": bucket.capacity_ratio(),
                "circuit_state": bucket.circuit_breaker.state.value
                if hasattr(bucket.circuit_breaker.state, "value")
                else str(bucket.circuit_breaker.state),
                "backoff_remaining_sec": bucket.circuit_breaker.backoff_remaining(),
                "requests_in_flight": bucket.requests_in_flight,
                "total_tokens": bucket.total_tokens_consumed,
                "total_cost": bucket.total_cost_usd,
                "is_healthy": await self.api_pool.is_key_healthy(key_id),
            }
        return health

    # ------------------------------------------------------------------ #
    # Token & cost bookkeeping
    # ------------------------------------------------------------------ #

    async def record_token_usage(
        self, key_id: str, tokens_used: int, cost_usd: float
    ) -> None:
        """
        Record actual token consumption and monetary cost against the
        specified key's token bucket.
        """
        await self.api_pool.buckets[key_id].consume_tokens(tokens_used, cost_usd)
        logger.debug(
            "Recorded usage — key=%s, tokens=%d, cost=$%.6f",
            key_id,
            tokens_used,
            cost_usd,
        )

    async def record_rate_limit_hit(self, key_id: str) -> None:
        """Record a HTTP 429 (rate-limit) event for the given key."""
        await self.api_pool.buckets[key_id].record_429()
        logger.debug("Recorded 429 — key=%s", key_id)

    # ------------------------------------------------------------------ #
    # Batch assignment
    # ------------------------------------------------------------------ #

    async def get_batch_assignment(
        self, ready_tasks: list[dict]
    ) -> list[tuple[dict, str]]:
        """
        Greedily assign an optimal API key to each ready task, up to the
        number of available concurrent slots (hard cap 20).

        Assignment stops as soon as a key cannot be found — tasks are
        *not* skipped; the caller should retry later.

        Parameters
        ----------
        ready_tasks : list[dict]
            Tasks awaiting execution.  Each dict may contain
            ``estimated_tokens`` or ``description`` for token estimation.

        Returns
        -------
        list[tuple[dict, str]]
            Pairs of (task_dict, assigned_key_id).
        """
        assignments: list[tuple[dict, str]] = []

        # Clamp to the absolute maximum of 20 regardless of slots
        available = await self.get_available_slots()
        max_batch = min(len(ready_tasks), available, self.HARD_MAX_CONCURRENT)

        for idx in range(max_batch):
            task = ready_tasks[idx]

            # ---- token estimation ----------------------------------- #
            estimated_tokens = task.get("estimated_tokens")
            if estimated_tokens is None:
                description = task.get("description", "")
                # Rough heuristic: ~1 token ≈ 4 characters for English text
                estimated_tokens = max(100, len(description) // 4)
            estimated_tokens = max(1, int(estimated_tokens))

            # ---- key selection -------------------------------------- #
            key_id = await self.get_optimal_key_for_task(estimated_tokens)
            if key_id is None:
                logger.info(
                    "Batch assignment stopped at task %d — no key available", idx
                )
                break

            assignments.append((task, key_id))

        logger.info(
            "Batch assignment — requested=%d, assigned=%d",
            len(ready_tasks),
            len(assignments),
        )
        return assignments

    # ------------------------------------------------------------------ #
    # Capacity predicate
    # ------------------------------------------------------------------ #

    async def is_capacity_available(self, estimated_tokens: int = 1000) -> bool:
        """
        ``True`` when there is a free concurrent slot *and* an API key
        capable of handling *estimated_tokens*.
        """
        if self.active_count >= self.max_concurrent:
            return False
        key_id = await self.get_optimal_key_for_task(estimated_tokens)
        return key_id is not None

    # ------------------------------------------------------------------ #
    # Emergency handling
    # ------------------------------------------------------------------ #

    async def emergency_rebalance(self) -> None:
        """
        Check for catastrophic degradation (≥ 3 keys with OPEN circuits).

        When triggered, logs a CRITICAL alert and returns immediately so
        the scheduler can pause dispatch until keys recover.
        """
        open_count = 0
        for key_id, bucket in self.api_pool.buckets.items():
            cb = bucket.circuit_breaker
            state_value = cb.state.value if hasattr(cb.state, "value") else str(cb.state)
            # Common circuit-breaker libraries expose state as an enum/string;
            # "OPEN" or "open" indicates the circuit is tripped.
            if str(state_value).upper() == "OPEN":
                open_count += 1

        if open_count >= 3:
            logger.critical(
                "EMERGENCY REBALANCE triggered — %d/4 keys have OPEN circuits. "
                "Pausing scheduler dispatch until recovery.",
                open_count,
            )
            # Return immediately — the scheduler is responsible for pausing.
            return

        logger.debug(
            "Emergency check passed — %d/4 keys OPEN", open_count
        )
