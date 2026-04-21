"""
agents/reactive/reason.py — OODA-based reasoning engine.

Builds a structured Plan from:
  - Current observation (input + RAG context)
  - Heartbeat state (memory root hash, last verified event)
  - Law registry (available tools, constraints)
  - Last N events from memory (action history)

LLM call is optional — if no LLM is wired, produces a RAG search plan by default.
The plan output is always a structured dict, never free-form text.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from core.hashing import sha256_hex

if TYPE_CHECKING:
    from agents.base import AgentState
    from law_engine.registry import LawRegistry
    from llm.base import BaseLLMInterface

log = logging.getLogger(__name__)


@dataclass
class Observation:
    input_text:       str
    rag_context:      list[dict]      # chunks from ChromaDB semantic search
    last_events:      list[Any]       # last N events from event store
    heartbeat_hash:   str = ""


@dataclass
class Plan:
    tool_name:        str
    tool_args:        dict
    reasoning:        str
    cited_source_ids: list[str] = field(default_factory=list)
    iteration:        int       = 0

    def args_hash(self) -> str:
        return sha256_hex(
            json.dumps(self.tool_args, sort_keys=True, separators=(",", ":")).encode()
        )


class ReasonEngine:
    """
    Produces a Plan from an Observation.

    Two modes:
      - LLM mode: sends structured prompt to LLM, parses JSON plan from response
      - Fallback mode (no LLM): always returns rag_search plan
    """

    SYSTEM_PROMPT_TEMPLATE = """\
You are the Reactive Agent of VEKIL-KAAN.
Your cycle: THINK → DECIDE → ACT → FEED.
Available tools: {tools}
Soul Laws:
  I. Never command Heartbeat — only request.
  II. No simulation — all actions must be real.
  III. All memory is shared — no private state.
Current memory root hash: {memory_root}
Last verified event: {last_event}

Respond ONLY with valid JSON:
{{"tool_name": "<tool>", "tool_args": {{}}, "reasoning": "<why>", "cited_source_ids": []}}
"""

    def __init__(
        self,
        registry:     "LawRegistry",
        llm:          "BaseLLMInterface | None" = None,
        substrate:    Any = None,
    ) -> None:
        self._registry  = registry
        self._llm       = llm
        self._substrate = substrate
        self._tools     = self._build_tool_list()

    def _build_tool_list(self) -> list[str]:
        """Get available tools from TOOL_USE.md law registry."""
        tool_rows = self._registry.query_by_tag("tool_definition")
        tools = []
        for row in tool_rows:
            tool_name = row.structured.get("tool", "")
            if tool_name:
                tools.append(tool_name.lower())
        if not tools:
            tools = ["rag_read", "rag_write", "rag_search", "rag_ingest"]
        return tools

    def reason(
        self,
        obs:             "Observation",
        heartbeat_state: "AgentState",
        iteration:       int = 0,
    ) -> Plan:
        """
        Produce a Plan from observation.
        Falls back to rag_search if LLM unavailable or returns invalid JSON.
        """
        if self._llm is not None and self._llm.is_available():
            try:
                return self._llm_reason(obs, heartbeat_state, iteration)
            except Exception as e:
                log.warning("LLM reasoning failed (%s) — falling back to rag_search", e)

        return self._fallback_plan(obs, iteration)

    def _llm_reason(
        self,
        obs:             "Observation",
        heartbeat_state: "AgentState",
        iteration:       int,
    ) -> Plan:
        from llm.base import Message

        rag_context_str = "\n".join(
            f"[{r.get('id', '?')}] {r.get('text', '')[:200]}"
            for r in obs.rag_context[:5]
        )
        cited_ids = [r.get("id", "") for r in obs.rag_context[:5] if r.get("id")]

        user_content = (
            f"Goal/Input: {obs.input_text}\n\n"
            f"RAG context:\n{rag_context_str}\n\n"
            f"Decide what tool to call to make progress toward the goal."
        )

        system = self.SYSTEM_PROMPT_TEMPLATE.format(
            tools=", ".join(self._tools),
            memory_root=heartbeat_state.memory_root_hash[:16] + "...",
            last_event=heartbeat_state.last_event_id[:8] + "..." if heartbeat_state.last_event_id else "none",
        )

        response = self._llm.complete(
            messages=[Message(role="user", content=user_content)],
            system=system,
        )

        # Parse JSON plan
        plan_dict = self._parse_plan_json(response)
        return Plan(
            tool_name        = plan_dict.get("tool_name", "rag_search"),
            tool_args        = plan_dict.get("tool_args", {"query": obs.input_text}),
            reasoning        = plan_dict.get("reasoning", "LLM-generated plan"),
            cited_source_ids = plan_dict.get("cited_source_ids", cited_ids),
            iteration        = iteration,
        )

    def _parse_plan_json(self, response: str) -> dict:
        """Extract JSON from LLM response. Robust to markdown fencing."""
        import re
        # Strip ```json fences
        cleaned = re.sub(r"```(?:json)?|```", "", response).strip()
        # Find first {...} block
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        return {}

    def _fallback_plan(self, obs: "Observation", iteration: int) -> Plan:
        """Default plan: search RAG for the input text."""
        return Plan(
            tool_name        = "rag_search",
            tool_args        = {"query": obs.input_text, "n_results": 5},
            reasoning        = "Fallback: search RAG for context (no LLM available)",
            cited_source_ids = [],
            iteration        = iteration,
        )
