"""DualReActLoop — reads step definitions from REACT_LOOP.md.
MAX_LOOPS, MAX_SOUL_REJECTIONS, step names — all from Markdown.
"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import AsyncIterator, Callable, Dict, List

from src.core.agents.heartbeat_agent import HeartbeatAgent
from src.core.agents.reactive_agent import ReactiveAgent, ReactPlan
from src.core.agents.soul import SoulCheckResult
from src.core.agents.memory import get_memory
from src.core.agents.md_loader import react_loop_config

log = logging.getLogger("DUAL_LOOP")


@dataclass
class LoopEvent:
    kind:    str   # text|tool_call|tool_result|pulse|verify|error|done
    content: str
    agent:   str   # REACTIVE|HEARTBEAT|SYSTEM
    meta:    Dict  = field(default_factory=dict)


class DualReActLoop:
    """
    Joint cycle from REACT_LOOP.md.
    Reads MAX_LOOPS and MAX_SOUL_REJECTIONS from Markdown — not hardcoded.
    """

    def __init__(self, reactive: ReactiveAgent, heartbeat: HeartbeatAgent,
                 tool_executor: Callable):
        self._r       = reactive
        self._hb      = heartbeat
        self._memory  = get_memory()
        self._md      = react_loop_config()
        # Config from Markdown
        self._max_loops    = self._md.get_int("MAX_LOOPS", 12)
        self._max_rejects  = self._md.get_int("MAX_SOUL_REJECTIONS", 3)
        # Wire agents
        self._r.set_heartbeat(self._hb)
        self._r._executor = tool_executor
        self._messages: List[Dict] = []

    def boot(self) -> bool:
        log.info("DualReActLoop booting — MAX_LOOPS=%d  MAX_REJECTS=%d  (from REACT_LOOP.md)",
                 self._max_loops, self._max_rejects)
        ok = self._hb.boot()
        if ok:
            log.info("DualReActLoop READY")
        return ok

    def reload_config(self):
        """Hot-reload REACT_LOOP.md. No restart."""
        self._md.reload()
        self._max_loops   = self._md.get_int("MAX_LOOPS", 12)
        self._max_rejects = self._md.get_int("MAX_SOUL_REJECTIONS", 3)
        self._r.reload_config()
        self._hb.reload_config()
        log.info("DualReActLoop reloaded: max_loops=%d max_rejects=%d",
                 self._max_loops, self._max_rejects)

    async def run(self, user_input: str) -> AsyncIterator[LoopEvent]:
        self._messages.append({"role": "user", "content": user_input})
        loop_count   = 0
        reject_count = 0
        final_text   = ""

        while loop_count < self._max_loops:
            loop_count += 1

            # STEP 1: OBSERVE
            hb_state = self._hb.sense()
            obs      = self._r.observe(user_input, hb_state)

            if not hb_state["alive"]:
                yield LoopEvent("error", "Heartbeat not alive — pausing", "SYSTEM")
                break

            # STEP 2: REASON (Reactive → Kimi)
            yield LoopEvent("text", "⟳ Reasoning…", "SYSTEM")
            try:
                text, tool_calls = await self._r.reason(obs, self._messages)
            except Exception as e:
                yield LoopEvent("error", f"Kimi API error: {e}", "REACTIVE")
                break

            if text:
                final_text = text
                yield LoopEvent("text", text, "REACTIVE")

            if not tool_calls:
                self._messages.append({"role": "assistant", "content": text})
                break

            self._messages.append({
                "role": "assistant", "content": text, "tool_calls": tool_calls,
            })

            tool_results_for_msg = []
            for tc in tool_calls:
                fn   = tc.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}

                plan = ReactPlan(tool=name, args=args, reason="Kimi decided")

                # STEP 3: VERIFY (Heartbeat)
                soul_result: SoulCheckResult = self._hb.verify(plan.to_dict())
                if not soul_result.passed:
                    reject_count += 1
                    yield LoopEvent("verify",
                        f"⚠ SOUL VIOLATION [{soul_result.violated_law}]: {soul_result.reason}",
                        "HEARTBEAT", meta={"law": soul_result.violated_law})
                    if reject_count >= self._max_rejects:
                        yield LoopEvent("error",
                            f"Max soul rejections ({self._max_rejects}) reached — abort",
                            "HEARTBEAT")
                        return
                    self._messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": f"REJECTED by SOUL: {soul_result.reason}",
                    })
                    continue

                yield LoopEvent("verify", f"✅ Verified: {name}", "HEARTBEAT",
                                meta={"tool": name})

                # STEP 4: ACT (Reactive)
                args_preview = ", ".join(f"{k}={v!r}" for k, v in list(args.items())[:3])
                yield LoopEvent("tool_call", f"⚙ {name}({args_preview})", "REACTIVE",
                                meta={"tool": name, "args": args})

                result = self._r.act(plan)
                yield LoopEvent("tool_result", result[:600], "REACTIVE",
                                meta={"tool": name})

                # STEP 5: INGEST (Heartbeat)
                self._hb.ingest(name, args, result)
                yield LoopEvent("pulse", f"💓 Ingested: {name}", "HEARTBEAT")

                tool_results_for_msg.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

            self._messages.extend(tool_results_for_msg)

            # STEP 6: LOOP
            if self._r.goal_achieved(final_text, user_input):
                yield LoopEvent("done", "✅ Goal achieved", "SYSTEM")
                break

        yield LoopEvent("done", final_text or "Done.", "SYSTEM")

    def new_conversation(self):
        self._messages.clear()
        self._memory.write_event("SYSTEM", "NEW_CONVERSATION", {})

    @property
    def memory(self):
        return self._memory

    @property
    def heartbeat(self) -> HeartbeatAgent:
        return self._hb

    @property
    def reactive(self) -> ReactiveAgent:
        return self._r
