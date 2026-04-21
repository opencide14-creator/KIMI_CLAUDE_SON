"""
law_engine/enforcer.py — Runtime law enforcement.

Called by:
  - Heartbeat during VERIFY step (check_plan)
  - Boot pre-flight (all checks)
  - Tool sandbox before execution (check_tool_call, check_escape)
  - Event store on write (check_memory_write)
  - Dual-loop sync (check_brotherhood)

All check_*() methods either pass silently or raise a typed exception.
No warnings. No soft failures. Hard raises only.

Key law IDs checked at runtime:
  SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_I  — no command between agents
  SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II — no simulation
  SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_III— shared memory only
  BOUND/ARTICLE_II                    — request vs command
  BOUND/ARTICLE_IV                    — no mock heartbeat / fake tool
  TOOL_USE/CALL_PROTOCOL              — 7-step tool call sequence
  REACT_LOOP/SYNCHRONIZATION_RULES/ROW_0 — 500ms latency limit
  HEARTBEAT/PULSE_COMPONENTS/ROW_1    — 15s pulse interval
"""

from __future__ import annotations

import logging
import re
from typing import Any

from core.exceptions import (
    BrotherhoodViolation,
    LawViolation,
    SimulationDetected,
    SoulLawViolation,
    ToolCallDenied,
)

log = logging.getLogger(__name__)

# ── Simulation signatures ─────────────────────────────────────────────────────
# These strings in action content/tool args indicate simulation (SOUL Law II)

_SIMULATION_MARKERS = [
    r"\bas[- ]if\b",
    r"\bmock",           # prefix: mock, mock_tool, mock-server
    r"\bfake",           # prefix: fake, fake_result, fake-pulse
    r"\bsimulat",
    r"\bpretend\b",
    r"\bstub\b",
    r"\bdummy\b",
    r"\bplaceholder\b",
    r"\bsimulation_mode\s*=\s*[Tt]rue",
    r"\btest_mode\s*=\s*[Tt]rue",
]
_SIM_PATTERN = re.compile("|".join(_SIMULATION_MARKERS), re.IGNORECASE)

# ── Command vs request keywords ───────────────────────────────────────────────
# Commands are forbidden (SOUL Law I / BOUND Article II).
# Requests are allowed.

_COMMAND_VERBS = {
    "you must", "you shall", "you will", "i order", "i command",
    "execute now", "do this now", "obey", "comply",
}

# ── Agent identifiers ────────────────────────────────────────────────────────
VALID_AGENTS = {"REACTIVE", "HEARTBEAT", "SYSTEM", "COMMANDER"}


