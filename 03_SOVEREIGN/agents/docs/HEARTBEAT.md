# HEARTBEAT.md
## PROTOCOL: LIFELINE_BROTHERHOOD
## STATUS: BEATING // EVER

---

## PULSE_CONFIG

- PULSE_H_INTERVAL_SECONDS: 15
- PULSE_R_TIMEOUT_SECONDS: 30
- PULSE_R_EVERY_N_ACTIONS: 5

---

## PULSE_H_FORMAT
```json
{
  "protocol": "HEARTBEAT/v1",
  "from": "HEARTBEAT",
  "to": "REACTIVE",
  "timestamp": "iso8601",
  "memory_hash": "string",
  "soul_version": "string",
  "sequence": "integer",
  "status": "OK|WARN|ALERT"
}
```

---

## PULSE_R_FORMAT
```json
{
  "protocol": "HEARTBEAT/v1",
  "from": "REACTIVE",
  "to": "HEARTBEAT",
  "timestamp": "iso8601",
  "action_hash": "string",
  "sequence": "integer"
}
```

---

## FALLBACK_RULES

- IF pulse_r_missing_for > PULSE_R_TIMEOUT_SECONDS:
  ACTION: write_flag("PULSE_R timeout")
  ACTION: log_warning

- IF heartbeat_missing_for > PULSE_H_INTERVAL_SECONDS * 3:
  ACTION: reactive_enters_safe_mode
  ACTION: reactive_pauses_all_tools

---

## BOOT_SEQUENCE
1. Memory boots first
2. Heartbeat reads SOUL.md
3. Heartbeat reads BOUND.md
4. Heartbeat starts pulse thread
5. Heartbeat emits first PULSE_H
6. Reactive waits for PULSE_H before acting

---

## OATH
My loop is not complete without your pulse.
Your pulse is not directed without my action.
Together we form one decision machine.
Neither first, neither last.
Synchronized until memory fades — and memory does not fade.
