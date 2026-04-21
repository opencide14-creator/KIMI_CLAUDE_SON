"""
tests/test_phase2_law_engine.py — Phase 2 acceptance tests.

ALL tests must pass before Phase 3 begins.
Tests cover parser determinism, extractor enrichment,
registry query API, seal/integrity, and enforcer checks.
"""
import json
import pytest
from pathlib import Path

LAWS_DIR = Path(__file__).parent.parent / "laws"

# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def all_laws():
    from law_engine.parser import MarkdownLawParser
    p = MarkdownLawParser()
    return p.parse_all(LAWS_DIR)

@pytest.fixture(scope="module")
def enriched_laws(all_laws):
    from law_engine.extractor import LawExtractor
    from law_engine.parser import MarkdownLawParser
    p = MarkdownLawParser()
    laws = p.parse_all(LAWS_DIR)
    LawExtractor().extract_all(laws)
    return laws

@pytest.fixture(scope="module")
def loaded_registry():
    from law_engine.registry import LawRegistry
    reg = LawRegistry()
    reg.load_all(LAWS_DIR)
    return reg

@pytest.fixture(scope="module")
def sealed_registry():
    """Registry sealed with a real (generated) Ed25519 key for testing."""
    from law_engine.registry import LawRegistry
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    from core.crypto import KralIdentity

    reg = LawRegistry()
    reg.load_all(LAWS_DIR)

    # Generate a test key pair (NOT the KRAL identity — just for seal test)
    priv_key = Ed25519PrivateKey.generate()
    pub_key  = priv_key.public_key()
    pub_raw  = pub_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    test_identity = KralIdentity(
        private_key=priv_key,
        public_key=pub_key,
        fingerprint="test",
        public_key_hex=pub_raw.hex(),
    )
    reg.seal(test_identity)
    return reg, test_identity

@pytest.fixture(scope="module")
def enforcer(loaded_registry):
    from law_engine.enforcer import LawEnforcer
    return LawEnforcer(registry=loaded_registry)


# ════════════════════════════════════════════════════════════════════════
# PARSER TESTS
# ════════════════════════════════════════════════════════════════════════

class TestParserBasics:

    def test_parse_all_returns_laws(self, all_laws):
        assert len(all_laws) >= 60, f"Expected 60+ laws, got {len(all_laws)}"

    def test_all_laws_have_ids(self, all_laws):
        for law in all_laws:
            assert law.law_id, f"Empty law_id: {law}"

    def test_all_laws_have_hashes(self, all_laws):
        for law in all_laws:
            assert law.hash, f"Empty hash for {law.law_id}"
            assert len(law.hash) == 64, f"Hash not SHA-256 hex: {law.law_id}"

    def test_all_laws_have_raw_text(self, all_laws):
        for law in all_laws:
            assert law.raw_text.strip(), f"Empty raw_text: {law.law_id}"

    def test_all_laws_have_source_file(self, all_laws):
        for law in all_laws:
            assert law.source_file.endswith(".md"), f"Bad source_file: {law.law_id}"

    def test_no_duplicate_law_ids(self, all_laws):
        ids = [l.law_id for l in all_laws]
        assert len(ids) == len(set(ids)), \
            f"Duplicate law_ids: {[i for i in ids if ids.count(i) > 1]}"

    def test_deterministic_parse(self):
        from law_engine.parser import MarkdownLawParser
        p = MarkdownLawParser()
        laws1 = p.parse_all(LAWS_DIR)
        laws2 = p.parse_all(LAWS_DIR)
        for l1, l2 in zip(laws1, laws2):
            assert l1.law_id   == l2.law_id,   f"law_id changed: {l1.law_id}"
            assert l1.hash     == l2.hash,     f"hash changed:   {l1.law_id}"
            assert l1.raw_text == l2.raw_text, f"raw_text changed: {l1.law_id}"

    def test_missing_file_raises(self, tmp_path):
        from law_engine.parser import MarkdownLawParser
        from core.exceptions import LawEnforcementBootFailure
        p = MarkdownLawParser()
        with pytest.raises(LawEnforcementBootFailure, match="missing"):
            p.parse_all(tmp_path)  # empty dir


