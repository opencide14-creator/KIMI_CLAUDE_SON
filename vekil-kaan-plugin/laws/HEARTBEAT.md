# HEARTBEAT Law

## Principle
No action without validation. The heartbeat agent maintains the pulse of the system.

## Rules
1. Reactive agent must receive PULSE_OK before tool execution.
2. Heartbeat interval is 15 seconds maximum.
3. Missing 3 consecutive pulses triggers safe mode (all actions suspended).
4. Heartbeat records every pulse to the substrate.
5. Mourning timeout: 60 seconds before agent is declared dead.
