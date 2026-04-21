"""Discover Panel — nmap/NSE integration, service fingerprinting, AI stack detection."""
from __future__ import annotations
import logging
import re
import webbrowser

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QLineEdit, QSpinBox, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QProgressBar, QCheckBox, QListWidget, QListWidgetItem,
    QComboBox, QGroupBox, QScrollArea,
    QMessageBox,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, HostStatus, Protocol
from src.models.gateway import DiscoveredService
from src.models.state import get_state, SK
from src.core.discovery.scanner import ServiceDiscovery
from src.core.discovery.tool_manager import ToolManager, ToolStatus, NSE_PRESETS, NSE_CATEGORIES
from src.utils.formatters import fmt_ms
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole, HexView,
    neon_btn, ghost_btn, danger_btn,
)

log = logging.getLogger(__name__)


class ToolStatusWidget(QWidget):
    from PyQt6.QtCore import pyqtSignal
    install_requested = pyqtSignal(str)

    def __init__(self, tool_name: str, info, parent=None):
        super().__init__(parent)
        from PyQt6.QtCore import pyqtSignal
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(8)
        self._tool_name  = tool_name
        self._name_lbl   = QLabel(info.name)
        self._name_lbl.setFixedWidth(90)
        self._name_lbl.setStyleSheet(f"color:{COLORS['text_primary']};font-size:11px;")
        self._status_lbl = QLabel("?")
        self._status_lbl.setFixedWidth(100)
        self._status_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
        self._ver_lbl    = QLabel("")
        self._ver_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:9px;")
        self._install_btn = ghost_btn("Install")
        self._install_btn.setFixedWidth(70)
        self._install_btn.clicked.connect(lambda: self.install_requested.emit(tool_name))
        self._install_btn.setVisible(False)
        lay.addWidget(self._name_lbl)
        lay.addWidget(self._status_lbl)
        lay.addWidget(self._ver_lbl, 1)
        lay.addWidget(self._install_btn)
        self.update_info(info)

    def update_info(self, info):
        status_map = {
            ToolStatus.AVAILABLE:  (f"● AVAILABLE",  COLORS["neon_green"],  False),
            ToolStatus.MISSING:    ("● MISSING",      COLORS["neon_red"],    True),
            ToolStatus.INSTALLING: ("● INSTALLING…",  COLORS["neon_yellow"], False),
            ToolStatus.FAILED:     ("● FAILED",        COLORS["neon_red"],    True),
            ToolStatus.UNKNOWN:    ("● CHECKING…",     COLORS["text_muted"],  False),
        }
        text, color, show_install = status_map.get(info.status, ("?", COLORS["text_muted"], False))
        self._status_lbl.setText(text)
        self._status_lbl.setStyleSheet(f"color:{color};font-size:10px;font-weight:bold;")
        self._ver_lbl.setText(info.version[:60] if info.version else "")
        self._install_btn.setVisible(show_install)


