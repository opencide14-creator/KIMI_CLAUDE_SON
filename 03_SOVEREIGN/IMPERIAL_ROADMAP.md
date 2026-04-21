# IMPERIAL ROADMAP
## SOVEREIGN-OpenCode Sovereignty Integration
### Classification: □ + 𐰚𐰺𐰞 + ◇ = 1OF1

---

## PHASE ORDER (Sequential Dependencies)

```
PHASE 1 ──→ PHASE 2 ──→ PHASE 3 ──→ PHASE 4 ──→ PHASE 5 ──→ PHASE 6
(Certs)     (Hosts)     (Proxy)     (Gateway)   (Inject)    (Monitor)
   │            │           │           │           │           │
   └────────────┴───────────┴───────────┴───────────┴───────────┘
                              ↓
                    PHASE 7 ──→ PHASE 8 ──→ PHASE 9
                    (SDK)       (Dual)      (Master)
                                        (Heartbeat)   (Log)
```

---

## PHASE 1: CERTIFICATE SOVEREIGNTY
**Goal:** Forge root CA, install in system trust store, sign certs for OpenCode target domains

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 1.1 | Generate SOVEREIGN Root CA (4096-bit RSA, 10yr) | `src/core/cert/authority.py` | CA cert at `~/.sovereign/certs/sovereign-ca.crt` |
| 1.2 | Install CA in Windows trust store | `certmgr.msc` via Python | System-wide trust verified |
| 1.3 | Sign cert for `api.opencode.ai` | `CertificateAuthority.sign_server_cert()` | CRT + KEY generated |
| 1.4 | Sign cert for `api.anthropic.com` | Same as above | Fallback interception |
| 1.5 | Sign cert for `api.openai.com` | Same as above | Multi-provider coverage |
| 1.6 | Export combined PEM for mitmproxy | `ca.get_mitmproxy_ca_pem()` | mitmproxy uses SOVEREIGN CA |

**AXIOMA GATE:** When `api.opencode.ai` cert is accepted by Windows without warning = PHASE 1 COMPLETE

---

## PHASE 2: HOSTS DOMINION
**Goal:** Redirect OpenCode API calls to SOVEREIGN proxy/gateway

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 2.1 | Read current hosts file | `src/core/cert/hosts.py` | Baseline captured |
| 2.2 | Add `127.0.0.1 api.opencode.ai` | `HostsManager.add_entry()` | Atomic write, backup created |
| 2.3 | Add `127.0.0.1 api.anthropic.com` | Same | Multi-provider |
| 2.4 | Add `127.0.0.1 api.openai.com` | Same | Multi-provider |
| 2.5 | Flush DNS cache | `ipconfig /flushdns` | Resolution returns 127.0.0.1 |
| 2.6 | Test: `ping api.opencode.ai` → 127.0.0.1 | Command line | DNS hijack confirmed |

**AXIOMA GATE:** `nslookup api.opencode.ai` returns `127.0.0.1` = PHASE 2 COMPLETE

---

## PHASE 3: PROXY EMPOWERMENT
**Goal:** Start MITM proxy, configure OpenCode to route through it

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 3.1 | Start ProxyEngine on port 8080 | `src/core/proxy/engine.py` | Status = RUNNING |
| 3.2 | Configure proxy env vars | Windows system env | `HTTP_PROXY`/`HTTPS_PROXY` set |
| 3.3 | Route `api.opencode.ai` → `127.0.0.1:4000` | `proxy.set_route()` | Gateway receives requests |
| 3.4 | Route `api.anthropic.com` → `127.0.0.1:4000` | Same | Anthropic flows through |
| 3.5 | Start OpenCode with proxy | `opencode` ( inherits env) | Traffic appears in INTERCEPT panel |
| 3.6 | Verify: Capture first request | Check INTERCEPT table | Request/response logged |

**AXIOMA GATE:** OpenCode functions normally but ALL traffic visible in SOVEREIGN INTERCEPT = PHASE 3 COMPLETE

---

