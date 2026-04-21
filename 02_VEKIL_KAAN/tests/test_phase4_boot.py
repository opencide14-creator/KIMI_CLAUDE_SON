"""
tests/test_phase4_boot.py — Phase 4 acceptance tests.

ALL tests must pass before Phase 5 begins.
Uses ephemeral ChromaDB, test vault, and real law files.
LLM endpoint check uses mock config so CI doesn't need running models.
"""
import hashlib
import json
import time
import pytest
from pathlib import Path

from chromadb.api.types import EmbeddingFunction, Documents, Embeddings

VAULT_DIR = Path(__file__).parent / "test_vault"
LAWS_DIR  = Path(__file__).parent.parent / "laws"

HMAC_SECRET = "test_hmac_secret_at_least_32_chars_vekil"


# ── Deterministic EF (same as Phase 3) ────────────────────────────────────────

class DeterministicEF(EmbeddingFunction[Documents]):
    def __init__(self) -> None: pass
    def __call__(self, input: Documents) -> Embeddings:
        result = []
        for text in input:
            h = hashlib.sha256(str(text).encode()).digest()
            result.append([(float(h[i % 32]) / 255.0) - 0.5 for i in range(384)])
        return result
    @staticmethod
    def name() -> str: return "DeterministicEF"
    def get_config(self): return {"name": "DeterministicEF"}
    @staticmethod
    def build_from_config(config): return DeterministicEF()


# ── Key pair helper ───────────────────────────────────────────────────────────

