"""Centralized input sanitization for all external input.

Trust nothing — validate and sanitize all external input at the boundary.
This module provides a single source of truth for input validation.

SECURITY NOTE:
    All validation methods raise ValueError on invalid input.
    Callers should catch these exceptions and handle them appropriately.
"""
from __future__ import annotations
import ipaddress
import re
from pathlib import Path
from typing import Any, List, Optional
from urllib.parse import urlparse

log = __import__('logging').getLogger(__name__)


class Sanitizer:
    """Centralized input sanitization for all external input."""

    # DNS hostname regex (RFC 1123 compliant)
    # Allows: 1-253 chars, labels 1-63 chars, alphanumeric with hyphens
    # No leading/trailing hyphen, no leading dot (optional trailing dot for FQDN)
    HOSTNAME_RE = re.compile(
        r'^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.[A-Za-z0-9-]{1,63})*\.?$'
    )

    # IP address patterns for private network detection
    PRIVATE_IP_PATTERNS: List[str] = [
        '127.',      # Loopback
        '10.',       # Class A private
        '172.16.', '172.17.', '172.18.', '172.19.',  # Class B private (16-31)
        '172.20.', '172.21.', '172.22.', '172.23.',
        '172.24.', '172.25.', '172.26.', '172.27.',
        '172.28.', '172.29.', '172.30.', '172.31.',
        '192.168.',  # Class C private
        '169.254.',  # Link-local (APIPA)
        '0.',        # Invalid/loopback
    ]

    # Valid API key prefixes by provider
    VALID_KEY_PREFIXES: dict[str, str] = {
        "sk-ant-": "Anthropic",
        "sk-": "OpenAI/Generic",
        "AIza": "Google",
    }

    @classmethod
    def sanitize_hostname(cls, hostname: str) -> str:
        """Sanitize and validate hostname.

        Args:
            hostname: The hostname string to validate.

        Returns:
            The sanitized hostname (lowercased, stripped).

        Raises:
            ValueError: If hostname is empty, too long, or invalid format.
        """
        if not hostname:
            raise ValueError("Hostname cannot be empty")

        hostname = hostname.strip().lower()

        if len(hostname) > 253:
            raise ValueError("Hostname too long (max 253)")

        if not cls.HOSTNAME_RE.match(hostname):
            raise ValueError(f"Invalid hostname format: {hostname}")

        return hostname

    @classmethod
    def sanitize_url(cls, url: str, allow_private: bool = False) -> str:
        """Sanitize and validate URL.

        Args:
            url: The URL string to validate.
            allow_private: If True, allow private IP addresses in URLs.

        Returns:
            The sanitized URL string.

        Raises:
            ValueError: If URL is missing scheme, has invalid scheme,
                       or contains private IP when allow_private=False.
        """
        parsed = urlparse(url)

        if not parsed.scheme:
            raise ValueError("URL must have scheme")

        if parsed.scheme not in ('http', 'https'):
            raise ValueError(f"Invalid scheme: {parsed.scheme}")

        if not allow_private:
            host = parsed.hostname or ''
            # Check for private IP patterns
            for pattern in cls.PRIVATE_IP_PATTERNS:
                if host.startswith(pattern):
                    raise ValueError(f"Private IP not allowed: {host}")

            # Also validate if hostname is actually an IP address
            try:
                addr = ipaddress.ip_address(host)
                if addr.is_private or addr.is_loopback:
                    raise ValueError(f"Private IP not allowed: {host}")
            except ValueError:
                pass  # Not an IP address, might be hostname

        return url

    @classmethod
    def sanitize_api_key(cls, key: str) -> str:
        """Sanitize API key for safe logging.

        Shows only first 4 and last 4 characters to allow identification
        while protecting the key value.

        Args:
            key: The API key string to sanitize.

        Returns:
            Sanitized key string (e.g., "sk-an...xxxx") or "(empty)".
        """
        if not key:
            return "(empty)"
        if len(key) <= 8:
            return "***"
        return f"{key[:4]}...{key[-4:]}"

    @classmethod
    def sanitize_path(cls, path: str, base: Path) -> Path:
        """Sanitize file path to prevent directory traversal attacks.

        Resolves the path and ensures it stays within the allowed base directory.

        Args:
            path: The file path to sanitize.
            base: The base directory that path must be within.

        Returns:
            The resolved Path object.

        Raises:
            ValueError: If path traversal is detected or path escapes base.
        """
        path = Path(path).resolve()
        base = base.resolve()

        if not str(path).startswith(str(base)):
            raise ValueError("Path traversal detected")

        return path

    @classmethod
    def validate_api_key_format(cls, key: str, provider: str) -> bool:
        """Validate that API key format matches provider expectations.

        Args:
            key: The API key string to validate.
            provider: The provider name (e.g., 'anthropic', 'openai').

        Returns:
            True if key format is valid for the provider, False otherwise.
        """
        if not key:
            return False

        provider_lower = provider.lower() if provider else ""

        # Check key prefix matches expected provider
        if provider_lower == "anthropic":
            return key.startswith("sk-ant-")
        elif provider_lower in ("google", "gemini"):
            return key.startswith("AIza")
        elif provider_lower in ("openai", "kimi", "groq", "mistral", "custom"):
            return key.startswith("sk-")

        # Unknown provider - allow but log for monitoring
        log.warning("Unknown provider '%s' for key validation", provider)
        return True

    @classmethod
    def validate_ip(cls, ip: str, allow_private: bool = False) -> str:
        """Validate IP address.

        Args:
            ip: The IP address string to validate.
            allow_private: If True, allow private/reserved IP addresses.

        Returns:
            The validated IP address string.

        Raises:
            ValueError: If IP is invalid or private when not allowed.
        """
        try:
            addr = ipaddress.ip_address(ip)
            if not allow_private:
                if addr.is_private or addr.is_loopback:
                    raise ValueError(f"Private/loopback IP not allowed: {ip}")
            return ip
        except ValueError:
            raise ValueError(f"Invalid IP address: {ip}")

    @classmethod
    def sanitize_host_entry(cls, ip: str, hostname: str, allow_private: bool = False) -> tuple[str, str]:
        """Sanitize and validate a hosts file entry (IP + hostname pair).

        Args:
            ip: The IP address string.
            hostname: The hostname string.
            allow_private: If True, allow private IP addresses.

        Returns:
            Tuple of (sanitized_ip, sanitized_hostname).

        Raises:
            ValueError: If either IP or hostname is invalid.
        """
        # Validate and sanitize IP
        sanitized_ip = cls.validate_ip(ip, allow_private=allow_private)

        # Validate and sanitize hostname
        # Block localhost aliases
        hostname_lower = hostname.lower().strip()
        if hostname_lower in ("localhost", "localhost.localdomain"):
            raise ValueError("Cannot redirect localhost")

        sanitized_hostname = cls.sanitize_hostname(hostname)

        return sanitized_ip, sanitized_hostname