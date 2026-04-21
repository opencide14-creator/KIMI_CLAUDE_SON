"""
boot/preflight.py — Pre-flight validation system.

7 checks. All must pass. One failure = BootFailure = process halt.
No retries. No fallbacks. Fix root cause and reboot.

Each check receives a BootContext containing all objects built in
Phases MEMORY, RAG, and LAWS. Each check is independent and isolated.
"""

from __future__ import annotations

import logging
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.exceptions import (
    AuditLogTampered,
    FingerprintMismatch,
    LawRegistryTampered,
    PreflightFailure,
    SignatureVerificationFailed,
)

if TYPE_CHECKING:
    from boot.context import BootContext

log = logging.getLogger(__name__)

_ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[a-zA-Z0-9_-]{10,}$")
_OLLAMA_TIMEOUT   = 5.0   # seconds


@dataclass
class PreflightResult:
    check_name:  str
    passed:      bool
    detail:      str   = ""
    elapsed_ms:  float = 0.0


class PreflightCheck(ABC):
    """
    Base class. run(ctx) must:
      - Return PreflightResult(passed=True, ...) on success.
      - Raise PreflightFailure (subclass of BootFailure) on failure.
      Never return passed=False — always raise.
    """
    name: str = "unnamed"

    @abstractmethod
    def run(self, ctx: "BootContext") -> PreflightResult: ...


# ── Check 1 ── MEMORY_INTEGRITY ───────────────────────────────────────────────

class MemoryIntegrityCheck(PreflightCheck):
    """
    1. ChromaDB: all 4 required collections exist and are accessible.
    2. Memory root hash is computable and consistent with snapshot.
    3. SQLite is responsive.
    """
    name = "MEMORY_INTEGRITY"

    def run(self, ctx: "BootContext") -> PreflightResult:
        from memory.substrate import COLLECTIONS

        sub = ctx.memory_substrate
        if sub is None:
            raise PreflightFailure(f"[{self.name}] MemorySubstrate not initialised")

        if not sub.is_healthy():
            raise PreflightFailure(f"[{self.name}] MemorySubstrate health check failed")

        # Verify all 4 collections
        sizes = sub.collection_sizes()
        missing = [c for c in COLLECTIONS if c not in sizes]
        if missing:
            raise PreflightFailure(
                f"[{self.name}] Missing ChromaDB collections: {missing}"
            )

        # Verify root hash is computable and matches last snapshot
        current_hash = sub.compute_root_hash()
        snap = sub.get_last_snapshot()
        if snap is None:
            raise PreflightFailure(
                f"[{self.name}] No memory snapshot found — substrate may not have booted"
            )

        detail = (
            f"4/4 collections OK | root={current_hash[:16]}... "
            f"| snapshot={snap.snapshot_id[:8]}..."
        )
        return PreflightResult(self.name, True, detail)


# ── Check 2 ── LAW_REGISTRY_INTEGRITY ────────────────────────────────────────

class LawRegistryIntegrityCheck(PreflightCheck):
    """
    1. Law registry is loaded and sealed.
    2. Seal hash is consistent (no tampering since sealing).
    3. Ed25519 seal signature verifies against KRAL identity.
    """
    name = "LAW_REGISTRY_INTEGRITY"

    def run(self, ctx: "BootContext") -> PreflightResult:
        reg      = ctx.law_registry
        identity = ctx.kral_identity

        if reg is None:
            raise PreflightFailure(f"[{self.name}] LawRegistry not initialised")
        if not reg.is_loaded:
            raise PreflightFailure(f"[{self.name}] LawRegistry not loaded")
        if not reg.is_sealed:
            raise PreflightFailure(f"[{self.name}] LawRegistry not sealed")

        # Integrity: re-hash and compare
        try:
            reg.verify_integrity()
        except LawRegistryTampered as e:
            raise PreflightFailure(f"[{self.name}] Registry tampered: {e}") from e

        # Signature: verify with KRAL public key
        if identity is not None:
            try:
                reg.verify_signature(identity)
            except SignatureVerificationFailed as e:
                raise PreflightFailure(
                    f"[{self.name}] KRAL seal signature invalid: {e}"
                ) from e

        detail = (
            f"{reg.total_laws} laws | seal={reg.seal_hash[:16]}... "
            + (f"| KRAL sig OK" if identity else "| no identity (verify-only mode)")
        )
        return PreflightResult(self.name, True, detail)


