"""
Settings Panel — Ultra Orchestrator GUI

Configuration panel for API keys, orchestrator parameters, quality gate toggles,
and display preferences.  Emits a consolidated settings dict when saved.
"""

import os
import sys

import yaml
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QCheckBox,
    QComboBox,
    QPushButton,
    QGroupBox,
    QSizePolicy,
    QPlainTextEdit,
)


# ---------------------------------------------------------------------------
# Dark theme colours
# ---------------------------------------------------------------------------
BG_MAIN = "#1e1e1e"
BG_CARD = "#2d2d2d"
BG_PANEL = "#252525"
TEXT_PRIMARY = "#e0e0e0"
TEXT_SECONDARY = "#a0a0a0"
BORDER = "#3d3d3d"
ACCENT = "#0d7377"
SUCCESS = "#4CAF50"
ERROR = "#F44336"
WARNING = "#FFC107"


# ---------------------------------------------------------------------------
# Config file path (works in dev and PyInstaller bundle)
# ---------------------------------------------------------------------------
def _get_config_path() -> str:
    """Resolve the config YAML path.

    In a normal Python environment the file lives beside the source tree.
    Inside a PyInstaller onefile bundle it is extracted to a temp folder
    pointed to by ``sys._MEIPASS``.
    """
    if hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(base, "config", "default_config.yaml")


