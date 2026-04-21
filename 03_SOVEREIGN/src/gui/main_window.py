"""SOVEREIGN — Main Application Window."""
from __future__ import annotations
import logging
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QFont, QColor
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
    QStackedWidget, QLabel, QPushButton, QStatusBar,
    QApplication,
)

from src.constants import (
    APP_NAME, APP_VERSION, APP_SUBTITLE, COLORS, PanelID,
    ServiceStatus, DEFAULT_PROXY_PORT, DEFAULT_GATEWAY_PORT,
)
from src.core.proxy.engine import ProxyEngine
from src.core.gateway.router import GatewayRouter
from src.core.discovery.scanner import ServiceDiscovery
from src.core.stream.monitor import StreamMonitor
from src.core.vault.store import VaultStore
from src.models.state import get_state, SK
from src.gui.widgets.common import PulseDot, NeonLabel, DimLabel

log = logging.getLogger(__name__)


class SidebarButton(QPushButton):
    def __init__(self, icon: str, label: str, color: str, panel_id: PanelID):
        super().__init__(f" {icon}  {label}")
        self.panel_id = panel_id
        self._color   = color
        self.setCheckable(True)
        self.setFixedHeight(38)
        self._apply(False)

    def _apply(self, active: bool):
        if active:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: {COLORS['bg_highlight']};
                    color: {self._color};
                    border: none;
                    border-left: 3px solid {self._color};
                    padding: 0 10px 0 7px;
                    text-align: left;
                    font-size: 12px;
                    font-weight: bold;
                    font-family: "JetBrains Mono","Consolas",monospace;
                    letter-spacing: 0.5px;
                }}
            """)
        else:
            self.setStyleSheet(f"""
                QPushButton {{
                    background: transparent;
                    color: {COLORS['text_muted']};
                    border: none;
                    border-left: 3px solid transparent;
                    padding: 0 10px 0 7px;
                    text-align: left;
                    font-size: 12px;
                    font-family: "JetBrains Mono","Consolas",monospace;
                }}
                QPushButton:hover {{
                    background: {COLORS['bg_panel']};
                    color: {COLORS['text_primary']};
                }}
            """)

    def setChecked(self, checked: bool):
        super().setChecked(checked)
        self._apply(checked)


class Sidebar(QWidget):
    PANELS = [
        ("⬛", "DASHBOARD", COLORS["neon_blue"],   PanelID.DASHBOARD),
        ("🤖", "AI CHAT",   COLORS["neon_cyan"],   PanelID.CHAT),
        ("🔴", "INTERCEPT", COLORS["neon_red"],    PanelID.INTERCEPT),
        ("🟡", "FORGE",     COLORS["neon_yellow"],  PanelID.FORGE),
        ("🟢", "GATEWAY",   COLORS["neon_orange"],  PanelID.GATEWAY),
        ("🔵", "STREAMS",   COLORS["neon_cyan"],    PanelID.STREAMS),
        ("🟣", "DISCOVER",  COLORS["neon_purple"],  PanelID.DISCOVER),
        ("⚪", "VAULT",     COLORS["text_muted"],   PanelID.VAULT),
        ("📊", "INTEL",     COLORS["neon_blue"],    PanelID.INTEL),
    ]

    def __init__(self, on_select, parent=None):
        super().__init__(parent)
        self._on_select  = on_select
        self._buttons:   dict[PanelID, SidebarButton] = {}
        self.setFixedWidth(170)
        self.setStyleSheet(
            f"background:{COLORS['bg_void']};"
            f"border-right:1px solid {COLORS['border']};"
        )
        self._build()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Logo
        logo = QWidget()
        logo.setFixedHeight(56)
        logo.setStyleSheet(f"border-bottom:1px solid {COLORS['border']};")
        ll = QVBoxLayout(logo)
        ll.setContentsMargins(12, 8, 8, 8)
        title = QLabel(APP_NAME)
        title.setStyleSheet(
            f"color:{COLORS['neon_blue']};font-size:16px;font-weight:bold;"
            f"letter-spacing:2px;font-family:'JetBrains Mono','Consolas',monospace;"
        )
        sub = QLabel(f"v{APP_VERSION}")
        sub.setStyleSheet(f"color:{COLORS['text_dim']};font-size:9px;letter-spacing:1px;")
        ll.addWidget(title)
        ll.addWidget(sub)
        lay.addWidget(logo)

        for icon, label, color, pid in self.PANELS:
            btn = SidebarButton(icon, label, color, pid)
            btn.clicked.connect(lambda _, p=pid: self._click(p))
            self._buttons[pid] = btn
            lay.addWidget(btn)

        lay.addStretch()

        # Service status dots at bottom
        status_w = QWidget()
        status_w.setFixedHeight(80)
        status_w.setStyleSheet(f"border-top:1px solid {COLORS['border']};")
        sl = QVBoxLayout(status_w)
        sl.setContentsMargins(10, 6, 8, 6)
        sl.setSpacing(4)

        self._proxy_dot  = self._make_status_dot("PROXY",   PanelID.INTERCEPT)
        self._gw_dot     = self._make_status_dot("GATEWAY", PanelID.GATEWAY)
        sl.addLayout(self._proxy_dot[0])
        sl.addLayout(self._gw_dot[0])
        lay.addWidget(status_w)

        # Wire state
        get_state().subscribe(SK.PROXY_STATUS,   self._on_proxy_status)
        get_state().subscribe(SK.GATEWAY_STATUS, self._on_gw_status)

    def _make_status_dot(self, label: str, pid: PanelID):
        row = QHBoxLayout()
        row.setSpacing(6)
        dot = PulseDot(ServiceStatus.STOPPED, size=8)
        lbl = QLabel(f"{label}  STOPPED")
        lbl.setStyleSheet(f"color:{COLORS['text_dim']};font-size:9px;letter-spacing:0.5px;")
        row.addWidget(dot)
        row.addWidget(lbl)
        row.addStretch()
        return row, dot, lbl

    def _click(self, pid: PanelID):
        for p, b in self._buttons.items():
            b.setChecked(p == pid)
        self._on_select(pid)

    def select(self, pid: PanelID):
        """Select a panel — highlights button AND triggers panel switch via _on_select."""
        self._click(pid)

    def highlight(self, pid: PanelID):
        """Only update button appearance — no panel switch (avoids recursion)."""
        for p, b in self._buttons.items():
            b.setChecked(p == pid)

    def _on_proxy_status(self, status: ServiceStatus):
        _, dot, lbl = self._proxy_dot
        dot.set_status(status)
        lbl.setText(f"PROXY  {status.value.upper()}")
        color = {
            ServiceStatus.RUNNING: COLORS["neon_green"],
            ServiceStatus.ERROR:   COLORS["neon_red"],
            ServiceStatus.STARTING:COLORS["neon_yellow"],
        }.get(status, COLORS["text_dim"])
        lbl.setStyleSheet(f"color:{color};font-size:9px;letter-spacing:0.5px;")

    def _on_gw_status(self, status: ServiceStatus):
        _, dot, lbl = self._gw_dot
        dot.set_status(status)
        lbl.setText(f"GATEWAY  {status.value.upper()}")
        color = {
            ServiceStatus.RUNNING: COLORS["neon_green"],
            ServiceStatus.ERROR:   COLORS["neon_red"],
            ServiceStatus.STARTING:COLORS["neon_yellow"],
        }.get(status, COLORS["text_dim"])
        lbl.setStyleSheet(f"color:{color};font-size:9px;letter-spacing:0.5px;")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"⚔  {APP_NAME}  —  {APP_SUBTITLE}")
        self.resize(1440, 900)
        self.setMinimumSize(1100, 700)

        # ── Core services ──────────────────────────────────────────
        self._proxy    = ProxyEngine(port=DEFAULT_PROXY_PORT)
        self._gateway  = GatewayRouter(port=DEFAULT_GATEWAY_PORT)
        self._discovery= ServiceDiscovery()
        self._monitor  = StreamMonitor()

        # Wire proxy → stream monitor (WS)
        self._proxy.ws_connected.connect(self._monitor.on_ws_connect)
        self._proxy.ws_frame.connect(self._monitor.on_ws_frame)
        self._proxy.ws_closed.connect(self._monitor.on_ws_close)

        # Wire traffic into state (for Intel + cross-panel broadcast)
        # request_captured  → TRAFFIC_NEW  → _on_new_entry  (adds to list)
        # response_captured → TRAFFIC_UPDATE → on_entry_updated (updates existing row)
        self._proxy.request_captured.connect(
            lambda e: get_state().set(SK.TRAFFIC_NEW, e)
        )
        self._proxy.response_captured.connect(
            lambda e: get_state().set(SK.TRAFFIC_UPDATE, e)
        )

        # Wire gateway events into state
        self._gateway.request_routed.connect(self._on_gateway_routed)
        self._gateway.error_occurred.connect(
            lambda e: log.error("Gateway error: %s", e)
        )

        # ── Panels (lazy, cached — each built once) ────────────────
        self._panels:   dict[PanelID, QWidget] = {}
        self._factories = {
            PanelID.DASHBOARD: self._make_dashboard,
            PanelID.CHAT:      self._make_chat,
            PanelID.INTERCEPT: self._make_intercept,
            PanelID.FORGE:     self._make_forge,
            PanelID.GATEWAY:   self._make_gateway,
            PanelID.STREAMS:   self._make_streams,
            PanelID.DISCOVER:  self._make_discover,
            PanelID.VAULT:     self._make_vault,
            PanelID.INTEL:     self._make_intel,
        }

        self._build_ui()
        self._build_menu()
        self._build_statusbar()

        # Default panel
        self._sidebar.select(PanelID.INTERCEPT)

        # Subscribe to ACTIVE_PANEL state — allows any panel to request a switch
        get_state().subscribe(SK.ACTIVE_PANEL, self._on_active_panel_changed)

        # Intercept panel action subscriptions (BUG-3: intercept.block_host / add_route_host were set but never consumed)
        get_state().subscribe("intercept.block_host", self._on_block_host)
        get_state().subscribe("intercept.add_route_host", self._on_add_route_host)

        # Wizard proxy route subscription (BUG-5: wizard sets proxy route but never wires it to proxy engine)
        get_state().subscribe("proxy.set_route", self._on_proxy_set_route)

        # Status bar refresh
        self._sb_timer = QTimer(self)
        self._sb_timer.timeout.connect(self._update_statusbar)
        self._sb_timer.start(2000)

        # Auto-unlock vault
        VaultStore.unlock("")

        log.info("SOVEREIGN %s started", APP_VERSION)

    # ── UI Construction ────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # ── Main area: sidebar + panel stack ──────────────────────
        main_w = QWidget()
        lay    = QHBoxLayout(main_w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        self._sidebar = Sidebar(self._switch_panel)
        lay.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setStyleSheet(f"background:{COLORS['bg_dark']};")
        lay.addWidget(self._stack, 1)
        root_lay.addWidget(main_w, 1)

        # ── Activity bar — always visible at bottom ────────────────
        from src.gui.widgets.progress import ProgressPanel
        self._progress_panel = ProgressPanel()
        self._progress_panel.setFixedHeight(130)
        self._progress_panel.setStyleSheet(
            f"border-top:1px solid {COLORS['border']};"
        )
        root_lay.addWidget(self._progress_panel)

    def _build_menu(self):
        mb = self.menuBar()
        mb.setStyleSheet(
            f"QMenuBar {{background:{COLORS['bg_void']};color:{COLORS['text_muted']};"
            f"border-bottom:1px solid {COLORS['border']};font-size:11px;}}"
            f"QMenuBar::item:selected {{color:{COLORS['neon_blue']};}}"
        )

        # Services menu
        svc = mb.addMenu("Services")
        start_proxy = QAction("▶ Start Proxy", self)
        start_proxy.triggered.connect(lambda: self._proxy.start())
        stop_proxy  = QAction("■ Stop Proxy", self)
        stop_proxy.triggered.connect(self._proxy.stop)
        start_gw    = QAction("▶ Start Gateway", self)
        start_gw.triggered.connect(self._gateway.start)
        stop_gw     = QAction("■ Stop Gateway", self)
        stop_gw.triggered.connect(self._gateway.stop)
        svc.addAction(start_proxy)
        svc.addAction(stop_proxy)
        svc.addSeparator()
        svc.addAction(start_gw)
        svc.addAction(stop_gw)

        # View menu
        view = mb.addMenu("View")
        for icon, label, color, pid in Sidebar.PANELS:
            act = QAction(f"{icon} {label}", self)
            act.triggered.connect(lambda _, p=pid: self._sidebar.select(p))
            view.addAction(act)

        # Help menu
        help_m = mb.addMenu("Help")
        docs = QAction("Documentation…", self)
        docs.triggered.connect(lambda: webbrowser.open("https://github.com/sovereign-tool/sovereign"))
        help_m.addAction(docs)

    def _build_statusbar(self):
        sb = QStatusBar()
        sb.setStyleSheet(
            f"QStatusBar {{background:{COLORS['bg_void']};color:{COLORS['text_muted']};"
            f"border-top:1px solid {COLORS['border']};font-size:10px;"
            f"font-family:'JetBrains Mono','Consolas',monospace;}}"
        )
        self.setStatusBar(sb)
        self._sb = sb

    def _update_statusbar(self):
        proxy_status   = get_state().get(SK.PROXY_STATUS,   ServiceStatus.STOPPED)
        gw_status      = get_state().get(SK.GATEWAY_STATUS, ServiceStatus.STOPPED)
        traffic_count  = len(get_state().get(SK.TRAFFIC_ENTRIES, []))
        ws_count       = len(get_state().get(SK.WS_CONNECTIONS, []))
        req_count      = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0)
        self._sb.showMessage(
            f"  PROXY: {proxy_status.value.upper()}"
            f"  ·  GATEWAY: {gw_status.value.upper()}"
            f"  ·  Traffic: {traffic_count:,}"
            f"  ·  WS: {ws_count}"
            f"  ·  Routed: {req_count:,}"
        )

    # ── Panel factory ──────────────────────────────────────────────

    def _switch_panel(self, pid: PanelID):
        if pid not in self._panels:
            widget = self._factories[pid]()
            self._panels[pid] = widget
            self._stack.addWidget(widget)
        self._stack.setCurrentWidget(self._panels[pid])
        # NOTE: do NOT write SK.ACTIVE_PANEL here — would cause recursion.
        # External callers write state; _on_active_panel_changed calls us.

    def _make_dashboard(self) -> QWidget:
        from src.gui.panels.dashboard.panel import DashboardPanel
        return DashboardPanel(self._proxy, self._gateway, self._discovery)

    def _make_chat(self) -> QWidget:
        from src.gui.panels.chat.panel import ChatPanel
        return ChatPanel(self._proxy, self._gateway, self._discovery)

    def _make_intercept(self) -> QWidget:
        from src.gui.panels.intercept.panel import InterceptPanel
        return InterceptPanel(self._proxy)

    def _make_forge(self) -> QWidget:
        from src.gui.panels.forge.panel import ForgePanel
        return ForgePanel()

    def _make_gateway(self) -> QWidget:
        from src.gui.panels.gateway.panel import GatewayPanel
        return GatewayPanel(self._gateway)

    def _make_streams(self) -> QWidget:
        from src.gui.panels.streams.panel import StreamsPanel
        return StreamsPanel(self._monitor)

    def _make_discover(self) -> QWidget:
        from src.gui.panels.discover.panel import DiscoverPanel
        return DiscoverPanel(self._discovery)

    def _make_vault(self) -> QWidget:
        from src.gui.panels.vault.panel import VaultPanel
        return VaultPanel()

    def _make_intel(self) -> QWidget:
        from src.gui.panels.intel.panel import IntelPanel
        return IntelPanel()

    # ── Lifecycle ──────────────────────────────────────────────────

    def _on_active_panel_changed(self, panel_id):
        """Called when any code does get_state().set(SK.ACTIVE_PANEL, pid).
        Directly switches panel without going through sidebar (avoids recursion)."""
        try:
            pid = PanelID(panel_id) if isinstance(panel_id, str) else panel_id
            if isinstance(pid, PanelID):
                self._switch_panel(pid)
                self._sidebar.highlight(pid)   # highlight button without triggering click
        except (ValueError, AttributeError) as e:
            log.debug("Active panel change ignored: %s", e)

    def _on_gateway_routed(self, route, source_model, target_model):
        """Gateway routed a request — update state counters."""
        from src.constants import SK
        count = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0)
        get_state().set(SK.GATEWAY_REQUEST_COUNT, count + 1)

    def _on_block_host(self, host: str):
        """Called when intercept.panel sets intercept.block_host — forward to proxy engine."""
        if host:
            self._proxy.block_host(host)

    def _on_add_route_host(self, host: str):
        """Called when intercept.panel sets intercept.add_route_host — switch to gateway with host pre-filled."""
        if host:
            self._switch_to_gateway_with_host(host)

    def _on_proxy_set_route(self, data: dict):
        """Called when wizard sets proxy.set_route — wire the route into the proxy engine."""
        if data and isinstance(data, dict):
            source_host = data.get("source_host")
            target_url = data.get("target_url")
            if source_host and target_url:
                self._proxy.set_route(source_host, target_url)
                log.info("Proxy route set: %s -> %s", source_host, target_url)

    def _switch_to_gateway_with_host(self, host: str):
        """Switch to Gateway panel and pre-fill the host field."""
        self._sidebar.select(PanelID.GATEWAY)
        # Defer to next tick so the panel is created and visible before we poke it
        QTimer.singleShot(50, lambda: self._fill_gateway_host(host))

    def _fill_gateway_host(self, host: str):
        """Set the host field on the Gateway panel if it has that control."""
        panel = self._panels.get(PanelID.GATEWAY)
        if panel:
            from src.gui.panels.gateway.panel import GatewayPanel
            if isinstance(panel, GatewayPanel):
                panel.set_focus_host(host)

    def closeEvent(self, event):
        log.info("Shutting down services…")
        self._proxy.stop()
        self._gateway.stop()
        VaultStore.lock()
        event.accept()