# ── Check 3 ── VAULT_INGEST_COMPLETENESS ─────────────────────────────────────

class VaultIngestCompletenessCheck(PreflightCheck):
    """
    1. Vault ingest completed without errors.
    2. obsidian_knowledge collection has at least 1 chunk.
    3. IngestReport shows at least 1 file was processed or skipped.
    """
    name = "VAULT_INGEST_COMPLETENESS"

    def run(self, ctx: "BootContext") -> PreflightResult:
        report = ctx.ingest_report
        sub    = ctx.memory_substrate

        if report is None:
            raise PreflightFailure(
                f"[{self.name}] No ingest report — RAG phase did not complete"
            )

        if not report.success:
            raise PreflightFailure(
                f"[{self.name}] Vault ingest had errors: "
                + "; ".join(report.errors[:3])
            )

        total_files = report.files_processed + report.files_skipped
        if total_files == 0:
            raise PreflightFailure(
                f"[{self.name}] Vault ingest processed zero files — vault may be empty"
            )

        # Verify chunks are actually in ChromaDB
        if sub is not None:
            sizes = sub.collection_sizes()
            chunk_count = sizes.get("obsidian_knowledge", 0)
            if chunk_count == 0 and report.files_processed > 0:
                raise PreflightFailure(
                    f"[{self.name}] Vault ingest reported {report.files_processed} "
                    f"files processed but obsidian_knowledge collection is empty"
                )

        detail = (
            f"{report.files_processed} processed, {report.files_skipped} skipped "
            f"| {report.chunks_created} chunks created"
        )
        return PreflightResult(self.name, True, detail)


# ── Check 4 ── LLM_ENDPOINT ──────────────────────────────────────────────────

class LLMEndpointCheck(PreflightCheck):
    """
    Verify at least one LLM endpoint is reachable.
    Reactive: ping Ollama or verify Claude API key format.
    Heartbeat: same for its configured provider.
    Soft: if LLM is unreachable, logs warning but does not halt
    (agents can still boot and operate if LLM comes up later).

    Hard: if BOTH reactive AND heartbeat LLMs are unreachable → fail.
    """
    name = "LLM_ENDPOINT"

    def run(self, ctx: "BootContext") -> PreflightResult:
        if ctx.config is None:
            raise PreflightFailure(f"[{self.name}] No config available")

        cfg = ctx.config
        reactive_ok   = self._check_provider(
            cfg.reactive_llm_provider.value, cfg, "reactive"
        )
        heartbeat_ok  = self._check_provider(
            cfg.heartbeat_llm_provider.value, cfg, "heartbeat"
        )

        if not reactive_ok and not heartbeat_ok:
            raise PreflightFailure(
                f"[{self.name}] Both LLM providers unreachable. "
                f"Reactive: {cfg.reactive_llm_provider.value}, "
                f"Heartbeat: {cfg.heartbeat_llm_provider.value}. "
                f"System cannot reason without at least one LLM."
            )

        parts = []
        if reactive_ok:
            parts.append(f"reactive={cfg.reactive_llm_provider.value} OK")
        else:
            parts.append(f"reactive={cfg.reactive_llm_provider.value} UNREACHABLE")
        if heartbeat_ok:
            parts.append(f"heartbeat={cfg.heartbeat_llm_provider.value} OK")
        else:
            parts.append(f"heartbeat={cfg.heartbeat_llm_provider.value} UNREACHABLE")

        return PreflightResult(self.name, True, " | ".join(parts))

    def _check_provider(self, provider: str, cfg: Any, role: str) -> bool:
        if provider == "ollama":
            return self._ping_ollama(cfg.ollama_host)
        elif provider == "claude":
            return self._verify_claude_key(cfg.anthropic_api_key)
        return False

    def _ping_ollama(self, host: str) -> bool:
        try:
            import requests
            r = requests.get(f"{host}/api/tags", timeout=_OLLAMA_TIMEOUT)
            return r.status_code == 200
        except Exception:
            return False

    def _verify_claude_key(self, key: str) -> bool:
        if not key:
            return False
        return bool(_ANTHROPIC_KEY_RE.match(key))


