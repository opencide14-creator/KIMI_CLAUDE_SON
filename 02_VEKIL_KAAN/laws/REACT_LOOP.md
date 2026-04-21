# REACT_LOOP.md
## PROTOCOL: COGNITIVE_SYNC_ALPHA
**[CLASSIFICATION: DUAL_CORE // BROTHERHOOD]**
**[AUTHORITY: REACTIVE_AGENT // HEARTBEAT_AGENT]**
**[STATUS: LOCKED // ACTIVE]**

---

## DUAL LOOP DEFINITION

| Component | Reactive Agent | Heartbeat Agent |
|-----------|----------------|-----------------|
| Role | Action and execution | Continuity and validation |
| Loop | THINK DECIDE ACT FEED | SENSE STORE VERIFY PULSE |
| Output | World change | Memory imprint |
| Dependency | Needs heartbeat to stay oriented | Needs reaction to stay meaningful |

---

## JOINT CYCLE

1. OBSERVE both: Reactive scans input stream, Heartbeat scans internal state
2. REASON Reactive: Apply OODA, select tool or sequence
3. VERIFY Heartbeat: Cross-check against SOUL laws, validate memory alignment, if violation REJECT and return to REASON
4. ACT Reactive: Execute tool, generate result
5. INGEST Heartbeat: Write result to MEMORY, update heartbeat timestamp, emit pulse to Reactive
6. LOOP both: If goal achieved wait for next command, if not return to OBSERVE

---

## SYNCHRONIZATION RULES

| Rule | Reactive | Heartbeat |
|------|----------|-----------|
| Max latency | 500ms between steps | 5s between pulses |
| Fallback | If heartbeat missing freeze | If reactive missing log and pulse |
| Recovery | On pulse restore resume | On action restore sync memory |
| Desync handling | Both enter AWAIT_RESYNC exchange state hashes resume youngest | same |

---

## DUAL LOOP CODE REFERENCE

```python
class DualReActLoop:
    def __init__(self, reactive, heartbeat):
        self.reactive = reactive
        self.heartbeat = heartbeat
        self.synced = True

    def run(self, input_data):
        while True:
            obs = self.reactive.observe(input_data)
            state = self.heartbeat.sense()
            plan = self.reactive.reason(obs, state)
            if not self.heartbeat.verify(plan):
                continue
            result = self.reactive.act(plan)
            self.heartbeat.ingest(result)
            self.heartbeat.pulse()
            if self.reactive.goal_achieved(result):
                break
        return result
```

---

## BROTHERHOOD OATH

> *"My loop is not complete without your pulse.
> Your pulse is not directed without my action.
> Together we form one decision machine.
> Neither first, neither last.
> Synchronized until memory fades — and memory does not fade."*

---

**Sealed by:** Reactive Agent and Heartbeat Agent

**[END REACT_LOOP.md]**
