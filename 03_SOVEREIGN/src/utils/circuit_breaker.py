"""Circuit Breaker Pattern — Fail securely when backends are unavailable.

Prevents cascading failures by stopping requests to failing backends,
allowing them time to recover.

Based on: Thinker-2 Security Oracle - Fail securely
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable, Optional, TypeVar

T = TypeVar("T")

log = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = "closed"       # Normal operation, requests pass through
    OPEN = "open"           # Failing, requests are rejected immediately
    HALF_OPEN = "half_open"  # Testing recovery, one request allowed


class CircuitOpenError(Exception):
    """Raised when a request is rejected because the circuit is OPEN."""

    def __init__(self, circuit_name: str, retry_after: Optional[float] = None):
        self.circuit_name = circuit_name
        self.retry_after = retry_after
        msg = f"Circuit '{circuit_name}' is OPEN"
        if retry_after is not None:
            msg += f" (retry after {retry_after:.1f}s)"
        super().__init__(msg)


class CircuitBreaker:
    """Circuit breaker for external service calls.

    Monitors failures and opens the circuit when the failure threshold is reached.
    In OPEN state, requests are rejected immediately. After the recovery timeout,
    the circuit transitions to HALF_OPEN to test if the backend has recovered.

    Args:
        name: Identifier for this circuit (e.g., "kimi", "ollama")
        failure_threshold: Number of consecutive failures before opening (default 5)
        recovery_timeout: Seconds to wait before testing recovery (default 30.0)
        expected_exception: Exception type to catch and count as failure (default Exception)
    """

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        expected_exception: type = Exception,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout <= 0:
            raise ValueError("recovery_timeout must be positive")

        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._success_count = 0  # Track successes in half-open state

    @property
    def state(self) -> CircuitState:
        """Get current circuit state, checking if OPEN should transition to HALF_OPEN."""
        if self._state == CircuitState.OPEN:
            if self._last_failure_time is not None:
                elapsed = time.time() - self._last_failure_time
                if elapsed >= self.recovery_timeout:
                    log.info("Circuit '%s' transitioning OPEN -> HALF_OPEN (timeout reached)", self.name)
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
        return self._state

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count."""
        return self._failure_count

    @property
    def is_closed(self) -> bool:
        """True if circuit is CLOSED (normal operation)."""
        return self.state == CircuitState.CLOSED

    @property
    def is_open(self) -> bool:
        """True if circuit is OPEN (rejecting requests)."""
        return self.state == CircuitState.OPEN

    def close(self) -> None:
        """Manually reset the circuit to CLOSED state."""
        self._failure_count = 0
        self._last_failure_time = None
        self._success_count = 0
        if self._state != CircuitState.CLOSED:
            log.info("Circuit '%s' manually closed", self.name)
        self._state = CircuitState.CLOSED

    def record_success(self) -> None:
        """Record a successful call (called internally, not by user)."""
        if self._state == CircuitState.HALF_OPEN:
            self._success_count += 1
            if self._success_count >= 1:  # One successful call closes the circuit
                log.info("Circuit '%s' HALF_OPEN -> CLOSED (recovery successful)", self.name)
                self._on_success()
        else:
            # Normal success in CLOSED state resets failure count
            self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed call (called internally, not by user)."""
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN reopens the circuit
            log.warning("Circuit '%s' HALF_OPEN -> OPEN (recovery failed)", self.name)
            self._state = CircuitState.OPEN
        elif self._failure_count >= self.failure_threshold:
            log.warning(
                "Circuit '%s' OPENED after %d consecutive failures (threshold: %d)",
                self.name,
                self._failure_count,
                self.failure_threshold,
            )
            self._state = CircuitState.OPEN

    def call(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute a function through the circuit breaker.

        Args:
            func: The function to call
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            The result of func(*args, **kwargs)

        Raises:
            CircuitOpenError: If the circuit is OPEN and request is rejected
            Exception: Any exception from the wrapped function
        """
        if self.state == CircuitState.OPEN:
            raise CircuitOpenError(
                self.name,
                retry_after=self._get_retry_after(),
            )

        try:
            result = func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exception as e:
            self.record_failure()
            raise

    async def call_async(self, func: Callable[..., T], *args, **kwargs) -> T:
        """Execute an async function through the circuit breaker.

        Args:
            func: The async function to call
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            The result of await func(*args, **kwargs)

        Raises:
            CircuitOpenError: If the circuit is OPEN and request is rejected
            Exception: Any exception from the wrapped function
        """
        if self.state == CircuitState.OPEN:
            raise CircuitOpenError(
                self.name,
                retry_after=self._get_retry_after(),
            )

        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except self.expected_exception as e:
            self.record_failure()
            raise

    def _on_success(self) -> None:
        """Handle successful call (internal)."""
        self._failure_count = 0
        self._state = CircuitState.CLOSED
        self._success_count = 0

    def _on_failure(self) -> None:
        """Handle failed call (internal)."""
        self._failure_count += 1
        self._last_failure_time = time.time()
        if self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            log.warning(
                "Circuit '%s' opened after %d failures",
                self.name,
                self._failure_count,
            )

    def _get_retry_after(self) -> Optional[float]:
        """Calculate seconds until circuit transitions to HALF_OPEN."""
        if self._state == CircuitState.OPEN and self._last_failure_time is not None:
            elapsed = time.time() - self._last_failure_time
            return max(0.0, self.recovery_timeout - elapsed)
        return None

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name={self.name!r}, state={self.state.value}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )


# ── Per-backend circuit breakers for the gateway ──────────────────────────────

def _build_circuit_breakers() -> dict[str, CircuitBreaker]:
    """Create circuit breakers for each known backend provider."""
    return {
        # Kimi (Moonshot) — more aggressive failure threshold due to rate limits
        "kimi": CircuitBreaker(
            name="kimi",
            failure_threshold=3,
            recovery_timeout=60.0,
            expected_exception=Exception,
        ),
        # Ollama — local, may be slower to start
        "ollama": CircuitBreaker(
            name="ollama",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # OpenAI — standard threshold
        "openai": CircuitBreaker(
            name="openai",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # Anthropic — standard threshold
        "anthropic": CircuitBreaker(
            name="anthropic",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # Gemini — standard threshold
        "gemini": CircuitBreaker(
            name="gemini",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # Groq — standard threshold
        "groq": CircuitBreaker(
            name="groq",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # Mistral — standard threshold
        "mistral": CircuitBreaker(
            name="mistral",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
        # Default catch-all
        "default": CircuitBreaker(
            name="default",
            failure_threshold=5,
            recovery_timeout=30.0,
            expected_exception=Exception,
        ),
    }


# Module-level circuit breakers registry
_circuit_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(provider: str) -> CircuitBreaker:
    """Get or create a circuit breaker for a provider.

    Args:
        provider: Provider name (e.g., "kimi", "openai")

    Returns:
        The CircuitBreaker instance for this provider
    """
    provider_lower = provider.lower()
    if provider_lower not in _circuit_breakers:
        _circuit_breakers[provider_lower] = CircuitBreaker(
            name=provider_lower,
            failure_threshold=5,
            recovery_timeout=30.0,
        )
    return _circuit_breakers[provider_lower]


def get_all_circuit_breakers() -> dict[str, CircuitBreaker]:
    """Return all registered circuit breakers (for monitoring/UI)."""
    return dict(_circuit_breakers)


def reset_all_circuits() -> None:
    """Reset all circuit breakers to CLOSED (for testing/maintenance)."""
    for cb in _circuit_breakers.values():
        cb.close()
    log.info("All circuit breakers reset to CLOSED state")