"""
Main Application Window for Ultra Orchestrator.

This is the central PyQt6 main window that hosts all panels and connects
them to the OrchestratorCore via Qt signals/slots. All widget updates
happen on the main Qt thread for thread safety.

Layout::

    +-------------------------------------------------------------+
    |  Ultra Orchestrator v1.0     [Run] [Pause] [Stop] [Settings]
    +-------------------------------------------------------------+
    |  +------------------------+  +---------------------------+  |
    |  |   TASK INPUT PANEL     |  |  ORCHESTRATOR REASONING   |  |
    |  +------------------------+  +---------------------------+  |
    |  +--------------------------------------------------------+ |
    |  |              AGENT MONITOR (20 SLOTS)                  | |
    |  +--------------------------------------------------------+ |
    |  +------------------------+  +---------------------------+  |
    |  |    API KEY STATUS      |  |   QUALITY GATE STATS      |  |
    |  +------------------------+  +---------------------------+  |
    |  +--------------------------------------------------------+ |
    |  |                    LOG VIEWER                          | |
    |  +--------------------------------------------------------+ |
    +-------------------------------------------------------------+
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Dict, Optional

from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import (
    Q_ARG,
    QMetaObject,
    Qt,
    QThread,
    QTimer,
    pyqtSignal,
)

# ---------------------------------------------------------------------------
# Panel imports
# ---------------------------------------------------------------------------
from gui.panels.task_input import TaskInputPanel
from gui.panels.reasoning_viewer import ReasoningViewer
from gui.panels.agent_monitor import AgentMonitor
from gui.panels.log_viewer import LogViewer
from gui.panels.api_status import ApiStatusPanel
from gui.panels.quality_stats import QualityStatsPanel
from gui.panels.settings import SettingsPanel
from gui.widgets.reasoning_popup import ReasoningPopup
from orchestrator.core import OrchestratorCore


# ---------------------------------------------------------------------------
# MainWindow
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    """Central PyQt6 main window for the Ultra Orchestrator desktop application.

    Hosts all panels, manages the toolbar / status bar, and connects everything
    to the ``OrchestratorCore`` via Qt signals/slots.  Every GUI mutation is
    guaranteed to run on the main Qt thread.
    """

    # -- Qt signal used to marshal core events onto the main thread ------------
    _core_event_received = pyqtSignal(str, dict)

    # -- Internal signal for safe cross-thread GUI updates --------------------
    _gui_update_requested = pyqtSignal(object, object)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(self, core: OrchestratorCore) -> None:
        super().__init__()

        self.core: OrchestratorCore = core
        self.panels: Dict[str, QWidget] = {}
        self.toolbar: Optional[QToolBar] = None
        self.status_bar: QStatusBar = QStatusBar(self)
        self.update_timer: QTimer = QTimer(self)
        self.is_running: bool = False

        # Status label displayed in the toolbar
        self._status_label: Optional[QLabel] = None

        self._setup_window()
        self._setup_panels()
        self._setup_toolbar()
        self._setup_status_bar()
        self._setup_timer()
        self._connect_signals()
        self._connect_core_events()
        self._load_stylesheet_and_apply()

        # Prompt the user about incomplete sessions after the event loop starts
        QTimer.singleShot(100, self._check_incomplete_sessions)

    # ------------------------------------------------------------------
    # Window / stylesheet setup
    # ------------------------------------------------------------------

    def _setup_window(self) -> None:
        """Configure basic window properties."""
        self.setWindowTitle("Ultra Orchestrator v1.0")
        self.setMinimumSize(1400, 900)
        self.resize(1600, 1050)

    def _load_stylesheet_and_apply(self) -> None:
        """Load the QSS stylesheet and apply it to the application."""
        qss = self._load_stylesheet()
        if qss:
            self.setStyleSheet(qss)

    def _load_stylesheet(self) -> str:
        """Load and return QSS content from ``assets/styles.qss``.

        The file is searched relative to the executable / project root so that
        both frozen and development runs work.
        """
        candidate_paths = [
            Path(__file__).resolve().parents[2] / "assets" / "styles.qss",
            Path.cwd() / "assets" / "styles.qss",
            Path(sys.argv[0]).resolve().parent / "assets" / "styles.qss" if "sys" in globals() else Path(),
            Path.cwd().parent / "assets" / "styles.qss",
        ]

        for candidate in candidate_paths:
            if candidate.exists() and candidate.is_file():
                try:
                    return candidate.read_text(encoding="utf-8")
                except OSError:
                    continue

        # Fallback: return an empty string so the app still runs without styles
        return ""

    # ------------------------------------------------------------------
    # Toolbar
    # ------------------------------------------------------------------

    def _setup_toolbar(self) -> None:
        """Create the top QToolBar with Run / Pause / Stop / Settings buttons."""
        self.toolbar = QToolBar("Control Toolbar", self)
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)

        # --- Run button ------------------------------------------------
        btn_run = QPushButton("\u25b6  Run")
        btn_run.setObjectName("toolbarBtnRun")
        btn_run.setToolTip("Start orchestrator execution")
        btn_run.clicked.connect(self._on_run)
        self.toolbar.addWidget(btn_run)
        self.panels["btn_run"] = btn_run

        # --- Pause button ----------------------------------------------
        btn_pause = QPushButton("\u23f8 Pause")
        btn_pause.setObjectName("toolbarBtnPause")
        btn_pause.setToolTip("Pause execution")
        btn_pause.clicked.connect(self._on_pause)
        self.toolbar.addWidget(btn_pause)
        self.panels["btn_pause"] = btn_pause

        # --- Stop button -----------------------------------------------
        btn_stop = QPushButton("\u23f9 Stop")
        btn_stop.setObjectName("toolbarBtnStop")
        btn_stop.setToolTip("Stop execution")
        btn_stop.clicked.connect(self._on_stop)
        self.toolbar.addWidget(btn_stop)
        self.panels["btn_stop"] = btn_stop

        # --- Separator -------------------------------------------------
        self.toolbar.addSeparator()

        # --- Settings button -------------------------------------------
        btn_settings = QPushButton("\u2699 Settings")
        btn_settings.setObjectName("toolbarBtnSettings")
        btn_settings.setToolTip("Open settings")
        btn_settings.clicked.connect(self._on_settings)
        self.toolbar.addWidget(btn_settings)
        self.panels["btn_settings"] = btn_settings

        # --- Spacer + session status label -----------------------------
        self.toolbar.addSeparator()
        self._status_label = QLabel("<span style='color:#4caf50;'>&#9679;</span> Ready")
        self._status_label.setObjectName("toolbarStatusLabel")
        self.toolbar.addWidget(self._status_label)

    # ------------------------------------------------------------------
    # Status bar
    # ------------------------------------------------------------------

    def _setup_status_bar(self) -> None:
        """Attach the status bar and add a permanent progress hint label."""
        self.setStatusBar(self.status_bar)
        self.show_status_message("Ready")

    def show_status_message(self, message: str, timeout: int = 5000) -> None:
        """Display *message* in the status bar for *timeout* ms (0 = permanent)."""
        self.status_bar.showMessage(message, timeout)

    # ------------------------------------------------------------------
    # Panel layout
    # ------------------------------------------------------------------

    def _setup_panels(self) -> None:
        """Instantiate every panel, wrap it in a QGroupBox, and arrange the UI.

        Layout hierarchy::

            QMainWindow
                central_widget (QWidget)
                    QVBoxLayout
                        QSplitter (vertical)
                            ├─ row_top    (QSplitter horizontal)
                            │     ├─ TaskInputPanel
                            │     └─ ReasoningViewer
                            ├─ row_middle (AgentMonitor)
                            ├─ row_bottom (QSplitter horizontal)
                            │     ├─ ApiStatusPanel
                            │     └─ QualityStatsPanel
                            └─ row_log    (LogViewer)
        """
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(6)

        # -- Panels ----------------------------------------------------
        self.panels["task_input"] = TaskInputPanel(self)
        self.panels["reasoning"] = ReasoningViewer(self)
        self.panels["agent_monitor"] = AgentMonitor(self)
        self.panels["api_status"] = ApiStatusPanel(self)
        self.panels["quality_stats"] = QualityStatsPanel(self)
        self.panels["log_viewer"] = LogViewer(self)
        self.panels["settings"] = SettingsPanel(self)

        # -- Group boxes -----------------------------------------------
        gb_task = QGroupBox("Task Input")
        gb_task.setObjectName("panelTaskInput")
        gb_task_layout = QVBoxLayout(gb_task)
        gb_task_layout.setContentsMargins(6, 14, 6, 6)
        gb_task_layout.addWidget(self.panels["task_input"])

        gb_reasoning = QGroupBox("Orchestrator Reasoning")
        gb_reasoning.setObjectName("panelReasoning")
        gb_reasoning_layout = QVBoxLayout(gb_reasoning)
        gb_reasoning_layout.setContentsMargins(6, 14, 6, 6)
        gb_reasoning_layout.addWidget(self.panels["reasoning"])

        gb_agent = QGroupBox("Agent Monitor")
        gb_agent.setObjectName("panelAgentMonitor")
        gb_agent_layout = QVBoxLayout(gb_agent)
        gb_agent_layout.setContentsMargins(6, 14, 6, 6)
        gb_agent_layout.addWidget(self.panels["agent_monitor"])

        gb_api = QGroupBox("API Key Status")
        gb_api.setObjectName("panelApiStatus")
        gb_api_layout = QVBoxLayout(gb_api)
        gb_api_layout.setContentsMargins(6, 14, 6, 6)
        gb_api_layout.addWidget(self.panels["api_status"])

        gb_quality = QGroupBox("Quality Gate Stats")
        gb_quality.setObjectName("panelQualityStats")
        gb_quality_layout = QVBoxLayout(gb_quality)
        gb_quality_layout.setContentsMargins(6, 14, 6, 6)
        gb_quality_layout.addWidget(self.panels["quality_stats"])

        gb_log = QGroupBox("Log Viewer")
        gb_log.setObjectName("panelLogViewer")
        gb_log_layout = QVBoxLayout(gb_log)
        gb_log_layout.setContentsMargins(6, 14, 6, 6)
        gb_log_layout.addWidget(self.panels["log_viewer"])

        # -- Settings panel is initially hidden (shown via Settings button)
        self.panels["settings"].setVisible(False)

        # -- Top row: TaskInput (40%) + ReasoningViewer (60%) --------
        splitter_top = QSplitter(Qt.Orientation.Horizontal)
        splitter_top.addWidget(gb_task)
        splitter_top.addWidget(gb_reasoning)
        splitter_top.setSizes([560, 840])
        splitter_top.setStretchFactor(0, 4)
        splitter_top.setStretchFactor(1, 6)

        # -- Bottom row: ApiStatus (50%) + QualityStats (50%) --------
        splitter_bottom = QSplitter(Qt.Orientation.Horizontal)
        splitter_bottom.addWidget(gb_api)
        splitter_bottom.addWidget(gb_quality)
        splitter_bottom.setSizes([700, 700])

        # -- Main vertical splitter -----------------------------------
        splitter_main = QSplitter(Qt.Orientation.Vertical)
        splitter_main.addWidget(splitter_top)
        splitter_main.addWidget(gb_agent)
        splitter_main.addWidget(splitter_bottom)
        splitter_main.addWidget(gb_log)

        # Give the agent monitor the most stretch
        splitter_main.setStretchFactor(0, 1)   # top row
        splitter_main.setStretchFactor(1, 3)   # agent monitor
        splitter_main.setStretchFactor(2, 1)   # bottom row
        splitter_main.setStretchFactor(3, 1)   # log viewer

        # Set initial sizes so the log viewer is ~200 px and agent monitor dominates
        splitter_main.setSizes([180, 420, 160, 200])

        main_layout.addWidget(splitter_main)

        # Add the settings panel below everything (hidden by default)
        main_layout.addWidget(self.panels["settings"])

    # ------------------------------------------------------------------
    # Timer
    # ------------------------------------------------------------------

    def _setup_timer(self) -> None:
        """Start the periodic dashboard refresh timer (500 ms)."""
        self.update_timer.setInterval(500)
        self.update_timer.timeout.connect(self._refresh_dashboard)
        self.update_timer.start()

    # ------------------------------------------------------------------
    # Signal / slot connections
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        """Wire panel signals to their corresponding slots."""
        task_panel: TaskInputPanel = self.panels["task_input"]       # type: ignore[assignment]
        agent_monitor: AgentMonitor = self.panels["agent_monitor"]   # type: ignore[assignment]
        reasoning: ReasoningViewer = self.panels["reasoning"]        # type: ignore[assignment]
        log_viewer: LogViewer = self.panels["log_viewer"]            # type: ignore[assignment]
        settings: SettingsPanel = self.panels["settings"]            # type: ignore[assignment]

        task_panel.start_task.connect(self._on_start_task)
        agent_monitor.agent_reasoning_requested.connect(self._show_reasoning_popup)
        reasoning.reasoning_toggled.connect(self._on_reasoning_toggled)
        log_viewer.export_requested.connect(self._on_export_logs)
        settings.settings_saved.connect(self._on_settings_saved)

        # Internal signals for thread-safe GUI updates
        self._core_event_received.connect(self._handle_core_event_on_main_thread)
        self._gui_update_requested.connect(self._execute_gui_update)

    def _connect_core_events(self) -> None:
        """Register the core event callback so core events reach the GUI."""
        self.core.register_event_callback(self._on_core_event)

    # ------------------------------------------------------------------
    # Thread-safe GUI helpers
    # ------------------------------------------------------------------

    def _safe_gui_update(self, callback: Any, *args: Any) -> None:
        """Ensure *callback* runs on the main Qt thread.

        If already on the main thread the callback is invoked directly;
        otherwise it is marshalled via a queued signal.
        """
        app = QApplication.instance()
        main_thread = app.thread() if app is not None else self.thread()

        if QThread.currentThread() == main_thread:
            callback(*args)
        else:
            self._gui_update_requested.emit(callback, args)

    def _execute_gui_update(self, callback: object, args: object) -> None:
        """Slot that executes a callback + args tuple on the main thread."""
        try:
            cb = callback  # type: ignore[operator]
            a = args       # type: ignore[operator]
            cb(*a)
        except Exception as exc:
            self.show_status_message(f"GUI update error: {exc}", 8000)

    # ------------------------------------------------------------------
    # Slots – task lifecycle
    # ------------------------------------------------------------------

    def _on_start_task(
        self,
        title: str,
        description: str,
        template: str,
        max_subtasks: int,
    ) -> None:
        """Handle the **Start** signal from the TaskInputPanel.

        Disables the task input, creates a new session on the core, and
        begins execution when the session is ready.
        """
        self._safe_gui_update(self._disable_task_input)
        self.show_status_message("Decomposing task...")

        async def _launch() -> None:
            try:
                session = await self.core.create_new_session(
                    title=title,
                    description=description,
                    template=template,
                    max_subtasks=max_subtasks,
                )
                subtasks = session.subtasks if hasattr(session, "subtasks") else []
                count = len(subtasks)

                self._safe_gui_update(
                    self._show_decompose_result, count
                )

                # Start execution automatically after decomposition
                await self.core.start_execution()
                self._safe_gui_update(self._mark_running)

            except Exception as exc:
                self._safe_gui_update(
                    self._handle_task_error, str(exc)
                )

        asyncio.create_task(_launch())

    def _disable_task_input(self) -> None:
        """Disable the task input panel so the user cannot submit twice."""
        panel: TaskInputPanel = self.panels["task_input"]  # type: ignore[assignment]
        panel.setEnabled(False)

    def _enable_task_input(self) -> None:
        """Re-enable the task input panel after execution finishes."""
        panel: TaskInputPanel = self.panels["task_input"]  # type: ignore[assignment]
        panel.setEnabled(True)

    def _show_decompose_result(self, subtask_count: int) -> None:
        """Show how many subtasks were created."""
        self.add_log_entry(
            timestamp="",
            event_type="ORCH",
            severity="INFO",
            message=f"Task decomposed into {subtask_count} subtask(s)",
        )
        self.show_status_message(
            f"Task decomposed into {subtask_count} subtask(s) — starting execution..."
        )

    def _handle_task_error(self, error_msg: str) -> None:
        """Display an error that occurred during task launch."""
        self.show_status_message(f"Error: {error_msg}", 10000)
        self.add_log_entry(
            timestamp="", event_type="ORCH", severity="ERROR", message=error_msg
        )
        self._enable_task_input()

    def _mark_running(self) -> None:
        """Update internal state and UI to reflect a running session."""
        self.is_running = True
        if self._status_label is not None:
            self._status_label.setText(
                "<span style='color:#2196f3;'>&#9679;</span> Running"
            )

    # ------------------------------------------------------------------
    # Slots – toolbar controls
    # ------------------------------------------------------------------

    def _on_run(self) -> None:
        """Toolbar **Run** button — start execution via the core."""
        async def _do_run() -> None:
            try:
                await self.core.start_execution()
                self._safe_gui_update(self._mark_running)
            except Exception as exc:
                self._safe_gui_update(
                    self.show_status_message, f"Run error: {exc}", 8000
                )

        asyncio.create_task(_do_run())

    def _on_pause(self) -> None:
        """Toolbar **Pause** button — pause execution."""
        async def _do_pause() -> None:
            try:
                await self.core.pause_execution()
                self._safe_gui_update(self._mark_paused)
            except Exception as exc:
                self._safe_gui_update(
                    self.show_status_message, f"Pause error: {exc}", 8000
                )

        asyncio.create_task(_do_pause())

    def _mark_paused(self) -> None:
        """Update internal state and UI to reflect a paused session."""
        self.is_running = False
        if self._status_label is not None:
            self._status_label.setText(
                "<span style='color:#ff9800;'>&#9679;</span> Paused"
            )
        self.show_status_message("Execution paused")

    def _on_stop(self) -> None:
        """Toolbar **Stop** button — stop execution and re-enable input."""
        async def _do_stop() -> None:
            try:
                await self.core.stop_execution()
                self._safe_gui_update(self._mark_stopped)
            except Exception as exc:
                self._safe_gui_update(
                    self.show_status_message, f"Stop error: {exc}", 8000
                )

        asyncio.create_task(_do_stop())

    def _mark_stopped(self) -> None:
        """Update internal state and UI after stopping."""
        self.is_running = False
        if self._status_label is not None:
            self._status_label.setText(
                "<span style='color:#f44336;'>&#9679;</span> Stopped"
            )
        self._enable_task_input()
        self.show_status_message("Execution stopped")

    def _on_settings(self) -> None:
        """Toggle visibility of the Settings panel."""
        settings: SettingsPanel = self.panels["settings"]  # type: ignore[assignment]
        settings.setVisible(not settings.isVisible())

    # ------------------------------------------------------------------
    # Periodic dashboard refresh
    # ------------------------------------------------------------------

    def _refresh_dashboard(self) -> None:
        """Called every 500 ms — pulls live data from the core and refreshes panels.

        Only performs work when ``self.is_running`` is *True*.
        """
        if not self.is_running:
            return

        try:
            dashboard = self.core.get_dashboard_data()
        except Exception:
            return

        # -- AgentMonitor ----------------------------------------------
        subtasks = dashboard.get("subtasks", [])
        self.panels["agent_monitor"].update_subtasks(subtasks)  # type: ignore[attr-defined]

        # -- ApiStatusPanel --------------------------------------------
        api_statuses = dashboard.get("api_keys", {})
        self.panels["api_status"].update_statuses(api_statuses)  # type: ignore[attr-defined]

        # -- QualityStatsPanel -----------------------------------------
        quality = dashboard.get("quality_stats", {})
        self.panels["quality_stats"].update_stats(quality)  # type: ignore[attr-defined]

        # -- Toolbar status (progress) ---------------------------------
        total = dashboard.get("total_subtasks", 0)
        completed = dashboard.get("completed_subtasks", 0)
        if total > 0 and self._status_label is not None:
            pct = int((completed / total) * 100)
            self._status_label.setText(
                f"<span style='color:#2196f3;'>&#9679;</span> "
                f"Running  ({completed}/{total}  {pct}%)"
            )

    # ------------------------------------------------------------------
    # Core event callback (may be called from a worker thread)
    # ------------------------------------------------------------------

    def _on_core_event(self, event_type: str, data: dict) -> None:
        """Entry point called by ``OrchestratorCore`` when something happens.

        The callback is *not* guaranteed to be on the Qt thread, so we emit a
        Qt signal to marshal the event safely onto the main loop.
        """
        self._core_event_received.emit(event_type, data)

    def _handle_core_event_on_main_thread(self, event_type: str, data: dict) -> None:
        """Process a core event on the main Qt thread.

        This slot is connected to ``_core_event_received`` and therefore
        always runs on the GUI thread.
        """
        handlers: Dict[str, Any] = {
            "subtask_updated": self._evt_subtask_updated,
            "agent_completed": self._evt_agent_completed,
            "batch_spawned": self._evt_batch_spawned,
            "session_complete": self._evt_session_complete,
            "error": self._evt_error,
            "log": self._evt_log,
        }

        handler = handlers.get(event_type)
        if handler is not None:
            try:
                handler(data)
            except Exception as exc:
                self.show_status_message(f"Event handler error: {exc}", 8000)
        else:
            # Unknown event — just log it
            self.add_log_entry(
                timestamp="",
                event_type="ORCH",
                severity="WARN",
                message=f"Unhandled core event: {event_type}",
            )

    # -- Individual event handlers -------------------------------------

    def _evt_subtask_updated(self, data: dict) -> None:
        """Refresh a single agent card when its subtask changes."""
        subtask_id = data.get("subtask_id", "")
        subtask_data = data.get("data", {})
        agent_monitor: AgentMonitor = self.panels["agent_monitor"]  # type: ignore[assignment]
        agent_monitor.refresh_card(subtask_id, subtask_data)

    def _evt_agent_completed(self, data: dict) -> None:
        """Log completion and refresh quality stats."""
        agent_name = data.get("agent_name", "Unknown")
        subtask_id = data.get("subtask_id", "")
        result = data.get("result", "")
        self.add_log_entry(
            timestamp="",
            event_type="AGENT",
            severity="INFO",
            message=f"Agent '{agent_name}' completed subtask {subtask_id}: {result}",
        )
        # Trigger an immediate stats refresh
        self._refresh_dashboard()

    def _evt_batch_spawned(self, data: dict) -> None:
        """Log a new batch of subtasks."""
        count = data.get("count", 0)
        self.add_log_entry(
            timestamp="",
            event_type="ORCH",
            severity="INFO",
            message=f"Spawned batch of {count} subtask(s)",
        )

    def _evt_session_complete(self, data: dict) -> None:
        """Show a completion message and re-enable the UI."""
        message = data.get("message", "Session completed successfully")
        self.is_running = False

        if self._status_label is not None:
            self._status_label.setText(
                "<span style='color:#4caf50;'>&#9679;</span> Ready"
            )

        self.add_log_entry(
            timestamp="", event_type="ORCH", severity="INFO", message=message
        )
        self._enable_task_input()
        self.show_status_message(message, 10000)

        # Show a non-blocking info dialog
        QMessageBox.information(self, "Session Complete", message)

    def _evt_error(self, data: dict) -> None:
        """Display an error that originated inside the core."""
        error_msg = data.get("message", "Unknown error")
        self.show_status_message(f"Error: {error_msg}", 10000)
        self.add_log_entry(
            timestamp="", event_type="ORCH", severity="ERROR", message=error_msg
        )

    def _evt_log(self, data: dict) -> None:
        """Generic log event forwarded from the core."""
        self.add_log_entry(
            timestamp=data.get("timestamp", ""),
            event_type=data.get("event_type", "ORCH"),
            severity=data.get("severity", "INFO"),
            message=data.get("message", ""),
        )

    # ------------------------------------------------------------------
    # Reasoning popup
    # ------------------------------------------------------------------

    def _show_reasoning_popup(self, subtask_id: str) -> None:
        """Open a modal popup that displays reasoning data for *subtask_id*."""
        try:
            reasoning_data = self.core.get_reasoning_for_subtask(subtask_id)
        except Exception as exc:
            reasoning_data = {"error": str(exc)}

        popup = ReasoningPopup(subtask_id, reasoning_data, parent=self)
        popup.exec()

    # ------------------------------------------------------------------
    # Reasoning toggle
    # ------------------------------------------------------------------

    def _on_reasoning_toggled(self, enabled: bool) -> None:
        """Enable or disable reasoning capture in the core."""
        try:
            self.core.set_reasoning_capture(enabled)
        except Exception as exc:
            self.show_status_message(f"Reasoning toggle error: {exc}", 8000)

    # ------------------------------------------------------------------
    # Log export
    # ------------------------------------------------------------------

    def _on_export_logs(self, file_path: str, fmt: str) -> None:
        """Export logs to *file_path* in the given format via the core."""
        try:
            self.core.export_logs(file_path, fmt)
            self.show_status_message(f"Logs exported to {file_path}")
        except Exception as exc:
            self.show_status_message(f"Export error: {exc}", 8000)

    # ------------------------------------------------------------------
    # Settings saved
    # ------------------------------------------------------------------

    def _on_settings_saved(self, settings: dict) -> None:
        """Apply new settings through the core."""
        try:
            self.core.update_settings(settings)
            self.show_status_message("Settings saved and applied")

            # Hide the settings panel after a successful save
            self.panels["settings"].setVisible(False)  # type: ignore[attr-defined]
        except Exception as exc:
            self.show_status_message(f"Settings error: {exc}", 8000)

    # ------------------------------------------------------------------
    # Log viewer public API
    # ------------------------------------------------------------------

    def add_log_entry(
        self,
        timestamp: str,
        event_type: str,
        severity: str,
        message: str,
    ) -> None:
        """Append a log entry to the LogViewer.

        Safe to call from any thread — automatically marshals onto the Qt
        main thread when necessary.
        """
        self._safe_gui_update(
            self._do_add_log_entry, timestamp, event_type, severity, message
        )

    def _do_add_log_entry(
        self,
        timestamp: str,
        event_type: str,
        severity: str,
        message: str,
    ) -> None:
        """Actual implementation (always runs on main thread)."""
        log_viewer: LogViewer = self.panels["log_viewer"]  # type: ignore[assignment]
        log_viewer.add_entry(timestamp, event_type, severity, message)

    # ------------------------------------------------------------------
    # Incomplete session check (startup)
    # ------------------------------------------------------------------

    def _check_incomplete_sessions(self) -> None:
        """On startup, ask the user whether to resume an incomplete session."""
        try:
            incomplete = self.core.has_incomplete_sessions()
        except Exception:
            return

        if incomplete:
            reply = QMessageBox.question(
                self,
                "Resume Session",
                "An incomplete session was found.\n\n"
                "Would you like to resume where you left off?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if reply == QMessageBox.StandardButton.Yes:
                asyncio.create_task(self._do_resume_session())

    async def _do_resume_session(self) -> None:
        """Resume the most recent incomplete session."""
        try:
            session = await self.core.resume_last_session()
            self._safe_gui_update(self._on_resume_success, session)
        except Exception as exc:
            self._safe_gui_update(
                self.show_status_message, f"Resume error: {exc}", 8000
            )

    def _on_resume_success(self, session: Any) -> None:
        """UI updates after a successful resume."""
        subtask_count = (
            len(session.subtasks) if hasattr(session, "subtasks") else 0
        )
        self.add_log_entry(
            timestamp="",
            event_type="ORCH",
            severity="INFO",
            message=f"Resumed session with {subtask_count} subtask(s)",
        )
        self.show_status_message(
            f"Resumed session with {subtask_count} subtask(s)"
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Graceful shutdown: stop execution, save checkpoint, cleanup core."""
        self.show_status_message("Shutting down...")
        self.update_timer.stop()

        # Stop any running execution
        if self.is_running:
            try:
                # Fire-and-forget stop; we block briefly with an event-loop hack
                asyncio.ensure_future(self.core.stop_execution())
            except Exception:
                pass

        # Save checkpoint and shutdown core
        try:
            self.core.save_checkpoint()
            self.core.shutdown()
        except Exception:
            pass

        event.accept()
