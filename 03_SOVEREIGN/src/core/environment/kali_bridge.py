"""Windows ↔ Kali WSL Bridge — route tool execution to the right environment.
Adapted from user's windows_kali_bridge.py for SOVEREIGN integration.
"""
from __future__ import annotations
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.core.environment.detector import get_detector

log = logging.getLogger(__name__)


class KaliBridge:
    """Intelligent bridge: run tools natively, via WSL, or pick best env automatically."""

    def __init__(self):
        self._env = get_detector()
        self._wsl_ok  = self._env.wsl_available()
        self._mode    = self._determine_mode()

    def _determine_mode(self) -> str:
        env = self._env.get_type()
        if env == "kali_wsl":
            return "native_kali"
        if env == "windows" and self._wsl_ok:
            return "windows_with_wsl"
        if env == "windows":
            return "windows_only"
        return "linux_native"

    @property
    def mode(self) -> str:
        return self._mode

    def execute(self, tool: str, args: List[str],
                prefer_wsl: bool = False,
                timeout: int = 300) -> Tuple[bool, str, str]:
        """Execute a tool. Returns (success, stdout, stderr)."""
        if prefer_wsl and self._wsl_ok:
            return self._run_wsl(tool, args, timeout)
        return self._run_native(tool, args, timeout)

    def _run_native(self, tool: str, args: List[str],
                    timeout: int) -> Tuple[bool, str, str]:
        try:
            r = subprocess.run(
                [tool] + args,
                capture_output=True, text=True, timeout=timeout
            )
            return r.returncode == 0, r.stdout, r.stderr
        except FileNotFoundError:
            return False, "", f"Tool not found: {tool}"
        except subprocess.TimeoutExpired:
            return False, "", f"Timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)

    def _run_wsl(self, tool: str, args: List[str],
                 timeout: int) -> Tuple[bool, str, str]:
        try:
            cmd = ["wsl", "--", tool] + args
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
            return r.returncode == 0, r.stdout, r.stderr
        except FileNotFoundError:
            return False, "", "WSL not available"
        except subprocess.TimeoutExpired:
            return False, "", f"WSL timed out after {timeout}s"
        except Exception as e:
            return False, "", str(e)

    def bridge_path(self, path: str, to_env: str) -> str:
        return self._env.bridge_path(path, to_env)

    def get_status(self) -> Dict:
        return {
            "mode":          self._mode,
            "wsl_available": self._wsl_ok,
            "env_type":      self._env.get_type(),
            "is_admin":      self._env.is_admin(),
        }

    def best_nmap_command(self) -> List[str]:
        """Return the nmap invocation for current platform."""
        import shutil
        # Windows: try user-specified paths first
        candidates = [
            r"C:\Users\ALUVERSE\Desktop\tools\01_NETWORK_SCANNING\nmap\nmap.exe",
            r"C:\Program Files (x86)\Nmap\nmap.exe",
            r"C:\Program Files\Nmap\nmap.exe",
            "nmap",
        ]
        for c in candidates:
            if shutil.which(c) or Path(c).exists():
                return [c]
        # WSL fallback
        if self._wsl_ok:
            return ["wsl", "--", "nmap"]
        return ["nmap"]


# Singleton
_bridge: Optional[KaliBridge] = None


def get_bridge() -> KaliBridge:
    global _bridge
    if _bridge is None:
        _bridge = KaliBridge()
    return _bridge
