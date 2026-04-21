"""Hosts file manager — read, modify, backup and restore /etc/hosts.

Cross-platform: macOS/Linux (/etc/hosts) and Windows (drivers\\etc\\hosts).
All writes are atomic and backed up before modification.
"""
from __future__ import annotations
import ipaddress
import logging
import platform
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from src.core.security import SECURITY, validate_hostname_strict
from src.models.gateway import HostsEntry
from src.utils.sanitization import Sanitizer

log = logging.getLogger(__name__)

SOVEREIGN_MARKER_START = "# ─── SOVEREIGN MANAGED ENTRIES ───"
SOVEREIGN_MARKER_END   = "# ─── END SOVEREIGN ───"


def _hosts_path() -> Path:
    if platform.system() == "Windows":
        return Path(r"C:\Windows\System32\drivers\etc\hosts")
    return Path("/etc/hosts")


class HostsManager:
    """Read and write the OS hosts file safely."""

    def __init__(self, allow_private: bool = False):
        self._path         = _hosts_path()
        self._backup       = self._path.parent / "hosts.sovereign.bak"
        self._allow_private = allow_private

    def read_all(self) -> List[HostsEntry]:
        """Parse the current hosts file into HostsEntry objects."""
        try:
            text = self._path.read_text(errors="replace")
        except PermissionError:
            log.warning("Cannot read hosts file — permission denied")
            return []
        except FileNotFoundError:
            log.warning("Hosts file not found: %s", self._path)
            return []

        entries = []
        in_sovereign_block = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped == SOVEREIGN_MARKER_START:
                in_sovereign_block = True
                continue
            if stripped == SOVEREIGN_MARKER_END:
                in_sovereign_block = False
                continue
            if not stripped or stripped.startswith("#"):
                continue
            parts = stripped.split()
            if len(parts) < 2:
                continue
            ip   = parts[0]
            host = parts[1]
            entries.append(HostsEntry(
                ip      = ip,
                host    = host,
                active  = True,
                managed = in_sovereign_block,
            ))
        return entries

    def read_sovereign_entries(self) -> List[HostsEntry]:
        """Return only the entries managed by SOVEREIGN."""
        return [e for e in self.read_all() if e.managed]

    def add_entry(self, ip: str, host: str) -> Tuple[bool, str]:
        """Add a SOVEREIGN-managed hosts entry."""
        # Validate input before any modification
        valid, msg = self._validate_entry(ip, host)
        if not valid:
            return False, f"Invalid hosts entry: {msg}"

        existing = self.read_all()
        for e in existing:
            if e.host == host and e.ip == ip:
                return True, f"{ip} {host} already present"

        self._backup_hosts()
        try:
            text = self._path.read_text(errors="replace")
        except PermissionError:
            return False, "Permission denied — run as administrator/sudo"
        except FileNotFoundError:
            text = ""

        new_entry_line = f"{ip} {host}"
        if SOVEREIGN_MARKER_START in text and SOVEREIGN_MARKER_END in text:
            # Insert before end marker
            text = text.replace(
                SOVEREIGN_MARKER_END,
                f"{new_entry_line}\n{SOVEREIGN_MARKER_END}"
            )
        else:
            # Append sovereign block
            text = text.rstrip("\n") + (
                f"\n\n{SOVEREIGN_MARKER_START}\n"
                f"{new_entry_line}\n"
                f"{SOVEREIGN_MARKER_END}\n"
            )

        return self._write_hosts(text)

    def remove_entry(self, host: str) -> Tuple[bool, str]:
        """Remove a specific host from the SOVEREIGN block."""
        try:
            text = self._path.read_text(errors="replace")
        except PermissionError:
            return False, "Permission denied — run as administrator/sudo"
        except FileNotFoundError:
            return False, "Hosts file not found"

        self._backup_hosts()
        lines = text.splitlines(keepends=True)
        new_lines = []
        in_block  = False
        removed   = False
        for line in lines:
            stripped = line.strip()
            if stripped == SOVEREIGN_MARKER_START:
                in_block = True
                new_lines.append(line)
                continue
            if stripped == SOVEREIGN_MARKER_END:
                in_block = False
                new_lines.append(line)
                continue
            if in_block and host in line and not stripped.startswith("#"):
                removed = True
                continue  # skip this line
            new_lines.append(line)

        if not removed:
            return False, f"Host '{host}' not found in SOVEREIGN block"
        return self._write_hosts("".join(new_lines))

    def remove_all_sovereign(self) -> Tuple[bool, str]:
        """Remove the entire SOVEREIGN-managed block from hosts file."""
        try:
            text = self._path.read_text(errors="replace")
        except PermissionError:
            return False, "Permission denied — run as administrator/sudo"

        self._backup_hosts()
        lines = text.splitlines(keepends=True)
        new_lines = []
        in_block  = False
        for line in lines:
            stripped = line.strip()
            if stripped == SOVEREIGN_MARKER_START:
                in_block = True
                continue
            if stripped == SOVEREIGN_MARKER_END:
                in_block = False
                continue
            if in_block:
                continue
            new_lines.append(line)

        ok, msg = self._write_hosts("".join(new_lines))
        if ok:
            return True, "All SOVEREIGN hosts entries removed"
        return ok, msg

    def flush_dns(self) -> Tuple[bool, str]:
        """Flush the OS DNS cache after hosts file changes."""
        import subprocess
        system = platform.system()
        try:
            if system == "Darwin":
                r = subprocess.run(
                    ["dscacheutil", "-flushcache"],
                    capture_output=True, text=True
                )
                subprocess.run(["killall", "-HUP", "mDNSResponder"],
                               capture_output=True)
                return r.returncode == 0, "DNS cache flushed (macOS)"
            elif system == "Linux":
                # Try systemd-resolved first
                r = subprocess.run(
                    ["systemd-resolve", "--flush-caches"],
                    capture_output=True, text=True
                )
                if r.returncode == 0:
                    return True, "DNS cache flushed (systemd-resolved)"
                # Fallback: nscd
                r2 = subprocess.run(
                    ["nscd", "-i", "hosts"],
                    capture_output=True, text=True
                )
                return r2.returncode == 0, "DNS cache flushed (nscd)"
            elif system == "Windows":
                r = subprocess.run(
                    ["ipconfig", "/flushdns"],
                    capture_output=True, text=True
                )
                return r.returncode == 0, r.stdout.strip()
            else:
                return False, f"DNS flush not implemented for {system}"
        except FileNotFoundError as e:
            return False, f"Command not found: {e}"
        except Exception as e:
            return False, str(e)

    def restore_backup(self) -> Tuple[bool, str]:
        """Restore the hosts file from the last SOVEREIGN backup."""
        if not self._backup.exists():
            return False, "No backup found"
        try:
            shutil.copy2(self._backup, self._path)
            return True, f"Restored from {self._backup}"
        except PermissionError:
            return False, "Permission denied — run as administrator/sudo"
        except Exception as e:
            return False, str(e)

    def get_current_text(self) -> str:
        """Return raw hosts file content."""
        try:
            return self._path.read_text(errors="replace")
        except (PermissionError, FileNotFoundError) as e:
            return f"# Cannot read hosts file: {e}"

    # ── Private ────────────────────────────────────────────────────

    def _validate_entry(self, ip: str, hostname: str) -> tuple[bool, str]:
        """Validate hosts entry for security using centralized sanitization.

        Blocks:
        - Invalid IP addresses
        - Private/loopback IPs (unless allow_private=True)
        - Invalid hostname formats (uses SECURITY.max_hostname_length)
        - Localhost aliases
        """
        # Validate IP address using Sanitizer
        try:
            Sanitizer.validate_ip(ip, allow_private=self._allow_private)
        except ValueError as e:
            return False, str(e)

        # Validate hostname using centralized security config
        valid, msg = validate_hostname_strict(hostname)
        if not valid:
            return False, msg

        # Additional security: block localhost aliases (redundant but explicit)
        if hostname.lower() in ("localhost", "localhost.localdomain"):
            return False, "Cannot redirect localhost"

        return True, ""

    def _backup_hosts(self):
        if self._path.exists():
            try:
                shutil.copy2(self._path, self._backup)
            except Exception as e:
                log.warning("Could not backup hosts file: %s", e)

    def _write_hosts(self, text: str) -> Tuple[bool, str]:
        tmp = self._path.with_suffix(".tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self._path)
            log.info("Hosts file updated: %s", self._path)
            return True, "Hosts file updated"
        except PermissionError:
            try:
                tmp.unlink()
            except OSError:
                log.debug("Could not remove tmp file %s during cleanup", tmp)
            return False, "Permission denied — run as administrator/sudo"
        except Exception as e:
            return False, str(e)
