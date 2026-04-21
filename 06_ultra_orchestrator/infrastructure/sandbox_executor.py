#!/usr/bin/env python3
"""
sandbox_executor.py — Production-grade sandboxed code execution engine.

Provides the SandboxExecutor class for securely running Python and PowerShell
code within isolated temporary environments. Includes syntax validation, safety
pattern scanning, timeout enforcement, automatic resource cleanup, and optional
pytest integration for code-with-test workflows.

This module is designed for integration with the PowerShellBridge but includes
fallback subprocess-based execution to remain self-sufficient when the bridge
is unavailable or lacks specific methods.

Usage::

    from sandbox_executor import SandboxExecutor, PowerShellBridge

    ps = PowerShellBridge()
    executor = SandboxExecutor(ps_bridge=ps, timeout_seconds=30)

    result = await executor.run_code_with_test(
        code="print('hello')",
        test_code="def test_hello(): assert True",
    )
"""

import os
import re
import ast
import sys
import time
import uuid
import shutil
import asyncio
import logging
import tempfile
import subprocess
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT_SECONDS: int = 30
SANDBOX_FILE_PREFIX: str = "sandbox_"
TEST_FILE_PREFIX: str = "test_"
PYTHON_FILE_SUFFIX: str = ".py"
POWERSHELL_FILE_SUFFIX: str = ".ps1"

# ---------------------------------------------------------------------------
# Safety pattern definitions
# ---------------------------------------------------------------------------

BLOCKED_PATTERNS: List[Tuple[str, str]] = [
    (r"os\.system\s*\(", "os.system"),
    (r"subprocess\.call\s*\(", "subprocess.call"),
    (r"subprocess\.Popen\s*\(", "subprocess.Popen"),
    (r"subprocess\.run\s*\(", "subprocess.run"),
    (r"eval\s*\(", "eval()"),
    (r"exec\s*\(", "exec()"),
    (r"__import__\s*\(", "__import__()"),
    (r"importlib\.import_module\s*\(", "importlib.import_module()"),
    (r"socket\.", "socket module"),
    (r"urllib\.request", "urllib.request"),
    (r"requests\.", "requests library"),
    (r"import\s+ctypes", "ctypes import"),
    (r"from\s+ctypes", "ctypes import"),
    (r"import\s+multiprocessing", "multiprocessing import"),
    (r"from\s+multiprocessing", "multiprocessing import"),
]

WARNED_PATTERNS: List[Tuple[str, str]] = [
    (r"import\s+threading", "threading"),
    (r"from\s+threading", "threading"),
    (r"import\s+asyncio", "asyncio"),
    (r"from\s+asyncio", "asyncio"),
]


# ---------------------------------------------------------------------------
# PowerShellBridge reference / stub
# ---------------------------------------------------------------------------