class ServiceTable(QTableWidget):
    COLS = ["Host:Port", "Service", "Version", "Protocol", "Process", "AI?", "FastAPI?", "MCP?", "Latency"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for i in (4, 5, 6, 7, 8):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(22)
        self.setAlternatingRowColors(True)
        self._services: list[DiscoveredService] = []

    def add_service(self, svc: DiscoveredService):
        self._services.append(svc)
        r = self.rowCount()
        self.insertRow(r)
        addr = QTableWidgetItem(svc.address)
        addr.setForeground(QColor(COLORS["neon_blue"]))
        addr.setData(Qt.ItemDataRole.UserRole, svc)
        self.setItem(r, 0, addr)
        color = (COLORS["neon_orange"] if svc.is_ai_api
                 else COLORS["neon_purple"] if svc.is_mcp
                 else COLORS["neon_cyan"] if svc.is_fastapi
                 else COLORS["text_primary"])
        svc_item = QTableWidgetItem(svc.service or "TCP")
        svc_item.setForeground(QColor(color))
        self.setItem(r, 1, svc_item)
        self.setItem(r, 2, QTableWidgetItem(svc.version[:60]))
        self.setItem(r, 3, QTableWidgetItem(svc.protocol.value))
        proc = f"[{svc.pid}] {svc.process}" if svc.pid else svc.process
        self.setItem(r, 4, QTableWidgetItem(proc))
        def flag(val):
            item = QTableWidgetItem("✓" if val else "")
            item.setForeground(QColor(COLORS["neon_green"] if val else COLORS["text_dim"]))
            return item
        self.setItem(r, 5, flag(svc.is_ai_api))
        self.setItem(r, 6, flag(svc.is_fastapi))
        self.setItem(r, 7, flag(svc.is_mcp))
        lat = QTableWidgetItem(fmt_ms(svc.latency_ms) if svc.latency_ms else "")
        lat.setForeground(QColor(COLORS["text_muted"]))
        self.setItem(r, 8, lat)

    def clear_services(self):
        self.setRowCount(0)
        self._services.clear()

    def selected_service(self):
        row = self.currentRow()
        item = self.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def filter_by(self, text: str):
        text = text.lower()
        for row in range(self.rowCount()):
            match = any(
                text in (self.item(row, col) or QTableWidgetItem()).text().lower()
                for col in range(self.columnCount())
            )
            self.setRowHidden(row, not match)


class DiscoverPanel(QWidget):
    def __init__(self, discovery: ServiceDiscovery = None, parent=None):
        super().__init__(parent)
        self._discovery   = discovery or ServiceDiscovery()
        self._tool_mgr    = ToolManager()
        self._nmap_worker = None
        self._nse_worker  = None
        self._tool_widgets: dict = {}
        self._build()
        self._setup_subscriptions()
        QTimer.singleShot(600, self._detect_tools)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(NeonLabel("🟣  DISCOVER", COLORS["neon_purple"]))
        hl.addStretch()
        self._found_lbl = DimLabel("0 services")
        hl.addWidget(self._found_lbl)
        lay.addWidget(hdr)

        tabs = QTabWidget()
        tabs.addTab(self._build_quick_tab(),  "⚡ Quick Scan")
        tabs.addTab(self._build_nmap_tab(),   "🔍 Nmap")
        tabs.addTab(self._build_nse_tab(),    "📜 NSE Scripts")
        tabs.addTab(self._build_tools_tab(),  "🔧 Tools")
        lay.addWidget(tabs, 1)

    # ── Quick Scan tab ─────────────────────────────────────────────

    def _build_quick_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(8, 8, 8, 8)

        ctrl = QHBoxLayout()
        ctrl.addWidget(DimLabel("Target:"))
        self._q_host  = QLineEdit("127.0.0.1")
        self._q_host.setFixedWidth(150)
        ctrl.addWidget(self._q_host)
        ctrl.addWidget(DimLabel("Ports:"))
        self._q_start = QSpinBox()
        self._q_start.setRange(1, 65535); self._q_start.setValue(1)
        self._q_start.setFixedWidth(72)
        ctrl.addWidget(self._q_start)
        ctrl.addWidget(DimLabel("→"))
        self._q_end   = QSpinBox()
        self._q_end.setRange(1, 65535); self._q_end.setValue(9999)
        self._q_end.setFixedWidth(72)
        ctrl.addWidget(self._q_end)
        self._q_scan_btn = neon_btn("▶ SCAN", COLORS["neon_purple"])
        self._q_scan_btn.clicked.connect(self._start_quick_scan)
        self._q_stop_btn = danger_btn("■ STOP")
        self._q_stop_btn.clicked.connect(self._stop_quick_scan)
        self._q_stop_btn.setEnabled(False)
        ctrl.addWidget(self._q_scan_btn)
        ctrl.addWidget(self._q_stop_btn)
        self._q_progress = QProgressBar()
        self._q_progress.setFixedWidth(180)
        ctrl.addWidget(self._q_progress)
        self._q_filter = QLineEdit()
        self._q_filter.setPlaceholderText("Filter…")
        self._q_filter.setFixedWidth(160)
        self._q_filter.textChanged.connect(lambda t: self._svc_table.filter_by(t))
        ctrl.addWidget(self._q_filter)
        ctrl.addStretch()
        lay.addLayout(ctrl)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll   = QVBoxLayout(left); ll.setContentsMargins(0, 4, 0, 0)
        ll.addWidget(NeonLabel("SERVICES", COLORS["neon_purple"]))
        self._svc_table = ServiceTable()
        self._svc_table.cellClicked.connect(self._on_service_selected)
        ll.addWidget(self._svc_table, 1)

        act = QHBoxLayout()
        add_gw = neon_btn("→ Add Gateway Route", COLORS["neon_orange"])
        add_gw.clicked.connect(self._add_gateway_route)
        open_b = ghost_btn("⬡ Open in Browser")
        open_b.clicked.connect(self._open_browser)
        clr    = ghost_btn("✕ Clear")
        clr.clicked.connect(self._svc_table.clear_services)
        act.addWidget(add_gw); act.addWidget(open_b); act.addWidget(clr); act.addStretch()
        ll.addLayout(act)

        self._q_log = LogConsole(max_lines=300)
        self._q_log.setFixedHeight(90)
        ll.addWidget(self._q_log)
        splitter.addWidget(left)

        right = QWidget()
        rl    = QVBoxLayout(right); rl.setContentsMargins(8, 4, 4, 4)
        rl.addWidget(NeonLabel("DETAIL", COLORS["text_muted"]))
        self._detail_view = HexView()
        self._detail_view.setMinimumWidth(260)
        rl.addWidget(self._detail_view, 1)
        rl.addWidget(NeonLabel("ENDPOINTS", COLORS["text_muted"]))
        self._endpoints_list = QListWidget()
        self._endpoints_list.setStyleSheet(
            f"QListWidget{{background:{COLORS['bg_void']};color:{COLORS['neon_cyan']};"
            f"border:1px solid {COLORS['border']};font-size:10px;}}"
        )
        self._endpoints_list.setMaximumHeight(120)
        rl.addWidget(self._endpoints_list)
        splitter.addWidget(right)
        splitter.setSizes([720, 320])
        lay.addWidget(splitter, 1)
        return w

    # ── Nmap tab ───────────────────────────────────────────────────

    def _build_nmap_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8)

        form_w = QWidget()
        form   = QHBoxLayout(form_w)

        left_form = QVBoxLayout()
        left_form.setSpacing(6)

        r1 = QHBoxLayout()
        r1.addWidget(DimLabel("Target:")); 
        self._nm_target = QLineEdit("127.0.0.1")
        self._nm_target.setFixedWidth(180)
        r1.addWidget(self._nm_target)
        r1.addWidget(DimLabel("Ports:"))
        self._nm_ports = QLineEdit("1-1024")
        self._nm_ports.setFixedWidth(120)
        r1.addWidget(self._nm_ports)
        r1.addStretch()
        left_form.addLayout(r1)

        r2 = QHBoxLayout()
        r2.addWidget(DimLabel("Preset:"))
        self._nm_preset = QComboBox()
        self._nm_preset.setMinimumWidth(220)
        self._nm_preset.addItem("── custom args ──")
        for name in NSE_PRESETS:
            self._nm_preset.addItem(name)
        self._nm_preset.currentTextChanged.connect(self._on_nm_preset_changed)
        r2.addWidget(self._nm_preset)
        r2.addStretch()
        left_form.addLayout(r2)

        r3 = QHBoxLayout()
        r3.addWidget(DimLabel("Args:"))
        self._nm_args = QLineEdit("-sV -T4")
        self._nm_args.setMinimumWidth(320)
        r3.addWidget(self._nm_args)
        r3.addStretch()
        left_form.addLayout(r3)

        form.addLayout(left_form, 1)
        lay.addWidget(form_w)

        btn_row = QHBoxLayout()
        self._nm_scan_btn = neon_btn("▶ Run Nmap", COLORS["neon_purple"])
        self._nm_scan_btn.clicked.connect(self._start_nmap_scan)
        self._nm_stop_btn = danger_btn("■ Stop")
        self._nm_stop_btn.clicked.connect(self._stop_nmap_scan)
        self._nm_stop_btn.setEnabled(False)
        self._nm_progress = QProgressBar(); self._nm_progress.setFixedWidth(200)
        btn_row.addWidget(self._nm_scan_btn)
        btn_row.addWidget(self._nm_stop_btn)
        btn_row.addWidget(self._nm_progress)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        lay.addWidget(NeonLabel("OUTPUT", COLORS["text_muted"]))
        self._nm_output = LogConsole(max_lines=2000)
        lay.addWidget(self._nm_output, 1)
        return w

    # ── NSE tab ────────────────────────────────────────────────────

    def _build_nse_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0)

        top = QWidget()
        top.setFixedHeight(80)
        top.setStyleSheet(f"background:{COLORS['bg_panel']};")
        tl  = QHBoxLayout(top); tl.setContentsMargins(10, 8, 10, 8)
        tl.addWidget(DimLabel("Target:"))
        self._nse_target = QLineEdit("127.0.0.1")
        self._nse_target.setFixedWidth(180)
        tl.addWidget(self._nse_target)
        tl.addWidget(DimLabel("Preset:"))
        self._nse_preset_cb = QComboBox()
        self._nse_preset_cb.setMinimumWidth(240)
        for name, info in NSE_PRESETS.items():
            self._nse_preset_cb.addItem(f"{name}  —  {info['desc'][:50]}")
        tl.addWidget(self._nse_preset_cb)
        run_btn = neon_btn("▶ Run", COLORS["neon_purple"])
        run_btn.clicked.connect(self._run_nse_from_combo)
        self._nse_stop_btn = danger_btn("■ Stop")
        self._nse_stop_btn.clicked.connect(self._stop_nse)
        self._nse_stop_btn.setEnabled(False)
        tl.addWidget(run_btn); tl.addWidget(self._nse_stop_btn); tl.addStretch()
        lay.addWidget(top)

        # Scrollable preset grid — grouped by NSE_CATEGORIES
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"background:{COLORS['bg_dark']};border:none;")
        grid_w = QWidget()
        grid_l = QVBoxLayout(grid_w); grid_l.setSpacing(4); grid_l.setContentsMargins(8, 8, 8, 8)

        # Group presets by category
        from collections import defaultdict
        by_cat = defaultdict(list)
        for name, info in NSE_PRESETS.items():
            cat = info.get("category", "basics")
            by_cat[cat].append((name, info))

        for cat_key, cat_label in NSE_CATEGORIES.items():
            presets_in_cat = by_cat.get(cat_key, [])
            if not presets_in_cat:
                continue
            # Category header
            cat_hdr = QLabel(f"  {cat_label}  ({len(presets_in_cat)})")
            cat_hdr.setFixedHeight(24)
            cat_hdr.setStyleSheet(
                f"background:{COLORS['bg_void']};color:{COLORS['neon_blue']};"
                f"font-size:10px;font-weight:bold;letter-spacing:1px;padding-left:6px;"
            )
            grid_l.addWidget(cat_hdr)
            for name, info in presets_in_cat:
                row = QWidget()
                row.setFixedHeight(52)
                row.setStyleSheet(
                    f"background:{COLORS['bg_card']};border:1px solid {COLORS['border']};"
                    f"border-radius:3px;"
                )
                rl = QHBoxLayout(row); rl.setContentsMargins(10, 4, 10, 4)
                name_lbl = QLabel(name)
                name_lbl.setFixedWidth(200)
                name_lbl.setStyleSheet(f"color:{COLORS['neon_blue']};font-size:11px;font-weight:bold;")
                desc_lbl = QLabel(info["desc"])
                desc_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
                args_lbl = QLabel(info["args"][:60])
                args_lbl.setStyleSheet(f"color:{COLORS['text_dim']};font-size:9px;font-family:monospace;")
                run_this = neon_btn("▶ Run", COLORS["neon_purple"])
                run_this.setFixedWidth(70)
                run_this.clicked.connect(lambda _, n=name: self._run_named_preset(n))
                col = QVBoxLayout()
                col.addWidget(desc_lbl)
                col.addWidget(args_lbl)
                rl.addWidget(name_lbl)
                rl.addLayout(col, 1)
                rl.addWidget(run_this)
                grid_l.addWidget(row)

        scroll.setWidget(grid_w)
        lay.addWidget(scroll, 1)

        lay.addWidget(NeonLabel("NSE OUTPUT", COLORS["text_muted"]))
        self._nse_output = LogConsole(max_lines=3000)
        self._nse_output.setFixedHeight(180)
        lay.addWidget(self._nse_output)
        return w

    # ── Tools tab ──────────────────────────────────────────────────

    def _build_tools_tab(self):
        w   = QWidget()
        lay = QVBoxLayout(w); lay.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(NeonLabel("EXTERNAL TOOLS", COLORS["neon_purple"]))
        lay.addWidget(DimLabel(
            "SOVEREIGN detects and installs external network tools automatically.\n"
            "nmap is required for NSE presets. Others enhance discovery."
        ))
        detect_btn = neon_btn("↻ Detect All", COLORS["neon_blue"])
        detect_btn.clicked.connect(self._detect_tools)
        lay.addWidget(detect_btn)
        self._tools_log = LogConsole(max_lines=200)
        self._tools_log.setMaximumHeight(100)
        lay.addWidget(self._tools_log)
        lay.addWidget(NeonLabel("TOOL STATUS", COLORS["text_muted"]))

        from src.core.discovery.tool_manager import _build_catalogue
        for name, info in _build_catalogue().items():
            tw = ToolStatusWidget(name, info)
            tw.install_requested.connect(self._install_tool)
            self._tool_widgets[name] = tw
            lay.addWidget(tw)

        lay.addStretch()
        return w

    # ── Quick scan ─────────────────────────────────────────────────

    def _start_quick_scan(self):
        host  = self._q_host.text().strip() or "127.0.0.1"
        start = self._q_start.value()
        end   = self._q_end.value()
        if start > end:
            self._q_log.log("Start port must be ≤ end port", "WARN")
            return
        self._svc_table.clear_services()
        self._q_progress.setValue(0)
        self._q_scan_btn.setEnabled(False)
        self._q_stop_btn.setEnabled(True)
        self._q_log.log(f"Scanning {host}:{start}-{end}…", "INFO")
        self._discovery.scan(host, start, end)

    def _stop_quick_scan(self):
        self._discovery.stop_scan()
        self._q_scan_btn.setEnabled(True)
        self._q_stop_btn.setEnabled(False)

    # ── Nmap ──────────────────────────────────────────────────────

    def _on_nm_preset_changed(self, name: str):
        preset = NSE_PRESETS.get(name)
        if preset:
            self._nm_args.setText(preset["args"])
            self._nm_args.setEnabled(False)
        else:
            self._nm_args.setEnabled(True)

    def _start_nmap_scan(self):
        if not self._tool_mgr.is_available("nmap"):
            QMessageBox.warning(self, "nmap Missing",
                "nmap not installed.\nGo to Tools tab and click Install.")
            return
        target = self._nm_target.text().strip()
        ports  = self._nm_ports.text().strip() or "1-1024"
        args   = self._nm_args.text().strip() or "-sV -T4"
        if not target:
            return
        from src.core.discovery.nmap_scanner import NmapScanWorker
        self._nmap_worker = NmapScanWorker(target, ports, args)
        self._nmap_worker.result_ready.connect(self._on_nmap_result)
        self._nmap_worker.service_found.connect(self._on_nmap_service)
        self._nmap_worker.log_line.connect(lambda l: self._nm_output.log(l, "INFO"))
        self._nmap_worker.progress.connect(self._nm_progress.setValue)
        self._nmap_worker.error_occurred.connect(lambda e: self._nm_output.log(f"❌ {e}", "ERROR"))
        self._nmap_worker.scan_finished.connect(self._on_nmap_done)
        self._nmap_worker.start()
        self._nm_scan_btn.setEnabled(False)
        self._nm_stop_btn.setEnabled(True)
        self._nm_output.log(f"nmap {args} -p {ports} {target}", "INFO")

    def _stop_nmap_scan(self):
        if self._nmap_worker:
            self._nmap_worker.stop()
        self._nm_scan_btn.setEnabled(True)
        self._nm_stop_btn.setEnabled(False)

    def _on_nmap_result(self, result):
        """Handle NmapResult object — log OS and script info."""
        if result.os_guess:
            self._nm_output.log(
                f"  OS: {result.os_guess} ({result.os_accuracy}%)", "INFO"
            )
        if result.scripts:
            for script_name, output in list(result.scripts.items())[:3]:
                self._nm_output.log(f"  [{script_name}] {output[:100]}", "DATA")

    def _on_nmap_service(self, svc: DiscoveredService):
        self._svc_table.add_service(svc)
        self._discovery.add_manual(svc)
        self._found_lbl.setText(f"{self._svc_table.rowCount()} services")

    def _on_nmap_done(self, services: list):
        self._nm_scan_btn.setEnabled(True)
        self._nm_stop_btn.setEnabled(False)
        self._nm_progress.setValue(100)
        self._nm_output.log(f"✅ Done — {len(services)} open ports", "OK")

    # ── NSE ───────────────────────────────────────────────────────

    def _run_nse_from_combo(self):
        text = self._nse_preset_cb.currentText()
        name = text.split("  —  ")[0].strip()
        self._run_named_preset(name)

    def _run_named_preset(self, name: str):
        if not self._tool_mgr.is_available("nmap"):
            QMessageBox.warning(self, "nmap Missing",
                "nmap not installed.\nGo to Tools tab and click Install.")
            return
        preset = NSE_PRESETS.get(name)
        if not preset:
            return
        target = self._nse_target.text().strip() or "127.0.0.1"
        args   = preset["args"]
        ports  = "1-65535"
        m = re.search(r'-p\s+(\S+)', args)
        if m:
            ports = m.group(1)
            args  = args[:m.start()].strip() + " " + args[m.end():].strip()
        from src.core.discovery.nmap_scanner import NmapScanWorker
        self._nse_worker = NmapScanWorker(target, ports, args)
        self._nse_worker.service_found.connect(self._on_nmap_service)
        self._nse_worker.log_line.connect(lambda l: self._nse_output.log(l, "DATA"))
        self._nse_worker.error_occurred.connect(lambda e: self._nse_output.log(f"❌ {e}", "ERROR"))
        self._nse_worker.scan_finished.connect(
            lambda s: self._nse_output.log(f"✅ {name} — {len(s)} services", "OK")
        )
        self._nse_worker.start()
        self._nse_stop_btn.setEnabled(True)
        self._nse_output.log(f"▶ {name}: nmap {args} -p {ports} {target}", "INFO")

    def _stop_nse(self):
        if self._nse_worker:
            self._nse_worker.stop()
        self._nse_stop_btn.setEnabled(False)

    # ── Tools ─────────────────────────────────────────────────────

    def _detect_tools(self):
        self._tools_log.log("Detecting installed tools…", "INFO")
        import threading
        def _run():
            tools = self._tool_mgr.detect_all()
            for name, info in tools.items():
                tw = self._tool_widgets.get(name)
                if tw:
                    tw.update_info(info)
                level = "OK" if info.is_available() else "WARN"
                self._tools_log.log(
                    f"  {info.name:12} {info.status.value.upper():12} {info.version[:40]}",
                    level
                )
        threading.Thread(target=_run, daemon=True).start()

    def _install_tool(self, name: str):
        self._tools_log.log(f"Installing {name}…", "INFO")
        self._tool_mgr.install(name)

    # ── Service selection ─────────────────────────────────────────

    def _on_service_selected(self, row: int, col: int):
        svc = self._svc_table.selected_service()
        if not svc:
            return
        detail = (
            f"Address:  {svc.address}\n"
            f"Service:  {svc.service or 'TCP'}\n"
            f"Version:  {svc.version or '?'}\n"
            f"Protocol: {svc.protocol.value}\n"
            f"PID:      {svc.pid or '?'}\n"
            f"Process:  {svc.process or '?'}\n"
            f"AI API:   {'Yes' if svc.is_ai_api else 'No'}\n"
            f"FastAPI:  {'Yes' if svc.is_fastapi else 'No'}\n"
            f"MCP:      {'Yes' if svc.is_mcp else 'No'}\n"
            f"Latency:  {fmt_ms(svc.latency_ms) if svc.latency_ms else '?'}\n"
            f"Base URL: {svc.base_url}\n"
        )
        if svc.notes:
            detail += f"Notes:    {svc.notes}\n"
        self._detail_view.set_text(detail)
        self._endpoints_list.clear()
        for ep in svc.endpoints[:80]:
            item = QListWidgetItem(ep)
            item.setForeground(QColor(COLORS["neon_cyan"]))
            self._endpoints_list.addItem(item)

    def _add_gateway_route(self):
        svc = self._svc_table.selected_service()
        if not svc:
            return
        from src.models.gateway import GatewayRoute
        from src.constants import ModelProvider
        route = GatewayRoute(
            name="Auto → " + svc.address,
            source_host="",
            target_url=svc.base_url,
            target_provider=ModelProvider.CUSTOM,
            enabled=True,
        )
        get_state().set("gateway.save_route", route)
        self._q_log.log(f"Gateway route created for {svc.address}", "OK")

    def _open_browser(self):
        svc = self._svc_table.selected_service()
        if svc:
            webbrowser.open(svc.base_url)

    # ── Subscriptions ─────────────────────────────────────────────

    def _setup_subscriptions(self):
        self._discovery.service_found.connect(self._on_quick_service_found)
        self._discovery.scan_started.connect(self._on_scan_started)
        self._discovery.scan_finished.connect(self._on_quick_scan_done)
        self._discovery.progress.connect(self._q_progress.setValue)
        self._tool_mgr.tool_status_changed.connect(self._on_tool_status_changed)
        self._tool_mgr.install_log.connect(
            lambda n, m: self._tools_log.log(f"[{n}] {m}", "INFO")
        )
        self._tool_mgr.install_finished.connect(self._on_install_finished)

    def _on_quick_service_found(self, svc: DiscoveredService):
        self._svc_table.add_service(svc)
        tags = []
        if svc.is_ai_api:  tags.append("AI")
        if svc.is_fastapi: tags.append("FastAPI")
        if svc.is_mcp:     tags.append("MCP")
        tag = f"  [{' '.join(tags)}]" if tags else ""
        self._q_log.log(f"Found: {svc.address:22} {svc.service}{tag}", "OK")
        self._found_lbl.setText(f"{self._svc_table.rowCount()} services")

    def _on_scan_started(self):
        self._q_scan_btn.setEnabled(False)
        self._q_stop_btn.setEnabled(True)
        self._q_progress.setValue(0)

    def _on_quick_scan_done(self, services: list):
        self._q_scan_btn.setEnabled(True)
        self._q_stop_btn.setEnabled(False)
        self._q_progress.setValue(100)
        self._q_log.log(f"✅ Done — {len(services)} services", "OK")

    def _on_tool_status_changed(self, name: str, status):
        tw   = self._tool_widgets.get(name)
        info = self._tool_mgr.get_tool(name)
        if tw and info:
            tw.update_info(info)

    def _on_install_finished(self, name: str, success: bool, message: str):
        sym   = "✅" if success else "❌"
        level = "OK" if success else "ERROR"
        self._tools_log.log(f"{sym} {name}: {message}", level)
        if success:
            self._detect_tools()
