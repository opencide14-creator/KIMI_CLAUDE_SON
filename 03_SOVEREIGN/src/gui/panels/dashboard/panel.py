"""Dashboard Panel — live service status, start/stop controls, traffic summary."""
from __future__ import annotations
import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QGridLayout,
    QLabel, QGroupBox, QSpinBox, QLineEdit,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, ServiceStatus, DEFAULT_PROXY_PORT, DEFAULT_GATEWAY_PORT
from src.models.state import get_state, SK
from src.gui.widgets.common import (
    NeonLabel, DimLabel, PulseDot, LogConsole,
    neon_btn, ghost_btn, danger_btn, ServiceStatusRow,
)

log = logging.getLogger(__name__)


class ServiceCard(QGroupBox):
    """A service control card: status dot, label, start/stop button, port."""
    def __init__(self, title: str, color: str, parent=None):
        super().__init__(parent)
        self._color = color
        self.setStyleSheet(f"""
            QGroupBox {{
                color: {color};
                border: 1px solid {COLORS['border']};
                border-radius: 4px;
                margin-top: 14px;
                padding-top: 10px;
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1px;
                font-family: 'JetBrains Mono','Consolas',monospace;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 10px; top: -2px;
                padding: 0 6px;
                color: {color};
                font-size: 11px;
                font-weight: bold;
                letter-spacing: 1.5px;
            }}
        """)
        self.setTitle(title)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setContentsMargins(10, 8, 10, 10)

        # Status row
        status_row = QHBoxLayout()
        self._dot    = PulseDot(ServiceStatus.STOPPED, size=12)
        self._status = QLabel("STOPPED")
        self._status.setStyleSheet(f"color:{COLORS['text_muted']};font-size:11px;letter-spacing:0.5px;")
        status_row.addWidget(self._dot)
        status_row.addWidget(self._status)
        status_row.addStretch()
        lay.addLayout(status_row)

        # Stat display
        self._stat = QLabel("—")
        self._stat.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
        lay.addWidget(self._stat)

    def update_status(self, status: ServiceStatus, stat_text: str = ""):
        self._dot.set_status(status)
        labels = {
            ServiceStatus.STOPPED:  ("STOPPED",   COLORS["text_muted"]),
            ServiceStatus.STARTING: ("STARTING…", COLORS["neon_yellow"]),
            ServiceStatus.RUNNING:  ("RUNNING",   COLORS["neon_green"]),
            ServiceStatus.ERROR:    ("ERROR",      COLORS["neon_red"]),
        }
        text, color = labels.get(status, ("?", COLORS["text_muted"]))
        self._status.setText(text)
        self._status.setStyleSheet(f"color:{color};font-size:11px;letter-spacing:0.5px;font-weight:bold;")
        if stat_text:
            self._stat.setText(stat_text)


