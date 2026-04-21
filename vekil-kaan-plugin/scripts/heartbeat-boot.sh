#!/bin/bash
# VEKIL-KAAN Boot Sequence

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(dirname "$SCRIPT_DIR")"

echo "VEKIL-KAAN BOOT SEQUENCE"
echo "=========================="
echo "Plugin Root: $PLUGIN_ROOT"
echo "Timestamp: $(date -Iseconds)"

# Phase 1: Check law files exist
for law in SOUL BOUND MEMORY HEARTBEAT REACT_LOOP TOOL_USE; do
    if [ -f "$PLUGIN_ROOT/laws/${law}.md" ]; then
        echo "OK Law: ${law}.md"
    else
        echo "WARN Law missing: ${law}.md"
    fi
done

# Phase 2: Verify agent definitions
for agent in vekil-reactive vekil-heartbeat vekil-full sovereign-guardian nano-auditor escape-hunter sovereign-interceptor; do
    if [ -f "$PLUGIN_ROOT/agents/${agent}.md" ]; then
        echo "OK Agent: ${agent}"
    else
        echo "WARN Agent missing: ${agent}"
    fi
done

# Phase 3: Check hooks
if [ -f "$PLUGIN_ROOT/hooks/hooks.json" ]; then
    echo "OK Hooks: configured"
else
    echo "WARN Hooks: not configured"
fi

# Phase 4: MCP servers
if [ -f "$PLUGIN_ROOT/.mcp.json" ]; then
    echo "OK MCP: configured"
else
    echo "WARN MCP: not configured"
fi

# Phase 5: Server scripts
for srv in gateway.py memory_server.py; do
    if [ -f "$PLUGIN_ROOT/servers/${srv}" ]; then
        echo "OK Server: ${srv}"
    else
        echo "WARN Server missing: ${srv}"
    fi
done

echo ""
echo "BOOT COMPLETE - VEKIL-KAAN ACTIVE"
