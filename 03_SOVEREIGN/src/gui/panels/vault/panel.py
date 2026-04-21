"""Vault Panel — encrypted API key and credential management."""
from __future__ import annotations
import logging
import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QLineEdit, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QFormLayout, QInputDialog, QMessageBox,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, ModelProvider
from src.core.vault.store import VaultStore
from src.models.gateway import VaultEntry
from src.models.state import get_state, SK
from src.gui.widgets.common import (
    NeonLabel, DimLabel, LogConsole,
    neon_btn, ghost_btn, danger_btn, success_btn,
)

log = logging.getLogger(__name__)


class VaultTable(QTableWidget):
    COLS = ["Name", "Provider", "Type", "Env Var", "Value (masked)", "Last Used"]

    def __init__(self, parent=None):
        super().__init__(0, len(self.COLS), parent)
        self.setHorizontalHeaderLabels(self.COLS)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.setShowGrid(False)
        self.verticalHeader().setVisible(False)
        self._entries: list[VaultEntry] = []

    def load_entries(self, entries: list[VaultEntry]):
        self._entries = entries
        self.setRowCount(0)
        for e in entries:
            r = self.rowCount()
            self.insertRow(r)
            name_item = QTableWidgetItem(e.name)
            name_item.setForeground(QColor(COLORS["neon_blue"]))
            self.setItem(r, 0, name_item)
            self.setItem(r, 1, QTableWidgetItem(e.provider))
            self.setItem(r, 2, QTableWidgetItem(e.key_type))
            env_item = QTableWidgetItem(e.env_var)
            env_item.setForeground(QColor(COLORS["text_muted"]))
            self.setItem(r, 3, env_item)
            masked_item = QTableWidgetItem(e.masked())
            masked_item.setForeground(QColor(COLORS["neon_green"]))
            self.setItem(r, 4, masked_item)
            last = e.last_used.strftime("%Y-%m-%d %H:%M") if e.last_used else "Never"
            self.setItem(r, 5, QTableWidgetItem(last))

    def selected_entry(self) -> VaultEntry | None:
        row = self.currentRow()
        if 0 <= row < len(self._entries):
            return self._entries[row]
        return None