class DashboardPanel(QWidget):
    """Real-time dashboard: service controls, traffic counters, quick actions."""

    def __init__(self, proxy=None, gateway=None, discovery=None, parent=None):
        super().__init__(parent)
        self._proxy     = proxy
        self._gateway   = gateway
        self._discovery = discovery
        self._build()
        self._subscribe()
        # Refresh every 2s
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(2000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(14)

        # ── Title ──────────────────────────────────────────────────
        title = QLabel("⚔  SOVEREIGN")
        title.setStyleSheet(
            f"color:{COLORS['neon_blue']};font-size:22px;font-weight:bold;"
            f"letter-spacing:3px;font-family:'JetBrains Mono','Consolas',monospace;"
        )
        sub = QLabel("Network Sovereignty Command Center")
        sub.setStyleSheet(f"color:{COLORS['text_muted']};font-size:11px;letter-spacing:1px;")
        lay.addWidget(title)
        lay.addWidget(sub)

        # ── Service cards grid ─────────────────────────────────────
        grid = QGridLayout()
        grid.setSpacing(10)

        # Proxy card
        self._proxy_card = ServiceCard("🔴  PROXY", COLORS["neon_red"])
        self._proxy_start = neon_btn("▶ START", COLORS["neon_green"])
        self._proxy_stop  = danger_btn("■ STOP")
        self._proxy_stop.setEnabled(False)
        self._proxy_port  = QSpinBox()
        self._proxy_port.setRange(1024, 65535)
        self._proxy_port.setValue(DEFAULT_PROXY_PORT)
        self._proxy_port.setStyleSheet(
            f"background:{COLORS['bg_input']};color:{COLORS['neon_blue']};"
            f"border:1px solid {COLORS['border']};border-radius:3px;padding:3px;"
        )
        self._proxy_start.clicked.connect(self._start_proxy)
        self._proxy_stop.clicked.connect(self._stop_proxy)
        pbtn = QHBoxLayout()
        pbtn.addWidget(QLabel("Port:"))
        pbtn.addWidget(self._proxy_port)
        pbtn.addStretch()
        pbtn.addWidget(self._proxy_start)
        pbtn.addWidget(self._proxy_stop)
        self._proxy_card.layout().addLayout(pbtn)
        grid.addWidget(self._proxy_card, 0, 0)

        # Gateway card
        self._gw_card   = ServiceCard("🟢  GATEWAY", COLORS["neon_orange"])
        self._gw_start  = neon_btn("▶ START", COLORS["neon_green"])
        self._gw_stop   = danger_btn("■ STOP")
        self._gw_stop.setEnabled(False)
        self._gw_port   = QSpinBox()
        self._gw_port.setRange(1024, 65535)
        self._gw_port.setValue(DEFAULT_GATEWAY_PORT)
        self._gw_port.setStyleSheet(self._proxy_port.styleSheet())
        self._gw_start.clicked.connect(self._start_gateway)
        self._gw_stop.clicked.connect(self._stop_gateway)
        gbtn = QHBoxLayout()
        gbtn.addWidget(QLabel("Port:"))
        gbtn.addWidget(self._gw_port)
        gbtn.addStretch()
        gbtn.addWidget(self._gw_start)
        gbtn.addWidget(self._gw_stop)
        self._gw_card.layout().addLayout(gbtn)
        grid.addWidget(self._gw_card, 0, 1)

        lay.addLayout(grid)

        # ── Traffic summary ────────────────────────────────────────
        traffic_box = QGroupBox("TRAFFIC")
        traffic_box.setStyleSheet(
            f"QGroupBox {{color:{COLORS['text_muted']};border:1px solid {COLORS['border']};"
            f"border-radius:4px;margin-top:14px;padding-top:8px;font-size:10px;letter-spacing:1px;}}"
            f"QGroupBox::title {{subcontrol-origin:margin;left:8px;color:{COLORS['text_muted']};"
            f"font-size:10px;letter-spacing:1px;}}"
        )
        tl = QHBoxLayout(traffic_box)
        self._total_lbl   = self._big_counter("REQUESTS",  COLORS["neon_blue"])
        self._ai_lbl      = self._big_counter("AI CALLS",  COLORS["neon_orange"])
        self._ws_lbl      = self._big_counter("WS CONNS",  COLORS["neon_cyan"])
        self._errors_lbl  = self._big_counter("ERRORS",    COLORS["neon_red"])
        self._routes_lbl  = self._big_counter("ROUTES",    COLORS["neon_green"])
        for w in (self._total_lbl, self._ai_lbl, self._ws_lbl, self._errors_lbl, self._routes_lbl):
            tl.addWidget(w)
        lay.addWidget(traffic_box)

        # ── Quick actions ──────────────────────────────────────────
        qa_lbl = NeonLabel("QUICK ACTIONS", COLORS["text_muted"])
        qa_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;letter-spacing:1px;")
        lay.addWidget(qa_lbl)

        qa_row = QHBoxLayout()
        wizard_btn  = neon_btn("⚡ Claude→Kimi Wizard", COLORS["neon_orange"])
        wizard_btn.clicked.connect(lambda: get_state().set(SK.ACTIVE_PANEL, "forge"))
        scan_btn    = ghost_btn("🟣 Scan Local Services")
        scan_btn.clicked.connect(self._quick_scan)
        intercept_btn = ghost_btn("🔴 Go to Intercept")
        intercept_btn.clicked.connect(lambda: get_state().set(SK.ACTIVE_PANEL, "intercept"))
        qa_row.addWidget(wizard_btn)
        qa_row.addWidget(scan_btn)
        qa_row.addWidget(intercept_btn)
        qa_row.addStretch()
        lay.addLayout(qa_row)

        # ── Log ────────────────────────────────────────────────────
        lay.addWidget(NeonLabel("EVENT LOG", COLORS["text_muted"]))
        self._log = LogConsole(max_lines=200)
        self._log.setMaximumHeight(180)
        lay.addWidget(self._log)
        lay.addStretch()

    def _big_counter(self, label: str, color: str) -> QWidget:
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 4, 8, 4)
        num = QLabel("0")
        num.setObjectName(f"num_{label}")
        num.setAlignment(Qt.AlignmentFlag.AlignCenter)
        num.setStyleSheet(
            f"color:{color};font-size:24px;font-weight:bold;"
            f"font-family:'JetBrains Mono','Consolas',monospace;"
        )
        lbl = QLabel(label)
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:9px;letter-spacing:1px;")
        lay.addWidget(num)
        lay.addWidget(lbl)
        return w

    def _get_num(self, widget: QWidget) -> QLabel:
        return widget.findChild(QLabel, widget.layout().itemAt(0).widget().objectName()
                                if widget.layout().count() > 0 else "") or widget.findChildren(QLabel)[0]

    # ── Service controls ───────────────────────────────────────────

    def _start_proxy(self):
        if not self._proxy:
            self._log.log("Proxy engine not available", "ERROR")
            return
        # S-18: Warn user if CA cert not installed — HTTPS interception will fail silently
        from src.constants import CERT_DIR
        from pathlib import Path
        ca_cert = CERT_DIR / "sovereign-ca.crt"
        if not ca_cert.exists():
            self._log.log(
                "⚠ CA cert not found — HTTPS interception requires cert trust. "                "Go to FORGE panel → Generate CA → Install in OS trust store.",
                "WARN"
            )
        port = self._proxy_port.value()
        self._proxy._port = port
        self._proxy.start()
        self._log.log(f"Starting proxy on port {port}…", "INFO")

    def _stop_proxy(self):
        if self._proxy:
            self._proxy.stop()
            self._log.log("Proxy stopped", "WARN")

    def _start_gateway(self):
        if not self._gateway:
            self._log.log("Gateway not available", "ERROR")
            return
        port = self._gw_port.value()
        self._gateway._port = port   # sync port before starting
        self._gateway.start()
        self._log.log(f"Starting gateway on port {port}…", "INFO")

    def _stop_gateway(self):
        if self._gateway:
            self._gateway.stop()
            self._log.log("Gateway stopped", "WARN")

    def _quick_scan(self):
        if self._discovery:
            self._log.log("Scanning 127.0.0.1:1-9999…", "INFO")
            self._discovery.scan("127.0.0.1", 1, 9999)
            get_state().set(SK.ACTIVE_PANEL, "discover")

    # ── State subscriptions ────────────────────────────────────────

    def _subscribe(self):
        get_state().subscribe(SK.PROXY_STATUS,   self._on_proxy_status)
        get_state().subscribe(SK.GATEWAY_STATUS, self._on_gw_status)
        get_state().subscribe(SK.GATEWAY_REQUEST_COUNT, self._refresh)

    def _on_proxy_status(self, status: ServiceStatus):
        if not isinstance(status, ServiceStatus):
            return
        self._proxy_card.update_status(
            status,
            f"port {self._proxy_port.value()}"
        )
        running = status == ServiceStatus.RUNNING
        self._proxy_start.setEnabled(not running)
        self._proxy_stop.setEnabled(running)
        level = "OK" if running else ("ERROR" if status == ServiceStatus.ERROR else "INFO")
        self._log.log(f"Proxy → {status.value.upper()}", level)

    def _on_gw_status(self, status: ServiceStatus):
        if not isinstance(status, ServiceStatus):
            return
        routes = get_state().get(SK.GATEWAY_ROUTES, [])
        self._gw_card.update_status(
            status,
            f"port {self._gw_port.value()} · {len(routes)} routes"
        )
        running = status == ServiceStatus.RUNNING
        self._gw_start.setEnabled(not running)
        self._gw_stop.setEnabled(running)
        level = "OK" if running else ("ERROR" if status == ServiceStatus.ERROR else "INFO")
        self._log.log(f"Gateway → {status.value.upper()}", level)

    def _refresh(self, *_):
        # Traffic counters
        entries  = get_state().get(SK.TRAFFIC_ENTRIES, [])
        ai_calls = sum(1 for e in entries if e.request.is_ai_api)
        errors   = sum(1 for e in entries if e.response and e.response.status_code >= 400)
        ws_conns = len(get_state().get(SK.WS_CONNECTIONS, []))
        routes   = len(get_state().get(SK.GATEWAY_ROUTES, []))

        counters = [
            (self._total_lbl,  str(len(entries))),
            (self._ai_lbl,     str(ai_calls)),
            (self._ws_lbl,     str(ws_conns)),
            (self._errors_lbl, str(errors)),
            (self._routes_lbl, str(routes)),
        ]
        for widget, val in counters:
            labels = widget.findChildren(QLabel)
            if labels:
                labels[0].setText(val)

        # Proxy card stats
        p_status = get_state().get(SK.PROXY_STATUS, ServiceStatus.STOPPED)
        if isinstance(p_status, ServiceStatus):
            self._proxy_card.update_status(p_status,
                f"port {self._proxy_port.value()} · {len(entries)} captured")

        # Gateway card stats
        g_status = get_state().get(SK.GATEWAY_STATUS, ServiceStatus.STOPPED)
        if isinstance(g_status, ServiceStatus):
            req_count = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0)
            self._gw_card.update_status(g_status,
                f"port {self._gw_port.value()} · {req_count} routed")
