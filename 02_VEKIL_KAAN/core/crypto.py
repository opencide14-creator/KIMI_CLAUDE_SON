"""
core/crypto.py — KRAL Ed25519 key management and event signing.

Identity:
  Owner:       KRAL
  System:      SDCK-UAGL-v2.0
  Algorithm:   Ed25519
  Fingerprint: 629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52
  Public key:  7f276ad75d301a05e61f90d4423ed75118c39f40a9ae4bb1f11523cf39855bf1

Two signing layers:
  v1 (default):  HMAC-SHA256 with shared secret — fast, no key file required
  v2 (full):     Ed25519 — loaded from kral_private.pem, full cryptographic proof

Event signatures use v1 internally (both agents share the HMAC secret via config).
Ed25519 is used for:
  - Law registry seal signatures (immutable proof of law state at boot)
  - Cross-agent brotherhood bond verification (BOUND.md oath signing)
  - Audit log integrity checkpoints

Private key is NEVER stored in code. Loaded from KRAL_PRIVATE_KEY_PATH at boot.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

from core.exceptions import (
    KeyLoadError,
    SignatureVerificationFailed,
    FingerprintMismatch,
    CryptoError,
)
from core.hashing import sha256_hex


# ── KRAL identity constants ───────────────────────────────────────────────────

KRAL_EXPECTED_FINGERPRINT = "629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52"
KRAL_PUBLIC_KEY_HEX = "7f276ad75d301a05e61f90d4423ed75118c39f40a9ae4bb1f11523cf39855bf1"


@dataclass(frozen=True)
class KralIdentity:
    """Loaded KRAL key pair. Immutable after construction."""
    private_key: Optional[Ed25519PrivateKey]  # None if only public key loaded
    public_key: Ed25519PublicKey
    fingerprint: str
    public_key_hex: str

    def has_private_key(self) -> bool:
        return self.private_key is not None


# ── Key loader ────────────────────────────────────────────────────────────────

def load_kral_identity(
    private_key_path: Path,
    public_key_path: Path,
    verify_fingerprint: bool = True,
) -> KralIdentity:
    """
    Load KRAL Ed25519 key pair from PEM files.
    Verifies fingerprint against expected KRAL identity on load.
    Raises KeyLoadError or FingerprintMismatch on failure.
    """
    # Load public key
    try:
        pub_pem = public_key_path.read_bytes()
        public_key = serialization.load_pem_public_key(pub_pem)
        if not isinstance(public_key, Ed25519PublicKey):
            raise KeyLoadError(f"Public key is not Ed25519: {public_key_path}")
    except (OSError, ValueError) as e:
        raise KeyLoadError(f"Failed to load public key from {public_key_path}: {e}") from e

    # Derive public key hex for verification
    pub_raw = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    pub_hex = pub_raw.hex()

    # Fingerprint = SHA-256 of raw public key bytes (matching kral_key_info.json)
    fingerprint = sha256_hex(pub_raw)

    if verify_fingerprint:
        if fingerprint != KRAL_EXPECTED_FINGERPRINT:
            raise FingerprintMismatch(
                f"Public key fingerprint mismatch.\n"
                f"  Expected: {KRAL_EXPECTED_FINGERPRINT}\n"
                f"  Got:      {fingerprint}"
            )
        if pub_hex != KRAL_PUBLIC_KEY_HEX:
            raise FingerprintMismatch(
                f"Public key hex mismatch.\n"
                f"  Expected: {KRAL_PUBLIC_KEY_HEX}\n"
                f"  Got:      {pub_hex}"
            )

    # Load private key (optional — system can run verify-only without it)
    private_key: Optional[Ed25519PrivateKey] = None
    if private_key_path.exists():
        try:
            priv_pem = private_key_path.read_bytes()
            loaded = serialization.load_pem_private_key(priv_pem, password=None)
            if not isinstance(loaded, Ed25519PrivateKey):
                raise KeyLoadError(f"Private key is not Ed25519: {private_key_path}")
            private_key = loaded
        except (OSError, ValueError) as e:
            raise KeyLoadError(f"Failed to load private key from {private_key_path}: {e}") from e
    # If private key file doesn't exist, we operate in verify-only mode

    return KralIdentity(
        private_key=private_key,
        public_key=public_key,
        fingerprint=fingerprint,
        public_key_hex=pub_hex,
    )


# ── Ed25519 sign / verify ─────────────────────────────────────────────────────

def ed25519_sign(identity: KralIdentity, data: bytes) -> bytes:
    """
    Sign data with KRAL private key. Returns 64-byte Ed25519 signature.
    Raises CryptoError if private key not loaded.
    """
    if not identity.has_private_key():
        raise CryptoError("Cannot sign: private key not loaded (verify-only mode)")
    assert identity.private_key is not None
    return identity.private_key.sign(data)


def ed25519_verify(identity: KralIdentity, data: bytes, signature: bytes) -> None:
    """
    Verify Ed25519 signature against KRAL public key.
    Raises SignatureVerificationFailed on invalid signature.
    """
    try:
        identity.public_key.verify(signature, data)
    except InvalidSignature as e:
        raise SignatureVerificationFailed(
            f"Ed25519 signature verification failed for KRAL identity"
        ) from e


# ── HMAC-SHA256 (v1 event signatures) ────────────────────────────────────────

def hmac_sign(secret: str, data: bytes) -> str:
    """
    HMAC-SHA256 signature for internal event integrity (v1).
    Returns hex digest.
    Both agents share the secret via EVENT_HMAC_SECRET in config.
    """
    return hmac.new(
        secret.encode("utf-8"),
        data,
        hashlib.sha256,
    ).hexdigest()


def hmac_verify(secret: str, data: bytes, expected_hex: str) -> None:
    """
    Verify HMAC-SHA256 signature.
    Raises EventSignatureInvalid on mismatch.
    Uses constant-time comparison (hmac.compare_digest).
    """
    from core.exceptions import EventSignatureInvalid
    computed = hmac_sign(secret, data)
    if not hmac.compare_digest(computed, expected_hex):
        raise EventSignatureInvalid("HMAC-SHA256 event signature verification failed")


# ── Law registry seal (Ed25519) ───────────────────────────────────────────────

def seal_law_registry(identity: KralIdentity, registry_hash: str) -> bytes:
    """
    Sign the law registry hash with KRAL private key.
    Called once at boot when the law registry is sealed.
    Returns 64-byte Ed25519 signature over the hash bytes.

    This creates an immutable proof that laws were loaded in a specific state.
    Any tamper of law files after this seal will fail verification.
    """
    return ed25519_sign(identity, registry_hash.encode("utf-8"))


def verify_law_seal(identity: KralIdentity, registry_hash: str, seal: bytes) -> None:
    """
    Verify law registry seal signature.
    Called at every boot to confirm law integrity.
    Raises SignatureVerificationFailed if laws were modified after sealing.
    """
    ed25519_verify(identity, registry_hash.encode("utf-8"), seal)


# ── Brotherhood bond signing (Ed25519) ────────────────────────────────────────

def sign_brotherhood_oath(identity: KralIdentity, oath_text: str) -> bytes:
    """
    Sign the brotherhood oath text (from BOUND.md Article VI) with KRAL key.
    Stored as immutable log entry #0 in the event store.
    """
    return ed25519_sign(identity, oath_text.encode("utf-8"))


def verify_brotherhood_oath(identity: KralIdentity, oath_text: str, signature: bytes) -> None:
    """Verify the stored brotherhood oath signature."""
    ed25519_verify(identity, oath_text.encode("utf-8"), signature)
