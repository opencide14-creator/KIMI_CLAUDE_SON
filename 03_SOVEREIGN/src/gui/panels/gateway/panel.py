"""Gateway Panel — AI model router: configure routes, manage models, live stats."""
from __future__ import annotations
import logging
import os

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QSplitter,
    QLabel, QLineEdit, QComboBox, QCheckBox,
    QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QFormLayout, QGroupBox, QSpinBox,
    QMessageBox,
)
from PyQt6.QtGui import QColor

from src.constants import (
    COLORS, ModelProvider, PROVIDER_URLS,
    GATEWAY_MODEL_PRESETS, DEFAULT_GATEWAY_PORT,
)
from src.models.gateway import GatewayRoute, GatewayModel
from src.models.state import get_state, SK
from src.core.gateway.router import GatewayRouter
from src.core.vault.store import VaultStore
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole, ServiceStatusRow,
    neon_btn, ghost_btn, danger_btn, success_btn,
)

log = logging.getLogger(__name__)


class RouteTable(QTableWidget):
    COLS = ["Name", "Source Host", "Target URL", "Model", "Enabled", "Requests", "Errors"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        for i in (4, 5, 6):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self._routes: list[GatewayRoute] = []

    def load_routes(self, routes: list[GatewayRoute]):
        self._routes = routes
        self.setRowCount(0)
        for r_obj in routes:
            r = self.rowCount()
            self.insertRow(r)
            self.setItem(r, 0, QTableWidgetItem(r_obj.name or r_obj.id))
            self.setItem(r, 1, QTableWidgetItem(r_obj.source_host))
            self.setItem(r, 2, QTableWidgetItem(r_obj.target_url))
            self.setItem(r, 3, QTableWidgetItem(r_obj.target_model or r_obj.target_provider.value))
            en_item = QTableWidgetItem("✓" if r_obj.enabled else "✗")
            en_item.setForeground(QColor(COLORS["neon_green"] if r_obj.enabled else COLORS["text_muted"]))
            self.setItem(r, 4, en_item)
            self.setItem(r, 5, QTableWidgetItem(str(r_obj.request_count)))
            err_item = QTableWidgetItem(str(r_obj.error_count))
            if r_obj.error_count > 0:
                err_item.setForeground(QColor(COLORS["neon_red"]))
            self.setItem(r, 6, err_item)

    def selected_route(self) -> GatewayRoute | None:
        row = self.currentRow()
        if 0 <= row < len(self._routes):
            return self._routes[row]
        return None


class RouteForm(QWidget):
    """Form to add or edit a gateway routing rule."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build()
        self._current_route: GatewayRoute | None = None

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(8)

        lay.addWidget(NeonLabel("ROUTE CONFIGURATION", COLORS["neon_orange"]))

        form = QFormLayout()
        form.setSpacing(6)

        self._name_in = QLineEdit()
        self._name_in.setPlaceholderText("e.g. Claude → Kimi")
        form.addRow("Route Name:", self._name_in)

        self._source_in = QLineEdit()
        self._source_in.setPlaceholderText("api.anthropic.com")
        form.addRow("Intercept Host:", self._source_in)

        self._path_in = QLineEdit()
        self._path_in.setPlaceholderText("/v1/messages  (empty = all paths)")
        form.addRow("Path Prefix:", self._path_in)

        self._target_in = QLineEdit()
        self._target_in.setPlaceholderText("http://127.0.0.1:4000")
        form.addRow("Forward To:", self._target_in)

        self._provider_cb = QComboBox()
        for p in ModelProvider:
            self._provider_cb.addItem(p.value, p)
        self._provider_cb.currentIndexChanged.connect(self._on_provider_change)
        form.addRow("Backend Provider:", self._provider_cb)

        self._model_cb = QComboBox()
        self._model_cb.setEditable(True)
        form.addRow("Target Model:", self._model_cb)

        self._inject_key_in = QLineEdit()
        self._inject_key_in.setPlaceholderText("API key to inject (overrides original auth)")
        self._inject_key_in.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Inject API Key:", self._inject_key_in)

        self._strip_auth_cb = QCheckBox("Strip original Authorization header")
        form.addRow("", self._strip_auth_cb)

        self._enabled_cb = QCheckBox("Route enabled")
        self._enabled_cb.setChecked(True)
        form.addRow("", self._enabled_cb)

        lay.addLayout(form)

        btn_row = QHBoxLayout()
        self._save_btn  = neon_btn("💾 Save Route", COLORS["neon_blue"])
        self._clear_btn = ghost_btn("✕ Clear")
        self._save_btn.clicked.connect(self._emit_save)
        self._clear_btn.clicked.connect(self.clear)
        btn_row.addWidget(self._save_btn)
        btn_row.addWidget(self._clear_btn)
        btn_row.addStretch()
        lay.addLayout(btn_row)
        lay.addStretch()

        self._on_provider_change(0)

    def _on_provider_change(self, idx: int):
        provider = self._provider_cb.currentData()
        if not provider:
            return
        self._model_cb.clear()
        for m in GATEWAY_MODEL_PRESETS.get(provider, []):
            self._model_cb.addItem(m)
        url = PROVIDER_URLS.get(provider, "")
        if url and not self._target_in.text():
            self._target_in.setText(f"http://127.0.0.1:{DEFAULT_GATEWAY_PORT}")

    def load_route(self, route: GatewayRoute):
        self._current_route = route
        self._name_in.setText(route.name)
        self._source_in.setText(route.source_host)
        self._path_in.setText(route.source_path_prefix)
        self._target_in.setText(route.target_url)
        # Set provider
        for i in range(self._provider_cb.count()):
            if self._provider_cb.itemData(i) == route.target_provider:
                self._provider_cb.setCurrentIndex(i)
                break
        self._model_cb.setCurrentText(route.target_model)
        self._inject_key_in.setText(route.inject_key)
        self._strip_auth_cb.setChecked(route.strip_auth)
        self._enabled_cb.setChecked(route.enabled)

    def clear(self):
        self._current_route = None
        self._name_in.clear()
        self._source_in.clear()
        self._path_in.clear()
        self._target_in.clear()
        self._inject_key_in.clear()
        self._strip_auth_cb.setChecked(False)
        self._enabled_cb.setChecked(True)

    def build_route(self) -> GatewayRoute | None:
        name   = self._name_in.text().strip()
        source = self._source_in.text().strip()
        target = self._target_in.text().strip()
        if not source or not target:
            return None
        provider = self._provider_cb.currentData() or ModelProvider.CUSTOM
        route = GatewayRoute(
            id              = self._current_route.id if self._current_route else None,
            name            = name or f"{source} → {target}",
            source_host     = source,
            source_path_prefix = self._path_in.text().strip(),
            target_url      = target,
            target_provider = provider,
            target_model    = self._model_cb.currentText().strip(),
            inject_key      = self._inject_key_in.text().strip(),
            strip_auth      = self._strip_auth_cb.isChecked(),
            enabled         = self._enabled_cb.isChecked(),
        )
        if not route.id:
            import uuid
            route.id = str(uuid.uuid4())[:8]
        return route

    def _emit_save(self):
        route = self.build_route()
        if route:
            get_state().set("gateway.save_route", route)
        else:
            log.warning("Route form: source host and target URL are required")


class GatewayPanel(QWidget):
    """Full AI gateway management panel."""

    def __init__(self, gateway: GatewayRouter = None, parent=None):
        super().__init__(parent)
        self._gateway = gateway
        self._build()
        self._setup_subscriptions()

        # Refresh stats every 2s
        self._stats_timer = QTimer(self)
        self._stats_timer.timeout.connect(self._refresh_stats)
        self._stats_timer.start(2000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(NeonLabel("🟢  AI GATEWAY", COLORS["neon_orange"]))
        hl.addStretch()

        # Service controls
        self._svc_row = ServiceStatusRow("gateway")
        self._svc_row.toggle_requested.connect(self._on_service_toggle)
        hl.addWidget(self._svc_row)

        # Port display
        self._port_lbl = DimLabel(f"port {DEFAULT_GATEWAY_PORT}")
        hl.addWidget(self._port_lbl)
        lay.addWidget(hdr)

        # Stats strip
        stats = QWidget()
        stats.setFixedHeight(36)
        stats.setStyleSheet(f"background:{COLORS['bg_void']};border-bottom:1px solid {COLORS['border']};")
        sl = QHBoxLayout(stats)
        sl.setContentsMargins(12, 4, 12, 4)
        sl.setSpacing(24)
        self._req_count_lbl  = QLabel("0  requests")
        self._route_count_lbl= QLabel("0  routes")
        self._model_count_lbl= QLabel("0  models")
        self._err_count_lbl  = QLabel("0  errors")
        for lbl in (self._req_count_lbl, self._route_count_lbl,
                    self._model_count_lbl, self._err_count_lbl):
            lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:11px;")
            sl.addWidget(lbl)
        sl.addStretch()
        lay.addWidget(stats)

        tabs = QTabWidget()

        # ── Tab 1: Routes ──────────────────────────────────────────
        routes_w = QWidget()
        rl = QHBoxLayout(routes_w)
        rl.setContentsMargins(8, 8, 8, 8)

        left = QWidget()
        ll   = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.addWidget(NeonLabel("ACTIVE ROUTES", COLORS["neon_orange"]))

        self._route_table = RouteTable()
        self._route_table.cellClicked.connect(self._on_route_selected)
        ll.addWidget(self._route_table, 1)

        r_btn_row = QHBoxLayout()
        self._delete_route_btn = danger_btn("✕ Delete")
        self._delete_route_btn.clicked.connect(self._delete_route)
        self._toggle_route_btn = ghost_btn("⏸ Toggle")
        self._toggle_route_btn.clicked.connect(self._toggle_route)
        self._new_route_btn    = neon_btn("＋ New", COLORS["neon_green"])
        r_btn_row.addWidget(self._new_route_btn)
        r_btn_row.addWidget(self._toggle_route_btn)
        r_btn_row.addWidget(self._delete_route_btn)
        r_btn_row.addStretch()
        ll.addLayout(r_btn_row)

        self._gw_log = LogConsole()
        self._gw_log.setFixedHeight(90)
        ll.addWidget(self._gw_log)
        rl.addWidget(left, 1)

        self._form = RouteForm()
        self._new_route_btn.clicked.connect(self._form.clear)
        rl.addWidget(self._form)
        tabs.addTab(routes_w, "⇄ Routes")

        # ── Tab 2: Models ──────────────────────────────────────────
        models_w = QWidget()
        ml = QVBoxLayout(models_w)
        ml.setContentsMargins(8, 8, 8, 8)
        ml.addWidget(NeonLabel("MODEL ALIASES", COLORS["neon_blue"]))
        ml.addWidget(DimLabel("Map incoming model names to real backend models"))

        self._model_table = QTableWidget(0, 5)
        self._model_table.setHorizontalHeaderLabels(["Alias", "Provider", "Real Model", "Base URL", "Enabled"])
        h = self._model_table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self._model_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._model_table.setShowGrid(False)
        self._model_table.verticalHeader().setVisible(False)
        ml.addWidget(self._model_table, 1)

        m_form = QFormLayout()
        self._model_alias_in   = QLineEdit()
        self._model_alias_in.setPlaceholderText("claude-sonnet-4-5")
        m_form.addRow("Alias (incoming):", self._model_alias_in)
        self._model_provider_cb = QComboBox()
        for p in ModelProvider:
            self._model_provider_cb.addItem(p.value, p)
        self._model_provider_cb.currentIndexChanged.connect(self._on_model_provider_change)
        m_form.addRow("Provider:", self._model_provider_cb)
        self._model_real_cb = QComboBox()
        self._model_real_cb.setEditable(True)
        m_form.addRow("Real Model:", self._model_real_cb)
        self._model_url_in = QLineEdit()
        self._model_url_in.setPlaceholderText("Leave blank to use provider default")
        m_form.addRow("Base URL:", self._model_url_in)
        ml.addLayout(m_form)

        m_btn_row = QHBoxLayout()
        add_model_btn = neon_btn("＋ Add Model", COLORS["neon_blue"])
        add_model_btn.clicked.connect(self._add_model)
        del_model_btn = danger_btn("✕ Remove")
        del_model_btn.clicked.connect(self._remove_model)
        m_btn_row.addWidget(add_model_btn)
        m_btn_row.addWidget(del_model_btn)
        m_btn_row.addStretch()
        ml.addLayout(m_btn_row)
        tabs.addTab(models_w, "🤖 Models")

        # ── Tab 3: Test ────────────────────────────────────────────
        test_w = QWidget()
        tl = QVBoxLayout(test_w)
        tl.setContentsMargins(8, 8, 8, 8)
        tl.addWidget(NeonLabel("GATEWAY TEST", COLORS["neon_cyan"]))
        tl.addWidget(DimLabel("Send a test request through the gateway"))

        tf = QFormLayout()
        self._test_model_in = QLineEdit("claude-sonnet-4-5")
        tf.addRow("Model:", self._test_model_in)
        self._test_prompt_in = QLineEdit("Say SOVEREIGN in one word")
        tf.addRow("Prompt:", self._test_prompt_in)
        tl.addLayout(tf)

        test_btn = neon_btn("▶ Send Test Request", COLORS["neon_cyan"])
        test_btn.clicked.connect(self._run_test)
        tl.addWidget(test_btn)

        self._test_log = LogConsole()
        tl.addWidget(self._test_log, 1)
        tabs.addTab(test_w, "🔬 Test")

        lay.addWidget(tabs, 1)

    def _setup_subscriptions(self):
        get_state().subscribe("gateway.save_route", self._on_save_route)
        get_state().subscribe("wizard.route", self._on_wizard_route)
        if self._gateway:
            self._gateway.status_changed.connect(
                lambda s: self._svc_row.update_status(s)
            )
            self._gateway.request_routed.connect(self._on_request_routed)
            self._gateway.error_occurred.connect(
                lambda e: self._gw_log.log(f"Error: {e}", "ERROR")
            )

    def _on_service_toggle(self, name: str, start: bool):
        if not self._gateway:
            return
        if start:
            self._gateway.start()
            port = self._gateway.port
            self._port_lbl.setText(f"port {port}")
            self._gw_log.log(f"Gateway starting on port {port}…", "INFO")
        else:
            self._gateway.stop()
            self._gw_log.log("Gateway stopped", "WARN")

    def _on_save_route(self, route: GatewayRoute):
        if self._gateway:
            self._gateway.add_route(route)
        routes = get_state().get(SK.GATEWAY_ROUTES, [])
        routes = [r for r in routes if r.id != route.id]
        routes.append(route)
        get_state().set(SK.GATEWAY_ROUTES, routes)
        self._route_table.load_routes(routes)
        self._gw_log.log(f"Route saved: {route.name}", "OK")
        self._form.clear()

    def _on_wizard_route(self, route: GatewayRoute):
        if self._gateway:
            self._gateway.add_route(route)
        routes = get_state().get(SK.GATEWAY_ROUTES, [])
        routes.append(route)
        get_state().set(SK.GATEWAY_ROUTES, routes)
        self._route_table.load_routes(routes)
        self._gw_log.log(f"Wizard route added: {route.name}", "OK")

    def _on_route_selected(self, row: int, col: int):
        route = self._route_table.selected_route()
        if route:
            self._form.load_route(route)

    def _delete_route(self):
        route = self._route_table.selected_route()
        if not route:
            return
        if self._gateway:
            self._gateway.remove_route(route.id)
        routes = [r for r in get_state().get(SK.GATEWAY_ROUTES, []) if r.id != route.id]
        get_state().set(SK.GATEWAY_ROUTES, routes)
        self._route_table.load_routes(routes)
        self._gw_log.log(f"Route deleted: {route.name}", "WARN")

    def _toggle_route(self):
        route = self._route_table.selected_route()
        if not route:
            return
        route.enabled = not route.enabled
        if self._gateway:
            self._gateway.remove_route(route.id)
            if route.enabled:
                self._gateway.add_route(route)
        routes = get_state().get(SK.GATEWAY_ROUTES, [])
        get_state().set(SK.GATEWAY_ROUTES, routes)
        self._route_table.load_routes(routes)

    def _on_request_routed(self, route_name: str, src_model: str, tgt_model: str):
        self._gw_log.log(f"→ {src_model}  ⇒  {tgt_model}  [{route_name}]", "ROUTE")
        routes = get_state().get(SK.GATEWAY_ROUTES, [])
        self._route_table.load_routes(routes)

    def _on_model_provider_change(self, idx: int):
        provider = self._model_provider_cb.currentData()
        if not provider:
            return
        self._model_real_cb.clear()
        for m in GATEWAY_MODEL_PRESETS.get(provider, []):
            self._model_real_cb.addItem(m)
        url = PROVIDER_URLS.get(provider, "")
        self._model_url_in.setText(url)

    def _add_model(self):
        alias    = self._model_alias_in.text().strip()
        provider = self._model_provider_cb.currentData() or ModelProvider.CUSTOM
        real_m   = self._model_real_cb.currentText().strip()
        base_url = self._model_url_in.text().strip()
        if not alias or not real_m:
            self._gw_log.log("Alias and real model name required", "WARN")
            return
        model = GatewayModel(
            alias=alias, provider=provider, real_model=real_m,
            base_url=base_url or PROVIDER_URLS.get(provider, ""),
        )
        if self._gateway:
            self._gateway.add_model(model)
        r = self._model_table.rowCount()
        self._model_table.insertRow(r)
        self._model_table.setItem(r, 0, QTableWidgetItem(alias))
        self._model_table.setItem(r, 1, QTableWidgetItem(provider.value))
        self._model_table.setItem(r, 2, QTableWidgetItem(real_m))
        self._model_table.setItem(r, 3, QTableWidgetItem(base_url))
        en = QTableWidgetItem("✓")
        en.setForeground(QColor(COLORS["neon_green"]))
        self._model_table.setItem(r, 4, en)
        self._gw_log.log(f"Model added: {alias} → {real_m}", "OK")

    def _remove_model(self):
        row = self._model_table.currentRow()
        if row >= 0:
            alias = self._model_table.item(row, 0)
            if alias:
                models = get_state().get(SK.GATEWAY_MODELS, [])
                models = [m for m in models if m.alias != alias.text()]
                get_state().set(SK.GATEWAY_MODELS, models)
            self._model_table.removeRow(row)

    def _run_test(self):
        import threading, httpx, json
        model  = self._test_model_in.text().strip()
        prompt = self._test_prompt_in.text().strip()
        port   = self._gateway.port if self._gateway else DEFAULT_GATEWAY_PORT
        self._test_log.log(f"Testing: model={model}, prompt={prompt!r}", "INFO")

        def _send():
            try:
                resp = httpx.post(
                    f"http://127.0.0.1:{port}/v1/messages",
                    json={"model": model, "max_tokens": 100,
                          "messages": [{"role": "user", "content": prompt}]},
                    timeout=30,
                )
                self._test_log.log(f"Status: {resp.status_code}", "OK")
                try:
                    data = resp.json()
                    content = data.get("content", [{}])
                    text = content[0].get("text", "") if content else str(data)[:200]
                    self._test_log.log(f"Response: {text}", "DATA")
                except Exception:
                    self._test_log.log(f"Raw: {resp.text[:300]}", "DATA")
            except httpx.ConnectError:
                self._test_log.log("Connection refused — is the gateway running?", "ERROR")
            except Exception as e:
                self._test_log.log(f"Error: {e}", "ERROR")

        threading.Thread(target=_send, daemon=True).start()

    def set_focus_host(self, host: str):
        """Pre-fill the source host field in the route form (called when intercept panel triggers add_route_host)."""
        if hasattr(self, '_form'):
            self._form._source_in.setText(host)
            self._form._source_in.setFocus()

    def _refresh_stats(self):
        routes  = get_state().get(SK.GATEWAY_ROUTES, [])
        models  = get_state().get(SK.GATEWAY_MODELS, [])
        req_cnt = get_state().get(SK.GATEWAY_REQUEST_COUNT, 0)
        err_cnt = sum(r.error_count for r in routes)
        self._req_count_lbl.setText(f"{req_cnt:,}  requests")
        self._route_count_lbl.setText(f"{len(routes)}  routes")
        self._model_count_lbl.setText(f"{len(models)}  models")
        color = COLORS["neon_red"] if err_cnt else COLORS["text_muted"]
        self._err_count_lbl.setText(f"{err_cnt}  errors")
        self._err_count_lbl.setStyleSheet(f"color:{color};font-size:11px;")
