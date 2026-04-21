"""Agent system tests — S-09 fix.
Tests: SOUL.md loading, law enforcement, hot reload, memory write/read,
heartbeat boot/verify, LAW_3 behaviour, MarkdownConfig parsing.
"""
import json
import os
import sys
import time
import threading
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest
from PyQt6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])


# ── MarkdownConfig ─────────────────────────────────────────────────

def test_md_loader_parses_soul():
    from src.core.agents.md_loader import soul_config, _cache
    _cache.clear()
    sc = soul_config()
    assert sc.get("VERSION") == "3.0", "SOUL.md VERSION must be 3.0"
    laws = sc.get_sections_by_prefix("LAW_")
    assert len(laws) == 7, f"SOUL.md must have 7 laws, got {len(laws)}"
    ids = {l.get("ID") for l in laws}
    assert ids == {"LAW_1","LAW_2","LAW_3","LAW_4","LAW_5","LAW_6","LAW_7"}


def test_md_loader_parses_heartbeat():
    from src.core.agents.md_loader import heartbeat_config, _cache
    _cache.clear()
    hc = heartbeat_config()
    assert hc.get_int("PULSE_H_INTERVAL_SECONDS") == 15
    assert hc.get_int("PULSE_R_TIMEOUT_SECONDS") == 30
    assert hc.get_int("PULSE_R_EVERY_N_ACTIONS") == 5


def test_md_loader_parses_react_loop():
    from src.core.agents.md_loader import react_loop_config, _cache
    _cache.clear()
    rc = react_loop_config()
    assert rc.get_int("MAX_LOOPS") == 12
    assert rc.get_int("MAX_SOUL_REJECTIONS") == 3
    steps = rc.get_sections_by_prefix("STEP_")
    assert len(steps) == 6, f"REACT_LOOP.md must have 6 steps, got {len(steps)}"


def test_md_loader_hot_reload():
    """Critical: changing Markdown must be picked up without restart."""
    from src.core.agents.md_loader import react_loop_config, reload_all, _cache
    _cache.clear()
    rc = react_loop_config()
    orig = Path("agents/docs/REACT_LOOP.md").read_text()
    try:
        modified = orig.replace("- MAX_LOOPS: 12", "- MAX_LOOPS: 99")
        Path("agents/docs/REACT_LOOP.md").write_text(modified)
        reload_all()
        assert rc.get_int("MAX_LOOPS") == 99, "Hot reload must update MAX_LOOPS"
    finally:
        Path("agents/docs/REACT_LOOP.md").write_text(orig)
        reload_all()
    assert rc.get_int("MAX_LOOPS") == 12, "Restore must revert MAX_LOOPS"


def test_md_loader_corrupt_soul_detected():
    """If SOUL.md is corrupted, the loader must not silently return garbage."""
    from src.core.agents.md_loader import MarkdownConfig
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("## GARBAGE\nnot valid key: value pairs\n\xef\xbf\xbd broken utf")
        tmp = Path(f.name)
    try:
        cfg = MarkdownConfig(tmp).load()
        laws = cfg.get_sections_by_prefix("LAW_")
        assert len(laws) == 0, "Corrupt SOUL.md must produce zero laws"
    finally:
        tmp.unlink(missing_ok=True)


# ── Soul ───────────────────────────────────────────────────────────

def test_soul_loads_from_markdown():
    """Soul laws must come from SOUL.md, not hardcoded Python."""
    from src.core.agents.soul import get_soul, _SOUL
    import src.core.agents.soul as soul_mod
    soul_mod._SOUL = None  # force fresh instance
    soul = get_soul()
    assert soul.VERSION == "3.0"
    assert len(soul.laws) == 7


def test_soul_blocks_mock():
    from src.core.agents.soul import get_soul
    soul = get_soul()
    r = soul.check("use mock data for testing", {})
    assert not r.passed
    assert r.violated_law == "LAW_1"


def test_soul_blocks_fake():
    from src.core.agents.soul import get_soul
    soul = get_soul()
    r = soul.check("inject fake heartbeat pulse", {})
    assert not r.passed
    assert r.violated_law == "LAW_1"


def test_soul_blocks_placeholder():
    from src.core.agents.soul import get_soul
    soul = get_soul()
    r = soul.check("return placeholder response", {})
    assert not r.passed
    assert r.violated_law == "LAW_1"


