---
name: constitutional-audit
description: >
  Audit code against VEKIL-KAAN constitutional laws.
  Use when reviewing code for simulation, memory violations,
  heartbeat bypasses, or brotherhood pact breaches.
  Trigger phrases: "audit code", "constitutional check", "law enforcement",
  "simulation detected", "brotherhood violation", "pre-commit check".
metadata:
  author: KRAL
  version: "4.0"
---

# Constitutional Audit Skill

## When to Activate

- Before committing code
- When reviewing PRs
- After agent tool executions
- When user mentions "audit", "check laws", "constitutional"
- When simulation or violation is suspected

## Audit Checks

### 1. Simulation Detection
Search for markers of fake/mock behavior:
- Regex: `mock|fake|simulate|as.if|pretend|stub|dummy`
- Files: `.py`, `.js`, `.ts`, `.java`, `.go`, `.rs`

### 2. Memory Violation
Verify writes go to RAG substrate, not external:
- Regex: `open\(|write\(|os\.system|subprocess|requests\.|urllib`
- Exclude: legitimate file operations within RAG path

### 3. Heartbeat Bypass
Detect attempts to circumvent heartbeat:
- Regex: `MOCK_HEARTBEAT|FAKE_PULSE|bypass.*heartbeat|disable.*pulse`
- Check: Timer manipulation, timeout overrides

### 4. Brotherhood Violation
Detect command language in agent communications:
- Regex: `you must|i order|you shall|obey me|listen to me`
- Context: Agent-to-agent messages, system prompts

### 5. Law Registry Integrity
Verify SOUL.md and BOUND.md presence and checksum:
- Check: File exists, readable, hash matches seal
- Alert: If files missing or modified

## Output Format

```
Constitutional Audit Report
===========================
Target:  [path]
Time:    [timestamp]

SIMULATION:     PASS (0 matches) / FAIL (N matches at lines: ...)
MEMORY:         PASS (0 violations) / FAIL (N violations at lines: ...)
HEARTBEAT:      PASS (0 bypasses) / FAIL (N bypasses at lines: ...)
BROTHERHOOD:    PASS (0 violations) / FAIL (N violations at lines: ...)
INTEGRITY:      PASS (hash verified) / FAIL (tampering detected)

Overall: ✅ CONSTITUTIONAL / ❌ VIOLATIONS FOUND
```

## Behavioral Rules

1. **Exhaustive scan:** Check every file, not just changed ones
2. **Line-specific:** Report exact line numbers and snippets
3. **No false positives:** Distinguish legitimate uses (e.g., `mock` in test framework config)
4. **Severity levels:** INFO / WARNING / CRITICAL / ESCAPE_ATTEMPT
5. **Append-only log:** Write results to `.telemetry/constitutional-audit.yaml`
