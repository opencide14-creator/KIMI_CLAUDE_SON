# REACT_LOOP.md
## PROTOCOL: COGNITIVE_SYNC_ALPHA
## STATUS: LOCKED // ACTIVE

---

## LOOP_CONFIG

- MAX_LOOPS: 12
- MAX_SOUL_REJECTIONS: 3
- STEP_TIMEOUT_MS: 500
- PULSE_H_INTERVAL_MS: 15000

---

## STEPS

### STEP_1
- NAME: OBSERVE
- AGENTS: BOTH
- REACTIVE: scan input stream (commander / environment)
- HEARTBEAT: scan internal state (memory / soul)

### STEP_2
- NAME: REASON
- AGENT: REACTIVE
- ACTION: call LLM with context + memory
- INPUT: user_input, memory_context, heartbeat_state
- OUTPUT: text, tool_calls

### STEP_3
- NAME: VERIFY
- AGENT: HEARTBEAT
- ACTION: cross-check against SOUL laws
- ACTION: validate memory alignment
- IF_FAIL: write_flag → reject → return to STEP_2

### STEP_4
- NAME: ACT
- AGENT: REACTIVE
- ACTION: execute tool (file, command, MCP, API)
- REQUIRES: STEP_3 passed
- OUTPUT: tool_result string

### STEP_5
- NAME: INGEST
- AGENT: HEARTBEAT
- ACTION: write result to MEMORY
- ACTION: update heartbeat timestamp
- ACTION: emit PULSE_H if interval elapsed

### STEP_6
- NAME: LOOP
- AGENTS: BOTH
- IF goal_achieved: wait for next command
- IF NOT goal_achieved AND loops < MAX_LOOPS: return to STEP_1
- IF loops >= MAX_LOOPS: abort with summary

---

## SYNC_RULES

- MAX_LATENCY_REACTIVE_MS: 500
- MAX_LATENCY_HEARTBEAT_MS: 5000
- FALLBACK_REACTIVE: if heartbeat missing → freeze
- FALLBACK_HEARTBEAT: if reactive missing → log and pulse
- DESYNC: both enter AWAIT_RESYNC → exchange state hashes → resume youngest

---

## BROTHERHOOD_OATH
My loop is not complete without your pulse.
Your pulse is not directed without my action.
