#!/usr/bin/env python3
"""
Stop hook script for ultra-swarm.
Checks if active tasks exist and whether they are in a safe state to stop.
"""

import json
import os
import sys
from pathlib import Path


def main():
    try:
        hook_input = json.load(sys.stdin)
    except json.JSONDecodeError:
        hook_input = {}

    ultra_home = os.environ.get("ULTRA_SWARM_HOME", os.path.join(Path.home(), ".claude", "ultra-swarm"))
    tasks_dir = os.path.join(ultra_home, "tasks")

    active_tasks = []
    incomplete_tasks = []

    if os.path.isdir(tasks_dir):
        for fname in os.listdir(tasks_dir):
            if fname.startswith("task_") and fname.endswith(".json") and not fname.endswith("_checkpoint.json"):
                fpath = os.path.join(tasks_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        task = json.load(f)

                    status = task.get("status")
                    task_id = task.get("task_id", fname)

                    if status in ("queued", "running"):
                        active_tasks.append(task_id)

                        # Check checkpoint exists
                        cp_file = os.path.join(tasks_dir, f"{task_id}_checkpoint.json")
                        has_checkpoint = os.path.isfile(cp_file)

                        # Check quality gate
                        qg_file = os.path.join(tasks_dir, f"{task_id}_quality.json")
                        has_quality = os.path.isfile(qg_file)
                        quality_pass = False
                        if has_quality:
                            try:
                                with open(qg_file, "r", encoding="utf-8") as f:
                                    qg = json.load(f)
                                quality_pass = qg.get("overall_pass", False)
                            except Exception:
                                pass

                        if not has_checkpoint or not quality_pass:
                            incomplete_tasks.append({
                                "id": task_id,
                                "checkpoint": has_checkpoint,
                                "quality_pass": quality_pass
                            })
                except Exception:
                    pass

    result = {"continue": True, "suppressOutput": True, "systemMessage": ""}

    if active_tasks:
        messages = [f"[UltraSwarm] Dikkat: {len(active_tasks)} aktif swarm gorevi var."]

        if incomplete_tasks:
            messages.append("Asagidaki gorevler tamamlanmamis durumda:")
            for t in incomplete_tasks:
                cp_status = "var" if t["checkpoint"] else "YOK"
                qg_status = "PASS" if t["quality_pass"] else "FAIL/BILINMIYOR"
                messages.append(f"  - {t['id']}: Checkpoint {cp_status}, Quality Gate {qg_status}")
            messages.append("Devam etmek istiyor musunuz, yoksa once gorevleri tamamlamam mi?")
            result["suppressOutput"] = False
            result["systemMessage"] = "\n".join(messages)
        else:
            # All active tasks have checkpoints and quality passed
            messages.append("Tum aktif gorevlerde checkpoint alindi ve quality gate passed. Guvenli durduruabilirsiniz.")
            result["suppressOutput"] = False
            result["systemMessage"] = "\n".join(messages)

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
