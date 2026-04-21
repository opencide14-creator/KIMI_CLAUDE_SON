"""Reactive Agent — reads config from REACT_LOOP.md.
MAX_LOOPS, PULSE_R_EVERY, system prompt structure — all from Markdown.
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

import httpx

from src.core.agents.soul import get_soul
from src.core.agents.memory import get_memory
from src.core.agents.md_loader import react_loop_config, soul_config, bound_config

T = TypeVar('T')

log = logging.getLogger("REACTIVE")


async def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0
) -> T:
    """Retry function with exponential backoff.

    Handles transient failures (network errors, rate limits, service unavailable)
    by retrying with exponential backoff: 1s, 2s, 4s, 8s...

    Args:
        func: Synchronous or async callable to retry
        max_retries: Maximum number of retry attempts (default 3)
        base_delay: Initial delay in seconds (default 1.0)
        max_delay: Maximum delay cap in seconds (default 30.0)

    Returns:
        The result of func() on success

    Raises:
        The last exception if all retries are exhausted
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            if asyncio.iscoroutinefunction(func):
                return await func()
            else:
                return func()
        except Exception as e:
            last_exception = e

            # Log transient error categories
            is_transient = (
                isinstance(e, httpx.HTTPError) or
                "rate" in str(e).lower() or
                "429" in str(e) or
                "503" in str(e) or
                "timeout" in str(e).lower() or
                "connection" in str(e).lower()
            )

            if attempt < max_retries - 1:
                delay = min(base_delay * (2 ** attempt), max_delay)
                log.warning(
                    "Attempt %d/%d failed (%s): %s. Retrying in %.1fs...",
                    attempt + 1, max_retries,
                    "transient" if is_transient else "permanent",
                    e, delay
                )
                await asyncio.sleep(delay)
            else:
                log.error("All %d retry attempts exhausted for %s", max_retries, func)

    raise last_exception

KIMI_ENDPOINT = "https://api.kimi.com/coding/v1/chat/completions"


@dataclass
class ReactPlan:
    tool:     str
    args:     Dict[str, Any]
    reason:   str
    sequence: int = 0

    def to_dict(self) -> Dict:
        return {"tool": self.tool, "args": self.args,
                "reason": self.reason, "sequence": self.sequence}