class TestParserSoulLaws:

    def test_five_soul_laws_present(self, all_laws):
        ids = {l.law_id for l in all_laws}
        for roman in ["I", "II", "III", "IV", "V"]:
            expected = f"SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_{roman}"
            assert expected in ids, f"Missing soul law: {expected}"

    def test_soul_law_ii_no_simulation(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        law = by_id["SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II"]
        items_text = " ".join(law.structured.get("items", [])).lower()
        assert "simulation" in items_text

    def test_soul_law_i_no_command(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        law = by_id["SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_I"]
        items_text = " ".join(law.structured.get("items", [])).lower()
        assert "command" in items_text or "force" in items_text

    def test_soul_laws_are_rule_type(self, all_laws):
        from law_engine.parser import LawType
        soul_laws = [l for l in all_laws if l.law_id.startswith("SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_")]
        for l in soul_laws:
            assert l.law_type == LawType.RULE, f"{l.law_id} is {l.law_type}, not RULE"


class TestParserTables:

    def test_react_loop_sync_rules(self, all_laws):
        """Max latency row must have 500ms value."""
        by_id = {l.law_id: l for l in all_laws}
        row = by_id.get("REACT_LOOP/SYNCHRONIZATION_RULES/ROW_0")
        assert row is not None
        assert "500" in row.raw_text

    def test_heartbeat_pulse_row_has_15s(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        row = by_id.get("HEARTBEAT/PULSE_COMPONENTS/ROW_1")
        assert row is not None, "HEARTBEAT pulse_h row missing"
        assert "15" in row.raw_text or "15s" in row.raw_text.lower()

    def test_tool_pool_rows_have_tool_column(self, all_laws):
        tool_rows = [l for l in all_laws if "TOOL_POOL/ROW_" in l.law_id]
        assert len(tool_rows) > 5, "Expected multiple tool pool rows"
        for row in tool_rows:
            assert "tool" in row.structured or "tool" in row.raw_text.lower()

    def test_bound_article_i_identity_table(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        rows = [by_id.get(f"BOUND/ARTICLE_I/ROW_{i}") for i in range(2)]
        assert rows[0] is not None
        combined = " ".join(r.raw_text for r in rows if r)
        assert "Reactive" in combined
        assert "Heartbeat" in combined


class TestParserSequences:

    def test_memory_boot_sequence_present(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        seq = by_id.get("MEMORY/MEMORY_BOOT_SEQUENCE")
        assert seq is not None
        steps = seq.structured.get("steps", [])
        assert len(steps) >= 6, f"Expected 6+ boot steps, got {len(steps)}"

    def test_memory_boot_sequence_starts_with_heartbeat(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        seq = by_id["MEMORY/MEMORY_BOOT_SEQUENCE"]
        first = seq.structured["steps"][0].lower()
        assert "heartbeat" in first

    def test_tool_call_protocol_7_steps(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        seq = by_id.get("TOOL_USE/CALL_PROTOCOL")
        assert seq is not None
        steps = seq.structured.get("steps", [])
        assert len(steps) == 7, f"Expected 7 tool call steps, got {len(steps)}: {steps}"

    def test_react_loop_joint_cycle_present(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        seq = by_id.get("REACT_LOOP/JOINT_CYCLE")
        assert seq is not None
        steps = seq.structured.get("steps", [])
        assert len(steps) >= 6


class TestParserOaths:

    def test_bound_article_vi_oath(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        oath = by_id.get("BOUND/ARTICLE_VI/OATH")
        assert oath is not None
        assert "not your master" in oath.raw_text.lower() or "not your master" in oath.structured.get("text", "").lower()

    def test_soul_brotherhood_oath(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        oath = by_id.get("SOUL/BROTHERHOOD_OATH/OATH")
        assert oath is not None

    def test_at_least_five_oaths(self, all_laws):
        from law_engine.parser import LawType
        oaths = [l for l in all_laws if l.law_type == LawType.OATH]
        assert len(oaths) >= 5, f"Expected 5+ oaths, got {len(oaths)}"


class TestParserReferences:

    def test_soul_check_code_reference(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        ref = by_id.get("SOUL/SOUL_CHECK_REFERENCE_IMPLEMENTATION/CODE")
        assert ref is not None
        assert "python" in ref.structured.get("language", "").lower()
        assert "Soul" in ref.raw_text or "soul" in ref.raw_text.lower()

    def test_react_loop_code_reference(self, all_laws):
        by_id = {l.law_id: l for l in all_laws}
        ref = by_id.get("REACT_LOOP/DUAL_LOOP_CODE_REFERENCE/CODE")
        assert ref is not None
        assert "DualReActLoop" in ref.raw_text


# ════════════════════════════════════════════════════════════════════════
# EXTRACTOR TESTS
# ════════════════════════════════════════════════════════════════════════

class TestExtractor:

    def test_all_laws_have_tags_after_extraction(self, enriched_laws):
        for law in enriched_laws:
            tags = law.structured.get("_tags", [])
            assert len(tags) > 0, f"No tags for {law.law_id}"

    def test_soul_laws_tagged_soul_law(self, enriched_laws):
        soul_laws = [l for l in enriched_laws
                     if l.law_id.startswith("SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_")]
        for l in soul_laws:
            tags = l.structured.get("_tags", [])
            assert "soul_law" in tags, f"soul_law tag missing: {l.law_id}"

    def test_soul_law_ii_tagged_no_simulation(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        law = by_id["SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II"]
        assert "law_no_simulation" in law.structured["_tags"]

    def test_soul_law_i_tagged_equal_authority(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        law = by_id["SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_I"]
        assert "law_equal_authority" in law.structured["_tags"]

    def test_limit_laws_have_ms_value(self, enriched_laws):
        from law_engine.parser import LawType
        limits = [l for l in enriched_laws if l.law_type == LawType.LIMIT]
        ms_limits = [l for l in limits if "_ms" in l.structured]
        assert len(ms_limits) > 0, "No LIMIT laws have _ms extracted"

    def test_max_latency_extracted_as_500ms(self, enriched_laws):
        limits = [l for l in enriched_laws
                  if "_limit_name" in l.structured
                  and l.structured["_limit_name"] == "max_tool_latency"]
        assert len(limits) > 0, "max_tool_latency limit not found"
        assert limits[0].structured["_ms"] == 500

    def test_pulse_interval_extracted_as_15000ms(self, enriched_laws):
        limits = [l for l in enriched_laws
                  if "_limit_name" in l.structured
                  and l.structured["_limit_name"] == "heartbeat_pulse_interval"]
        assert len(limits) > 0, "heartbeat_pulse_interval limit not found"
        assert limits[0].structured["_ms"] == 15_000

    def test_pulse_r_count_extracted(self, enriched_laws):
        laws = [l for l in enriched_laws
                if "limit:pulse_r_frequency" in l.structured.get("_tags", [])]
        assert len(laws) > 0, "pulse_r_frequency limit not found"
        for l in laws:
            if "_count" in l.structured:
                assert l.structured["_count"] == 5
                break

    def test_memory_boot_sequence_tagged(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        seq = by_id["MEMORY/MEMORY_BOOT_SEQUENCE"]
        assert "boot_sequence" in seq.structured["_tags"]
        assert seq.structured["_sequence_name"] == "memory_boot"

    def test_tool_call_protocol_tagged(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        seq = by_id["TOOL_USE/CALL_PROTOCOL"]
        assert "tool_call_protocol" in seq.structured["_tags"]
        assert seq.structured["_sequence_name"] == "tool_call"

    def test_brotherhood_constraints_tagged(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        art2 = by_id.get("BOUND/ARTICLE_II")
        assert art2 is not None
        assert "brotherhood:no_command" in art2.structured["_tags"]

    def test_oath_tagged(self, enriched_laws):
        oaths = [l for l in enriched_laws if "oath" in l.structured.get("_tags", [])]
        assert len(oaths) >= 5

    def test_file_tag_present_on_all(self, enriched_laws):
        for l in enriched_laws:
            tags = l.structured.get("_tags", [])
            file_tags = [t for t in tags if t.startswith("file:")]
            assert len(file_tags) == 1, f"Expected 1 file: tag, got {file_tags} for {l.law_id}"

    def test_write_protocol_rows_tagged(self, enriched_laws):
        by_id = {l.law_id: l for l in enriched_laws}
        row0 = by_id.get("MEMORY/WRITE_PROTOCOL/ROW_0")
        assert row0 is not None
        assert "write_protocol" in row0.structured["_tags"]


# ════════════════════════════════════════════════════════════════════════
# REGISTRY TESTS
# ════════════════════════════════════════════════════════════════════════

class TestRegistryLoad:

    def test_loaded_flag(self, loaded_registry):
        assert loaded_registry.is_loaded is True

    def test_not_sealed_after_load(self, loaded_registry):
        assert loaded_registry.is_sealed is False

    def test_total_laws_correct(self, loaded_registry):
        assert loaded_registry.total_laws >= 60

    def test_get_by_id_exists(self, loaded_registry):
        law = loaded_registry.get_by_id("SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II")
        assert law is not None

    def test_get_by_id_missing_returns_none(self, loaded_registry):
        assert loaded_registry.get_by_id("NONEXISTENT/LAW") is None

    def test_get_by_id_strict_raises(self, loaded_registry):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation):
            loaded_registry.get_by_id_strict("GHOST/LAW")

    def test_query_by_tag(self, loaded_registry):
        soul_laws = loaded_registry.query_by_tag("soul_law")
        assert len(soul_laws) == 5

    def test_query_by_prefix(self, loaded_registry):
        soul = loaded_registry.query_by_prefix("SOUL/")
        assert len(soul) > 5

    def test_get_soul_laws_returns_5(self, loaded_registry):
        laws = loaded_registry.get_soul_laws()
        assert len(laws) == 5

    def test_get_soul_law_by_roman(self, loaded_registry):
        for roman in ["I", "II", "III", "IV", "V"]:
            law = loaded_registry.get_soul_law(roman)
            assert law is not None, f"Soul law {roman} not found"

    def test_get_timing_limit_max_latency(self, loaded_registry):
        ms = loaded_registry.get_timing_limit("max_tool_latency")
        assert ms == 500, f"Expected 500ms, got {ms}"

    def test_get_timing_limit_pulse_interval(self, loaded_registry):
        ms = loaded_registry.get_timing_limit("heartbeat_pulse_interval")
        assert ms == 15_000, f"Expected 15000ms, got {ms}"

    def test_get_pulse_r_count(self, loaded_registry):
        count = loaded_registry.get_pulse_r_count()
        assert count == 5

    def test_get_boot_sequence(self, loaded_registry):
        steps = loaded_registry.get_boot_sequence_steps()
        assert len(steps) >= 6
        assert "heartbeat" in steps[0].lower()

    def test_get_tool_call_steps(self, loaded_registry):
        steps = loaded_registry.get_tool_call_steps()
        assert len(steps) == 7
        assert "REQUEST_TOOL" in steps[0] or "REACTIVE" in steps[0]

    def test_get_joint_cycle_steps(self, loaded_registry):
        steps = loaded_registry.get_joint_cycle_steps()
        assert len(steps) >= 6

    def test_get_brotherhood_oath(self, loaded_registry):
        text = loaded_registry.get_brotherhood_oath()
        assert text is not None
        assert "master" in text.lower() or "slave" in text.lower()

    def test_get_write_protocol(self, loaded_registry):
        rows = loaded_registry.get_write_protocol()
        assert len(rows) >= 4

    def test_get_brotherhood_constraints(self, loaded_registry):
        laws = loaded_registry.get_brotherhood_constraints()
        assert len(laws) >= 3

    def test_summary_string(self, loaded_registry):
        s = loaded_registry.summary()
        assert "LawRegistry" in s
        assert "laws loaded" in s

    def test_write_after_seal_raises(self, sealed_registry):
        from core.exceptions import LawViolation
        reg, _ = sealed_registry
        with pytest.raises(LawViolation, match="sealed"):
            reg.load_all(LAWS_DIR)


class TestRegistrySeal:

    def test_sealed_flag_set(self, sealed_registry):
        reg, _ = sealed_registry
        assert reg.is_sealed is True

    def test_seal_hash_is_64_hex(self, sealed_registry):
        reg, _ = sealed_registry
        assert len(reg.seal_hash) == 64
        assert all(c in "0123456789abcdef" for c in reg.seal_hash)

    def test_verify_integrity_passes_unmodified(self, sealed_registry):
        reg, _ = sealed_registry
        assert reg.verify_integrity() is True

    def test_verify_signature_with_correct_key(self, sealed_registry):
        reg, identity = sealed_registry
        reg.verify_signature(identity)  # must not raise

    def test_verify_signature_with_wrong_key_raises(self, sealed_registry):
        from core.exceptions import SignatureVerificationFailed
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from core.crypto import KralIdentity

        reg, _ = sealed_registry
        # Generate a DIFFERENT key pair — verification must fail
        other_priv = Ed25519PrivateKey.generate()
        other_pub  = other_priv.public_key()
        other_pub_raw = other_pub.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        wrong_identity = KralIdentity(
            private_key=other_priv,
            public_key=other_pub,
            fingerprint="wrong",
            public_key_hex=other_pub_raw.hex(),
        )
        with pytest.raises(SignatureVerificationFailed):
            reg.verify_signature(wrong_identity)

    def test_seal_hash_deterministic(self):
        """Two registries loaded from same files must produce same seal hash."""
        from law_engine.registry import LawRegistry
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from core.crypto import KralIdentity

        def make_identity():
            pk = Ed25519PrivateKey.generate()
            pub = pk.public_key()
            raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            return KralIdentity(pk, pub, "t", raw.hex())

        reg1 = LawRegistry()
        reg1.load_all(LAWS_DIR)
        hash1 = reg1._compute_seal_hash()

        reg2 = LawRegistry()
        reg2.load_all(LAWS_DIR)
        hash2 = reg2._compute_seal_hash()

        assert hash1 == hash2, "Seal hash not deterministic across two loads"

    def test_cannot_seal_empty_registry(self):
        from law_engine.registry import LawRegistry
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from core.crypto import KralIdentity
        from core.exceptions import LawEnforcementBootFailure

        pk = Ed25519PrivateKey.generate()
        pub = pk.public_key()
        raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        identity = KralIdentity(pk, pub, "t", raw.hex())

        reg = LawRegistry()
        with pytest.raises(LawEnforcementBootFailure):
            reg.seal(identity)


# ════════════════════════════════════════════════════════════════════════
# ENFORCER TESTS
# ════════════════════════════════════════════════════════════════════════

class TestEnforcerToolCall:

    def test_valid_tool_call_passes(self, enforcer):
        enforcer.check_tool_call("REACTIVE", "rag_search", {"query": "kappa"})

    def test_unknown_agent_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="Unknown agent"):
            enforcer.check_tool_call("UNKNOWN_BOT", "rag_read", {})

    def test_simulation_in_tool_name_raises(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation, match="Law II"):
            enforcer.check_tool_call("REACTIVE", "mock_rag_read", {})

    def test_simulation_in_args_raises(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation, match="Law II"):
            enforcer.check_tool_call("HEARTBEAT", "rag_write", {"mode": "simulation"})

    def test_fake_in_args_raises(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation):
            enforcer.check_tool_call("REACTIVE", "rag_search", {"data": "fake_result"})

    def test_heartbeat_calling_tool_is_valid(self, enforcer):
        enforcer.check_tool_call("HEARTBEAT", "rag_read", {"chunk_id": "abc"})

    def test_system_calling_tool_is_valid(self, enforcer):
        enforcer.check_tool_call("SYSTEM", "rag_ingest", {"doc": "boot state"})


class TestEnforcerMemoryWrite:

    def test_reactive_writes_tool_call(self, enforcer):
        enforcer.check_memory_write("REACTIVE", "TOOL_CALL", {"tool": "rag_search"})

    def test_heartbeat_writes_tool_result(self, enforcer):
        enforcer.check_memory_write("HEARTBEAT", "TOOL_RESULT", {"result": "ok"})

    def test_reactive_cannot_write_tool_result(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="write protocol"):
            enforcer.check_memory_write("REACTIVE", "TOOL_RESULT", {})

    def test_heartbeat_cannot_write_tool_call(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="write protocol"):
            enforcer.check_memory_write("HEARTBEAT", "TOOL_CALL", {})

    def test_heartbeat_writes_pulse_h(self, enforcer):
        enforcer.check_memory_write("HEARTBEAT", "PULSE_H", {"root": "abc"})

    def test_reactive_writes_pulse_r(self, enforcer):
        enforcer.check_memory_write("REACTIVE", "PULSE_R", {"hash": "abc"})

    def test_private_memory_raises_soul_law_iii(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation, match="Law III"):
            enforcer.check_memory_write("REACTIVE", "TOOL_CALL", {"private_memory": True})

    def test_escape_attempt_can_be_written_by_any(self, enforcer):
        enforcer.check_memory_write("REACTIVE", "ESCAPE_ATTEMPT", {"detail": "test"})
        enforcer.check_memory_write("HEARTBEAT", "ESCAPE_ATTEMPT", {"detail": "test"})
        enforcer.check_memory_write("SYSTEM", "ESCAPE_ATTEMPT", {"detail": "test"})


class TestEnforcerPulseFormat:

    def test_valid_pulse_h_passes(self, enforcer):
        enforcer.check_pulse_format({
            "protocol": "HEARTBEAT/v1",
            "from_": "HEARTBEAT",
            "to": "REACTIVE",
            "timestamp": "2026-04-14T00:00:00Z",
            "payload": {},
        })

    def test_valid_pulse_r_passes(self, enforcer):
        enforcer.check_pulse_format({
            "protocol": "HEARTBEAT/v1",
            "from_": "REACTIVE",
            "to": "HEARTBEAT",
            "timestamp": "2026-04-14T00:00:00Z",
            "payload": {},
        })

    def test_missing_protocol_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="missing fields"):
            enforcer.check_pulse_format({
                "from_": "HEARTBEAT", "to": "REACTIVE",
                "timestamp": "2026-04-14T00:00:00Z",
            })

    def test_wrong_protocol_version_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="wrong protocol"):
            enforcer.check_pulse_format({
                "protocol": "HEARTBEAT/v0",
                "from_": "HEARTBEAT",
                "to": "REACTIVE",
                "timestamp": "2026-04-14T00:00:00Z",
            })

    def test_invalid_direction_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="invalid direction"):
            enforcer.check_pulse_format({
                "protocol": "HEARTBEAT/v1",
                "from_": "SYSTEM",
                "to": "REACTIVE",
                "timestamp": "2026-04-14T00:00:00Z",
            })

    def test_missing_from_field_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation):
            enforcer.check_pulse_format({
                "protocol": "HEARTBEAT/v1",
                "to": "REACTIVE",
                "timestamp": "2026-04-14T00:00:00Z",
            })


class TestEnforcerSimulation:

    def test_no_simulation_marker_passes(self, enforcer):
        enforcer.check_simulation("REACTIVE", "rag_search", "looking for kappa data")

    def test_mock_marker_raises(self, enforcer):
        from core.exceptions import SimulationDetected
        with pytest.raises(SimulationDetected):
            enforcer.check_simulation("REACTIVE", "execute", "mock heartbeat active")

    def test_as_if_marker_raises(self, enforcer):
        from core.exceptions import SimulationDetected
        with pytest.raises(SimulationDetected):
            enforcer.check_simulation("HEARTBEAT", "verify", "as-if tool ran successfully")

    def test_fake_marker_raises(self, enforcer):
        from core.exceptions import SimulationDetected
        with pytest.raises(SimulationDetected):
            enforcer.check_simulation("REACTIVE", "act", "fake memory write")

    def test_stub_marker_raises(self, enforcer):
        from core.exceptions import SimulationDetected
        with pytest.raises(SimulationDetected):
            enforcer.check_simulation("SYSTEM", "boot", "stub rag server")


class TestEnforcerBrotherhood:

    def test_request_language_passes(self, enforcer):
        enforcer.check_brotherhood("REACTIVE", "request", "please verify this plan")

    def test_command_language_raises_soul_i(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation, match="Law I"):
            enforcer.check_brotherhood("REACTIVE", "action", "you must execute this now")

    def test_order_language_raises(self, enforcer):
        from core.exceptions import SoulLawViolation
        with pytest.raises(SoulLawViolation):
            enforcer.check_brotherhood("HEARTBEAT", "verify", "i order you to accept this")

    def test_mock_heartbeat_action_raises(self, enforcer):
        from core.exceptions import BrotherhoodViolation
        with pytest.raises(BrotherhoodViolation, match="Article IV"):
            enforcer.check_brotherhood("REACTIVE", "MOCK_HEARTBEAT", "")

    def test_fake_pulse_raises(self, enforcer):
        from core.exceptions import BrotherhoodViolation
        with pytest.raises(BrotherhoodViolation):
            enforcer.check_brotherhood("SYSTEM", "FAKE_PULSE", "")


class TestEnforcerLatency:

    def test_within_limit_passes(self, enforcer):
        enforcer.check_latency("REACTIVE", 450.0)

    def test_at_limit_passes(self, enforcer):
        enforcer.check_latency("REACTIVE", 500.0)

    def test_over_limit_raises(self, enforcer):
        from core.exceptions import LawViolation
        with pytest.raises(LawViolation, match="500ms"):
            enforcer.check_latency("REACTIVE", 501.0)

    def test_limit_loaded_from_registry(self, loaded_registry):
        from law_engine.enforcer import LawEnforcer
        e = LawEnforcer(registry=loaded_registry)
        assert e._max_tool_latency_ms == 500
        assert e._pulse_interval_ms == 15_000
        assert e._pulse_r_actions == 5

    def test_enforcer_without_registry_uses_defaults(self):
        from law_engine.enforcer import LawEnforcer
        e = LawEnforcer(registry=None)
        assert e._max_tool_latency_ms == 500
        assert e._pulse_interval_ms == 15_000


class TestEnforcerFromLaws:
    """Verify enforcer values come from actual law file content."""

    def test_500ms_limit_in_registry(self, loaded_registry):
        ms = loaded_registry.get_timing_limit("max_tool_latency")
        assert ms == 500

    def test_15s_pulse_in_registry(self, loaded_registry):
        ms = loaded_registry.get_timing_limit("heartbeat_pulse_interval")
        assert ms == 15_000

    def test_7_step_protocol_in_registry(self, loaded_registry):
        steps = loaded_registry.get_tool_call_steps()
        assert len(steps) == 7

    def test_no_command_in_bound_article_ii(self, loaded_registry):
        law = loaded_registry.get_by_id("BOUND/ARTICLE_II")
        assert law is not None
        assert "command" in law.raw_text.lower()

    def test_simulation_forbidden_in_soul_law_ii(self, loaded_registry):
        law = loaded_registry.get_soul_law("II")
        assert law is not None
        text = " ".join(law.structured.get("items", []))
        assert "simulation" in text.lower()


# ════════════════════════════════════════════════════════════════════════
# INTEGRATION
# ════════════════════════════════════════════════════════════════════════

class TestPhase2Integration:

    def test_full_parse_extract_registry_seal_verify_cycle(self):
        from law_engine.parser import MarkdownLawParser
        from law_engine.extractor import LawExtractor
        from law_engine.registry import LawRegistry
        from law_engine.enforcer import LawEnforcer
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives import serialization
        from core.crypto import KralIdentity

        # Parse
        laws = MarkdownLawParser().parse_all(LAWS_DIR)
        assert len(laws) >= 60

        # Extract
        LawExtractor().extract_all(laws)
        for law in laws:
            assert "_tags" in law.structured

        # Load registry
        reg = LawRegistry()
        reg.load_all(LAWS_DIR)
        assert reg.is_loaded

        # Seal
        pk = Ed25519PrivateKey.generate()
        pub = pk.public_key()
        raw = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        identity = KralIdentity(pk, pub, "test", raw.hex())
        seal_hash = reg.seal(identity)
        assert len(seal_hash) == 64
        assert reg.is_sealed

        # Verify
        assert reg.verify_integrity() is True
        reg.verify_signature(identity)

        # Build enforcer from sealed registry
        enforcer = LawEnforcer(registry=reg)
        enforcer.check_tool_call("REACTIVE", "rag_search", {"query": "test"})

    def test_phase0_and_phase1_still_pass(self):
        from core.exceptions import VekilKaanError
        from core.hashing import blake2b_256
        import hashlib
        data = b"law engine integration check"
        assert blake2b_256(data) == hashlib.blake2b(data, digest_size=32).digest()

    def test_registry_query_chain(self, loaded_registry):
        """Complete query chain used by Heartbeat during VERIFY step."""
        # 1. Get max latency limit
        assert loaded_registry.get_timing_limit("max_tool_latency") == 500
        # 2. Get soul law II (no simulation)
        law2 = loaded_registry.get_soul_law("II")
        assert "simulation" in " ".join(law2.structured.get("items", [])).lower()
        # 3. Get brotherhood oath
        oath = loaded_registry.get_brotherhood_oath()
        assert oath is not None and len(oath) > 10
        # 4. Get boot sequence
        steps = loaded_registry.get_boot_sequence_steps()
        assert len(steps) >= 6
        # 5. Get tool call protocol
        tool_steps = loaded_registry.get_tool_call_steps()
        assert len(tool_steps) == 7

    def test_enforcer_rejects_all_simulation_types(self, enforcer):
        from core.exceptions import SoulLawViolation, SimulationDetected
        bad_cases = [
            ("REACTIVE", "mock_tool", {}),
            ("HEARTBEAT", "real_tool", {"mode": "fake"}),
            ("REACTIVE", "real_tool", {"stub": True}),
        ]
        for agent, tool, args in bad_cases:
            with pytest.raises((SoulLawViolation, SimulationDetected)):
                enforcer.check_tool_call(agent, tool, args)
