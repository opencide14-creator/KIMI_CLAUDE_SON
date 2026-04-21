"""SOVEREIGN AI Chat — Dual Agent System (Heartbeat + Reactive).
Heartbeat guards. Reactive acts. Both share memory.
"""
from __future__ import annotations
import asyncio
import logging
import os
import threading
from typing import Optional

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout,
    QLabel, QTextEdit, QLineEdit, QScrollArea,
    QComboBox, QGroupBox,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS, APP_DIR
from src.core.vault.store import VaultStore
from src.models.state import get_state, SK
from src.gui.widgets.common import (
    NeonLabel, DimLabel, neon_btn, ghost_btn,
)
from src.gui.widgets.progress import TaskTracker

log = logging.getLogger(__name__)


class DualLoopWorker(QThread):
    """Runs DualReActLoop.run() in a QThread, emits events to UI."""
    event_ready = pyqtSignal(str, str, dict)  # kind, content, meta
    finished    = pyqtSignal()
    error       = pyqtSignal(str)

    def __init__(self, loop, user_input: str):
        super().__init__()
        self._loop  = loop
        self._input = user_input
        self._stop  = False

    def stop(self):
        self._stop = True
        self.quit()
        self.wait(2000)

    def run(self):
        async def _run():
            async for event in self._loop.run(self._input):
                if self._stop:
                    break
                self.event_ready.emit(event.kind, event.content, event.meta)
        try:
            asyncio.run(_run())
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.finished.emit()


class MessageBubble(QWidget):
    AGENT_COLORS = {
        "REACTIVE":  COLORS["neon_cyan"],
        "HEARTBEAT": COLORS["neon_purple"],
        "SYSTEM":    COLORS["text_muted"],
        "user":      COLORS["neon_blue"],
    }
    AGENT_LABELS = {
        "REACTIVE":  "⚡ REACTIVE",
        "HEARTBEAT": "💓 HEARTBEAT",
        "SYSTEM":    "◈ SYSTEM",
        "user":      "YOU",
    }

    def __init__(self, agent: str, content: str, kind: str = "text", parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self); lay.setContentsMargins(8, 3, 8, 3)
        is_user = agent == "user"
        color   = self.AGENT_COLORS.get(agent, COLORS["text_muted"])
        label   = self.AGENT_LABELS.get(agent, agent)

        col = QVBoxLayout(); col.setSpacing(1)
        lbl = QLabel(label)
        lbl.setStyleSheet(
            f"color:{color};font-size:8px;font-weight:bold;letter-spacing:1px;"
        )
        bubble = QLabel(content)
        bubble.setWordWrap(True)
        bubble.setTextFormat(Qt.TextFormat.PlainText)

        bg = (COLORS["bg_highlight"] if is_user else
              COLORS["bg_void"] if kind in ("tool_call","tool_result","verify","pulse")
              else COLORS["bg_card"])
        bubble.setStyleSheet(
            f"background:{bg};color:{color};"
            f"border-radius:4px;padding:6px 10px;"
            f"font-size:11px;"
            f"font-family:'JetBrains Mono','Consolas',monospace;"
            f"border:1px solid {COLORS['border']};"
        )
        col.addWidget(lbl); col.addWidget(bubble)
        if is_user:
            lay.addStretch(); lay.addLayout(col)
        else:
            lay.addLayout(col); lay.addStretch()