class SettingsPanel(QWidget):
    """Panel for configuring API keys, orchestrator parameters, and display options."""

    # ── Signals ──────────────────────────────────────────────────────────────
    settings_saved = pyqtSignal(dict)
    """Emitted when the user clicks the SAVE SETTINGS button.

    Parameter:
        settings – dict containing all configured values.
    """

    # ── Construction ─────────────────────────────────────────────────────────
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._key_inputs: list[QLineEdit] = []
        self._key_test_btns: list[QPushButton] = []
        self._key_clear_btns: list[QPushButton] = []
        self._setup_ui()
        self._apply_styles()
        self._load_from_config()

    # ── UI setup ─────────────────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        self.setObjectName("settingsPanel")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(16)

        # ---- API Keys section -----------------------------------------------
        api_keys_section = self._create_api_keys_section()
        main_layout.addWidget(api_keys_section)

        # ---- Orchestrator Parameters section --------------------------------
        params_section = self._create_params_section()
        main_layout.addWidget(params_section)

        # ---- Quality Gate section -------------------------------------------
        qg_section = self._create_qg_section()
        main_layout.addWidget(qg_section)

        # ---- Sandbox Patterns section ---------------------------------------
        sandbox_section = self._create_sandbox_section()
        main_layout.addWidget(sandbox_section)

        # ---- Banned Patterns section ----------------------------------------
        banned_section = self._create_banned_section()
        main_layout.addWidget(banned_section)

        # ---- Display section ------------------------------------------------
        display_section = self._create_display_section()
        main_layout.addWidget(display_section)

        main_layout.addStretch()

        # ---- Save button ----------------------------------------------------
        self._save_btn = QPushButton("SAVE SETTINGS")
        self._save_btn.setObjectName("saveButton")
        self._save_btn.setMinimumHeight(44)
        self._save_btn.clicked.connect(self._on_save)
        main_layout.addWidget(self._save_btn)

    # ── Section builders ─────────────────────────────────────────────────────
    def _create_api_keys_section(self) -> QGroupBox:
        """Build the API Keys input section.

        Returns:
            A QGroupBox containing 4 rows of key inputs with Test/Clear buttons.
        """
        group = QGroupBox("API KEYS")
        group.setObjectName("apiKeysGroup")

        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        for i in range(4):
            row_layout = QHBoxLayout()
            row_layout.setSpacing(10)

            name_label = QLabel(f"Key {i + 1}")
            name_label.setFixedWidth(50)
            row_layout.addWidget(name_label)

            key_input = QLineEdit()
            key_input.setEchoMode(QLineEdit.EchoMode.Password)
            key_input.setPlaceholderText("sk-...")
            key_input.setObjectName(f"keyInput{i + 1}")
            self._key_inputs.append(key_input)
            row_layout.addWidget(key_input, stretch=2)

            test_btn = QPushButton("Test")
            test_btn.setObjectName(f"keyTestBtn{i + 1}")
            test_btn.setFixedWidth(60)
            idx = i  # capture for closure
            test_btn.clicked.connect(lambda checked=False, x=idx: self._test_key(x))
            self._key_test_btns.append(test_btn)
            row_layout.addWidget(test_btn)

            clear_btn = QPushButton("Clear")
            clear_btn.setObjectName(f"keyClearBtn{i + 1}")
            clear_btn.setFixedWidth(60)
            clear_btn.clicked.connect(
                lambda checked=False, x=idx: self._key_inputs[x].clear()
            )
            self._key_clear_btns.append(clear_btn)
            row_layout.addWidget(clear_btn)

            layout.addLayout(row_layout)

        return group

    def _create_params_section(self) -> QGroupBox:
        """Build the Orchestrator Parameters section.

        Returns:
            A QGroupBox with max concurrent, safety margin, retries, timeout,
            and sandbox toggle.
        """
        group = QGroupBox("Orchestrator Parameters")
        group.setObjectName("paramsGroup")

        layout = QGridLayout(group)
        layout.setSpacing(12)

        # Row 0: Max concurrent
        layout.addWidget(QLabel("Max concurrent"), 0, 0)
        self._max_concurrent_spin = QSpinBox()
        self._max_concurrent_spin.setRange(1, 20)
        self._max_concurrent_spin.setValue(20)
        self._max_concurrent_spin.setObjectName("maxConcurrentSpin")
        layout.addWidget(self._max_concurrent_spin, 0, 1)

        # Row 1: API safety margin %
        layout.addWidget(QLabel("API safety margin %"), 1, 0)
        self._safety_margin_spin = QSpinBox()
        self._safety_margin_spin.setRange(50, 100)
        self._safety_margin_spin.setValue(80)
        self._safety_margin_spin.setSuffix("%")
        self._safety_margin_spin.setObjectName("safetyMarginSpin")
        layout.addWidget(self._safety_margin_spin, 1, 1)

        # Row 2: Max retries
        layout.addWidget(QLabel("Max retries"), 2, 0)
        self._max_retries_spin = QSpinBox()
        self._max_retries_spin.setRange(1, 10)
        self._max_retries_spin.setValue(3)
        self._max_retries_spin.setObjectName("maxRetriesSpin")
        layout.addWidget(self._max_retries_spin, 2, 1)

        # Row 3: Task timeout
        layout.addWidget(QLabel("Task timeout (sec)"), 3, 0)
        self._timeout_spin = QSpinBox()
        self._timeout_spin.setRange(30, 600)
        self._timeout_spin.setValue(120)
        self._timeout_spin.setSingleStep(10)
        self._timeout_spin.setObjectName("timeoutSpin")
        layout.addWidget(self._timeout_spin, 3, 1)

        # Row 4: Sandbox execution
        layout.addWidget(QLabel("Sandbox execution"), 4, 0)
        self._sandbox_check = QCheckBox()
        self._sandbox_check.setChecked(True)
        self._sandbox_check.setObjectName("sandboxCheck")
        layout.addWidget(self._sandbox_check, 4, 1)

        layout.setColumnStretch(1, 1)
        return group

    def _create_qg_section(self) -> QGroupBox:
        """Build the Quality Gate section with layer toggles.

        Returns:
            A QGroupBox with 5 quality gate layer checkboxes.
        """
        group = QGroupBox("Quality Gate")
        group.setObjectName("qgGroup")

        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        # Layer 0: Existence (always on, disabled)
        self._qg_layer0 = QCheckBox("Layer 0 Existence")
        self._qg_layer0.setChecked(True)
        self._qg_layer0.setEnabled(False)
        self._qg_layer0.setObjectName("qgLayer0")
        layout.addWidget(self._qg_layer0)

        # Layer 1: Anti-Smell (always on, disabled)
        self._qg_layer1 = QCheckBox("Layer 1 Anti-Smell")
        self._qg_layer1.setChecked(True)
        self._qg_layer1.setEnabled(False)
        self._qg_layer1.setObjectName("qgLayer1")
        layout.addWidget(self._qg_layer1)

        # Layer 2: Criteria
        self._qg_layer2 = QCheckBox("Layer 2 Criteria")
        self._qg_layer2.setChecked(True)
        self._qg_layer2.setObjectName("qgLayer2")
        layout.addWidget(self._qg_layer2)

        # Layer 3: Sandbox
        self._qg_layer3 = QCheckBox("Layer 3 Sandbox")
        self._qg_layer3.setChecked(True)
        self._qg_layer3.setObjectName("qgLayer3")
        layout.addWidget(self._qg_layer3)

        # Layer 4: Dedup
        self._qg_layer4 = QCheckBox("Layer 4 Dedup")
        self._qg_layer4.setChecked(True)
        self._qg_layer4.setObjectName("qgLayer4")
        layout.addWidget(self._qg_layer4)

        return group

    def _create_display_section(self) -> QGroupBox:
        """Build the Display settings section.

        Returns:
            A QGroupBox with reasoning panel toggle and log level selector.
        """
        group = QGroupBox("Display")
        group.setObjectName("displayGroup")

        layout = QGridLayout(group)
        layout.setSpacing(12)

        # Row 0: Show reasoning panel
        self._show_reasoning_check = QCheckBox("Show reasoning panel")
        self._show_reasoning_check.setChecked(False)
        self._show_reasoning_check.setObjectName("showReasoningCheck")
        layout.addWidget(self._show_reasoning_check, 0, 0, 1, 2)

        # Row 1: Log level
        layout.addWidget(QLabel("Log level"), 1, 0)
        self._log_level_combo = QComboBox()
        self._log_level_combo.addItems(["DEBUG", "INFO", "WARNING", "ERROR"])
        self._log_level_combo.setCurrentText("INFO")
        self._log_level_combo.setObjectName("logLevelCombo")
        layout.addWidget(self._log_level_combo, 1, 1)

        layout.setColumnStretch(1, 1)
        return group

    def _create_sandbox_section(self) -> QGroupBox:
        """Build the Sandbox Security Patterns section.

        Returns:
            A QGroupBox with a plain-text edit for blocked regex patterns.
        """
        group = QGroupBox("Sandbox Security Patterns")
        group.setObjectName("sandboxGroup")

        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        info = QLabel("Each line:  regex | description  (e.g. os\\.system\\s*\\(| os.system)")
        info.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(info)

        self._sandbox_text = QPlainTextEdit()
        self._sandbox_text.setObjectName("sandboxText")
        self._sandbox_text.setPlaceholderText("regex | description")
        self._sandbox_text.setMinimumHeight(120)
        layout.addWidget(self._sandbox_text)

        return group

    def _create_banned_section(self) -> QGroupBox:
        """Build the Quality Gate Banned Patterns section.

        Returns:
            A QGroupBox with a plain-text edit for banned regex patterns.
        """
        group = QGroupBox("Quality Gate Banned Patterns")
        group.setObjectName("bannedGroup")

        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        info = QLabel("Each line:  regex | description  (e.g. pass\\s*#\\s*TODO| Placeholder comment)")
        info.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px;")
        layout.addWidget(info)

        self._banned_text = QPlainTextEdit()
        self._banned_text.setObjectName("bannedText")
        self._banned_text.setPlaceholderText("regex | description")
        self._banned_text.setMinimumHeight(120)
        layout.addWidget(self._banned_text)

        return group

    # ── Styles ───────────────────────────────────────────────────────────────
    def _apply_styles(self) -> None:
        self.setStyleSheet(f"""
            QWidget#settingsPanel {{
                background-color: {BG_PANEL};
                border: 1px solid {BORDER};
                border-radius: 8px;
            }}
            QGroupBox {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
                font-weight: bold;
                border: 1px solid {BORDER};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 12px;
                padding-left: 10px;
                padding-right: 10px;
                padding-bottom: 10px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 8px;
                padding: 0 4px;
            }}
            QLabel {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
            }}
            QLineEdit {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 13px;
            }}
            QLineEdit:focus {{
                border: 1px solid {ACCENT};
            }}
            QSpinBox {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
            }}
            QSpinBox:focus {{
                border: 1px solid {ACCENT};
            }}
            QCheckBox {{
                color: {TEXT_PRIMARY};
                font-size: 13px;
            }}
            QCheckBox::indicator {{
                width: 16px;
                height: 16px;
                border: 1px solid {BORDER};
                border-radius: 3px;
                background-color: {BG_MAIN};
            }}
            QCheckBox::indicator:checked {{
                background-color: {ACCENT};
                border: 1px solid {ACCENT};
            }}
            QCheckBox::indicator:disabled {{
                background-color: #444444;
                border: 1px solid #555555;
            }}
            QComboBox {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                selection-background-color: {ACCENT};
            }}
            QPushButton {{
                background-color: {BG_CARD};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 5px 10px;
                font-size: 12px;
            }}
            QPushButton:hover {{
                background-color: {BORDER};
            }}
            QPushButton:pressed {{
                background-color: {ACCENT};
            }}
            QPushButton#saveButton {{
                background-color: {ACCENT};
                color: #ffffff;
                border: none;
                border-radius: 8px;
                font-size: 15px;
                font-weight: bold;
                padding: 12px;
            }}
            QPushButton#saveButton:hover {{
                background-color: #0e8a8f;
            }}
            QPushButton#saveButton:pressed {{
                background-color: #0a5c5f;
            }}
            QPlainTextEdit {{
                background-color: {BG_MAIN};
                color: {TEXT_PRIMARY};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 6px;
                font-size: 12px;
                font-family: Consolas, monospace;
            }}
        """)

    # ── Config persistence ───────────────────────────────────────────────────
    def _load_from_config(self) -> None:
        """Populate the UI from ``default_config.yaml`` if it exists."""
        try:
            with open(_get_config_path(), "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh)
        except Exception:
            cfg = {}  # gracefully fall back to hardcoded UI defaults

        if not cfg:
            return

        # --- Hard limits / orchestrator params -------------------------------
        hl = cfg.get("hard_limits", {})
        if "max_concurrent" in hl:
            self._max_concurrent_spin.setValue(hl["max_concurrent"])
        if "api_max_retries" in hl:
            self._max_retries_spin.setValue(hl["api_max_retries"])
        # task_timeout can live at root or under hard_limits
        timeout = hl.get("task_timeout_seconds") or cfg.get("task_timeout_seconds")
        if timeout is not None:
            self._timeout_spin.setValue(int(timeout))
        # safety margin lives at root (float 0-1) or hard_limits
        margin = hl.get("safety_margin") or cfg.get("safety_margin")
        if margin is not None:
            self._safety_margin_spin.setValue(int(float(margin) * 100))

        # --- API keys (resolve env-var placeholders like ${KIMI_KEY_1}) --------
        keys = cfg.get("kimi_api_keys", [])
        for i, kdef in enumerate(keys):
            if i >= len(self._key_inputs):
                break
            raw = kdef.get("key", "")
            if not raw:
                continue
            # Resolve ${ENV_VAR} syntax from environment
            if raw.startswith("${") and raw.endswith("}"):
                env_name = raw[2:-1]
                raw = os.environ.get(env_name, "")
            if raw:
                self._key_inputs[i].setText(raw)

        # --- Quality gate layers ---------------------------------------------
        qg = cfg.get("quality_gate", {})
        if "layer_2_criteria" in qg:
            self._qg_layer2.setChecked(qg["layer_2_criteria"])
        if "layer_3_sandbox" in qg:
            self._qg_layer3.setChecked(qg["layer_3_sandbox"])
        if "layer_4_dedup" in qg:
            self._qg_layer4.setChecked(qg["layer_4_dedup"])

        # --- Display ----------------------------------------------------------
        disp = cfg.get("display", {})
        if "show_reasoning_panel" in disp:
            self._show_reasoning_check.setChecked(disp["show_reasoning_panel"])
        if "log_level" in disp:
            level = disp["log_level"]
            idx = self._log_level_combo.findText(level)
            if idx >= 0:
                self._log_level_combo.setCurrentIndex(idx)

        # --- Sandbox patterns -------------------------------------------------
        sp = cfg.get("sandbox_patterns", [])
        lines: list[str] = []
        for entry in sp:
            if isinstance(entry, dict):
                pat = entry.get("pattern", "")
                desc = entry.get("description", "")
                if pat:
                    lines.append(f"{pat} | {desc}")
            elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
                lines.append(f"{entry[0]} | {entry[1]}")
        if lines:
            self._sandbox_text.setPlainText("\n".join(lines))

        # --- Banned patterns --------------------------------------------------
        bp = cfg.get("banned_patterns", {})
        if isinstance(bp, dict):
            b_lines = [f"{pat} | {desc}" for pat, desc in bp.items()]
            if b_lines:
                self._banned_text.setPlainText("\n".join(b_lines))

    def _write_config(self, settings: dict) -> None:
        """Persist *settings* back to ``default_config.yaml`` atomically."""
        # Read existing config so we merge rather than clobber everything
        try:
            with open(_get_config_path(), "r", encoding="utf-8") as fh:
                cfg = yaml.safe_load(fh) or {}
        except Exception:
            cfg = {}

        # Merge top-level scalar settings
        for key in ("safety_margin", "max_retries", "task_timeout_seconds",
                    "checkpoint_interval", "global_concurrent_limit",
                    "batch_size_max", "batch_size_min"):
            if key in settings:
                cfg[key] = settings[key]

        # Merge hard_limits
        hl = settings.get("hard_limits", {})
        if hl:
            cfg.setdefault("hard_limits", {})
            cfg["hard_limits"].update(hl)

        # Merge quality_gate
        qg = settings.get("quality_gate", {})
        if qg:
            cfg.setdefault("quality_gate", {})
            cfg["quality_gate"].update(qg)

        # Merge display
        disp = settings.get("display", {})
        if disp:
            cfg.setdefault("display", {})
            cfg["display"].update(disp)

        # Merge sandbox patterns
        sp = settings.get("sandbox_patterns")
        if sp is not None:
            cfg["sandbox_patterns"] = sp

        # Merge banned patterns
        bp = settings.get("banned_patterns")
        if bp is not None:
            cfg["banned_patterns"] = bp

        # Atomic write
        tmp_path = _get_config_path() + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                yaml.safe_dump(cfg, fh, default_flow_style=False, sort_keys=False, allow_unicode=True)
            os.replace(tmp_path, _get_config_path())
        except Exception as exc:
            print(f"[SettingsPanel] Config write failed: {exc}")

    # ── Slots ────────────────────────────────────────────────────────────────
    def _on_save(self) -> None:
        """Collect all current settings values, persist to YAML, and emit signal."""
        # Parse pattern text areas
        sandbox_lines = self._sandbox_text.toPlainText().strip().splitlines()
        sandbox_patterns: list[dict] = []
        for line in sandbox_lines:
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                pat, desc = line.rsplit("|", 1)
                sandbox_patterns.append({"pattern": pat.strip(), "description": desc.strip()})
            else:
                sandbox_patterns.append({"pattern": line, "description": "custom"})

        banned_lines = self._banned_text.toPlainText().strip().splitlines()
        banned_patterns: dict[str, str] = {}
        for line in banned_lines:
            line = line.strip()
            if not line:
                continue
            if "|" in line:
                pat, desc = line.rsplit("|", 1)
                banned_patterns[pat.strip()] = desc.strip()
            else:
                banned_patterns[line] = "custom"

        settings: dict = {
            "api_keys": [inp.text() for inp in self._key_inputs],
            "max_concurrent": self._max_concurrent_spin.value(),
            "api_safety_margin_percent": self._safety_margin_spin.value(),
            "safety_margin": self._safety_margin_spin.value() / 100.0,
            "max_retries": self._max_retries_spin.value(),
            "task_timeout_seconds": self._timeout_spin.value(),
            "sandbox_execution": self._sandbox_check.isChecked(),
            "quality_gate": {
                "layer_0_existence": True,  # always on
                "layer_1_anti_smell": True,  # always on
                "layer_2_criteria": self._qg_layer2.isChecked(),
                "layer_3_sandbox": self._qg_layer3.isChecked(),
                "layer_4_dedup": self._qg_layer4.isChecked(),
            },
            "display": {
                "show_reasoning_panel": self._show_reasoning_check.isChecked(),
                "log_level": self._log_level_combo.currentText(),
            },
            "hard_limits": {
                "max_concurrent": self._max_concurrent_spin.value(),
                "api_max_retries": self._max_retries_spin.value(),
                "task_timeout_seconds": self._timeout_spin.value(),
                "safety_margin": self._safety_margin_spin.value() / 100.0,
            },
            "sandbox_patterns": sandbox_patterns,
            "banned_patterns": banned_patterns,
        }

        self._write_config(settings)
        self.settings_saved.emit(settings)

    def _test_key(self, key_index: int) -> None:
        """Placeholder for an API key connectivity test.

        Currently just prints a message.  In production this would make a
        lightweight API call to validate the key.

        Args:
            key_index: Zero-based index of the key to test.
        """
        key_text = self._key_inputs[key_index].text()
        masked = key_text[:8] + "..." if len(key_text) > 8 else "(empty)"
        print(f"[SettingsPanel] Test key {key_index + 1}: {masked} — placeholder (no-op)")

    # ── Public API ───────────────────────────────────────────────────────────
    def load_settings(self, settings: dict) -> None:
        """Populate the UI from a previously saved settings dictionary.

        Args:
            settings: Dict in the same shape as emitted by _on_save.
        """
        # API keys
        api_keys = settings.get("api_keys", [])
        for i, key in enumerate(api_keys):
            if i < len(self._key_inputs):
                self._key_inputs[i].setText(key)

        # Orchestrator parameters
        if "max_concurrent" in settings:
            self._max_concurrent_spin.setValue(settings["max_concurrent"])
        if "api_safety_margin_percent" in settings:
            self._safety_margin_spin.setValue(settings["api_safety_margin_percent"])
        if "max_retries" in settings:
            self._max_retries_spin.setValue(settings["max_retries"])
        if "task_timeout_seconds" in settings:
            self._timeout_spin.setValue(settings["task_timeout_seconds"])
        if "sandbox_execution" in settings:
            self._sandbox_check.setChecked(settings["sandbox_execution"])

        # Quality gate
        qg = settings.get("quality_gate", {})
        if "layer_2_criteria" in qg:
            self._qg_layer2.setChecked(qg["layer_2_criteria"])
        if "layer_3_sandbox" in qg:
            self._qg_layer3.setChecked(qg["layer_3_sandbox"])
        if "layer_4_dedup" in qg:
            self._qg_layer4.setChecked(qg["layer_4_dedup"])

        # Display
        display = settings.get("display", {})
        if "show_reasoning_panel" in display:
            self._show_reasoning_check.setChecked(display["show_reasoning_panel"])
        if "log_level" in display:
            level = display["log_level"]
            idx = self._log_level_combo.findText(level)
            if idx >= 0:
                self._log_level_combo.setCurrentIndex(idx)
