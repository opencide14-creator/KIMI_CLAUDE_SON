"""Nmap Scanner — real nmap + NSE integration via python-nmap.

Falls back to asyncio TCP scanner if nmap not available.
All scans run in QThread — never blocks the UI.
"""
from __future__ import annotations
import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from PyQt6.QtCore import QThread, pyqtSignal

from src.models.gateway import DiscoveredService
from src.constants import Protocol, HostStatus

log = logging.getLogger(__name__)


@dataclass
class NmapResult:
    """One host from an nmap scan."""
    host:        str
    hostname:    str = ""
    state:       str = "up"
    ports:       List[Dict] = field(default_factory=list)
    os_guess:    str = ""
    os_accuracy: int = 0
    scripts:     Dict[str, str] = field(default_factory=dict)  # script_name → output
    raw:         Dict = field(default_factory=dict)

    def to_discovered_service(self) -> List[DiscoveredService]:
        """Convert each open port to a DiscoveredService."""
        services = []
        for p in self.ports:
            if p.get("state") != "open":
                continue
            port_num = int(p["port"])
            proto_str = p.get("protocol", "tcp").upper()
            proto = Protocol.HTTPS if port_num in (443, 8443, 9443) else Protocol.HTTP

            svc = DiscoveredService(
                host      = self.host,
                port      = port_num,
                protocol  = proto,
                status    = HostStatus.OPEN,
                service   = p.get("name", ""),
                version   = f"{p.get('product','')} {p.get('version','')} {p.get('extrainfo','')}".strip(),
                discovered_at = datetime.now(),
                last_seen = datetime.now(),
            )
            # Apply OS info
            if self.os_guess:
                svc.notes = f"OS: {self.os_guess} ({self.os_accuracy}%)"

            # Flag well-known services
            service_lower = svc.service.lower()
            version_lower = svc.version.lower()
            if any(k in service_lower or k in version_lower
                   for k in ("http", "https", "nginx", "apache", "lighttpd")):
                # Check script output for FastAPI / AI hints
                for script_out in self.scripts.values():
                    out_lower = script_out.lower()
                    if "fastapi" in out_lower or "openapi" in out_lower or "swagger" in out_lower:
                        svc.is_fastapi = True
                    if "ollama" in out_lower:
                        svc.is_ai_api = True
                        svc.service = "Ollama"
                    if "mcp" in out_lower or "jsonrpc" in out_lower:
                        svc.is_mcp = True

            services.append(svc)
        return services


