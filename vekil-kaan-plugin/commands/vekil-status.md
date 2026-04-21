---
name: vekil-status
description: >
  Display VEKIL-KAAN dual-agent system health and status.
  Shows agent states, memory stats, pulse history, and law enforcement status.
allowed-tools: ["Bash", "Read"]
---

# VEKIL-KAAN Status

Display comprehensive system health.

## Information Displayed

### Agent States
```
┌─────────────────────────────────────────┐
│  REACTIVE AGENT                         │
│  Status: ACTIVE / SAFE_MODE / HALTED   │
│  Cycle: 1427                            │
│  Last PULSE_R: 2.3s ago                │
│  Memory Hash: a1b2c3...                │
├─────────────────────────────────────────┤
│  HEARTBEAT AGENT                        │
│  Status: ACTIVE / MOURNING / SILENT    │
│  Cycle: 1427                            │
│  Last PULSE_H: 12.1s ago               │
│  Memory Hash: a1b2c3...                │
└─────────────────────────────────────────┘
```

### Memory Substrate
```
ChromaDB Collections: 4
- obsidian_knowledge: ... docs
- agent_events:       ... events
- law_registry:       ... laws
- session_context:    ... entries

SQLite Tables: 5
- events:        ... rows
- audit_log:     ... rows
- snapshots:     ... rows
- pulses:        ... rows
- escape_attempts: ... rows
```

### Law Enforcement
```
SOUL Laws:    7 loaded | 0 violations
BOUND Pact:   Active | Brotherhood bonded
Registry:     Sealed ✓ | Hash verified
```

### Pulse History
```
Last 5 pulses:
[14:23:01] PULSE_H | Hash: a1b2... | ✓
[14:22:46] PULSE_R | Actions: 5   | ✓
[14:22:31] PULSE_H | Hash: a1b2... | ✓
[14:22:16] PULSE_R | Actions: 5   | ✓
[14:22:01] PULSE_H | Hash: a1b2... | ✓
```

## Command

```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/status.py
```
