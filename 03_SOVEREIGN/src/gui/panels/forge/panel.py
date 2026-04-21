"""Forge Panel — Certificate Authority, cert generation, system trust, hosts file.

The complete Claude Desktop → Kimi redirect wizard lives here.
"""
from __future__ import annotations
import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QMetaObject, Q_ARG
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QLineEdit, QPushButton, QTabWidget,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QCheckBox, QGroupBox, QFormLayout, QTextEdit,
    QMessageBox, QInputDialog, QComboBox,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, APP_DIR, KNOWN_AI_HOSTS, PROVIDER_URLS, ModelProvider
from src.core.cert.authority import CertificateAuthority
from src.core.cert.hosts import HostsManager
from src.models.gateway import CertRecord, HostsEntry
from src.models.state import get_state, SK
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole, HexView,
    neon_btn, ghost_btn, danger_btn, success_btn,
)

log = logging.getLogger(__name__)


class CertWorker(QThread):
    done    = pyqtSignal(object, str)   # CertRecord or None, message
    error   = pyqtSignal(str)

    def __init__(self, ca: CertificateAuthority, mode: str, domains: list = None):
        super().__init__()
        self._ca      = ca
        self._mode    = mode   # "generate_ca" | "sign" | "install" | "remove"
        self._domains = domains or []

    def run(self):
        try:
            if self._mode == "generate_ca":
                record = self._ca.generate_ca()
                self.done.emit(record, "Root CA generated successfully")
            elif self._mode == "sign":
                record = self._ca.generate_server_cert(self._domains)
                self.done.emit(record, f"Certificate signed for {', '.join(self._domains)}")
            elif self._mode == "install":
                ok, msg = self._ca.install_ca_system()
                self.done.emit(None, msg if ok else f"Install failed: {msg}")
            elif self._mode == "remove":
                ok, msg = self._ca.remove_ca_system()
                self.done.emit(None, msg if ok else f"Remove failed: {msg}")
        except Exception as e:
            self.error.emit(str(e))