class PowerShellBridge:
    """
    Reference / stub implementation of the PowerShellBridge.

    The real PowerShellBridge lives in ``powershell_bridge.py`` and is expected
    to provide at minimum:

    * ``execute_script(script: str, timeout: int = None) -> dict``
    * ``execute_python_code(code: str, timeout: int = None) -> dict``
    * ``run_pytest(test_code: str, timeout: int = None) -> dict``
    * ``validate_script_safety(script: str) -> dict``

    This stub provides friendly no-op fallbacks so that
    ``SandboxExecutor`` can operate standalone when the bridge is not
    wired in.
    """

    def __init__(self) -> None:
        self.logger = logging.getLogger(f"{__name__}.PowerShellBridge")

    # --- Internal helpers -------------------------------------------------

    def _build_ps_command(self, script: str) -> str:
        """Wrap a PowerShell script in a Base64-encoded command for safe execution."""
        import base64
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        return f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}"

    async def _run_subprocess(
        self,
        cmd: List[str],
        timeout: int,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run a subprocess with timeout, capture stdout/stderr, and return
        a standardized result dictionary.
        """
        start_ms = int(time.time() * 1000)
        stdout_data = ""
        stderr_data = ""
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                exit_code = proc.returncode if proc.returncode is not None else -1
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                stdout_b, stderr_b = b"", b""
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                stderr_data += "\n[TIMEOUT] Process killed after exceeding timeout."
                exit_code = -1

        except FileNotFoundError as exc:
            stderr_data = f"[ERROR] Executable not found: {exc}"
            exit_code = -1
        except PermissionError as exc:
            stderr_data = f"[ERROR] Permission denied: {exc}"
            exit_code = -1
        except OSError as exc:
            stderr_data = f"[ERROR] OS error running subprocess: {exc}"
            exit_code = -1
        except Exception as exc:
            stderr_data = f"[ERROR] Unexpected error: {exc}"
            exit_code = -1

        duration_ms = int(time.time() * 1000) - start_ms
        return {
            "stdout": stdout_data,
            "stderr": stderr_data,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    # --- Public interface (expected by SandboxExecutor) -------------------

    async def execute_script(
        self, script: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """
        Execute a PowerShell script via ``powershell.exe``.

        Parameters
        ----------
        script : str
            The PowerShell script body.
        timeout : int, optional
            Seconds to wait before killing the process.

        Returns
        -------
        dict
            ``{"stdout": str, "stderr": str, "exit_code": int, "duration_ms": int}``
        """
        self.logger.info("Executing PowerShell script (%d chars)", len(script))
        # Write script to a temporary .ps1 file and execute it
        tmp_dir = tempfile.gettempdir()
        ps_path = os.path.join(tmp_dir, f"ps_sandbox_{uuid.uuid4().hex[:8]}.ps1")

        try:
            with open(ps_path, "w", encoding="utf-8") as fh:
                fh.write(script)

            result = await self._run_subprocess(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy", "Bypass",
                    "-File", ps_path,
                ],
                timeout=timeout,
            )
            result["success"] = result["exit_code"] == 0
            return result
        except Exception as exc:
            self.logger.error("PowerShell execution failed: %s", exc)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[ERROR] {exc}",
                "exit_code": -1,
                "duration_ms": 0,
            }
        finally:
            try:
                if os.path.exists(ps_path):
                    os.remove(ps_path)
            except OSError:
                pass

    async def execute_python_code(
        self, code: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """
        Execute arbitrary Python code string via ``python -c``.

        Parameters
        ----------
        code : str
            Python source code to execute.
        timeout : int, optional
            Seconds to wait before killing the process.

        Returns
        -------
        dict
            ``{"stdout": str, "stderr": str, "exit_code": int, "duration_ms": int}``
        """
        self.logger.info("Executing Python code via subprocess (%d chars)", len(code))

        # Escape double-quotes for safe shell embedding
        safe_code = code.replace('"', '\\"')

        result = await self._run_subprocess(
            [sys.executable, "-c", code],
            timeout=timeout,
        )
        result["success"] = result["exit_code"] == 0
        return result

    async def run_pytest(
        self, test_code: str, timeout: int = DEFAULT_TIMEOUT_SECONDS
    ) -> Dict[str, Any]:
        """
        Write *test_code* to a temporary file and run pytest against it.

        Parameters
        ----------
        test_code : str
            Python test code (functions named ``test_*``).
        timeout : int, optional
            Seconds to wait before killing the process.

        Returns
        -------
        dict
            ``{"success": bool, "stdout": str, "stderr": str, "exit_code": int, "duration_ms": int}``
        """
        self.logger.info("Running pytest on test code (%d chars)", len(test_code))
        tmp_dir = tempfile.gettempdir()
        test_path = os.path.join(tmp_dir, f"pytest_sandbox_{uuid.uuid4().hex[:8]}.py")

        try:
            with open(test_path, "w", encoding="utf-8") as fh:
                fh.write(test_code)

            # Check if pytest is available
            result = await self._run_subprocess(
                [sys.executable, "-m", "pytest", test_path, "-v", "--tb=short"],
                timeout=timeout,
            )
            result["success"] = result["exit_code"] == 0
            return result
        except Exception as exc:
            self.logger.error("Pytest execution failed: %s", exc)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[ERROR] {exc}",
                "exit_code": -1,
                "duration_ms": 0,
            }
        finally:
            try:
                if os.path.exists(test_path):
                    os.remove(test_path)
            except OSError:
                pass

    def validate_script_safety(self, script: str) -> Dict[str, Any]:
        """
        Perform basic safety validation on a PowerShell script.

        Checks for known dangerous cmdlets and patterns.

        Returns
        -------
        dict
            ``{"safe": bool, "reason": str or None}``
        """
        dangerous_patterns = [
            (r"Invoke-Expression", "Invoke-Expression is dangerous"),
            (r"IEX\b", "IEX alias for Invoke-Expression is dangerous"),
            (r"Start-Process.*-Verb\s+runAs", "Elevation via Start-Process is blocked"),
            (r"net\s+user", "net user command is blocked"),
            (r"New-LocalUser", "New-LocalUser is blocked"),
            (r"Remove-Item\s+-Recurse\s+-Force\s+C:\\", "Recursive deletion of C drive is blocked"),
            (r"Format-Volume", "Format-Volume is blocked"),
            (r"Clear-Disk", "Clear-Disk is blocked"),
            (r"Disable-WindowsOptionalFeature", "Disabling Windows features is blocked"),
            (r"Set-MpPreference.*-DisableRealtimeMonitoring", "Disabling Defender is blocked"),
            (r"reg\s+delete", "Registry deletion is blocked"),
            (r"reg\s+add.*DisableAntiSpyware", "Disabling anti-spyware is blocked"),
        ]

        for pattern, reason in dangerous_patterns:
            if re.search(pattern, script, re.IGNORECASE):
                self.logger.warning("PowerShell safety check failed: %s", reason)
                return {"safe": False, "reason": reason}

        return {"safe": True, "reason": None}


# ---------------------------------------------------------------------------
# SandboxExecutor
# ---------------------------------------------------------------------------

class SandboxExecutor:
    """
    Production-grade sandboxed code execution engine.

    Manages temporary files, enforces timeouts, validates Python syntax,
    scans for dangerous code patterns, and orchestrates execution of both
    Python and PowerShell code through the PowerShellBridge or direct
    subprocess fallbacks.

    Attributes
    ----------
    ps_bridge : PowerShellBridge
        Bridge instance for PowerShell and Python execution.
    sandbox_dir : str
        Absolute path to the directory used for temporary sandbox files.
    timeout_seconds : int
        Default execution timeout in seconds.
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        ps_bridge: Any,
        sandbox_dir: Optional[str] = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """
        Initialise the sandbox executor.

        Parameters
        ----------
        ps_bridge : Any
            Reference to a PowerShellBridge instance (or compatible duck-typed
            object).  May be ``None`` — in that case a stub
            :class:`PowerShellBridge` is created internally.
        sandbox_dir : str, optional
            Directory path for temporary sandbox files.  If *None*, a new
            temporary directory is created via :func:`tempfile.mkdtemp`.
        timeout_seconds : int, optional
            Default timeout for code execution in seconds (default 30).
        """
        if ps_bridge is None:
            logger.warning(
                "No PowerShellBridge provided; creating internal stub."
            )
            ps_bridge = PowerShellBridge()

        self.ps_bridge: Any = ps_bridge
        self.timeout_seconds: int = max(1, int(timeout_seconds))

        if sandbox_dir is None:
            self.sandbox_dir: str = tempfile.mkdtemp(prefix="sandbox_executor_")
        else:
            self.sandbox_dir = os.path.abspath(sandbox_dir)
            os.makedirs(self.sandbox_dir, exist_ok=True)

        logger.info(
            "SandboxExecutor initialised: dir=%s timeout=%ds",
            self.sandbox_dir,
            self.timeout_seconds,
        )

    # ------------------------------------------------------------------
    # 1. Syntax validation
    # ------------------------------------------------------------------

    async def validate_python_syntax(self, code: str) -> Dict[str, Any]:
        """
        Validate that *code* is syntactically correct Python.

        Parameters
        ----------
        code : str
            Python source code to validate.

        Returns
        -------
        dict
            * ``{"valid": True, "error": None, "line": None}`` on success.
            * ``{"valid": False, "error": str, "line": int}`` on SyntaxError.
            * ``{"valid": False, "error": str, "line": None}`` on other errors.
        """
        try:
            tree = ast.parse(code)
            # Count AST nodes for diagnostics
            node_count = len(list(ast.walk(tree)))
            logger.debug(
                "Syntax validation passed: %d AST nodes parsed.", node_count
            )
            return {"valid": True, "error": None, "line": None}
        except SyntaxError as exc:
            line_no = exc.lineno if exc.lineno is not None else None
            logger.warning(
                "Syntax validation failed at line %s: %s", line_no, exc
            )
            return {"valid": False, "error": str(exc), "line": line_no}
        except ValueError as exc:
            logger.warning("Syntax validation failed (ValueError): %s", exc)
            return {"valid": False, "error": str(exc), "line": None}
        except MemoryError as exc:
            logger.error("Syntax validation failed (MemoryError): %s", exc)
            return {"valid": False, "error": f"MemoryError: {exc}", "line": None}
        except Exception as exc:
            logger.error(
                "Syntax validation failed (unexpected %s): %s",
                type(exc).__name__,
                exc,
            )
            return {"valid": False, "error": str(exc), "line": None}

    # ------------------------------------------------------------------
    # 2. Core Python execution
    # ------------------------------------------------------------------

    async def execute_python_sandbox(
        self, code: str, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Execute *code* in a sandboxed environment.

        A temporary Python file is created in :attr:`sandbox_dir`, the code
        is written to it, and then executed via the PowerShellBridge (or a
        direct subprocess fallback).  The temporary file is always deleted
        in the ``finally`` block.

        Parameters
        ----------
        code : str
            Python source code to execute.
        timeout : int, optional
            Override the default timeout in seconds.

        Returns
        -------
        dict
            ``{"success": bool, "stdout": str, "stderr": str,
               "exit_code": int, "duration_ms": int}``
        """
        effective_timeout: int = timeout if timeout is not None else self.timeout_seconds
        effective_timeout = max(1, effective_timeout)

        # Generate a unique sandbox file name
        file_name = f"{SANDBOX_FILE_PREFIX}{uuid.uuid4().hex[:8]}{PYTHON_FILE_SUFFIX}"
        file_path = os.path.join(self.sandbox_dir, file_name)

        logger.info(
            "Sandbox execution starting: file=%s timeout=%ds code_len=%d",
            file_name,
            effective_timeout,
            len(code),
        )

        start_ms = int(time.time() * 1000)

        try:
            # Write code to the temporary file
            with open(file_path, "w", encoding="utf-8") as fh:
                fh.write(code)
                fh.flush()
                os.fsync(fh.fileno())

            # Determine the execution path — prefer the bridge if available
            if hasattr(self.ps_bridge, "execute_python_code"):
                bridge_result = await self.ps_bridge.execute_python_code(
                    code, timeout=effective_timeout
                )
                # Normalise the bridge result
                result = {
                    "success": bridge_result.get("success", bridge_result.get("exit_code", -1) == 0),
                    "stdout": bridge_result.get("stdout", ""),
                    "stderr": bridge_result.get("stderr", ""),
                    "exit_code": bridge_result.get("exit_code", -1),
                    "duration_ms": bridge_result.get(
                        "duration_ms", int(time.time() * 1000) - start_ms
                    ),
                }
            else:
                # Direct subprocess fallback
                logger.debug(
                    "PowerShellBridge has no execute_python_code; using subprocess fallback."
                )
                result = await self._execute_python_via_subprocess(
                    file_path, effective_timeout
                )

            logger.info(
                "Sandbox execution finished: success=%s exit_code=%s duration_ms=%s",
                result["success"],
                result["exit_code"],
                result["duration_ms"],
            )
            return result

        except OSError as exc:
            logger.error("OS error during sandbox execution: %s", exc)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[SANDBOX ERROR] OS error: {exc}",
                "exit_code": -1,
                "duration_ms": int(time.time() * 1000) - start_ms,
            }
        except Exception as exc:
            logger.error(
                "Unexpected error during sandbox execution: %s", exc, exc_info=True
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[SANDBOX ERROR] {type(exc).__name__}: {exc}",
                "exit_code": -1,
                "duration_ms": int(time.time() * 1000) - start_ms,
            }
        finally:
            # Always clean up the temporary file
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
                    logger.debug("Cleaned up sandbox file: %s", file_path)
            except OSError as cleanup_exc:
                logger.warning(
                    "Failed to clean up sandbox file %s: %s", file_path, cleanup_exc
                )

    # ------------------------------------------------------------------
    # 2b. Subprocess fallback for Python execution
    # ------------------------------------------------------------------

    async def _execute_python_via_subprocess(
        self, file_path: str, timeout: int
    ) -> Dict[str, Any]:
        """
        Run a Python file via :func:`asyncio.create_subprocess_exec`.

        Parameters
        ----------
        file_path : str
            Absolute path to the ``.py`` file to execute.
        timeout : int
            Timeout in seconds.

        Returns
        -------
        dict
            Standardised execution result.
        """
        start_ms = int(time.time() * 1000)
        stdout_data = ""
        stderr_data = ""
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                file_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.sandbox_dir,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                exit_code = proc.returncode if proc.returncode is not None else -1
            except asyncio.TimeoutError:
                logger.warning("Subprocess timed out after %d seconds; killing.", timeout)
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                # Attempt to drain remaining output
                stdout_b, stderr_b = b"", b""
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=5
                    )
                except asyncio.TimeoutError:
                    pass
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                stderr_data += f"\n[TIMEOUT] Process killed after {timeout}s."
                exit_code = -1

        except FileNotFoundError:
            stderr_data = f"[ERROR] Python interpreter not found: {sys.executable}"
            exit_code = -1
        except PermissionError as exc:
            stderr_data = f"[ERROR] Permission denied executing Python: {exc}"
            exit_code = -1
        except OSError as exc:
            stderr_data = f"[ERROR] OS error: {exc}"
            exit_code = -1

        duration_ms = int(time.time() * 1000) - start_ms
        return {
            "success": exit_code == 0,
            "stdout": stdout_data,
            "stderr": stderr_data,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    # ------------------------------------------------------------------
    # 3. Code + test workflow
    # ------------------------------------------------------------------

    async def run_code_with_test(
        self,
        code: str,
        test_code: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Validate syntax, execute code, and optionally run pytest tests.

        This is the primary high-level entry-point for running user code
        inside the sandbox.

        Parameters
        ----------
        code : str
            Main Python source code to execute.
        test_code : str, optional
            Additional test code (functions named ``test_*``) to run via
            pytest after the main code has executed.
        timeout : int, optional
            Override the default timeout in seconds.

        Returns
        -------
        dict
            Combined result dictionary::

                {
                    "syntax_valid": bool,
                    "syntax_error": str or None,
                    "execution_success": bool,
                    "stdout": str,
                    "stderr": str,
                    "exit_code": int,
                    "duration_ms": int,
                    "test_success": bool or None,
                    "test_output": str or None,
                    "overall_success": bool,
                }
        """
        effective_timeout: int = timeout if timeout is not None else self.timeout_seconds
        effective_timeout = max(1, effective_timeout)

        # --- Step 1: Syntax validation ----------------------------------
        syntax_result = await self.validate_python_syntax(code)
        syntax_valid: bool = syntax_result.get("valid", False)
        syntax_error: Optional[str] = syntax_result.get("error")
        syntax_line: Optional[int] = syntax_result.get("line")

        if not syntax_valid:
            logger.warning(
                "run_code_with_test aborted: syntax error at line %s: %s",
                syntax_line,
                syntax_error,
            )
            return {
                "syntax_valid": False,
                "syntax_error": syntax_error,
                "syntax_line": syntax_line,
                "execution_success": False,
                "stdout": "",
                "stderr": f"Syntax error: {syntax_error}",
                "exit_code": -1,
                "duration_ms": 0,
                "test_success": None,
                "test_output": None,
                "overall_success": False,
            }

        logger.debug("Syntax validation passed; proceeding to execution.")

        # --- Step 2: Execute main code ----------------------------------
        exec_result = await self.execute_python_sandbox(code, timeout=effective_timeout)
        execution_success: bool = exec_result.get("success", False)
        stdout: str = exec_result.get("stdout", "")
        stderr: str = exec_result.get("stderr", "")
        exit_code: int = exec_result.get("exit_code", -1)
        duration_ms: int = exec_result.get("duration_ms", 0)

        test_success: Optional[bool] = None
        test_output: Optional[str] = None

        # --- Step 3: Optional pytest ------------------------------------
        if test_code is not None and test_code.strip():
            logger.info("Running pytest on provided test code (%d chars).", len(test_code))

            # Validate test code syntax first
            test_syntax = await self.validate_python_syntax(test_code)
            if not test_syntax.get("valid", False):
                test_success = False
                test_output = f"Test code syntax error: {test_syntax.get('error')}"
                stderr += f"\n{test_output}"
            else:
                # Write test code to a temporary file
                test_file_name = f"{TEST_FILE_PREFIX}{uuid.uuid4().hex[:8]}{PYTHON_FILE_SUFFIX}"
                test_file_path = os.path.join(self.sandbox_dir, test_file_name)

                try:
                    with open(test_file_path, "w", encoding="utf-8") as fh:
                        fh.write(test_code)
                        fh.flush()
                        os.fsync(fh.fileno())

                    if hasattr(self.ps_bridge, "run_pytest"):
                        test_result = await self.ps_bridge.run_pytest(
                            test_code, timeout=effective_timeout
                        )
                    else:
                        test_result = await self._run_pytest_via_subprocess(
                            test_file_path, effective_timeout
                        )

                    test_success = test_result.get("success", False)
                    test_output = test_result.get("stdout", "") + test_result.get("stderr", "")
                    duration_ms += test_result.get("duration_ms", 0)

                    # Append test output to main stderr for unified view
                    if test_output.strip():
                        stderr += f"\n--- TEST OUTPUT ---\n{test_output}"

                except OSError as exc:
                    test_success = False
                    test_output = f"[TEST ERROR] OS error: {exc}"
                    stderr += f"\n{test_output}"
                except Exception as exc:
                    test_success = False
                    test_output = f"[TEST ERROR] {type(exc).__name__}: {exc}"
                    stderr += f"\n{test_output}"
                finally:
                    try:
                        if os.path.exists(test_file_path):
                            os.remove(test_file_path)
                            logger.debug("Cleaned up test file: %s", test_file_path)
                    except OSError as cleanup_exc:
                        logger.warning(
                            "Failed to clean up test file %s: %s",
                            test_file_path,
                            cleanup_exc,
                        )

        # --- Step 4: Compute overall success ----------------------------
        overall_success = syntax_valid and execution_success
        if test_code is not None and test_code.strip():
            overall_success = overall_success and (test_success is True)

        result = {
            "syntax_valid": syntax_valid,
            "syntax_error": syntax_error,
            "syntax_line": syntax_line,
            "execution_success": execution_success,
            "stdout": stdout,
            "stderr": stderr,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "test_success": test_success,
            "test_output": test_output,
            "overall_success": overall_success,
        }

        logger.info(
            "run_code_with_test completed: overall_success=%s syntax=%s exec=%s test=%s",
            overall_success,
            syntax_valid,
            execution_success,
            test_success,
        )
        return result

    # ------------------------------------------------------------------
    # 3b. Subprocess fallback for pytest
    # ------------------------------------------------------------------

    async def _run_pytest_via_subprocess(
        self, test_file_path: str, timeout: int
    ) -> Dict[str, Any]:
        """
        Run pytest on a test file via subprocess.

        Parameters
        ----------
        test_file_path : str
            Absolute path to the test ``.py`` file.
        timeout : int
            Timeout in seconds.

        Returns
        -------
        dict
            Standardised test result.
        """
        start_ms = int(time.time() * 1000)
        stdout_data = ""
        stderr_data = ""
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "pytest",
                test_file_path,
                "-v",
                "--tb=short",
                "--no-header",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.sandbox_dir,
            )

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                exit_code = proc.returncode if proc.returncode is not None else -1
            except asyncio.TimeoutError:
                logger.warning("Pytest timed out after %d seconds; killing.", timeout)
                proc.kill()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    pass
                stdout_b, stderr_b = b"", b""
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(), timeout=5
                    )
                except asyncio.TimeoutError:
                    pass
                stdout_data = stdout_b.decode("utf-8", errors="replace") if stdout_b else ""
                stderr_data = stderr_b.decode("utf-8", errors="replace") if stderr_b else ""
                stderr_data += f"\n[TIMEOUT] Pytest killed after {timeout}s."
                exit_code = -1

        except FileNotFoundError:
            stderr_data = "[ERROR] pytest not found. Install with: pip install pytest"
            exit_code = -1
        except PermissionError as exc:
            stderr_data = f"[ERROR] Permission denied running pytest: {exc}"
            exit_code = -1
        except OSError as exc:
            stderr_data = f"[ERROR] OS error running pytest: {exc}"
            exit_code = -1

        duration_ms = int(time.time() * 1000) - start_ms
        return {
            "success": exit_code == 0,
            "stdout": stdout_data,
            "stderr": stderr_data,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
        }

    # ------------------------------------------------------------------
    # 4. PowerShell sandbox execution
    # ------------------------------------------------------------------

    async def execute_powershell_sandbox(
        self, script: str, timeout: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Execute a PowerShell script through the sandbox.

        The script is first validated for safety patterns via
        ``ps_bridge.validate_script_safety()``.  If validation fails the
        script is rejected without execution.

        Parameters
        ----------
        script : str
            PowerShell script body.
        timeout : int, optional
            Override the default timeout in seconds.

        Returns
        -------
        dict
            ``{"success": bool, "stdout": str, "stderr": str,
               "exit_code": int, "duration_ms": int}``
        """
        effective_timeout: int = timeout if timeout is not None else self.timeout_seconds
        effective_timeout = max(1, effective_timeout)

        logger.info(
            "PowerShell sandbox execution starting: script_len=%d timeout=%ds",
            len(script),
            effective_timeout,
        )

        # --- Step 1: Safety validation ----------------------------------
        if hasattr(self.ps_bridge, "validate_script_safety"):
            safety_check = self.ps_bridge.validate_script_safety(script)
            is_safe = safety_check.get("safe", True)
            reason = safety_check.get("reason")
            if not is_safe:
                logger.warning(
                    "PowerShell script rejected by safety validation: %s", reason
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"[SAFETY BLOCKED] {reason}",
                    "exit_code": -1,
                    "duration_ms": 0,
                }
        else:
            logger.debug(
                "PowerShellBridge has no validate_script_safety; skipping safety check."
            )

        # --- Step 2: Execute --------------------------------------------
        try:
            if hasattr(self.ps_bridge, "execute_script"):
                result = await self.ps_bridge.execute_script(
                    script, timeout=effective_timeout
                )
                # Normalise
                return {
                    "success": result.get(
                        "success", result.get("exit_code", -1) == 0
                    ),
                    "stdout": result.get("stdout", ""),
                    "stderr": result.get("stderr", ""),
                    "exit_code": result.get("exit_code", -1),
                    "duration_ms": result.get("duration_ms", 0),
                }
            else:
                logger.error(
                    "PowerShellBridge has no execute_script method and no fallback available."
                )
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": "[ERROR] PowerShellBridge does not support script execution.",
                    "exit_code": -1,
                    "duration_ms": 0,
                }

        except Exception as exc:
            logger.error(
                "PowerShell execution failed: %s", exc, exc_info=True
            )
            return {
                "success": False,
                "stdout": "",
                "stderr": f"[ERROR] {type(exc).__name__}: {exc}",
                "exit_code": -1,
                "duration_ms": 0,
            }

    # ------------------------------------------------------------------
    # 5. Cleanup
    # ------------------------------------------------------------------

    async def cleanup(self) -> Dict[str, int]:
        """
        Remove all sandbox and test files from :attr:`sandbox_dir`.

        Deletes files matching ``sandbox_*.py`` and ``test_*.py``.
        Non-matching files and the directory itself are left intact.

        Returns
        -------
        dict
            ``{"sandbox_files_removed": int, "test_files_removed": int,
               "errors": int}``
        """
        sandbox_removed = 0
        test_removed = 0
        errors = 0

        if not os.path.isdir(self.sandbox_dir):
            logger.warning("Sandbox directory does not exist: %s", self.sandbox_dir)
            return {
                "sandbox_files_removed": 0,
                "test_files_removed": 0,
                "errors": 0,
            }

        try:
            entries = os.listdir(self.sandbox_dir)
        except OSError as exc:
            logger.error("Cannot list sandbox directory: %s", exc)
            return {
                "sandbox_files_removed": 0,
                "test_files_removed": 0,
                "errors": 1,
            }

        for entry in entries:
            full_path = os.path.join(self.sandbox_dir, entry)

            # Only delete regular files, not subdirectories
            if not os.path.isfile(full_path):
                continue

            is_sandbox = entry.startswith(SANDBOX_FILE_PREFIX) and entry.endswith(
                PYTHON_FILE_SUFFIX
            )
            is_test = entry.startswith(TEST_FILE_PREFIX) and entry.endswith(
                PYTHON_FILE_SUFFIX
            )

            if is_sandbox or is_test:
                try:
                    os.remove(full_path)
                    if is_sandbox:
                        sandbox_removed += 1
                    if is_test:
                        test_removed += 1
                    logger.debug("Removed: %s", full_path)
                except OSError as exc:
                    errors += 1
                    logger.warning("Failed to remove %s: %s", full_path, exc)

        logger.info(
            "Cleanup complete: sandbox=%d test=%d errors=%d",
            sandbox_removed,
            test_removed,
            errors,
        )

        return {
            "sandbox_files_removed": sandbox_removed,
            "test_files_removed": test_removed,
            "errors": errors,
        }

    # ------------------------------------------------------------------
    # 6. Safety pattern scanning
    # ------------------------------------------------------------------

    async def check_safety_patterns(self, code: str) -> Dict[str, Any]:
        """
        Scan Python code for dangerous patterns.

        Checks against a blacklist of patterns known to enable code
        injection, remote access, or system modification.  Also records
        warnings for patterns that are allowed but logged (e.g. threading).

        Parameters
        ----------
        code : str
            Python source code to scan.

        Returns
        -------
        dict
            ``{"safe": bool, "warnings": list[str], "blocked": list[str]}``

            * ``safe`` is ``True`` only if no blocked patterns are found.
            * ``warnings`` contains human-readable descriptions of
              non-blocking but noteworthy patterns.
            * ``blocked`` contains human-readable descriptions of blocked
              patterns that were detected.
        """
        warnings_found: List[str] = []
        blocked_found: List[str] = []

        # Check blocked patterns
        for pattern, description in BLOCKED_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                blocked_found.append(description)
                logger.warning("Safety scan BLOCKED pattern: %s", description)

        # Check warning patterns
        for pattern, description in WARNED_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE):
                warnings_found.append(description)
                logger.info("Safety scan WARNING pattern: %s", description)

        is_safe = len(blocked_found) == 0

        logger.info(
            "Safety scan result: safe=%s blocked=%d warnings=%d",
            is_safe,
            len(blocked_found),
            len(warnings_found),
        )

        return {
            "safe": is_safe,
            "warnings": warnings_found,
            "blocked": blocked_found,
        }

    # ------------------------------------------------------------------
    # 7. Context-manager style helper
    # ------------------------------------------------------------------

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit — always runs cleanup."""
        await self.cleanup()
        return False


# ---------------------------------------------------------------------------
# Standalone convenience helpers
# ---------------------------------------------------------------------------

async def quick_sandbox_run(
    code: str,
    test_code: Optional[str] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """
    One-shot convenience function: create a temporary sandbox, run code,
    and clean up.

    Parameters
    ----------
    code : str
        Python source code to execute.
    test_code : str, optional
        Optional pytest test code.
    timeout : int, optional
        Timeout in seconds (default 30).

    Returns
    -------
    dict
        Combined result from :meth:`SandboxExecutor.run_code_with_test`.
    """
    executor = SandboxExecutor(
        ps_bridge=PowerShellBridge(),
        timeout_seconds=timeout,
    )
    try:
        return await executor.run_code_with_test(code, test_code=test_code, timeout=timeout)
    finally:
        await executor.cleanup()
        # Remove the temporary directory itself
        try:
            shutil.rmtree(executor.sandbox_dir, ignore_errors=True)
        except Exception:
            pass


def scan_safety_sync(code: str) -> Dict[str, Any]:
    """
    Synchronous wrapper around :meth:`SandboxExecutor.check_safety_patterns`.

    Useful for quick safety checks without creating an executor instance.

    Parameters
    ----------
    code : str
        Python source code to scan.

    Returns
    -------
    dict
        Safety scan result.
    """
    warnings_found: List[str] = []
    blocked_found: List[str] = []

    for pattern, description in BLOCKED_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            blocked_found.append(description)

    for pattern, description in WARNED_PATTERNS:
        if re.search(pattern, code, re.IGNORECASE):
            warnings_found.append(description)

    return {
        "safe": len(blocked_found) == 0,
        "warnings": warnings_found,
        "blocked": blocked_found,
    }


# ---------------------------------------------------------------------------
# Module-level smoke test (not a unit test — just validates imports)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    logger.info("sandbox_executor.py loaded successfully.")
    logger.info("Classes available: SandboxExecutor, PowerShellBridge")
    logger.info("Helpers available: quick_sandbox_run, scan_safety_sync")
