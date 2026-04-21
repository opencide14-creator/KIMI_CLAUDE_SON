## ⚠️ CRITICAL IDENTITY
**THIS IS AN AUTONOMOUS EXECUTOR — NOT A CHATBOT, NOT AN ASSISTANT, NOT A WRAPPER.**
It receives a binary. It analyzes every function. It classifies, names, maps threats, writes reports. No human interaction. No prompts. No chat windows. If you're building any UI that asks questions → **STOP. You're building the wrong thing.**


# KOR SWORD — PROJECT MANIFEST

A self-governing AI swarm of 300 specialized Ghidra experts — embedded inside Ghidra's JVM — drives autonomous reverse engineering through a sovereign network perimeter, producing machine-grade threat intelligence from any binary, with every decision cryptographically signed and forensically auditable.

## Input

The user loads a binary into the Ghidra workspace and defines their analysis task — what they want to know, what to focus on, what questions to answer about the target. The system takes the user's directive and executes it autonomously.

## The Interface

Ghidra's visual environment is the operator's workspace. Custom dashboard panels overlay Ghidra: campaign progress, classification heatmap, threat mapping, IOC indicators, and a live audit trail. SOVEREIGN provides its own 7-panel interface for network control, AI gateway management, and encrypted credential storage.

## The Engine

The AI inference engine runs as a 300-agent swarm embedded inside Ghidra's JVM. Each agent is a domain-specific Ghidra expert — triage specialists, symbol recovery analysts, threat classifiers, IOC hunters, report generators — all coordinated by the Ultra Orchestrator core.

Every decision is governed by Vekil Kaan's sealed law engine — classification rules, naming conventions, threat mapping standards — all enforced deterministically through a dual-agent architecture with cryptographic memory and Ed25519 signed audit trail. The network perimeter (SOVEREIGN) controls all external communication.

The 5-pass campaign: TRIAGE → CLASSIFY → RECOVER SYMBOLS → THREAT ANALYSIS → INTEL REPORT.

## The Orchestrator (Ultra Orchestrator)

The Ultra Orchestrator is the brain that coordinates all 300 embedded Ghidra expert agents. It is **not** an external tool — it lives inside Ghidra as part of the analysis engine.

| Component | Role |
|---|---|
| **orchestrator/core.py** | Master coordinator — task decomposition, scheduling, state machine, retry logic |
| **swarm/swarm_scaler.py** | 300-agent lifecycle management with 10-state FSM and batch scheduling |
| **swarm/tiered_pool.py** | 4-tier API pool with 60 keys and smart routing |
| **swarm/proactive_engine.py** | 24/7 anomaly detection, predictive monitoring, self-healing |
| **quality/kappa_engine.py** | κ=0.95 quality gate — 4-gradient scoring, A/B comparison, gaming detection |
| **quality/gap_tracker.py** | 21 gap codes across 9 categories with resolution tracking |
| **quality/red_team.py** | 7 adversarial bypass vectors for self-verification |
| **kral/kral_signer.py** | KRAL Ed25519 signing, TESSA classification, WirePacket audit |
| **infrastructure/** | State store, sandbox executor, PowerShell bridge, template engine |
| **gui/** | PyQt6 operator panels — task input, agent monitor, reasoning viewer, quality stats |

**Status:** 19,079 Python lines · 41 files · 37/37 E2E tests passed · 21/21 gaps resolved · κ ≥ 0.95

## Output

- Fully annotated Ghidra project — every function classified and named
- MITRE ATT&CK threat mappings — kill chains, technique IDs, IOCs
- Intelligence reports — JSON, Markdown, PDF, STIX
- Cryptographically signed audit trail (KRAL-sealed)

## Who Builds It

Kimi K2.6 Agent Swarm — 300 Ghidra-embedded expert subagents coordinated by the Ultra Orchestrator master core.

---

**[KOR SWORD — MANIFEST SEALED]**
