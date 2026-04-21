"""
boot/context.py — BootContext: shared state passed between boot phases.

The context accumulates results as phases complete.
Each phase reads from context (previous results) and writes to it (its outputs).
After boot completes, BootContext holds all live system objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from core.config import SystemConfig
    from core.crypto import KralIdentity
    from law_engine.registry import LawRegistry
    from law_engine.enforcer import LawEnforcer
    from memory.substrate import MemorySubstrate, MemoryRootHash
    from obsidian.ingest import IngestReport


@dataclass
class BootContext:
    """
    Accumulates all live objects created during the boot sequence.
    Populated phase by phase — fields are None until their phase completes.
    """
    # Filled before boot starts
    config:            Optional["SystemConfig"]  = None

    # Phase MEMORY
    memory_substrate:  Optional["MemorySubstrate"]  = None
    memory_root_hash:  Optional["MemoryRootHash"]   = None

    # Phase RAG
    ingest_report:     Optional["IngestReport"]     = None

    # Phase LAWS
    law_registry:      Optional["LawRegistry"]      = None
    law_enforcer:      Optional["LawEnforcer"]       = None
    kral_identity:     Optional["KralIdentity"]      = None

    # Phase PREFLIGHT
    preflight_report:  Any = None   # PreflightReport (avoid circular import)

    # Phase AGENTS — populated in Phase 5/6
    heartbeat_agent:   Any = None
    reactive_agent:    Any = None

    # Extra metadata
    extras: dict = field(default_factory=dict)

    @property
    def vault_path(self) -> Optional[Path]:
        return Path(self.config.obsidian_vault_path) if self.config else None

    def is_memory_ready(self) -> bool:
        return self.memory_substrate is not None and self.memory_substrate.is_healthy()

    def is_laws_ready(self) -> bool:
        return (
            self.law_registry is not None
            and self.law_registry.is_sealed
            and self.law_enforcer is not None
        )

    def is_preflight_passed(self) -> bool:
        return (
            self.preflight_report is not None
            and self.preflight_report.all_passed
        )

    def is_agents_ready(self) -> bool:
        return self.heartbeat_agent is not None and self.reactive_agent is not None
