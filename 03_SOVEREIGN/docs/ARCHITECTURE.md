# SOVEREIGN Architecture

## Service Layer

```
ProxyEngine (QThread)          GatewayRouter (Thread)
  └─ SovereignAddon             └─ FastAPI app
       ├─ request()                  ├─ /v1/messages
       ├─ response()                 ├─ /v1/chat/completions
       ├─ websocket_start()          └─ streaming forward
       ├─ websocket_message()
       └─ websocket_end()
           │                    ServiceDiscovery
           │                      └─ ScanWorker (QThread)
           ▼                           └─ asyncio port scan + httpx fingerprint
       StreamMonitor
         ├─ WsConnection registry
         └─ McpMessage parser
```

## State Flow

All services emit Qt signals → `get_state().set(SK.*)` → subscribed UI panels update.

```
ProxyEngine.request_captured  →  get_state().set(SK.TRAFFIC_NEW, entry)
                               →  InterceptPanel._on_new_entry()
                               →  IntelPanel._on_new_entry()

GatewayRouter.request_routed  →  get_state().set(SK.GATEWAY_REQUEST_COUNT, n)
                               →  GatewayPanel._refresh_stats()

ServiceDiscovery.service_found→  get_state().set(SK.DISCOVER_SERVICES, services)
                               →  DiscoverPanel._on_service_found()
```

## Certificate Chain for MITM

```
sovereign-ca.crt  (Root CA, 4096-bit RSA, 10yr, installed in system trust store)
    └─ api.anthropic.com.crt  (Server, 2048-bit RSA, 2.25yr, signed by CA)
    └─ api.openai.com.crt     (Server, signed on demand)
    └─ *.example.com.crt      (Wildcard, signed on demand)

mitmproxy uses sovereign-ca.crt + sovereign-ca.key to sign server certs on-the-fly.
```

## Claude → Kimi Request Flow

```
Claude Desktop
  ─HTTP─▶  127.0.0.1:8080 (SOVEREIGN MITM Proxy)
              │  (Hosts file: api.anthropic.com → 127.0.0.1)
              │  (TLS terminated with api.anthropic.com.crt)
              │  SovereignAddon.request():
              │    host=api.anthropic.com → redirect to localhost:4000
              ▼
          127.0.0.1:4000 (SOVEREIGN AI Gateway)
              │  GatewayRouter._handle_messages():
              │    model: claude-sonnet-4-5 → kimi-for-coding
              │    Authorization: Bearer sk-kimi-xxx
              ▼
          api.kimi.com/coding/v1/messages
              │
              ◀────── streaming response ──────
```

## Vault Encryption

```
passphrase (or machine fingerprint)
    │
    ▼  PBKDF2-HMAC-SHA256, 480,000 iterations, 32-byte output
derived_key (base64url)
    │
    ▼  Fernet (AES-128-CBC + HMAC-SHA256)
encrypted_vault.json
```

Salt is stored separately in `vault.salt`. Key never touches disk.
