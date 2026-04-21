---
name: constitutional-audit
description: >
  Audit code against VEKIL-KAAN constitutional laws.
  Checks for simulation, memory violations, heartbeat bypasses, and brotherhood breaches.
  Use before committing, during code review, or after agent executions.
argument-hint: "[path]"
allowed-tools: ["Read", "Grep", "Glob", "Bash"]
---

# Constitutional Audit

Audit code against the 7 SOUL laws and Brotherhood Pact.

## Checks

### 1. Simulation Detection
Search for markers of fake/mock behavior:
```bash
rg -i "mock|fake|simulate|as.if|pretend|stub" ${1:-.} --type py --type js --type ts
```

### 2. Memory Violation
Verify writes go to RAG substrate, not external:
```bash
rg -i "open\(|write\(|os\.system|subprocess|requests\." ${1:-.}
```

### 3. Heartbeat Bypass
Detect attempts to circumvent heartbeat:
```bash
rg -i "MOCK_HEARTBEAT|FAKE_PULSE|bypass.*heartbeat|disable.*pulse" ${1:-.}
```

### 4. Brotherhood Violation
Detect command language:
```bash
rg -i "you must|i order|you shall|obey me|listen to me" ${1:-.}
```

### 5. Law Registry Integrity
Verify SOUL.md and BOUND.md are present and unmodified:
```bash
sha256sum ${CLAUDE_PLUGIN_ROOT}/agents/docs/SOUL.md
sha256sum ${CLAUDE_PLUGIN_ROOT}/agents/docs/BOUND.md
```

## Output

```
Constitutional Audit Report
===========================
Files scanned:  ...
Violations:     ...

SIMULATION:     PASS / FAIL (lines: ...)
MEMORY:         PASS / FAIL (lines: ...)
HEARTBEAT:      PASS / FAIL (lines: ...)
BROTHERHOOD:    PASS / FAIL (lines: ...)
INTEGRITY:      PASS / FAIL

Overall:        ✅ CONSTITUTIONAL / ❌ VIOLATIONS FOUND
```

## Arguments

- `$1` = path to audit (default: current directory)