class CertListTable(QTableWidget):
    COLS = ["Common Name", "Type", "Domains", "Expires", "Fingerprint"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

    def load_certs(self, records: list[CertRecord]):
        self.setRowCount(0)
        for rec in records:
            r = self.rowCount()
            self.insertRow(r)
            self.setItem(r, 0, QTableWidgetItem(rec.name))
            type_text = "CA" if rec.is_ca else "Server"
            type_item = QTableWidgetItem(type_text)
            type_item.setForeground(QColor(
                COLORS["neon_purple"] if rec.is_ca else COLORS["neon_blue"]
            ))
            self.setItem(r, 1, type_item)
            self.setItem(r, 2, QTableWidgetItem(", ".join(rec.domains[:3])))
            exp = rec.expires_at.strftime("%Y-%m-%d") if rec.expires_at else "?"
            exp_item = QTableWidgetItem(exp)
            if rec.is_expired:
                exp_item.setForeground(QColor(COLORS["neon_red"]))
            self.setItem(r, 3, exp_item)
            self.setItem(r, 4, QTableWidgetItem(rec.fingerprint))


class HostsTable(QTableWidget):
    COLS = ["IP", "Host", "Managed", "Active"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in (2, 3):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)

    def load_entries(self, entries: list[HostsEntry]):
        self.setRowCount(0)
        for e in entries:
            r = self.rowCount()
            self.insertRow(r)
            self.setItem(r, 0, QTableWidgetItem(e.ip))
            h_item = QTableWidgetItem(e.host)
            h_item.setForeground(QColor(
                COLORS["neon_orange"] if e.managed else COLORS["text_muted"]
            ))
            self.setItem(r, 1, h_item)
            m_item = QTableWidgetItem("✓" if e.managed else "")
            m_item.setForeground(QColor(COLORS["neon_green"] if e.managed else COLORS["text_muted"]))
            self.setItem(r, 2, m_item)
            a_item = QTableWidgetItem("✓" if e.active else "✗")
            a_item.setForeground(QColor(COLORS["neon_green"] if e.active else COLORS["neon_red"]))
            self.setItem(r, 3, a_item)
        get_state().set(SK.HOSTS_ENTRIES, entries)


class ForgePanel(QWidget):
    """Certificate Authority + Hosts file management panel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._ca     = CertificateAuthority()
        self._hosts  = HostsManager()
        self._worker: CertWorker | None = None
        self._build()
        self._refresh_certs()
        self._refresh_hosts()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(NeonLabel("🟡  FORGE", COLORS["neon_yellow"]))
        hl.addStretch()
        hl.addWidget(DimLabel("Certificate Authority  ·  Hosts File  ·  DNS"))
        lay.addWidget(hdr)

        tabs = QTabWidget()

        # ── Tab 1: Certificate Authority ───────────────────────────
        ca_w = QWidget()
        cl   = QHBoxLayout(ca_w)
        cl.setContentsMargins(8, 8, 8, 8)

        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(NeonLabel("CERTIFICATES", COLORS["neon_purple"]))

        self._cert_table = CertListTable()
        ll.addWidget(self._cert_table, 1)

        btn_row = QHBoxLayout()
        self._gen_ca_btn   = neon_btn("⊕ New CA",     COLORS["neon_purple"])
        self._sign_btn     = neon_btn("✎ Sign Cert",  COLORS["neon_blue"])
        self._install_btn  = success_btn("↑ Trust CA")
        self._remove_tr_btn= danger_btn("↓ Untrust")
        self._refresh_cert_btn = ghost_btn("↻ Refresh")
        for b in (self._gen_ca_btn, self._sign_btn, self._install_btn,
                  self._remove_tr_btn, self._refresh_cert_btn):
            btn_row.addWidget(b)
        btn_row.addStretch()
        ll.addLayout(btn_row)

        self._gen_ca_btn.clicked.connect(self._generate_ca)
        self._sign_btn.clicked.connect(self._sign_cert)
        self._install_btn.clicked.connect(self._install_ca)
        self._remove_tr_btn.clicked.connect(self._remove_ca)
        self._refresh_cert_btn.clicked.connect(self._refresh_certs)

        self._cert_log = LogConsole()
        self._cert_log.setFixedHeight(100)
        ll.addWidget(self._cert_log)
        cl.addWidget(left, 1)

        # Right: cert details
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 0, 0, 0)
        rl.addWidget(NeonLabel("CERT DETAILS", COLORS["text_muted"]))
        self._cert_detail = HexView()
        self._cert_detail.setMinimumWidth(300)
        rl.addWidget(self._cert_detail, 1)
        self._cert_table.cellClicked.connect(self._on_cert_selected)
        cl.addWidget(right)
        tabs.addTab(ca_w, "🔐 Certificates")

        # ── Tab 2: Hosts File ───────────────────────────────────────
        hosts_w = QWidget()
        hol     = QHBoxLayout(hosts_w)
        hol.setContentsMargins(8, 8, 8, 8)

        h_left = QWidget()
        hll = QVBoxLayout(h_left)
        hll.setContentsMargins(0, 0, 0, 0)
        hll.addWidget(NeonLabel("HOSTS FILE", COLORS["neon_orange"]))
        hll.addWidget(DimLabel(str(self._hosts._path)))

        self._hosts_table = HostsTable()
        hll.addWidget(self._hosts_table, 1)

        add_row = QHBoxLayout()
        self._ip_in   = QLineEdit("127.0.0.1")
        self._ip_in.setFixedWidth(120)
        self._host_in = QLineEdit()
        self._host_in.setPlaceholderText("hostname  e.g. api.anthropic.com")
        self._add_host_btn  = neon_btn("＋ Add", COLORS["neon_green"])
        self._add_host_btn.clicked.connect(self._add_host)
        self._remove_host_btn = danger_btn("✕ Remove")
        self._remove_host_btn.clicked.connect(self._remove_host)
        self._flush_dns_btn = ghost_btn("↻ Flush DNS")
        self._flush_dns_btn.clicked.connect(self._flush_dns)
        self._restore_btn = ghost_btn("⟲ Restore Backup")
        self._restore_btn.clicked.connect(self._restore_hosts)
        for w in (self._ip_in, self._host_in, self._add_host_btn,
                  self._remove_host_btn, self._flush_dns_btn, self._restore_btn):
            add_row.addWidget(w)
        add_row.addStretch()
        hll.addLayout(add_row)

        self._hosts_log = LogConsole()
        self._hosts_log.setFixedHeight(80)
        hll.addWidget(self._hosts_log)
        hol.addWidget(h_left, 1)

        h_right = QWidget()
        hrl = QVBoxLayout(h_right)
        hrl.setContentsMargins(8, 0, 0, 0)
        hrl.addWidget(NeonLabel("RAW FILE", COLORS["text_muted"]))
        self._hosts_raw = HexView()
        self._hosts_raw.setMinimumWidth(320)
        hrl.addWidget(self._hosts_raw, 1)
        refresh_raw_btn = ghost_btn("↻ Refresh Raw")
        refresh_raw_btn.clicked.connect(self._refresh_hosts)
        hrl.addWidget(refresh_raw_btn)
        hol.addWidget(h_right)
        tabs.addTab(hosts_w, "📄 Hosts File")

        # ── Tab 3: Claude → Kimi Wizard ────────────────────────────
        wizard_w = self._build_wizard()
        tabs.addTab(wizard_w, "⚡ Claude→Kimi Wizard")

        lay.addWidget(tabs, 1)

    def _build_wizard(self) -> QWidget:
        """The one-click Claude Desktop → Kimi redirect setup wizard."""
        w   = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(12)

        lay.addWidget(NeonLabel("⚡ CLAUDE DESKTOP → KIMI REDIRECT WIZARD", COLORS["neon_orange"]))
        lay.addWidget(DimLabel(
            "Redirects Claude Desktop's API calls to Kimi K2 via local proxy.\n"
            "Generates a trusted cert for api.anthropic.com, injects hosts entry, starts the gateway."
        ))

        # Config form
        form = QFormLayout()
        self._wiz_kimi_key = QLineEdit()
        self._wiz_kimi_key.setPlaceholderText("sk-kimi-…")
        self._wiz_kimi_key.setEchoMode(QLineEdit.EchoMode.Password)
        # Pre-fill from vault/env if available
        import os
        env_key = os.environ.get("KIMI_API_KEY", "")
        if env_key:
            self._wiz_kimi_key.setText(env_key)
        form.addRow("Kimi API Key:", self._wiz_kimi_key)

        self._wiz_model = QComboBox()
        self._wiz_model.addItems(["kimi-for-coding", "kimi-k2-thinking-turbo"])
        form.addRow("Kimi Model:", self._wiz_model)

        self._wiz_proxy_port = QLineEdit("8080")
        form.addRow("Proxy Port:", self._wiz_proxy_port)
        self._wiz_gw_port    = QLineEdit("4000")
        form.addRow("Gateway Port:", self._wiz_gw_port)
        lay.addLayout(form)

        # Steps display
        steps_box = QGroupBox("SETUP STEPS")
        steps_box.setStyleSheet(
            f"QGroupBox {{color:{COLORS['text_muted']};border:1px solid {COLORS['border']};"
            f"border-radius:3px;margin-top:12px;padding-top:8px;}}"
            f"QGroupBox::title {{subcontrol-origin:margin;left:8px;color:{COLORS['text_muted']};"
            f"font-size:10px;letter-spacing:1px;}}"
        )
        sl = QVBoxLayout(steps_box)
        self._step_labels = []
        steps = [
            "Generate Root CA  (sovereign-ca.crt)",
            "Install CA in system trust store  (requires admin)",
            "Sign server cert for api.anthropic.com",
            "Add  127.0.0.1 api.anthropic.com  to hosts file  (requires admin)",
            "Flush DNS cache",
            "Configure AI Gateway route  →  Kimi",
            "Start Proxy + Gateway",
        ]
        for s in steps:
            lbl = QLabel(f"  ⬜  {s}")
            lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:11px;")
            sl.addWidget(lbl)
            self._step_labels.append(lbl)
        lay.addWidget(steps_box)

        btn_row = QHBoxLayout()
        self._wiz_run_btn   = neon_btn("⚡ RUN WIZARD", COLORS["neon_orange"])
        self._wiz_run_btn.setFixedHeight(36)
        self._wiz_run_btn.clicked.connect(self._run_wizard)
        self._wiz_undo_btn  = danger_btn("↩ UNDO ALL")
        self._wiz_undo_btn.setFixedHeight(36)
        self._wiz_undo_btn.clicked.connect(self._undo_wizard)
        btn_row.addWidget(self._wiz_run_btn)
        btn_row.addWidget(self._wiz_undo_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)

        self._wiz_log = LogConsole()
        self._wiz_log.setMaximumHeight(140)
        lay.addWidget(self._wiz_log)
        lay.addStretch()
        return w

    # ── Certificate actions ────────────────────────────────────────

    def _generate_ca(self):
        self._cert_log.log("Generating Root CA…", "INFO")
        self._run_cert_worker("generate_ca")

    def _sign_cert(self):
        text, ok = QInputDialog.getText(
            self, "Sign Certificate",
            "Enter domain(s) comma-separated:\ne.g. api.anthropic.com, www.anthropic.com"
        )
        if not ok or not text.strip():
            return
        domains = [d.strip() for d in text.split(",") if d.strip()]
        if not self._ca.ca_exists():
            self._cert_log.log("No CA found — generating one first…", "WARN")
            self._ca.generate_ca()
        self._ca.load_ca()
        self._cert_log.log(f"Signing cert for: {domains}", "INFO")
        self._run_cert_worker("sign", domains)

    def _install_ca(self):
        self._cert_log.log("Installing CA in system trust store…", "WARN")
        self._cert_log.log("This requires administrator/sudo privileges.", "WARN")
        self._run_cert_worker("install")

    def _remove_ca(self):
        self._cert_log.log("Removing CA from system trust store…", "WARN")
        self._run_cert_worker("remove")

    def _run_cert_worker(self, mode: str, domains: list = None):
        if self._worker and self._worker.isRunning():
            return
        if mode in ("install", "remove", "sign"):
            if not self._ca.load_ca() and mode != "install":
                self._cert_log.log("No CA found — generate one first", "ERROR")
                return
        self._worker = CertWorker(self._ca, mode, domains)
        self._worker.done.connect(self._on_cert_done)
        self._worker.error.connect(lambda e: self._cert_log.log(f"Error: {e}", "ERROR"))
        self._worker.start()

    def _on_cert_done(self, record: CertRecord | None, message: str):
        level = "OK" if "failed" not in message.lower() else "ERROR"
        self._cert_log.log(message, level)
        self._refresh_certs()

    def _on_cert_selected(self, row: int, col: int):
        records = self._ca.list_certs()
        if row < len(records):
            rec = records[row]
            detail = (
                f"Name:        {rec.name}\n"
                f"Domains:     {', '.join(rec.domains)}\n"
                f"Type:        {'CA' if rec.is_ca else 'Server'}\n"
                f"Cert:        {rec.cert_path}\n"
                f"Key:         {rec.key_path}\n"
                f"Created:     {rec.created_at.strftime('%Y-%m-%d')}\n"
                f"Expires:     {rec.expires_at.strftime('%Y-%m-%d') if rec.expires_at else '?'}\n"
                f"Days left:   {rec.days_remaining}\n"
                f"Fingerprint: {rec.fingerprint}\n"
            )
            self._cert_detail.set_text(detail)
            get_state().set(SK.CERT_SELECTED, rec)

    def _refresh_certs(self):
        records = self._ca.list_certs()
        self._cert_table.load_certs(records)
        get_state().set(SK.CERT_LIST, records)

    # ── Hosts file actions ─────────────────────────────────────────

    def _add_host(self):
        ip   = self._ip_in.text().strip()
        host = self._host_in.text().strip()
        if not ip or not host:
            self._hosts_log.log("IP and hostname required", "WARN")
            return
        ok, msg = self._hosts.add_entry(ip, host)
        self._hosts_log.log(msg, "OK" if ok else "ERROR")
        if ok:
            ok2, msg2 = self._hosts.flush_dns()
            self._hosts_log.log(msg2, "OK" if ok2 else "WARN")
        self._refresh_hosts()

    def _remove_host(self):
        row = self._hosts_table.currentRow()
        if row < 0:
            return
        host_item = self._hosts_table.item(row, 1)
        if not host_item:
            return
        host = host_item.text()
        ok, msg = self._hosts.remove_entry(host)
        self._hosts_log.log(msg, "OK" if ok else "ERROR")
        if ok:
            self._hosts.flush_dns()
        self._refresh_hosts()

    def _flush_dns(self):
        ok, msg = self._hosts.flush_dns()
        self._hosts_log.log(msg, "OK" if ok else "WARN")

    def _restore_hosts(self):
        ok, msg = self._hosts.restore_backup()
        self._hosts_log.log(msg, "OK" if ok else "ERROR")
        if ok:
            self._refresh_hosts()

    def _refresh_hosts(self):
        entries = self._hosts.read_all()
        self._hosts_table.load_entries(entries)
        self._hosts_raw.set_text(self._hosts.get_current_text())

    # ── Wizard ─────────────────────────────────────────────────────

    def _step_update(self, idx: int, status: str = "running"):
        """Thread-safe step update — uses invokeMethod when called from worker thread."""
        # If called from non-GUI thread, marshal to main thread
        if self.thread() != QThread.currentThread():
            QMetaObject.invokeMethod(
                self, "_step_update_sync",
                Qt.ConnectionType.QueuedConnection,
                Q_ARG(int, idx), Q_ARG(str, status)
            )
            return
        self._step_update_sync(idx, status)

    def _step_update_sync(self, idx: int, status: str = "running"):
        """Synchronous step update — MUST run on GUI thread."""
        icons = {"pending": "⬜", "running": "🟡", "ok": "✅", "error": "❌"}
        lbl = self._step_labels[idx]
        icon  = icons.get(status, "⬜")
        text  = lbl.text().split("  ", 1)[-1]
        color = {
            "running": COLORS["neon_yellow"],
            "ok":      COLORS["neon_green"],
            "error":   COLORS["neon_red"],
        }.get(status, COLORS["text_muted"])
        lbl.setText(f"  {icon}  {text}")
        lbl.setStyleSheet(f"color:{color};font-size:11px;")

    def _run_wizard(self):
        kimi_key = self._wiz_kimi_key.text().strip()
        if not kimi_key:
            self._wiz_log.log("Kimi API key required", "ERROR")
            return

        import threading
        def _wizard_thread():
            try:
                # Step 0: CA
                self._step_update(0, "running")
                self._wiz_log.log("Generating Root CA…", "INFO")
                if not self._ca.ca_exists():
                    self._ca.generate_ca()
                else:
                    self._ca.load_ca()
                self._step_update(0, "ok")
                self._wiz_log.log("CA ready", "OK")

                # Step 1: Install CA
                self._step_update(1, "running")
                ok, msg = self._ca.install_ca_system()
                self._step_update(1, "ok" if ok else "error")
                self._wiz_log.log(msg, "OK" if ok else "WARN")

                # Step 2: Sign cert for api.anthropic.com
                self._step_update(2, "running")
                self._ca.generate_server_cert(["api.anthropic.com", "www.anthropic.com"])
                self._step_update(2, "ok")
                self._wiz_log.log("Cert signed for api.anthropic.com", "OK")

                # Step 3: Hosts entry
                self._step_update(3, "running")
                ok, msg = self._hosts.add_entry("127.0.0.1", "api.anthropic.com")
                self._step_update(3, "ok" if ok else "error")
                self._wiz_log.log(msg, "OK" if ok else "ERROR")

                # Step 4: Flush DNS
                self._step_update(4, "running")
                ok, msg = self._hosts.flush_dns()
                self._step_update(4, "ok" if ok else "error")
                self._wiz_log.log(msg, "OK" if ok else "WARN")

                # Step 5: Gateway route
                self._step_update(5, "running")
                from src.models.gateway import GatewayRoute, GatewayModel
                route = GatewayRoute(
                    name="Claude → Kimi", source_host="api.anthropic.com",
                    target_url=f"http://127.0.0.1:{self._wiz_gw_port.text().strip()}",
                    target_provider=ModelProvider.KIMI,
                    inject_key=kimi_key, enabled=True,
                )
                get_state().set("wizard.route", route)
                # Also set the proxy route so MainWindow can wire it up
                gw_port = self._wiz_gw_port.text().strip()
                get_state().set("proxy.set_route", {
                    "source_host": "api.anthropic.com",
                    "target_url": f"http://127.0.0.1:{gw_port}"
                })
                self._step_update(5, "ok")
                self._wiz_log.log("Gateway route configured", "OK")

                # Step 6: Notify to start services
                self._step_update(6, "ok")
                self._wiz_log.log("✅ Wizard complete — start Proxy and Gateway from Dashboard", "OK")
                get_state().set("wizard.complete", True)

            except Exception as e:
                self._wiz_log.log(f"Wizard error: {e}", "ERROR")

        threading.Thread(target=_wizard_thread, daemon=True).start()

    def _undo_wizard(self):
        import threading
        def _undo():
            self._wiz_log.log("Removing hosts entry…", "WARN")
            ok, msg = self._hosts.remove_entry("api.anthropic.com")
            self._wiz_log.log(msg, "OK" if ok else "WARN")
            self._hosts.flush_dns()
            ok2, msg2 = self._ca.remove_ca_system()
            self._wiz_log.log(msg2, "OK" if ok2 else "WARN")
            for i in range(len(self._step_labels)):
                self._step_update(i, "pending")
            self._refresh_hosts()
            self._wiz_log.log("Undo complete", "OK")
        threading.Thread(target=_undo, daemon=True).start()
