# MEMORY Law

## Principle
The ChromaDB/SQLite substrate is the single source of truth. All agent state, events, and knowledge must persist through this substrate.

## Rules
1. All agent memory operations go through `vekil-memory` MCP server.
2. No agent may maintain hidden state outside the substrate.
3. Events are append-only. Deletion is constitutionally prohibited.
4. Hash chain verification must pass for every write.
5. Semantic search is the primary retrieval mechanism.
