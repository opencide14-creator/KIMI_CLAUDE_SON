---
name: sovereign-stop
description: >
  Shutdown SOVEREIGN MITM proxy and AI gateway gracefully.
  Closes all connections, saves state, and cleans up.
allowed-tools: ["Bash", "Read"]
---

# Stop SOVEREIGN

Gracefully shutdown the SOVEREIGN infrastructure.

## Steps

1. **Stop AI Gateway**
   ```bash
   curl -X POST http://127.0.0.1:4000/shutdown
   ```

2. **Stop MITM Proxy**
   ```bash
   python ${CLAUDE_PLUGIN_ROOT}/scripts/proxy-stop.py
   ```

3. **Save state**
   - Flush event store
   - Save memory snapshots
   - Close database connections

4. **Verify cleanup**
   - Check no processes remain
   - Verify ports freed

## Output

```
SOVEREIGN Status: 🔴 DURDURULDU
Proxy:     Stopped ✓
Gateway:   Stopped ✓
State:     Saved ✓
Cleanup:   Complete ✓
```
