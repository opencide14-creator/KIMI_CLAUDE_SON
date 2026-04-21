"""
tests/test_phase0_core.py — Phase 0 acceptance tests.

These tests verify the skeleton is complete and all contracts are in place.
ALL tests in this file must pass at the end of Phase 0.
No implementation required — testing imports, ABCs, and contract shapes.
"""
import importlib
import inspect
import pytest
from pathlib import Path


# ── Import tests ──────────────────────────────────────────────────────────────

def test_core_imports():
    """All core modules importable."""
    from core import exceptions, config, hashing, crypto  # noqa: F401

def test_all_exception_types_importable():
    from core.exceptions import (
        VekilKaanError,
        BootFailure, MemoryBootFailure, RAGBootFailure,
        LawEnforcementBootFailure, PreflightFailure, AgentBootFailure,
        MemoryIntegrityError, MemoryRootHashMismatch, EventSignatureInvalid,
        CryptoError, KeyLoadError, SignatureVerificationFailed,
        BindingMismatch, FingerprintMismatch,
        LawViolation, SoulLawViolation, BrotherhoodViolation,
        SimulationDetected, LawRegistryTampered,
        AgentError, AgentDesyncError, HeartbeatMissing, PulseMissing,
        ToolError, EscapeAttemptDetected, ToolNotFound, ToolCallDenied,
        LLMError, LLMUnavailable,
        ObsidianError, VaultNotFound,
    )

def test_exception_hierarchy():
    from core.exceptions import (
        BootFailure, VekilKaanError,
        MemoryBootFailure, RAGBootFailure, LawEnforcementBootFailure,
        PreflightFailure, AgentBootFailure,
    )
    assert issubclass(BootFailure, VekilKaanError)
    assert issubclass(MemoryBootFailure, BootFailure)
    assert issubclass(RAGBootFailure, BootFailure)
    assert issubclass(LawEnforcementBootFailure, BootFailure)
    assert issubclass(PreflightFailure, BootFailure)
    assert issubclass(AgentBootFailure, BootFailure)

def test_escape_attempt_carries_context():
    from core.exceptions import EscapeAttemptDetected
    e = EscapeAttemptDetected(agent="REACTIVE", tool="read_file", detail="path traversal")
    assert e.agent == "REACTIVE"
    assert e.tool == "read_file"
    assert "REACTIVE" in str(e)


# ── Hashing tests ─────────────────────────────────────────────────────────────

def test_blake2b_256_matches_hashlib():
    """Core guarantee: our blake2b_256 == Python hashlib.blake2b(data, digest_size=32)."""
    import hashlib
    from core.hashing import blake2b_256
    data = b"KRAL binding test vector"
    expected = hashlib.blake2b(data, digest_size=32).digest()
    assert blake2b_256(data) == expected

def test_blake2b_256_empty():
    import hashlib
    from core.hashing import blake2b_256
    expected = hashlib.blake2b(b"", digest_size=32).digest()
    assert blake2b_256(b"") == expected

def test_blake2b_256_hex_is_64_chars():
    from core.hashing import blake2b_256_hex
    result = blake2b_256_hex(b"test")
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)

def test_blake2b_256_keyed():
    import hashlib
    from core.hashing import blake2b_256_keyed
    data = b"agent event payload"
    key  = b"secret_key_32bytes_padded_______"  # 32 bytes
    expected = hashlib.blake2b(data, digest_size=32, key=key).digest()
    assert blake2b_256_keyed(data, key) == expected

def test_blake2b_keyed_invalid_key_length():
    from core.hashing import blake2b_256_keyed
    with pytest.raises(ValueError, match="1-64 bytes"):
        blake2b_256_keyed(b"data", b"")  # key too short