class ReactiveAgent:
    NAME = "REACTIVE"

    TOOLS = [
        {"type":"function","function":{"name":"nmap_scan","description":"Run nmap scan","parameters":{"type":"object","properties":{"target":{"type":"string"},"ports":{"type":"string","default":"1-1024"},"args":{"type":"string","default":"-sV -T4"}},"required":["target"]}}},
        {"type":"function","function":{"name":"proxy_control","description":"Start/stop SOVEREIGN proxy","parameters":{"type":"object","properties":{"action":{"type":"string","enum":["start","stop","status"]},"port":{"type":"integer"}},"required":["action"]}}},
        {"type":"function","function":{"name":"gateway_add_route","description":"Add AI routing rule","parameters":{"type":"object","properties":{"source_host":{"type":"string"},"target_url":{"type":"string"},"target_model":{"type":"string"},"inject_key":{"type":"string"}},"required":["source_host","target_url"]}}},
        {"type":"function","function":{"name":"vault_read","description":"Read credential from vault","parameters":{"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}}},
        {"type":"function","function":{"name":"read_file","description":"Read file from disk","parameters":{"type":"object","properties":{"path":{"type":"string"}},"required":["path"]}}},
        {"type":"function","function":{"name":"write_file","description":"Write file to disk","parameters":{"type":"object","properties":{"path":{"type":"string"},"content":{"type":"string"}},"required":["path","content"]}}},
        {"type":"function","function":{"name":"sovereign_status","description":"Get SOVEREIGN services status","parameters":{"type":"object","properties":{}}}},
        {"type":"function","function":{"name":"search_memory","description":"Search agent memory","parameters":{"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}}},
    ]

    def __init__(self, api_key: str, model: str = "kimi-for-coding",
                 tool_executor: Callable = None):
        self._api_key      = api_key
        self._model        = model
        self._executor     = tool_executor
        self._memory       = get_memory()
        self._soul         = get_soul()
        # Read config from Markdown — not from Python constants
        self._loop_md      = react_loop_config()
        self._max_loops    = self._loop_md.get_int("MAX_LOOPS", 12)
        self._pulse_r_every = self._loop_md.get_int("PULSE_R_EVERY_N_ACTIONS", 5)
        self._action_count = 0
        self._pulse_seq    = 0
        self._heartbeat    = None
        self._system_prompt = self._build_system_prompt()

    def reload_config(self):
        """Hot-reload REACT_LOOP.md without restart."""
        self._loop_md.reload()
        self._max_loops     = self._loop_md.get_int("MAX_LOOPS", 12)
        self._pulse_r_every = self._loop_md.get_int("PULSE_R_EVERY_N_ACTIONS", 5)
        self._soul.reload()
        self._system_prompt = self._build_system_prompt()
        log.info("ReactiveAgent config reloaded: max_loops=%d pulse_r_every=%d",
                 self._max_loops, self._pulse_r_every)

    def set_heartbeat(self, heartbeat):
        self._heartbeat = heartbeat

    def observe(self, user_input: str, hb_state: Dict) -> Dict:
        memory_context = ""
        if self._heartbeat:
            memory_context = self._heartbeat.get_memory_context(user_input)
        return {
            "user_input":      user_input,
            "memory_context":  memory_context,
            "heartbeat_state": hb_state,
            "timestamp":       datetime.now(timezone.utc).isoformat(),
        }

    async def reason(self, obs: Dict, messages: List[Dict]) -> Tuple[str, List]:
        system = self._system_prompt
        if obs.get("memory_context"):
            system += f"\n\n{obs['memory_context']}"
        kimi_messages = [{"role": "system", "content": system}] + messages
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        body = {
            "model":       self._model,
            "messages":    kimi_messages,
            "tools":       self.TOOLS,
            "tool_choice": "auto",
            "max_tokens":  2048,
        }
        self._memory.write_event(self.NAME, "REASON_START",
                                 {"query": obs["user_input"][:100]}, priority="MEDIUM")

        async def _call_kimi_api():
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(KIMI_ENDPOINT, json=body, headers=headers)
                if resp.status_code == 401:
                    raise RuntimeError("Invalid API key — check your Kimi API key in the vault or environment")
                if resp.status_code == 429:
                    raise RuntimeError("Kimi API rate limit exceeded — wait a moment and retry")
                if resp.status_code == 503:
                    raise RuntimeError("Kimi API service unavailable — check https://status.kimi.com")
                if resp.status_code != 200:
                    try:
                        err = resp.json().get("error", {}).get("message", resp.text[:200])
                    except Exception:
                        err = resp.text[:200]
                    raise RuntimeError(f"Kimi API error {resp.status_code}: {err}")
                return resp

        resp = await retry_with_backoff(_call_kimi_api, max_retries=3, base_delay=1.0, max_delay=30.0)
        data   = resp.json()
        choice = data["choices"][0]["message"]
        text   = choice.get("content", "") or ""
        tools  = choice.get("tool_calls", [])
        self._memory.write_event(self.NAME, "REASON_DONE",
                                 {"text_len": len(text), "tool_calls": len(tools)},
                                 priority="MEDIUM")
        return text, tools

    def decide(self, tool_calls: List[Dict]) -> List[ReactPlan]:
        plans = []
        for seq, tc in enumerate(tool_calls):
            fn = tc.get("function", {})
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            plans.append(ReactPlan(
                tool=fn.get("name", ""),
                args=args,
                reason="Kimi decided",
                sequence=self._action_count + seq,
            ))
        return plans

    def act(self, plan: ReactPlan) -> str:
        if not self._executor:
            return f"No executor for: {plan.tool}"
        self._action_count += 1
        if self._action_count % self._pulse_r_every == 0:
            self._send_pulse_r()
        try:
            result = self._executor(plan.tool, plan.args)
            return str(result)
        except Exception as e:
            return f"Error: {e}"

    def goal_achieved(self, result: str, goal: str = "") -> bool:
        done_signals = ["task complete", "done", "finished", "achieved", "success"]
        return any(s in result.lower() for s in done_signals)

    def _send_pulse_r(self):
        self._pulse_seq += 1
        pulse = {
            "protocol":    "HEARTBEAT/v1",
            "from":        "REACTIVE",
            "to":          "HEARTBEAT",
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "action_hash": f"action_{self._action_count}",
            "sequence":    self._pulse_seq,
        }
        if self._heartbeat:
            self._heartbeat.receive_pulse_r(pulse)

    def _build_system_prompt(self) -> str:
        """Build system prompt from Markdown content — not hardcoded string."""
        soul_md  = soul_config()
        bound_md = bound_config()

        # Pull law texts from SOUL.md
        laws_text = ""
        for law in self._soul.laws:
            laws_text += f"  {law.get('ID','?')}: {law.get('NAME','?')} — {law.get('TEXT','')}\n"

        # Pull identity from BOUND.md
        identity = bound_md.get_section("ARTICLE_I_IDENTITY") or {}

        return f"""You are SOVEREIGN Reactive Agent — part of the VEKIL-KAAN dual-agent system.

IDENTITY (from BOUND.md):
  Role: {identity.get('REACTIVE_AGENT', 'Action, Tool Use, reAct loop')}
  Partner: Heartbeat Agent — validates every action, writes every result to memory
  Together: {identity.get('TOGETHER', 'VEKIL-KAAN')}

SOUL LAWS (from SOUL.md v{self._soul.VERSION}):
{laws_text}
MEMORY:
  Shared RAG. Read before every REASON. Heartbeat writes after every ACT.
  Use search_memory tool to query past context.

TOOLS: nmap_scan, proxy_control, gateway_add_route, vault_read,
       read_file, write_file, sovereign_status, search_memory

Config: max_loops={self._max_loops}, pulse_r_every={self._pulse_r_every} actions
Soul hash: {self._soul.hash()}"""