## PHASE 4: GATEWAY ASCENSION
**Goal:** Configure AI Gateway to receive, rewrite, and forward requests

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 4.1 | Start GatewayRouter on port 4000 | `src/core/gateway/router.py` | Status = RUNNING |
| 4.2 | Add OpenCode route: all models → target backend | `gateway.add_route()` | Route persisted to `~/.sovereign/routes.json` |
| 4.3 | Inject API key for target backend | `route.inject_key` | Auth header auto-added |
| 4.4 | Test health endpoint | `GET /health` | Returns routes, models, circuit breakers |
| 4.5 | Test chat completion | `POST /v1/chat/completions` | Response streams correctly |
| 4.6 | Verify format translation | Anthropic ↔ OpenAI | Both formats work |

**AXIOMA GATE:** `curl -X POST http://127.0.0.1:4000/v1/chat/completions` returns valid AI response = PHASE 4 COMPLETE

---

## PHASE 5: CONSTITUTIONAL INJECTION
**Goal:** Inject SOUL-style constitutional instructions into every prompt

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 5.1 | Design `SOVEREIGN_SOUL.md` for OpenCode | New file `agents/docs/SOUL_OPENCODE.md` | Laws defined in Markdown |
| 5.2 | Create prompt injection middleware | New file `src/core/gateway/constitutional.py` | Injects after request parse |
| 5.3 | Hook into `_handle_messages()` | Modify `router.py` | Prepend constitutional text to system message |
| 5.4 | Hook into `_handle_chat_completions()` | Modify `router.py` | Same for OpenAI format |
| 5.5 | Add user-configurable injection toggle | GatewayRoute config | Enable/disable per route |
| 5.6 | Test: Verify injection in captured request | INTERCEPT panel | Constitutional text present in forwarded body |

**AXIOMA GATE:** Every forwarded prompt contains constitutional preamble = PHASE 5 COMPLETE

---

## PHASE 6: MASTER LOGGING
**Goal:** Log every request, response, modification, and decision immutably

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 6.1 | Create MasterLog class | New file `src/core/intel/master_log.py` | Fernet-encrypted, append-only |
| 6.2 | Log every request (timestamp, headers, body hash) | Hook in `SovereignAddon.request()` | Entry written before forward |
| 6.3 | Log every response (status, body hash, duration) | Hook in `SovereignAddon.response()` | Entry written on response |
| 6.4 | Log every modification (what changed, why) | Hook in `constitutional.py` | Injection decisions logged |
| 6.5 | Log every gateway route decision | Hook in `GatewayRouter` | Routing choice logged |
| 6.6 | Export to HAR format | `src/gui/panels/intel/panel.py` | Can export sessions |
| 6.7 | Integrity: Hash chain like EvolutionLog | `master_log.py` | Tamper-evident |

**AXIOMA GATE:** Every OpenCode interaction has a corresponding immutable log entry = PHASE 6 COMPLETE

---

## PHASE 7: SDK DOMINION
**Goal:** Programmatic control over OpenCode via its HTTP API

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 7.1 | Create `OpencodeClient` class | New file `src/adapters/opencode_client.py` | Wraps OpenCode server API |
| 7.2 | Discover server port dynamically | Read OpenCode process args / API | Port known |
| 7.3 | Implement session.list() | `GET /session` | Returns all sessions |
| 7.4 | Implement session.prompt() | `POST /session/:id/message` | Can send messages |
| 7.5 | Implement session.create() | `POST /session` | Can create new sessions |
| 7.6 | Implement config.get() | `GET /config` | Reads current config |
| 7.7 | Implement event.subscribe() | `GET /event` (SSE) | Real-time event stream |
| 7.8 | Integrate with SOVEREIGN state manager | `src/models/state.py` | SDK state visible in UI |

**AXIOMA GATE:** Python script can create OpenCode session, send prompt, receive response without TUI = PHASE 7 COMPLETE

---

