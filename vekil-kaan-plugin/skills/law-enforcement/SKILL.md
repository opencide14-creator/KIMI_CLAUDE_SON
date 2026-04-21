---
name: law-enforcement
description: >
  Parse, verify, and enforce VEKIL-KAAN constitutional laws at runtime.
  Use when loading laws, checking compliance, verifying seals, or managing the law registry.
  Trigger phrases: "load laws", "verify constitution", "law registry",
  "seal check", "SOUL laws", "BOUND pact", "constitutional enforcement".
metadata:
  author: KRAL
  version: "4.0"
---

# Law Enforcement Skill

## Purpose

Manage the VEKIL-KAAN constitutional law system:
- Parse markdown laws into structured objects
- Build law registry with indices
- Cryptographically seal with Ed25519
- Runtime enforcement via LawEnforcer

## Law Files

| File | Laws | Type |
|------|------|------|
| `laws/SOUL.md` | 7 soul laws | Immutable constitutional |
| `laws/BOUND.md` | 6 brotherhood articles | Pact/contract |
| `laws/MEMORY.md` | Memory architecture | Protocol |
| `laws/HEARTBEAT.md` | Pulse protocol | Protocol |
| `laws/REACT_LOOP.md` | Dual loop spec | Protocol |
| `laws/TOOL_USE.md` | Tool pool | Reference |

## Law Parsing

Uses `markdown-it-py` to parse structured Markdown:
```python
parser = MarkdownLawParser()
laws = parser.parse_file("laws/SOUL.md")
```

Law ID scheme:
```
{FILE_STEM}/{H2_SLUG}[/{H3_SLUG}][/ROW_{N}|/OATH|/CODE]
```

Law types: RULE, LIMIT, PROTOCOL, OATH, CONSTRAINT, SEQUENCE, TABLE_ROW, REFERENCE

## Registry Operations

### Build
```python
registry = LawRegistry()
registry.load_directory("laws/")
```

### Seal (Boot Time)
```python
registry.seal(private_key)
# Computes aggregate SHA-256 + Ed25519 signature
```

### Verify (Runtime)
```python
registry.verify_integrity(public_key)
# Returns True/False, detects tampering
```

### Query
```python
registry.get_by_id("SOUL/LAW_1")
registry.query_by_tag("simulation")
registry.get_soul_laws()
registry.get_timing_limit("REACT_LOOP/MAX_LATENCY_MS")
```

## Enforcement

### Tool Call Validation
```python
enforcer = LawEnforcer(registry)
enforcer.check_tool_call("rag_write", args)
# Raises SoulLawViolation if blocked
```

### Simulation Detection
```python
enforcer.check_simulation(code_text)
# Regex: mock|fake|simulate|as-if
# Raises SimulationDetected
```

### Brotherhood Check
```python
enforcer.check_brotherhood(message_text)
# Regex: you must|i order|you shall
# Raises BrotherhoodViolation
```

### Latency Enforcement
```python
enforcer.check_latency(elapsed_ms)
# Max 500ms for Reactive actions
# Raises LatencyViolation
```

## Cryptographic Hierarchy

```
KRAL Ed25519 Keypair (hardcoded fingerprint)
    ├── Signs: LawRegistry.seal() [boot]
    ├── Signs: BrotherhoodOath [boot]
    └── Verifies: Law integrity [runtime]

HMAC-SHA256 (per-event, from config secret)
    ├── Signs: MemoryEvent.write()
    └── Verifies: MemoryEvent.read_by_id()
```

## Hot Reload

Laws support hot-reload without restart:
1. Edit `.md` file
2. Registry detects change via file watcher
3. Re-parse, re-build indices, re-seal
4. Agents pick up new laws on next cycle
