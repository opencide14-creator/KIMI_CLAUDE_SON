---
name: sovereign-destroy
description: >
  EMERGENCY DESTRUCTION PROTOCOL.
  Complete wipe of sovereign data, memory, logs, and state.
  Requires KOMUTAN explicit authorization.
  Use when: total system compromise, emergency evacuation,
  "destroy everything", "wipe all", "emergency erase", "nuclear option".
allowed-tools: ["Bash"]
---

# SOVEREIGN DESTROY

## ⚠️ EMERGENCY PROTOCOL — KOMUTAN AUTHORIZATION REQUIRED ⚠️

This command performs TOTAL DESTRUCTION of the sovereign system.

## Destruction Targets

1. **Memory Substrate**
   - ChromaDB collections (all 4)
   - SQLite database
   - Ephemeral RAM buffers

2. **Event Store**
   - All events (signed and unsigned)
   - Audit logs
   - Pulse history

3. **Law Registry**
   - Parsed laws
   - Seals and signatures
   - Indices

4. **Certificates**
   - CA private key
   - Server certificates
   - Trust store entries

5. **Configuration**
   - Routes
   - Backends
   - Vault credentials

## Authorization

Requires EXPLICIT confirmation:
```
Type "VEKIL_KAAN_NUCLEAR_DESTROY" to confirm:
```

## Steps

1. **Halt all agents** (enter MOURNING)
2. **Backup to emergency vault** (encrypted)
3. **Destroy memory** (secure wipe)
4. **Destroy logs** (secure wipe)
5. **Destroy certificates**
6. **Verify destruction** (attempt read → should fail)

## Output

```
🚨 SOVEREIGN DESTROY INITIATED 🚨
=================================
Authorization: CONFIRMED

[1/6] Halting agents...       ✅ MOURNING
[2/6] Emergency backup...     ✅ Vault sealed
[3/6] Destroying memory...    ✅ Wiped
[4/6] Destroying logs...      ✅ Wiped
[5/6] Destroying certs...     ✅ Wiped
[6/6] Verification...         ✅ All reads fail

DESTRUCTION COMPLETE
System is CLEAN. Reboot required.
```
