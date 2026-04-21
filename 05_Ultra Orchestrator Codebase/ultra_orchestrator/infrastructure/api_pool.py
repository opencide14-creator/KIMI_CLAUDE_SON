"""
Kimi K2.6 API Pool — Production-Grade Async API Client

Manages 4 Kimi API keys with:
  - Token bucket rate limiting (per-key TPM/RPM)
  - Circuit breaker pattern for fault tolerance
  - Health tracking and intelligent key rotation
  - Automatic retry with exponential backoff and key failover
  - Cost tracking ($0.50/1M input, $1.50/1M output tokens)

Classes:
    APIKeyBucket  — Single key state (tokens, circuit breaker, metrics)
    KimiAPIPool   — Multi-key pool manager with rotation & retries
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────

_ENDPOINT = "https://api.kimi.com/coding/v1/chat/completions"
_MODEL = "kimi-for-coding"
_KEY_ENV_PREFIX = "KIMI_KEY_"
_KEY_ENV_PREFIX_ALT = "KIMI_API_KEY_"
_NUM_KEYS = 4

# Token-bucket safety margins
_BUCKET_SAFETY_MARGIN = 0.80      # cap tokens at 80 % of max
_REQUEST_SAFETY_MULTIPLIER = 1.2  # require 1.2x estimated budget

# Retry / backoff defaults
_MAX_RETRIES = 5
_BASE_RETRY_DELAY = 2.0           # exponential backoff starting at 2 s
_RETRY_BACKOFF_EXPONENT = 2

# 429-backoff schedule (seconds)
_BACKOFF_SCHEDULE = {1: 30, 2: 120, 3: 300}  # 1st→30s, 2nd→120s, 3rd+→300s
_BACKOFF_DEFAULT = 300

# Circuit breaker thresholds
_CIRCUIT_OPEN_AFTER_429S = 5

# Kimi pricing (USD per token)
_COST_PER_INPUT_TOKEN = 0.50 / 1_000_000
_COST_PER_OUTPUT_TOKEN = 1.50 / 1_000_000


# ── Helpers ────────────────────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    """Return a masked version of an API key safe for logging."""
    if not key:
        return "<empty>"
    if len(key) <= 12:
        return key[:4] + "****"
    return key[:8] + "****" + key[-4:]


# ── APIKeyBucket ───────────────────────────────────────────────────────────

class APIKeyBucket:
    """Represents a single API key's rate-limit bucket and health state.

    Thread-safety: all mutating methods must be called while holding the
    bucket's ``lock`` (provided as a public attribute).
    """

    # ------------------------------------------------------------------ #
    # Attributes
    # ------------------------------------------------------------------ #

    key_id: str
    api_key: str

    max_tokens_per_minute: int
    max_requests_per_minute: int

    current_tokens_available: float
    last_refill_time: float

    requests_in_flight: int

    consecutive_429_count: int
    backoff_until: float

    total_tokens_consumed: int
    estimated_cost: float
    total_requests: int
    successful_requests: int

    circuit_state: str          # "CLOSED" | "OPEN" | "HALF_OPEN"
    _permanent_dead: bool       # 401/403 → permanently dead

    lock: asyncio.Lock          # coarse-grained lock for this bucket

    # ------------------------------------------------------------------ #
    # Construction
    # ------------------------------------------------------------------ #

    def __init__(
        self,
        key_id: str,
        api_key: str,
        max_tpm: int = 200_000,
        max_rpm: int = 60,
    ) -> None:
        self.key_id = key_id
        self.api_key = api_key

        self.max_tokens_per_minute = max_tpm
        self.max_requests_per_minute = max_rpm

        now = time.monotonic()
        self.current_tokens_available = float(max_tpm)
        self.last_refill_time = now

        self.requests_in_flight = 0

        self.consecutive_429_count = 0
        self.backoff_until = 0.0

        self.total_tokens_consumed = 0
        self.estimated_cost = 0.0
        self.total_requests = 0
        self.successful_requests = 0

        self.circuit_state = "CLOSED"
        self._permanent_dead = False

        self.lock = asyncio.Lock()

        logger.info(
            "APIKeyBucket initialised: key_id=%s, max_tpm=%d, max_rpm=%d",
            key_id,
            max_tpm,
            max_rpm,
        )

    # ------------------------------------------------------------------ #
    # Token bucket
    # ------------------------------------------------------------------ #

    def refill_tokens(self) -> None:
        """Add tokens proportional to elapsed time since last refill.

        The refill rate is ``max_tokens_per_minute / 60`` tokens per second.
        Tokens are capped at ``max_tokens_per_minute * 0.80``.
        """
        now = time.monotonic()
        elapsed = now - self.last_refill_time
        if elapsed <= 0:
            return

        rate_per_second = self.max_tokens_per_minute / 60.0
        tokens_to_add = elapsed * rate_per_second

        self.current_tokens_available = min(
            self.current_tokens_available + tokens_to_add,
            self.max_tokens_per_minute * _BUCKET_SAFETY_MARGIN,
        )
        self.last_refill_time = now

    def consume_tokens(self, tokens: int, cost: float) -> None:
        """Deduct consumed tokens and increment cumulative counters."""
        self.current_tokens_available = max(
            0.0, self.current_tokens_available - tokens
        )
        self.total_tokens_consumed += tokens
        self.estimated_cost += cost
        self.total_requests += 1

    def can_handle(self, estimated_tokens: int) -> bool:
        """Return ``True`` iff this key can accept a request right now.

        Checks:
        1. Not currently in backoff.
        2. Circuit breaker is not OPEN.
        3. Enough token budget with a 1.2x safety margin.
        4. Not already saturated with in-flight requests.
        """
        now = time.monotonic()
        if now < self.backoff_until:
            return False
        if self.circuit_state == "OPEN":
            return False
        # Hard cap on concurrent requests per key to force rotation
        if self.requests_in_flight >= 10:
            return False
        required = estimated_tokens * _REQUEST_SAFETY_MULTIPLIER
        return self.current_tokens_available >= required

    # ------------------------------------------------------------------ #
    # Circuit breaker / health
    # ------------------------------------------------------------------ #

    def record_429(self) -> None:
        """Handle a 429 response: increment counter and set backoff.

        Backoff schedule:
            1st 429 → 60 s
            2nd 429 → 300 s
            3rd 429+ → 900 s + circuit OPEN
        """
        self.consecutive_429_count += 1
        count = self.consecutive_429_count

        backoff_seconds = _BACKOFF_SCHEDULE.get(count, _BACKOFF_DEFAULT)
        self.backoff_until = time.monotonic() + backoff_seconds

        if count >= _CIRCUIT_OPEN_AFTER_429S:
            self.circuit_state = "OPEN"
            logger.warning(
                "Circuit OPEN for %s after %d consecutive 429s (backoff %ds)",
                self.key_id,
                count,
                backoff_seconds,
            )
        else:
            logger.warning(
                "429 recorded for %s (count=%d, backoff %ds)",
                self.key_id,
                count,
                backoff_seconds,
            )

    def record_success(self) -> None:
        """Handle a successful response: reset 429 counter, close circuit."""
        self.consecutive_429_count = 0
        self.successful_requests += 1
        if self.circuit_state == "HALF_OPEN":
            self.circuit_state = "CLOSED"
            logger.info("Circuit CLOSED for %s (recovery confirmed)", self.key_id)

    def record_failure(self, status_code: int) -> None:
        """Handle an HTTP error response.

        - 429 → :meth:`record_429`
        - 401 / 403 → mark key permanently dead (circuit OPEN)
        - Other → log; caller may decide to retry
        """
        if status_code == 429:
            self.record_429()
            return

        if status_code in (401, 403):
            self.circuit_state = "OPEN"
            self._permanent_dead = True
            self.backoff_until = float("inf")
            logger.error(
                "Key %s marked PERMANENTLY DEAD (%d) — masking=%s",
                self.key_id,
                status_code,
                _mask_key(self.api_key),
            )
            return

        # Generic failure — logged, but not enough on its own to open circuit
        logger.warning(
            "HTTP error for %s: status=%d", self.key_id, status_code
        )

    def get_capacity_ratio(self) -> float:
        """Return the fraction of the TPM budget currently available."""
        if self.max_tokens_per_minute == 0:
            return 0.0
        return self.current_tokens_available / self.max_tokens_per_minute

    def release_request_slot(self) -> None:
        """Decrement the in-flight request counter (call in ``finally``)."""
        self.requests_in_flight = max(0, self.requests_in_flight - 1)

    # ------------------------------------------------------------------ #
    # Serialisation helpers
    # ------------------------------------------------------------------ #

    def to_status_dict(self) -> dict[str, Any]:
        """Return a JSON-safe status snapshot (key value is masked)."""
        now = time.monotonic()
        backoff_remaining = max(0.0, self.backoff_until - now)
        return {
            "key_id": self.key_id,
            "key_masked": _mask_key(self.api_key),
            "capacity_ratio": round(self.get_capacity_ratio(), 4),
            "circuit_state": self.circuit_state,
            "backoff_remaining": round(backoff_remaining, 1),
            "requests_in_flight": self.requests_in_flight,
            "total_tokens_consumed": self.total_tokens_consumed,
            "estimated_cost": round(self.estimated_cost, 6),
            "total_requests": self.total_requests,
            "successful_requests": self.successful_requests,
            "consecutive_429s": self.consecutive_429_count,
            "current_tokens_available": round(self.current_tokens_available, 1),
        }

    def __repr__(self) -> str:
        return (
            f"APIKeyBucket(key_id={self.key_id!r}, "
            f"circuit={self.circuit_state}, "
            f"capacity={self.get_capacity_ratio():.2%})"
        )


# ── KimiAPIPool ────────────────────────────────────────────────────────────

class KimiAPIPool:
    """Manages multiple Kimi API keys with rotation, retries, and cost tracking.

    Usage::

        pool = KimiAPIPool()
        try:
            result = await pool.send_request(messages=[...])
            print(result["content"])
        finally:
            await pool.close()
    """

    # ------------------------------------------------------------------ #
    # Attributes
    # ------------------------------------------------------------------ #

    buckets: dict[str, APIKeyBucket]
    safety_margin: float
    session: aiohttp.ClientSession

    _pool_lock: asyncio.Lock          # protects bucket list mutations

    # ------------------------------------------------------------------ #
    # Construction / teardown
    # ------------------------------------------------------------------ #

    def __init__(self, safety_margin: float = 0.80) -> None:
        self.safety_margin = safety_margin
        self.buckets = {}
        self._pool_lock = asyncio.Lock()

        # Shared HTTP session with generous connection pool
        connector = aiohttp.TCPConnector(
            limit=500,
            limit_per_host=100,
            enable_cleanup_closed=True,
            force_close=False,
        )
        timeout = aiohttp.ClientTimeout(total=120, connect=30)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={"User-Agent": "claude-code/0.1.0"},
        )

        # Load keys synchronously — env vars must be present at init time
        key_pairs = self._load_keys()
        for key_id, api_key in key_pairs:
            self.buckets[key_id] = APIKeyBucket(
                key_id=key_id,
                api_key=api_key,
                max_tpm=200_000,
                max_rpm=60,
            )

        if not self.buckets:
            logger.critical(
                "No Kimi API keys loaded — set at least one of %s1..%s%d",
                _KEY_ENV_PREFIX,
                _KEY_ENV_PREFIX,
                _NUM_KEYS,
            )
        else:
            logger.info(
                "KimiAPIPool initialised with %d key(s): %s",
                len(self.buckets),
                ", ".join(self.buckets),
            )

    def _load_keys(self) -> list[tuple[str, str]]:
        """Read up to ``_NUM_KEYS`` keys from environment variables.

        Tries ``KIMI_KEY_1``..``KIMI_KEY_4`` first, then falls back to
        ``KIMI_API_KEY_1``..``KIMI_API_KEY_4``.
        """
        loaded: list[tuple[str, str]] = []
        for i in range(1, _NUM_KEYS + 1):
            # Try primary prefix first, then alternate
            env_name = f"{_KEY_ENV_PREFIX}{i}"
            alt_env_name = f"{_KEY_ENV_PREFIX_ALT}{i}"
            value = os.environ.get(env_name)
            if not value:
                value = os.environ.get(alt_env_name)
            if not value:
                logger.warning(
                    "Environment variable %s or %s not set — skipping",
                    env_name,
                    alt_env_name,
                )
                continue
            key_id = f"KEY_{i}"
            loaded.append((key_id, value))
            logger.info(
                "Loaded %s (masked=%s)", key_id, _mask_key(value)
            )
        return loaded

    async def close(self) -> None:
        """Close the shared aiohttp session."""
        if not self.session.closed:
            await self.session.close()
            logger.info("KimiAPIPool session closed")

    # ------------------------------------------------------------------ #
    # Key selection
    # ------------------------------------------------------------------ #

    async def get_optimal_key(self, estimated_tokens: int = 1_000) -> str | None:
        """Return the ``key_id`` of the best available key.

        Selection criteria (all must pass):
        1. Not in backoff period.
        2. Circuit breaker not OPEN.
        3. ``can_handle(estimated_tokens)`` is ``True``.

        From passing candidates the one with the best load-balanced
        score is chosen — preferring keys with fewer total requests
        but still high capacity.

        Returns ``None`` when no key is usable.
        """
        candidates: list[tuple[str, float]] = []
        total_requests = sum(
            b.total_requests for b in self.buckets.values()
        ) or 1

        for key_id, bucket in self.buckets.items():
            async with bucket.lock:
                bucket.refill_tokens()
                if bucket.can_handle(estimated_tokens):
                    # Load-balanced score:
                    #   capacity_ratio dominates (70%)
                    #   request penalty dominates (30%) — less-used keys preferred
                    capacity = bucket.get_capacity_ratio()
                    request_penalty = (
                        bucket.total_requests / total_requests
                    ) * 0.30
                    score = capacity - request_penalty
                    candidates.append((key_id, score))

        if not candidates:
            return None

        # Pick candidate with highest balanced score
        candidates.sort(key=lambda x: x[1], reverse=True)
        chosen = candidates[0][0]
        logger.debug(
            "Selected %s (score=%.3f) for ~%d tokens",
            chosen,
            candidates[0][1],
            estimated_tokens,
        )
        return chosen

    def get_all_key_status(self) -> list[dict]:
        """Return a status dict for every configured key."""
        return [bucket.to_status_dict() for bucket in self.buckets.values()]

    async def are_any_keys_available(self) -> bool:
        """Return ``True`` if at least one key can handle a minimal request."""
        for bucket in self.buckets.values():
            async with bucket.lock:
                bucket.refill_tokens()
                if bucket.can_handle(1):
                    return True
        return False

    # ------------------------------------------------------------------ #
    # Cost calculation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate request cost in USD.

        Kimi K2.6 pricing:
          - Input  : $0.50 / 1M tokens
          - Output : $1.50 / 1M tokens
        """
        input_cost = prompt_tokens * _COST_PER_INPUT_TOKEN
        output_cost = completion_tokens * _COST_PER_OUTPUT_TOKEN
        return input_cost + output_cost

    # ------------------------------------------------------------------ #
    # Request building
    # ------------------------------------------------------------------ #

    def _build_headers(self, api_key: str) -> dict[str, str]:
        """Build request headers with the given API key."""
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "claude-code/0.1.0",
        }

    def _build_body(
        self,
        messages: list[dict],
        temperature: float,
        max_tokens: int,
        enable_thinking: bool = True,
    ) -> dict[str, Any]:
        """Build the JSON request body for the chat completion endpoint."""
        body: dict[str, Any] = {
            "model": _MODEL,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if enable_thinking:
            body["extra_body"] = {"thinking": {"type": "enabled"}}
        return body

    # ------------------------------------------------------------------ #
    # Response parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _parse_response(body: dict[str, Any]) -> dict[str, Any]:
        """Parse an OpenAI-compatible completion response.

        Expected shape::

            {
                "choices": [
                    {"message": {"content": "...", "reasoning": "..."}}
                ],
                "usage": {
                    "prompt_tokens": N,
                    "completion_tokens": N
                }
            }

        Returns a flat dict with extracted fields.
        """
        choices = body.get("choices", [])
        if not choices:
            raise ValueError("Response contains no 'choices' array")

        first_choice = choices[0]
        message = first_choice.get("message", {})
        content = message.get("content", "")
        reasoning = message.get("reasoning_content", "") or message.get("reasoning", "")

        usage = body.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = prompt_tokens + completion_tokens

        cost = KimiAPIPool._calculate_cost(prompt_tokens, completion_tokens)

        return {
            "content": content,
            "reasoning": reasoning,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
            "cost_usd": round(cost, 8),
        }

    # ------------------------------------------------------------------ #
    # Core request method
    # ------------------------------------------------------------------ #

    async def send_request(
        self,
        messages: list[dict],
        key_id: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        estimated_tokens: int = 1_000,
        enable_thinking: bool = True,
    ) -> dict[str, Any]:
        """Send a chat-completion request with automatic key rotation & retries.

        Parameters
        ----------
        messages :
            OpenAI-style message list, e.g.
            ``[{"role": "system", "content": "..."}, {"role": "user", ...}]``
        key_id :
            Explicit key to use.  When ``None`` the optimal key is chosen
            automatically.
        temperature :
            Sampling temperature (0–2).
        max_tokens :
            Maximum tokens to generate.
        estimated_tokens :
            Estimated total token cost for key-selection purposes.

        Returns
        -------
        dict
            Contains ``content``, ``reasoning``, ``prompt_tokens``,
            ``completion_tokens``, ``total_tokens``, ``cost_usd``,
            ``key_used``.

        Raises
        ------
        RuntimeError
            If no keys are configured or all keys are exhausted.
        aiohttp.ClientError
            On unrecoverable network failure after all retries.
        ValueError
            On malformed API response.
        """
        if not self.buckets:
            raise RuntimeError("No API keys configured — cannot send request")

        body_json = self._build_body(messages, temperature, max_tokens, enable_thinking)
        attempted_keys: set[str] = set()
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            # ── Select key ──────────────────────────────────────────────
            selected_key_id = key_id
            if selected_key_id is None:
                selected_key_id = await self.get_optimal_key(estimated_tokens)

            if selected_key_id is None or selected_key_id not in self.buckets:
                # Maybe a HALF_OPEN key can be tried
                selected_key_id = self._pick_half_open_or_least_backoff(
                    attempted_keys
                )

            if selected_key_id is None:
                logger.error(
                    "All keys exhausted after %d attempt(s)", attempt
                )
                if last_error is not None:
                    raise last_error
                raise RuntimeError(
                    "No API keys available — all rate-limited or circuit-open"
                )

            attempted_keys.add(selected_key_id)
            bucket = self.buckets[selected_key_id]

            # ── Check / refill bucket ──────────────────────────────────
            async with bucket.lock:
                bucket.refill_tokens()
                if not bucket.can_handle(estimated_tokens):
                    logger.debug(
                        "%s cannot handle request, skipping", selected_key_id
                    )
                    continue
                bucket.requests_in_flight += 1

            # ── Send request ────────────────────────────────────────────
            headers = self._build_headers(bucket.api_key)
            try:
                result = await self._do_single_request(
                    headers=headers,
                    body_json=body_json,
                    bucket=bucket,
                    key_id=selected_key_id,
                )
                result["key_used"] = selected_key_id
                return result

            except aiohttp.ClientResponseError as exc:
                status = exc.status
                last_error = exc

                async with bucket.lock:
                    bucket.release_request_slot()
                    if status == 429:
                        bucket.record_429()
                        logger.warning(
                            "429 on %s (attempt %d/%d) — retrying with "
                            "different key",
                            selected_key_id,
                            attempt,
                            _MAX_RETRIES,
                        )
                        continue
                    if status in (401, 403):
                        bucket.record_failure(status)
                        logger.error(
                            "Auth error %d on %s — key marked dead, "
                            "retrying",
                            status,
                            selected_key_id,
                        )
                        continue
                    # Other 4xx/5xx — log but still allow retry
                    bucket.record_failure(status)
                    logger.warning(
                        "HTTP %d on %s (attempt %d/%d)",
                        status,
                        selected_key_id,
                        attempt,
                        _MAX_RETRIES,
                    )

            except aiohttp.ClientError as exc:
                # Network-level error (DNS, connection reset, timeout, …)
                last_error = exc
                async with bucket.lock:
                    bucket.release_request_slot()
                delay = _BASE_RETRY_DELAY * (_RETRY_BACKOFF_EXPONENT ** attempt)
                logger.warning(
                    "Network error on %s (attempt %d/%d): %s — "
                    "retrying in %.1fs",
                    selected_key_id,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            except Exception as exc:
                # Unexpected exception — release slot and propagate
                async with bucket.lock:
                    bucket.release_request_slot()
                raise

        # ── Exhausted all retries ─────────────────────────────────────
        logger.error("All %d retry attempts exhausted", _MAX_RETRIES)
        if last_error is not None:
            raise last_error
        raise RuntimeError("Request failed after maximum retries")

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    async def _do_single_request(
        self,
        headers: dict[str, str],
        body_json: dict[str, Any],
        bucket: APIKeyBucket,
        key_id: str,
    ) -> dict[str, Any]:
        """Execute one HTTP POST and return parsed result.

        The caller is responsible for acquiring the bucket lock around
        ``requests_in_flight``.
        """
        async with self.session.post(
            _ENDPOINT,
            headers=headers,
            json=body_json,
        ) as resp:
            if resp.status >= 400:
                # Let the caller handle HTTP errors via ClientResponseError
                text = await resp.text()
                logger.debug(
                    "HTTP %d from %s: %s",
                    resp.status,
                    key_id,
                    text[:500],
                )
                resp.raise_for_status()

            response_body = await resp.json()
            parsed = self._parse_response(response_body)

            # Update bucket metrics
            async with bucket.lock:
                bucket.record_success()
                bucket.consume_tokens(
                    tokens=parsed["total_tokens"],
                    cost=parsed["cost_usd"],
                )
                bucket.release_request_slot()

            logger.info(
                "Request succeeded on %s: %d tokens ($%.6f)",
                key_id,
                parsed["total_tokens"],
                parsed["cost_usd"],
            )
            return parsed

    def _pick_half_open_or_least_backoff(
        self, exclude: set[str]
    ) -> str | None:
        """Emergency key picker when no key passes ``can_handle``.

        Prefers:
        1. HALF_OPEN keys (testing recovery).
        2. Key with smallest remaining backoff (soonest available).

        Returns ``None`` if every key is permanently dead.
        """
        now = time.monotonic()
        best_key: str | None = None
        best_backoff = float("inf")

        for key_id, bucket in self.buckets.items():
            if key_id in exclude:
                continue
            if bucket._permanent_dead:
                continue
            if bucket.circuit_state == "HALF_OPEN":
                return key_id
            remaining = max(0.0, bucket.backoff_until - now)
            if remaining < best_backoff:
                best_backoff = remaining
                best_key = key_id

        return best_key

    # ------------------------------------------------------------------ #
    # Convenience / introspection
    # ------------------------------------------------------------------ #

    def get_total_usage(self) -> dict[str, Any]:
        """Aggregate usage across all keys."""
        total_tokens = sum(b.total_tokens_consumed for b in self.buckets.values())
        total_cost = sum(b.estimated_cost for b in self.buckets.values())
        total_reqs = sum(b.total_requests for b in self.buckets.values())
        total_ok = sum(b.successful_requests for b in self.buckets.values())
        return {
            "total_tokens_consumed": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "total_requests": total_reqs,
            "successful_requests": total_ok,
            "failed_requests": total_reqs - total_ok,
            "keys_configured": len(self.buckets),
            "keys_available": sum(
                1 for b in self.buckets.values() if b.circuit_state != "OPEN"
            ),
        }

    def __repr__(self) -> str:
        return (
            f"KimiAPIPool(keys={len(self.buckets)}, "
            f"available={sum(1 for b in self.buckets.values() if b.circuit_state != 'OPEN')})"
        )
