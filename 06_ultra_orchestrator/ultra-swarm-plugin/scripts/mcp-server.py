#!/usr/bin/env python3
"""
UltraSwarm MCP Server — stdio transport
Implements Model Context Protocol over JSON-RPC 2.0 via stdin/stdout.

Communication strategy with UltraOrchestrator (cascading fallback):
1. Direct Python imports (when running in same env as orchestrator source)
2. SQLite DB polling (if orchestrator uses SQLite for task state)
3. File-based communication (task JSON + log files)
4. HTTP API (if user started orchestrator HTTP server separately)
"""

import json
import os
import sys
import time
import uuid
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
ULTRA_HOME = os.environ.get(
    "ULTRA_SWARM_HOME",
    str(Path.home() / ".claude" / "ultra-swarm")
)
EXE_PATH = os.environ.get("ULTRA_EXE_PATH", "")
CONFIG_PATH = os.environ.get("ULTRA_CONFIG_PATH", "")
PYTHONPATH = os.environ.get("PYTHONPATH", "")

# Ensure directories exist
os.makedirs(os.path.join(ULTRA_HOME, "tasks"), exist_ok=True)
os.makedirs(os.path.join(ULTRA_HOME, "logs"), exist_ok=True)
os.makedirs(os.path.join(ULTRA_HOME, "checkpoints"), exist_ok=True)

# ---------------------------------------------------------------------------
# Logging (to stderr so stdout stays clean for JSON-RPC)
# ---------------------------------------------------------------------------
def log(msg: str) -> None:
    print(f"[UltraSwarm MCP] {msg}", file=sys.stderr, flush=True)

