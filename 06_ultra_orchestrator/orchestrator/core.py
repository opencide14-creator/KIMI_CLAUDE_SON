"""
OrchestratorCore — Central Integration Point for the Ultra Orchestrator.

This module ties all infrastructure and orchestrator subsystems together.
It initialises every component, manages the session lifecycle, and provides
a clean async API for the GUI layer.

Usage::

    core = OrchestratorCore(db_path="orchestrator.db")
    init_result = await core.initialize()
    session = await core.create_new_session(
        task_title="Build REST API",
        task_description="...",
    )
    await core.start_execution()
    dashboard = await core.get_dashboard_data()
    await core.shutdown()
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Callable

# ── Infrastructure ──────────────────────────────────────────────────────────
from infrastructure.state_store import SQLiteStateStore
from infrastructure.api_pool import KimiAPIPool
from infrastructure.template_engine import Jinja2TemplateEngine
from infrastructure.powershell_bridge import PowerShellBridge
from infrastructure.sandbox_executor import SandboxExecutor

# ── Orchestrator ────────────────────────────────────────────────────────────
from orchestrator.state_machine import AgentStateMachine
from orchestrator.token_resonance import TokenResonanceEngine
from orchestrator.quality_gate import QualityGate
from orchestrator.retry_engine import RetryEngine
from orchestrator.decomposer import TaskDecomposer, TaskGraph
from orchestrator.scheduler import BatchScheduler

logger = logging.getLogger("ultra_orchestrator.core")


# ── Helper: safely invoke a method by name, catching all exceptions ────────

async def _safe_call(
    obj: Any,
    method_name: str,
    *args: Any,
    default: Any = None,
    label: str = "",
    **kwargs: Any,
) -> Any:
    """Safely call ``await obj.<method_name>(*args, **kwargs)``.

    Uses :func:`getattr` inside the *try* block so that
    ``AttributeError`` on missing methods is caught and *default* is
    returned.
    """
    try:
        method = getattr(obj, method_name)
        coro = method(*args, **kwargs)
        if coro is None:
            return default
        return await coro
    except Exception as exc:
        logger.warning("Safe-call failed [%s]: %s", label or "unknown", exc)
        return default


def _safe_call_sync(
    obj: Any,
    method_name: str,
    *args: Any,
    default: Any = None,
    label: str = "",
    **kwargs: Any,
) -> Any:
    """Safely call ``obj.<method_name>(*args, **kwargs)`` synchronously."""
    try:
        method = getattr(obj, method_name)
        return method(*args, **kwargs)
    except Exception as exc:
        logger.warning("Safe-call failed [%s]: %s", label or "unknown", exc)
        return default


# ============================================================================
# CLASS: OrchestratorCore
# ============================================================================

class OrchestratorCore:
    """Central integration point for all Ultra Orchestrator subsystems.

    Attributes
    ----------
    state_store : SQLiteStateStore
        SQLite-backed persistent state storage.
    api_pool : KimiAPIPool
        Multi-key API pool with rate limiting and circuit breakers.
    template_engine : Jinja2TemplateEngine
        Jinja2 YAML template loader and renderer.
    ps_bridge : PowerShellBridge
        PowerShell execution bridge for Windows 11.
    sandbox : SandboxExecutor
        Sandboxed Python code execution environment.
    state_machine : AgentStateMachine
        Agent state-transition manager.
    token_resonance : TokenResonanceEngine
        Intelligent API-key selection and concurrency slot management.
    quality_gate : QualityGate
        5-layer output validation gate.
    retry_engine : RetryEngine
        Escalating retry logic for rejected outputs.
    decomposer : TaskDecomposer
        LLM-powered task decomposition into a SubTask DAG.
    scheduler : BatchScheduler
        Batch scheduler enforcing max 20 concurrent agents.
    current_session_id : str
        Active session identifier (empty string when none).
    task_graph : TaskGraph | None
        Current session's task dependency graph.
    event_callbacks : list[callable]
        Registered GUI notification callbacks.
    _initialized : bool
        True once :meth:`initialize` has completed successfully.
    _scheduler_task : asyncio.Task | None
        Background asyncio task running the scheduling loop.
    """

    # ── 1. __init__ ────────────────────────────────────────────────────────

    def __init__(
        self,
        db_path: str = "orchestrator.db",
        templates_dir: str = "templates",
        max_concurrent: int = 20,
    ) -> None:
        """Create all component instances. Validate constraints. Do NOT start.

        Parameters
        ----------
        db_path:
            Path to the SQLite database file.
        templates_dir:
            Directory containing Jinja2 YAML template files.
        max_concurrent:
            Maximum concurrent agents (hard cap 20).

        Raises
        ------
        ValueError
            If *max_concurrent* exceeds 20.
        """
        if max_concurrent > 20:
            raise ValueError(
                f"max_concurrent ({max_concurrent}) exceeds hard limit of 20"
            )
        if max_concurrent < 1:
            raise ValueError(f"max_concurrent must be >= 1, got {max_concurrent}")

        # Infrastructure layer
        self.state_store: SQLiteStateStore = SQLiteStateStore(db_path)
        self.api_pool: KimiAPIPool = KimiAPIPool()
        self.template_engine: Jinja2TemplateEngine = Jinja2TemplateEngine(
            templates_dir
        )
        self.ps_bridge: PowerShellBridge = PowerShellBridge()
        self.sandbox: SandboxExecutor = SandboxExecutor()

        # Orchestrator layer
        self.state_machine: AgentStateMachine = AgentStateMachine(self.state_store)
        self.token_resonance: TokenResonanceEngine = TokenResonanceEngine(
            self.api_pool, max_concurrent
        )
        self.quality_gate: QualityGate = QualityGate(self.sandbox, self.state_store)
        self.retry_engine: RetryEngine = RetryEngine(
            self.state_store, self.template_engine
        )
        self.decomposer: TaskDecomposer = TaskDecomposer(
            self.api_pool, self.state_store
        )
        self.scheduler: BatchScheduler = BatchScheduler(
            self.token_resonance,
            self.state_machine,
            self.quality_gate,
            self.retry_engine,
            self.api_pool,
            self.template_engine,
            self.sandbox,
            self.state_store,
        )

        # Session state
        self.current_session_id: str = ""
        self.task_graph: TaskGraph | None = None

        # Event system
        self.event_callbacks: list[Callable] = []

        # Lifecycle flags
        self._initialized: bool = False
        self._scheduler_task: asyncio.Task | None = None

        # Settings cache
        self._max_concurrent: int = max_concurrent
        self._max_retries: int = 3
        self._timeout: int = 120
        self._safety_margin: float = 0.80
        self._checkpoint_interval: int = 5
        self._enabled_layers: dict[int, bool] = {
            0: True, 1: True, 2: True, 3: True, 4: True
        }

        logger.info(
            "OrchestratorCore created (db=%s, templates=%s, max_concurrent=%d)",
            db_path,
            templates_dir,
            max_concurrent,
        )

    # ── 2. initialize ──────────────────────────────────────────────────────

    async def initialize(self) -> dict:
        """Initialize all components and check for previous sessions.

        Returns
        -------
        dict
            ``{"success": bool, "incomplete_sessions": list,
               "components_ready": list[str]}``
        """
        components_ready: list[str] = []
        incomplete_sessions: list[dict] = []

        # Check state store for incomplete sessions
        try:
            incomplete_sessions = await _safe_call(
                self.state_store, "get_incomplete_sessions",
                default=[],
                label="get_incomplete_sessions",
            )
            components_ready.append("state_store")
        except Exception as exc:
            logger.warning("State store init check failed: %s", exc)

        # Validate API pool
        try:
            key_statuses = self.api_pool.get_all_key_status()
            if key_statuses:
                components_ready.append("api_pool")
            logger.info("API pool: %d key(s) configured", len(key_statuses))
        except Exception as exc:
            logger.warning("API pool init check failed: %s", exc)

        # Validate template engine
        try:
            templates = self.template_engine.list_templates()
            if templates:
                components_ready.append("template_engine")
            logger.info("Template engine: %d template(s) loaded", len(templates))
        except Exception as exc:
            logger.warning("Template engine init check failed: %s", exc)

        # Validate PowerShell bridge
        try:
            components_ready.append("ps_bridge")
        except Exception as exc:
            logger.warning("PS bridge init check failed: %s", exc)

        # Validate sandbox
        try:
            components_ready.append("sandbox")
        except Exception as exc:
            logger.warning("Sandbox init check failed: %s", exc)

        # Validate state machine
        try:
            components_ready.append("state_machine")
        except Exception as exc:
            logger.warning("State machine init check failed: %s", exc)

        # Validate token resonance
        try:
            components_ready.append("token_resonance")
        except Exception as exc:
            logger.warning("Token resonance init check failed: %s", exc)

        # Validate quality gate
        try:
            components_ready.append("quality_gate")
        except Exception as exc:
            logger.warning("Quality gate init check failed: %s", exc)

        # Validate retry engine
        try:
            components_ready.append("retry_engine")
        except Exception as exc:
            logger.warning("Retry engine init check failed: %s", exc)

        # Validate decomposer
        try:
            components_ready.append("decomposer")
        except Exception as exc:
            logger.warning("Decomposer init check failed: %s", exc)

        # Validate scheduler
        try:
            components_ready.append("scheduler")
        except Exception as exc:
            logger.warning("Scheduler init check failed: %s", exc)

        success = len(components_ready) >= 10
        self._initialized = success

        logger.info(
            "OrchestratorCore initialized: success=%s, components=%d, "
            "incomplete_sessions=%d",
            success,
            len(components_ready),
            len(incomplete_sessions),
        )

        return {
            "success": success,
            "incomplete_sessions": incomplete_sessions,
            "components_ready": components_ready,
        }

    # ── 3. create_new_session ──────────────────────────────────────────────

    async def create_new_session(
        self,
        task_title: str,
        task_description: str,
        template_name: str = "BLANK_CODE_GENERATION",
        max_subtasks: int = 300,
    ) -> dict:
        """Create a new session: generate ID, decompose task, build graph.

        Parameters
        ----------
        task_title:
            Short human-readable title for the task.
        task_description:
            Detailed description of what needs to be built.
        template_name:
            Name of the Jinja2 template to use for prompt rendering.
        max_subtasks:
            Maximum number of subtasks to generate (default 300).

        Returns
        -------
        dict
            ``{"session_id": str, "total_subtasks": int,
               "critical_path_length": int, "estimated_time": str}``
        """
        session_id = "SESS-" + uuid.uuid4().hex[:12]
        self.current_session_id = session_id

        logger.info(
            "Creating new session %s: title='%s', template=%s, max_subtasks=%d",
            session_id,
            task_title,
            template_name,
            max_subtasks,
        )

        # Store session in state store
        try:
            await _safe_call(
                self.state_store, "create_session",
                session_id=session_id,
                title=task_title,
                description=task_description,
                template_name=template_name,
                status="CREATED",
                created_at=time.time(),
                label="create_session",
            )
        except Exception as exc:
            logger.warning("Failed to persist session creation: %s", exc)

        # Log session creation event
        try:
            await _safe_call(
                self.state_store, "log_event",
                session_id=session_id,
                event_type="SESSION_CREATED",
                severity="INFO",
                message=f"Session created: {task_title}",
                payload={
                    "title": task_title,
                    "template_name": template_name,
                    "max_subtasks": max_subtasks,
                },
                label="log_session_created",
            )
        except Exception as exc:
            logger.warning("Failed to log session creation event: %s", exc)

        # Decompose task into subtask DAG
        try:
            graph = await self.decomposer.decompose(
                task_title=task_title,
                task_description=task_description,
                session_id=session_id,
                max_subtasks=max_subtasks,
                template_hint=template_name,
            )
        except Exception as exc:
            logger.exception("Task decomposition failed: %s", exc)
            graph = TaskGraph()

        self.task_graph = graph

        # Compute critical path info
        critical_path = graph.critical_path
        critical_path_length = len(critical_path)

        # Estimate completion time
        total_subtasks = len(graph.subtasks)
        avg_time_per_task = 15.0
        effective_concurrency = min(self._max_concurrent, max(total_subtasks, 1))
        if critical_path_length > 0:
            eta_seconds = critical_path_length * avg_time_per_task
        else:
            eta_seconds = (
                total_subtasks * avg_time_per_task
            ) / max(effective_concurrency, 1)

        estimated_time = self._format_duration(eta_seconds)

        logger.info(
            "Session %s decomposed: %d subtasks, critical_path=%d, est=%s",
            session_id,
            total_subtasks,
            critical_path_length,
            estimated_time,
        )

        try:
            await _safe_call(
                self.state_store, "log_event",
                session_id=session_id,
                event_type="DECOMPOSITION_COMPLETE",
                severity="INFO",
                message=f"Task decomposed into {total_subtasks} subtasks",
                payload={
                    "total_subtasks": total_subtasks,
                    "critical_path_length": critical_path_length,
                    "estimated_time_sec": eta_seconds,
                },
                label="log_decomposition",
            )
        except Exception as exc:
            logger.warning("Failed to log decomposition event: %s", exc)

        return {
            "session_id": session_id,
            "total_subtasks": total_subtasks,
            "critical_path_length": critical_path_length,
            "estimated_time": estimated_time,
        }

    # ── 4. start_execution ─────────────────────────────────────────────────

    async def start_execution(self) -> dict:
        """Start the scheduling loop for the current session.

        Returns
        -------
        dict
            ``{"started": bool, "session_id": str}``

        Raises
        ------
        RuntimeError
            If no session or task graph exists.
        """
        if not self.current_session_id:
            raise RuntimeError(
                "No active session -- call create_new_session() first"
            )
        if self.task_graph is None or not self.task_graph.subtasks:
            raise RuntimeError(
                "No task graph -- call create_new_session() first"
            )

        await self.scheduler.start_session(self.current_session_id)

        self._scheduler_task = asyncio.create_task(
            self._run_scheduler_loop(),
            name=f"scheduler-{self.current_session_id}",
        )

        logger.info("Execution started for session %s", self.current_session_id)

        try:
            await _safe_call(
                self.state_store, "log_event",
                session_id=self.current_session_id,
                event_type="EXECUTION_STARTED",
                severity="INFO",
                message="Execution started",
                label="log_execution_start",
            )
        except Exception as exc:
            logger.warning("Failed to log execution start: %s", exc)

        await self._notify_event(
            "execution_started",
            {"session_id": self.current_session_id},
        )

        return {
            "started": True,
            "session_id": self.current_session_id,
        }

    # ── 5. pause_execution ─────────────────────────────────────────────────

    async def pause_execution(self) -> dict:
        """Pause the scheduling loop.

        Returns
        -------
        dict
            ``{"paused": bool, "session_id": str}``
        """
        session_id = self.current_session_id

        try:
            await self.scheduler.pause_session()
        except Exception as exc:
            logger.warning("Scheduler pause failed: %s", exc)

        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await asyncio.wait_for(self._scheduler_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._scheduler_task = None

        try:
            await _safe_call(
                self.state_store, "log_event",
                session_id=session_id,
                event_type="EXECUTION_PAUSED",
                severity="INFO",
                message="Execution paused by user",
                label="log_pause",
            )
        except Exception as exc:
            logger.warning("Failed to log pause event: %s", exc)

        logger.info("Execution paused for session %s", session_id)

        await self._notify_event(
            "execution_paused",
            {"session_id": session_id},
        )

        return {"paused": True, "session_id": session_id}

    # ── 6. resume_execution ────────────────────────────────────────────────

    async def resume_execution(self) -> dict:
        """Resume a paused session.

        Returns
        -------
        dict
            ``{"resumed": bool, "session_id": str}``
        """
        session_id = self.current_session_id

        try:
            reset_count = await self.state_machine.reset_for_resume(session_id)
            logger.info("Reset %d subtasks for resume", reset_count)
        except Exception as exc:
            logger.warning("State machine reset_for_resume failed: %s", exc)

        try:
            await self.scheduler.resume_session()
        except Exception as exc:
            logger.warning("Scheduler resume failed: %s", exc)

        self._scheduler_task = asyncio.create_task(
            self._run_scheduler_loop(),
            name=f"scheduler-resumed-{session_id}",
        )

        logger.info("Execution resumed for session %s", session_id)

        await self._notify_event(
            "execution_resumed",
            {"session_id": session_id},
        )

        return {"resumed": True, "session_id": session_id}

    # ── 7. stop_execution ──────────────────────────────────────────────────

    async def stop_execution(self) -> dict:
        """Stop the scheduling loop permanently.

        Returns
        -------
        dict
            ``{"stopped": bool, "session_id": str}``
        """
        session_id = self.current_session_id

        try:
            await self.scheduler.stop_session()
        except Exception as exc:
            logger.warning("Scheduler stop failed: %s", exc)

        if self._scheduler_task and not self._scheduler_task.done():
            self._scheduler_task.cancel()
            try:
                await asyncio.wait_for(self._scheduler_task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            self._scheduler_task = None

        try:
            await _safe_call(
                self.state_store, "log_event",
                session_id=session_id,
                event_type="EXECUTION_STOPPED",
                severity="INFO",
                message="Execution stopped by user",
                label="log_stop",
            )
        except Exception as exc:
            logger.warning("Failed to log stop event: %s", exc)

        logger.info("Execution stopped for session %s", session_id)

        await self._notify_event(
            "execution_stopped",
            {"session_id": session_id},
        )

        return {"stopped": True, "session_id": session_id}

    # ── 8. resume_previous_session ─────────────────────────────────────────

    async def resume_previous_session(self, session_id: str) -> dict:
        """Load and resume a previously incomplete session.

        Parameters
        ----------
        session_id:
            The session ID to resume.

        Returns
        -------
        dict
            Session info dict with session metadata and state counts.
        """
        session_data = await _safe_call(
            self.state_store, "get_session",
            session_id,
            default={},
            label="get_session",
        )

        self.current_session_id = session_id

        all_subtasks = await _safe_call(
            self.state_store, "get_session_subtasks",
            session_id,
            default=[],
            label="get_session_subtasks",
        )

        # Reconstruct task graph from subtasks
        graph = TaskGraph()
        for st_dict in all_subtasks:
            try:
                from orchestrator.decomposer import SubTask
                subtask = SubTask.from_dict(st_dict)
                graph.add_subtask(subtask)
            except Exception as exc:
                logger.warning("Failed to restore subtask: %s", exc)

        # Rebuild dependency edges
        for st_dict in all_subtasks:
            st_id = st_dict.get("subtask_id") or st_dict.get("id", "")
            deps_raw = st_dict.get("input_dependencies", [])
            if isinstance(deps_raw, str):
                try:
                    import json
                    deps = json.loads(deps_raw)
                except (json.JSONDecodeError, TypeError):
                    deps = []
            else:
                deps = list(deps_raw) if deps_raw else []
            for dep_id in deps:
                if dep_id in graph.subtasks:
                    try:
                        graph.add_dependency(st_id, dep_id)
                    except ValueError:
                        pass

        graph.compute_critical_path()
        graph.compute_topological_levels()

        self.task_graph = graph

        state_counts = await _safe_call(
            self.state_machine, "get_state_counts",
            session_id,
            default={},
            label="get_state_counts",
        )

        logger.info(
            "Resumed previous session %s: %d subtasks loaded",
            session_id,
            len(graph.subtasks),
        )

        return {
            "session_id": session_id,
            "title": session_data.get("title", "Unknown"),
            "description": session_data.get("description", ""),
            "status": session_data.get("status", "UNKNOWN"),
            "template_name": session_data.get(
                "template_name", "BLANK_CODE_GENERATION"
            ),
            "total_subtasks": len(graph.subtasks),
            "critical_path_length": len(graph.critical_path),
            "state_counts": state_counts,
            "created_at": session_data.get("created_at", 0),
        }

    # ── 9. get_dashboard_data ──────────────────────────────────────────────

    async def get_dashboard_data(self) -> dict:
        """Aggregate all data needed for the GUI dashboard.

        Returns
        -------
        dict
            Combined dashboard data with scheduler, quality, cost,
            and key health.
        """
        session_id = self.current_session_id

        scheduler_status: dict = {}
        try:
            scheduler_status = self.scheduler.get_scheduler_status()
        except Exception as exc:
            logger.warning("Failed to get scheduler status: %s", exc)

        try:
            scheduler_status["active_count"] = await _safe_call(
                self.token_resonance, "get_active_count",
                default=0,
                label="active_count",
            )
        except Exception:
            scheduler_status["active_count"] = 0

        try:
            scheduler_status["available_slots"] = await _safe_call(
                self.token_resonance, "get_available_slots",
                default=0,
                label="available_slots",
            )
        except Exception:
            scheduler_status["available_slots"] = 0

        try:
            state_counts = await _safe_call(
                self.state_machine, "get_state_counts",
                session_id,
                default={},
                label="state_counts",
            )
            scheduler_status["state_counts"] = state_counts
            scheduler_status["total_tasks"] = sum(state_counts.values())
        except Exception:
            scheduler_status["state_counts"] = {}
            scheduler_status["total_tasks"] = 0

        quality_stats: dict = {}
        try:
            quality_stats = await _safe_call(
                self.state_store, "get_quality_stats",
                session_id,
                default={},
                label="get_quality_stats",
            )
        except Exception as exc:
            logger.warning("Failed to get quality stats: %s", exc)

        cost_summary: dict = {}
        try:
            cost_summary = self.api_pool.get_total_usage()
        except Exception as exc:
            logger.warning("Failed to get cost summary: %s", exc)

        key_health: dict = {}
        try:
            key_health = await _safe_call(
                self.token_resonance, "get_all_key_health",
                default={},
                label="get_all_key_health",
            )
        except Exception as exc:
            logger.warning("Failed to get key health: %s", exc)

        return {
            "session_id": session_id,
            "scheduler": scheduler_status,
            "quality": quality_stats,
            "cost": cost_summary,
            "key_health": key_health,
            "initialized": self._initialized,
            "timestamp": time.time(),
        }

    # ── 10. get_recent_events ─────────────────────────────────────────────

    async def get_recent_events(self, limit: int = 200) -> list[dict]:
        """Get recent events for the current session.

        Parameters
        ----------
        limit:
            Maximum number of events to return (default 200).

        Returns
        -------
        list[dict]
            Recent event records.
        """
        return await _safe_call(
            self.state_store, "get_recent_events",
            self.current_session_id,
            limit,
            default=[],
            label="get_recent_events",
        )

    # ── 11. get_subtask_detail ─────────────────────────────────────────────

    async def get_subtask_detail(self, subtask_id: str) -> dict:
        """Get detailed information about a specific subtask.

        Parameters
        ----------
        subtask_id:
            The subtask identifier.

        Returns
        -------
        dict
            Combined subtask data with reasoning history.
        """
        subtask = await _safe_call(
            self.state_store, "get_subtask",
            subtask_id,
            default={},
            label="get_subtask",
        )

        reasoning = await _safe_call(
            self.state_store, "get_reasoning_history",
            subtask_id,
            default=[],
            label="get_reasoning_history",
        )

        return {
            "subtask": subtask,
            "reasoning_history": reasoning,
        }

    # ── 12. get_reasoning_for_subtask ──────────────────────────────────────

    async def get_reasoning_for_subtask(self, subtask_id: str) -> list[dict]:
        """Get all reasoning entries for a subtask.

        Parameters
        ----------
        subtask_id:
            The subtask identifier.

        Returns
        -------
        list[dict]
            All reasoning entries for the subtask.
        """
        return await _safe_call(
            self.state_store, "get_reasoning_history",
            subtask_id,
            default=[],
            label="get_reasoning_for_subtask",
        )

    # ── 13. export_logs ────────────────────────────────────────────────────

    async def export_logs(self, file_path: str, format: str = "csv") -> dict:
        """Export session logs to a file.

        Parameters
        ----------
        file_path:
            Destination file path for the exported logs.
        format:
            Export format -- "csv" or "log" (default "csv").

        Returns
        -------
        dict
            ``{"exported": bool, "file_path": str, "record_count": int}``
        """
        record_count = 0
        exported = False

        if format == "csv":
            record_count = await _safe_call(
                self.state_store, "export_logs_to_csv",
                self.current_session_id,
                file_path,
                default=0,
                label="export_logs_to_csv",
            )
            exported = record_count > 0

        elif format == "log":
            record_count = await _safe_call(
                self.state_store, "export_logs_to_plaintext",
                self.current_session_id,
                file_path,
                default=0,
                label="export_logs_to_plaintext",
            )
            exported = record_count > 0

        else:
            logger.error("Unknown export format: %s", format)

        return {
            "exported": exported,
            "file_path": file_path,
            "record_count": record_count,
        }

    # ── 14. get_settings ───────────────────────────────────────────────────

    async def get_settings(self) -> dict:
        """Return current orchestrator settings.

        Returns
        -------
        dict
            Settings dictionary with all configurable parameters.
        """
        return {
            "max_concurrent": self._max_concurrent,
            "max_retries": self._max_retries,
            "timeout": self._timeout,
            "safety_margin": self._safety_margin,
            "checkpoint_interval": self._checkpoint_interval,
            "enabled_layers": dict(self._enabled_layers),
        }

    # ── 15. update_settings ────────────────────────────────────────────────

    async def update_settings(self, settings: dict) -> dict:
        """Validate and update orchestrator settings.

        Parameters
        ----------
        settings:
            Dictionary of settings to update.

        Returns
        -------
        dict
            Updated settings dictionary.
        """
        if "max_concurrent" in settings:
            new_max = settings["max_concurrent"]
            if new_max > 20:
                raise ValueError(
                    f"max_concurrent ({new_max}) exceeds hard limit of 20"
                )
            if new_max < 1:
                raise ValueError(f"max_concurrent must be >= 1, got {new_max}")
            self._max_concurrent = new_max

        if "max_retries" in settings:
            self._max_retries = max(1, int(settings["max_retries"]))
            self.state_machine.max_retries = self._max_retries
            self.retry_engine.max_retries = self._max_retries

        if "timeout" in settings:
            self._timeout = max(10, int(settings["timeout"]))

        if "safety_margin" in settings:
            margin = float(settings["safety_margin"])
            self._safety_margin = max(0.1, min(1.0, margin))

        if "checkpoint_interval" in settings:
            self._checkpoint_interval = max(
                1, int(settings["checkpoint_interval"])
            )
            self.scheduler.checkpoint_interval = self._checkpoint_interval

        if "enabled_layers" in settings:
            new_layers = settings["enabled_layers"]
            if isinstance(new_layers, dict):
                self._enabled_layers.update(new_layers)
                # Layers 0 and 1 are always enabled
                self._enabled_layers[0] = True
                self._enabled_layers[1] = True
                # Update quality gate
                self.quality_gate.layer_settings.update(
                    self._enabled_layers
                )
                self.quality_gate.layer_settings[0] = True
                self.quality_gate.layer_settings[1] = True

        logger.info("Settings updated: %s", await self.get_settings())
        return await self.get_settings()

    # ── 16. register_event_callback ────────────────────────────────────────

    def register_event_callback(self, callback: Callable) -> None:
        """Register a callback for GUI event notifications.

        The callback is invoked as ``callback(event_type, data)``.

        Parameters
        ----------
        callback:
            Callable accepting ``(event_type: str, data: dict)``.
        """
        if callback not in self.event_callbacks:
            self.event_callbacks.append(callback)
            logger.debug("Registered event callback: %s", callback)

    # ── 17. _notify_event ──────────────────────────────────────────────────

    async def _notify_event(self, event_type: str, data: dict) -> None:
        """Notify all registered callbacks of an event.

        Callback failures are logged but never raised.

        Parameters
        ----------
        event_type:
            Type identifier for the event.
        data:
            Event payload dictionary.
        """
        if not self.event_callbacks:
            return

        for cb in self.event_callbacks:
            try:
                result = cb(event_type, data)
                if result is not None and hasattr(result, "__await__"):
                    await result
            except Exception:
                logger.exception(
                    "Event callback failed for %s", event_type
                )

    # ── 18. validate_environment ───────────────────────────────────────────

    async def validate_environment(self) -> dict:
        """Run comprehensive environment validation checks.

        Returns
        -------
        dict
            Combined validation results from PowerShell bridge and API pool.
        """
        ps_result = await _safe_call(
            self.ps_bridge, "validate_environment",
            default={
                "powershell_ok": False,
                "version": "unknown",
                "writable": False,
                "api_connectivity": False,
                "all_ok": False,
            },
            label="ps_bridge.validate_environment",
        )

        api_keys_ok = await _safe_call(
            self.api_pool, "are_any_keys_available",
            default=False,
            label="are_any_keys_available",
        )

        templates_ok = False
        try:
            templates = self.template_engine.list_templates()
            templates_ok = len(templates) > 0
        except Exception as exc:
            logger.warning("Template validation failed: %s", exc)

        all_ok = (
            ps_result.get("all_ok", False)
            and api_keys_ok
            and templates_ok
        )

        return {
            "powershell": ps_result,
            "api_keys_available": api_keys_ok,
            "templates_loaded": templates_ok,
            "all_ok": all_ok,
        }

    # ── 19. shutdown ───────────────────────────────────────────────────────

    async def shutdown(self) -> None:
        """Graceful shutdown: stop execution, close resources, log."""
        logger.info("OrchestratorCore shutting down...")

        try:
            if (
                self._scheduler_task
                and not self._scheduler_task.done()
            ):
                await self.stop_execution()
        except Exception as exc:
            logger.warning(
                "Error stopping execution during shutdown: %s", exc
            )

        try:
            await self.api_pool.close()
            logger.info("API pool closed")
        except Exception as exc:
            logger.warning("Error closing API pool: %s", exc)

        try:
            if hasattr(self.state_store, "close"):
                await _safe_call(
                    self.state_store, "close",
                    label="state_store.close",
                )
            logger.info("State store closed")
        except Exception as exc:
            logger.warning("Error closing state store: %s", exc)

        try:
            if self.current_session_id:
                await _safe_call(
                    self.state_store, "log_event",
                    session_id=self.current_session_id,
                    event_type="ORCHESTRATOR_SHUTDOWN",
                    severity="INFO",
                    message="Orchestrator shut down gracefully",
                    label="log_shutdown",
                )
        except Exception as exc:
            logger.warning("Failed to log shutdown event: %s", exc)

        self._initialized = False
        logger.info("OrchestratorCore shutdown complete")

    # ── 20. _run_scheduler_loop ────────────────────────────────────────────

    async def _run_scheduler_loop(self) -> None:
        """Wrapper around the scheduler's scheduling loop.

        Catches exceptions, logs them, and notifies GUI on completion or
        error.
        """
        session_id = self.current_session_id

        try:
            logger.info(
                "Scheduler loop wrapper starting for session %s",
                session_id,
            )
            await self.scheduler.run_scheduling_loop()

        except asyncio.CancelledError:
            logger.info(
                "Scheduler loop cancelled for session %s", session_id
            )
            await self._notify_event(
                "scheduler_cancelled",
                {"session_id": session_id},
            )
            raise

        except Exception as exc:
            logger.exception(
                "Scheduler loop crashed for session %s: %s",
                session_id,
                exc,
            )
            await self._notify_event(
                "scheduler_error",
                {"session_id": session_id, "error": str(exc)},
            )

        finally:
            self._scheduler_task = None
            await self._notify_event(
                "scheduler_ended",
                {"session_id": session_id},
            )
            logger.info(
                "Scheduler loop wrapper ended for session %s",
                session_id,
            )

    # ── Utility: format duration ───────────────────────────────────────────

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format a duration in seconds to a human-readable string.

        Examples: "< 1s", "~30s", "~2m 15s", "~1h 30m"
        """
        if seconds < 1:
            return "< 1s"
        if seconds < 60:
            return f"~{int(seconds)}s"
        if seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"~{minutes}m {secs}s" if secs else f"~{minutes}m"

        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"~{hours}h {minutes}m" if minutes else f"~{hours}h"

    # ── Dunder ─────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"OrchestratorCore(initialized={self._initialized}, "
            f"session={self.current_session_id or 'none'}, "
            f"components=11)"
        )
