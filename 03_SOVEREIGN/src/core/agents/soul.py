"""SOUL — reads laws from agents/docs/SOUL.md.
Python does not define the laws. Markdown defines the laws.
Python only parses and enforces them.

To change a law: edit SOUL.md. Then call get_soul().reload().
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass
import os
from typing import Dict, List, Optional

# Track module load time — LAW_3 is enforced after HEARTBEAT_TIMEOUT_SECONDS from boot
_BOOT_TIME = time.time()

from src.core.agents.md_loader import soul_config

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SoulCheckResult:
    passed:       bool
    violated_law: Optional[str]
    reason:       str
    flagged:      bool = False


class Soul:
    """Enforces laws loaded from SOUL.md. Python executes. Markdown defines."""

    def __init__(self):
        self._md     = soul_config()
        self._laws:  List[Dict] = []
        self._version: str = ""
        self._load()

    def _load(self):
        self._version = self._md.get("VERSION", "?")
        self._laws    = self._md.get_sections_by_prefix("LAW_")
        log.info("Soul loaded from SOUL.md: version=%s  laws=%d",
                 self._version, len(self._laws))

    def reload(self):
        """Hot-reload SOUL.md — no restart needed."""
        self._md.reload()
        self._load()
        log.info("Soul reloaded: version=%s  laws=%d", self._version, len(self._laws))

    @property
    def VERSION(self) -> str:
        return self._version

    @property
    def laws(self) -> List[Dict]:
        return list(self._laws)

    def hash(self) -> str:
        return self._md.hash

    def check(self, action: str, context: dict) -> SoulCheckResult:
        """Check action against all laws from SOUL.md. First violation wins."""
        action_lower = action.lower()
        for law in self._laws:
            blocks_raw = law.get("BLOCKS", "")
            if blocks_raw:
                for term in (b.strip().lower() for b in blocks_raw.split(",") if b.strip()):
                    if term and term in action_lower:
                        return SoulCheckResult(
                            passed=False,
                            violated_law=law.get("ID", "?"),
                            reason=f"{law.get('NAME','?')}: blocked term '{term}'. {law.get('TEXT','')}",
                            flagged=True,
                        )
            # LAW_3: heartbeat timeout check
            # Grace period: skip enforcement during first HEARTBEAT_TIMEOUT_SECONDS after boot
            # (heartbeat thread hasn't had time to emit first pulse yet)
            if law.get("ID") == "LAW_3":
                timeout = int(law.get("HEARTBEAT_TIMEOUT_SECONDS", 30))
                last_pulse = context.get("last_heartbeat_ts", 0)
                boot_elapsed = time.time() - _BOOT_TIME
                if boot_elapsed < timeout:
                    pass  # Grace period — heartbeat hasn't had time to start yet
                elif last_pulse == 0:
                    # Heartbeat was never registered — enforce after grace period
                    return SoulCheckResult(
                        passed=False,
                        violated_law="LAW_3",
                        reason=f"No heartbeat pulse received (grace period expired after {timeout}s)",
                        flagged=True,
                    )
                elif (time.time() - last_pulse) > timeout:
                    return SoulCheckResult(
                        passed=False,
                        violated_law="LAW_3",
                        reason=f"Heartbeat pulse older than {timeout}s — action blocked",
                        flagged=True,
                    )
        return SoulCheckResult(passed=True, violated_law=None, reason="All laws satisfied")

    def get_law(self, law_id: str) -> Optional[Dict]:
        return next((l for l in self._laws if l.get("ID") == law_id), None)


_SOUL: Optional[Soul] = None


def get_soul() -> Soul:
    global _SOUL
    if _SOUL is None:
        _SOUL = Soul()
    return _SOUL


# Alias for backward compat
class _SoulProxy:
    def __getattr__(self, name):
        return getattr(get_soul(), name)
    def __call__(self, *a, **kw):
        return get_soul()

SOUL = _SoulProxy()