class ChatPanel(QWidget):
    _boot_result = pyqtSignal(bool, str)

    def __init__(self, proxy=None, gateway=None, discovery=None, parent=None):
        super().__init__(parent)
        self._proxy     = proxy
        self._gateway   = gateway
        self._discovery = discovery
        self._loop      = None          # DualReActLoop
        self._worker:   Optional[DualLoopWorker] = None
        self._task_id   = None
        self._build()
        self._load_api_key()
        self._boot_result.connect(self._on_boot_done)

    def _build(self):
        lay = QVBoxLayout(self); lay.setContentsMargins(0,0,0,0)

        # Header
        hdr = QWidget(); hdr.setFixedHeight(44)
        hdr.setStyleSheet(f"background:{COLORS['bg_panel']};border-bottom:1px solid {COLORS['border']};")
        hl  = QHBoxLayout(hdr); hl.setContentsMargins(10,6,10,6)
        hl.addWidget(NeonLabel("🤖  VEKIL-KAAN  ·  DUAL AGENT", COLORS["neon_cyan"]))
        hl.addStretch()
        self._status_dot = QLabel("●")
        self._status_dot.setStyleSheet(f"color:{COLORS['text_muted']};font-size:14px;")
        self._hb_lbl = DimLabel("HB: —")
        self._model_cb = QComboBox(); self._model_cb.setFixedWidth(200)
        for m in ["kimi-for-coding","kimi-k2-thinking-turbo","moonshot-v1-8k"]:
            self._model_cb.addItem(m)
        hl.addWidget(self._status_dot); hl.addWidget(self._hb_lbl)
        hl.addWidget(DimLabel("Model:")); hl.addWidget(self._model_cb)
        new_btn = ghost_btn("＋ New"); new_btn.clicked.connect(self._new_conv)
        hl.addWidget(new_btn)
        lay.addWidget(hdr)

        # API key strip
        ks = QWidget(); ks.setFixedHeight(34)
        ks.setStyleSheet(f"background:{COLORS['bg_void']};border-bottom:1px solid {COLORS['border']};")
        kl = QHBoxLayout(ks); kl.setContentsMargins(10,4,10,4)
        kl.addWidget(DimLabel("Kimi API Key:"))
        self._key_in = QLineEdit()
        self._key_in.setPlaceholderText("sk-kimi-…  (or KIMI_API_KEY env var)")
        self._key_in.setEchoMode(QLineEdit.EchoMode.Password)
        self._key_in.setFixedWidth(360)
        save_btn = ghost_btn("💾 Save"); save_btn.clicked.connect(self._save_key)
        boot_btn = neon_btn("⚡ BOOT AGENTS", COLORS["neon_purple"])
        boot_btn.clicked.connect(self._boot_agents)
        self._api_status = QLabel("")
        self._api_status.setStyleSheet(f"color:{COLORS['text_muted']};font-size:10px;")
        kl.addWidget(self._key_in); kl.addWidget(save_btn)
        kl.addWidget(boot_btn); kl.addWidget(self._api_status); kl.addStretch()
        lay.addWidget(ks)

        # Chat scroll
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea{{background:{COLORS['bg_dark']};border:none;}}"
            f"QScrollBar:vertical{{background:{COLORS['bg_void']};width:5px;}}"
            f"QScrollBar::handle:vertical{{background:{COLORS['border']};}}"
        )
        self._chat_w = QWidget()
        self._chat_l = QVBoxLayout(self._chat_w)
        self._chat_l.setSpacing(4); self._chat_l.setContentsMargins(12,12,12,12)
        self._chat_l.addStretch()
        scroll.setWidget(self._chat_w)
        self._scroll = scroll
        lay.addWidget(scroll, 1)

        # Input
        inp_w = QWidget(); inp_w.setFixedHeight(96)
        inp_w.setStyleSheet(f"background:{COLORS['bg_panel']};border-top:1px solid {COLORS['border']};")
        il = QHBoxLayout(inp_w); il.setContentsMargins(10,8,10,8)
        self._input = QTextEdit()
        self._input.setPlaceholderText(
            "Talk to VEKIL-KAAN…\n"
            "e.g. 'scan 192.168.1.1'  |  'start proxy on 8080'  |  'what's in the vault?'"
        )
        self._input.setStyleSheet(
            f"background:{COLORS['bg_input']};color:{COLORS['text_primary']};"
            f"border:1px solid {COLORS['border']};border-radius:4px;"
            f"font-size:12px;padding:4px;"
        )
        self._input.setMaximumHeight(76)
        btn_col = QVBoxLayout()
        self._send_btn = neon_btn("▶ Send", COLORS["neon_cyan"])
        self._send_btn.setFixedWidth(86); self._send_btn.clicked.connect(self._send)
        self._stop_btn = ghost_btn("■ Stop")
        self._stop_btn.setFixedWidth(86); self._stop_btn.clicked.connect(self._stop)
        self._stop_btn.setEnabled(False)
        btn_col.addWidget(self._send_btn); btn_col.addWidget(self._stop_btn)
        il.addWidget(self._input, 1); il.addLayout(btn_col)
        lay.addWidget(inp_w)

        # Quick prompts
        qw = QWidget(); qw.setFixedHeight(30)
        qw.setStyleSheet(f"background:{COLORS['bg_void']};border-top:1px solid {COLORS['border']};")
        ql = QHBoxLayout(qw); ql.setContentsMargins(10,4,10,4); ql.setSpacing(6)
        for q in ["Status report","Scan localhost","What's in vault?","Proxy status","Search memory"]:
            b = ghost_btn(q); b.setFixedHeight(20)
            b.clicked.connect(lambda _,t=q: self._quick(t))
            ql.addWidget(b)
        ql.addStretch(); lay.addWidget(qw)

        # Boot message
        self._add_bubble("SYSTEM", "text",
            "VEKIL-KAAN Dual Agent System.\n"
            "Boot agents with your Kimi API key first, then start talking.\n"
            "Heartbeat guards every action. Reactive executes. Both share memory.")

    # ── Boot ─────────────────────────────────────────────────────

    def _boot_agents(self):
        key = self._key_in.text().strip()
        if not key:
            self._api_status.setText("⚠ API key required")
            self._api_status.setStyleSheet(f"color:{COLORS['neon_yellow']};font-size:10px;")
            return
        self._api_status.setText("⟳ Booting…")
        self._api_status.setStyleSheet(f"color:{COLORS['neon_yellow']};font-size:10px;")

        from src.core.agents.heartbeat_agent import HeartbeatAgent
        from src.core.agents.reactive_agent import ReactiveAgent
        from src.core.agents.dual_loop import DualReActLoop
        from src.gui.panels.chat.tool_executor import SovereignToolExecutor

        model    = self._model_cb.currentText()
        executor = SovereignToolExecutor(self._proxy, self._gateway, self._discovery)
        reactive  = ReactiveAgent(key, model, executor.execute)
        heartbeat = HeartbeatAgent()
        loop      = DualReActLoop(reactive, heartbeat, executor.execute)

        def _do_boot():
            ok = loop.boot()
            # Store loop + wire pulse BEFORE emitting so UI can safely use them
            if ok:
                self._loop = loop
                heartbeat.on_pulse(self._on_pulse)
            self._boot_result.emit(ok, f"soul={heartbeat._soul.VERSION}")

        threading.Thread(target=_do_boot, daemon=True).start()

    def _on_boot_done(self, ok: bool, info: str):
        # ALL UI updates go here - safe in Qt thread
        if ok:
            self._add_bubble("HEARTBEAT", "pulse",
                f"✅ BOOTED — {info} "
                f"memory={self._loop.memory.root_hash[:8]}")
            self._api_status.setText("✅ Agents ONLINE")
            self._api_status.setStyleSheet(
                f"color:{COLORS['neon_green']};font-size:10px;"
            )
            self._status_dot.setStyleSheet(
                f"color:{COLORS['neon_green']};font-size:14px;"
            )
        else:
            self._add_bubble("SYSTEM","error","❌ Boot failed — check logs")
            self._api_status.setText("❌ Boot failed")
            self._api_status.setStyleSheet(
                f"color:{COLORS['neon_red']};font-size:10px;"
            )

    def _on_pulse(self, pulse):
        seq = pulse.sequence
        self._hb_lbl.setText(f"HB: #{seq}")

    # ── Send ────────────────────────────────────────────────────

    def _send(self):
        text = self._input.toPlainText().strip()
        if not text: return
        if not self._loop:
            self._add_bubble("SYSTEM","error","Boot agents first (⚡ BOOT AGENTS button)")
            return
        self._input.clear()
        self._add_bubble("user","text", text)
        tracker = TaskTracker.get()
        self._task_id = tracker.start("Dual Agent", f"Processing: {text[:50]}…", indeterminate=True).id
        self._worker = DualLoopWorker(self._loop, text)
        self._worker.event_ready.connect(self._on_event)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()
        self._send_btn.setEnabled(False)
        self._stop_btn.setEnabled(True)

    def _stop(self):
        if self._worker: self._worker.stop()
        if self._task_id: TaskTracker.get().done(self._task_id, "Stopped", failed=True)
        self._send_btn.setEnabled(True); self._stop_btn.setEnabled(False)

    def _quick(self, text: str):
        self._input.setPlainText(text); self._send()

    def _on_event(self, kind: str, content: str, meta: dict):
        if kind == "done" and not content.strip(): return
        agent = meta.get("agent", "SYSTEM")
        if kind == "text":
            if content.strip() == "⟳ Reasoning…":
                agent = "SYSTEM"
            else:
                agent = "REACTIVE"
        elif kind in ("tool_call","tool_result"):
            agent = "REACTIVE"
        elif kind in ("verify","pulse"):
            agent = "HEARTBEAT"
        elif kind == "error":
            agent = "SYSTEM"
        self._add_bubble(agent, kind, content)

    def _on_done(self):
        if self._task_id: TaskTracker.get().done(self._task_id, "Done")
        self._send_btn.setEnabled(True); self._stop_btn.setEnabled(False)

    def _on_error(self, err: str):
        self._add_bubble("SYSTEM","error", f"❌ {err}")
        if self._task_id: TaskTracker.get().fail(self._task_id, err)
        self._send_btn.setEnabled(True); self._stop_btn.setEnabled(False)

    def _add_bubble(self, agent: str, kind: str, content: str):
        b = MessageBubble(agent, content, kind)
        count = self._chat_l.count()
        self._chat_l.insertWidget(count-1, b)
        QTimer.singleShot(40, lambda: self._scroll.verticalScrollBar().setValue(
            self._scroll.verticalScrollBar().maximum()
        ))

    def _new_conv(self):
        if self._loop: self._loop.new_conversation()
        while self._chat_l.count() > 1:
            item = self._chat_l.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self._add_bubble("SYSTEM","text","New conversation. Memory persists. Talk away.")

    def _save_key(self):
        key = self._key_in.text().strip()
        if not key: return
        try:
            VaultStore.unlock("")
            VaultStore.set("Kimi API Key", key, provider="kimi", env_var="KIMI_API_KEY")
            self._api_status.setText("💾 Saved to vault")
            self._api_status.setStyleSheet(f"color:{COLORS['neon_green']};font-size:10px;")
        except Exception as e:
            self._api_status.setText(f"❌ {e}")

    def _load_api_key(self):
        key = os.environ.get("KIMI_API_KEY","")
        if not key and VaultStore.is_unlocked():
            key = VaultStore.get_key("Kimi API Key") or ""
        if key:
            self._key_in.setText(key)
            self._api_status.setText("✅ Key loaded")
            self._api_status.setStyleSheet(f"color:{COLORS['neon_green']};font-size:10px;")
