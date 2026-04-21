#!/usr/bin/env python3
"""
SessionStart hook script for ultra-swarm plugin.
Validates environment on Claude Code session start.
"""

import json
import os
import sys
from pathlib import Path


def main():
    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    cwd = hook_input.get("cwd", os.getcwd())
    issues = []
    warnings = []

    # Determine UltraSwarm home
    ultra_home = os.environ.get("ULTRA_SWARM_HOME", os.path.join(Path.home(), ".claude", "ultra-swarm"))
    exe_path = os.environ.get("ULTRA_EXE_PATH", "")
    config_path = os.environ.get("ULTRA_CONFIG_PATH", "")

    # 1. Check .claude/ultra-swarm/ directory
    if not os.path.isdir(ultra_home):
        try:
            os.makedirs(ultra_home, exist_ok=True)
            os.makedirs(os.path.join(ultra_home, "tasks"), exist_ok=True)
            os.makedirs(os.path.join(ultra_home, "logs"), exist_ok=True)
            warnings.append(f"Created UltraSwarm home directory: {ultra_home}")
        except OSError as e:
            issues.append(f"Cannot create UltraSwarm home directory: {e}")

    # 2. Check EXE exists
    if exe_path:
        if not os.path.isfile(exe_path):
            issues.append(f"UltraOrchestrator.exe not found at: {exe_path}")
    else:
        warnings.append("ULTRA_EXE_PATH not set. Configure .claude/ultra-swarm.local.md")

    # 3. Check config readable
    if config_path:
        if not os.path.isfile(config_path):
            issues.append(f"Config file not found at: {config_path}")
        else:
            try:
                import yaml
                with open(config_path, "r", encoding="utf-8") as f:
                    yaml.safe_load(f)
            except Exception as e:
                issues.append(f"Config file parse error: {e}")
    else:
        warnings.append("ULTRA_CONFIG_PATH not set. Configure .claude/ultra-swarm.local.md")

    # 4. Check for active tasks from previous session
    active_tasks = []
    tasks_dir = os.path.join(ultra_home, "tasks")
    if os.path.isdir(tasks_dir):
        for fname in os.listdir(tasks_dir):
            if fname.startswith("task_") and fname.endswith(".json") and not fname.endswith("_checkpoint.json"):
                fpath = os.path.join(tasks_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        task = json.load(f)
                    if task.get("status") in ("queued", "running"):
                        active_tasks.append(task.get("task_id", fname))
                except Exception:
                    pass

    if active_tasks:
        warnings.append(f"Found {len(active_tasks)} active task(s) from previous session: {', '.join(active_tasks)}")

    # Output result
    result = {
        "continue": True,
        "suppressOutput": len(issues) == 0 and len(warnings) == 0,
        "systemMessage": ""
    }

    messages = []
    if issues:
        messages.append("[UltraSwarm] CRITICAL ISSUES:")
        messages.extend(f"  - {i}" for i in issues)
    if warnings:
        messages.append("[UltraSwarm] WARNINGS:")
        messages.extend(f"  - {w}" for w in warnings)
    if active_tasks:
        messages.append(f"[UltraSwarm] Aktif gorev(ler): {len(active_tasks)}. Kontrol etmemi ister misiniz?")

    if messages:
        result["systemMessage"] = "\n".join(messages)
        result["suppressOutput"] = False

    print(json.dumps(result))
    return 0 if not issues else 2


if __name__ == "__main__":
    sys.exit(main())
