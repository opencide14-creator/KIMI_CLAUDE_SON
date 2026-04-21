"""
core — VEKIL-KAAN RAG OS core primitives.

Modules:
  exceptions — full typed exception hierarchy (zero-fallback)
  config     — system configuration (pydantic, frozen)
  hashing    — BLAKE2b-256 + SHA-256 utilities
  crypto     — KRAL Ed25519 key management + HMAC event signing
"""

from core.exceptions import VekilKaanError
from core.config import SystemConfig, get_config
from core.hashing import blake2b_256, sha256_hex, compute_root_hash
from core.crypto import KralIdentity, load_kral_identity

__all__ = [
    "VekilKaanError",
    "SystemConfig",
    "get_config",
    "blake2b_256",
    "sha256_hex",
    "compute_root_hash",
    "KralIdentity",
    "load_kral_identity",
]
