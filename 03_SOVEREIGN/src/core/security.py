"""Security-hardened configuration defaults.

Defense in depth: all system components import from here for consistent
security posture. Hardened defaults prevent misconfiguration.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Security Configuration
# ---------------------------------------------------------------------------

@dataclass
class SecurityConfig:
    """Security-hardened configuration defaults.

    All components should import SECURITY from this module to ensure
    consistent enforcement of security policies.
    """

    # ── Network security ────────────────────────────────────────────────

    #: Bind to localhost only (127.0.0.1) — never 0.0.0.0 in production
    bind_localhost_only: bool = True

    #: Refuse plain HTTP in production environments
    require_tls: bool = True

    #: Default TLS minimum version (1.2 = TLS 1.2)
    min_tls_version: str = "1.2"

    # ── Input validation ────────────────────────────────────────────────

    #: Maximum hostname length (RFC 1123)
    max_hostname_length: int = 253

    #: Maximum HTTP header size in bytes
    max_header_size: int = 8192

    #: Maximum request body size (10 MB)
    max_body_size: int = 10 * 1024 * 1024

    #: Maximum URL length
    max_url_length: int = 2048

    #: Valid hostname label length
    max_label_length: int = 63

    # ── Rate limiting ────────────────────────────────────────────────────

    #: Requests per window before throttling
    rate_limit_requests: int = 100

    #: Window duration in seconds
    rate_limit_window: int = 60

    # ── Cryptography ─────────────────────────────────────────────────────

    #: Minimum key length in bytes (256-bit)
    min_key_length: int = 32

    #: CA private keys MUST be encrypted at rest
    require_key_encryption: bool = True

    #: Allowed key encryption algorithms
    allowed_key_encryption_algos: tuple = ("aes-256-gcm", "aes-192-gcm", "chacha20-poly1305")

    # ── Audit ───────────────────────────────────────────────────────────

    #: Log all API calls for audit trail
    log_all_api_calls: bool = True

    #: Mask sensitive data in logs (API keys, tokens)
    mask_sensitive_data: bool = True

    #: Mask character for redacted values
    mask_char: str = "*"


# Global security config — import this in all modules
SECURITY = SecurityConfig()


# ---------------------------------------------------------------------------
# Validation Functions
# ---------------------------------------------------------------------------

# Compiled regex for hostname validation (computed once, reused)
_HOSTNAME_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.?$"
)


def validate_hostname(hostname: str) -> bool:
    """Validate hostname against security config.

    Args:
        hostname: The hostname to validate.

    Returns:
        True if hostname is valid, False otherwise.
    """
    if not hostname:
        return False

    if len(hostname) > SECURITY.max_hostname_length:
        return False

    if not _HOSTNAME_PATTERN.match(hostname):
        return False

    return True


def validate_hostname_strict(hostname: str) -> tuple[bool, str]:
    """Validate hostname with detailed error message.

    Returns:
        Tuple of (is_valid, error_message).
    """
    if not hostname:
        return False, "Hostname cannot be empty"

    if len(hostname) > SECURITY.max_hostname_length:
        return False, f"Hostname exceeds {SECURITY.max_hostname_length} characters"

    # Check for valid characters
    if not _HOSTNAME_PATTERN.match(hostname):
        return False, "Invalid hostname format (must be valid DNS name)"

    # Reject localhost aliases
    if hostname.lower() in ("localhost", "localhost.localdomain"):
        return False, "localhost aliases not allowed"

    return True, ""


def validate_header_size(headers: dict) -> bool:
    """Validate total header size is within limits.

    Args:
        headers: Dictionary of header name -> value pairs.

    Returns:
        True if headers are within size limits, False otherwise.
    """
    total = sum(len(str(k)) + len(str(v)) for k, v in headers.items())
    return total <= SECURITY.max_header_size


def validate_body_size(content_length: Optional[int]) -> bool:
    """Validate request body size is within limits.

    Args:
        content_length: The Content-Length header value (may be None).

    Returns:
        True if body size is acceptable, False otherwise.
    """
    if content_length is None:
        return True  # Will be validated during streaming

    return content_length <= SECURITY.max_body_size


def validate_url_length(url: str) -> bool:
    """Validate URL length is within limits.

    Args:
        url: The URL to validate.

    Returns:
        True if URL is within length limits, False otherwise.
    """
    return len(url) <= SECURITY.max_url_length


def validate_key_length(key: str) -> bool:
    """Validate cryptographic key meets minimum length.

    Args:
        key: The key bytes or string to validate.

    Returns:
        True if key meets minimum length, False otherwise.
    """
    key_bytes = key if isinstance(key, bytes) else key.encode("utf-8")
    return len(key_bytes) >= SECURITY.min_key_length


def mask_sensitive(value: str, show_chars: int = 4) -> str:
    """Mask sensitive data for logging.

    Args:
        value: The sensitive value to mask.
        show_chars: Number of characters to show at start and end.

    Returns:
        Masked string (e.g., "sk-ant-****XXXX").
    """
    if not value:
        return "(none)"

    if len(value) <= show_chars * 2:
        return SECURITY.mask_char * len(value)

    return (
        f"{value[:show_chars]}"
        f"{SECURITY.mask_char * 4}"
        f"{value[-show_chars:]}"
    )


def is_valid_key_format(key: str, provider: str) -> bool:
    """Validate API key format against known provider patterns.

    Args:
        key: The API key string to validate.
        provider: The provider name (e.g., "anthropic", "openai").

    Returns:
        True if key format is valid or provider unknown, False otherwise.
    """
    if not key:
        return False

    provider_lower = provider.lower() if provider else ""

    # Known provider prefix validation
    if provider_lower == "anthropic":
        return key.startswith("sk-ant-")
    elif provider_lower in ("google", "gemini"):
        return key.startswith("AIza")
    elif provider_lower in ("openai", "kimi", "groq", "mistral", "custom"):
        return key.startswith("sk-")

    # Unknown provider — allow but log (already handled by caller)
    return True


# ---------------------------------------------------------------------------
# Rate Limiter (simple token bucket)
# ---------------------------------------------------------------------------

class RateLimiter:
    """Simple token-bucket rate limiter for API calls."""

    def __init__(
        self,
        requests: int = SECURITY.rate_limit_requests,
        window: int = SECURITY.rate_limit_window,
    ):
        self._requests = requests
        self._window = window
        self._calls: list[float] = []

    def is_allowed(self) -> bool:
        """Check if a new request is allowed under rate limit.

        Returns:
            True if allowed, False if rate limited.
        """
        import time
        now = time.monotonic()
        # Remove calls outside the window
        self._calls = [t for t in self._calls if now - t < self._window]
        if len(self._calls) >= self._requests:
            return False
        self._calls.append(now)
        return True

    def reset(self):
        """Reset the rate limiter."""
        self._calls.clear()


# Default rate limiter instance
_default_limiter = RateLimiter()


def is_rate_limited() -> bool:
    """Check if current request would exceed rate limits.

    Returns:
        True if rate limited, False otherwise.
    """
    return not _default_limiter.is_allowed()