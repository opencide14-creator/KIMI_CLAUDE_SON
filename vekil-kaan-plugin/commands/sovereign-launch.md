---
name: sovereign-launch
description: >
  Launch SOVEREIGN MITM proxy and AI gateway.
  Starts the interception and routing infrastructure.
argument-hint: "[proxy-port] [gateway-port]"
allowed-tools: ["Bash", "Read", "Write"]
---

# Launch SOVEREIGN

Initialize the SOVEREIGN network sovereignty infrastructure.

## Steps

1. **Check prerequisites**
   - Verify Python 3.11+ installed
   - Check if ports are free (default: 8080 proxy, 4000 gateway)
   - Verify CA certificate exists

2. **Start MITM Proxy**
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/proxy-start.py --port ${1:-8080}
   ```

3. **Start AI Gateway**
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/gateway-start.py --port ${2:-4000}
   ```

4. **Verify certificates**
   - Check CA cert in system trust store
   - Verify cert validity

5. **Health check**
   - Test proxy connectivity
   - Test gateway `/health` endpoint
   - Report status

## Arguments

- `$1` = proxy port (default: 8080)
- `$2` = gateway port (default: 4000)

## Output

```
SOVEREIGN Status: 🟢 AKTIF
Proxy:     127.0.0.1:8080
Gateway:   127.0.0.1:4000
CA Cert:   Installed ✓
Traffic:   Intercepting ✓
```