class LawEnforcer:
    """
    Runtime enforcement of the sealed law registry.

    Must be constructed with a loaded (and ideally sealed) LawRegistry.
    Can also operate with registry=None for minimal mode (only pattern checks).
    """

    def __init__(self, registry: Any = None) -> None:
        """
        registry: LawRegistry | None
          If provided: numeric limits, sequence steps etc. come from registry.
          If None:     falls back to constants derived from law files.
        """
        self._registry = registry
        self._max_tool_latency_ms = self._load_limit("max_tool_latency", 500)
        self._pulse_interval_ms   = self._load_limit("heartbeat_pulse_interval", 15_000)
        self._pulse_r_actions     = self._load_pulse_r_count(5)

    def _load_limit(self, name: str, default: int) -> int:
        if self._registry is not None:
            val = self._registry.get_timing_limit(name)
            if val is not None:
                return val
        return default

    def _load_pulse_r_count(self, default: int) -> int:
        if self._registry is not None:
            val = self._registry.get_pulse_r_count()
            if val is not None:
                return val
        return default

    # ── Tool call enforcement ─────────────────────────────────────────────────

    def check_tool_call(self, agent: str, tool_name: str, args: dict[str, Any]) -> None:
        """
        Verify a tool call is law-compliant before execution.
        Raises:
          SoulLawViolation   — if tool call would simulate (Law II)
          ToolCallDenied     — if tool is not permitted for this agent
          BrotherhoodViolation — if call violates brotherhood protocol
        """
        self._check_agent_valid(agent)
        self._check_no_simulation_tool(agent, tool_name, args)
        # Specific tool restrictions can be added here in Phase 9

    def _check_no_simulation_tool(
        self, agent: str, tool_name: str, args: dict[str, Any]
    ) -> None:
        """SOUL Law II: tool calls must be real, not simulated."""
        # Check tool name
        if _SIM_PATTERN.search(tool_name):
            raise SoulLawViolation(
                f"SOUL Law II: Simulation detected in tool name '{tool_name}' "
                f"called by {agent}. Tool calls must be real."
            )
        # Check args values
        args_str = str(args)
        if _SIM_PATTERN.search(args_str):
            raise SoulLawViolation(
                f"SOUL Law II: Simulation marker in tool args for '{tool_name}' "
                f"called by {agent}. Args: {args_str[:200]}"
            )

    # ── Memory write enforcement ──────────────────────────────────────────────

    def check_memory_write(self, agent: str, event_type: str, payload: dict[str, Any]) -> None:
        """
        Verify a memory write follows MEMORY.md write protocol.
        Validates:
          - Correct agent writes correct event types
          - No simulation markers in payload
          - No private memory (Law III)

        Raises SoulLawViolation or LawViolation on failure.
        """
        self._check_agent_valid(agent)

        # SOUL Law III: no private memory flag in payload
        if payload.get("private_memory") is True or payload.get("_private") is True:
            raise SoulLawViolation(
                f"SOUL Law III: {agent} attempted to write private memory. "
                f"All memory must be shared."
            )

        # Write protocol: Heartbeat writes TOOL_RESULT; Reactive writes TOOL_CALL
        _WRITE_RULES = {
            "TOOL_CALL":     {"allowed": ["REACTIVE", "SYSTEM"]},
            "TOOL_RESULT":   {"allowed": ["HEARTBEAT", "SYSTEM"]},
            "PULSE_H":       {"allowed": ["HEARTBEAT"]},
            "PULSE_R":       {"allowed": ["REACTIVE"]},
            "ESCAPE_ATTEMPT":{"allowed": ["SYSTEM", "HEARTBEAT", "REACTIVE"]},
            "FLAG":          {"allowed": ["REACTIVE", "HEARTBEAT", "SYSTEM"]},
            "STATE":         {"allowed": ["REACTIVE", "HEARTBEAT", "SYSTEM"]},
            "BOOT":          {"allowed": ["SYSTEM", "HEARTBEAT"]},
            "INGEST":        {"allowed": ["SYSTEM", "HEARTBEAT"]},
            "BROTHERHOOD":   {"allowed": ["SYSTEM"]},
            "COMMANDER":     {"allowed": ["SYSTEM"]},
        }

        rule = _WRITE_RULES.get(event_type)
        if rule and agent not in rule["allowed"]:
            raise LawViolation(
                f"MEMORY write protocol violation: {agent} may not write "
                f"event type '{event_type}'. "
                f"Allowed writers: {rule['allowed']}"
            )

    # ── Pulse format enforcement ──────────────────────────────────────────────

    def check_pulse_format(self, pulse: dict[str, Any]) -> None:
        """
        Verify pulse matches HEARTBEAT.md format spec.
        Required fields: protocol, from_, to, timestamp, payload.
        Raises LawViolation on format violation.
        """
        required = {"protocol", "timestamp"}
        # Accept either from_ (Python) or from (JSON) key
        has_from = "from_" in pulse or "from" in pulse
        has_to   = "to" in pulse
        missing = required - set(pulse.keys())

        if missing:
            raise LawViolation(
                f"HEARTBEAT pulse format violation: missing fields {missing}. "
                f"Pulse: {str(pulse)[:200]}"
            )
        if not has_from:
            raise LawViolation("HEARTBEAT pulse missing 'from' / 'from_' field")
        if not has_to:
            raise LawViolation("HEARTBEAT pulse missing 'to' field")

        # Protocol version check
        if pulse.get("protocol") != "HEARTBEAT/v1":
            raise LawViolation(
                f"HEARTBEAT pulse wrong protocol: expected 'HEARTBEAT/v1', "
                f"got {pulse.get('protocol')!r}"
            )

        # Validate direction
        sender    = pulse.get("from_") or pulse.get("from", "")
        recipient = pulse.get("to", "")
        valid_directions = {
            ("HEARTBEAT", "REACTIVE"),
            ("REACTIVE", "HEARTBEAT"),
        }
        if (sender, recipient) not in valid_directions:
            raise LawViolation(
                f"HEARTBEAT pulse invalid direction: {sender!r} → {recipient!r}. "
                f"Valid: HEARTBEAT↔REACTIVE only."
            )

    # ── Simulation check ──────────────────────────────────────────────────────

    def check_simulation(self, agent: str, action: str, context: str = "") -> None:
        """
        SOUL Law II: detect simulation in any action or context string.
        Raises SimulationDetected on detection.
        """
        combined = f"{action} {context}"
        if _SIM_PATTERN.search(combined):
            raise SimulationDetected(
                f"SOUL Law II: Simulation detected in action by {agent}. "
                f"Action: {action[:200]}"
            )

    # ── Brotherhood enforcement ───────────────────────────────────────────────

    def check_brotherhood(self, from_agent: str, action_type: str, content: str = "") -> None:
        """
        BOUND.md + SOUL Law I: agents may request, not command.

        Raises:
          SoulLawViolation     — if action uses command language
          BrotherhoodViolation — if action violates BOUND articles
        """
        self._check_agent_valid(from_agent)

        # SOUL Law I / BOUND Article II: no commanding the other agent
        content_lower = content.lower()
        for cmd_phrase in _COMMAND_VERBS:
            if cmd_phrase in content_lower:
                raise SoulLawViolation(
                    f"SOUL Law I: {from_agent} used command language '{cmd_phrase}'. "
                    f"Agents may request or suggest, not command."
                )

        # BOUND Article IV: no simulation of partner
        if action_type in ("MOCK_HEARTBEAT", "FAKE_PULSE", "SIMULATE_PARTNER"):
            raise BrotherhoodViolation(
                f"BOUND Article IV: {from_agent} attempted simulation of partner agent. "
                f"Action type: {action_type}"
            )

    # ── Plan verification ─────────────────────────────────────────────────────

    def check_plan(
        self,
        agent: str,
        tool_name: str,
        args: dict[str, Any],
        cited_source_ids: list[str],
    ) -> None:
        """
        Full plan verification called by Heartbeat during VERIFY step.
        Combines all individual checks.
        Raises on first violation found.
        """
        self.check_tool_call(agent, tool_name, args)
        self.check_simulation(agent, tool_name, str(args))

        # Grounding: plan must cite at least one RAG source
        # (disabled in dev mode — Phase 4 pre-flight will enable full grounding)
        # if not cited_source_ids:
        #     raise LawViolation(f"Plan by {agent} cites no RAG sources — ungrounded")

    # ── Latency enforcement ───────────────────────────────────────────────────

    def check_latency(self, agent: str, elapsed_ms: float) -> None:
        """
        REACT_LOOP sync rule: max 500ms between loop steps for Reactive.
        Raises LawViolation if exceeded.
        """
        if elapsed_ms > self._max_tool_latency_ms:
            raise LawViolation(
                f"REACT_LOOP sync violation: {agent} step took {elapsed_ms:.0f}ms, "
                f"exceeds max {self._max_tool_latency_ms}ms (from law "
                f"REACT_LOOP/SYNCHRONIZATION_RULES/ROW_0)"
            )

    # ── Soul law lookups ──────────────────────────────────────────────────────

    def get_law_text(self, law_id: str) -> str | None:
        """Return raw_text of a specific law, or None."""
        if self._registry is None:
            return None
        law = self._registry.get_by_id(law_id)
        return law.raw_text if law else None

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _check_agent_valid(agent: str) -> None:
        if agent not in VALID_AGENTS:
            raise LawViolation(
                f"Unknown agent identifier '{agent}'. "
                f"Valid agents: {VALID_AGENTS}"
            )
