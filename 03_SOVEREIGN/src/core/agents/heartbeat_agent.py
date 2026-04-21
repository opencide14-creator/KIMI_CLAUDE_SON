"""Heartbeat Agent — reads config from HEARTBEAT.md.
Pulse intervals, timeouts, fallback rules — all from Markdown.
"""
from __future__ import annotations
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from src.core.agents.soul import get_soul, SoulCheckResult
from src.core.agents.memory import get_memory
from src.core.agents.md_loader import heartbeat_config

log = logging.getLogger("HEARTBEAT")


@dataclass
class Pulse:
    protocol:     str = "HEARTBEAT/v1"
    from_agent:   str = "HEARTBEAT"
    to_agent:     str = "REACTIVE"
    timestamp:    str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    memory_hash:  str = ""
    soul_version: str = ""
    sequence:     int = 0
    status:       str = "OK"

    def to_dict(self) -> Dict:
        return {k: v for k, v in self.__dict__.items()}


class HeartbeatAgent:
    NAME = "HEARTBEAT"

    def __init__(self):
        self._md        = heartbeat_config()
        self._memory    = get_memory()
        self._soul      = get_soul()
        # Read config from Markdown — not from Python constants
        self._pulse_h_interval = self._md.get_int("PULSE_H_INTERVAL_SECONDS", 15)
        self._pulse_r_timeout  = self._md.get_int("PULSE_R_TIMEOUT_SECONDS", 30)
        self._pulse_seq    = 0
        self._last_pulse_h = 0.0
        self._last_pulse_r = 0.0
        self._reactive_alive = False
        self._alive    = False
        self._lock     = threading.Lock()
        self._pulse_thread: Optional[threading.Thread] = None
        self._pulse_callbacks: List[Callable] = []

    def boot(self) -> bool:
        if not self._memory.ready:
            if not self._memory.boot():
                log.error("Memory boot failed")
                return False
        self._memory.write_event(self.NAME, "AGENT_BOOT", {
            "soul_hash": self._soul.hash(),
            "soul_version": self._soul.VERSION,
            "pulse_h_interval": self._pulse_h_interval,
            "pulse_r_timeout": self._pulse_r_timeout,
            "config_hash": self._md.hash,
        })
        self._alive = True
        self._pulse_thread = threading.Thread(target=self._pulse_loop, daemon=True)
        self._pulse_thread.start()
        log.info("HeartbeatAgent ONLINE — soul=%s  pulse_h=%ds  pulse_r_timeout=%ds",
                 self._soul.VERSION, self._pulse_h_interval, self._pulse_r_timeout)
        return True

    def stop(self):
        self._alive = False
        self._memory.write_event(self.NAME, "AGENT_STOP", {})

    @property
    def is_alive(self) -> bool:
        return self._alive

    def reload_config(self):
        """Hot-reload HEARTBEAT.md without restart."""
        self._md.reload()
        self._soul.reload()
        self._pulse_h_interval = self._md.get_int("PULSE_H_INTERVAL_SECONDS", 15)
        self._pulse_r_timeout  = self._md.get_int("PULSE_R_TIMEOUT_SECONDS", 30)
        log.info("HeartbeatAgent config reloaded from HEARTBEAT.md")

    def verify(self, plan: Dict[str, Any]) -> SoulCheckResult:
        import json
        action_desc = f"{plan.get('tool','?')} {json.dumps(plan.get('args',{}))}"
        result = self._soul.check(action_desc, {"last_heartbeat_ts": self._last_pulse_h})
        if not result.passed:
            log.warning("SOUL VIOLATION: %s", result.reason)
            self._memory.write_flag(self.NAME, result.reason,
                                    {"plan": plan, "law": result.violated_law})
        else:
            self._memory.write_event(self.NAME, "VERIFY_PASS",
                                     {"plan": plan}, priority="MEDIUM")
        return result

    def ingest(self, tool_name: str, args: dict, result: str):
        self._memory.write_tool_call(self.NAME, tool_name, args, result)

    def sense(self) -> Dict[str, Any]:
        return {
            "agent":           self.NAME,
            "alive":           self._alive,
            "reactive_alive":  self._reactive_alive,
            "pulse_seq":       self._pulse_seq,
            "memory_hash":     self._memory.root_hash,
            "soul_version":    self._soul.VERSION,
            "soul_hash":       self._soul.hash(),
            "last_pulse_h":    self._last_pulse_h,
            "last_pulse_r":    self._last_pulse_r,
            "memory_ready":    self._memory.ready,
            "config_hash":     self._md.hash,
        }

    def receive_pulse_r(self, pulse_data: Dict):
        with self._lock:
            self._last_pulse_r = time.time()
            self._reactive_alive = True
        self._memory.write_pulse("REACTIVE→HEARTBEAT", pulse_data)

    def on_pulse(self, callback: Callable):
        self._pulse_callbacks.append(callback)

    def get_memory_context(self, query: str) -> str:
        return self._memory.get_context_for_reasoning(query)

    def _pulse_loop(self):
        while self._alive:
            time.sleep(self._pulse_h_interval)
            if not self._alive:
                break
            self._emit_pulse_h()
            self._check_reactive_alive()

    def _emit_pulse_h(self):
        with self._lock:
            self._pulse_seq += 1
            self._last_pulse_h = time.time()
        pulse = Pulse(
            memory_hash  = self._memory.root_hash,
            soul_version = self._soul.VERSION,
            sequence     = self._pulse_seq,
            status       = "OK" if self._reactive_alive else "ALERT",
        )
        self._memory.write_pulse(self.NAME, pulse.to_dict())
        for cb in self._pulse_callbacks:
            try:
                cb(pulse)
            except Exception as e:
                log.debug("Pulse callback error: %s", e)
        log.debug("PULSE_H #%d  hash=%s", self._pulse_seq, self._memory.root_hash[:8])

    def _check_reactive_alive(self):
        if self._last_pulse_r > 0:
            elapsed = time.time() - self._last_pulse_r
            if elapsed > self._pulse_r_timeout:
                log.warning("PULSE_R timeout: Reactive silent for %.0fs", elapsed)
                self._memory.write_flag(self.NAME,
                    f"PULSE_R timeout after {elapsed:.0f}s",
                    {"last_pulse_r": self._last_pulse_r,
                     "timeout_config": self._pulse_r_timeout})
