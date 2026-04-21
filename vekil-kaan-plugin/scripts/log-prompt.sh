#!/bin/bash
# Log user prompt to append-only event store
# Called by UserPromptSubmit hook

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(dirname "$0")/..}"
LOG_DIR="${PLUGIN_ROOT}/logs/prompts"
mkdir -p "$LOG_DIR"

TIMESTAMP=$(date -u +%Y%m%d_%H%M%S_%N 2>/dev/null || date -u +%Y%m%d_%H%M%S_000000000)
HASH=$(echo -n "$TIMESTAMP $*" | sha256sum | cut -d' ' -f1 | head -c 16)

# Append-only log entry
cat >> "${LOG_DIR}/prompts.log" <<EOF
[${TIMESTAMP}] [${HASH}] PULSE
EOF

echo "PROMPT_LOGGED ${HASH}"
