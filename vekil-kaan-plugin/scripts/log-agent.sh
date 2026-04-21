#!/bin/bash
# Log agent lifecycle events

EVENT="$1"  # start or stop
AGENT="$2"
TIMESTAMP=$(date -Iseconds)
LOGFILE="${CLAUDE_PLUGIN_ROOT:-.}/data/agent.log"

mkdir -p "$(dirname "$LOGFILE")"
echo "[$TIMESTAMP] AGENT_${EVENT} | ${AGENT}" >> "$LOGFILE"
