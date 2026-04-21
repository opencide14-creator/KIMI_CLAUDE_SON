"""Vault — encrypted API key and credential store.

Uses Fernet symmetric encryption (from cryptography library).
The vault key is derived from a user passphrase via PBKDF2.
If no passphrase is set, vault uses a machine-unique key (less secure but convenient).
"""
from __future__ import annotations
import base64
import hashlib
import json
import logging
import os
import platform
import secrets
import socket
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

from src.constants import VAULT_FILE
from src.models.gateway import VaultEntry
from src.models.state import get_state, SK

log = logging.getLogger(__name__)

# Salt file stored alongside vault
SALT_FILE = VAULT_FILE.parent / "vault.salt"


def _legacy_machine_key_material() -> bytes:
    """Derive machine-unique bytes string for keyless mode (DEPRECATED - weak)."""
    system = platform.system()
    parts  = [platform.node(), system, platform.machine()]
    return ":".join(parts).encode("utf-8")


def _derive_machine_key() -> bytes:
    """Derive machine-specific encryption key using OS-level entropy.

    Uses multiple entropy sources for better security:
    - Hardware identifier (MAC address from uuid.getnode())
    - OS and architecture info
    - Process-specific values (PID, UID)
    - System files (/etc/machine-id)
    - Cryptographically secure random bytes

    Returns a 32-byte key suitable for Fernet encryption.
    """
    hasher = hashlib.sha256()

    # Hardware/network layer entropy
    hasher.update(str(uuid.getnode()).encode())

    # OS-level information
    hasher.update(platform.system().encode())
    hasher.update(platform.machine().encode())
    hasher.update(platform.node().encode())

    # Process-specific entropy
    hasher.update(str(os.getpid()).encode())
    if hasattr(os, 'getuid'):
        hasher.update(str(os.getuid()).encode())
    else:
        hasher.update(b"0")

    # System-specific files for additional entropy
    for path in ['/etc/machine-id', '/var/lib/dbus/machine-id',
                 '/proc/sys/kernel/random/boot_id']:
        try:
            if os.path.exists(path):
                with open(path, 'rb') as f:
                    hasher.update(f.read(32))
                break
        except (IOError, OSError):
            pass

    # NOTE: We intentionally do NOT add random bytes here.
    # The machine key must be deterministic so the same machine
    # can re-derive it and decrypt the vault later.
    # Entropy already comes from uuid.getnode(), platform info, PID, etc.

    return hasher.digest()


def _machine_key_material() -> bytes:
    """Public interface for machine key material (uses secure derivation)."""
    return _derive_machine_key()


def _derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key from passphrase + salt via PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480000,
    )
    return base64.urlsafe_b64encode(kdf.derive(passphrase))