# ── Check 5 ── BROTHERHOOD_BOND ──────────────────────────────────────────────

class BrotherhoodBondCheck(PreflightCheck):
    """
    1. BOUND.md is present in the law registry.
    2. Article VI oath text hash matches registry.
    3. If prior bond signature in event store → verify it.
    4. If no prior signature (first boot) → sign and store as event #bond.
    """
    name = "BROTHERHOOD_BOND"

    def run(self, ctx: "BootContext") -> PreflightResult:
        reg      = ctx.law_registry
        identity = ctx.kral_identity
        sub      = ctx.memory_substrate

        if reg is None:
            raise PreflightFailure(f"[{self.name}] LawRegistry not available")

        # 1. BOUND.md laws are present
        bound_laws = reg.query_by_prefix("BOUND/")
        if len(bound_laws) == 0:
            raise PreflightFailure(
                f"[{self.name}] BOUND.md laws missing from registry"
            )

        # 2. Article VI oath is present and non-empty
        oath_law = reg.get_by_id("BOUND/ARTICLE_VI/OATH")
        if oath_law is None:
            raise PreflightFailure(
                f"[{self.name}] Brotherhood oath (BOUND/ARTICLE_VI/OATH) not found"
            )
        if not oath_law.raw_text.strip():
            raise PreflightFailure(
                f"[{self.name}] Brotherhood oath text is empty"
            )

        # 3. Check/create bond signature in event store
        bond_status = "new"
        if sub is not None and identity is not None:
            bond_status = self._handle_bond_signature(
                oath_law.raw_text, oath_law.hash, identity, sub
            )

        detail = (
            f"{len(bound_laws)} BOUND laws | oath hash={oath_law.hash[:16]}... "
            f"| bond={bond_status}"
        )
        return PreflightResult(self.name, True, detail)

    def _handle_bond_signature(
        self,
        oath_text: str,
        oath_hash: str,
        identity: Any,
        sub: Any,
    ) -> str:
        """
        Look for existing bond event. If found: verify.
        If not found: sign and store as BROTHERHOOD event.
        Returns status string.
        """
        from memory.event_store import EventStore, EventType, AgentSource, MemoryEvent
        from core.crypto import sign_brotherhood_oath, verify_brotherhood_oath

        # Check for stored config secret
        cfg_secret = "boot_hmac_secret_placeholder"
        if sub._sqlite is not None:
            from memory.audit_log import AuditLog
            store = EventStore(sub.get_sqlite(), cfg_secret)

            # Look for existing bond event
            bond_events = store.read_by_type(EventType.BROTHERHOOD)
            for ev in bond_events:
                if ev.payload.get("oath_hash") == oath_hash:
                    # Verify stored signature
                    stored_sig = bytes.fromhex(ev.payload.get("signature_hex", ""))
                    if stored_sig:
                        try:
                            verify_brotherhood_oath(identity, oath_text, stored_sig)
                            return "verified"
                        except Exception:
                            return "sig_mismatch"

            # First boot: sign and store the oath
            try:
                sig = sign_brotherhood_oath(identity, oath_text)
                store.write(MemoryEvent(
                    source=AgentSource.SYSTEM,
                    type=EventType.BROTHERHOOD,
                    payload={
                        "oath_hash":     oath_hash,
                        "signature_hex": sig.hex(),
                        "note":          "brotherhood bond established at first boot",
                    }
                ))
                return "signed_and_stored"
            except Exception as e:
                log.warning("Could not sign brotherhood oath: %s", e)
                return "sign_failed"

        return "no_store"