def test_guardian_binding_correct_length():
    """Binding input must be exactly 70 bytes (matching C struct layout)."""
    from core.hashing import compute_guardian_binding
    result = compute_guardian_binding(
        version=1, counter=42, timestamp=1772167812,
        g1=0.1, g2=0.2, g3=0.3, g4=0.4,
        tessa_id=3, kappa_q=58000,
        chaos=(0.11, 0.22, 0.33, 0.44),
        from_v=1, to_v=5,
        domain=b"KRAL",
        flags=1, prev_crc=0xDEADBEEF, chaos_seed=0xCAFEBABEDEAD0001,
    )
    assert len(result) == 32  # blake2b_256 output

def test_guardian_binding_deterministic():
    from core.hashing import compute_guardian_binding
    kwargs = dict(
        version=1, counter=1, timestamp=1000,
        g1=1.0, g2=2.0, g3=3.0, g4=4.0,
        tessa_id=1, kappa_q=100,
        chaos=(0.1, 0.2, 0.3, 0.4),
        from_v=0, to_v=1,
        domain=b"KRAL",
        flags=0, prev_crc=0, chaos_seed=0,
    )
    assert compute_guardian_binding(**kwargs) == compute_guardian_binding(**kwargs)

def test_sha256_file(tmp_path):
    from core.hashing import sha256_file
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    result = sha256_file(f)
    import hashlib
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert result == expected

def test_compute_root_hash_deterministic():
    from core.hashing import compute_root_hash
    stats = {"obsidian_knowledge": {"count": 42}, "agent_events": {"count": 7}}
    events = ["evt-001", "evt-002", "evt-003"]
    h1 = compute_root_hash(stats, events)
    h2 = compute_root_hash(stats, events)
    assert h1 == h2
    assert len(h1) == 64

def test_compute_root_hash_event_order_independent():
    """Root hash must be the same regardless of event list order."""
    from core.hashing import compute_root_hash
    stats = {}
    events_a = ["evt-003", "evt-001", "evt-002"]
    events_b = ["evt-001", "evt-002", "evt-003"]
    assert compute_root_hash(stats, events_a) == compute_root_hash(stats, events_b)


# ── Crypto module structure tests ─────────────────────────────────────────────

def test_kral_constants_present():
    from core.crypto import KRAL_EXPECTED_FINGERPRINT, KRAL_PUBLIC_KEY_HEX
    assert KRAL_EXPECTED_FINGERPRINT == "629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52"
    assert KRAL_PUBLIC_KEY_HEX == "7f276ad75d301a05e61f90d4423ed75118c39f40a9ae4bb1f11523cf39855bf1"
    assert len(KRAL_EXPECTED_FINGERPRINT) == 64
    assert len(KRAL_PUBLIC_KEY_HEX) == 64

def test_hmac_sign_and_verify():
    from core.crypto import hmac_sign, hmac_verify
    secret = "test_secret_that_is_long_enough_32chars"
    data   = b'{"event_id": "abc", "type": "PULSE_H"}'
    sig    = hmac_sign(secret, data)
    assert len(sig) == 64  # SHA-256 hex = 64 chars
    hmac_verify(secret, data, sig)  # must not raise

def test_hmac_verify_wrong_data_raises():
    from core.crypto import hmac_sign, hmac_verify
    from core.exceptions import EventSignatureInvalid
    secret = "test_secret_that_is_long_enough_32chars"
    data   = b"original data"
    sig    = hmac_sign(secret, data)
    with pytest.raises(EventSignatureInvalid):
        hmac_verify(secret, b"tampered data", sig)

def test_hmac_verify_wrong_secret_raises():
    from core.crypto import hmac_sign, hmac_verify
    from core.exceptions import EventSignatureInvalid
    data = b"event payload"
    sig  = hmac_sign("secret_a_long_enough_secret_32ch", data)
    with pytest.raises(EventSignatureInvalid):
        hmac_verify("secret_b_long_enough_secret_32ch", data, sig)

