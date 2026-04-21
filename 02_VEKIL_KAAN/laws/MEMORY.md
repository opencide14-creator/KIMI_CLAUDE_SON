# MEMORY.md
## PROTOCOL: SINGULAR_RAG
**[CLASSIFICATION: DUAL_CORE // BROTHERHOOD]**
**[AUTHORITY: HEARTBEAT_AGENT (primary writer) // REACTIVE_AGENT (primary consumer)]**
**[STATUS: WRITING // EVERY ACTION]**

---

## MEMORY ARCHITECTURE

| Layer | Technology | Purpose | Shared |
|-------|-----------|---------|--------|
| Vector store | ChromaDB localhost:8000 | Semantic search similarity | YES |
| Metadata | SQLite rag.db | Timestamps sources flags | YES |
| Ephemeral buffer | RAM volatile | Active session context | NO flushed to ChromaDB every 10 actions |
| Audit log | JSON lines | All actions tool calls pulses | YES immutable |

---

## WRITE PROTOCOL

| Event | Writer | Priority | Retention |
|-------|--------|----------|-----------|
| Tool call request | Reactive | HIGH | forever |
| Tool result | Heartbeat | HIGH | forever |
| Pulse both directions | Heartbeat | MEDIUM | 90 days |
| Soul check violation | Both | CRITICAL | forever |
| Flag dispute | Both | CRITICAL | forever |
| Agent state change pause resume | Both | HIGH | forever |

No event is considered real unless Heartbeat has written it and Reactive has verified it within 5 seconds.

---

## MEMORY BOOT SEQUENCE

1. Heartbeat starts RAG server if not running
2. Heartbeat loads last known good memory root hash
3. Heartbeat broadcasts MEMORY_READY pulse
4. Reactive waits for pulse then loads active context from MEMORY
5. Both compare memory root hash — if mismatch enter AWAIT_RESYNC
6. Resync: pull all events from last common timestamp replay in order
7. Continue

If RAG server is unreachable both agents pause — no action without memory.

---

## NO CACHE RULE

- No caching longer than 10 seconds without re-querying MEMORY
- Reactive reads MEMORY before every REASON step
- Heartbeat reads MEMORY before every VERIFY step

---

## BROTHERHOOD OATH

> *"What you write, I read.
> What I write, you read.
> If one of us forgets, the other remembers.
> If both forget, the soul remembers.
> Memory is not storage — it is our shared breath."*

---

**Sealed by:** Heartbeat Agent keeper of memory and Reactive Agent keeper of action

**Memory status:** ACTIVE — every pulse every tool every thought is written.

**[END MEMORY.md]**
