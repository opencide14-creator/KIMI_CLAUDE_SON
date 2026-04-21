#!/usr/bin/env python3
"""
Ultra Orchestrator GUI Screenshot Tool
Captures each panel individually
"""
import os
import sys
import time

project_dir = r"C:\Users\ALUVERSE\Downloads\CLAUDE.AI\Kimi_Agent_Ultra Orchestrator Codebase\ultra_orchestrator"
os.chdir(project_dir)
sys.path.insert(0, project_dir)

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout
from PyQt6.QtCore import QTimer, Qt

try:
    from PIL import ImageGrab
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow", "-q"])
    from PIL import ImageGrab

output_dir = os.path.join(project_dir, "screenshots")
os.makedirs(output_dir, exist_ok=True)


def capture_widget(widget, name):
    """Capture a widget and save to file."""
    widget.show()
    widget.raise_()
    widget.activateWindow()
    QApplication.instance().processEvents()
    time.sleep(0.5)

    geo = widget.geometry()
    x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
    if w < 100 or h < 100:
        w, h = 800, 600
    filepath = os.path.join(output_dir, f"{name}.png")
    ImageGrab.grab(bbox=(x, y, x + w, y + h)).save(filepath)
    print(f"[OK] {filepath} ({w}x{h})")
    return filepath


def main():
    app = QApplication(sys.argv)
    screenshots = []

    # Test each panel individually in a container window
    panels_to_test = [
        ("task_input", "gui.panels.task_input", "TaskInputPanel"),
        ("reasoning_viewer", "gui.panels.reasoning_viewer", "ReasoningViewer"),
        ("agent_monitor", "gui.panels.agent_monitor", "AgentMonitor"),
        ("api_status", "gui.panels.api_status", "ApiStatusPanel"),
        ("quality_stats", "gui.panels.quality_stats", "QualityStatsPanel"),
        ("log_viewer", "gui.panels.log_viewer", "LogViewer"),
        ("settings", "gui.panels.settings", "SettingsPanel"),
    ]

    for name, module_path, class_name in panels_to_test:
        try:
            module = __import__(module_path, fromlist=[class_name])
            panel_class = getattr(module, class_name)

            # Create container
            container = QWidget()
            container.setWindowTitle(f"Ultra Orchestrator - {name}")
            container.setGeometry(100, 100, 900, 600)
            layout = QVBoxLayout()

            # Create panel instance
            panel = panel_class()
            layout.addWidget(panel)
            container.setLayout(layout)

            screenshots.append((name, capture_widget(container, name)))
            container.close()

        except Exception as e:
            print(f"[FAIL] {name}: {e}")

    # Summary
    print(f"\n[DONE] {len(screenshots)}/{len(panels_to_test)} panels captured:")
    for name, path in screenshots:
        print(f"  - {name}: {path}")

    QTimer.singleShot(500, app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
