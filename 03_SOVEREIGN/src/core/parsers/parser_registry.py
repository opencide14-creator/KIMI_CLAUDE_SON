"""Parser Registry — route tool outputs to the right parser.
Adapted from user's parser_registry.py for SOVEREIGN integration.
"""
from __future__ import annotations
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class GenericParser:
    """Fallback: extract IPs, URLs, emails, CVEs from any text."""

    _IP    = re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b')
    _URL   = re.compile(r'https?://[^\s<>"{}|\\^`\[\]]+')
    _EMAIL = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')
    _CVE   = re.compile(r'CVE-\d{4}-\d{4,}')
    _MD5   = re.compile(r'\b[a-f0-9]{32}\b')
    _SHA1  = re.compile(r'\b[a-f0-9]{40}\b')

    def parse(self, text: str) -> List[Dict]:
        events = []
        for ip in set(self._IP.findall(text)):
            if self._valid_ip(ip):
                events.append({"type": "IP_FOUND", "ip": ip, "confidence": 0.7})
        for url in set(self._URL.findall(text)):
            events.append({"type": "URL_FOUND", "url": url, "confidence": 0.8})
        for email in set(self._EMAIL.findall(text)):
            events.append({"type": "EMAIL_FOUND", "email": email, "confidence": 0.85})
        for cve in set(self._CVE.findall(text)):
            events.append({"type": "CVE_FOUND", "cve": cve, "confidence": 0.9})
        for h in set(self._MD5.findall(text)):
            events.append({"type": "HASH_FOUND", "hash": h, "hash_type": "MD5", "confidence": 0.7})
        return events

    @staticmethod
    def _valid_ip(ip: str) -> bool:
        try:
            return all(0 <= int(p) <= 255 for p in ip.split("."))
        except ValueError:
            return False


class ParserRegistry:
    """Central hub — route tool output to the correct parser, fall back to generic."""

    TOOL_MAP = {
        # Network
        "nmap":        "NmapParser",
        "masscan":     "NmapParser",   # masscan has nmap-like XML
        # Web
        "nikto":       "GenericParser",
        "nuclei":      "GenericParser",
        "sqlmap":      "SQLMapParser",
        # OSINT
        "sherlock":    "SherlockParser",
        "theharvester":"GenericParser",
        # Credentials
        "hashcat":     "GenericParser",
        "hydra":       "GenericParser",
    }

    def __init__(self):
        self._generic = GenericParser()
        self._cache: Dict[str, Any] = {}

    def parse(self, tool: str, output: str = "",
              output_file: str = "", fmt: str = "text") -> List[Dict]:
        """Parse tool output → list of events."""
        parser_name = self.TOOL_MAP.get(tool.lower(), "GenericParser")
        events: List[Dict] = []

        try:
            if parser_name == "NmapParser":
                from src.core.parsers.nmap_parser import NmapParser
                p = NmapParser()
                if fmt == "xml" or (output and output.strip().startswith("<?xml")):
                    events = [vars(e) for e in p.parse_xml(output)]
                elif output_file:
                    events = [vars(e) for e in p.parse_xml_file(output_file)]
                else:
                    events = [vars(e) for e in p.parse_text(output)]

            elif parser_name == "SherlockParser":
                events = self._parse_sherlock(output)

            elif parser_name == "SQLMapParser":
                events = self._parse_sqlmap(output)

            else:
                if output:
                    events = self._generic.parse(output)
                elif output_file:
                    try:
                        events = self._generic.parse(Path(output_file).read_text())
                    except OSError as e:
                        log.warning("Cannot read output file %s: %s", output_file, e)

        except Exception as e:
            log.error("Parser error (%s → %s): %s", tool, parser_name, e)
            events = self._generic.parse(output or "")

        ts = datetime.now(timezone.utc).isoformat()
        for ev in events:
            ev["source_tool"] = tool
            ev["parsed_at"]   = ts
        return events

    def parse_batch(self, items: List[Dict]) -> List[Dict]:
        all_events = []
        for item in items:
            all_events.extend(self.parse(
                tool=item["tool"],
                output=item.get("output", ""),
                output_file=item.get("file", ""),
                fmt=item.get("format", "text"),
            ))
        return all_events

    def supported_tools(self) -> List[str]:
        return sorted(self.TOOL_MAP.keys())

    # ── Inline parsers for sherlock and sqlmap ─────────────────────

    @staticmethod
    def _parse_sherlock(text: str) -> List[Dict]:
        events = []
        for line in text.splitlines():
            m = re.search(r'\[(\+|\*)\]\s+(\w+):\s+(https?://\S+)', line)
            if m:
                platform = m.group(2)
                url      = m.group(3)
                username = url.rstrip("/").split("/")[-1].lstrip("@")
                events.append({
                    "type":       "USERNAME_FOUND",
                    "platform":   platform,
                    "username":   username,
                    "url":        url,
                    "confidence": 0.95,
                })
        return events

    @staticmethod
    def _parse_sqlmap(text: str) -> List[Dict]:
        events = []
        for line in text.splitlines():
            if "vulnerable" in line.lower() and "parameter" in line.lower():
                m = re.search(r'Parameter:\s+(\w+)\s+\((\w+)\)', line)
                if m:
                    events.append({
                        "type":       "SQL_INJECTION_FOUND",
                        "parameter":  m.group(1),
                        "method":     m.group(2),
                        "confidence": 0.95,
                        "severity":   "CRITICAL",
                    })
            # Hash lines
            h = re.search(r'(\w+):([a-f0-9A-F$*]{16,})', line)
            if h:
                events.append({
                    "type":       "HASH_DUMPED",
                    "username":   h.group(1),
                    "hash":       h.group(2),
                    "confidence": 0.9,
                })
        return events


# Singleton
_registry: Optional[ParserRegistry] = None


def get_registry() -> ParserRegistry:
    global _registry
    if _registry is None:
        _registry = ParserRegistry()
    return _registry