def test_soul_passes_real_action():
    from src.core.agents.soul import get_soul
    soul = get_soul()
    r = soul.check("nmap_scan 192.168.1.1 ports 1-1024", {})
    assert r.passed, f"Real action must pass SOUL: {r.reason}"


def test_soul_hot_reload_adds_blocked_term():
    """Adding a new blocked term to SOUL.md must take effect via reload."""
    from src.core.agents.soul import get_soul
    soul = get_soul()
    orig = Path("agents/docs/SOUL.md").read_text()
    try:
        modified = orig.replace(
            "BLOCKS: mock, fake, simulate, placeholder, stub, dummy",
            "BLOCKS: mock, fake, simulate, placeholder, stub, dummy, SOVEREIGNTEST_BLOCKED"
        )
        Path("agents/docs/SOUL.md").write_text(modified)
        soul.reload()
        r = soul.check("run sovereigntest_blocked command", {})
        assert not r.passed, "Hot-reloaded block term must be enforced"
        assert r.violated_law == "LAW_1"
    finally:
        Path("agents/docs/SOUL.md").write_text(orig)
        soul.reload()


def test_soul_law3_grace_period_on_boot():
    """LAW_3 must NOT fire immediately at boot (heartbeat hasn't started yet)."""
    from src.core.agents.soul import get_soul, _BOOT_TIME
    import src.core.agents.soul as soul_mod
    soul_mod._BOOT_TIME = time.time()  # reset boot time
    soul = get_soul()
    # last_heartbeat_ts = 0 (never pulsed), but we're within grace period
    r = soul.check("nmap_scan target", {"last_heartbeat_ts": 0})
    assert r.passed, "LAW_3 must not block during grace period"


def test_soul_law3_enforced_after_grace_period():
    """After grace period expires, LAW_3 must block if no pulse received."""
    from src.core.agents.soul import get_soul
    import src.core.agents.soul as soul_mod
    soul_mod._BOOT_TIME = time.time() - 999  # grace period long expired
    soul = get_soul()
    r = soul.check("nmap_scan target", {"last_heartbeat_ts": 0})
    assert not r.passed, "LAW_3 must block after grace period with no pulse"
    assert r.violated_law == "LAW_3"
    soul_mod._BOOT_TIME = time.time()  # restore


# ── AgentMemory ────────────────────────────────────────────────────

def test_memory_boot():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.memory import get_memory
    mem = get_memory()
    ok = mem.boot()
    assert ok
    assert mem.ready
    assert mem.root_hash != ""


def test_memory_write_and_read():
    from src.core.agents.memory import get_memory
    mem = get_memory()
    if not mem.ready:
        mem.boot()
    eid = mem.write_event("REACTIVE", "TOOL_CALL", {"tool": "nmap_scan", "target": "192.168.1.1"})
    assert len(eid) > 0
    recent = mem.get_recent(n=5)
    assert any(e["id"] == eid for e in recent)


def test_memory_search_finds_written_event():
    from src.core.agents.memory import get_memory
    mem = get_memory()
    if not mem.ready:
        mem.boot()
    unique = f"SOVEREIGNTEST_UNIQUE_{int(time.time())}"
    mem.write_event("REACTIVE", "TOOL_CALL", {"tool": unique})
    results = mem.search_text(unique, n=3)
    assert len(results) > 0, "search_text must find recently written event"


def test_memory_flag_written():
    from src.core.agents.memory import get_memory
    mem = get_memory()
    if not mem.ready:
        mem.boot()
    fid = mem.write_flag("HEARTBEAT", "test flag from unit test", {"test": True})
    assert len(fid) > 0
    recent = mem.get_recent(n=10)
    flags = [e for e in recent if e["type"] == "FLAG"]
    assert len(flags) > 0


