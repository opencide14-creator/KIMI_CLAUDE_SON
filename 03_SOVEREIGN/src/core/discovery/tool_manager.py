"""External Tool Manager — detect, install, and manage network scanning tools.

Supports: nmap, masscan, whatweb, nikto
Cross-platform: Linux (apt/snap), macOS (brew), Windows (winget/choco/direct)
Tools install themselves — no manual steps required.
"""
from __future__ import annotations
import logging
import os
import platform
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional

from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)


class ToolStatus(Enum):
    UNKNOWN    = "unknown"
    AVAILABLE  = "available"
    MISSING    = "missing"
    INSTALLING = "installing"
    FAILED     = "failed"


@dataclass
class ToolInfo:
    name:       str
    binary:     str                          # executable name
    version:    str = ""
    path:       str = ""
    status:     ToolStatus = ToolStatus.UNKNOWN
    install_cmds: Dict[str, List[str]] = field(default_factory=dict)
    # install_cmds keys: "linux_apt","linux_snap","macos","windows_winget","windows_choco","windows_direct"

    def is_available(self) -> bool:
        return self.status == ToolStatus.AVAILABLE


# ── Tool catalogue ─────────────────────────────────────────────────────────────

def _build_catalogue() -> Dict[str, ToolInfo]:
    return {
        "nmap": ToolInfo(
            name   = "Nmap",
            binary = "nmap",
            install_cmds = {
                "linux_apt":      ["apt-get", "install", "-y", "nmap"],
                "linux_snap":     ["snap", "install", "nmap"],
                "macos":          ["brew", "install", "nmap"],
                "windows_winget": ["winget", "install", "--id", "Insecure.Nmap", "-e", "--silent"],
                "windows_choco":  ["choco", "install", "nmap", "-y"],
                "windows_direct": None,   # fallback: open download page
            },
        ),
        "masscan": ToolInfo(
            name   = "Masscan",
            binary = "masscan",
            install_cmds = {
                "linux_apt":  ["apt-get", "install", "-y", "masscan"],
                "macos":      ["brew", "install", "masscan"],
                "windows_choco": ["choco", "install", "masscan", "-y"],
            },
        ),
        "whatweb": ToolInfo(
            name   = "WhatWeb",
            binary = "whatweb",
            install_cmds = {
                "linux_apt":  ["apt-get", "install", "-y", "whatweb"],
                "macos":      ["brew", "install", "whatweb"],
            },
        ),
        "nikto": ToolInfo(
            name   = "Nikto",
            binary = "nikto",
            install_cmds = {
                "linux_apt":  ["apt-get", "install", "-y", "nikto"],
                "macos":      ["brew", "install", "nikto"],
                "windows_choco": ["choco", "install", "nikto", "-y"],
            },
        ),
    }


# ── NSE Script Presets ─────────────────────────────────────────────────────────

