---
name: memory-sync
description: >
  Force synchronization between agent memory substrates.
  Resolves hash mismatches, replays events, re-establishes consensus.
  Use when: "agents desynced", "hash mismatch", "memory conflict",
  "resync agents", "fix memory divergence", "consensus lost".
allowed-tools: ["Bash", "Read"]
---

# Memory Sync

Force resynchronization of agent memory substrates.

## When to Use

- Root hash mismatch between agents
- Event store divergence detected
- Post-crash recovery
- Manual intervention after conflict

## Resync Protocol

1. **Identify divergence point**
   ```bash
   python "${CLAUDE_PLUGIN_ROOT}/scripts/find-divergence.py"
   ```

2. **Snapshot current state**
   - Both agents snapshot their substrate
   - Save to `.resync/snapshot-{agent}-{timestamp}.json`

3. **Find last common timestamp**
   - Binary search event store
   - Identify last matching event ID

4. **Replay events**
   - Pull all events since divergence
   - Verify HMAC signatures
   - Skip tampered events
   - Re-execute in order

5. **Recompute root hash**
   - Both agents compute new hash
   - Verify match
   - If still mismatch: escalate to KOMUTAN

6. **Resume operation**
   - Clear AWAIT_RESYNC status
   - Resume normal cycle
   - Log resync completion

## Failure Handling

- 3 consecutive failures → HALT
- Escalate to KOMUTAN
- Preserve snapshots for forensics

## Output

```
MEMORY SYNC
===========
Divergence:  Event #1427 (2026-04-19T14:23:01)
Events:      23 to replay

[1/23] Replay: Event #1428 | Verify: PASS
[2/23] Replay: Event #1429 | Verify: PASS
...
[23/23] Replay: Event #1450 | Verify: PASS

Root Hash:   MATCH ✅ (a1b2c3...)
Status:      SYNCED

Agents resumed. Normal operation.
```