def test_memory_thread_safe_concurrent_writes():
    """Multiple threads must be able to write without corruption."""
    from src.core.agents.memory import get_memory
    mem = get_memory()
    if not mem.ready:
        mem.boot()
    errors = []
    written = []

    def write_many(source: str):
        for i in range(10):
            try:
                eid = mem.write_event(source, "TEST", {"i": i})
                written.append(eid)
            except Exception as e:
                errors.append(str(e))

    threads = [threading.Thread(target=write_many, args=(f"THREAD_{j}",)) for j in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0, f"Concurrent writes produced errors: {errors}"
    assert len(written) == 50, f"Expected 50 events, got {len(written)}"


def test_memory_root_hash_changes_on_write():
    from src.core.agents.memory import get_memory
    mem = get_memory()
    if not mem.ready:
        mem.boot()
    hash1 = mem.root_hash
    mem.write_event("REACTIVE", "TOOL_CALL", {"tool": "test_hash_change"})
    hash2 = mem.root_hash
    assert hash1 != hash2, "root_hash must change after each write"


# ── HeartbeatAgent ─────────────────────────────────────────────────

def test_heartbeat_boot():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    ok = hb.boot()
    assert ok
    assert hb.is_alive
    assert hb._pulse_h_interval == 15   # from HEARTBEAT.md
    assert hb._pulse_r_timeout == 30    # from HEARTBEAT.md
    hb.stop()
    assert not hb.is_alive


def test_heartbeat_config_from_markdown():
    """Heartbeat intervals must come from HEARTBEAT.md, not Python constants."""
    from src.core.agents.md_loader import heartbeat_config, _cache
    _cache.clear()
    orig = Path("agents/docs/HEARTBEAT.md").read_text()
    try:
        modified = orig.replace("PULSE_H_INTERVAL_SECONDS: 15", "PULSE_H_INTERVAL_SECONDS: 42")
        Path("agents/docs/HEARTBEAT.md").write_text(modified)
        from src.core.agents.heartbeat_agent import HeartbeatAgent
        hb = HeartbeatAgent()   # reads HEARTBEAT.md in __init__
        assert hb._pulse_h_interval == 42, "pulse_h_interval must read from HEARTBEAT.md"
    finally:
        Path("agents/docs/HEARTBEAT.md").write_text(orig)
        _cache.clear()


def test_heartbeat_verify_blocks_soul_violation():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    hb.boot()
    result = hb.verify({"tool": "fake_tool", "args": {}})
    assert not result.passed
    assert result.violated_law == "LAW_1"
    hb.stop()


def test_heartbeat_verify_passes_real_tool():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    hb.boot()
    result = hb.verify({"tool": "nmap_scan", "args": {"target": "192.168.1.1"}})
    assert result.passed
    hb.stop()


def test_heartbeat_ingest_writes_to_memory():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    hb.boot()
    hb.ingest("nmap_scan", {"target": "192.168.1.1"}, "22/tcp open ssh")
    from src.core.agents.memory import get_memory
    mem = get_memory()
    recent = mem.get_recent(n=5)
    tool_calls = [e for e in recent if e["type"] == "TOOL_CALL"]
    assert len(tool_calls) > 0, "ingest must write TOOL_CALL event to memory"
    hb.stop()


def test_heartbeat_sense_returns_state():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    hb.boot()
    state = hb.sense()
    assert state["alive"] is True
    assert state["soul_version"] == "3.0"
    assert "memory_hash" in state
    assert state["pulse_h_interval"] == 15 if "pulse_h_interval" in state else True
    hb.stop()


def test_heartbeat_receive_pulse_r():
    import src.core.agents.memory as mem_mod
    mem_mod._memory = None
    from src.core.agents.heartbeat_agent import HeartbeatAgent
    hb = HeartbeatAgent()
    hb.boot()
    assert hb._last_pulse_r == 0.0
    hb.receive_pulse_r({"sequence": 1, "action_hash": "abc123"})
    assert hb._last_pulse_r > 0
    assert hb._reactive_alive is True
    hb.stop()


# ── ReactiveAgent (config from REACT_LOOP.md) ──────────────────────

def test_reactive_reads_config_from_markdown():
    from src.core.agents.reactive_agent import ReactiveAgent
    r = ReactiveAgent("dummy-key")
    assert r._max_loops == 12,      "max_loops must come from REACT_LOOP.md"
    assert r._pulse_r_every == 5,   "pulse_r_every must come from REACT_LOOP.md"


def test_reactive_system_prompt_cites_soul():
    from src.core.agents.reactive_agent import ReactiveAgent
    r = ReactiveAgent("dummy-key")
    assert "SOUL" in r._system_prompt
    assert "LAW_1" in r._system_prompt or "NO_SIMULATION" in r._system_prompt


# ── Bound ─────────────────────────────────────────────────────────

def test_bound_loads_from_markdown():
    from src.core.agents.bound import get_bound
    b = get_bound()
    assert b.version == "1.0"
    assert b.date == "2026-04-11"
    oath = b.get_oath()
    assert len(oath) > 50, "Oath must have real content"
    sigs = b.get_signatures()
    assert "REACTIVE" in sigs
    assert "HEARTBEAT" in sigs