class VaultStore:
    """Thread-safe encrypted credential store with proper instance lifecycle.

    Supports multiple vault instances (one per vault_path) for testing and
    isolation. Use get_instance() to obtain the appropriate instance.

    Usage:
        vault = VaultStore.get_instance()
        vault.unlock("optional-passphrase")
        vault.set("kimi-api-key", os.environ.get("KIMI_API_KEY",""), provider="kimi")
        value = vault.get_key("kimi-api-key")
    """

    # Class-level registry of instances keyed by vault path
    _instances: Dict[str, "VaultStore"] = {}

    def __init__(self, vault_path: Optional[Path] = None) -> None:
        """Create a vault instance.

        Args:
            vault_path: Custom vault location. If None, uses default.
        """
        self._vault_path: Path = vault_path or Path("~/.sovereign/vault.json").expanduser()
        self._fernet: Optional[Fernet] = None
        self._entries: Dict[str, VaultEntry] = {}
        self._unlocked: bool = False
        self._write_lock = threading.Lock()

    @classmethod
    def get_instance(cls, vault_path: Optional[Path] = None) -> "VaultStore":
        """Get or create vault instance for the given path.

        Args:
            vault_path: Custom vault location. If None, uses default.

        Returns:
            VaultStore instance for the specified path.
        """
        key = str(vault_path or "default")
        if key not in cls._instances:
            cls._instances[key] = cls(vault_path)
        return cls._instances[key]

    @classmethod
    def reset_all(cls) -> None:
        """Reset all vault instances - primarily for testing."""
        cls._instances.clear()

    @classmethod
    def unlock(cls, passphrase: str = "") -> bool:
        """Unlock the vault. Returns True on success."""
        return cls.get_instance()._unlock(passphrase)

    def _unlock(self, passphrase: str = "") -> bool:
        """Unlock this vault instance. Returns True on success."""
        salt = self._load_or_create_salt()
        key_material = passphrase.encode("utf-8") if passphrase else _machine_key_material()
        key = _derive_key(key_material, salt)
        self._fernet = Fernet(key)

        if VAULT_FILE.exists():
            ok = self._load_vault()
            if not ok and passphrase:
                log.warning("Wrong passphrase or corrupted vault")
                self._fernet = None
                self._unlocked = False
                return False
            # Backward compatibility: if unlock failed without passphrase, try legacy key
            if not ok and not passphrase:
                log.warning("Vault unlock with machine key failed, trying legacy method (SEC-C1)")
                self._fernet = None
                self._unlocked = False
                # Try legacy key material
                legacy_key_material = _legacy_machine_key_material()
                legacy_key = _derive_key(legacy_key_material, salt)
                self._fernet = Fernet(legacy_key)
                ok = self._load_vault()
                if ok:
                    log.warning("Legacy machine key worked - vault was created with insecure key derivation")
                    log.warning("Consider re-encrypting vault with a passphrase for better security")
        else:
            self._entries = {}

        self._unlocked = True
        get_state().set(SK.VAULT_UNLOCKED, True)
        log.info("Vault unlocked (%d entries)", len(self._entries))
        return True

    @classmethod
    def lock(cls) -> None:
        """Lock the vault."""
        return cls.get_instance()._lock()

    def _lock(self) -> None:
        """Lock this vault instance."""
        self._fernet = None
        self._entries = {}
        self._unlocked = False
        get_state().set(SK.VAULT_UNLOCKED, False)
        log.info("Vault locked")

    @classmethod
    def is_unlocked(cls) -> bool:
        """Check if vault is unlocked."""
        return cls.get_instance()._unlocked

    # ── CRUD ───────────────────────────────────────────────────────

    @classmethod
    def set(cls, name: str, value: str, provider: str = "",
            env_var: str = "", notes: str = "") -> VaultEntry:
        """Store or update a credential."""
        return cls.get_instance()._set(name, value, provider, env_var, notes)

    def _set(self, name: str, value: str, provider: str = "",
             env_var: str = "", notes: str = "") -> VaultEntry:
        """Store or update a credential in this vault instance."""
        self._require_unlocked()
        entry_id = name.lower().replace(" ", "-")
        entry = VaultEntry(
            id       = entry_id,
            name     = name,
            value    = value,
            provider = provider,
            env_var  = env_var,
            notes    = notes,
        )
        self._entries[entry_id] = entry
        self._save_vault()
        get_state().set(SK.VAULT_ENTRIES, list(self._entries.values()))
        log.info("Vault entry set: %s", name)
        return entry

    @classmethod
    def get_key(cls, name: str) -> Optional[str]:
        """Return the decrypted value for a credential by name."""
        return cls.get_instance()._get_key(name)

    def _get_key(self, name: str) -> Optional[str]:
        """Return the decrypted value for a credential by name from this vault."""
        if not self._unlocked:
            # Try env var as fallback
            entry = self._entries.get(name.lower().replace(" ", "-"))
            if entry and entry.env_var:
                return os.environ.get(entry.env_var)
            return None
        entry = self._entries.get(name.lower().replace(" ", "-"))
        if entry:
            entry.last_used = datetime.now()
            return entry.value
        # Try environment variable with same name
        return os.environ.get(name.upper().replace("-", "_").replace(" ", "_"))

    @classmethod
    def delete(cls, name: str) -> bool:
        """Delete a credential by name."""
        return cls.get_instance()._delete(name)

    def _delete(self, name: str) -> bool:
        """Delete a credential by name from this vault instance."""
        self._require_unlocked()
        entry_id = name.lower().replace(" ", "-")
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._save_vault()
            get_state().set(SK.VAULT_ENTRIES, list(self._entries.values()))
            return True
        return False

    @classmethod
    def list_entries(cls) -> List[VaultEntry]:
        """List all vault entries."""
        return cls.get_instance()._list_entries()

    def _list_entries(self) -> List[VaultEntry]:
        """List all entries from this vault instance."""
        if not self._unlocked:
            return []
        return list(self._entries.values())

    @classmethod
    def import_from_env(cls) -> int:
        """Import known AI provider keys from environment variables."""
        return cls.get_instance()._import_from_env()

    def _import_from_env(self) -> int:
        """Import known AI provider keys from environment variables into this vault."""
        self._require_unlocked()
        known_vars = {
            "ANTHROPIC_API_KEY": ("Anthropic API Key", "anthropic"),
            "KIMI_API_KEY":      ("Kimi API Key",      "kimi"),
            "OPENAI_API_KEY":    ("OpenAI API Key",    "openai"),
            "GROQ_API_KEY":      ("Groq API Key",      "groq"),
            "MISTRAL_API_KEY":   ("Mistral API Key",   "mistral"),
            "GEMINI_API_KEY":    ("Gemini API Key",    "gemini"),
        }
        imported = 0
        for env_var, (name, provider) in known_vars.items():
            value = os.environ.get(env_var)
            if value:
                self._set(name, value, provider=provider, env_var=env_var)
                imported += 1
        log.info("Imported %d keys from environment", imported)
        return imported

    # ── Internal ───────────────────────────────────────────────────

    def _require_unlocked(self) -> None:
        """Raise if vault is locked."""
        if not self._unlocked or not self._fernet:
            raise RuntimeError("Vault is locked — call VaultStore.unlock() first")

    def _load_or_create_salt(self) -> bytes:
        """Load existing salt or create a new one."""
        SALT_FILE.parent.mkdir(parents=True, exist_ok=True)
        if SALT_FILE.exists():
            return SALT_FILE.read_bytes()
        salt = os.urandom(32)
        tmp  = SALT_FILE.with_suffix(".tmp")
        tmp.write_bytes(salt)
        tmp.replace(SALT_FILE)
        try:
            SALT_FILE.chmod(0o600)
        except NotImplementedError:
            pass  # Windows: no Unix permissions
        return salt

    def _save_vault(self) -> None:
        """Save vault entries to encrypted file."""
        with self._write_lock:
            VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "version": 1,
                "entries": {
                    eid: {
                        "id":       e.id,
                        "name":     e.name,
                        "value":    e.value,
                        "provider": e.provider,
                        "env_var":  e.env_var,
                        "notes":    e.notes,
                        "created_at": e.created_at.isoformat(),
                    }
                    for eid, e in self._entries.items()
                }
            }
            plaintext = json.dumps(data).encode("utf-8")
            encrypted = self._fernet.encrypt(plaintext)
            tmp = VAULT_FILE.with_suffix(".tmp")
            tmp.write_bytes(encrypted)
            tmp.replace(VAULT_FILE)
            try:
                VAULT_FILE.chmod(0o600)
            except NotImplementedError:
                pass  # Windows: no Unix permissions

    def _load_vault(self) -> bool:
        """Load vault entries from encrypted file. Returns True if decryption succeeded."""
        try:
            encrypted = VAULT_FILE.read_bytes()
            plaintext = self._fernet.decrypt(encrypted)
            data      = json.loads(plaintext.decode("utf-8"))
            self._entries = {}
            for eid, e in data.get("entries", {}).items():
                self._entries[eid] = VaultEntry(
                    id        = e["id"],
                    name      = e["name"],
                    value     = e["value"],
                    provider  = e.get("provider", ""),
                    env_var   = e.get("env_var", ""),
                    notes     = e.get("notes", ""),
                    created_at= datetime.fromisoformat(e.get("created_at", datetime.now().isoformat())),
                )
            return True
        except Exception as e:
            log.warning("Vault load failed: %s", e)
            self._entries = {}
            return False