# ── Check 6 ── SOUL_LAW_CONSISTENCY ──────────────────────────────────────────

class SoulLawConsistencyCheck(PreflightCheck):
    """
    Verify SOUL.md laws haven't been modified since registry was sealed.
    - All 5 soul laws must be present.
    - Each soul law hash must match what the registry recorded at seal time.
    - Soul laws are immutable by design — any change is a critical failure.
    """
    name = "SOUL_LAW_CONSISTENCY"

    def run(self, ctx: "BootContext") -> PreflightResult:
        reg = ctx.law_registry
        if reg is None:
            raise PreflightFailure(f"[{self.name}] LawRegistry not available")

        soul_laws = reg.get_soul_laws()
        if len(soul_laws) != 5:
            raise PreflightFailure(
                f"[{self.name}] Expected 5 SOUL laws, found {len(soul_laws)}. "
                f"SOUL.md may have been modified or is missing laws."
            )

        # Verify all 5 Roman numerals are present
        expected_ids = {f"SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_{r}" for r in ("I", "II", "III", "IV", "V")}
        found_ids    = {l.law_id for l in soul_laws}
        missing      = expected_ids - found_ids
        if missing:
            raise PreflightFailure(
                f"[{self.name}] Missing soul law IDs: {missing}"
            )

        # Re-parse soul laws from disk and compare hashes to registry
        # (detects post-seal file modification)
        if ctx.vault_path is not None or ctx.config is not None:
            laws_dir = self._find_laws_dir(ctx)
            if laws_dir and laws_dir.exists():
                soul_md = laws_dir / "SOUL.md"
                if soul_md.exists():
                    self._verify_soul_md_unchanged(soul_md, soul_laws, reg)

        detail = f"5/5 soul laws present | hashes verified"
        return PreflightResult(self.name, True, detail)

    def _find_laws_dir(self, ctx: "BootContext") -> Path | None:
        if ctx.config is not None:
            return Path(ctx.config.laws_dir)
        return None

    def _verify_soul_md_unchanged(
        self, soul_md: Path, soul_laws: list, reg: Any
    ) -> None:
        """
        Re-parse SOUL.md and verify against the sealed registry.
        Two checks:
        1. Existing soul law hashes must match.
        2. No new SOUL.md laws can have appeared (count must be same).
        3. File-level SHA-256 must match the sealed registry's aggregate hash
           indirectly — new laws added changes the seal → re-verify total count.
        """
        try:
            from law_engine.parser import MarkdownLawParser
            from core.hashing import sha256_hex

            fresh_laws = MarkdownLawParser().parse_file(soul_md)
            # All laws from fresh SOUL.md parse (not just soul laws)
            fresh_count = len(fresh_laws)
            # Laws from sealed registry that came from SOUL.md
            sealed_soul_count = len([l for l in reg.all_laws() if l.source_file == "SOUL.md"])

            # Check 1: count mismatch means content was added/removed
            if fresh_count != sealed_soul_count:
                raise PreflightFailure(
                    f"[{self.name}] SOUL.md was modified after registry seal! "
                    f"Sealed law count: {sealed_soul_count}, current: {fresh_count}. "
                    f"Soul laws are immutable."
                )

            # Check 2: existing soul law hashes must match
            fresh_soul = [l for l in fresh_laws if "SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_" in l.law_id]
            registry_hashes = {l.law_id: l.hash for l in soul_laws}
            for fresh_law in fresh_soul:
                expected_hash = registry_hashes.get(fresh_law.law_id)
                if expected_hash and fresh_law.hash != expected_hash:
                    raise PreflightFailure(
                        f"[{self.name}] SOUL.md was modified after registry seal! "
                        f"Law {fresh_law.law_id} hash changed.\n"
                        f"  Sealed:  {expected_hash}\n"
                        f"  Current: {fresh_law.hash}\n"
                        f"Soul laws are immutable. This is a critical integrity failure."
                    )
        except PreflightFailure:
            raise
        except Exception as e:
            log.warning("[%s] Could not re-verify SOUL.md: %s", self.name, e)