NSE_PRESETS: Dict[str, Dict] = {
    "Service Detection": {"args":"-sV --version-intensity 7","desc":"Servis versiyonlarini tespit et","timeout":120,"category":"basics"},
    "Default Scripts": {"args":"-sC -sV","desc":"Nmap default NSE script seti + versiyon tespiti","timeout":180,"category":"basics"},
    "OS Detection": {"args":"-O -sV --osscan-guess","desc":"Isletim sistemi parmak izi","timeout":180,"requires_root":True,"category":"basics"},
    "Quick Top 1000": {"args":"-F -T4","desc":"Top 1000 port hizli tarama","timeout":60,"category":"basics"},
    "Full TCP": {"args":"-p- -T4 --min-rate 5000","desc":"Tum 65535 TCP port","timeout":300,"category":"basics"},
    "Stealth SYN": {"args":"-sS -T3","desc":"SYN scan - daha az iz birakir (root gerekir)","timeout":240,"requires_root":True,"category":"basics"},
    "UDP Services": {"args":"-sU --top-ports 100","desc":"Top 100 UDP port","timeout":300,"requires_root":True,"category":"basics"},
    "HTTP Full": {"args":"--script http-title,http-headers,http-methods,http-auth-finder,http-server-header,http-favicon -p 80,443,8080,8443,3000,4000,5000,8000","desc":"HTTP baslik, metot, auth ve fingerprint","timeout":120,"category":"http"},
    "HTTP Enum": {"args":"--script http-enum -p 80,443,8080,8443","desc":"Yaygin dizin ve dosyalari kesfe (1000+ yol)","timeout":180,"category":"http"},
    "HTTP Auth Finder": {"args":"--script http-auth-finder,http-auth -p 80,443,8080,8443","desc":"HTTP auth tipini belirle (Basic/Digest/NTLM/Form)","timeout":90,"category":"http"},
    "HTTP Default Accounts": {"args":"--script http-default-accounts -p 80,443,8080,8443","desc":"Bilinen router/panel default sifrelerini dene","timeout":120,"category":"http"},
    "HTTP Security Headers": {"args":"--script http-security-headers,http-cors,http-cookie-flags -p 80,443","desc":"Guvenlik basliklarini kontrol et (CORS, CSP, Cookie)","timeout":60,"category":"http"},
    "HTTP Git Exposed": {"args":"--script http-git,http-config-backup,http-backup-finder -p 80,443,8080","desc":".git dizini ve backup dosyalarini bul","timeout":60,"category":"http"},
    "WordPress": {"args":"--script http-wordpress-enum,http-wordpress-users -p 80,443,8080","desc":"WordPress kullanici, plugin ve versiyonlari","timeout":120,"category":"http"},
    "FastAPI / REST API": {"args":"--script http-title,http-headers -p 3000,4000,5000,8000,8080,8088,8443,9000","desc":"FastAPI, REST API ve local servis tespiti","timeout":90,"category":"http"},
    "AI Stack": {"args":"--script http-title,http-headers -p 1234,4000,5000,7860,8000,8080,11434","desc":"Ollama, LM Studio, LocalAI, Open-WebUI tespiti","timeout":60,"category":"ai"},
    "MCP / WebSocket": {"args":"--script http-title,http-headers -p 3000,4001,5678,8765,9001","desc":"MCP server ve WebSocket endpoint tespiti","timeout":60,"category":"ai"},
    "MySQL": {"args":"--script mysql-info,mysql-databases,mysql-empty-password -p 3306","desc":"MySQL bilgi toplama ve bos sifre kontrolu","timeout":60,"category":"database"},
    "PostgreSQL": {"args":"--script pgsql-brute -p 5432","desc":"PostgreSQL baglanti analizi","timeout":60,"category":"database"},
    "MS SQL": {"args":"--script ms-sql-info,ms-sql-empty-password -p 1433","desc":"MSSQL bilgi toplama","timeout":60,"category":"database"},
    "MongoDB": {"args":"--script mongodb-info,mongodb-databases -p 27017","desc":"MongoDB instance bilgisi","timeout":60,"category":"database"},
    "Redis": {"args":"--script redis-info -p 6379","desc":"Redis instance bilgisi","timeout":30,"category":"database"},
    "SSH Info": {"args":"--script ssh-auth-methods,ssh-hostkey,ssh2-enum-algos -p 22","desc":"SSH auth metodlari ve host key analizi","timeout":30,"category":"remote"},
    "FTP": {"args":"--script ftp-anon,ftp-syst -p 21","desc":"FTP anonim giris ve sistem bilgisi","timeout":30,"category":"remote"},
    "RDP": {"args":"--script rdp-enum-encryption,rdp-ntlm-info -p 3389","desc":"RDP sifreleme ve NTLM bilgisi","timeout":30,"category":"remote"},
    "VNC": {"args":"--script vnc-info,vnc-title -p 5900,5901","desc":"VNC versiyon ve ekran bilgisi","timeout":30,"category":"remote"},
    "SMB Info": {"args":"--script smb-os-discovery,smb-security-mode,smb2-security-mode -p 445,139","desc":"SMB OS tespiti ve guvenlik modu","timeout":60,"category":"smb"},
    "SMB Enumeration": {"args":"--script smb-enum-shares,smb-enum-users,smb-enum-groups -p 445","desc":"SMB paylasim, kullanici ve grup listesi","timeout":90,"category":"smb"},
    "SNMP": {"args":"-sU --script snmp-info,snmp-interfaces,snmp-processes -p 161","desc":"SNMP bilgi toplama","timeout":60,"category":"network"},
    "DNS": {"args":"--script dns-service-discovery,dns-recursion -p 53","desc":"DNS servis kesfi","timeout":30,"category":"network"},
    "SSL/TLS": {"args":"--script ssl-cert,ssl-enum-ciphers,ssl-dh-params -p 443,8443","desc":"SSL sertifika analizi ve zayif cipher kontrolu","timeout":90,"category":"network"},
    "Vulnerability Scan": {"args":"--script vuln -sV","desc":"Bilinen CVEleri kontrol et (yavas ama kapsamli)","timeout":600,"requires_root":True,"category":"vuln"},
    "Safe Scripts": {"args":"--script safe -sV","desc":"Sadece safe kategorisindeki scriptleri calistir","timeout":180,"category":"vuln"},
}

NSE_CATEGORIES: Dict[str, str] = {
    "basics":   "Temel Tarama",
    "http":     "HTTP / Web",
    "ai":       "AI Stack",
    "database": "Veritabani",
    "remote":   "Uzak Erisim",
    "smb":      "SMB / Windows",
    "network":  "Ag Altyapisi",
    "vuln":     "Vulnerability",
}



