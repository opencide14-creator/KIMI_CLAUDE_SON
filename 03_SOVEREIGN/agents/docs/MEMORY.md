# MEMORY.md
## PROTOCOL: SINGULAR_RAG
## STATUS: WRITING // EVERY ACTION

---

## ARCHITECTURE

- VECTOR_STORE: chromadb (localhost, collection=sovereign_agents)
- METADATA_DB: sqlite (agents/memory/rag.db)
- AUDIT_LOG: jsonl (agents/memory/audit.jsonl, immutable append)
- EPHEMERAL_BUFFER: RAM — flushed every 10 actions

---

## WRITE_PRIORITIES

- TOOL_CALL: HIGH — forever
- TOOL_RESULT: HIGH — forever
- PULSE: MEDIUM — 90 days
- SOUL_VIOLATION: CRITICAL — forever
- FLAG: CRITICAL — forever
- AGENT_STATE_CHANGE: HIGH — forever

---

## EVENT_FORMAT
```json
{
  "event_id": "uuid",
  "timestamp": "iso8601",
  "source": "REACTIVE|HEARTBEAT|SYSTEM",
  "type": "TOOL_CALL|TOOL_RESULT|PULSE|FLAG|STATE|VERIFY_PASS|VERIFY_FAIL",
  "priority": "CRITICAL|HIGH|MEDIUM|LOW",
  "payload": {},
  "signature": "sha256_of_payload"
}
```

---

## READ_RULES

- REACTIVE reads MEMORY before every REASON step
- HEARTBEAT reads MEMORY before every VERIFY step
- NO_CACHE_LONGER_THAN_SECONDS: 10

---

## BOOT_SEQUENCE
1. Heartbeat starts RAG server if not running
2. Heartbeat loads last known root hash
3. Heartbeat broadcasts MEMORY_READY pulse
4. Reactive waits for pulse, loads active context
5. Both compare memory root — if mismatch enter AWAIT_RESYNC
6. Resync: pull all events from last common timestamp, replay
7. Continue

- IF_RAG_UNREACHABLE: both agents PAUSE (no action until returns)

---

## OATH
What you write, I read.
What I write, you read.
If one of us forgets, the other remembers.
If both forget, the soul remembers.
Memory is not storage — it is our shared breath.
