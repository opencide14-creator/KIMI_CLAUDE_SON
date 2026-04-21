---
name: nano-flawless-audit
description: >
  Audit code for 99.8% NANO_FLAWLESS quality.
  Checks syntax, security, constitutional compliance, simulation detection.
  Use when: "nano flawless", "quality check", "99.8%", "code audit",
  "flawless code", "perfect code", "zero defects", "pre-commit check".
metadata:
  author: KRAL
  version: "4.0"
---

# NANO FLAWLESS Audit Skill

## Purpose

Ensure every code artifact meets the 99.8% quality threshold.

## Audit Dimensions

### 1. Syntax Correctness
- Python: `py_compile` or `ast.parse`
- JavaScript: `node --check` or ESLint
- TypeScript: `tsc --noEmit`

### 2. Security
- No hardcoded secrets (API keys, passwords, tokens)
- No dangerous imports (pickle, marshal, exec, eval)
- No path traversal vulnerabilities
- No SQL injection patterns

### 3. Constitutional Compliance
- LAW_1: No simulation markers (mock, fake, simulate)
- LAW_3: Heartbeat gates present in agent code
- LAW_6: No external modification vectors
- BOUND Article II: No command language

### 4. Code Quality
- No bare except clauses
- Proper error handling
- Type hints (Python) or JSDoc (JS)
- Consistent naming conventions

### 5. Performance
- No N+1 queries
- No busy-waiting loops
- Proper resource cleanup

## Scoring

```
Score = 1.0 - (defect_penalty)
  CRITICAL: -0.10
  HIGH:     -0.05
  MEDIUM:   -0.02
  LOW:      -0.01

Target: ≥ 0.998
```

## Auto-Trigger Conditions

- File saved with `.py`, `.js`, `.ts` extension
- Pre-commit hook execution
- User says "check quality" or "nano flawless"
- After agent tool execution

## Output

```
NANO FLAWLESS REPORT
====================
Target:  [file]
Score:   0.9984 (99.84%)

Critical: 0 | High: 0 | Medium: 1 | Low: 0

1. [MEDIUM] Missing type hint for function X (line 42)

Verdict: ✅ NANO FLAWLESS
```