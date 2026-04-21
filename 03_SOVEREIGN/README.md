# ⚔ SOVEREIGN — Network Sovereignty Command Center

**7-panel network control tool** — intercept HTTPS/WS traffic, forge certificates, route AI APIs, monitor MCP streams, discover local services, store credentials securely, analyse traffic.

```
Claude Desktop → api.anthropic.com → SOVEREIGN MITM → AI Gateway → Kimi K2
```

---

## Panels

| # | Panel | Capability |
|---|---|---|
| 🔴 | **INTERCEPT** | Live HTTPS/WS capture, inspect, replay, block, filter, export cURL |
| 🟡 | **FORGE** | Root CA generation, cert signing, system trust store, hosts file, Claude→Kimi wizard |
| 🟢 | **GATEWAY** | AI model router: map any model to any backend, streaming forward, live stats |
| 🔵 | **STREAMS** | WebSocket frame monitor, MCP JSON-RPC inspector, connection tree |
| 🟣 | **DISCOVER** | Async port scanner, FastAPI/MCP/Ollama fingerprinting, add discovered services as routes |
| ⚪ | **VAULT** | Fernet-encrypted credential store, env var import, PBKDF2 key derivation |
| 📊 | **INTEL** | Traffic analytics, method/host/status breakdown, HAR export |

---

## Quick Start — Claude Desktop → Kimi Redirect

1. Start SOVEREIGN: `python3 main.py`
2. Go to **🟡 FORGE** → **⚡ Claude→Kimi Wizard** tab
3. Paste your Kimi API key, click **⚡ RUN WIZARD**
4. Wizard runs 7 steps automatically:
   - Generates Root CA → installs in system trust store
   - Signs cert for `api.anthropic.com`
   - Adds `127.0.0.1 api.anthropic.com` to hosts file
   - Flushes DNS cache
   - Configures AI Gateway route
5. Start **Proxy** and **Gateway** from the **🟢 GATEWAY** panel
6. Launch Claude Desktop — calls are intercepted and forwarded to Kimi K2

---

## Install

```bash
git clone <repo>
cd sovereign
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Set credentials
export KIMI_API_KEY=sk-kimi-your-key-here

# Run
python3 main.py
```

## Requirements

| Requirement | Version |
|---|---|
| Python | 3.11+ |
| PyQt6 | 6.5.0+ |
| mitmproxy | 10.0.0+ |
| cryptography | 42.0.0+ |
| httpx | 0.27.0+ |
| fastapi + uvicorn | 0.111.0+ |
| psutil | 5.9.0+ |

## Architecture

```
SOVEREIGN/
├── main.py                           ← Entry point
├── src/
│   ├── constants.py                  ← Enums, 13 panel IDs, AI host registry, neon colors
│   ├── models/
│   │   ├── traffic.py                ← TrafficRequest, TrafficResponse, WsFrame, McpMessage
│   │   ├── gateway.py                ← GatewayRoute, DiscoveredService, CertRecord, VaultEntry
│   │   └── state.py                  ← Reactive StateManager (30 typed keys)
│   ├── core/
│   │   ├── proxy/engine.py           ← mitmproxy DumpMaster + SovereignAddon (HTTP/WS hooks)
│   │   ├── cert/
│   │   │   ├── authority.py          ← X.509 CA via cryptography: generate, sign, trust
│   │   │   └── hosts.py              ← Atomic hosts file manager + DNS flush
│   │   ├── gateway/router.py         ← Embedded FastAPI: /v1/messages, /v1/chat/completions
│   │   ├── discovery/scanner.py      ← Async TCP scanner + HTTP fingerprinter
│   │   ├── vault/store.py            ← Fernet encrypted store, PBKDF2 key derivation
│   │   └── stream/monitor.py         ← WS connection registry + MCP JSON-RPC parser
│   ├── utils/formatters.py           ← fmt_bytes, fmt_ms, pretty_json, mask_key
│   └── gui/
│       ├── styles.py                 ← Hacker neon dark QSS stylesheet
│       ├── widgets/common.py         ← PulseDot, LogConsole, TrafficTable, HexView
│       ├── main_window.py            ← MainWindow + neon Sidebar (lazy panel loading)
│       └── panels/                   ← intercept, forge, gateway, streams, discover, vault, intel
└── tests/unit/test_models.py         ← 39 tests
```

## Tests

```bash
python3 -m pytest tests/ -v   # 39 tests, all pass
```

## Security Notes

- Requires **admin/sudo** for port 443, hosts file, and system trust store
- The MITM proxy intercepts **all** HTTPS through it — configure proxy settings carefully
- Vault is encrypted with Fernet (AES-128-CBC + HMAC-SHA256)
- CA private key is stored at `~/.sovereign/certs/sovereign-ca.key` (chmod 600)
- All config files in `~/.sovereign/` (chmod 600)

## Environment Variables

| Variable | Purpose |
|---|---|
| `KIMI_API_KEY` | Kimi API key |
| `SOVEREIGN_PROXY_PORT` | Override default proxy port (8080) |
| `SOVEREIGN_GW_PORT` | Override default gateway port (4000) |