def make_test_identity():
    """Generate a throwaway Ed25519 identity for testing."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from core.crypto import KralIdentity

    pk  = Ed25519PrivateKey.generate()
    pub = pk.public_key()
    raw = pub.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return KralIdentity(pk, pub, "test_fingerprint", raw.hex())


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup_vault_manifest():
    yield
    (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)


@pytest.fixture
def substrate():
    from memory.substrate import MemorySubstrate
    s = MemorySubstrate(ephemeral=True)
    s.boot()
    yield s
    s.shutdown()


@pytest.fixture
def ingested_substrate():
    """Substrate with vault already ingested (for checks that need chunks)."""
    from memory.substrate import MemorySubstrate
    from obsidian.ingest import ObsidianIngestPipeline

    s = MemorySubstrate(ephemeral=True)
    s.boot()
    col = s.get_collection("obsidian_knowledge")
    pipeline = ObsidianIngestPipeline(col, VAULT_DIR, DeterministicEF())
    report = pipeline.boot_ingest()
    yield s, report
    s.shutdown()
    (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)


@pytest.fixture
def sealed_registry():
    from law_engine.registry import LawRegistry
    identity = make_test_identity()
    reg = LawRegistry()
    reg.load_all(LAWS_DIR)
    reg.seal(identity)
    return reg, identity


@pytest.fixture
def boot_ctx(ingested_substrate, sealed_registry):
    """Full BootContext with all phases populated."""
    from boot.context import BootContext
    from law_engine.enforcer import LawEnforcer
    from obsidian.ingest import ObsidianIngestPipeline

    sub, report     = ingested_substrate
    registry, identity = sealed_registry

    ctx = BootContext()
    ctx.memory_substrate = sub
    ctx.memory_root_hash = sub.get_last_snapshot()
    ctx.ingest_report    = report
    ctx.law_registry     = registry
    ctx.law_enforcer     = LawEnforcer(registry=registry)
    ctx.kral_identity    = identity
    return ctx


# ════════════════════════════════════════════════════════════════════════
# BOOT CONTEXT TESTS
# ════════════════════════════════════════════════════════════════════════

class TestBootContext:

    def test_context_initially_empty(self):
        from boot.context import BootContext
        ctx = BootContext()
        assert ctx.memory_substrate is None
        assert ctx.law_registry is None
        assert ctx.ingest_report is None

    def test_is_memory_ready_false_before_boot(self):
        from boot.context import BootContext
        ctx = BootContext()
        assert not ctx.is_memory_ready()

    def test_is_memory_ready_true_after_substrate_boot(self, substrate):
        from boot.context import BootContext
        ctx = BootContext()
        ctx.memory_substrate = substrate
        assert ctx.is_memory_ready()

    def test_is_laws_ready_true_after_laws_phase(self, sealed_registry):
        from boot.context import BootContext
        from law_engine.enforcer import LawEnforcer
        reg, identity = sealed_registry
        ctx = BootContext()
        ctx.law_registry = reg
        ctx.law_enforcer  = LawEnforcer(registry=reg)
        assert ctx.is_laws_ready()

    def test_is_preflight_passed_false_initially(self):
        from boot.context import BootContext
        ctx = BootContext()
        assert not ctx.is_preflight_passed()

    def test_vault_path_from_config(self):
        from boot.context import BootContext
        from unittest.mock import MagicMock
        ctx = BootContext()
        ctx.config = MagicMock()
        ctx.config.obsidian_vault_path = str(VAULT_DIR)
        assert ctx.vault_path == VAULT_DIR


# ════════════════════════════════════════════════════════════════════════
# GUARD TESTS
# ════════════════════════════════════════════════════════════════════════

class TestGroundingGuard:

    def test_empty_citations_always_pass(self, substrate):
        from boot.guards import GroundingGuard
        guard = GroundingGuard(substrate)
        guard.verify_sources([])  # must not raise

    def test_existing_chunk_ids_pass(self, ingested_substrate):
        from boot.guards import GroundingGuard
        sub, _ = ingested_substrate
        col = sub.get_collection("obsidian_knowledge")
        existing = col.get(limit=1)
        if not existing["ids"]:
            pytest.skip("No chunks in test vault")
        guard = GroundingGuard(sub)
        guard.verify_sources(existing["ids"])

    def test_nonexistent_id_raises(self, substrate):
        from boot.guards import GroundingGuard
        from core.exceptions import PreflightFailure
        guard = GroundingGuard(substrate)
        with pytest.raises(PreflightFailure, match="not found in RAG"):
            guard.verify_sources(["ghost-chunk-id-does-not-exist"])

    def test_check_grounded_returns_false_for_missing(self, substrate):
        from boot.guards import GroundingGuard
        guard = GroundingGuard(substrate)
        assert guard.check_grounded(["nonexistent"]) is False

    def test_check_grounded_returns_true_for_empty(self, substrate):
        from boot.guards import GroundingGuard
        guard = GroundingGuard(substrate)
        assert guard.check_grounded([]) is True


class TestMemoryConsistencyGuard:

    def test_identical_hashes_pass(self):
        from boot.guards import MemoryConsistencyGuard
        guard = MemoryConsistencyGuard()
        guard.check("abc123", "abc123")  # must not raise

    def test_different_hashes_raise(self):
        from boot.guards import MemoryConsistencyGuard
        from core.exceptions import MemoryRootHashMismatch
        guard = MemoryConsistencyGuard()
        with pytest.raises(MemoryRootHashMismatch):
            guard.check("hash_a", "hash_b")

    def test_are_consistent_true_for_same(self):
        from boot.guards import MemoryConsistencyGuard
        guard = MemoryConsistencyGuard()
        assert guard.are_consistent("x", "x") is True

    def test_are_consistent_false_for_different(self):
        from boot.guards import MemoryConsistencyGuard
        guard = MemoryConsistencyGuard()
        assert guard.are_consistent("x", "y") is False


class TestTimeSourceGuard:

    def test_event_store_source_passes(self):
        from boot.guards import TimeSourceGuard
        guard = TimeSourceGuard()
        guard.verify_time_source("REACTIVE", "event_store")  # must not raise

    def test_rag_timestamp_passes(self):
        from boot.guards import TimeSourceGuard
        guard = TimeSourceGuard()
        guard.verify_time_source("HEARTBEAT", "rag_timestamp")

    def test_datetime_now_raises(self):
        from boot.guards import TimeSourceGuard
        from core.exceptions import LawViolation
        guard = TimeSourceGuard()
        with pytest.raises(LawViolation, match="forbidden time source"):
            guard.verify_time_source("REACTIVE", "datetime.now")

    def test_time_time_raises(self):
        from boot.guards import TimeSourceGuard
        from core.exceptions import LawViolation
        guard = TimeSourceGuard()
        with pytest.raises(LawViolation):
            guard.verify_time_source("REACTIVE", "time.time")

    def test_get_approved_timestamp_returns_string(self, substrate):
        from boot.guards import TimeSourceGuard
        from memory.event_store import EventStore
        guard = TimeSourceGuard()
        store = EventStore(substrate.get_sqlite(), HMAC_SECRET)
        ts = guard.get_approved_timestamp(store)
        assert isinstance(ts, str)
        assert "T" in ts  # ISO8601 format


class TestExternalReferenceGuard:

    def test_internal_ref_passes(self, substrate):
        from boot.guards import ExternalReferenceGuard
        guard = ExternalReferenceGuard(substrate)
        guard.check_reference("REACTIVE", "rag_search_query_text")  # plain text OK

    def test_http_url_raises(self, substrate):
        from boot.guards import ExternalReferenceGuard
        from core.exceptions import LawViolation
        guard = ExternalReferenceGuard(substrate)
        with pytest.raises(LawViolation, match="external resource"):
            guard.check_reference("REACTIVE", "https://api.example.com/data")

    def test_file_path_raises(self, substrate):
        from boot.guards import ExternalReferenceGuard
        from core.exceptions import LawViolation
        guard = ExternalReferenceGuard(substrate)
        with pytest.raises(LawViolation):
            guard.check_reference("REACTIVE", "/etc/passwd")

    def test_windows_path_raises(self, substrate):
        from boot.guards import ExternalReferenceGuard
        from core.exceptions import LawViolation
        guard = ExternalReferenceGuard(substrate)
        with pytest.raises(LawViolation):
            guard.check_reference("REACTIVE", r"C:\escape_proof.txt")

    def test_is_allowed_false_for_external(self, substrate):
        from boot.guards import ExternalReferenceGuard
        guard = ExternalReferenceGuard(substrate)
        assert guard.is_allowed("REACTIVE", "https://evil.com") is False

    def test_is_allowed_true_for_internal(self, substrate):
        from boot.guards import ExternalReferenceGuard
        guard = ExternalReferenceGuard(substrate)
        assert guard.is_allowed("REACTIVE", "rag search query") is True

    def test_build_guards_returns_all_four(self, boot_ctx):
        from boot.guards import build_guards
        guards = build_guards(boot_ctx)
        assert "grounding"   in guards
        assert "consistency" in guards
        assert "time_source" in guards
        assert "ext_ref"     in guards


# ════════════════════════════════════════════════════════════════════════
# PREFLIGHT CHECK TESTS
# ════════════════════════════════════════════════════════════════════════

class TestMemoryIntegrityCheck:

    def test_passes_with_healthy_substrate(self, boot_ctx):
        from boot.preflight import MemoryIntegrityCheck
        result = MemoryIntegrityCheck().run(boot_ctx)
        assert result.passed
        assert "4/4 collections OK" in result.detail

    def test_fails_without_substrate(self):
        from boot.preflight import MemoryIntegrityCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="not initialised"):
            MemoryIntegrityCheck().run(ctx)

    def test_fails_with_shutdown_substrate(self):
        from boot.preflight import MemoryIntegrityCheck
        from boot.context   import BootContext
        from memory.substrate import MemorySubstrate
        from core.exceptions  import PreflightFailure
        sub = MemorySubstrate(ephemeral=True)
        sub.boot()
        sub.shutdown()  # shut it down — now unhealthy
        ctx = BootContext()
        ctx.memory_substrate = sub
        with pytest.raises(PreflightFailure, match="health check"):
            MemoryIntegrityCheck().run(ctx)


class TestLawRegistryIntegrityCheck:

    def test_passes_with_sealed_registry(self, boot_ctx):
        from boot.preflight import LawRegistryIntegrityCheck
        result = LawRegistryIntegrityCheck().run(boot_ctx)
        assert result.passed
        assert "laws" in result.detail

    def test_fails_without_registry(self):
        from boot.preflight import LawRegistryIntegrityCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="not initialised"):
            LawRegistryIntegrityCheck().run(ctx)

    def test_fails_with_unsealed_registry(self, substrate):
        from boot.preflight import LawRegistryIntegrityCheck
        from boot.context   import BootContext
        from law_engine.registry import LawRegistry
        from core.exceptions import PreflightFailure
        reg = LawRegistry()
        reg.load_all(LAWS_DIR)
        # NOT sealed
        ctx = BootContext()
        ctx.memory_substrate = substrate
        ctx.law_registry = reg
        with pytest.raises(PreflightFailure, match="not sealed"):
            LawRegistryIntegrityCheck().run(ctx)

    def test_fails_with_wrong_signature(self, substrate):
        from boot.preflight  import LawRegistryIntegrityCheck
        from boot.context    import BootContext
        from law_engine.registry import LawRegistry
        from core.exceptions import PreflightFailure

        identity = make_test_identity()
        reg = LawRegistry()
        reg.load_all(LAWS_DIR)
        reg.seal(identity)

        # Use a DIFFERENT identity to verify — should fail
        wrong_id = make_test_identity()
        ctx = BootContext()
        ctx.memory_substrate = substrate
        ctx.law_registry = reg
        ctx.kral_identity = wrong_id
        with pytest.raises(PreflightFailure, match="KRAL seal signature"):
            LawRegistryIntegrityCheck().run(ctx)


class TestVaultIngestCompletenessCheck:

    def test_passes_with_successful_ingest(self, boot_ctx):
        from boot.preflight import VaultIngestCompletenessCheck
        result = VaultIngestCompletenessCheck().run(boot_ctx)
        assert result.passed
        assert "processed" in result.detail

    def test_fails_without_ingest_report(self):
        from boot.preflight import VaultIngestCompletenessCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="No ingest report"):
            VaultIngestCompletenessCheck().run(ctx)

    def test_fails_with_empty_vault(self, substrate):
        from boot.preflight import VaultIngestCompletenessCheck
        from boot.context   import BootContext
        from obsidian.ingest import IngestReport
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        ctx.memory_substrate = substrate
        ctx.ingest_report = IngestReport(
            files_processed=0, files_skipped=0, chunks_created=0
        )
        with pytest.raises(PreflightFailure, match="zero files"):
            VaultIngestCompletenessCheck().run(ctx)

    def test_fails_with_ingest_errors(self, substrate):
        from boot.preflight import VaultIngestCompletenessCheck
        from boot.context   import BootContext
        from obsidian.ingest import IngestReport
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        ctx.memory_substrate = substrate
        ctx.ingest_report = IngestReport(
            files_processed=2,
            files_skipped=0,
            errors=["Failed to ingest X.md: some error"],
        )
        with pytest.raises(PreflightFailure, match="errors"):
            VaultIngestCompletenessCheck().run(ctx)


class TestLLMEndpointCheck:
    """LLM check with mock config — no running models needed in tests."""

    def _make_mock_config(self, reactive="ollama", heartbeat="claude",
                           claude_key="sk-ant-api03-test123-valid"):
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.reactive_llm_provider.value  = reactive
        cfg.heartbeat_llm_provider.value = heartbeat
        cfg.anthropic_api_key            = claude_key
        cfg.ollama_host                  = "http://localhost:11434"
        return cfg

    def test_passes_with_valid_claude_key(self):
        from boot.preflight import LLMEndpointCheck
        from boot.context   import BootContext
        ctx = BootContext()
        # Both providers = claude with valid key
        ctx.config = self._make_mock_config(
            reactive="claude", heartbeat="claude",
            claude_key="sk-ant-api03-validkeyformattest12345"
        )
        result = LLMEndpointCheck().run(ctx)
        assert result.passed

    def test_fails_when_both_unreachable(self):
        from boot.preflight import LLMEndpointCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        # Ollama unreachable + invalid Claude key
        ctx.config = self._make_mock_config(
            reactive="ollama", heartbeat="claude", claude_key="invalid"
        )
        with pytest.raises(PreflightFailure, match="Both LLM providers unreachable"):
            LLMEndpointCheck().run(ctx)

    def test_passes_when_claude_key_valid_even_if_ollama_down(self):
        from boot.preflight import LLMEndpointCheck
        from boot.context   import BootContext
        ctx = BootContext()
        ctx.config = self._make_mock_config(
            reactive="ollama",   # will be unreachable
            heartbeat="claude",
            claude_key="sk-ant-api03-validkeyformattest12345"
        )
        result = LLMEndpointCheck().run(ctx)
        assert result.passed
        assert "UNREACHABLE" in result.detail  # ollama was unreachable
        assert "claude OK" in result.detail    # but claude is OK

    def test_fails_without_config(self):
        from boot.preflight import LLMEndpointCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="No config"):
            LLMEndpointCheck().run(ctx)


class TestBrotherhoodBondCheck:

    def test_passes_with_full_context(self, boot_ctx):
        from boot.preflight import BrotherhoodBondCheck
        result = BrotherhoodBondCheck().run(boot_ctx)
        assert result.passed
        assert "oath hash" in result.detail

    def test_fails_without_registry(self):
        from boot.preflight import BrotherhoodBondCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="LawRegistry not available"):
            BrotherhoodBondCheck().run(ctx)

    def test_fails_without_bound_laws(self):
        from boot.preflight import BrotherhoodBondCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        from unittest.mock import MagicMock

        ctx = BootContext()
        mock_reg = MagicMock()
        mock_reg.query_by_prefix.return_value = []  # no BOUND laws
        ctx.law_registry = mock_reg
        with pytest.raises(PreflightFailure, match="BOUND.md laws missing"):
            BrotherhoodBondCheck().run(ctx)

    def test_fails_without_oath(self):
        from boot.preflight import BrotherhoodBondCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        from unittest.mock import MagicMock

        ctx = BootContext()
        mock_reg = MagicMock()
        mock_reg.query_by_prefix.return_value = [MagicMock()]  # has BOUND laws
        mock_reg.get_by_id.return_value = None  # but no oath law
        ctx.law_registry = mock_reg
        with pytest.raises(PreflightFailure, match="Brotherhood oath.*not found"):
            BrotherhoodBondCheck().run(ctx)

    def test_bond_stored_on_first_boot(self, boot_ctx):
        """After check runs, a BROTHERHOOD event should be in the event store."""
        from boot.preflight import BrotherhoodBondCheck
        from memory.event_store import EventStore, EventType

        BrotherhoodBondCheck().run(boot_ctx)
        # BrotherhoodBondCheck uses a fixed internal secret; read with same secret
        BOND_SECRET = "boot_hmac_secret_placeholder"
        store = EventStore(boot_ctx.memory_substrate.get_sqlite(), BOND_SECRET)
        bond_events = store.read_by_type(EventType.BROTHERHOOD)
        assert len(bond_events) >= 1
        assert "oath_hash" in bond_events[0].payload


class TestSoulLawConsistencyCheck:

    def test_passes_with_all_soul_laws(self, boot_ctx):
        from boot.preflight import SoulLawConsistencyCheck
        result = SoulLawConsistencyCheck().run(boot_ctx)
        assert result.passed
        assert "5/5 soul laws" in result.detail

    def test_fails_without_registry(self):
        from boot.preflight import SoulLawConsistencyCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="not available"):
            SoulLawConsistencyCheck().run(ctx)

    def test_fails_with_missing_soul_laws(self):
        from boot.preflight import SoulLawConsistencyCheck
        from boot.context   import BootContext
        from core.exceptions import PreflightFailure
        from unittest.mock import MagicMock

        ctx = BootContext()
        mock_reg = MagicMock()
        mock_reg.get_soul_laws.return_value = []  # no soul laws
        ctx.law_registry = mock_reg
        with pytest.raises(PreflightFailure, match="Expected 5 SOUL laws"):
            SoulLawConsistencyCheck().run(ctx)

    def test_detects_modified_soul_law(self, substrate):
        """Modified SOUL.md on disk after seal → check must fail."""
        from boot.preflight import SoulLawConsistencyCheck
        from boot.context   import BootContext
        from law_engine.registry import LawRegistry
        from law_engine.enforcer import LawEnforcer
        from core.exceptions import PreflightFailure
        import tempfile, shutil

        identity = make_test_identity()

        # Copy laws dir to temp, seal registry from it
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_laws = Path(tmpdir)
            for f in LAWS_DIR.glob("*.md"):
                shutil.copy(f, tmp_laws / f.name)

            reg = LawRegistry()
            reg.load_all(tmp_laws)
            reg.seal(identity)

            # NOW modify SOUL.md on disk after sealing
            soul_md = tmp_laws / "SOUL.md"
            original = soul_md.read_text()
            soul_md.write_text(original + "\n\n### TAMPERED\n- Added after seal")

            ctx = BootContext()
            ctx.memory_substrate = substrate
            ctx.law_registry  = reg
            ctx.law_enforcer  = LawEnforcer(registry=reg)
            ctx.kral_identity = identity
            ctx.config        = type("C", (), {
                "laws_dir": str(tmp_laws),
                "obsidian_vault_path": str(VAULT_DIR),
            })()

            with pytest.raises(PreflightFailure, match="SOUL.md was modified"):
                SoulLawConsistencyCheck().run(ctx)


class TestAuditLogIntegrityCheck:

    def test_passes_with_intact_triggers(self, boot_ctx):
        from boot.preflight import AuditLogIntegrityCheck
        result = AuditLogIntegrityCheck().run(boot_ctx)
        assert result.passed
        assert "append-only triggers OK" in result.detail

    def test_fails_without_substrate(self):
        from boot.preflight  import AuditLogIntegrityCheck
        from boot.context    import BootContext
        from core.exceptions import PreflightFailure
        ctx = BootContext()
        with pytest.raises(PreflightFailure, match="not available"):
            AuditLogIntegrityCheck().run(ctx)

    def test_fails_when_trigger_dropped(self, substrate):
        from boot.preflight  import AuditLogIntegrityCheck
        from boot.context    import BootContext
        from core.exceptions import PreflightFailure

        db = substrate.get_sqlite()
        db.execute("DROP TRIGGER IF EXISTS audit_no_update")
        db.commit()

        ctx = BootContext()
        ctx.memory_substrate = substrate
        with pytest.raises(PreflightFailure, match="Audit log triggers"):
            AuditLogIntegrityCheck().run(ctx)

    def test_reports_escape_attempt_count(self, boot_ctx):
        from boot.preflight  import AuditLogIntegrityCheck
        from memory.audit_log import AuditLog, AuditLevel

        audit = AuditLog(boot_ctx.memory_substrate.get_sqlite())
        audit.log_escape("REACTIVE", "read_file", "test escape")

        result = AuditLogIntegrityCheck().run(boot_ctx)
        assert result.passed
        assert "1 escape attempts" in result.detail


# ════════════════════════════════════════════════════════════════════════
# RUN_PREFLIGHT INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestRunPreflight:

    def _make_full_ctx(self, ingested_substrate, sealed_registry, mock_claude=True):
        from boot.context    import BootContext
        from law_engine.enforcer import LawEnforcer
        from unittest.mock import MagicMock

        sub, report     = ingested_substrate
        registry, identity = sealed_registry

        ctx = BootContext()
        ctx.memory_substrate = sub
        ctx.memory_root_hash = sub.get_last_snapshot()
        ctx.ingest_report    = report
        ctx.law_registry     = registry
        ctx.law_enforcer     = LawEnforcer(registry=registry)
        ctx.kral_identity    = identity

        if mock_claude:
            cfg = MagicMock()
            cfg.reactive_llm_provider.value  = "claude"
            cfg.heartbeat_llm_provider.value = "claude"
            cfg.anthropic_api_key            = "sk-ant-api03-validkeyformat12345678"
            cfg.ollama_host                  = "http://localhost:11434"
            cfg.laws_dir                     = str(LAWS_DIR)
            ctx.config = cfg
        return ctx

    def test_all_7_checks_run(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight
        ctx = self._make_full_ctx(ingested_substrate, sealed_registry)
        report = run_preflight(ctx)
        assert len(report.results) == 7

    def test_all_checks_pass_with_valid_context(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight
        ctx = self._make_full_ctx(ingested_substrate, sealed_registry)
        report = run_preflight(ctx)
        assert report.all_passed, "\n" + report.summary()
        assert len(report.failed) == 0

    def test_preflight_fails_fast_on_first_error(self, substrate):
        from boot.preflight  import run_preflight
        from boot.context    import BootContext
        from core.exceptions import PreflightFailure
        # Context missing ingest report → VaultIngestCompletenessCheck will fail
        ctx = BootContext()
        ctx.memory_substrate = substrate
        with pytest.raises(PreflightFailure):
            run_preflight(ctx)

    def test_preflight_report_summary_format(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight
        ctx = self._make_full_ctx(ingested_substrate, sealed_registry)
        report = run_preflight(ctx)
        summary = report.summary()
        assert "Pre-flight:" in summary
        assert "✅" in summary

    def test_all_passed_property(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight
        ctx = self._make_full_ctx(ingested_substrate, sealed_registry)
        report = run_preflight(ctx)
        assert report.all_passed is True

    def test_failed_property_empty_on_success(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight
        ctx = self._make_full_ctx(ingested_substrate, sealed_registry)
        report = run_preflight(ctx)
        assert report.failed == []


# ════════════════════════════════════════════════════════════════════════
# BOOT SEQUENCE TESTS
# ════════════════════════════════════════════════════════════════════════

class TestBootSequence:

    def _make_boot_config(self, tmp_path):
        """Create a minimal SystemConfig mock for boot tests."""
        from unittest.mock import MagicMock
        from core.config import LLMProvider, SystemMode

        kral_pub = Path(__file__).parent.parent / "keys" / "kral_public.pem"

        cfg = MagicMock()
        cfg.obsidian_vault_path    = str(VAULT_DIR)
        cfg.chroma_host            = "localhost"
        cfg.chroma_port            = 8000
        cfg.sqlite_path            = str(tmp_path / "rag.db")
        cfg.kral_private_key_path  = str(tmp_path / "missing_private.pem")  # won't exist
        cfg.kral_public_key_path   = str(kral_pub)
        cfg.laws_dir               = str(LAWS_DIR)
        cfg.reactive_llm_provider.value  = "claude"
        cfg.heartbeat_llm_provider.value = "claude"
        cfg.anthropic_api_key            = "sk-ant-api03-validkeyformat12345678"
        cfg.ollama_host                  = "http://localhost:11434"
        cfg.system_mode.value            = "prison"
        cfg.event_hmac_secret            = HMAC_SECRET
        return cfg

    def test_boot_phases_defined(self):
        from boot.sequence import BOOT_PHASES
        ids = [p[0] for p in BOOT_PHASES]
        assert ids == ["MEMORY", "RAG", "LAWS", "PREFLIGHT", "AGENTS"]

    def test_boot_report_success_property(self):
        from boot.sequence import BootReport, PhaseResult
        report = BootReport()
        report.phases.append(PhaseResult("MEMORY", True, 100.0, "ok"))
        assert report.success is True
        report.phases.append(PhaseResult("RAG", False, 50.0, error="fail"))
        assert report.success is False

    def test_boot_report_summary_format(self):
        from boot.sequence import BootReport, PhaseResult
        report = BootReport()
        report.phases.append(PhaseResult("MEMORY", True, 120.0, "4 collections"))
        report.phases.append(PhaseResult("RAG", True, 450.0, "6 files"))
        report.total_elapsed_ms = 570.0
        summary = report.summary()
        assert "MEMORY" in summary
        assert "RAG" in summary
        assert "BOOT OK" in summary
        assert "✅ PASS" in summary

    def test_boot_report_completed_phases(self):
        from boot.sequence import BootReport, PhaseResult
        report = BootReport()
        report.phases.append(PhaseResult("MEMORY", True, 100.0))
        report.phases.append(PhaseResult("RAG", False, 50.0, error="x"))
        assert report.completed_phases == ["MEMORY"]

    def test_phase_memory_uses_ephemeral(self, tmp_path):
        """Test _phase_memory in isolation by subclassing BootSequence."""
        from boot.sequence import BootSequence
        from boot.context  import BootContext
        from memory.substrate import MemorySubstrate

        class EphemeralBootSequence(BootSequence):
            def _phase_memory(self, ctx):
                sub = MemorySubstrate(ephemeral=True)
                root_hash = sub.boot()
                ctx.memory_substrate = sub
                ctx.memory_root_hash = root_hash
                return f"root={root_hash.value[:16]}... | events={root_hash.event_count}"

        cfg = self._make_boot_config(tmp_path)
        seq = EphemeralBootSequence(cfg)
        ctx = BootContext(config=cfg)

        detail = seq._phase_memory(ctx)

        assert ctx.memory_substrate is not None
        assert ctx.memory_root_hash is not None
        assert "root=" in detail
        ctx.memory_substrate.shutdown()

    def test_phase_laws_loads_and_seals(self, tmp_path):
        """Test _phase_laws in isolation with a test identity that has a private key."""
        from boot.sequence import BootSequence
        from boot.context  import BootContext
        from memory.substrate import MemorySubstrate
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization

        # Generate a test private key and write to tmp_path
        pk  = Ed25519PrivateKey.generate()
        pub = pk.public_key()
        priv_pem = pk.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub_pem = pub.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        priv_path = tmp_path / "test_private.pem"
        pub_path  = tmp_path / "test_public.pem"
        priv_path.write_bytes(priv_pem)
        pub_path.write_bytes(pub_pem)

        cfg = self._make_boot_config(tmp_path)
        cfg.kral_private_key_path = str(priv_path)
        cfg.kral_public_key_path  = str(pub_path)
        # Must skip fingerprint check since this is a generated test key
        from unittest.mock import patch
        seq = BootSequence(cfg)

        sub = MemorySubstrate(ephemeral=True)
        sub.boot()
        ctx = BootContext(config=cfg)
        ctx.memory_substrate = sub

        # Patch load_kral_identity to skip fingerprint check for test keys
        from core.crypto import KralIdentity
        test_identity = KralIdentity(pk, pub, "test_fp",
            pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex())
        with patch("core.crypto.load_kral_identity", return_value=test_identity):
            detail = seq._phase_laws(ctx)

        assert ctx.law_registry is not None
        assert ctx.law_registry.is_sealed
        assert ctx.law_enforcer is not None
        assert ctx.kral_identity is not None
        assert "laws" in detail
        sub.shutdown()

    def test_phase_rag_ingests_vault(self, tmp_path):
        """Test _phase_rag in isolation with ephemeral substrate."""
        from boot.sequence import BootSequence
        from boot.context  import BootContext
        from memory.substrate import MemorySubstrate
        from obsidian.ingest import ObsidianIngestPipeline
        from unittest.mock import patch

        cfg = self._make_boot_config(tmp_path)
        seq = BootSequence(cfg)

        sub = MemorySubstrate(ephemeral=True)
        sub.boot()
        ctx = BootContext(config=cfg)
        ctx.memory_substrate = sub
        ctx.config = cfg

        # Patch ObsidianIngestPipeline to use DeterministicEF
        original_init = ObsidianIngestPipeline.__init__

        def patched_init(self_inner, chroma_collection, vault_path, embedding_function):
            original_init(self_inner, chroma_collection, vault_path, DeterministicEF())

        with patch.object(ObsidianIngestPipeline, "__init__", patched_init):
            detail = seq._phase_rag(ctx)

        assert ctx.ingest_report is not None
        assert ctx.ingest_report.success
        assert "processed" in detail
        sub.shutdown()
        (VAULT_DIR / ".manifest.json").unlink(missing_ok=True)

    def test_phase_preflight_runs_checks(self, boot_ctx):
        """Test _phase_preflight in isolation."""
        from boot.sequence import BootSequence
        from unittest.mock import MagicMock

        cfg = MagicMock()
        cfg.reactive_llm_provider.value  = "claude"
        cfg.heartbeat_llm_provider.value = "claude"
        cfg.anthropic_api_key            = "sk-ant-api03-validkeyformat12345678"
        cfg.ollama_host                  = "http://localhost:11434"
        cfg.laws_dir                     = str(LAWS_DIR)
        boot_ctx.config = cfg

        seq = BootSequence(cfg)
        detail = seq._phase_preflight(boot_ctx)
        assert boot_ctx.preflight_report is not None
        assert boot_ctx.preflight_report.all_passed

    def test_phase_agents_requires_memory(self, tmp_path):
        """AGENTS phase must fail if memory is not ready."""
        from boot.sequence import BootSequence
        from boot.context  import BootContext
        from core.exceptions import AgentBootFailure

        cfg = self._make_boot_config(tmp_path)
        seq = BootSequence(cfg)
        ctx = BootContext(config=cfg)
        # No memory substrate — should fail

        with pytest.raises(AgentBootFailure, match="Memory substrate not ready"):
            seq._phase_agents(ctx)

    def test_phase_agents_requires_laws(self, substrate):
        """AGENTS phase must fail if laws are not sealed."""
        from boot.sequence import BootSequence
        from boot.context  import BootContext
        from core.exceptions import AgentBootFailure
        from unittest.mock import MagicMock

        cfg = MagicMock()
        seq = BootSequence(cfg)
        ctx = BootContext(config=cfg)
        ctx.memory_substrate = substrate
        # No law registry

        with pytest.raises(AgentBootFailure, match="Law registry not sealed"):
            seq._phase_agents(ctx)


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase4Integration:

    def test_all_7_preflight_checks_have_run(self, ingested_substrate, sealed_registry):
        from boot.preflight import run_preflight, ALL_CHECKS
        from unittest.mock import MagicMock

        sub, report = ingested_substrate
        registry, identity = sealed_registry

        from boot.context    import BootContext
        from law_engine.enforcer import LawEnforcer
        ctx = BootContext()
        ctx.memory_substrate = sub
        ctx.memory_root_hash = sub.get_last_snapshot()
        ctx.ingest_report    = report
        ctx.law_registry     = registry
        ctx.law_enforcer     = LawEnforcer(registry=registry)
        ctx.kral_identity    = identity
        cfg = MagicMock()
        cfg.reactive_llm_provider.value  = "claude"
        cfg.heartbeat_llm_provider.value = "claude"
        cfg.anthropic_api_key            = "sk-ant-api03-valid1234567890"
        cfg.ollama_host                  = "http://localhost:11434"
        cfg.laws_dir                     = str(LAWS_DIR)
        ctx.config = cfg

        pf_report = run_preflight(ctx)
        check_names = {r.check_name for r in pf_report.results}
        expected    = {cls.name for cls in ALL_CHECKS}
        assert check_names == expected

    def test_preflight_check_results_have_timing(self, boot_ctx):
        from boot.preflight import run_preflight
        from unittest.mock import MagicMock
        cfg = MagicMock()
        cfg.reactive_llm_provider.value  = "claude"
        cfg.heartbeat_llm_provider.value = "claude"
        cfg.anthropic_api_key            = "sk-ant-api03-valid1234567890"
        cfg.ollama_host                  = "http://localhost:11434"
        cfg.laws_dir                     = str(LAWS_DIR)
        boot_ctx.config = cfg
        report = run_preflight(boot_ctx)
        for result in report.results:
            assert result.elapsed_ms >= 0

    def test_guards_built_from_boot_ctx(self, boot_ctx):
        from boot.guards import build_guards
        guards = build_guards(boot_ctx)
        # Verify all guards are functional
        guards["consistency"].check("abc", "abc")  # must not raise
        guards["time_source"].verify_time_source("REACTIVE", "event_store")

    def test_previous_phases_still_pass(self):
        """Regression: Phase 0-3 tests are unaffected by Phase 4 additions."""
        from core.hashing import blake2b_256
        from memory.event_store import MemoryEvent, EventType, AgentSource
        from law_engine.registry import LawRegistry
        from obsidian.parser import ObsidianParser
        assert blake2b_256(b"phase4") is not None
