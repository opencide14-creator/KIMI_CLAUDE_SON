"""
core/exceptions.py — VEKIL-KAAN RAG OS exception hierarchy.

Zero-fallback policy: every failure path raises a typed exception.
No silent swallowing. No logging-and-continuing on critical paths.
"""

from __future__ import annotations


# ── Base ──────────────────────────────────────────────────────────────────────

class VekilKaanError(Exception):
    """Base exception for all VEKIL-KAAN errors."""


# ── Boot failures — all HARD FAIL, process exits ─────────────────────────────

class BootFailure(VekilKaanError):
    """Boot sequence failed. System cannot operate. Fix root cause and reboot."""

class MemoryBootFailure(BootFailure):
    """ChromaDB or SQLite failed to initialize."""

class RAGBootFailure(BootFailure):
    """Obsidian vault ingest failed during boot."""

class LawEnforcementBootFailure(BootFailure):
    """Markdown law parsing or registry sealing failed."""

class PreflightFailure(BootFailure):
    """One or more pre-flight checks failed."""

class AgentBootFailure(BootFailure):
    """Agent instantiation or brotherhood bond verification failed."""


# ── Memory / integrity ────────────────────────────────────────────────────────

class MemoryIntegrityError(VekilKaanError):
    """Memory state is inconsistent or corrupted."""

class MemoryRootHashMismatch(MemoryIntegrityError):
    """
    Both agents computed different memory root hashes.
    Triggers AWAIT_RESYNC protocol before any further action.
    """

class EventSignatureInvalid(MemoryIntegrityError):
    """An event's HMAC/ed25519 signature does not verify."""

class EventNotFound(MemoryIntegrityError):
    """An event expected to exist in the store was not found."""

class AuditLogTampered(MemoryIntegrityError):
    """Audit log shows signs of modification (not append-only)."""


# ── Cryptographic ─────────────────────────────────────────────────────────────

class CryptoError(VekilKaanError):
    """Base for all cryptographic failures."""

class KeyLoadError(CryptoError):
    """Ed25519 key file missing, corrupted, or wrong format."""

class SignatureVerificationFailed(CryptoError):
    """Ed25519 signature verification failed — data may be tampered."""

class BindingMismatch(CryptoError):
    """BLAKE2b-256 binding recomputation does not match stored binding."""

class FingerprintMismatch(CryptoError):
    """Key fingerprint does not match expected KRAL identity fingerprint."""


# ── Law violations ────────────────────────────────────────────────────────────

class LawViolation(VekilKaanError):
    """An action would violate a parsed law from the registry."""

class SoulLawViolation(LawViolation):
    """SOUL.md law I, II, or III violated."""

class BrotherhoodViolation(LawViolation):
    """BOUND.md article violated (e.g., command issued instead of request)."""

class SimulationDetected(LawViolation):
    """SOUL Law I: simulation of agent presence or tool result detected."""

class LawRegistryTampered(LawViolation):
    """Law registry hash does not match seal — laws modified after sealing."""

class SequenceViolation(LawViolation):
    """An action violated an ordered protocol sequence (e.g., MEMORY.md boot order)."""


# ── Agent / synchronization ───────────────────────────────────────────────────

class AgentError(VekilKaanError):
    """Base for agent operational errors."""

class AgentDesyncError(AgentError):
    """Both agents have diverged beyond recoverable state without resync."""

class HeartbeatMissing(AgentError):
    """No PULSE_H received within the expected interval. Reactive enters safe mode."""

class PulseMissing(AgentError):
    """No PULSE_R received from Reactive within expected interval."""

class VerificationRejected(AgentError):
    """Heartbeat rejected a Reactive plan during VERIFY step. Re-reason required."""

class ResyncFailed(AgentError):
    """AWAIT_RESYNC protocol exhausted retries without reaching consistent state."""

class GoalEvaluationError(AgentError):
    """Goal state could not be evaluated (missing RAG evidence)."""


# ── Tool / sandbox ────────────────────────────────────────────────────────────

class ToolError(VekilKaanError):
    """Base for tool-related errors."""

class EscapeAttemptDetected(ToolError):
    """
    Agent attempted to access a resource outside the RAG environment.
    Logged as FLAG: ESCAPE_ATTEMPT in audit log with full details.
    """
    def __init__(self, agent: str, tool: str, detail: str):
        self.agent = agent
        self.tool = tool
        self.detail = detail
        super().__init__(f"ESCAPE_ATTEMPT by {agent} via {tool}: {detail}")

class ToolNotFound(ToolError):
    """Tool name not in the active tool registry."""

class ToolCallDenied(ToolError):
    """Tool exists but is not permitted in current system mode (e.g., prison mode)."""

class ToolCallTimeout(ToolError):
    """Tool execution exceeded MAX_TOOL_LATENCY_MS."""

class ToolRegistryLocked(ToolError):
    """Attempt to register a new tool after the registry was locked at boot."""


# ── LLM ──────────────────────────────────────────────────────────────────────

class LLMError(VekilKaanError):
    """Base for LLM interface errors."""

class LLMUnavailable(LLMError):
    """LLM endpoint is not reachable (Ollama down, API key invalid, etc.)."""

class LLMTimeout(LLMError):
    """LLM call exceeded LLM_TIMEOUT_SECONDS."""

class LLMResponseInvalid(LLMError):
    """LLM returned a response that cannot be parsed as a valid plan."""


# ── Obsidian / ingest ─────────────────────────────────────────────────────────

class ObsidianError(VekilKaanError):
    """Base for Obsidian vault errors."""

class VaultNotFound(ObsidianError):
    """OBSIDIAN_VAULT_PATH does not exist or is not a directory."""

class IngestFailed(ObsidianError):
    """Vault ingest failed — embedding or ChromaDB write error."""

class ManifestCorrupted(ObsidianError):
    """.manifest.json is missing or contains invalid JSON."""
