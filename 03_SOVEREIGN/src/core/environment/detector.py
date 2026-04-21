"""Environment Detector — detect OS, WSL, available tools, admin rights.
Adapted from user's environment_detector.py for SOVEREIGN integration.
"""
from __future__ import annotations
import logging
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


class EnvironmentDetector:
    """Detect system environment, platform, tools, and privileges."""

    def __init__(self):
        self._info: Dict = {}
        self._tool_cache: Dict[str, Tuple[bool, str]] = {}

    def get_info(self) -> Dict:
        """Return full environment info dict."""
        if not self._info:
            self._info = {
                "platform":       platform.system(),
                "release":        platform.release(),
                "machine":        platform.machine(),
                "python":         platform.python_version(),
                "is_wsl":         self._is_wsl(),
                "is_admin":       self._is_admin(),
                "home":           str(Path.home()),
                "cwd":            str(Path.cwd()),
            }
        return self._info

    def get_type(self) -> str:
        """Return environment type string."""
        system = platform.system()
        if system == "Windows":
            return "windows"
        if system == "Linux":
            return "kali_wsl" if self._is_wsl() else "linux"
        if system == "Darwin":
            return "macos"
        return "unknown"

    def is_windows(self) -> bool:
        return platform.system() == "Windows"

    def is_linux(self) -> bool:
        return platform.system() == "Linux"

    def is_wsl(self) -> bool:
        return self._is_wsl()

    def is_admin(self) -> bool:
        return self._is_admin()

    def check_tool(self, name: str) -> Tuple[bool, str]:
        """Check if a tool is available. Returns (available, path)."""
        if name in self._tool_cache:
            return self._tool_cache[name]
        path = shutil.which(name)
        result = (bool(path), path or "")
        self._tool_cache[name] = result
        return result

    def check_tools(self, names: List[str]) -> Dict[str, Dict]:
        """Check multiple tools. Returns {name: {available, path, version}}."""
        results = {}
        for name in names:
            available, path = self.check_tool(name)
            results[name] = {
                "available": available,
                "path":      path,
                "version":   self._get_version(name) if available else "",
            }
        return results

    def scan_sovereign_tools(self) -> Dict[str, Dict]:
        """Scan all tools SOVEREIGN can use."""
        return self.check_tools([
            "nmap", "masscan", "whatweb", "nikto",
            "sqlmap", "hydra", "curl", "wget",
            "python3", "python", "git",
        ])

    def get_install_suggestion(self, tool: str) -> str:
        """Return install command for a tool on current platform."""
        env = self.get_type()
        suggestions = {
            "windows": {
                "nmap":    "winget install --id Insecure.Nmap -e",
                "python":  "winget install --id Python.Python.3 -e",
                "git":     "winget install --id Git.Git -e",
            },
            "linux": {
                "nmap":    "sudo apt install nmap",
                "masscan": "sudo apt install masscan",
                "nikto":   "sudo apt install nikto",
                "sqlmap":  "pip install sqlmap",
                "git":     "sudo apt install git",
            },
            "macos": {
                "nmap":    "brew install nmap",
                "masscan": "brew install masscan",
                "nikto":   "brew install nikto",
                "git":     "brew install git",
            },
        }
        return suggestions.get(env, {}).get(tool, f"Install {tool} manually")

    def wsl_available(self) -> bool:
        """Check if WSL is available (Windows only)."""
        if not self.is_windows():
            return False
        try:
            r = subprocess.run(
                ["wsl", "--list"], capture_output=True, text=True, timeout=8
            )
            return r.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def bridge_path(self, path: str, target_env: str) -> str:
        """Convert file path between Windows and WSL."""
        if target_env == "wsl" and ":" in path and "\\" in path:
            drive = path[0].lower()
            rest  = path[2:].replace("\\", "/")
            return f"/mnt/{drive}{rest}"
        if target_env == "windows" and path.startswith("/mnt/"):
            drive = path[5].upper()
            rest  = path[6:].replace("/", "\\")
            return f"{drive}:{rest}"
        return path

    def generate_report(self) -> Dict:
        """Full environment report for display in SOVEREIGN."""
        info    = self.get_info()
        tools   = self.scan_sovereign_tools()
        missing = [n for n, d in tools.items() if not d["available"]]
        return {
            "info":     info,
            "type":     self.get_type(),
            "tools":    tools,
            "missing":  missing,
            "is_admin": info["is_admin"],
            "is_wsl":   info["is_wsl"],
            "suggestions": {t: self.get_install_suggestion(t) for t in missing},
        }

    # ── Private ────────────────────────────────────────────────────

    def _is_wsl(self) -> bool:
        try:
            return "microsoft" in Path("/proc/version").read_text().lower()
        except OSError:
            return False

    def _is_admin(self) -> bool:
        try:
            if platform.system() == "Windows":
                import ctypes
                return bool(ctypes.windll.shell32.IsUserAnAdmin())
            return os.geteuid() == 0
        except Exception:
            return False

    def _get_version(self, tool: str) -> str:
        try:
            r = subprocess.run(
                [tool, "--version"], capture_output=True, text=True, timeout=5
            )
            out = r.stdout or r.stderr
            return out.splitlines()[0].strip()[:60] if out else ""
        except Exception:
            return ""


# Singleton
_detector: Optional[EnvironmentDetector] = None


def get_detector() -> EnvironmentDetector:
    global _detector
    if _detector is None:
        _detector = EnvironmentDetector()
    return _detector
