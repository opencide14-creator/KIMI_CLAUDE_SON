#!/usr/bin/env python3
"""
SOVEREIGN — Network Sovereignty Command Center
═══════════════════════════════════════════════
7-panel network control tool:
  🔴 INTERCEPT  — HTTPS/WS MITM traffic capture
  🟡 FORGE      — Certificate Authority + hosts file
  🟢 GATEWAY    — AI model router (Claude→Kimi→Ollama)
  🔵 STREAMS    — WebSocket + MCP inspector
  🟣 DISCOVER   — Async port scanner + service registry
  ⚪ VAULT      — Encrypted credential store
  📊 INTEL      — Traffic analytics + HAR export

Usage:
    python3 main.py
    SOVEREIGN_PROXY_PORT=8080 python3 main.py
"""
import logging
import os
import sys
from pathlib import Path

# ── Ensure root is importable ──────────────────────────────────────
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Logging ────────────────────────────────────────────────────────
from src.constants import LOG_DIR, APP_NAME, APP_VERSION

LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "sovereign.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("sovereign")

# ── Qt high-DPI ────────────────────────────────────────────────────
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QFont, QFontDatabase

from src.gui.styles import get_stylesheet
from src.gui.main_window import MainWindow


def main() -> int:
    log.info("Starting %s v%s", APP_NAME, APP_VERSION)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)
    app.setStyleSheet(get_stylesheet())

    # Font fallback chain — prevents "-1 point size" warning if JetBrains Mono unavailable
    preferred = ["JetBrains Mono", "Fira Code", "Cascadia Code", "Consolas", "Courier New"]
    font_name = "Courier New"  # guaranteed fallback
    for name in preferred:
        if name in QFontDatabase.families():
            font_name = name
            break
    font = QFont(font_name, 11)
    font.setStyleHint(QFont.StyleHint.Monospace)
    app.setFont(font)

    window = MainWindow()
    window.show()

    log.info("UI ready — entering event loop")
    code = app.exec()
    log.info("Exit %d", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
