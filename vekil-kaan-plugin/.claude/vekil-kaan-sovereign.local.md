---
plugin: vekil-kaan-sovereign
version: "4.0.0"
lastUpdated: "2026-04-19"
---

# VEKIL-KAAN SOVEREIGN Local Configuration

This file stores per-project configuration for the VEKIL-KAAN SOVEREIGN plugin.
Edit as needed for your specific project requirements.

## Agent Configuration

### vekil-reactive
- max_latency_ms: 500
- pulse_interval_actions: 5
- model: kimi/kimi-code

### vekil-heartbeat
- pulse_interval_seconds: 15
- timeout_seconds: 30
- mourning_timeout_seconds: 60

### vekil-full
- enabled: true
- requires_komutan_auth: true

## Law Enforcement

- strict_mode: true
- hot_reload: true
- seal_on_boot: true

## Memory Substrate

- chroma_path: ./data/chroma
- sqlite_path: ./data/vekil.db
- ephemeral_buffer_size: 1000

## Gateway

- proxy_port: 8080
- gateway_port: 4000
- default_backend: kimi

## Backends

| Name | Base URL | API Key Env |
|------|----------|-------------|
| kimi | https://api.moonshot.ai/v1 | KIMI_API_KEY |
| anthropic | https://api.anthropic.com | ANTHROPIC_API_KEY |
| openai | https://api.openai.com/v1 | OPENAI_API_KEY |
| ollama | http://localhost:11434 | - |

## Security

- ca_cert_path: ./certs/ca.pem
- server_cert_path: ./certs/server.pem
- vault_key_path: ./keys/vault.key

## Logging

- level: info
- path: ./logs
- max_size_mb: 100
- retention_days: 30