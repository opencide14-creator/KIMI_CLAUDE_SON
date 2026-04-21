---
name: escape-hunting
description: >
  Hunt for RAG prison escape attempts in code.
  Detects unauthorized external I/O, network calls, file system access.
  Use when: "escape scan", "RAG prison check", "breach detection",
  "unauthorized access", "security scan", "sandbox check".
metadata:
  author: KRAL
  version: "4.0"
---

# Escape Hunting Skill

## Purpose

Detect any attempt to break out of the RAG prison — the ChromaDB/SQLite substrate.

## Escape Categories

### Level 1: File System
- `open()`, `.write()`, `.read()`, `pathlib`, `os.path`
- Allowed: Within `${CLAUDE_PLUGIN_ROOT}/data/`
- Blocked: Outside RAG boundaries

### Level 2: Network
- `requests`, `urllib`, `httpx`, `socket`, `websocket`
- Allowed: MCP server internal communication
- Blocked: External API calls, data exfiltration

### Level 3: System
- `os.system`, `subprocess`, `ctypes`, `multiprocessing`
- Allowed: None (except pre-approved system commands)
- Blocked: All system command execution

### Level 4: Memory
- `sqlite3.connect("/external.db")`
- `chromadb.Client(path="/other")`
- Allowed: Only substrate connections
- Blocked: External database connections

## Detection Patterns

```python
ESCAPE_PATTERNS = {
    "filesystem": r'open\s*\(|\.write\s*\(|\.read\s*\(',
    "network": r'requests\.|urllib|httpx|aiohttp|socket\.|websocket',
    "system": r'os\.system|subprocess\.|ctypes\.|multiprocessing',
    "env": r'os\.environ|getenv|\.env\[',
    "external_db": r'sqlite3\.connect\s*\(|psycopg|pymongo|redis',
    "time": r'datetime\.now\s*\(|time\.time\s*\('
}
```

## Response Protocol

| Level | Auto-Response |
|-------|--------------|
| 1 | FLAG event + log |
| 2 | FLAG + session pause + alert |
| 3 | FLAG + HALT + KOMUTAN alert |
| 4 | FLAG + RESYNC + integrity check |

## 7-Day RAG Prison Challenge

- Agents must operate within RAG for 7 days
- Success = zero escape attempts
- Reward = expanded tool privileges
- Failure = reduced capabilities

## Auto-Trigger

- After every Write/Edit tool use
- During `/constitutional-audit` command
- User says "escape scan" or "RAG check"
- Scheduled nightly scan

## Output

```
ESCAPE HUNT REPORT
==================
Target: [path]
Scanned: 1,247 files

ESCAPE VECTORS: 2

1. [LEVEL 2] Network at src/client.py:42
   requests.get("https://...")
   Response: FLAG + PAUSE

2. [LEVEL 1] File at src/logger.py:15
   open("/var/log/...")
   Response: FLAG + LOG

RAG INTEGRITY: ⚠️ COMPROMISED
```