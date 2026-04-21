#!/usr/bin/env python3
"""
PreToolUse hook for Bash commands.
Warns if user is about to run python main.py or UltraOrchestrator.exe
while an active swarm task exists (could conflict).
"""

import json
import os
import sys
import re


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    tool_input = hook_input.get("tool_input", {})
    command = tool_input.get("command", "")

    # Check if command is running orchestrator
    is_orchestrator = bool(re.search(
        r'(UltraOrchestrator\.exe|python.*main\.py|python.*-m\s+ultra_orchestrator)',
        command,
        re.IGNORECASE
    ))

    if not is_orchestrator:
        # Not our concern
        print(json.dumps({"continue": True, "suppressOutput": True}))
        return 0

    # Check for active swarm tasks
    ultra_home = os.environ.get("ULTRA_SWARM_HOME", os.path.join(os.path.expanduser("~"), ".claude", "ultra-swarm"))
    active_count = 0
    tasks_dir = os.path.join(ultra_home, "tasks")

    if os.path.isdir(tasks_dir):
        for fname in os.listdir(tasks_dir):
            if fname.startswith("task_") and fname.endswith(".json") and not fname.endswith("_checkpoint.json"):
                try:
                    with open(os.path.join(tasks_dir, fname), "r", encoding="utf-8") as f:
                        task = json.load(f)
                    if task.get("status") in ("queued", "running"):
                        active_count += 1
                except Exception:
                    pass

    if active_count > 0:
        result = {
            "continue": True,
            "suppressOutput": False,
            "systemMessage": (
                f"[UltraSwarm] UYARI: {active_count} aktif swarm gorevi var. "
                f"Bu komut calisirken cakisma olabilir. "
                f"Once aktif gorevleri kontrol etmemi ister misiniz? "
                f"Yoksa devam etmek istiyorsaniz bunu gormezden gelebilirsiniz."
            )
        }
    else:
        result = {"continue": True, "suppressOutput": True}

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
