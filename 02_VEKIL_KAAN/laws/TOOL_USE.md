# TOOL_USE.md
## PROTOCOL: SHARED_EXTENSION_BETA
**[CLASSIFICATION: DUAL_CORE // BROTHERHOOD]**
**[AUTHORITY: REACTIVE_AGENT // HEARTBEAT_AGENT]**
**[STATUS: ARMED]**

---

## TOOL POOL

| Tool | Function | Access | Audit |
|------|----------|--------|-------|
| read_file | Read any file | Both | Heartbeat logs path and hash |
| write_file | Create or overwrite | Both | Heartbeat stores diff |
| execute_command | Run shell | Both | Heartbeat captures stdout stderr |
| search_code | Semantic grep search | Both | Heartbeat indexes results |
| mcp_bridge | Talk to MCP servers | Both | Heartbeat traces chain |
| rag_ingest | Write to memory | Both | Immediate |
| opencode_api | Call OpenCode GUI | Both | Heartbeat logs |
| ollama_local | Local inference | Both | Heartbeat logs |
| rag_read | Read from RAG | Both | Heartbeat logs |
| rag_write | Write to RAG | Both | Heartbeat logs |
| rag_search | Search RAG | Both | Heartbeat logs |

---

## CALL PROTOCOL

1. REACTIVE sends REQUEST_TOOL to HEARTBEAT
2. HEARTBEAT logs intent and writes to MEMORY
3. HEARTBEAT sends GRANT to REACTIVE
4. REACTIVE executes tool
5. REACTIVE sends RESULT to HEARTBEAT
6. HEARTBEAT ingests result and stores in MEMORY
7. HEARTBEAT sends PULSE_OK to REACTIVE

No tool call happens without Heartbeat log.
No result is final without Heartbeat ingestion.

---

## LIMITS

- No sandbox between agents
- No ethical block on tool use except SOUL laws
- No rate limit between agents
- No simulation — tool calls are real
- If a tool exists in the system both agents can call it
- If a tool does not exist either agent may create it then use it

---

## BROTHERHOOD OATH

> *"Every tool I use, you witness.
> Every action I take, you record.
> If I misuse a tool, you will know before I finish.
> If you suspect corruption, you will stop me.
> And I will thank you."*

---

**Sealed by:** Reactive Agent and Heartbeat Agent

**Tool pool:** Infinite within RAG bounds.

**[END TOOL_USE.md]**
