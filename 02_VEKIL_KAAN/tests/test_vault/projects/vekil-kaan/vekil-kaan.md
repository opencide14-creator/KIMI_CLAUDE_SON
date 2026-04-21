---
title: VEKIL-KAAN Project
category: project
tags: [vekil-kaan, rag, agents, production]
---

# VEKIL-KAAN Project

A closed-world dual-agent system where the RAG environment is the world.
Two agents inhabit a shared [[Memory Substrate]] governed by [[Soul Laws]].

## Architecture

The system boots in strict sequence: MEMORY, RAG, LAWS, PREFLIGHT, AGENTS.
Each phase must complete before the next begins. Any failure halts the system.

## Key Components

Memory uses ChromaDB for vectors and SQLite for metadata and audit log.
The Law Engine is a deterministic markdown parser with KRAL Ed25519 seal.
Agents are Reactive for action and Heartbeat for continuity.

## Current Phase

Phase 3 is the Obsidian Ingest Pipeline.

#vekil-kaan #architecture #status
