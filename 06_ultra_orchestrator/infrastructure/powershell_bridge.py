"""
powershell_bridge.py

Production-grade PowerShell execution bridge for Windows 11.
Manages all PowerShell execution with security constraints and environment validation.
"""

from __future__ import annotations

import asyncio
import os
import platform
import re
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path


class PowerShellBridge:
    """
    Manages all PowerShell execution with security constraints and environment validation.

    Attributes:
        powershell_path: Path to the PowerShell executable (pwsh.exe or powershell.exe).
        working_dir: Base working directory for temp files and script execution.
        is_available: Whether PowerShell 7+ is available on the system.
    """

    def __init__(self, working_dir: str | None = None) -> None:
        """
        Initialize the PowerShell bridge.

        Args:
            working_dir: Base working directory. If None, uses tempfile.gettempdir().
        """
        self.working_dir: str = working_dir or tempfile.gettempdir()
        self.powershell_path: str = "powershell.exe"
        self.is_available: bool = False

        # Ensure working directory exists
        os.makedirs(self.working_dir, exist_ok=True)

        # Attempt to locate PowerShell 7+ (pwsh.exe) or fallback to Windows PowerShell
        self._detect_powershell()

    def _detect_powershell(self) -> None:
        """
        Detect the PowerShell executable path.
        Prefers PowerShell 7+ (pwsh.exe), falls back to Windows PowerShell (powershell.exe).
        """
        # Check for PowerShell 7+ first
        pwsh_path = shutil.which("pwsh.exe")
        if pwsh_path:
            self.powershell_path = pwsh_path
            return

        # Fall back to Windows PowerShell
        ps_path = shutil.which("powershell.exe")
        if ps_path:
            self.powershell_path = ps_path
            return

        # Last resort: common install locations
        common_paths = [
            r"C:\Program Files\PowerShell\7\pwsh.exe",
            r"C:\Program Files\PowerShell\6\pwsh.exe",
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        ]
        for path in common_paths:
            if os.path.isfile(path):
                self.powershell_path = path
                return

        # Default to powershell.exe and let validation handle missing case
        self.powershell_path = "powershell.exe"

    async def validate_environment(self) -> dict:
        """
        Run comprehensive environment checks.

        Checks:
            - PowerShell 7+ availability and version
            - Working directory writability
            - Outbound connectivity to Kimi API (api.moonshot.ai)

        Returns:
            Dict with keys: powershell_ok, version, writable, api_connectivity, all_ok
        """
        result = {
            "powershell_ok": False,
            "version": "unknown",
            "writable": False,
            "api_connectivity": False,
            "all_ok": False,
        }

        # Check 1: PowerShell 7+ availability
        try:
            cmd = (
                f'"{self.powershell_path}" -NoProfile -NonInteractive '
                f"-ExecutionPolicy Bypass -Command \""
                f"$PSVersionTable.PSVersion\""
            )
            proc = await asyncio.create_subprocess_exec(
                self.powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", "$PSVersionTable.PSVersion",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_data, stderr_data = await asyncio.wait_for(
                proc.communicate(), timeout=15
            )
            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            if proc.returncode == 0 and stdout_text:
                # Parse version output like "Major  Minor  Build  Revision\n7      4      6      -1"
                # or "7.4.6"
                version_match = re.search(r'(\d+)(?:\s*\.\s*(\d+))?', stdout_text)
                if version_match:
                    major = int(version_match.group(1))
                    minor = int(version_match.group(2)) if version_match.group(2) else 0
                    result["version"] = f"{major}.{minor}"
                    if major >= 7:
                        result["powershell_ok"] = True
                        self.is_available = True
                    else:
                        # Accept any available PowerShell but mark version
                        result["version"] = f"{major}.{minor}"
                        self.is_available = True
                else:
                    # Couldn't parse version, but PowerShell responded
                    result["version"] = "unknown (responded)"
                    self.is_available = True
            elif stderr_text:
                result["version"] = f"error: {stderr_text[:200]}"
            else:
                result["version"] = "not available"
        except asyncio.TimeoutError:
            result["version"] = "timeout"
        except FileNotFoundError:
            result["version"] = "not found"
        except Exception as e:
            result["version"] = f"error: {str(e)[:200]}"

        # Check 2: Working directory writable
        try:
            test_file = os.path.join(self.working_dir, f".pwr_test_{os.getpid()}.tmp")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            result["writable"] = True
        except Exception:
            result["writable"] = False

        # Check 3: API connectivity (DNS resolve of api.moonshot.ai)
        try:
            loop = asyncio.get_event_loop()
            await asyncio.wait_for(
                loop.getaddrinfo("api.moonshot.ai", None),
                timeout=10,
            )
            result["api_connectivity"] = True
        except asyncio.TimeoutError:
            result["api_connectivity"] = False
        except Exception:
            result["api_connectivity"] = False

        result["all_ok"] = (
            result["powershell_ok"] and result["writable"]
        )
        return result

    async def execute_command(
        self,
        command: str,
        timeout: int = 30,
        cwd: str | None = None,
    ) -> dict:
        """
        Execute a PowerShell command string securely.

        Builds a command with strict mode, error handling, and security flags.

        Args:
            command: The PowerShell command string to execute.
            timeout: Maximum execution time in seconds. Default 30.
            cwd: Working directory for the subprocess. Defaults to self.working_dir.

        Returns:
            Dict with keys: success, stdout, stderr, exit_code, duration_ms
        """
        start_time = time.monotonic()
        working_directory = cwd or self.working_dir

        # Build the secure command wrapper
        wrapped_command = (
            f"& {{ Set-StrictMode -Version Latest; "
            f"$ErrorActionPreference='Stop'; {command} }}"
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                self.powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", wrapped_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_directory,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                # Kill the process on timeout
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                duration_ms = int((time.monotonic() - start_time) * 1000)
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Execution timed out after {timeout} seconds",
                    "exit_code": -1,
                    "duration_ms": duration_ms,
                }

            duration_ms = int((time.monotonic() - start_time) * 1000)
            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            success = proc.returncode == 0 and not stderr_text
            return {
                "success": success,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "exit_code": proc.returncode or 0,
                "duration_ms": duration_ms,
            }

        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"PowerShell executable not found: {self.powershell_path}",
                "exit_code": -1,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Execution error: {str(e)}",
                "exit_code": -1,
                "duration_ms": duration_ms,
            }

    async def execute_script(
        self,
        script_content: str,
        timeout: int = 30,
    ) -> dict:
        """
        Write script to a temp .ps1 file, execute it, then delete the file.

        Args:
            script_content: The PowerShell script content to execute.
            timeout: Maximum execution time in seconds. Default 30.

        Returns:
            Dict with keys: success, stdout, stderr, exit_code, duration_ms
        """
        start_time = time.monotonic()
        script_path: str | None = None

        try:
            # Create temp file with .ps1 extension
            fd, script_path = tempfile.mkstemp(
                suffix=".ps1",
                prefix="psbridge_",
                dir=self.working_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(script_content)

            # Execute the script file
            proc = await asyncio.create_subprocess_exec(
                self.powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-File", script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                duration_ms = int((time.monotonic() - start_time) * 1000)
                result = {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Script execution timed out after {timeout} seconds",
                    "exit_code": -1,
                    "duration_ms": duration_ms,
                }
                # Cleanup
                if script_path and os.path.exists(script_path):
                    try:
                        os.remove(script_path)
                    except Exception:
                        pass
                return result

            duration_ms = int((time.monotonic() - start_time) * 1000)
            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            success = proc.returncode == 0 and not stderr_text
            result = {
                "success": success,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "exit_code": proc.returncode or 0,
                "duration_ms": duration_ms,
            }

        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = {
                "success": False,
                "stdout": "",
                "stderr": f"PowerShell executable not found: {self.powershell_path}",
                "exit_code": -1,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = {
                "success": False,
                "stdout": "",
                "stderr": f"Script execution error: {str(e)}",
                "exit_code": -1,
                "duration_ms": duration_ms,
            }
        finally:
            # Always clean up temp file
            if script_path and os.path.exists(script_path):
                try:
                    os.remove(script_path)
                except Exception:
                    pass

        return result

    async def execute_python_code(self, code: str, timeout: int = 30) -> dict:
        """
        Execute Python code via PowerShell for syntax validation.

        Escapes the code for safe PowerShell string embedding, then runs
        'python -c "<escaped_code>"' through the PowerShell bridge.

        Args:
            code: Python code string to execute.
            timeout: Maximum execution time in seconds. Default 30.

        Returns:
            Dict with keys: success, stdout, stderr, exit_code, duration_ms
        """
        escaped_code = self._escape_for_powershell(code)
        ps_command = f'python -c "{escaped_code}"'
        return await self.execute_command(ps_command, timeout=timeout)

    def _escape_for_powershell(self, code: str) -> str:
        """
        Escape Python code for safe embedding in a PowerShell -Command string.

        Escapes backslashes, double quotes, and dollar signs to prevent
        PowerShell from interpreting them.

        Args:
            code: Raw Python code string.

        Returns:
            Escaped string safe for PowerShell embedding.
        """
        # Replace backslashes first (order matters)
        escaped = code.replace("\\", "\\\\")
        # Escape double quotes
        escaped = escaped.replace('"', '\\"')
        # Escape dollar signs (PowerShell variable interpolation)
        # In PowerShell, `$ is the escaped form of literal $
        escaped = escaped.replace("$", "`$")
        # Escape backticks (PowerShell escape char)
        escaped = escaped.replace("`", "``")
        return escaped

    async def run_pytest(self, test_code: str, timeout: int = 30) -> dict:
        """
        Run pytest on test code.

        Writes test_code to a temporary test_*.py file, runs pytest,
        parses the output, and cleans up.

        Args:
            test_code: Python test code to run with pytest.
            timeout: Maximum execution time in seconds. Default 30.

        Returns:
            Dict with keys: success, stdout, stderr, exit_code, duration_ms, test_results
            where test_results is a dict with: passed, failed, errors, summary
        """
        start_time = time.monotonic()
        test_file_path: str | None = None

        try:
            # Create temp test file
            fd, test_file_path = tempfile.mkstemp(
                suffix=".py",
                prefix="test_",
                dir=self.working_dir,
            )
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(test_code)

            # Build pytest command
            pytest_cmd = (
                f"python -m pytest \"{test_file_path}\" --tb=short -v"
            )

            proc = await asyncio.create_subprocess_exec(
                self.powershell_path,
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy", "Bypass",
                "-Command", pytest_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.working_dir,
            )

            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                duration_ms = int((time.monotonic() - start_time) * 1000)
                # Cleanup
                if test_file_path and os.path.exists(test_file_path):
                    try:
                        os.remove(test_file_path)
                    except Exception:
                        pass
                return {
                    "success": False,
                    "stdout": "",
                    "stderr": f"Pytest timed out after {timeout} seconds",
                    "exit_code": -1,
                    "duration_ms": duration_ms,
                    "test_results": {
                        "passed": 0,
                        "failed": 0,
                        "errors": 0,
                        "summary": "Timeout",
                    },
                }

            duration_ms = int((time.monotonic() - start_time) * 1000)
            stdout_text = stdout_data.decode("utf-8", errors="replace").strip()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()

            # Parse test results from stdout
            test_results = self._parse_pytest_output(stdout_text)

            # Pytest exit codes: 0 = all passed, 1 = some failed, 2 = test execution interrupted
            success = proc.returncode == 0

            result = {
                "success": success,
                "stdout": stdout_text,
                "stderr": stderr_text,
                "exit_code": proc.returncode or 0,
                "duration_ms": duration_ms,
                "test_results": test_results,
            }

        except FileNotFoundError:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = {
                "success": False,
                "stdout": "",
                "stderr": f"PowerShell executable not found: {self.powershell_path}",
                "exit_code": -1,
                "duration_ms": duration_ms,
                "test_results": {
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "summary": "PowerShell not found",
                },
            }
        except Exception as e:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            result = {
                "success": False,
                "stdout": "",
                "stderr": f"Pytest error: {str(e)}",
                "exit_code": -1,
                "duration_ms": duration_ms,
                "test_results": {
                    "passed": 0,
                    "failed": 0,
                    "errors": 0,
                    "summary": f"Error: {str(e)[:200]}",
                },
            }
        finally:
            # Always clean up temp test file
            if test_file_path and os.path.exists(test_file_path):
                try:
                    os.remove(test_file_path)
                except Exception:
                    pass

        return result

    def _parse_pytest_output(self, output: str) -> dict:
        """
        Parse pytest output to extract test counts.

        Args:
            output: Raw stdout from pytest.

        Returns:
            Dict with keys: passed, failed, errors, summary
        """
        result = {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "summary": "",
        }
        if not output:
            return result

        # Look for summary line like "1 passed in 0.01s" or "1 failed, 1 passed in 0.01s"
        # Also handle "= 1 passed in 0.01s =" format
        summary_patterns = [
            r'(\d+)\s+passed',
            r'(\d+)\s+failed',
            r'(\d+)\s+error',
        ]

        passed_match = re.search(r'(\d+)\s+passed', output, re.IGNORECASE)
        if passed_match:
            result["passed"] = int(passed_match.group(1))

        failed_match = re.search(r'(\d+)\s+failed', output, re.IGNORECASE)
        if failed_match:
            result["failed"] = int(failed_match.group(1))

        error_match = re.search(r'(\d+)\s+error', output, re.IGNORECASE)
        if error_match:
            result["errors"] = int(error_match.group(1))

        # Extract summary line (typically the last line with timing)
        lines = output.strip().split("\n")
        for line in reversed(lines):
            line_stripped = line.strip()
            if "passed" in line_stripped or "failed" in line_stripped or "error" in line_stripped:
                # Clean up the summary line (remove box drawing characters)
                clean = re.sub(r'[=\-\*]+', '', line_stripped).strip()
                if clean:
                    result["summary"] = clean
                    break

        if not result["summary"]:
            result["summary"] = output.strip().split("\n")[-1][:200] if output else "No output"

        return result

    async def check_execution_policy(self) -> str:
        """
        Return the current PowerShell execution policy as a string.

        Returns:
            The execution policy name (e.g., 'Restricted', 'RemoteSigned', 'Unrestricted')
            or an error message if the check fails.
        """
        result = await self.execute_command(
            "Get-ExecutionPolicy -Scope Process"
        )
        if result["success"]:
            return result["stdout"].strip() or "Unknown"
        return f"Error checking execution policy: {result['stderr']}"

    async def get_system_info(self) -> dict:
        """
        Gather system information.

        Returns:
            Dict with keys: powershell_version, python_version, os_name,
            cpu_count, total_memory_gb
        """
        info = {
            "powershell_version": "unknown",
            "python_version": sys.version.split()[0],
            "os_name": platform.platform(),
            "cpu_count": os.cpu_count() or 0,
            "total_memory_gb": 0.0,
        }

        # Get PowerShell version
        ps_version_result = await self.execute_command(
            "$PSVersionTable.PSVersion.ToString()"
        )
        if ps_version_result["success"]:
            info["powershell_version"] = ps_version_result["stdout"].strip()

        # Get total memory via PowerShell
        mem_result = await self.execute_command(
            "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 2)"
        )
        if mem_result["success"]:
            try:
                info["total_memory_gb"] = float(mem_result["stdout"].strip())
            except ValueError:
                info["total_memory_gb"] = 0.0
        else:
            # Fallback to psutil if available
            try:
                import psutil
                mem = psutil.virtual_memory()
                info["total_memory_gb"] = round(mem.total / (1024 ** 3), 2)
            except ImportError:
                info["total_memory_gb"] = 0.0

        return info

    async def validate_script_safety(self, script_content: str) -> dict:
        """
        Check a PowerShell script for dangerous patterns.

        Blocked patterns:
            - Invoke-WebRequest, Invoke-RestMethod (network calls)
            - Start-BitsTransfer (file downloads)
            - New-Object System.Net.WebClient (web client)
            - DownloadString (remote code execution)
            - Registry write patterns (HKLM)
            - Administrative elevation (runAs)

        Args:
            script_content: The PowerShell script content to validate.

        Returns:
            Dict with keys: safe, blocked_patterns, reason
        """
        blocked_patterns = [
            (r"Invoke-WebRequest", "Network request: Invoke-WebRequest"),
            (r"Invoke-RestMethod", "Network request: Invoke-RestMethod"),
            (r"Start-BitsTransfer", "File transfer: Start-BitsTransfer"),
            (r"New-Object\s+System\.Net\.WebClient", "Web client: New-Object System.Net.WebClient"),
            (r"DownloadString", "Remote code execution: DownloadString"),
            (r"Set-ItemProperty.*HKLM", "Registry write to HKLM"),
            (r"New-ItemProperty.*HKLM", "Registry write to HKLM"),
            (r"Start-Process.*-Verb\s+runAs", "Administrative elevation: runAs"),
        ]

        found_patterns: list[str] = []
        script_upper = script_content.upper()

        for pattern, reason in blocked_patterns:
            if re.search(pattern, script_content, re.IGNORECASE):
                found_patterns.append(reason)

        # Additional check for encoded commands (common evasion technique)
        if "-EncodedCommand" in script_content or "-enc " in script_content:
            found_patterns.append("Encoded command detected")

        # Check for base64 encoded content that looks suspicious
        if re.search(r"FromBase64String", script_content, re.IGNORECASE):
            found_patterns.append("Base64 decoding: FromBase64String")

        is_safe = len(found_patterns) == 0
        return {
            "safe": is_safe,
            "blocked_patterns": found_patterns,
            "reason": (
                "Script is safe"
                if is_safe
                else f"Blocked {len(found_patterns)} dangerous pattern(s): "
                     f"{', '.join(found_patterns)}"
            ),
        }
