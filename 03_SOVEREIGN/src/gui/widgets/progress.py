"""Universal Progress Tracker — real-time status for every long-running operation.

Every tool call, scan, proxy start, gateway request shows here.
Never wonder if something is running again.
"""
from __future__ import annotations
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel,
    QProgressBar, QScrollArea, QFrame,
)
from PyQt6.QtGui import QColor

from src.constants import COLORS


class TaskState(Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    DONE     = "done"
    FAILED   = "failed"
    CANCELED = "canceled"


@dataclass
class Task:
    id:       str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name:     str = ""
    detail:   str = ""
    state:    TaskState = TaskState.PENDING
    progress: int = 0          # 0-100, -1 = indeterminate
    started:  datetime = field(default_factory=datetime.now)
    ended:    Optional[datetime] = None
    result:   str = ""

    @property
    def elapsed_s(self) -> float:
        end = self.ended or datetime.now()
        return (end - self.started).total_seconds()

    @property
    def elapsed_str(self) -> str:
        s = self.elapsed_s
        if s < 60:   return f"{s:.0f}s"
        if s < 3600: return f"{s/60:.0f}m {s%60:.0f}s"
        return f"{s/3600:.0f}h {(s%3600)/60:.0f}m"


class TaskTracker(QObject):
    """Singleton task manager. Emit signals when tasks change."""
    task_added   = pyqtSignal(object)  # Task
    task_updated = pyqtSignal(object)  # Task
    task_done    = pyqtSignal(object)  # Task

    _instance: Optional["TaskTracker"] = None

    @classmethod
    def get(cls) -> "TaskTracker":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__()
        self._tasks: Dict[str, Task] = {}

    def start(self, name: str, detail: str = "", indeterminate: bool = False) -> Task:
        t = Task(name=name, detail=detail, state=TaskState.RUNNING,
                 progress=-1 if indeterminate else 0)
        self._tasks[t.id] = t
        self.task_added.emit(t)
        return t

    def update(self, task_id: str, progress: int = -1,
               detail: str = "", state: TaskState = None):
        t = self._tasks.get(task_id)
        if not t:
            return
        if progress >= 0:
            t.progress = min(100, progress)
        if detail:
            t.detail = detail
        if state:
            t.state = state
        self.task_updated.emit(t)

    def done(self, task_id: str, result: str = "", failed: bool = False):
        t = self._tasks.get(task_id)
        if not t:
            return
        t.state   = TaskState.FAILED if failed else TaskState.DONE
        t.ended   = datetime.now()
        t.result  = result
        t.progress= 100 if not failed else t.progress
        self.task_done.emit(t)
        self.task_updated.emit(t)

    def fail(self, task_id: str, error: str = ""):
        self.done(task_id, result=error, failed=True)

    def active_tasks(self) -> List[Task]:
        return [t for t in self._tasks.values()
                if t.state in (TaskState.RUNNING, TaskState.PENDING)]

    def recent_tasks(self, n: int = 20) -> List[Task]:
        return sorted(self._tasks.values(),
                       key=lambda t: t.started, reverse=True)[:n]

    def clear_done(self):
        self._tasks = {k: v for k, v in self._tasks.items()
                       if v.state == TaskState.RUNNING}


# ── Task row widget ────────────────────────────────────────────────

_STATE_COLORS = {
    TaskState.PENDING:  COLORS["text_muted"],
    TaskState.RUNNING:  COLORS["neon_blue"],
    TaskState.DONE:     COLORS["neon_green"],
    TaskState.FAILED:   COLORS["neon_red"],
    TaskState.CANCELED: COLORS["text_dim"],
}

_STATE_ICONS = {
    TaskState.PENDING:  "⬜",
    TaskState.RUNNING:  "⟳",
    TaskState.DONE:     "✅",
    TaskState.FAILED:   "❌",
    TaskState.CANCELED: "⊘",
}


class TaskRow(QWidget):
    """Single task progress row."""

    def __init__(self, task: Task, parent=None):
        super().__init__(parent)
        self._task = task
        self._spin = 0
        self.setFixedHeight(48)
        self.setStyleSheet(
            f"background:{COLORS['bg_card']};border-bottom:1px solid {COLORS['border']};"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 4, 10, 4)
        lay.setSpacing(8)

        self._icon_lbl  = QLabel("⬜")
        self._icon_lbl.setFixedWidth(18)
        self._icon_lbl.setStyleSheet("font-size:13px;")

        info_col = QVBoxLayout()
        info_col.setSpacing(0)
        self._name_lbl   = QLabel(task.name)
        self._name_lbl.setStyleSheet(
            f"color:{COLORS['text_primary']};font-size:11px;font-weight:bold;"
        )
        self._detail_lbl = QLabel(task.detail)
        self._detail_lbl.setStyleSheet(
            f"color:{COLORS['text_muted']};font-size:9px;"
        )
        info_col.addWidget(self._name_lbl)
        info_col.addWidget(self._detail_lbl)

        self._bar = QProgressBar()
        self._bar.setFixedWidth(140)
        self._bar.setFixedHeight(6)
        self._bar.setTextVisible(False)
        self._bar.setStyleSheet(f"""
            QProgressBar {{
                background:{COLORS['bg_void']};
                border:none;
                border-radius:3px;
            }}
            QProgressBar::chunk {{
                background:{COLORS['neon_blue']};
                border-radius:3px;
            }}
        """)

        self._time_lbl = QLabel("0s")
        self._time_lbl.setFixedWidth(44)
        self._time_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._time_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:9px;")

        lay.addWidget(self._icon_lbl)
        lay.addLayout(info_col, 1)
        lay.addWidget(self._bar)
        lay.addWidget(self._time_lbl)

        self._spinner_chars = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._update(task)

    def _update(self, task: Task):
        self._task = task
        color = _STATE_COLORS.get(task.state, COLORS["text_muted"])
        icon  = _STATE_ICONS.get(task.state, "?")

        if task.state == TaskState.RUNNING:
            self._spin = (self._spin + 1) % len(self._spinner_chars)
            icon = self._spinner_chars[self._spin]

        self._icon_lbl.setText(icon)
        self._icon_lbl.setStyleSheet(f"font-size:13px;color:{color};")
        self._name_lbl.setStyleSheet(
            f"color:{color};font-size:11px;font-weight:bold;"
        )
        if task.detail:
            self._detail_lbl.setText(task.detail[:80])

        if task.progress < 0:
            # Indeterminate
            self._bar.setRange(0, 0)
        else:
            self._bar.setRange(0, 100)
            self._bar.setValue(task.progress)

        # Color chunk by state
        chunk_color = {
            TaskState.DONE:    COLORS["neon_green"],
            TaskState.FAILED:  COLORS["neon_red"],
            TaskState.RUNNING: COLORS["neon_blue"],
        }.get(task.state, COLORS["text_muted"])
        self._bar.setStyleSheet(
            self._bar.styleSheet().replace(
                f"background:{COLORS['neon_blue']};",
                f"background:{chunk_color};",
            )
        )

        self._time_lbl.setText(task.elapsed_str)

    def refresh(self, task: Task):
        self._update(task)


class ProgressPanel(QWidget):
    """Dockable progress panel — shows all running and recent tasks."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._tracker   = TaskTracker.get()
        self._rows:     Dict[str, TaskRow] = {}
        self._build()
        self._connect()

        # Spinner animation
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(120)

        # Auto-refresh
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._refresh_times)
        self._refresh_timer.start(1000)

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        # Header
        hdr = QWidget()
        hdr.setFixedHeight(30)
        hdr.setStyleSheet(
            f"background:{COLORS['bg_void']};border-bottom:1px solid {COLORS['border']};"
        )
        hl  = QHBoxLayout(hdr)
        hl.setContentsMargins(8, 4, 8, 4)
        title = QLabel("⚡ ACTIVITY")
        title.setStyleSheet(
            f"color:{COLORS['neon_blue']};font-size:9px;font-weight:bold;letter-spacing:1px;"
        )
        self._count_lbl = QLabel("")
        self._count_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:9px;")
        from src.gui.widgets.common import ghost_btn
        clear_btn = ghost_btn("✕")
        clear_btn.setFixedSize(20, 18)
        clear_btn.clicked.connect(self._clear_done)
        hl.addWidget(title)
        hl.addWidget(self._count_lbl, 1)
        hl.addWidget(clear_btn)
        lay.addWidget(hdr)

        # Scrollable task list
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{background:{COLORS['bg_dark']};border:none;}}"
            f"QScrollBar:vertical {{background:{COLORS['bg_void']};width:4px;}}"
            f"QScrollBar::handle:vertical {{background:{COLORS['border']};}}"
        )
        self._container = QWidget()
        self._container.setStyleSheet(f"background:{COLORS['bg_dark']};")
        self._vlay = QVBoxLayout(self._container)
        self._vlay.setContentsMargins(0, 0, 0, 0)
        self._vlay.setSpacing(0)
        self._vlay.addStretch()
        scroll.setWidget(self._container)
        lay.addWidget(scroll, 1)

    def _connect(self):
        self._tracker.task_added.connect(self._on_added)
        self._tracker.task_updated.connect(self._on_updated)
        self._tracker.task_done.connect(self._on_updated)   # reuse same handler

    def _on_added(self, task: Task):
        row = TaskRow(task)
        self._rows[task.id] = row
        # Insert before stretch
        count = self._vlay.count()
        self._vlay.insertWidget(count - 1, row)
        self._update_count()

    def _on_updated(self, task: Task):
        row = self._rows.get(task.id)
        if row:
            row.refresh(task)
        self._update_count()

    def _tick(self):
        """Animate spinner for running tasks."""
        for task_id, row in self._rows.items():
            t = self._tracker._tasks.get(task_id)
            if t and t.state == TaskState.RUNNING:
                row.refresh(t)

    def _refresh_times(self):
        for task_id, row in self._rows.items():
            t = self._tracker._tasks.get(task_id)
            if t:
                row._time_lbl.setText(t.elapsed_str)

    def _clear_done(self):
        done_ids = [tid for tid, t in self._tracker._tasks.items()
                    if t.state in (TaskState.DONE, TaskState.FAILED)]
        for tid in done_ids:
            row = self._rows.pop(tid, None)
            if row:
                self._vlay.removeWidget(row)
                row.deleteLater()
        self._tracker.clear_done()
        self._update_count()

    def _update_count(self):
        active = len(self._tracker.active_tasks())
        total  = len(self._tracker._tasks)
        if active:
            self._count_lbl.setText(f"{active} running / {total} total")
            self._count_lbl.setStyleSheet(f"color:{COLORS['neon_yellow']};font-size:9px;")
        else:
            self._count_lbl.setText(f"{total} tasks")
            self._count_lbl.setStyleSheet(f"color:{COLORS['text_muted']};font-size:9px;")