class VaultPanel(QWidget):
    """Encrypted credential vault — store, retrieve, import API keys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._unlocked = False
        self._build()
        # Auto-unlock with machine key on startup
        self._auto_unlock()

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(10, 6, 10, 6)
        hl.addWidget(NeonLabel("⚪  VAULT", COLORS["text_muted"]))
        hl.addStretch()
        self._lock_status = DimLabel("🔒 Locked")
        hl.addWidget(self._lock_status)
        self._unlock_btn = neon_btn("🔓 Unlock", COLORS["neon_blue"])
        self._unlock_btn.clicked.connect(self._unlock)
        self._lock_btn   = ghost_btn("🔒 Lock")
        self._lock_btn.clicked.connect(self._lock)
        self._lock_btn.setEnabled(False)
        hl.addWidget(self._unlock_btn)
        hl.addWidget(self._lock_btn)
        lay.addWidget(hdr)

        # Locked overlay
        self._locked_overlay = QWidget()
        ol = QVBoxLayout(self._locked_overlay)
        ol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lock_lbl = QLabel("🔒")
        lock_lbl.setStyleSheet("font-size:48px;")
        lock_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info_lbl = QLabel("Vault is locked.\nClick Unlock to access credentials.")
        info_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:13px;")
        info_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ol.addWidget(lock_lbl)
        ol.addWidget(info_lbl)
        quick_unlock = neon_btn("🔓 Unlock Vault", COLORS["neon_blue"])
        quick_unlock.clicked.connect(self._unlock)
        ol.addWidget(quick_unlock, alignment=Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(self._locked_overlay)

        # Vault content (hidden when locked)
        self._vault_content = QWidget()
        self._vault_content.setVisible(False)
        cl = QVBoxLayout(self._vault_content)
        cl.setContentsMargins(8, 8, 8, 8)

        # Entries table
        cl.addWidget(NeonLabel("STORED CREDENTIALS", COLORS["neon_blue"]))
        self._table = VaultTable()
        self._table.cellClicked.connect(self._on_entry_selected)
        cl.addWidget(self._table, 1)

        # Action buttons
        btn_row = QHBoxLayout()
        self._import_env_btn = neon_btn("↓ Import from ENV", COLORS["neon_green"])
        self._import_env_btn.clicked.connect(self._import_from_env)
        self._delete_btn     = danger_btn("✕ Delete")
        self._delete_btn.clicked.connect(self._delete_entry)
        self._reveal_btn     = ghost_btn("👁 Reveal")
        self._reveal_btn.clicked.connect(self._reveal_entry)
        self._copy_btn       = ghost_btn("📋 Copy")
        self._copy_btn.clicked.connect(self._copy_to_clipboard)
        btn_row.addWidget(self._import_env_btn)
        btn_row.addWidget(self._reveal_btn)
        btn_row.addWidget(self._copy_btn)
        btn_row.addWidget(self._delete_btn)
        btn_row.addStretch()
        cl.addLayout(btn_row)

        # Add new credential form
        cl.addWidget(NeonLabel("ADD CREDENTIAL", COLORS["text_muted"]))
        form = QFormLayout()
        form.setSpacing(6)
        self._name_in = QLineEdit()
        self._name_in.setPlaceholderText("e.g. Kimi API Key")
        form.addRow("Name:", self._name_in)
        self._value_in = QLineEdit()
        self._value_in.setPlaceholderText("sk-kimi-…")
        self._value_in.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Value:", self._value_in)
        self._provider_cb = QComboBox()
        self._provider_cb.addItem("", "")
        for p in ModelProvider:
            self._provider_cb.addItem(p.value, p.value)
        form.addRow("Provider:", self._provider_cb)
        self._env_var_in = QLineEdit()
        self._env_var_in.setPlaceholderText("KIMI_API_KEY  (optional)")
        form.addRow("Env Var:", self._env_var_in)
        cl.addLayout(form)

        add_row = QHBoxLayout()
        add_btn = success_btn("＋ Save Credential")
        add_btn.clicked.connect(self._add_entry)
        add_row.addWidget(add_btn)
        add_row.addStretch()
        cl.addLayout(add_row)

        self._vault_log = LogConsole()
        self._vault_log.setFixedHeight(70)
        cl.addWidget(self._vault_log)

        # S-20: Show vault file path so user can find/backup it
        from src.constants import VAULT_FILE
        path_lbl = QLabel(f"🔒 Vault file: {VAULT_FILE}")
        path_lbl.setStyleSheet(
            f"color:{COLORS['text_dim']};font-size:9px;padding:4px 8px;"            f"border-top:1px solid {COLORS['border']};"
        )
        path_lbl.setTextInteractionFlags(
            path_lbl.textInteractionFlags() |
            __import__('PyQt6.QtCore', fromlist=['Qt']).Qt.TextInteractionFlag.TextSelectableByMouse
        )
        cl.addWidget(path_lbl)

        lay.addWidget(self._vault_content)

    # ── Vault lifecycle ────────────────────────────────────────────

    def _auto_unlock(self):
        """Unlock with machine-unique key (no passphrase — convenient mode)."""
        ok = VaultStore.unlock("")
        if ok:
            self._set_unlocked(True)
            count = len(VaultStore.list_entries())
            self._vault_log.log(f"Vault auto-unlocked ({count} entries)", "OK")

    def _unlock(self):
        passphrase, ok = QInputDialog.getText(
            self, "Unlock Vault",
            "Enter passphrase (leave empty for machine-key mode):",
            QLineEdit.EchoMode.Password
        )
        if not ok:
            return
        success = VaultStore.unlock(passphrase)
        if success:
            self._set_unlocked(True)
            self._vault_log.log("Vault unlocked", "OK")
            self._refresh_entries()
        else:
            self._vault_log.log("Wrong passphrase or corrupted vault", "ERROR")
            QMessageBox.warning(self, "Vault", "Incorrect passphrase")

    def _lock(self):
        VaultStore.lock()
        self._set_unlocked(False)
        self._table.setRowCount(0)
        self._vault_log.log("Vault locked", "WARN")

    def _set_unlocked(self, unlocked: bool):
        self._unlocked = unlocked
        self._locked_overlay.setVisible(not unlocked)
        self._vault_content.setVisible(unlocked)
        self._unlock_btn.setEnabled(not unlocked)
        self._lock_btn.setEnabled(unlocked)
        status = "🔓 Unlocked" if unlocked else "🔒 Locked"
        color  = COLORS["neon_green"] if unlocked else COLORS["text_muted"]
        self._lock_status.setText(status)
        self._lock_status.setStyleSheet(f"color:{color};font-size:11px;")
        get_state().set(SK.VAULT_UNLOCKED, unlocked)

    # ── CRUD ──────────────────────────────────────────────────────

    def _add_entry(self):
        name  = self._name_in.text().strip()
        value = self._value_in.text().strip()
        if not name or not value:
            self._vault_log.log("Name and value are required", "WARN")
            return
        provider = self._provider_cb.currentData() or ""
        env_var  = self._env_var_in.text().strip()
        try:
            VaultStore.set(name, value, provider=provider, env_var=env_var)
            self._vault_log.log(f"Saved: {name}", "OK")
            self._name_in.clear()
            self._value_in.clear()
            self._env_var_in.clear()
            self._refresh_entries()
        except RuntimeError as e:
            self._vault_log.log(str(e), "ERROR")

    def _delete_entry(self):
        entry = self._table.selected_entry()
        if not entry:
            return
        reply = QMessageBox.question(
            self, "Delete Credential", f"Delete '{entry.name}'?"
        )
        if reply == QMessageBox.StandardButton.Yes:
            VaultStore.delete(entry.name)
            self._vault_log.log(f"Deleted: {entry.name}", "WARN")
            self._refresh_entries()

    def _reveal_entry(self):
        entry = self._table.selected_entry()
        if not entry:
            return
        value = VaultStore.get_key(entry.name) or ""
        QMessageBox.information(self, f"Credential: {entry.name}",
                                f"Name: {entry.name}\nValue: {value}\n\nClose when done.")

    def _copy_to_clipboard(self):
        entry = self._table.selected_entry()
        if not entry:
            return
        value = VaultStore.get_key(entry.name) or ""
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(value)
        self._vault_log.log(f"Copied {entry.name} to clipboard", "OK")

    def _import_from_env(self):
        try:
            count = VaultStore.import_from_env()
            self._vault_log.log(f"Imported {count} keys from environment", "OK")
            self._refresh_entries()
        except RuntimeError as e:
            self._vault_log.log(str(e), "ERROR")

    def _on_entry_selected(self, row: int, col: int):
        entry = self._table.selected_entry()
        if entry:
            get_state().set("vault.selected", entry)

    def _refresh_entries(self):
        entries = VaultStore.list_entries()
        self._table.load_entries(entries)
        get_state().set(SK.VAULT_ENTRIES, entries)
