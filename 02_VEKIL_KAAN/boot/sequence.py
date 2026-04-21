"""
boot/sequence.py — Boot sequence orchestrator.

Executes 5 phases in strict order. Each phase must complete before the next.
Any failure raises the appropriate BootFailure subclass — process halts.
No retries at this level. Fix root cause and reboot.

Phase flow:
  MEMORY   → MemorySubstrate.boot()
  RAG      → ObsidianIngestPipeline.boot_ingest()
  LAWS     → LawRegistry.load_all() + seal() + LawEnforcer()
  PREFLIGHT→ run_preflight(ctx) — 7 checks
  AGENTS   → HeartbeatAgent.boot() + ReactiveAgent.boot() [Phase 5/6]

After all 5 phases: boot_guards(ctx) builds the runtime guard objects.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.exceptions import (
    AgentBootFailure,
    BootFailure,
    LawEnforcementBootFailure,
    MemoryBootFailure,
    PreflightFailure,
    RAGBootFailure,
)

log = logging.getLogger(__name__)

BOOT_PHASES = [
    ("MEMORY",    "Memory substrate initialization (ChromaDB + SQLite)"),
    ("RAG",       "Obsidian vault ingest into ChromaDB"),
    ("LAWS",      "Markdown law parsing + registry sealing (KRAL Ed25519)"),
    ("PREFLIGHT", "Pre-flight validation (7 checks, zero tolerance)"),
    ("AGENTS",    "Agent instantiation + brotherhood bond verification"),
]


@dataclass
class PhaseResult:
    phase_id:   str
    success:    bool
    elapsed_ms: float
    detail:     str  = ""
    error:      Any  = None


@dataclass
class BootReport:
    phases:           list[PhaseResult] = field(default_factory=list)
    total_elapsed_ms: float             = 0.0

    @property
    def success(self) -> bool:
        return all(p.success for p in self.phases)

    @property
    def completed_phases(self) -> list[str]:
        return [p.phase_id for p in self.phases if p.success]

    def summary(self) -> str:
        lines = ["=" * 60, "VEKIL-KAAN BOOT REPORT", "=" * 60]
        for p in self.phases:
            status = "✅ PASS" if p.success else "❌ FAIL"
            lines.append(f"  [{status}] {p.phase_id} ({p.elapsed_ms:.0f}ms)")
            if p.detail:
                lines.append(f"           {p.detail}")
            if p.error:
                lines.append(f"           ERROR: {p.error}")
        lines.append("=" * 60)
        lines.append(
            f"  RESULT: {'BOOT OK' if self.success else 'BOOT FAILED'}"
            f" | {self.total_elapsed_ms:.0f}ms total"
        )
        lines.append("=" * 60)
        return "\n".join(lines)


class BootSequence:
    """
    Executes the 5-phase boot sequence.

    Usage:
        seq = BootSequence(config)
        report, ctx = seq.execute()

    After execute() completes:
        ctx.memory_substrate  — live, booted substrate
        ctx.law_registry      — sealed registry
        ctx.law_enforcer      — ready enforcer
        ctx.preflight_report  — all 7 checks passed
    """

    def __init__(self, config: Any) -> None:
        self._config = config

    def execute(self) -> tuple["BootReport", "BootContext"]:
        """
        Run all 5 phases. Returns (BootReport, BootContext).
        Raises BootFailure on any phase failure — halts immediately.
        """
        from boot.context import BootContext

        t_start = time.monotonic()
        ctx     = BootContext(config=self._config)
        report  = BootReport()

        for phase_id, phase_desc in BOOT_PHASES:
            log.info("── BOOT [%s] %s", phase_id, phase_desc)
            t0 = time.monotonic()

            try:
                detail = self._execute_phase(phase_id, ctx)
                elapsed = (time.monotonic() - t0) * 1000
                report.phases.append(PhaseResult(phase_id, True, elapsed, detail))
                log.info("── BOOT [%s] ✅ PASS (%dms) — %s", phase_id, elapsed, detail)

            except BootFailure as e:
                elapsed = (time.monotonic() - t0) * 1000
                report.phases.append(PhaseResult(phase_id, False, elapsed, error=str(e)))
                log.error("── BOOT [%s] ❌ FAIL — %s", phase_id, e)
                report.total_elapsed_ms = (time.monotonic() - t_start) * 1000
                raise   # Let BootFailure propagate — caller decides how to handle

            except Exception as e:
                # Wrap unexpected exceptions as BootFailure
                elapsed = (time.monotonic() - t0) * 1000
                wrapped = BootFailure(f"Unexpected error in phase {phase_id}: {e}")
                report.phases.append(PhaseResult(phase_id, False, elapsed, error=str(e)))
                log.error("── BOOT [%s] ❌ UNEXPECTED — %s", phase_id, e)
                report.total_elapsed_ms = (time.monotonic() - t_start) * 1000
                raise wrapped from e

        report.total_elapsed_ms = (time.monotonic() - t_start) * 1000
        return report, ctx

    # ── Phase implementations ─────────────────────────────────────────────────

    def _execute_phase(self, phase_id: str, ctx: "BootContext") -> str:
        dispatch = {
            "MEMORY":    self._phase_memory,
            "RAG":       self._phase_rag,
            "LAWS":      self._phase_laws,
            "PREFLIGHT": self._phase_preflight,
            "AGENTS":    self._phase_agents,
        }
        return dispatch[phase_id](ctx)

    def _phase_memory(self, ctx: "BootContext") -> str:
        """
        Initialize ChromaDB + SQLite memory substrate.
        Raises MemoryBootFailure on any error.
        """
        from memory.substrate import MemorySubstrate

        cfg = ctx.config
        try:
            sub = MemorySubstrate(
                chroma_host=cfg.chroma_host,
                chroma_port=cfg.chroma_port,
                sqlite_path=Path(cfg.sqlite_path),
                ephemeral=False,
            )
            root_hash = sub.boot()
        except Exception as e:
            raise MemoryBootFailure(f"Memory substrate boot failed: {e}") from e

        ctx.memory_substrate = sub
        ctx.memory_root_hash = root_hash
        return (
            f"root={root_hash.value[:16]}... "
            f"| events={root_hash.event_count}"
        )

    def _phase_rag(self, ctx: "BootContext") -> str:
        """
        Ingest Obsidian vault into ChromaDB obsidian_knowledge collection.
        Raises RAGBootFailure on ingest errors.
        """
        from obsidian.ingest import ObsidianIngestPipeline
        from core.exceptions import IngestFailed, VaultNotFound

        sub        = ctx.memory_substrate
        vault_path = ctx.vault_path

        if vault_path is None:
            raise RAGBootFailure("vault_path not configured — set OBSIDIAN_VAULT_PATH")

        try:
            col      = sub.get_collection("obsidian_knowledge")
            pipeline = ObsidianIngestPipeline(
                chroma_collection=col,
                vault_path=vault_path,
                embedding_function=None,  # Production: DefaultEmbeddingFunction
            )
            report = pipeline.boot_ingest()
        except (VaultNotFound, IngestFailed) as e:
            raise RAGBootFailure(f"Vault ingest failed: {e}") from e
        except Exception as e:
            raise RAGBootFailure(f"Unexpected ingest error: {e}") from e

        ctx.ingest_report = report
        return report.summary()

    def _phase_laws(self, ctx: "BootContext") -> str:
        """
        Parse all 6 law files → seal registry with KRAL Ed25519 → build enforcer.
        Raises LawEnforcementBootFailure on any error.
        """
        from law_engine.parser   import MarkdownLawParser
        from law_engine.extractor import LawExtractor
        from law_engine.registry import LawRegistry
        from law_engine.enforcer import LawEnforcer
        from core.crypto          import load_kral_identity
        from core.exceptions      import KeyLoadError, FingerprintMismatch
        from pathlib import Path

        cfg = ctx.config

        # Load KRAL identity (verify-only mode acceptable if private key absent)
        identity = None
        try:
            priv_path = Path(cfg.kral_private_key_path)
            pub_path  = Path(cfg.kral_public_key_path)
            identity  = load_kral_identity(priv_path, pub_path, verify_fingerprint=True)
        except (KeyLoadError, FingerprintMismatch) as e:
            raise LawEnforcementBootFailure(f"KRAL identity load failed: {e}") from e
        except Exception as e:
            raise LawEnforcementBootFailure(f"Crypto init error: {e}") from e

        ctx.kral_identity = identity

        # Parse + extract + seal registry
        laws_dir = Path(cfg.laws_dir)
        try:
            registry = LawRegistry()
            registry.load_all(laws_dir)
            seal_hash = registry.seal(identity)
        except LawEnforcementBootFailure:
            raise
        except Exception as e:
            raise LawEnforcementBootFailure(f"Law engine failed: {e}") from e

        ctx.law_registry = registry
        ctx.law_enforcer = LawEnforcer(registry=registry)

        return (
            f"{registry.total_laws} laws | seal={seal_hash[:16]}... "
            f"| KRAL fingerprint={'OK' if identity else 'verify-only'}"
        )

    def _phase_preflight(self, ctx: "BootContext") -> str:
        """
        Run all 7 pre-flight checks. First failure raises PreflightFailure.
        """
        from boot.preflight import run_preflight

        try:
            preflight_report = run_preflight(ctx)
        except PreflightFailure:
            raise   # Already logged — let it propagate

        ctx.preflight_report = preflight_report
        return preflight_report.summary().split("\n")[0]  # First line only

    def _phase_agents(self, ctx: "BootContext") -> str:
        """
        Instantiate HeartbeatAgent and ReactiveAgent.
        Phase 5/6 implement the actual agent logic.
        For now: create stub agents and verify the context is ready.
        Raises AgentBootFailure if agents cannot be created.
        """
        # Verify prerequisites
        if not ctx.is_memory_ready():
            raise AgentBootFailure("Memory substrate not ready for agent boot")
        if not ctx.is_laws_ready():
            raise AgentBootFailure("Law registry not sealed — cannot boot agents")
        if not ctx.is_preflight_passed():
            raise AgentBootFailure("Pre-flight not passed — cannot boot agents")

        # Phase 5/6: real agent instantiation
        # For now: record that agents would boot here
        log.info(
            "Agent boot: Phase 5/6 will instantiate HeartbeatAgent and ReactiveAgent here"
        )

        # Log brotherhood bond event
        try:
            from memory.event_store import EventStore, EventType, AgentSource, MemoryEvent
            from memory.audit_log   import AuditLog, AuditLevel
            from core.config import get_config

            hmac_secret = ctx.config.event_hmac_secret
            store = EventStore(ctx.memory_substrate.get_sqlite(), hmac_secret)
            audit = AuditLog(ctx.memory_substrate.get_sqlite())

            store.write(MemoryEvent(
                source=AgentSource.SYSTEM,
                type=EventType.BOOT,
                payload={
                    "phase":   "AGENTS",
                    "status":  "phase_5_6_pending",
                    "message": "Agent stubs registered — full implementation in Phase 5/6",
                }
            ))
            audit.log(
                AuditLevel.INFO,
                "SYSTEM",
                "boot_agents",
                "AGENTS phase: stubs registered, Phase 5/6 pending",
            )
        except Exception as e:
            log.warning("Could not log agent boot event: %s", e)

        return "agent stubs registered | Phase 5/6 will implement full agent boot"
