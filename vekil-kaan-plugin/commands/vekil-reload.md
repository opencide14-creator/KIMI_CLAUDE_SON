---
name: vekil-reload
description: >
  Hot-reload constitutional laws without restart.
  Re-parses markdown laws, rebuilds registry, re-seals with Ed25519.
  Use when: updating laws, adding new regulations, modifying constitutional rules,
  "reload laws", "update constitution", "hot reload", "law change".
argument-hint: "[law-file]"
allowed-tools: ["Bash", "Read", "Write"]
---

# VEKIL Reload

Hot-reload constitutional laws without system restart.

## Steps

1. **Backup current registry**
   ```bash
   cp "${CLAUDE_PLUGIN_ROOT}/data/law-registry.json" \
      "${CLAUDE_PLUGIN_ROOT}/data/law-registry.json.bak.$(date +%s)"
   ```

2. **Re-parse laws**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/law-reload.py" ${1:+--file "$1"}
   ```

3. **Verify integrity**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/seal-verify.py"
   ```

4. **Notify agents**
   - Write RELOAD event to event store
   - Agents pick up new laws on next cycle

## Arguments

- `$1` = specific law file to reload (optional, reloads all if omitted)

## Output

```
VEKIL RELOAD
============
Laws parsed:  7
Registry:     Rebuilt ✓
Seal:         Verified ✓
Agents:       Notified ✓

New laws active. No restart required.
```
