---
name: sovereign-gateway
description: >
  Configure and manage the SOVEREIGN AI gateway.
  Use when routing AI API traffic, configuring backends, managing certificates,
  or setting up MITM proxy. Trigger phrases: "gateway config", "route traffic",
  "proxy setup", "certificate install", "AI gateway", "model routing".
metadata:
  author: KRAL
  version: "4.0"
---

# Sovereign Gateway Skill

## Purpose

Manage the SOVEREIGN MITM proxy and AI gateway infrastructure:
- Intercept HTTPS/WebSocket traffic
- Route between AI backends (Anthropic, OpenAI, Kimi, Ollama)
- Inject constitutional instructions
- Log traffic immutably

## When to Activate

- User mentions "gateway", "proxy", "route", "intercept"
- Certificate management needed
- Backend switching (Claude → Kimi, etc.)
- Traffic logging or analysis
- Constitutional injection configuration

## Gateway Configuration

### Default Ports
- MITM Proxy: `8080`
- AI Gateway: `4000`

### Backends
```json
{
  "anthropic": {
    "base_url": "https://api.anthropic.com",
    "models": ["claude-opus-4-6", "claude-sonnet-4-6"]
  },
  "kimi": {
    "base_url": "https://api.moonshot.ai/v1",
    "models": ["kimi-k2", "kimi-code"]
  },
  "openai": {
    "base_url": "https://api.openai.com/v1",
    "models": ["gpt-4o", "gpt-4o-mini"]
  },
  "ollama": {
    "base_url": "http://localhost:11434",
    "models": ["llama3", "mistral"]
  }
}
```

### Certificate Management
- Generate CA: `scripts/cert-generate.py`
- Install CA: `scripts/cert-install.py`
- Remove CA: `scripts/cert-remove.py`
- Verify: `scripts/cert-verify.py`

### Model Rewriting
- Anthropic → Kimi: Rewrite `model` field, translate content blocks
- OpenAI → Anthropic: Convert chat format to messages format
- Streaming: Buffer SSE chunks, reformat on-the-fly

## Commands

### Launch Gateway
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/gateway-start.py --port 4000
```

### Test Backend
```bash
curl http://127.0.0.1:4000/v1/models
curl http://127.0.0.1:4000/v1/chat/completions -d '{"model":"kimi-k2"}'
```

### View Traffic Log
```bash
python ${CLAUDE_PLUGIN_ROOT}/scripts/traffic-log.py --tail 50
```

## Security

- All traffic encrypted at rest (Fernet)
- Append-only log with hash chain
- Tamper-evident verification
- Circuit breaker per backend (5 failures → 30s cooldown)
