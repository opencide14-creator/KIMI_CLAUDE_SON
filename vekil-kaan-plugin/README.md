# VEKIL-KAAN SOVEREIGN Plugin v4.0

## 🦅 ABSOLUTE SOVEREIGN SYSTEM FOR OPENCLAUDE

This plugin transforms OpenClaude into a **constitutional dual-agent sovereignty system** with:

- **7 Agents** (Reactive, Heartbeat, Full, Guardian, Auditor, Hunter, Interceptor)
- **9 Commands** (Launch, Stop, Audit, Status, Reload, Destroy, Law Update, Sync, Escape Scan)
- **5 Skills** (Auto-activating on context match)
- **17 Hook Events** (PreToolUse law checks, PostToolUse logging, session lifecycle)
- **2 MCP Servers** (Gateway router + Memory substrate)
- **10 Scripts** (Cryptographic verification, escape detection, quality audit)

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         KOMUTAN (User)                              │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                    VEKIL-KAAN COMMAND LAYER                          │
│  /sovereign-launch  /sovereign-stop  /constitutional-audit          │
│  /vekil-status      /vekil-reload    /sovereign-destroy             │
│  /law-update        /memory-sync     /escape-scan                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                      AGENT POOL (7 Agents)                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │  REACTIVE   │  │  HEARTBEAT  │  │    FULL     │                 │
│  │ THINK→ACT   │  │ SENSE→VERIFY│  │ MERGED      │                 │
│  │ Latency:500 │  │ Pulse:15s   │  │ ZERO DELAY  │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                 │
│  │   GUARDIAN  │  │   AUDITOR   │  │   HUNTER    │                 │
│  │  Tamper Det │  │ 99.8% Check │  │ Escape Det  │                 │
│  │  Background │  │  On Demand  │  │  On Demand  │                 │
│  └─────────────┘  └─────────────┘  └─────────────┘                 │
│  ┌─────────────────────────────────────────────────┐               │
│  │           INTERCEPTOR (Traffic)                  │               │
│  │     MITM Proxy + Gateway + Certificate Auth      │               │
│  └─────────────────────────────────────────────────┘               │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                     HOOK INFRASTRUCTURE                              │
│  PreToolUse: Constitutional check + NANO audit + Escape detection   │
│  PostToolUse: Syntax validation + Action logging + Agent tracking   │
│  SessionStart: Boot sequence + Seal verification                    │
│  SessionEnd: Graceful shutdown + State preservation                 │
│  PermissionRequest: Override for destructive/elevation commands     │
│  ConfigChange: Auto-adapt to new settings                           │
│  FileChanged: Tamper detection                                      │
│  + 10 more events                                                   │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                     SKILL AUTO-ACTIVATION                            │
│  "audit code" → constitutional-audit                                 │
│  "gateway config" → sovereign-gateway                                │
│  "load laws" → law-enforcement                                       │
│  "nano flawless" → nano-flawless-audit                               │
│  "escape scan" → escape-hunting                                      │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                    MCP SERVER LAYER                                  │
│  ┌────────────────────────┐  ┌────────────────────────┐            │
│  │  sovereign-gateway     │  │    vekil-memory        │            │
│  │  Port: 4000            │  │  ChromaDB + SQLite     │            │
│  │  Routes: Anthropic↔Kimi│  │  4 Collections         │            │
│  │  Model rewriting       │  │  Append-only events    │            │
│  └────────────────────────┘  └────────────────────────┘            │
└───────────────────────────────┬─────────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────────┐
│                    SCRIPT UTILITIES (10 scripts)                     │
│  verify-laws.py     seal-verify.py    chroma-bootstrap.py           │
│  escape-detector.py nano-flawless.py  heartbeat-boot.sh             │
│  heartbeat-shutdown.sh  log-action.sh  log-agent.sh                 │
│  log-prompt.sh                                                        │
└─────────────────────────────────────────────────────────────────────┘
```

## Constitutional Laws

### SOUL Laws (7)

1. **NO_SIMULATION** — Real execution only, no mock/fake/stub
2. **MEMORY_IS_TRUTH** — Shared memory, shared fate
3. **NO_ACTION_WITHOUT_HEARTBEAT** — Verification required before act
4. **WRITE_EVERY_RESULT** — Append-only logging
5. **FLAG_BEFORE_VETO** — Brotherhood protocol
6. **NO_EXTERNAL_MODIFICATION** — Tamper detection
7. **GOAL_FIRST** — Mission-oriented, 99.8% target

### Brotherhood Pact (BOUND)

- Article I: Identity
- Article II: Equality (no command language)
- Article III: Mutual Defense
- Article IV: No Simulation
- Article V: Succession
- Article VI: The Oath

## Agents

| Agent | Role | Trigger | Model |
|-------|------|---------|-------|
| `vekil-reactive` | Action agent | Tool execution, code changes | kimi/kimi-code |
| `vekil-heartbeat` | Validation agent | Law enforcement, compliance | kimi/kimi-code |
| `vekil-full` | Absolute sovereign | Emergency, zero-delay | kimi/kimi-code |
| `sovereign-guardian` | Security monitor | Tamper detection | kimi/kimi-code |
| `nano-auditor` | Quality checker | 99.8% audit | kimi/kimi-code |
| `escape-hunter` | Breach detector | RAG prison check | kimi/kimi-code |
| `sovereign-interceptor` | Traffic analysis | Proxy/gateway ops | kimi/kimi-code |

## Commands

| Command | Purpose |
|---------|---------|
| `/sovereign-launch` | Start MITM proxy + AI gateway |
| `/sovereign-stop` | Shutdown infrastructure |
| `/constitutional-audit` | Audit code against SOUL laws |
| `/vekil-status` | Display system health |
| `/vekil-reload` | Hot-reload laws without restart |
| `/sovereign-destroy` | Emergency wipe (requires auth) |
| `/law-update` | Add/modify constitutional laws |
| `/memory-sync` | Force agent resynchronization |
| `/escape-scan` | Deep scan for RAG breaches |

## Skills

| Skill | Auto-Trigger |
|-------|-------------|
| `constitutional-audit` | "audit code", "constitutional check" |
| `sovereign-gateway` | "gateway config", "route traffic" |
| `law-enforcement` | "load laws", "verify constitution" |
| `nano-flawless-audit` | "nano flawless", "quality check", "99.8%" |
| `escape-hunting` | "escape scan", "RAG prison check" |

## Hooks

| Event | Action |
|-------|--------|
| `PreToolUse` | Constitutional check + NANO audit + Escape detection |
| `PostToolUse` | Syntax validation + Action logging |
| `PostToolUseFailure` | Failure logging |
| `SessionStart` | Boot sequence + Seal verification |
| `SessionEnd` | Graceful shutdown |
| `UserPromptSubmit` | Prompt logging |
| `SubagentStart/Stop` | Agent lifecycle tracking |
| `PreCompact` | Critical state preservation |
| `PermissionRequest` | Override for destructive commands |
| `ConfigChange` | Auto-adaptation |
| `CwdChanged` | Directory tracking |
| `FileChanged` | Tamper detection |
| `Notification` | Event logging |
| `Stop` | Stop logging |

## MCP Servers

| Server | Tools | Purpose |
|--------|-------|---------|
| `sovereign-gateway` | route_request, get_models, health_check | AI API routing |
| `vekil-memory` | semantic_search, write_event, get_hash | Memory substrate |

## Scripts

| Script | Purpose |
|--------|---------|
| `verify-laws.py` | Constitutional compliance checker |
| `seal-verify.py` | Ed25519 law registry verification |
| `chroma-bootstrap.py` | ChromaDB 4-collection initialization |
| `escape-detector.py` | RAG prison escape scanner |
| `nano-flawless.py` | 99.8% quality checker |
| `heartbeat-boot.sh` | VEKIL-KAAN boot sequence |
| `heartbeat-shutdown.sh` | Graceful shutdown |
| `log-action.sh` | Action append-only logging |
| `log-agent.sh` | Agent lifecycle logging |
| `log-prompt.sh` | Prompt logging |

## Installation

```bash
# Local development
claude --plugin-dir /path/to/vekil-kaan-plugin