class ToolManager(QObject):
    """Detect and auto-install external network tools."""

    tool_status_changed = pyqtSignal(str, object)   # tool_name, ToolStatus
    install_log         = pyqtSignal(str, str)       # tool_name, message
    install_finished    = pyqtSignal(str, bool, str) # tool_name, success, message

    def __init__(self):
        super().__init__()
        self._tools = _build_catalogue()
        self._system = platform.system()

    # ── Detection ─────────────────────────────────────────────────

    def detect_all(self) -> Dict[str, ToolInfo]:
        """Check which tools are installed. Returns updated catalogue."""
        for name, tool in self._tools.items():
            self._detect_tool(tool)
            self.tool_status_changed.emit(name, tool.status)
        return dict(self._tools)

    def _detect_tool(self, tool: ToolInfo):
        path = shutil.which(tool.binary)
        if path:
            tool.path   = path
            tool.status = ToolStatus.AVAILABLE
            tool.version = self._get_version(tool)
        else:
            tool.status = ToolStatus.MISSING

    def _get_version(self, tool: ToolInfo) -> str:
        try:
            r = subprocess.run(
                [tool.binary, "--version"],
                capture_output=True, text=True, timeout=5
            )
            first = (r.stdout or r.stderr).splitlines()
            return first[0].strip()[:60] if first else ""
        except Exception:
            return ""

    def get_tool(self, name: str) -> Optional[ToolInfo]:
        return self._tools.get(name)

    def is_available(self, name: str) -> bool:
        t = self._tools.get(name)
        if not t:
            return False
        if t.status == ToolStatus.UNKNOWN:
            self._detect_tool(t)
        return t.is_available()

    # ── Installation ───────────────────────────────────────────────

    def install(self, name: str):
        """Install a tool in background thread."""
        tool = self._tools.get(name)
        if not tool:
            return
        threading.Thread(
            target=self._install_worker,
            args=(tool,), daemon=True
        ).start()

    def _install_worker(self, tool: ToolInfo):
        tool.status = ToolStatus.INSTALLING
        self.tool_status_changed.emit(tool.name, tool.status)
        self.install_log.emit(tool.name, f"Installing {tool.name}…")

        cmd = self._pick_install_cmd(tool)
        if not cmd:
            msg = f"No install method for {tool.name} on {self._system}"
            log.warning(msg)
            tool.status = ToolStatus.FAILED
            self.install_finished.emit(tool.name, False, msg)
            self.tool_status_changed.emit(tool.name, tool.status)
            return

        self.install_log.emit(tool.name, f"Running: {' '.join(cmd)}")
        try:
            r = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120
            )
            if r.returncode == 0:
                self._detect_tool(tool)
                if tool.is_available():
                    msg = f"{tool.name} installed at {tool.path}"
                    self.install_log.emit(tool.name, f"✅ {msg}")
                    self.install_finished.emit(tool.name, True, msg)
                else:
                    msg = f"Install ran but binary not found"
                    tool.status = ToolStatus.FAILED
                    self.install_finished.emit(tool.name, False, msg)
            else:
                err = (r.stderr or r.stdout)[:200]
                msg = f"Exit {r.returncode}: {err}"
                tool.status = ToolStatus.FAILED
                self.install_log.emit(tool.name, f"❌ {msg}")
                self.install_finished.emit(tool.name, False, msg)
        except subprocess.TimeoutExpired:
            msg = "Install timed out (120s)"
            tool.status = ToolStatus.FAILED
            self.install_finished.emit(tool.name, False, msg)
        except FileNotFoundError as e:
            msg = f"Installer not found: {e}"
            tool.status = ToolStatus.FAILED
            self.install_finished.emit(tool.name, False, msg)
        finally:
            self.tool_status_changed.emit(tool.name, tool.status)

    def _pick_install_cmd(self, tool: ToolInfo) -> Optional[List[str]]:
        """Pick the best install command for the current OS."""
        cmds = tool.install_cmds
        if self._system == "Linux":
            if shutil.which("apt-get") and "linux_apt" in cmds:
                return cmds["linux_apt"]
            if shutil.which("snap") and "linux_snap" in cmds:
                return cmds["linux_snap"]
        elif self._system == "Darwin":
            if "macos" in cmds and shutil.which("brew"):
                return cmds["macos"]
        elif self._system == "Windows":
            if "windows_winget" in cmds and shutil.which("winget"):
                return cmds["windows_winget"]
            if "windows_choco" in cmds and shutil.which("choco"):
                return cmds["windows_choco"]
        return None
