#!/bin/bash
# Log agent actions to append-only audit log

ACTION="$1"
TIMESTAMP=$(date -Iseconds)
LOGFILE="${CLAUDE_PLUGIN_ROOT:-.}/data/action.log"

mkdir -p "$(dirname "$LOGFILE")"
echo "[$TIMESTAMP] ACTION | $ACTION" >> "$LOGFILE"