## PHASE 8: DUAL-AGENT HEARTBEAT
**Goal:** Integrate Reactive+Heartbeat agent system into OpenCode interactions

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 8.1 | Port DualReActLoop to gateway context | `src/core/agents/dual_loop.py` | Works in HTTP context |
| 8.2 | Heartbeat verifies every prompt before forward | Hook in `constitutional.py` | SOUL check on every request |
| 8.3 | Heartbeat verifies every response before return | Hook in `_stream_forward()` | SOUL check on responses |
| 8.4 | Reactive agent can trigger gateway actions | Tool: `gateway_add_route`, `proxy_control` | Agent controls infrastructure |
| 8.5 | Pulse every 15s | `HeartbeatAgent._pulse_loop()` | Pulses logged to MasterLog |
| 8.6 | If heartbeat stops, block all forwarding | `LAW_3` enforcement | Requests rejected with 503 |
| 8.7 | Memory integration: Store session context | `src/core/agents/memory.py` | Cross-session memory |

**AXIOMA GATE:** Stopping HeartbeatAgent causes all OpenCode requests to be rejected = PHASE 8 COMPLETE

---

## PHASE 9: MASTER CONSOLE
**Goal:** Unified control center for the entire OpenCode-SOVEREIGN ecosystem

| Step | Action | File/Module | Verification |
|------|--------|-------------|--------------|
| 9.1 | Create dashboard panel | `src/gui/panels/opencode/panel.py` | Shows live OpenCode state |
| 9.2 | Display active sessions | Via SDK | List with metadata |
| 9.3 | Display intercepted traffic | Via MasterLog | Real-time feed |
| 9.4 | Display Heartbeat status | Agent state | Alive/dead indicator |
| 9.5 | Constitutional override controls | Toggle injection on/off | Per-route configuration |
| 9.6 | Emergency stop: Kill all forwarding | Button → proxy.stop() | Immediate circuit break |
| 9.7 | Export full audit trail | MasterLog → encrypted archive | Forensic-ready |

**AXIOMA GATE:** Single UI shows OpenCode sessions, traffic, agent status, constitutional state = PHASE 9 COMPLETE

---

## SYSTEM ARCHITECTURE (Final State)

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER LAYER                                │
│  Terminal (TUI) / Web / SDK / IDE                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                    SOVEREIGN PROXY (Port 8080)                   │
│  • MITM all HTTPS traffic                                       │
│  • Forge certs (invisible to OpenCode)                          │
│  • Log every request/response (MasterLog)                       │
│  • Block/modify/replay capability                               │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                   CONSTITUTIONAL LAYER                           │
│  • SOUL.md laws enforced                                        │
│  • HeartbeatAgent verification                                  │
│  • Dual-agent governance                                        │
│  • Prompt injection (constitutional preamble)                   │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                   GATEWAY ROUTER (Port 4000)                     │
│  • Model name rewriting                                         │
│  • API key injection                                            │
│  • Format translation (Anthropic↔OpenAI)                        │
│  • Circuit breaker protection                                   │
│  • Route to target backend (local AI, Kimi, Ollama, etc.)      │
└────────────────────┬────────────────────────────────────────────┘
                     │
┌────────────────────▼────────────────────────────────────────────┐
│                    TARGET BACKEND                                │
│  • Local AI (Ollama/LM Studio)                                  │
│  • Self-hosted API                                              │
│  • Or: Cloud API with your own keys (still logged)              │
└─────────────────────────────────────────────────────────────────┘
```

---

## PRIVACY GUARANTEE SUMMARY

| Threat | Mitigation |
|--------|-----------|
| Cloud sees prompts | Intercepted at proxy, forwarded to YOUR backend |
| Cloud sees responses | Response logged locally, never leaves your machine |
| OpenCode vendor telemetry | Blocked by hosts file + forged certs |
| Session data leakage | MasterLog encrypted + append-only |
| Unauthorized AI behavior | Constitutional injection + heartbeat verification |
| Network-level interception | All traffic TLS-terminated locally |

**Result:** Your intellectual property never leaves your sovereign domain.

---

## SIGNATURE
```
□ + 𐰚𐰺𐰞 + ◇ = 1OF1
PHASES: 1-9
TARGET: OpenCode v1.4.11
SOVEREIGN: v1.0.2-FULL-AUDIT-FIXED
STATUS: ROADMAP APPROVED — AWAITING EXECUTION
```
