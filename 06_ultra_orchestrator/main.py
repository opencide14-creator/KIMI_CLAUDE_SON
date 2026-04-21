#!/usr/bin/env python3
"""
Ultra Orchestrator — Desktop Application Entry Point
Launches the PyQt6 GUI with async orchestrator backend.
"""

import sys
import os
import asyncio
import logging
from pathlib import Path

# Ensure project root is in path
PROJECT_ROOT = Path(__file__).parent.resolve()
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

def setup_logging():
    """Configure root logging."""
    log_format = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller bundle
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(PROJECT_ROOT, relative_path)

async def main():
    """Async main entry point."""
    setup_logging()
    logger = logging.getLogger("UltraOrchestrator")
    logger.info("=" * 60)
    logger.info("Ultra Orchestrator v1.0 — Starting")
    logger.info("=" * 60)
    
    # Import here to allow path setup first
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    from gui.main_window import MainWindow
    from orchestrator.core import OrchestratorCore
    
    # Enable high DPI scaling
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    
    app = QApplication(sys.argv)
    app.setApplicationName("Ultra Orchestrator")
    app.setApplicationVersion("1.0.0")
    app.setOrganizationName("UltraOrchestrator")
    
    # Determine resource paths
    templates_dir = get_resource_path("templates")
    assets_dir = get_resource_path("assets")
    db_path = os.path.join(PROJECT_ROOT, "orchestrator.db")
    
    logger.info(f"Templates: {templates_dir}")
    logger.info(f"Assets: {assets_dir}")
    logger.info(f"Database: {db_path}")
    
    # Create and initialize core
    core = OrchestratorCore(
        db_path=db_path,
        templates_dir=templates_dir,
        max_concurrent=20
    )
    
    init_result = await core.initialize()
    if not init_result["success"]:
        logger.error("Failed to initialize core")
        return 1
    
    logger.info(f"Components ready: {init_result['components_ready']}")
    
    # Create main window
    window = MainWindow(core)
    window.show()
    
    # Check for incomplete sessions
    if init_result.get("incomplete_sessions"):
        window._check_incomplete_sessions()
    
    # Run Qt event loop
    logger.info("GUI initialized. Running event loop.")
    
    # Integrate asyncio with Qt using a timer-based approach
    def run_async_loop():
        """Process pending asyncio tasks."""
        loop = asyncio.get_event_loop()
        loop.stop()
        loop.run_forever()
    
    # Setup async-Qt integration timer
    from PyQt6.QtCore import QTimer
    timer = QTimer()
    timer.timeout.connect(lambda: asyncio.get_event_loop().stop())
    timer.start(10)  # Process async tasks every 10ms
    
    # Execute Qt app
    exit_code = app.exec()
    
    # Graceful shutdown
    logger.info("Shutting down...")
    await core.shutdown()
    
    return exit_code

if __name__ == "__main__":
    # On Windows, use ProactorEventLoop for subprocess support
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    
    try:
        loop = asyncio.get_event_loop()
        exit_code = loop.run_until_complete(main())
        sys.exit(exit_code if exit_code else 0)
    except KeyboardInterrupt:
        print("\nShutdown requested by user.")
        sys.exit(0)
    except Exception as e:
        logging.exception("Fatal error during startup")
        sys.exit(1)
