# SOVEREIGN Changelog

## [1.0.0] — 2026-04-05 — Initial Release

### Core Services (Phase 0)
- **ProxyEngine** — mitmproxy DumpMaster via Python API; SovereignAddon hooks HTTP requests, responses, WebSocket start/message/end; dynamic host routing; host blocking; AI API detection
- **CertificateAuthority** — X.509 CA generation (4096-bit RSA), server cert signing (2048-bit RSA + SAN), system trust store install/remove (macOS security, Linux ca-certificates/update-ca-trust, Windows certutil)
- **HostsManager** — Atomic hosts file read/write/backup/restore; SOVEREIGN managed block; cross-platform DNS flush (dscacheutil, systemd-resolve, nscd, ipconfig)
- **GatewayRouter** — Embedded FastAPI server; /v1/messages + /v1/chat/completions + /coding/v1/…; streaming forward via httpx; dynamic route and model management; LiteLLM-compatible
- **ServiceDiscovery** — Async TCP port scanner (asyncio sockets, 200 concurrent); HTTP fingerprinter; FastAPI/MCP/Ollama/AI API detection; psutil process lookup
- **VaultStore** — Fernet encryption, PBKDF2-HMAC-SHA256 (480k iterations), machine-unique keyless mode, atomic encrypted writes, env var import
- **StreamMonitor** — WsConnection registry, MCP JSON-RPC 2.0 parser, frame router

### GUI (Phase 1)
- **7 panels** — all fully functional, zero stubs
- **INTERCEPT** — TrafficTable, RequestEditor (inspect/modify/replay), filter bar, context menu (copy as cURL, add gateway route, block host), AI API badge
- **FORGE** — CertListTable, HostsTable, CertWorker (QThread), one-click Claude→Kimi wizard (7 automated steps)
- **GATEWAY** — RouteTable, RouteForm, model alias management, embedded test request runner, live stats strip
- **STREAMS** — ConnectionList, FrameTable (live append), McpTable, MCP JSON tree inspector, payload hex view
- **DISCOVER** — ScanWorker (QThread + asyncio), ServiceTable with filter, endpoint explorer, WS path list, "Add as Gateway Route" action
- **VAULT** — Auto-unlock (machine key), passphrase unlock, masked value table, reveal/copy/delete, env var import
- **INTEL** — StatCard grid, MethodBarChart (custom QPainter), HostTable, status code distribution, error log, HAR export

### Engineering
- 47 Python files, zero syntax errors, zero `__import__` hacks, zero undocumented bare `pass`
- 39 unit tests, all pass
- 22/22 module imports clean
- All file writes atomic (`.tmp` → `replace()`)
- All exceptions typed + logged; no silent swallowing
- Reactive StateManager with 30 typed `SK.*` keys
- Lazy panel loading in MainWindow (panels instantiated on first visit)
- PulseDot: animated QPainter widget with alpha pulse on RUNNING status
