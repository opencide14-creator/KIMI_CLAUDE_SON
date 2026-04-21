---
title: Reactive Agent
category: entities
tags: [agent, reactive, react-loop]
---

# Reactive Agent

The action arm of VEKIL-KAAN. Executes the THINK-DECIDE-ACT-FEED loop.

## Role

The Reactive Agent handles all tool execution and world-interaction.
It works in close partnership with the [[Heartbeat Agent]].

## Capabilities

- ReAct reasoning loop inspired by OODA
- Tool invocation via 7-step call protocol
- Goal evaluation against RAG evidence
- PULSE_R emission every 5 actions

The agent must never act without a valid [[Heartbeat Protocol]] pulse.
It enters safe mode if PULSE_H is not received within 60 seconds.

#agent #reactive #ooda
