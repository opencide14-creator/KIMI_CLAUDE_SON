# Memory Architecture

The memory substrate is the world, not a service the agents call.

## ChromaDB

Four collections: obsidian_knowledge, agent_events, law_registry, session_context.
Each collection has a specific purpose and retention policy.

## SQLite

Metadata, event store, audit log, memory snapshots.
All tables are append-only enforced by database triggers.

## Root Hash

SHA-256 over collection sizes plus sorted last-20 event IDs.
Both agents compute this independently. Mismatch triggers AWAIT_RESYNC.