class NmapScanWorker(QThread):
    """Run nmap in background, emit results as they come."""

    result_ready    = pyqtSignal(object)    # NmapResult
    service_found   = pyqtSignal(object)    # DiscoveredService
    progress        = pyqtSignal(int)       # 0-100
    log_line        = pyqtSignal(str)       # status message
    scan_finished   = pyqtSignal(list)      # List[DiscoveredService]
    error_occurred  = pyqtSignal(str)

    def __init__(self,
                 target: str,
                 ports:  str,              # e.g. "1-1024", "80,443,8080", "-"
                 nmap_args: str = "-sV -T4",
                 nmap_binary: str = "nmap"):
        super().__init__()
        self._target = target
        self._ports  = ports
        self._args   = nmap_args
        self._binary = nmap_binary
        self._stop   = False

    def stop(self):
        self._stop = True
        self.terminate()

    def run(self):
        from src.gui.widgets.progress import TaskTracker, TaskState
        tracker = TaskTracker.get()
        task = tracker.start(
            name=f"nmap {self._target}",
            detail=f"Scanning {self._ports} with {self._args}",
            indeterminate=False,
        )

        try:
            import nmap as python_nmap
        except ImportError:
            self.error_occurred.emit("python-nmap not installed — run: pip install python-nmap")
            tracker.fail(task.id, "python-nmap not installed")
            return

        self.log_line.emit(f"nmap {self._args} -p {self._ports} {self._target}")
        self.progress.emit(5)
        tracker.update(task.id, progress=5, detail=f"Connecting to nmap…")

        nm = python_nmap.PortScanner()
        try:
            scan_args = f"{self._args} --host-timeout 60s"
            tracker.update(task.id, progress=10, detail=f"Scanning {self._target}:{self._ports}…")
            result = nm.scan(
                hosts    = self._target,
                ports    = self._ports,
                arguments= scan_args,
                sudo     = self._needs_root(),
            )
        except python_nmap.PortScannerError as e:
            self.error_occurred.emit(f"nmap error: {e}")
            tracker.fail(task.id, str(e))
            return
        except Exception as e:
            self.error_occurred.emit(str(e))
            tracker.fail(task.id, str(e))
            return

        self.progress.emit(80)
        tracker.update(task.id, progress=80, detail="Parsing results…")

        all_services = []
        hosts = nm.all_hosts()
        self.log_line.emit(f"Scan complete — {len(hosts)} host(s)")

        for i, host in enumerate(hosts):
            if self._stop:
                break
            host_data = nm[host]
            ports_list = []

            for proto in host_data.all_protocols():
                for port_num in sorted(host_data[proto].keys()):
                    port_info = host_data[proto][port_num]
                    ports_list.append({
                        "port":      str(port_num),
                        "protocol":  proto,
                        "state":     port_info.get("state", ""),
                        "name":      port_info.get("name", ""),
                        "product":   port_info.get("product", ""),
                        "version":   port_info.get("version", ""),
                        "extrainfo": port_info.get("extrainfo", ""),
                        "scripts":   port_info.get("script", {}),
                    })

            # OS detection
            os_guess = ""
            os_acc   = 0
            try:
                osmatch = host_data.get("osmatch", [])
                if osmatch:
                    os_guess = osmatch[0].get("name", "")
                    os_acc   = int(osmatch[0].get("accuracy", 0))
            except Exception as e:
                log.debug("OS detection parse error: %s", e)

            # Scripts at host level
            host_scripts = {}
            try:
                if hasattr(host_data, "keys"):
                    for k in host_data.keys():
                        if k not in ("tcp", "udp", "status", "osmatch", "osclass", "hostnames"):
                            host_scripts[k] = str(host_data[k])
            except Exception as e:
                log.debug("Host scripts parse error: %s", e)

            nmap_result = NmapResult(
                host        = host,
                hostname    = host_data.hostname() if hasattr(host_data, "hostname") else "",
                state       = host_data.state() if hasattr(host_data, "state") else "up",
                ports       = ports_list,
                os_guess    = os_guess,
                os_accuracy = os_acc,
                scripts     = host_scripts,
                raw         = dict(host_data),
            )
            self.result_ready.emit(nmap_result)

            # Convert to DiscoveredService and emit
            for svc in nmap_result.to_discovered_service():
                self.service_found.emit(svc)
                all_services.append(svc)
                self.log_line.emit(
                    f"  {svc.address:22} {svc.service:15} {svc.version[:40]}"
                )
                tracker.update(task.id,
                    progress=80 + int(20 * (i + 1) / max(len(hosts), 1)),
                    detail=f"Found: {svc.address} {svc.service}"
                )

            self.progress.emit(80 + int(20 * (i + 1) / max(len(hosts), 1)))

        self.progress.emit(100)
        tracker.done(task.id, result=f"{len(all_services)} services found")
        self.scan_finished.emit(all_services)

    def _needs_root(self) -> bool:
        """Check if the scan args require root (SYN scan, OS detection, UDP)."""
        args_lower = self._args.lower()
        return any(flag in args_lower for flag in ["-ss", "-so", "-su", "-o ", "-o\n"])


class NmapNSERunner(QThread):
    """Run a specific NSE script against a target."""

    result_ready   = pyqtSignal(str, dict)   # script_name, {host: output}
    log_line       = pyqtSignal(str)
    finished       = pyqtSignal(bool, str)   # success, message

    def __init__(self, target: str, script: str, ports: str = "1-65535",
                 extra_args: str = "", nmap_binary: str = "nmap"):
        super().__init__()
        self._target = target
        self._script = script
        self._ports  = ports
        self._extra  = extra_args
        self._binary = nmap_binary

    def run(self):
        cmd = [
            self._binary, "-sV",
            "--script", self._script,
            "-p", self._ports,
        ]
        if self._extra:
            cmd.extend(self._extra.split())
        cmd.append(self._target)

        self.log_line.emit(f"Running: {' '.join(cmd)}")
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=300
            )
            if r.returncode == 0:
                self.finished.emit(True, r.stdout[:4000])
                self.log_line.emit(r.stdout[:2000])
            else:
                self.finished.emit(False, r.stderr[:500])
        except subprocess.TimeoutExpired:
            self.finished.emit(False, "NSE scan timed out (300s)")
        except FileNotFoundError:
            self.finished.emit(False, f"nmap not found: {self._binary}")
        except Exception as e:
            self.finished.emit(False, str(e))