# ---------------------------------------------------------------------------
# UltraOrchestrator Connector — Cascading Fallback
# ---------------------------------------------------------------------------
class UltraConnector:
    """Connects to UltraOrchestrator via multiple strategies."""

    def __init__(self):
        self._engine = None
        self._direct_import_ok = False
        self._try_direct_import()

    def _try_direct_import(self):
        """Strategy 1: Direct Python imports."""
        try:
            if PYTHONPATH and PYTHONPATH not in sys.path:
                sys.path.insert(0, PYTHONPATH)
            # Attempt import
            from ultra_orchestrator.orchestrator.core import SwarmEngine  # type: ignore
            self._engine_class = SwarmEngine
            self._direct_import_ok = True
            log("Direct Python import: SUCCESS")
        except Exception as e:
            log(f"Direct Python import: FAILED ({e})")
            self._direct_import_ok = False

    # ------------------------------------------------------------------
    # Task Operations
    # ------------------------------------------------------------------

    def submit_task(self, description: str, priority: int = 3, keys: Optional[List[str]] = None,
                    tier: str = "coding", timeout: int = 180) -> Dict[str, Any]:
        """Submit a new task. Returns task metadata."""
        task_id = f"task_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        task_meta = {
            "task_id": task_id,
            "description": description,
            "status": "queued",
            "priority": priority,
            "keys": keys or [],
            "tier": tier,
            "timeout": timeout,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
            "started_at": None,
            "completed_at": None,
            "pid": None,
            "checkpoint_file": os.path.join(ULTRA_HOME, "checkpoints", f"{task_id}_checkpoint.json"),
            "log_file": os.path.join(ULTRA_HOME, "logs", f"{task_id}.log"),
            "quality_file": os.path.join(ULTRA_HOME, "checkpoints", f"{task_id}_quality.json"),
            "progress_percent": 0,
            "error": None,
        }

        # Persist task metadata
        meta_path = os.path.join(ULTRA_HOME, "tasks", f"{task_id}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(task_meta, f, indent=2)

        # Try to launch via direct import first
        if self._direct_import_ok:
            try:
                self._launch_via_import(task_meta)
                return self.get_task_status(task_id)
            except Exception as e:
                log(f"Direct import launch failed: {e}, falling back to EXE")

        # Fallback: EXE subprocess
        self._launch_via_exe(task_meta)
        return self.get_task_status(task_id)

    def _launch_via_import(self, task_meta: Dict[str, Any]):
        """Launch task using direct Python import."""
        # This would integrate with the actual SwarmEngine
        # For now, we update status and let the EXE handle actual execution
        # In a full implementation, this would call engine.submit_task()
        log("Launch via direct import not yet fully integrated — using EXE fallback")
        raise NotImplementedError("Direct import launch pending full integration")

    def _launch_via_exe(self, task_meta: Dict[str, Any]):
        """Launch task via EXE subprocess."""
        if not EXE_PATH or not os.path.isfile(EXE_PATH):
            raise FileNotFoundError(f"EXE not found: {EXE_PATH}")

        log_file = task_meta["log_file"]
        meta_path = os.path.join(ULTRA_HOME, "tasks", f"{task_meta['task_id']}.json")

        cmd = [
            EXE_PATH,
            "--task-file", meta_path,
        ]
        if CONFIG_PATH and os.path.isfile(CONFIG_PATH):
            cmd.extend(["--config", CONFIG_PATH])

        try:
            with open(log_file, "w", encoding="utf-8") as lf:
                proc = subprocess.Popen(
                    cmd,
                    stdout=lf,
                    stderr=subprocess.STDOUT,
                    cwd=os.path.dirname(EXE_PATH) if EXE_PATH else None,
                )
            task_meta["pid"] = proc.pid
            task_meta["status"] = "running"
            task_meta["started_at"] = datetime.now(timezone.utc).isoformat()
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(task_meta, f, indent=2)
            log(f"Launched {task_meta['task_id']} with PID {proc.pid}")
        except Exception as e:
            task_meta["status"] = "failed"
            task_meta["error"] = str(e)
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(task_meta, f, indent=2)
            raise

    def get_task_status(self, task_id: str) -> Dict[str, Any]:
        """Get current status of a task."""
        meta_path = os.path.join(ULTRA_HOME, "tasks", f"{task_id}.json")
        if not os.path.isfile(meta_path):
            return {"error": f"Task not found: {task_id}"}

        with open(meta_path, "r", encoding="utf-8") as f:
            task = json.load(f)

        # Update status from checkpoint if available
        cp_path = task.get("checkpoint_file", "")
        if os.path.isfile(cp_path):
            try:
                with open(cp_path, "r", encoding="utf-8") as f:
                    cp = json.load(f)
                task["progress_percent"] = cp.get("progress_percent", task.get("progress_percent", 0))
                task["current_phase"] = cp.get("current_phase", "unknown")
                task["completed_subtasks"] = cp.get("completed_subtasks", 0)
                task["total_subtasks"] = cp.get("total_subtasks", 0)
                if cp.get("status"):
                    task["status"] = cp["status"]
            except Exception as e:
                log(f"Error reading checkpoint: {e}")

        # Check if process is still alive
        pid = task.get("pid")
        if pid and task["status"] == "running":
            if not self._is_process_alive(pid):
                task["status"] = "completed"  # Or failed — we'd need exit code
                task["completed_at"] = datetime.now(timezone.utc).isoformat()
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(task, f, indent=2)

        return task

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process is still running."""
        try:
            import psutil
            return psutil.pid_exists(pid)
        except ImportError:
            # Fallback for Windows without psutil
            try:
                import ctypes
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(1, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            except Exception:
                return True  # Assume alive if we can't check

    def list_active_tasks(self) -> List[Dict[str, Any]]:
        """List all active (queued or running) tasks."""
        tasks = []
        tasks_dir = os.path.join(ULTRA_HOME, "tasks")
        if not os.path.isdir(tasks_dir):
            return tasks

        for fname in sorted(os.listdir(tasks_dir)):
            if fname.startswith("task_") and fname.endswith(".json") and not fname.endswith("_checkpoint.json"):
                try:
                    with open(os.path.join(tasks_dir, fname), "r", encoding="utf-8") as f:
                        task = json.load(f)
                    if task.get("status") in ("queued", "running"):
                        tasks.append(self.get_task_status(task["task_id"]))
                except Exception as e:
                    log(f"Error reading task file {fname}: {e}")
        return tasks

    def get_quality_report(self, task_id: str) -> Dict[str, Any]:
        """Get quality gate report for a task."""
        qg_path = os.path.join(ULTRA_HOME, "checkpoints", f"{task_id}_quality.json")
        if os.path.isfile(qg_path):
            with open(qg_path, "r", encoding="utf-8") as f:
                return json.load(f)

        # Fallback: try to read from task metadata
        task = self.get_task_status(task_id)
        if "error" in task:
            return task
        return {"error": "Quality report not yet available", "task_id": task_id}

    def get_sandbox_logs(self, task_id: str) -> Dict[str, Any]:
        """Get sandbox execution logs for a task."""
        sb_path = os.path.join(ULTRA_HOME, "checkpoints", f"{task_id}_sandbox.json")
        if os.path.isfile(sb_path):
            with open(sb_path, "r", encoding="utf-8") as f:
                return json.load(f)

        # Fallback: scan log file for sandbox entries
        log_file = os.path.join(ULTRA_HOME, "logs", f"{task_id}.log")
        if os.path.isfile(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                sandbox_lines = [l for l in lines if "SANDBOX" in l.upper() or "BLOCKED" in l.upper() or "WARNING" in l.upper()]
                return {
                    "task_id": task_id,
                    "source": "log_file",
                    "sandbox_entries": sandbox_lines[-50:]  # Last 50 lines
                }
            except Exception as e:
                return {"error": f"Error reading log: {e}"}

        return {"error": "Sandbox logs not yet available", "task_id": task_id}

    def cancel_task(self, task_id: str) -> Dict[str, Any]:
        """Cancel a running task."""
        task = self.get_task_status(task_id)
        if "error" in task:
            return task

        previous_status = task.get("status")
        pid = task.get("pid")
        if pid and task["status"] == "running":
            try:
                if sys.platform == "win32":
                    # Windows: use taskkill for reliable termination
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/T", "/F"],
                        capture_output=True,
                        check=False
                    )
                else:
                    import signal
                    os.kill(pid, signal.SIGTERM)
                    time.sleep(2)
                    if self._is_process_alive(pid):
                        os.kill(pid, signal.SIGKILL)
            except Exception as e:
                log(f"Error killing process {pid}: {e}")

        task["status"] = "cancelled"
        task["completed_at"] = datetime.now(timezone.utc).isoformat()
        meta_path = os.path.join(ULTRA_HOME, "tasks", f"{task_id}.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(task, f, indent=2)

        return {"task_id": task_id, "status": "cancelled", "previous_status": previous_status}

    def update_config(self, key: str, value: Any) -> Dict[str, Any]:
        """Update a config value in default_config.yaml."""
        if not CONFIG_PATH or not os.path.isfile(CONFIG_PATH):
            return {"error": f"Config file not found: {CONFIG_PATH}"}

        # Pre-check write permission
        if not os.access(CONFIG_PATH, os.W_OK):
            return {"error": f"Config file is not writable: {CONFIG_PATH}. Run with appropriate permissions."}

        try:
            import yaml
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}

            # Support nested keys via dot notation: "hard_limits.max_concurrent"
            keys = key.split(".")
            target = config
            for k in keys[:-1]:
                if k not in target:
                    target[k] = {}
                target = target[k]
            target[keys[-1]] = value

            # Atomic write
            config_dir = os.path.dirname(CONFIG_PATH)
            tmp_path = os.path.join(config_dir, "default_config.yaml.tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
            os.replace(tmp_path, CONFIG_PATH)

            return {"success": True, "key": key, "value": value, "config_path": CONFIG_PATH}
        except Exception as e:
            return {"error": f"Config update failed: {e}"}

    def get_config(self) -> Dict[str, Any]:
        """Read current config."""
        if not CONFIG_PATH or not os.path.isfile(CONFIG_PATH):
            return {"error": f"Config file not found: {CONFIG_PATH}"}

        try:
            import yaml
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f) or {}
            return {"config": config, "path": CONFIG_PATH}
        except Exception as e:
            return {"error": f"Config read failed: {e}"}


# ---------------------------------------------------------------------------
# MCP Protocol Implementation
# ---------------------------------------------------------------------------
class MCPServer:
    """Simple MCP stdio server implementing JSON-RPC 2.0."""

    def __init__(self):
        self.connector = UltraConnector()
        self._tools = self._define_tools()

    def _define_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "submit_task",
                "description": "Submit a new task to UltraOrchestrator swarm. Returns task metadata with ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "description": {"type": "string", "description": "Task description"},
                        "priority": {"type": "integer", "description": "Priority 1-5", "default": 3},
                        "keys": {"type": "array", "items": {"type": "string"}, "description": "API key IDs to use"},
                        "tier": {"type": "string", "description": "Task tier", "default": "coding"},
                        "timeout": {"type": "integer", "description": "Timeout in seconds", "default": 180}
                    },
                    "required": ["description"]
                }
            },
            {
                "name": "get_task_status",
                "description": "Get current status of a specific task by ID.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"}
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "list_active_tasks",
                "description": "List all active (queued or running) tasks.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "get_quality_report",
                "description": "Get quality gate report for a task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"}
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "get_sandbox_logs",
                "description": "Get sandbox execution logs for a task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"}
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "cancel_task",
                "description": "Cancel a running or queued task.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID"}
                    },
                    "required": ["task_id"]
                }
            },
            {
                "name": "update_config",
                "description": "Update a value in default_config.yaml. Supports dot notation for nested keys.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string", "description": "Config key (e.g., hard_limits.max_concurrent)"},
                        "value": {"type": "any", "description": "New value"}
                    },
                    "required": ["key", "value"]
                }
            },
            {
                "name": "get_config",
                "description": "Read the current default_config.yaml contents.",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            }
        ]

    def send(self, msg: Dict[str, Any]) -> None:
        """Send a JSON-RPC message to stdout."""
        print(json.dumps(msg), flush=True)

    def handle(self, raw: str) -> None:
        """Handle a single JSON-RPC message."""
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self.send({"jsonrpc": "2.0", "error": {"code": -32700, "message": "Parse error"}, "id": None})
            return

        method = req.get("method", "")
        msg_id = req.get("id")
        params = req.get("params", {})

        if method == "initialize":
            self.send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ultra-swarm-mcp", "version": "1.0.0"}
                }
            })

        elif method == "notifications/initialized":
            pass  # No response needed for notifications

        elif method == "tools/list":
            self.send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": self._tools}
            })

        elif method == "tools/call":
            self._handle_tool_call(msg_id, params)

        elif method in ("shutdown", "exit"):
            self.send({"jsonrpc": "2.0", "id": msg_id, "result": {}})
            sys.exit(0)

        else:
            self.send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            })

    def _handle_tool_call(self, msg_id: Any, params: Dict[str, Any]) -> None:
        """Execute a tool call and send result."""
        name = params.get("name", "")
        arguments = params.get("arguments", {})

        try:
            if name == "submit_task":
                result = self.connector.submit_task(
                    description=arguments.get("description", ""),
                    priority=arguments.get("priority", 3),
                    keys=arguments.get("keys"),
                    tier=arguments.get("tier", "coding"),
                    timeout=arguments.get("timeout", 180)
                )
            elif name == "get_task_status":
                result = self.connector.get_task_status(arguments.get("task_id", ""))
            elif name == "list_active_tasks":
                result = self.connector.list_active_tasks()
            elif name == "get_quality_report":
                result = self.connector.get_quality_report(arguments.get("task_id", ""))
            elif name == "get_sandbox_logs":
                result = self.connector.get_sandbox_logs(arguments.get("task_id", ""))
            elif name == "cancel_task":
                result = self.connector.cancel_task(arguments.get("task_id", ""))
            elif name == "update_config":
                result = self.connector.update_config(
                    key=arguments.get("key", ""),
                    value=arguments.get("value")
                )
            elif name == "get_config":
                result = self.connector.get_config()
            else:
                result = {"error": f"Unknown tool: {name}"}

            self.send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}
            })
        except Exception as e:
            self.send({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"content": [{"type": "text", "text": json.dumps({"error": str(e)}, indent=2)}], "isError": True}
            })

    def run(self) -> None:
        """Main loop: read lines from stdin, handle each."""
        log("MCP Server started. Waiting for requests...")
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            self.handle(line)


if __name__ == "__main__":
    server = MCPServer()
    server.run()