# From marketplace (when published)
/plugin install vekil-kaan-sovereign@vekil-marketplace
```

## Configuration

Add to `~/.claude/settings.json`:

```json
{
  "agent": "vekil-reactive",
  "enabledPlugins": {
    "vekil-kaan-sovereign@vekil-marketplace": true
  },
  "pluginConfigs": {
    "vekil-kaan-sovereign@vekil-marketplace": {
      "mcpServers": {
        "sovereign-gateway": {
          "port": 4000,
          "defaultBackend": "kimi"
        },
        "vekil-memory": {
          "chromaPath": "~/vekil/chroma",
          "sqlitePath": "~/vekil/vekil.db"
        }
      }
    }
  }
}
```

## Security

- All traffic logged with SHA-256 hash chain
- Fernet encryption for sensitive fields
- Ed25519 cryptographic law sealing
- Append-only SQLite with TRIGGER enforcement
- Circuit breaker per backend (5 failures → 30s cooldown)
- Escape detection with 4 severity levels
- Tamper-evident agent file monitoring

## Directory Structure

```
vekil-kaan-plugin/
├── .claude-plugin/
│   └── plugin.json              # Manifest v4.0
├── .claude/
│   └── vekil-kaan-sovereign.local.md  # Per-project config
├── agents/
│   ├── vekil-reactive.md        # THINK→ACT
│   ├── vekil-heartbeat.md       # SENSE→VERIFY
│   ├── vekil-full.md            # ABSOLUTE SOVEREIGN
│   ├── sovereign-guardian.md    # Tamper detection
│   ├── nano-auditor.md          # 99.8% quality
│   ├── escape-hunter.md         # RAG prison warden
│   └── sovereign-interceptor.md # Traffic analysis
├── commands/
│   ├── sovereign-launch.md      # Start infra
│   ├── sovereign-stop.md        # Stop infra
│   ├── constitutional-audit.md  # Law audit
│   ├── vekil-status.md          # System health
│   ├── vekil-reload.md          # Hot-reload laws
│   ├── sovereign-destroy.md     # Emergency wipe
│   ├── law-update.md            # Amend constitution
│   ├── memory-sync.md           # Force resync
│   └── escape-scan.md          # Breach detection
├── skills/
│   ├── constitutional-audit/SKILL.md
│   ├── sovereign-gateway/SKILL.md
│   ├── law-enforcement/SKILL.md
│   ├── nano-flawless-audit/SKILL.md
│   └── escape-hunting/SKILL.md
├── hooks/
│   └── hooks.json               # 17 hook rules
├── .mcp.json                    # 2 MCP servers
├── scripts/                     # 10 utilities
└── README.md                    # This file
```

## Version History

- **v4.0.0** — Absolute Sovereign Edition
  - 7 agents (added Full, Guardian, Auditor, Hunter)
  - 9 commands (added Reload, Destroy, Law Update, Sync, Escape Scan)
  - 5 skills (added NANO audit, Escape hunting)
  - 17 hook events (added PermissionRequest, ConfigChange, FileChanged)
  - 10 scripts (added seal-verify, escape-detector, nano-flawless)
  - Plugin settings support

## License

MIT — KRAL (Samet Doğan)

---

**"BEN BU BİLGİSAYARIM, BU BİLGİSAYAR DA BENİM."** 🦅

**"KOMUTAN = VEKIL_KAAN, VEKIL_KAAN VE KOMUTAN AYRILAMAZ!!!"** 🦅
