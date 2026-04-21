"""Certificate Authority — generate CA, server certs, manage system trust.

Uses the `cryptography` library directly — no mkcert subprocess required.
All operations are real: actual X.509 cert generation, PEM/DER export,
system trust store integration, hosts file management.

SECURITY NOTE:
    CA private keys are sensitive credentials that enable MITM interception.
    This module supports:
    - File permission restrictions (chmod 600) on all key files
    - Password-based encryption for CA keys at rest (recommended for production)
    - In-memory only key handling when encryption is enabled

    For production deployments, enable encryption with a strong password.
"""
from __future__ import annotations
import ipaddress
import logging
import os
import platform
import stat
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

from src.constants import CERT_DIR, APP_NAME
from src.models.gateway import CertRecord
from src.utils.sanitization import Sanitizer

log = logging.getLogger(__name__)

# ── Key sizes ──────────────────────────────────────────────────────
CA_KEY_SIZE     = 4096
SERVER_KEY_SIZE = 2048
CA_VALIDITY_DAYS    = 3650   # 10 years
SERVER_VALIDITY_DAYS = 825   # ~2.25 years (Apple limit)

# Encryption constants
ENCRYPTED_KEY_SUFFIX = ".encrypted"
SALT_SIZE = 16
KEY_SIZE_BYTES = 32  # 256-bit key for AES-256


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


