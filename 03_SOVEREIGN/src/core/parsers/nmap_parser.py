"""Nmap output parser — XML and text formats → DiscoveredService events.
Adapted from user's nmap_parser.py for SOVEREIGN integration.
"""
from __future__ import annotations
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from src.models.gateway import DiscoveredService
from src.constants import Protocol, HostStatus

log = logging.getLogger(__name__)

CRITICAL_PORTS = {
    21: "ftp", 22: "ssh", 23: "telnet", 25: "smtp",
    53: "dns", 80: "http", 110: "pop3", 139: "netbios",
    143: "imap", 443: "https", 445: "smb",
    1433: "mssql", 3306: "mysql", 3389: "rdp",
    5432: "postgresql", 5900: "vnc",
    8080: "http-proxy", 8443: "https-alt",
    11434: "ollama", 4000: "ai-gateway",
}


@dataclass
class NmapEvent:
    type:       str
    ip:         str
    hostname:   str = ""
    port:       int = 0
    protocol:   str = "tcp"
    service:    str = ""
    version:    str = ""
    os:         str = ""
    critical:   bool = False
    confidence: float = 1.0
    metadata:   Dict = field(default_factory=dict)


class NmapParser:
    """Parse nmap XML and plain-text output into structured events."""

    def parse_xml(self, xml_content: str) -> List[NmapEvent]:
        """Parse nmap -oX (XML) output."""
        try:
            root = ET.fromstring(xml_content)
            return self._parse_root(root)
        except ET.ParseError as e:
            log.warning("Nmap XML parse error: %s", e)
            return []

    def parse_xml_file(self, path: str) -> List[NmapEvent]:
        try:
            tree = ET.parse(path)
            return self._parse_root(tree.getroot())
        except Exception as e:
            log.warning("Nmap XML file error: %s", e)
            return []

    def parse_text(self, text: str) -> List[NmapEvent]:
        """Parse nmap plain-text output (-oN)."""
        events: List[NmapEvent] = []
        current_ip = ""
        current_hostname = ""
        current_os = ""

        for line in text.splitlines():
            line = line.strip()
            # Host line
            m = re.match(r"Nmap scan report for (.+?)(?:\s+\((.+?)\))?$", line)
            if m:
                current_hostname = m.group(1)
                current_ip = m.group(2) if m.group(2) else m.group(1)
                events.append(NmapEvent(
                    type="HOST_DISCOVERED", ip=current_ip,
                    hostname=current_hostname, confidence=1.0,
                ))
                continue

            # OS detection
            m = re.match(r"OS details?: (.+)", line)
            if m:
                current_os = m.group(1)
                continue

            # Port line: "22/tcp   open  ssh     OpenSSH 8.2"
            m = re.match(
                r"(\d+)/(tcp|udp)\s+(open|closed|filtered)\s+(\S+)(?:\s+(.+))?", line
            )
            if m and current_ip:
                port     = int(m.group(1))
                proto    = m.group(2)
                state    = m.group(3)
                svc      = m.group(4)
                version  = (m.group(5) or "").strip()

                if state != "open":
                    continue

                events.append(NmapEvent(
                    type="OPEN_PORT_DETECTED",
                    ip=current_ip, hostname=current_hostname,
                    port=port, protocol=proto,
                    service=svc, version=version, os=current_os,
                    critical=(port in CRITICAL_PORTS),
                    confidence=1.0,
                    metadata={"state": state},
                ))

        return events

    def to_discovered_services(self, events: List[NmapEvent]) -> List[DiscoveredService]:
        """Convert NmapEvents to DiscoveredService objects for SOVEREIGN state."""
        services = []
        for ev in events:
            if ev.type != "OPEN_PORT_DETECTED":
                continue
            proto = Protocol.HTTPS if ev.port in (443, 8443, 9443) else Protocol.HTTP
            svc = DiscoveredService(
                host     = ev.ip,
                port     = ev.port,
                protocol = proto,
                status   = HostStatus.OPEN,
                service  = ev.service,
                version  = ev.version,
                is_ai_api= ev.port in (11434, 4000, 8000, 5000),
                notes    = f"OS: {ev.os}" if ev.os else "",
            )
            services.append(svc)
        return services

    def get_attack_surface(self, events: List[NmapEvent]) -> Dict:
        open_ports  = [e for e in events if e.type == "OPEN_PORT_DETECTED"]
        critical    = [e for e in open_ports if e.critical]
        services: Dict[str, List[int]] = {}
        for e in open_ports:
            services.setdefault(e.service, []).append(e.port)
        return {
            "total_open":   len(open_ports),
            "critical":     len(critical),
            "services":     services,
            "unique_hosts": len(set(e.ip for e in open_ports)),
            "critical_detail": [
                {"ip": e.ip, "port": e.port, "service": e.service}
                for e in critical
            ],
        }

    # ── Private ────────────────────────────────────────────────────

    def _parse_root(self, root: ET.Element) -> List[NmapEvent]:
        events = []
        for host in root.findall(".//host"):
            events.extend(self._parse_host(host))
        return events

    def _parse_host(self, host: ET.Element) -> List[NmapEvent]:
        events = []
        status = host.find("status")
        if status is None or status.get("state") != "up":
            return events

        ip_elem = (host.find('.//address[@addrtype="ipv4"]') or
                   host.find('.//address[@addrtype="ipv6"]'))
        if ip_elem is None:
            return events
        ip = ip_elem.get("addr", "")

        hostname = ""
        h_elem = host.find(".//hostname")
        if h_elem is not None:
            hostname = h_elem.get("name", "")

        os_name = ""
        os_elem = host.find(".//osmatch")
        if os_elem is not None:
            os_name = os_elem.get("name", "")

        events.append(NmapEvent(
            type="HOST_DISCOVERED", ip=ip,
            hostname=hostname, os=os_name, confidence=1.0,
        ))

        for port_el in host.findall(".//port"):
            ev = self._parse_port(port_el, ip, hostname, os_name)
            if ev:
                events.append(ev)
        return events

    def _parse_port(self, port_el: ET.Element,
                    ip: str, hostname: str, os: str) -> Optional[NmapEvent]:
        port_id  = int(port_el.get("portid", 0))
        proto    = port_el.get("protocol", "tcp")
        state_el = port_el.find("state")
        if state_el is None or state_el.get("state") != "open":
            return None
        svc_el   = port_el.find("service")
        svc_name = ""
        version  = ""
        if svc_el is not None:
            svc_name = svc_el.get("name", "")
            product  = svc_el.get("product", "")
            ver      = svc_el.get("version", "")
            version  = f"{product} {ver}".strip()
        return NmapEvent(
            type="OPEN_PORT_DETECTED",
            ip=ip, hostname=hostname, os=os,
            port=port_id, protocol=proto,
            service=svc_name, version=version,
            critical=(port_id in CRITICAL_PORTS),
            confidence=1.0,
            metadata={"nmap_state": "open"},
        )