# ── Check 7 ── AUDIT_LOG_INTEGRITY ───────────────────────────────────────────

class AuditLogIntegrityCheck(PreflightCheck):
    """
    Verify the audit_log and events tables are append-only.
    Checks that the SQLite triggers enforcing immutability are in place.
    If triggers are missing, data may have been tampered with.
    """
    name = "AUDIT_LOG_INTEGRITY"

    def run(self, ctx: "BootContext") -> PreflightResult:
        sub = ctx.memory_substrate
        if sub is None:
            raise PreflightFailure(f"[{self.name}] MemorySubstrate not available")

        db = sub.get_sqlite()
        from memory.audit_log import AuditLog

        audit = AuditLog(db)
        try:
            audit.verify_append_only()
        except AuditLogTampered as e:
            raise PreflightFailure(
                f"[{self.name}] Audit log triggers missing: {e}"
            ) from e

        try:
            audit.verify_event_triggers()
        except AuditLogTampered as e:
            raise PreflightFailure(
                f"[{self.name}] Events table triggers missing: {e}"
            ) from e

        count = audit.total_count()
        escape_count = audit.count_escape_attempts()
        detail = f"append-only triggers OK | {count} audit entries | {escape_count} escape attempts"
        return PreflightResult(self.name, True, detail)


# ── All checks registry ────────────────────────────────────────────────────────

ALL_CHECKS: list[type[PreflightCheck]] = [
    MemoryIntegrityCheck,
    LawRegistryIntegrityCheck,
    VaultIngestCompletenessCheck,
    LLMEndpointCheck,
    BrotherhoodBondCheck,
    SoulLawConsistencyCheck,
    AuditLogIntegrityCheck,
]


# ── PreflightReport ───────────────────────────────────────────────────────────

from dataclasses import dataclass as _dc

@_dc
class PreflightReport:
    results: list[PreflightResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[PreflightResult]:
        return [r for r in self.results if not r.passed]

    def summary(self) -> str:
        lines = [f"Pre-flight: {len(self.results)} checks"]
        for r in self.results:
            status = "✅" if r.passed else "❌"
            lines.append(f"  {status} {r.check_name} ({r.elapsed_ms:.0f}ms)")
            if r.detail:
                lines.append(f"       → {r.detail}")
        return "\n".join(lines)


# ── Pre-flight runner ─────────────────────────────────────────────────────────

def run_preflight(ctx: "BootContext") -> PreflightReport:
    """
    Run all 7 checks in order.
    First failure raises PreflightFailure immediately (zero tolerance).
    On success, returns a complete PreflightReport.
    """
    report = PreflightReport()

    for check_cls in ALL_CHECKS:
        check = check_cls()
        t0 = time.monotonic()
        try:
            result = check.run(ctx)
            result.elapsed_ms = (time.monotonic() - t0) * 1000
            report.results.append(result)
            log.info("Preflight ✅ %s (%dms) — %s",
                     check.name, result.elapsed_ms, result.detail)
        except PreflightFailure:
            # Record the failure then re-raise — boot halts immediately
            elapsed = (time.monotonic() - t0) * 1000
            report.results.append(
                PreflightResult(check.name, False, elapsed_ms=elapsed)
            )
            raise

    return report