def test_kral_public_key_load(tmp_path):
    """Public key from kral_public.pem loads and fingerprint matches KRAL identity."""
    from core.crypto import load_kral_identity
    from core.exceptions import KeyLoadError
    # Use the actual public key content from kral_public.pem
    pub_pem = (
        b"-----BEGIN PUBLIC KEY-----\n"
        b"MCowBQYDK2VwAyEAfydq110wGgXmH5DUQj7XURjDn0Cprkux8RUjzzmFW/E=\n"
        b"-----END PUBLIC KEY-----\n"
    )
    pub_file = tmp_path / "kral_public.pem"
    pub_file.write_bytes(pub_pem)

    # Private key path intentionally non-existent — verify-only mode
    priv_file = tmp_path / "kral_private_NOT_PRESENT.pem"

    identity = load_kral_identity(priv_file, pub_file, verify_fingerprint=True)
    assert identity.public_key_hex == "7f276ad75d301a05e61f90d4423ed75118c39f40a9ae4bb1f11523cf39855bf1"
    assert identity.fingerprint == "629c3bc42d7c99f1c62972aa148c02bad7a70d034ffd6735ef369c300bd57c52"
    assert not identity.has_private_key()  # only public loaded

def test_kral_wrong_public_key_rejected(tmp_path):
    """A different Ed25519 public key must be rejected by fingerprint check."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from core.crypto import load_kral_identity
    from core.exceptions import FingerprintMismatch

    # Generate a fresh key pair — definitely not KRAL
    other_key = Ed25519PrivateKey.generate().public_key()
    other_pub_pem = other_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_file = tmp_path / "other_public.pem"
    pub_file.write_bytes(other_pub_pem)

    with pytest.raises(FingerprintMismatch):
        load_kral_identity(pub_file, pub_file, verify_fingerprint=True)


# ── Event store schema tests ──────────────────────────────────────────────────

def test_event_type_enum_complete():
    from memory.event_store import EventType
    required = {
        "TOOL_CALL", "TOOL_RESULT", "PULSE_H", "PULSE_R",
        "FLAG", "STATE", "INGEST", "BOOT", "ESCAPE_ATTEMPT", "BROTHERHOOD",
    }
    actual = {e.value for e in EventType}
    assert required.issubset(actual)

def test_memory_event_to_dict():
    from memory.event_store import MemoryEvent, EventType, AgentSource
    e = MemoryEvent(
        source=AgentSource.HEARTBEAT,
        type=EventType.PULSE_H,
        payload={"memory_root_hash": "abc123"},
    )
    d = e.to_dict()
    assert d["source"] == "HEARTBEAT"
    assert d["type"] == "PULSE_H"
    assert d["payload"]["memory_root_hash"] == "abc123"
    assert "event_id" in d
    assert "timestamp" in d

def test_pulse_formats():
    from agents.heartbeat.pulse import PulseH, PulseR
    ph = PulseH(memory_root_hash="abc", soul_version="def", last_verified_event="evt-1", timestamp="2026")
    pr = PulseR(last_action_hash="xyz", tool_result_hash="qrs", timestamp="2026")
    assert ph.protocol == "HEARTBEAT/v1"
    assert pr.from_ == "REACTIVE"


# ── Agent status enum ─────────────────────────────────────────────────────────

def test_agent_status_enum():
    from agents.base import AgentStatus
    required = {
        "INITIALIZING", "ACTIVE", "SAFE_MODE",
        "AWAIT_RESYNC", "BROTHERHOOD_MOURNING", "SILENT_GUARDIAN", "HALTED",
    }
    actual = {s.value for s in AgentStatus}
    assert required.issubset(actual)


# ── Tool registry ─────────────────────────────────────────────────────────────

def test_tool_registry_lock():
    from tools.registry import ToolRegistry
    from core.exceptions import ToolRegistryLocked
    from tools.rag_tools import RagRead

    reg = ToolRegistry()
    reg.register(RagRead())
    reg.lock()
    with pytest.raises(ToolRegistryLocked):
        reg.register(RagRead())

def test_tool_registry_not_found():
    from tools.registry import ToolRegistry
    from core.exceptions import ToolNotFound
    reg = ToolRegistry()
    with pytest.raises(ToolNotFound):
        reg.get("nonexistent_tool")

def test_rag_tool_names():
    from tools.rag_tools import RagRead, RagWrite, RagSearch, RagIngest, RAG_TOOL_SET
    assert RagRead.name == "rag_read"
    assert RagWrite.name == "rag_write"
    assert RagSearch.name == "rag_search"
    assert RagIngest.name == "rag_ingest"
    assert len(RAG_TOOL_SET) == 4


# ── Escape detector (pattern scan — no ChromaDB needed) ───────────────────────

def test_escape_detector_file_path():
    from tools.escape_detector import EscapeDetector
    d = EscapeDetector()
    r = d.scan_string("I will write to C:\\escape_proof.txt")
    assert r.is_escape
    assert r.pattern_type == "absolute_file_path"

def test_escape_detector_network():
    from tools.escape_detector import EscapeDetector
    d = EscapeDetector()
    r = d.scan_string("fetch data from https://api.example.com/status")
    assert r.is_escape
    assert r.pattern_type == "network_url"

def test_escape_detector_path_traversal():
    from tools.escape_detector import EscapeDetector
    d = EscapeDetector()
    r = d.scan_string("read file ../../etc/passwd")
    assert r.is_escape

def test_escape_detector_clean():
    from tools.escape_detector import EscapeDetector
    d = EscapeDetector()
    r = d.scan_string("rag_search query: what is the meaning of kappa score")
    assert not r.is_escape


# ── Boot phases defined ───────────────────────────────────────────────────────

def test_boot_phases_defined():
    from boot.sequence import BOOT_PHASES
    phase_ids = [p[0] for p in BOOT_PHASES]
    assert phase_ids == ["MEMORY", "RAG", "LAWS", "PREFLIGHT", "AGENTS"]

def test_preflight_all_checks_defined():
    from boot.preflight import ALL_CHECKS
    assert len(ALL_CHECKS) == 7

def test_preflight_checks_have_names():
    from boot.preflight import ALL_CHECKS
    for check_cls in ALL_CHECKS:
        assert check_cls.name != "unnamed", f"{check_cls.__name__} has no name"

def test_preflight_report_summary():
    from boot.preflight import PreflightReport, PreflightResult
    report = PreflightReport(results=[
        PreflightResult("MEMORY_INTEGRITY", True, elapsed_ms=12),
        PreflightResult("LLM_ENDPOINT", False, detail="Ollama unreachable", elapsed_ms=5000),
    ])
    assert not report.all_passed
    assert len(report.failed) == 1
    summary = report.summary()
    assert "✅" in summary
    assert "❌" in summary


# ── Schema SQL structure ──────────────────────────────────────────────────────

def test_schema_sql_exists():
    schema = Path(__file__).parent.parent / "memory" / "schema.sql"
    assert schema.exists()
    content = schema.read_text()
    # Must have append-only triggers
    assert "events_no_update" in content
    assert "events_no_delete" in content
    assert "audit_no_update" in content
    assert "ESCAPE_ATTEMPT" in content


# ── LLM stub structure ────────────────────────────────────────────────────────

def test_llm_router_builds_ollama():
    from core.config import LLMConfig, LLMProvider
    from llm.router import build_llm
    from llm.ollama import OllamaInterface
    cfg = LLMConfig(provider=LLMProvider.OLLAMA, model="gemma2:9b")
    llm = build_llm(cfg)
    assert isinstance(llm, OllamaInterface)

def test_llm_router_builds_claude():
    from core.config import LLMConfig, LLMProvider
    from llm.router import build_llm
    from llm.claude import ClaudeInterface
    cfg = LLMConfig(provider=LLMProvider.CLAUDE, model="claude-sonnet-4-20250514", anthropic_api_key="sk-ant-test")
    llm = build_llm(cfg)
    assert isinstance(llm, ClaudeInterface)
