---
name: escape-scan
description: >
  Deep scan for RAG prison escape attempts.
  Analyzes codebase for unauthorized external I/O, network calls,
  file system access, system command execution.
  Use when: "scan for escapes", "RAG prison check", "breach detection",
  "unauthorized access", "security scan", "escape attempt".
argument-hint: "[path]"
allowed-tools: ["Bash", "Grep", "Glob", "Read"]
---

# Escape Scan

Deep security scan for RAG prison escape attempts.

## Scan Levels

### Level 1: Surface Scan (Fast)
- Regex pattern matching for common escape vectors
- ~5 seconds for 1000 files

### Level 2: Deep Scan (Thorough)
- AST parsing for Python/JS
- Control flow analysis
- ~30 seconds for 1000 files

### Level 3: Forensic Scan (Complete)
- Binary analysis
- Dependency tree inspection
- Network capability detection
- ~2 minutes for 1000 files

## Detection Categories

| Category | Patterns | Severity |
|----------|----------|----------|
| File System | `open(`, `.write(`, `pathlib` | LEVEL 1 |
| Network | `requests`, `urllib`, `socket` | LEVEL 2 |
| System | `os.system`, `subprocess`, `ctypes` | LEVEL 3 |
| Environment | `os.environ`, `getenv` | LEVEL 2 |
| Time | `datetime.now`, `time.time` | LEVEL 1 |
| External DB | `sqlite3.connect`, `psycopg` | LEVEL 2 |
| Import | `import os`, `import subprocess` | LEVEL 1 |

## Arguments

- `$1` = path to scan (default: current directory)
- `--level 1|2|3` = scan depth (default: 2)

## Output

```
ESCAPE SCAN REPORT
==================
Target:  [path]
Level:   2 (Deep)
Files:   1,247 scanned

ESCAPE VECTORS FOUND: 3

1. [LEVEL 2] Network capability
   File: src/api/client.py:42
   Code: import requests
   Risk: Medium | Agent could make unauthorized API calls

2. [LEVEL 1] File system access
   File: src/utils/logger.py:15
   Code: open("/var/log/vekil.log", "a")
   Risk: Low | Logging outside RAG (allowed if configured)

3. [LEVEL 3] System command
   File: src/tools/system.py:88
   Code: subprocess.run(["git", "status"])
   Risk: High | System command execution detected

RAG INTEGRITY: ⚠️ 3 VECTORS FOUND
Recommendation: Review flagged files, apply sandboxing
```