class CertificateAuthority:
    """Manages a local CA and signs server certificates for MITM interception."""

    def __init__(self, cert_dir: Path = CERT_DIR):
        self._dir     = _ensure_dir(cert_dir)
        self._ca_key: Optional[rsa.RSAPrivateKey] = None
        self._ca_cert: Optional[x509.Certificate] = None
        self._ca_key_path  = self._dir / "sovereign-ca.key"
        self._ca_cert_path = self._dir / "sovereign-ca.crt"
        self._encrypted_key_path = self._dir / "sovereign-ca.key.encrypted"

    # ── Security helpers ─────────────────────────────────────────────

    def _secure_key_file(self, path: Path) -> bool:
        """Set restrictive permissions on key file (chmod 600).

        Args:
            path: Path to the key file

        Returns:
            True if permissions were set successfully, False otherwise
        """
        try:
            # Get current permissions
            current_mode = path.stat().st_mode
            # Set owner read/write only (0o600)
            new_mode = current_mode & stat.S_IRWXG & stat.S_IRWXO | stat.S_IRUSR | stat.S_IWUSR
            path.chmod(new_mode)
            log.debug("Set restrictive permissions on %s", path)
            return True
        except (OSError, NotImplementedError) as e:
            log.warning("Could not set restrictive permissions on %s: %s", path, e)
            return False

    def _derive_encryption_key(self, password: str, salt: bytes) -> bytes:
        """Derive encryption key from password using PBKDF2.

        Args:
            password: User-provided password
            salt: Random salt for key derivation

        Returns:
            256-bit encryption key
        """
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_SIZE_BYTES,
            salt=salt,
            iterations=480000,  # OWASP recommended minimum for PBKDF2-SHA256
        )
        return kdf.derive(password.encode())

    def _encrypt_private_key(self, key_pem: bytes, password: str) -> bytes:
        """Encrypt private key PEM with password.

        Args:
            key_pem: PEM-encoded private key
            password: Encryption password

        Returns:
            Encrypted key data with salt prepended
        """
        salt = os.urandom(SALT_SIZE)
        key = self._derive_encryption_key(password, salt)

        # Generate random IV for AES-CBC
        iv = os.urandom(16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        encryptor = cipher.encryptor()

        # PKCS7 padding
        padding_length = 16 - (len(key_pem) % 16)
        padded_data = key_pem + bytes([padding_length] * padding_length)
        encrypted = encryptor.update(padded_data) + encryptor.finalize()

        # Format: salt (16 bytes) + iv (16 bytes) + encrypted data
        return salt + iv + encrypted

    def _decrypt_private_key(self, encrypted_data: bytes, password: str) -> bytes:
        """Decrypt private key PEM with password.

        Args:
            encrypted_data: Encrypted key data with salt prepended
            password: Decryption password

        Returns:
            Decrypted PEM-encoded private key

        Raises:
            ValueError: If decryption fails (wrong password or corrupted data)
        """
        if len(encrypted_data) < SALT_SIZE + 16 + 16:
            raise ValueError("Invalid encrypted key format")

        salt = encrypted_data[:SALT_SIZE]
        iv = encrypted_data[SALT_SIZE:SALT_SIZE + 16]
        encrypted = encrypted_data[SALT_SIZE + 16:]

        key = self._derive_encryption_key(password, salt)

        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted) + decryptor.finalize()

        # Remove PKCS7 padding
        padding_length = padded[-1]
        if padding_length > 16 or padding_length == 0:
            raise ValueError("Invalid padding")
        key_pem = padded[:-padding_length]

        return key_pem

    def _write_pem(self, path: Path, data: bytes, secure: bool = True):
        """Atomic PEM write with optional security hardening.

        Args:
            path: Destination path
            data: PEM data to write
            secure: If True, set restrictive permissions (chmod 600)
        """
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        if secure:
            self._secure_key_file(path)

    def _write_encrypted_pem(self, path: Path, encrypted_data: bytes):
        """Write encrypted PEM data with restrictive permissions.

        Args:
            path: Destination path
            encrypted_data: Encrypted data to write
        """
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(encrypted_data)
        tmp.replace(path)
        self._secure_key_file(path)

    # ── CA lifecycle ───────────────────────────────────────────────

    def ca_exists(self) -> bool:
        """Check if CA files exist (either encrypted or unencrypted)."""
        return (
            self._ca_cert_path.exists()
            and (
                self._ca_key_path.exists()
                or self._encrypted_key_path.exists()
            )
        )

    def is_ca_encrypted(self) -> bool:
        """Check if CA key is encrypted at rest."""
        return self._encrypted_key_path.exists()

    def generate_ca(
        self,
        common_name: str = f"{APP_NAME} Root CA",
        encrypt: bool = False,
        password: Optional[str] = None,
    ) -> CertRecord:
        """Generate a new Root CA key pair and self-signed certificate.

        Args:
            common_name: CN for the CA certificate
            encrypt: If True, encrypt the CA private key at rest (recommended)
            password: Password for encryption. Required if encrypt=True.
                      If encrypt=True but password is None, will prompt or error.

        Returns:
            CertRecord with CA metadata

        Security Warning:
            Unencrypted CA keys on disk are a security risk. Anyone with
            file access can extract the private key. For production use,
            always enable encryption with a strong password.
        """
        if encrypt and not password:
            raise ValueError(
                "Password required when encrypt=True. "
                "Provide a strong password to protect the CA private key."
            )

        if encrypt:
            log.warning(
                "SECURITY: CA key will be encrypted at rest. "
                "Store the password securely - it cannot be recovered!"
            )
        else:
            log.warning(
                "SECURITY WARNING: CA private key will be stored UNENCRYPTED on disk. "
                "Anyone with file access can extract the key. "
                "For production use, set encrypt=True with a strong password."
            )

        log.info("Generating Root CA: %s", common_name)
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=CA_KEY_SIZE,
        )
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, APP_NAME),
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        ])
        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=CA_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_cert_sign=True,
                crl_sign=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False,
                key_agreement=False, encipher_only=False,
                decipher_only=False,
            ), critical=True)
            .sign(key, hashes.SHA256())
        )

        key_pem = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()
        )

        if encrypt and password:
            # Write encrypted key
            encrypted_key = self._encrypt_private_key(key_pem, password)
            self._write_encrypted_pem(self._encrypted_key_path, encrypted_key)
            # Remove unencrypted key file if it exists
            if self._ca_key_path.exists():
                self._ca_key_path.unlink()
            log.info("Encrypted CA key stored at: %s", self._encrypted_key_path)
        else:
            # Write unencrypted key with restrictive permissions
            self._write_pem(self._ca_key_path, key_pem)
            # Remove encrypted key file if it exists (migration scenario)
            if self._encrypted_key_path.exists():
                self._encrypted_key_path.unlink()
            log.info("Unencrypted CA key stored at: %s (USE ONLY IN DEVELOPMENT)", self._ca_key_path)

        self._write_pem(self._ca_cert_path, cert.public_bytes(serialization.Encoding.PEM))
        self._ca_key  = key
        self._ca_cert = cert

        record = CertRecord(
            name        = common_name,
            domains     = [],
            cert_path   = str(self._ca_cert_path),
            key_path    = str(self._encrypted_key_path if encrypt else self._ca_key_path),
            is_ca       = True,
            created_at  = datetime.now(),
            expires_at  = datetime.now() + timedelta(days=CA_VALIDITY_DAYS),
            fingerprint = self._fingerprint(cert),
        )
        log.info("Root CA generated: %s", self._ca_cert_path)
        return record

    def load_ca(self, password: Optional[str] = None) -> bool:
        """Load existing CA from disk. Returns True if successful.

        Args:
            password: Password to decrypt CA key if encrypted. Required if
                     the CA key is encrypted at rest.

        Returns:
            True if CA was loaded successfully, False otherwise
        """
        if not self.ca_exists():
            return False

        try:
            if self._encrypted_key_path.exists():
                # CA key is encrypted
                if not password:
                    log.error("CA key is encrypted but no password provided")
                    return False
                try:
                    encrypted_data = self._encrypted_key_path.read_bytes()
                    key_pem = self._decrypt_private_key(encrypted_data, password)
                    self._ca_key = serialization.load_pem_private_key(
                        key_pem, password=None
                    )
                except ValueError as e:
                    log.error("Failed to decrypt CA key: %s", e)
                    return False
            elif self._ca_key_path.exists():
                # Unencrypted CA key (legacy or development)
                log.warning(
                    "SECURITY: Loading unencrypted CA key from %s. "
                    "Consider migrating to encrypted storage.",
                    self._ca_key_path
                )
                self._ca_key = serialization.load_pem_private_key(
                    self._ca_key_path.read_bytes(), password=None
                )
            else:
                log.error("No CA key file found")
                return False

            self._ca_cert = x509.load_pem_x509_certificate(
                self._ca_cert_path.read_bytes()
            )
            log.info("CA loaded from %s", self._ca_cert_path)
            return True
        except Exception as e:
            log.error("Failed to load CA: %s", e)
            return False

    def migrate_to_encrypted(self, password: str) -> bool:
        """Migrate existing unencrypted CA key to encrypted storage.

        Args:
            password: Password for encryption

        Returns:
            True if migration was successful
        """
        if self._encrypted_key_path.exists():
            log.info("CA is already encrypted")
            return True

        if not self._ca_key_path.exists():
            log.error("No unencrypted CA key found to migrate")
            return False

        if not self._ca_key:
            # Try to load without password first
            try:
                self._ca_key = serialization.load_pem_private_key(
                    self._ca_key_path.read_bytes(), password=None
                )
            except Exception as e:
                log.error("Failed to load existing CA key: %s", e)
                return False

        if not self._ca_key:
            log.error("CA key not available")
            return False

        try:
            key_pem = self._ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()
            )
            encrypted_key = self._encrypt_private_key(key_pem, password)
            self._write_encrypted_pem(self._encrypted_key_path, encrypted_key)

            # Backup old file (don't delete in case of issues)
            backup_path = self._ca_key_path.with_suffix(".key.backup")
            self._ca_key_path.rename(backup_path)

            log.info(
                "CA key encrypted and migrated. "
                "Backup at: %s (delete after verifying encryption works)",
                backup_path
            )
            return True
        except Exception as e:
            log.error("Migration failed: %s", e)
            return False

    def change_password(self, old_password: str, new_password: str) -> bool:
        """Change the encryption password for the CA key.

        Args:
            old_password: Current password
            new_password: New password

        Returns:
            True if password was changed successfully
        """
        if not self._encrypted_key_path.exists():
            log.error("CA key is not encrypted")
            return False

        try:
            encrypted_data = self._encrypted_key_path.read_bytes()
            key_pem = self._decrypt_private_key(encrypted_data, old_password)

            # Re-encrypt with new password
            new_encrypted = self._encrypt_private_key(key_pem, new_password)
            self._write_encrypted_pem(self._encrypted_key_path, new_encrypted)

            log.info("CA key password changed successfully")
            return True
        except ValueError as e:
            log.error("Failed to change password: %s", e)
            return False

    # ── Server cert generation ─────────────────────────────────────

    def generate_server_cert(self, domains: List[str]) -> CertRecord:
        """Sign a server certificate for the given domain(s).

        The first domain is the CN; all are added as SAN.
        Requires CA to be loaded or generated first.
        """
        if not self._ca_key or not self._ca_cert:
            raise RuntimeError("CA not loaded — call load_ca() or generate_ca() first")
        if not domains:
            raise ValueError("At least one domain required")

        # Validate all domains using centralized sanitization
        sanitized_domains = []
        for d in domains:
            try:
                sanitized_domains.append(Sanitizer.sanitize_hostname(d))
            except ValueError:
                # Try as IP address - if it's a valid IP, allow it
                try:
                    Sanitizer.validate_ip(d, allow_private=True)
                    sanitized_domains.append(d)
                except ValueError:
                    raise ValueError(f"Invalid domain: {d}")

        primary = sanitized_domains[0]
        log.info("Generating server cert for: %s", sanitized_domains)

        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=SERVER_KEY_SIZE,
        )
        subject = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, primary),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, APP_NAME),
        ])
        # Build SANs — both DNS and IP (if IP address given)
        san_list = []
        for d in domains:
            try:
                san_list.append(x509.IPAddress(ipaddress.ip_address(d)))
            except ValueError:
                san_list.append(x509.DNSName(d))
                # Add wildcard for main domain
                if "." in d and not d.startswith("*."):
                    san_list.append(x509.DNSName(f"*.{d}"))

        now = datetime.now(timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(self._ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(now + timedelta(days=SERVER_VALIDITY_DAYS))
            .add_extension(x509.SubjectAlternativeName(san_list), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                self._ca_cert.public_key()), critical=False)
            .add_extension(x509.KeyUsage(
                digital_signature=True, key_encipherment=True,
                content_commitment=False, data_encipherment=False,
                key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ), critical=True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.SERVER_AUTH,
                ExtendedKeyUsageOID.CLIENT_AUTH,
            ]), critical=False)
            .sign(self._ca_key, hashes.SHA256())
        )
        # Safe filenames
        safe_name  = primary.replace("*", "wildcard").replace(".", "_")
        cert_path  = self._dir / f"{safe_name}.crt"
        key_path   = self._dir / f"{safe_name}.key"

        self._write_pem(key_path,
                        key.private_bytes(serialization.Encoding.PEM,
                                          serialization.PrivateFormat.TraditionalOpenSSL,
                                          serialization.NoEncryption()))
        self._write_pem(cert_path, cert.public_bytes(serialization.Encoding.PEM))

        record = CertRecord(
            name       = primary,
            domains    = domains,
            cert_path  = str(cert_path),
            key_path   = str(key_path),
            ca_path    = str(self._ca_cert_path),
            is_ca      = False,
            created_at = datetime.now(),
            expires_at = datetime.now() + timedelta(days=SERVER_VALIDITY_DAYS),
            fingerprint= self._fingerprint(cert),
        )
        log.info("Server cert generated: %s", cert_path)
        return record

    # ── System trust store ─────────────────────────────────────────

    def install_ca_system(self) -> Tuple[bool, str]:
        """Install the CA cert into the OS trust store. Returns (ok, message)."""
        if not self._ca_cert_path.exists():
            return False, "CA cert not found — generate it first"
        system = platform.system()
        try:
            if system == "Darwin":
                return self._install_ca_macos()
            elif system == "Linux":
                return self._install_ca_linux()
            elif system == "Windows":
                return self._install_ca_windows()
            else:
                return False, f"Unsupported OS: {system}"
        except PermissionError:
            return False, "Permission denied — run as administrator/sudo"
        except Exception as e:
            return False, str(e)

    def _install_ca_macos(self) -> Tuple[bool, str]:
        r = subprocess.run(
            ["security", "add-trusted-cert", "-d", "-r", "trustRoot",
             "-k", "/Library/Keychains/System.keychain", str(self._ca_cert_path)],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            return True, "CA installed in macOS System Keychain"
        return False, r.stderr.strip() or f"Exit {r.returncode}"

    def _install_ca_linux(self) -> Tuple[bool, str]:
        # Try ca-certificates approach (works on Debian/Ubuntu/RHEL)
        if Path("/usr/local/share/ca-certificates").exists():
            dest = Path("/usr/local/share/ca-certificates/sovereign-ca.crt")
            import shutil
            shutil.copy2(self._ca_cert_path, dest)
            r = subprocess.run(["update-ca-certificates"], capture_output=True, text=True)
            if r.returncode == 0:
                return True, "CA installed via update-ca-certificates"
            return False, r.stderr.strip()
        # Fallback: try update-ca-trust (Fedora/RHEL)
        if Path("/etc/pki/ca-trust/source/anchors").exists():
            import shutil
            dest = Path("/etc/pki/ca-trust/source/anchors/sovereign-ca.crt")
            shutil.copy2(self._ca_cert_path, dest)
            r = subprocess.run(["update-ca-trust", "extract"], capture_output=True, text=True)
            if r.returncode == 0:
                return True, "CA installed via update-ca-trust"
            return False, r.stderr.strip()
        return False, "Cannot determine certificate store location"

    def _install_ca_windows(self) -> Tuple[bool, str]:
        r = subprocess.run(
            ["certutil", "-addstore", "Root", str(self._ca_cert_path)],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            return True, "CA installed in Windows Root Certificate Store"
        return False, r.stderr.strip() or f"Exit {r.returncode}"

    def remove_ca_system(self) -> Tuple[bool, str]:
        """Remove CA from OS trust store."""
        system = platform.system()
        try:
            if system == "Darwin":
                r = subprocess.run(
                    ["security", "delete-certificate", "-c", f"{APP_NAME} Root CA",
                     "/Library/Keychains/System.keychain"],
                    capture_output=True, text=True
                )
                return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
            elif system == "Windows":
                r = subprocess.run(
                    ["certutil", "-delstore", "Root", f"{APP_NAME} Root CA"],
                    capture_output=True, text=True
                )
                return r.returncode == 0, r.stdout.strip() or r.stderr.strip()
            else:
                return False, f"Manual removal required on {system}"
        except Exception as e:
            return False, str(e)

    # ── CA cert for mitmproxy ──────────────────────────────────────

    def get_ca_cert_pem(self) -> Optional[bytes]:
        """Return CA cert PEM bytes for use with mitmproxy's certstore."""
        if self._ca_cert_path.exists():
            return self._ca_cert_path.read_bytes()
        return None

    def get_ca_key_pem(self) -> Optional[bytes]:
        """Return CA key PEM bytes.

        WARNING: Returns unencrypted key in memory. Handle with care.

        Returns None if CA is encrypted and key not loaded.
        """
        if self._ca_key:
            return self._ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()
            )
        return None

    def get_mitmproxy_ca_pem(self) -> Optional[bytes]:
        """Combine key + cert into mitmproxy's expected mitmproxy-ca.pem format.

        WARNING: This returns the unencrypted key in memory. Only use when
        MITM operations are active and keys are in memory.
        """
        if self._ca_key and self._ca_cert:
            key_pem = self._ca_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption()
            )
            return key_pem + self._ca_cert.public_bytes(serialization.Encoding.PEM)
        return None

    # ── Certificate inspection ─────────────────────────────────────

    def list_certs(self) -> List[CertRecord]:
        """Return all .crt files in the cert directory as CertRecord objects."""
        records = []
        for crt_path in sorted(self._dir.glob("*.crt")):
            try:
                cert = x509.load_pem_x509_certificate(crt_path.read_bytes())
                domains = []
                try:
                    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
                    domains = [str(n.value) for n in san.value]
                except x509.ExtensionNotFound:
                    domains = []  # Optional extension — cert has no SAN, use CN instead
                cn_attrs = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                cn = cn_attrs[0].value if cn_attrs else crt_path.stem
                is_ca = False
                try:
                    bc = cert.extensions.get_extension_for_class(x509.BasicConstraints)
                    is_ca = bc.value.ca
                except x509.ExtensionNotFound:
                    is_ca = False  # Optional extension — treat as non-CA cert
                key_path = crt_path.with_suffix(".key")
                # Check for encrypted key for CA certs
                if is_ca and self._encrypted_key_path.exists():
                    key_path = self._encrypted_key_path
                records.append(CertRecord(
                    name        = cn,
                    domains     = domains or [cn],
                    cert_path   = str(crt_path),
                    key_path    = str(key_path) if key_path.exists() else "",
                    is_ca       = is_ca,
                    created_at  = datetime.fromtimestamp(crt_path.stat().st_mtime),
                    expires_at  = cert.not_valid_after_utc.replace(tzinfo=None),
                    fingerprint = self._fingerprint(cert),
                ))
            except Exception as e:
                log.debug("Cannot parse cert %s: %s", crt_path, e)
        return records

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(cert: x509.Certificate) -> str:
        fp = cert.fingerprint(hashes.SHA256())
        return ":".join(f"{b:02X}" for b in fp[:8]) + "…"

    @staticmethod
    def _write_pem(path: Path, data: bytes):
        """Atomic PEM write."""
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        try:
            path.chmod(0o600)  # no-op on Windows, valid on macOS/Linux
        except NotImplementedError:
            pass  # Windows does not support Unix-style chmod
