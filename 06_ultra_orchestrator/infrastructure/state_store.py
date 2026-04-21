#!/usr/bin/env python3
"""
================================================================================
                        ORCHESTRATOR STATE STORE
                      Async SQLite Persistence Layer
================================================================================

Production-grade async SQLite persistence layer for the Ultra-Orchestrator.
Provides durable storage for orchestration events, subtask lifecycle tracking,
agent reasoning logs, command history, and session management.

ARCHITECTURE
------------
- ThreadPoolExecutor-based async pattern (no external async-SQLite dependency)
- Thread-local SQLite connections for concurrent safety
- WAL journal mode for improved concurrency
- Row factory returning dict-like sqlite3.Row objects
- Full JSON serialization/deserialization for structured columns
- Comprehensive error handling with structured logging throughout

SCHEMA OVERVIEW
---------------
orchestrator_events : Audit trail of all orchestration events
subtask_states      : Full lifecycle tracking of each subtask
agent_reasoning     : Agent thinking and response logs per attempt
command_history     : CLI and programmatic command audit trail
sessions            : Top-level session metadata and aggregates

DESIGN DECISIONS
----------------
1. ThreadPoolExecutor with max_workers=1 serializes all DB access, eliminating
   lock contention while preserving async compatibility.
2. check_same_thread=False allows the single worker thread to reuse connections.
3. threading.local() provides thread-isolated connection objects.
4. All JSON columns are serialized on write and deserialized on read.
5. Dynamic UPDATE builders only touch columns with non-None values.
6. Every method includes full docstrings, type hints, and error handling.
================================================================================
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import sqlite3
import textwrap
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# ==============================================================================
#                            DATABASE SCHEMA
# ==============================================================================

SCHEMA_SQL: str = """
-- ---------------------------------------------------------------------------
--  ORCHESTRATOR EVENTS: Audit trail of every significant orchestration event
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orchestrator_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    event_type  TEXT NOT NULL,
    severity    TEXT NOT NULL,
    subtask_id  TEXT,
    api_key_id  TEXT,
    message     TEXT NOT NULL,
    payload     TEXT,
    duration_ms INTEGER
);

-- ---------------------------------------------------------------------------
--  SUBTASK STATES: Full lifecycle tracking for each individual subtask
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subtask_states (
    subtask_id          TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT NOT NULL,
    acceptance_criteria TEXT NOT NULL,
    status              TEXT NOT NULL,
    priority            TEXT NOT NULL,
    retry_count         INTEGER DEFAULT 0,
    created_at          REAL NOT NULL,
    started_at          REAL,
    completed_at        REAL,
    assigned_key        TEXT,
    tokens_used         INTEGER,
    cost_usd            REAL,
    output_text         TEXT,
    rejection_reasons   TEXT,
    reasoning_text      TEXT
);

-- ---------------------------------------------------------------------------
--  AGENT REASONING: Structured reasoning and response logging per attempt
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_reasoning (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subtask_id  TEXT NOT NULL,
    attempt_no  INTEGER NOT NULL,
    timestamp   REAL NOT NULL,
    thinking    TEXT,
    response    TEXT,
    qg_result   TEXT
);

-- ---------------------------------------------------------------------------
--  COMMAND HISTORY: CLI and programmatic command audit trail
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS command_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    timestamp   REAL NOT NULL,
    command     TEXT NOT NULL,
    source      TEXT NOT NULL,
    result      TEXT
);

-- ---------------------------------------------------------------------------
--  SESSIONS: Top-level session metadata, aggregates, and configuration
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sessions (
    session_id      TEXT PRIMARY KEY,
    started_at      REAL NOT NULL,
    last_active     REAL NOT NULL,
    status          TEXT NOT NULL,
    task_title      TEXT,
    total_subtasks  INTEGER,
    completed_count INTEGER DEFAULT 0,
    total_cost_usd  REAL DEFAULT 0.0,
    config_snapshot TEXT
);

-- ---------------------------------------------------------------------------
--  INDEXES: Performance optimization for common query patterns
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_events_session
    ON orchestrator_events(session_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_events_subtask
    ON orchestrator_events(subtask_id);

CREATE INDEX IF NOT EXISTS idx_subtasks_status
    ON subtask_states(session_id, status);

CREATE INDEX IF NOT EXISTS idx_reasoning_subtask
    ON agent_reasoning(subtask_id);
"""

# ==============================================================================
#                         JSON SERIALIZATION HELPERS
# ==============================================================================


class _JSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling sets and fallback to string representation."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, set):
            return sorted(obj)
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def _serialize_json(value: Any) -> Optional[str]:
    """Serialize a Python object to a JSON string. Returns None if input is None."""
    if value is None:
        return None
    try:
        return json.dumps(value, cls=_JSONEncoder, ensure_ascii=False, indent=None)
    except (TypeError, ValueError) as exc:
        logger.error("JSON serialization failed for type %s: %s", type(value).__name__, exc)
        raise


def _deserialize_json(value: Optional[str], default: Any = None) -> Any:
    """Deserialize a JSON string back to a Python object."""
    if value is None or value == "":
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning("JSON deserialization failed (%s), returning raw string", exc)
        return value


def _deserialize_json_row(
    row: Optional[Dict[str, Any]],
    json_columns: Sequence[str],
    defaults: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Deserialize specified JSON columns in a database row dictionary."""
    if row is None:
        return None
    if defaults is None:
        defaults = {}
    for col in json_columns:
        row[col] = _deserialize_json(row.get(col), default=defaults.get(col))
    return row


def _deserialize_json_rows(
    rows: List[Dict[str, Any]],
    json_columns: Sequence[str],
    defaults: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Deserialize JSON columns in a list of row dictionaries."""
    if defaults is None:
        defaults = {}
    for row in rows:
        for col in json_columns:
            row[col] = _deserialize_json(row.get(col), default=defaults.get(col))
    return rows


# ==============================================================================
#                       SAFE SQL IDENTIFIER HELPERS
# ==============================================================================

_SUBTASK_STATE_COLUMNS: set[str] = {
    "status",
    "assigned_key",
    "tokens_used",
    "cost_usd",
    "output_text",
    "rejection_reasons",
    "reasoning_text",
    "started_at",
    "completed_at",
    "retry_count",
}

_SESSION_COLUMNS: set[str] = {
    "status",
    "completed_count",
    "total_cost_usd",
    "last_active",
}


def _safe_columns(allowed: set[str], provided: Dict[str, Any]) -> Dict[str, Any]:
    """
    Filter a dictionary of column updates to only include allowed keys.
    Prevents accidental SQL injection in dynamic UPDATE queries.
    """
    return {k: v for k, v in provided.items() if k in allowed and v is not None}


# ==============================================================================
#                         SEVERITY / STATUS CONSTANTS
# ==============================================================================

VALID_SEVERITIES: set[str] = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}

VALID_SUBTASK_STATUSES: set[str] = {
    "PENDING", "QUEUED", "RUNNING", "REVIEW",
    "APPROVED", "REJECTED", "BLOCKED", "DEAD_LETTER", "RETRYING",
}

VALID_SESSION_STATUSES: set[str] = {"RUNNING", "PAUSED", "COMPLETED", "FAILED", "ABORTED"}

VALID_EVENT_TYPES: set[str] = {
    "SESSION_START", "SESSION_END", "SUBTASK_CREATED", "SUBTASK_STARTED",
    "SUBTASK_COMPLETED", "SUBTASK_APPROVED", "SUBTASK_REJECTED",
    "SUBTASK_RETRYING", "SUBTASK_BLOCKED", "SUBTASK_DEAD_LETTER",
    "AGENT_CALL", "AGENT_RESPONSE", "AGENT_ERROR",
    "QUALITY_CHECK", "COST_UPDATE", "COMMAND_EXECUTED", "SYSTEM_EVENT",
}


# ==============================================================================
#                         LOG ENTRY FORMATTER
# ==============================================================================


def _format_log_line(row: Dict[str, Any]) -> str:
    """
    Format a single event row into a human-readable log line.
    Format: [ISO_TIMESTAMP] [SEVERITY] [EVENT_TYPE] message (extras)
    """
    ts = row.get("timestamp", 0.0)
    ts_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts))
    f_ts = f"{ts_iso}.{int((ts % 1) * 1000):03d}Z"
    severity = row.get("severity", "UNKNOWN")
    event_type = row.get("event_type", "UNKNOWN")
    message = row.get("message", "")
    subtask_id = row.get("subtask_id")
    api_key_id = row.get("api_key_id")
    duration_ms = row.get("duration_ms")

    extras = []
    if subtask_id:
        extras.append(f"subtask={subtask_id}")
    if api_key_id:
        extras.append(f"key={api_key_id}")
    if duration_ms is not None:
        extras.append(f"dur={duration_ms}ms")

    sep = ", "
    extra_str = f" ({sep.join(extras)})" if extras else ""
    return f"[{f_ts}] [{severity:8s}] [{event_type:20s}] {message}{extra_str}"


# ==============================================================================
#                       CHECKPOINT DATA BUILDER
# ==============================================================================


def _build_checkpoint(
    session_row: Optional[Dict[str, Any]],
    subtask_rows: List[Dict[str, Any]],
    event_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Construct a checkpoint data structure from session, subtask, and event data."""
    if session_row is None:
        session_info = None
    else:
        session_info = {
            "session_id": session_row.get("session_id"),
            "started_at": session_row.get("started_at"),
            "last_active": session_row.get("last_active"),
            "status": session_row.get("status"),
            "task_title": session_row.get("task_title"),
            "total_subtasks": session_row.get("total_subtasks"),
            "completed_count": session_row.get("completed_count"),
            "total_cost_usd": session_row.get("total_cost_usd"),
            "config_snapshot": session_row.get("config_snapshot"),
        }

    return {
        "exported_at": time.time(),
        "session": session_info,
        "subtasks": subtask_rows,
        "recent_events": event_rows,
        "summary": {
            "total_subtasks": len(subtask_rows),
            "approved": sum(1 for s in subtask_rows if s.get("status") == "APPROVED"),
            "rejected": sum(1 for s in subtask_rows if s.get("status") == "REJECTED"),
            "blocked": sum(1 for s in subtask_rows if s.get("status") == "BLOCKED"),
            "dead_letter": sum(1 for s in subtask_rows if s.get("status") == "DEAD_LETTER"),
            "pending": sum(1 for s in subtask_rows if s.get("status") == "PENDING"),
            "running": sum(1 for s in subtask_rows if s.get("status") == "RUNNING"),
            "queued": sum(1 for s in subtask_rows if s.get("status") == "QUEUED"),
        },
    }


# ==============================================================================
#                         SQLITESTATESTORE CLASS
# ==============================================================================


class SQLiteStateStore:
    """
    Production-grade async SQLite persistence layer for the Ultra-Orchestrator.

    Uses a ThreadPoolExecutor-based pattern to provide async database access
    without requiring external async-SQLite dependencies. All public methods
    are ``async`` and can be called from asyncio code.

    Threading model:
        - A single-worker ThreadPoolExecutor serializes all DB operations.
        - Each executor thread gets its own thread-local SQLite connection.
        - WAL journal mode allows readers to proceed during writes.

    JSON handling:
        - Columns storing structured data are JSON-serialized on INSERT/UPDATE.
        - The same columns are JSON-deserialized back on SELECT.

    Usage::

        store = SQLiteStateStore(db_path="/path/to/orchestrator.db")
        await store.create_session("sess_001", "Build a web scraper", 10, {"model": "gpt-4"})
        await store.log_event("sess_001", "SESSION_START", "INFO", "Session initialized")
        await store.close()

    Args:
        db_path: Filesystem path to the SQLite database file.
    """

    _SUBTASK_JSON_COLS: Tuple[str, ...] = ("acceptance_criteria", "rejection_reasons")
    _SUBTASK_JSON_DEFAULTS: Dict[str, Any] = {
        "acceptance_criteria": [],
        "rejection_reasons": [],
    }
    _SESSION_JSON_COLS: Tuple[str, ...] = ("config_snapshot",)
    _SESSION_JSON_DEFAULTS: Dict[str, Any] = {"config_snapshot": {}}
    _REASONING_JSON_COLS: Tuple[str, ...] = ("qg_result",)
    _REASONING_JSON_DEFAULTS: Dict[str, Any] = {"qg_result": None}
    _EVENT_JSON_COLS: Tuple[str, ...] = ("payload",)
    _EVENT_JSON_DEFAULTS: Dict[str, Any] = {"payload": None}

    def __init__(self, db_path: str = "orchestrator.db") -> None:
        """
        Initialize the state store.

        Creates parent directory, initializes schema, enables WAL mode.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path: str = db_path

        db_file = Path(db_path)
        if db_file.parent and not db_file.parent.exists():
            db_file.parent.mkdir(parents=True, exist_ok=True)
            logger.info("Created database parent directory: %s", db_file.parent)

        self._pool: ThreadPoolExecutor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sqlite_"
        )
        self._local: threading.local = threading.local()
        self._init_db()
        logger.info("SQLiteStateStore initialized at %s", self.db_path)

    def _get_conn(self) -> sqlite3.Connection:
        """Get (or create) the thread-local SQLite connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                isolation_level=None,
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("BEGIN")
            logger.debug("Created new thread-local SQLite connection")
        return self._local.conn

    def _init_db(self) -> None:
        """Initialize the database schema and configure performance pragmas."""
        conn: sqlite3.Connection = sqlite3.connect(self.db_path)
        try:
            conn.executescript(SCHEMA_SQL)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA wal_autocheckpoint=1000")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")
            conn.commit()
            logger.info("Database schema initialized successfully")
        except sqlite3.Error as exc:
            logger.error("Failed to initialize database schema: %s", exc)
            raise
        finally:
            conn.close()

    async def _execute(self, sql: str, params: Tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Execute an INSERT, UPDATE, or DELETE asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._pool, self._execute_sync, sql, params)

    def _execute_sync(self, sql: str, params: Tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Synchronous worker for _execute."""
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    async def _fetchall(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """Execute a SELECT and return all rows asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._pool, self._fetchall_sync, sql, params)

    def _fetchall_sync(self, sql: str, params: Tuple[Any, ...] = ()) -> List[Dict[str, Any]]:
        """Synchronous worker for _fetchall."""
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
        return [dict(row) for row in rows]

    async def _fetchone(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        """Execute a SELECT and return the first row asynchronously."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._pool, self._fetchone_sync, sql, params)

    def _fetchone_sync(self, sql: str, params: Tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        """Synchronous worker for _fetchone."""
        conn = self._get_conn()
        cur = conn.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    # ==================================================================
    #  METHOD 1: log_event
    # ==================================================================
    async def log_event(
        self,
        session_id: str,
        event_type: str,
        severity: str,
        message: str,
        subtask_id: Optional[str] = None,
        api_key_id: Optional[str] = None,
        payload: Optional[Any] = None,
        duration_ms: Optional[int] = None,
    ) -> int:
        """
        Record an orchestration event into the audit trail.

        Every significant action in the orchestrator should be logged through
        this method to maintain a complete audit trail.

        Args:
            session_id: Unique session identifier.
            event_type: Categorization of the event.
            severity: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
            message: Human-readable description.
            subtask_id: Optional associated subtask identifier.
            api_key_id: Optional API key identifier.
            payload: Optional structured data (JSON-serialized).
            duration_ms: Optional duration in milliseconds.

        Returns:
            The auto-incremented id of the newly inserted event row.

        Raises:
            ValueError: If session_id or message is empty.
            sqlite3.Error: If the insert fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not message:
            raise ValueError("message is required")

        severity = severity.upper()
        if severity not in VALID_SEVERITIES:
            logger.warning("Unknown severity '%s' for event_type '%s', storing as-is", severity, event_type)

        payload_json = _serialize_json(payload)
        ts = time.time()

        sql = """
            INSERT INTO orchestrator_events
                (session_id, timestamp, event_type, severity, subtask_id,
                 api_key_id, message, payload, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (session_id, ts, event_type, severity, subtask_id, api_key_id, message, payload_json, duration_ms)

        try:
            cur = await self._execute(sql, params)
            event_id = cur.lastrowid
            if cur.rowcount != 1:
                logger.warning("Expected 1 row inserted for event, got %d", cur.rowcount)
            logger.debug("Logged event id=%s type=%s severity=%s session=%s", event_id, event_type, severity, session_id)
            return int(event_id) if event_id is not None else -1
        except sqlite3.Error as exc:
            logger.error("Failed to log event (type=%s, session=%s): %s", event_type, session_id, exc)
            raise

    # ==================================================================
    #  METHOD 2: create_subtask
    # ==================================================================
    async def create_subtask(
        self,
        subtask_id: str,
        session_id: str,
        title: str,
        description: str,
        acceptance_criteria: Any,
        priority: str,
        status: str = "PENDING",
    ) -> None:
        """
        Create a new subtask record in the database.

        Args:
            subtask_id: Globally unique subtask identifier.
            session_id: Parent session identifier.
            title: Short human-readable title.
            description: Detailed description.
            acceptance_criteria: Structured criteria (JSON-serialized).
            priority: Priority level (HIGH, MEDIUM, LOW).
            status: Initial status. Defaults to PENDING.

        Raises:
            ValueError: If required fields are missing.
            sqlite3.IntegrityError: If subtask_id already exists.
            sqlite3.Error: If the insert fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")
        if not session_id:
            raise ValueError("session_id is required")
        if not title:
            raise ValueError("title is required")
        if not description:
            raise ValueError("description is required")

        acceptance_json = _serialize_json(acceptance_criteria)
        ts = time.time()

        sql = """
            INSERT INTO subtask_states
                (subtask_id, session_id, title, description, acceptance_criteria,
                 status, priority, retry_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (subtask_id, session_id, title, description, acceptance_json, status, priority, 0, ts)

        try:
            cur = await self._execute(sql, params)
            if cur.rowcount != 1:
                logger.warning("Expected 1 row inserted for subtask, got %d", cur.rowcount)
            logger.info("Created subtask %s (session=%s, status=%s, priority=%s)", subtask_id, session_id, status, priority)
        except sqlite3.IntegrityError as exc:
            logger.error("Subtask %s already exists in session %s: %s", subtask_id, session_id, exc)
            raise
        except sqlite3.Error as exc:
            logger.error("Failed to create subtask %s (session=%s): %s", subtask_id, session_id, exc)
            raise

    # ==================================================================
    #  METHOD 3: update_subtask_status
    # ==================================================================
    async def update_subtask_status(
        self,
        subtask_id: str,
        status: Optional[str] = None,
        assigned_key: Optional[str] = None,
        tokens_used: Optional[int] = None,
        cost_usd: Optional[float] = None,
        output_text: Optional[str] = None,
        rejection_reasons: Optional[Any] = None,
        reasoning_text: Optional[str] = None,
        started_at: Optional[float] = None,
        completed_at: Optional[float] = None,
        retry_count: Optional[int] = None,
    ) -> int:
        """
        Dynamically update one or more fields of a subtask record.

        Only non-None fields are included in the UPDATE. The rejection_reasons
        parameter is JSON-serialized before storage.

        Args:
            subtask_id: Unique subtask identifier to update.
            status: New lifecycle status.
            assigned_key: API key identifier assigned.
            tokens_used: Cumulative token consumption.
            cost_usd: Cumulative cost in USD.
            output_text: Generated output from agent.
            rejection_reasons: Structured rejection reasons (JSON-serialized).
            reasoning_text: Agent reasoning as text.
            started_at: Unix timestamp when execution began.
            completed_at: Unix timestamp when execution completed.
            retry_count: Current retry attempt count.

        Returns:
            Number of rows updated (should be 1 for valid subtask_id).

        Raises:
            ValueError: If subtask_id is empty.
            sqlite3.Error: If the update fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")

        fields: Dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if assigned_key is not None:
            fields["assigned_key"] = assigned_key
        if tokens_used is not None:
            fields["tokens_used"] = tokens_used
        if cost_usd is not None:
            fields["cost_usd"] = cost_usd
        if output_text is not None:
            fields["output_text"] = output_text
        if rejection_reasons is not None:
            fields["rejection_reasons"] = _serialize_json(rejection_reasons)
        if reasoning_text is not None:
            fields["reasoning_text"] = reasoning_text
        if started_at is not None:
            fields["started_at"] = started_at
        if completed_at is not None:
            fields["completed_at"] = completed_at
        if retry_count is not None:
            fields["retry_count"] = retry_count

        if not fields:
            logger.warning("update_subtask_status called with no fields to update for %s", subtask_id)
            return 0

        safe_fields = _safe_columns(_SUBTASK_STATE_COLUMNS, fields)
        if not safe_fields:
            logger.error("No valid fields to update after filtering for subtask %s", subtask_id)
            return 0

        set_clause = ", ".join(f"{col} = ?" for col in safe_fields)
        values = list(safe_fields.values())
        values.append(subtask_id)

        sql = f"UPDATE subtask_states SET {set_clause} WHERE subtask_id = ?"

        try:
            cur = await self._execute(sql, tuple(values))
            if cur.rowcount == 0:
                logger.warning("No subtask found with id=%s to update", subtask_id)
            else:
                logger.debug("Updated subtask %s fields: %s", subtask_id, ", ".join(safe_fields.keys()))
            return cur.rowcount
        except sqlite3.Error as exc:
            logger.error("Failed to update subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  METHOD 4: get_subtask
    # ==================================================================
    async def get_subtask(self, subtask_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a single subtask record by identifier.

        JSON columns (acceptance_criteria, rejection_reasons) are automatically
        deserialized back to Python objects.

        Args:
            subtask_id: Unique subtask identifier.

        Returns:
            Subtask dictionary with JSON parsed, or None if not found.

        Raises:
            ValueError: If subtask_id is empty.
            sqlite3.Error: If query fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")

        sql = "SELECT * FROM subtask_states WHERE subtask_id = ?"

        try:
            row = await self._fetchone(sql, (subtask_id,))
            return _deserialize_json_row(row, self._SUBTASK_JSON_COLS, self._SUBTASK_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  METHOD 5: get_session_subtasks
    # ==================================================================
    async def get_session_subtasks(
        self, session_id: str, status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all subtasks for a session, optionally filtered by status.

        Args:
            session_id: Session identifier.
            status: Optional status filter.

        Returns:
            List of subtask dictionaries with JSON parsed. Empty list if none.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        if status:
            sql = "SELECT * FROM subtask_states WHERE session_id = ? AND status = ? ORDER BY created_at"
            params: Tuple[Any, ...] = (session_id, status)
        else:
            sql = "SELECT * FROM subtask_states WHERE session_id = ? ORDER BY created_at"
            params = (session_id,)

        try:
            rows = await self._fetchall(sql, params)
            return _deserialize_json_rows(rows, self._SUBTASK_JSON_COLS, self._SUBTASK_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch subtasks for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 6: get_subtasks_by_status
    # ==================================================================
    async def get_subtasks_by_status(
        self, session_id: str, statuses: Sequence[str]
    ) -> List[Dict[str, Any]]:
        """
        Retrieve subtasks filtered by multiple allowed statuses.

        Uses an IN clause for efficient batch filtering.

        Args:
            session_id: Session identifier.
            statuses: Sequence of status strings to include.

        Returns:
            List of matching subtask dictionaries with JSON parsed.

        Raises:
            ValueError: If session_id is empty or statuses is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not statuses:
            raise ValueError("statuses sequence must not be empty")

        placeholders = ", ".join("?" for _ in statuses)
        sql = f"SELECT * FROM subtask_states WHERE session_id = ? AND status IN ({placeholders}) ORDER BY created_at"
        params = (session_id,) + tuple(statuses)

        try:
            rows = await self._fetchall(sql, params)
            return _deserialize_json_rows(rows, self._SUBTASK_JSON_COLS, self._SUBTASK_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch subtasks by status for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 7: log_reasoning
    # ==================================================================
    async def log_reasoning(
        self,
        subtask_id: str,
        attempt_no: int,
        thinking: Optional[str] = None,
        response: Optional[str] = None,
        qg_result: Optional[Any] = None,
    ) -> int:
        """
        Log an agent reasoning record for a subtask attempt.

        Stores thinking, response, and quality gate result. qg_result is
        JSON-serialized before storage.

        Args:
            subtask_id: Subtask this reasoning belongs to.
            attempt_no: Attempt number (1-indexed).
            thinking: Agent chain-of-thought text.
            response: Generated response text.
            qg_result: Quality gate evaluation (JSON-serialized).

        Returns:
            Auto-incremented id of the inserted reasoning row.

        Raises:
            ValueError: If subtask_id is empty or attempt_no is negative.
            sqlite3.Error: If insert fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")
        if attempt_no < 0:
            raise ValueError("attempt_no must be non-negative")

        qg_result_json = _serialize_json(qg_result)
        ts = time.time()

        sql = """
            INSERT INTO agent_reasoning (subtask_id, attempt_no, timestamp, thinking, response, qg_result)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (subtask_id, attempt_no, ts, thinking, response, qg_result_json)

        try:
            cur = await self._execute(sql, params)
            reasoning_id = cur.lastrowid
            logger.debug("Logged reasoning id=%s subtask=%s attempt=%s", reasoning_id, subtask_id, attempt_no)
            return int(reasoning_id) if reasoning_id is not None else -1
        except sqlite3.Error as exc:
            logger.error("Failed to log reasoning for subtask %s attempt %d: %s", subtask_id, attempt_no, exc)
            raise

    # ==================================================================
    #  METHOD 8: create_session
    # ==================================================================
    async def create_session(
        self,
        session_id: str,
        task_title: Optional[str],
        total_subtasks: Optional[int],
        config_snapshot: Optional[Any],
    ) -> None:
        """
        Create a new orchestrator session record.

        config_snapshot is JSON-serialized before storage.

        Args:
            session_id: Globally unique session identifier.
            task_title: Human-readable task title.
            total_subtasks: Expected number of subtasks.
            config_snapshot: Orchestrator config (JSON-serialized).

        Raises:
            ValueError: If session_id is empty.
            sqlite3.IntegrityError: If session already exists.
            sqlite3.Error: If insert fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        config_json = _serialize_json(config_snapshot)
        ts = time.time()

        sql = """
            INSERT INTO sessions
                (session_id, started_at, last_active, status, task_title,
                 total_subtasks, completed_count, total_cost_usd, config_snapshot)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (session_id, ts, ts, "RUNNING", task_title, total_subtasks, 0, 0.0, config_json)

        try:
            cur = await self._execute(sql, params)
            if cur.rowcount != 1:
                logger.warning("Expected 1 row inserted for session, got %d", cur.rowcount)
            logger.info("Created session %s (title='%s', subtasks=%s)", session_id, task_title, total_subtasks)
        except sqlite3.IntegrityError as exc:
            logger.error("Session %s already exists: %s", session_id, exc)
            raise
        except sqlite3.Error as exc:
            logger.error("Failed to create session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 9: update_session
    # ==================================================================
    async def update_session(
        self,
        session_id: str,
        status: Optional[str] = None,
        completed_count: Optional[int] = None,
        total_cost_usd: Optional[float] = None,
        last_active: Optional[float] = None,
    ) -> int:
        """
        Dynamically update fields of a session record.

        Only non-None fields are included. Auto-updates last_active if other
        fields are being changed and last_active is not explicitly provided.

        Args:
            session_id: Session identifier to update.
            status: New session status.
            completed_count: Updated completed subtask count.
            total_cost_usd: Updated cumulative cost.
            last_active: Updated activity timestamp.

        Returns:
            Number of rows updated (should be 1).

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If update fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        fields: Dict[str, Any] = {}
        if status is not None:
            fields["status"] = status
        if completed_count is not None:
            fields["completed_count"] = completed_count
        if total_cost_usd is not None:
            fields["total_cost_usd"] = total_cost_usd
        if last_active is not None:
            fields["last_active"] = last_active
        elif fields:
            fields["last_active"] = time.time()

        if not fields:
            logger.warning("update_session called with no fields for %s", session_id)
            return 0

        safe_fields = _safe_columns(_SESSION_COLUMNS, fields)
        if not safe_fields:
            logger.error("No valid fields after filtering for session %s", session_id)
            return 0

        set_clause = ", ".join(f"{col} = ?" for col in safe_fields)
        values = list(safe_fields.values())
        values.append(session_id)

        sql = f"UPDATE sessions SET {set_clause} WHERE session_id = ?"

        try:
            cur = await self._execute(sql, tuple(values))
            if cur.rowcount == 0:
                logger.warning("No session found with id=%s to update", session_id)
            logger.debug("Updated session %s fields: %s", session_id, ", ".join(safe_fields.keys()))
            return cur.rowcount
        except sqlite3.Error as exc:
            logger.error("Failed to update session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 10: get_session
    # ==================================================================
    async def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve a session record by identifier.

        config_snapshot JSON column is automatically deserialized.

        Args:
            session_id: Session identifier.

        Returns:
            Session dictionary with JSON parsed, or None if not found.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = "SELECT * FROM sessions WHERE session_id = ?"

        try:
            row = await self._fetchone(sql, (session_id,))
            return _deserialize_json_row(row, self._SESSION_JSON_COLS, self._SESSION_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 11: get_incomplete_sessions
    # ==================================================================
    async def get_incomplete_sessions(self) -> List[Dict[str, Any]]:
        """
        Retrieve sessions that are not yet complete.

        Returns sessions with status RUNNING or PAUSED, ordered by most
        recently active first. config_snapshot is auto-deserialized.

        Returns:
            List of session dictionaries with JSON parsed.

        Raises:
            sqlite3.Error: If query fails.
        """
        sql = "SELECT * FROM sessions WHERE status IN ('RUNNING', 'PAUSED') ORDER BY last_active DESC"

        try:
            rows = await self._fetchall(sql)
            return _deserialize_json_rows(rows, self._SESSION_JSON_COLS, self._SESSION_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch incomplete sessions: %s", exc)
            raise

    # ==================================================================
    #  METHOD 12: get_recent_events
    # ==================================================================
    async def get_recent_events(
        self, session_id: str, limit: int = 500
    ) -> List[Dict[str, Any]]:
        """
        Retrieve the most recent events for a session.

        Events in reverse chronological order. payload JSON is auto-deserialized.

        Args:
            session_id: Session to query.
            limit: Max events to return (capped at 10000).

        Returns:
            List of event dictionaries.

        Raises:
            ValueError: If session_id is empty or limit non-positive.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > 10000:
            limit = 10000

        sql = "SELECT * FROM orchestrator_events WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?"

        try:
            rows = await self._fetchall(sql, (session_id, limit))
            return _deserialize_json_rows(rows, self._EVENT_JSON_COLS, self._EVENT_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch recent events for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 13: get_quality_stats
    # ==================================================================
    async def get_quality_stats(self, session_id: str) -> Dict[str, Any]:
        """
        Compute quality statistics for a session's subtasks.

        Aggregates counts by status, computes retry metrics, approval rate,
        and average retries.

        Args:
            session_id: Session to analyze.

        Returns:
            Dictionary with approved, rejected, retrying, dead_letter, blocked,
            pending, running, queued, total, approval_rate, avg_retries, max_retries.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = """
            SELECT
                COUNT(CASE WHEN status = 'APPROVED' THEN 1 END)    AS approved,
                COUNT(CASE WHEN status = 'REJECTED' THEN 1 END)    AS rejected,
                COUNT(CASE WHEN retry_count > 0 THEN 1 END)         AS retrying,
                COUNT(CASE WHEN status = 'DEAD_LETTER' THEN 1 END) AS dead_letter,
                COUNT(CASE WHEN status = 'BLOCKED' THEN 1 END)     AS blocked,
                COUNT(CASE WHEN status = 'PENDING' THEN 1 END)     AS pending,
                COUNT(CASE WHEN status = 'RUNNING' THEN 1 END)     AS running,
                COUNT(CASE WHEN status = 'QUEUED' THEN 1 END)      AS queued,
                COUNT(*)                                           AS total,
                AVG(COALESCE(retry_count, 0))                      AS avg_retries,
                MAX(COALESCE(retry_count, 0))                      AS max_retries
            FROM subtask_states
            WHERE session_id = ?
        """

        try:
            row = await self._fetchone(sql, (session_id,))
            if row is None or row.get("total", 0) == 0:
                return {
                    "approved": 0, "rejected": 0, "retrying": 0,
                    "dead_letter": 0, "blocked": 0, "pending": 0,
                    "running": 0, "queued": 0, "total": 0,
                    "approval_rate": 0.0, "avg_retries": 0.0, "max_retries": 0,
                }

            total = row["total"] or 0
            approved = row["approved"] or 0
            avg_retries = row["avg_retries"] or 0.0
            max_retries = row["max_retries"] or 0
            approval_rate = approved / total if total > 0 else 0.0

            return {
                "approved": approved,
                "rejected": row["rejected"] or 0,
                "retrying": row["retrying"] or 0,
                "dead_letter": row["dead_letter"] or 0,
                "blocked": row["blocked"] or 0,
                "pending": row["pending"] or 0,
                "running": row["running"] or 0,
                "queued": row["queued"] or 0,
                "total": total,
                "approval_rate": round(approval_rate, 4),
                "avg_retries": round(avg_retries, 4),
                "max_retries": max_retries,
            }
        except sqlite3.Error as exc:
            logger.error("Failed to compute quality stats for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 14: get_cost_summary
    # ==================================================================
    async def get_cost_summary(self, session_id: str) -> Dict[str, Any]:
        """
        Generate a cost summary for a session grouped by assigned API key.

        Computes total tokens and cost per key, plus session-wide aggregates.

        Args:
            session_id: Session to summarize.

        Returns:
            Dictionary with total_tokens, total_cost_usd, subtask_count,
            and by_key list of per-key breakdowns.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        overall_sql = """
            SELECT COALESCE(SUM(tokens_used), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0)  AS total_cost_usd,
                   COUNT(*)                        AS subtask_count
            FROM subtask_states WHERE session_id = ?
        """

        per_key_sql = """
            SELECT assigned_key                  AS assigned_key,
                   COALESCE(SUM(tokens_used), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0)  AS total_cost_usd,
                   COUNT(*)                      AS subtask_count
            FROM subtask_states
            WHERE session_id = ? AND assigned_key IS NOT NULL
            GROUP BY assigned_key
            ORDER BY total_cost_usd DESC
        """

        try:
            overall = await self._fetchone(overall_sql, (session_id,))
            per_key_rows = await self._fetchall(per_key_sql, (session_id,))

            if overall is None:
                overall = {"total_tokens": 0, "total_cost_usd": 0.0, "subtask_count": 0}

            return {
                "session_id": session_id,
                "total_tokens": overall.get("total_tokens", 0) or 0,
                "total_cost_usd": overall.get("total_cost_usd", 0.0) or 0.0,
                "subtask_count": overall.get("subtask_count", 0) or 0,
                "by_key": [
                    {
                        "assigned_key": r.get("assigned_key"),
                        "total_tokens": r.get("total_tokens", 0) or 0,
                        "total_cost_usd": r.get("total_cost_usd", 0.0) or 0.0,
                        "subtask_count": r.get("subtask_count", 0) or 0,
                    }
                    for r in per_key_rows
                ],
            }
        except sqlite3.Error as exc:
            logger.error("Failed to compute cost summary for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 15: export_logs_to_csv
    # ==================================================================
    async def export_logs_to_csv(self, session_id: str, file_path: str) -> int:
        """
        Export all events for a session to a CSV file.

        Writes atomically via temp file + rename. Includes all columns with
        deserialized JSON payload serialized back as JSON string.

        Args:
            session_id: Session whose events to export.
            file_path: Destination CSV file path.

        Returns:
            Number of event rows written.

        Raises:
            ValueError: If session_id or file_path is empty.
            sqlite3.Error: If query fails.
            OSError: If file operations fail.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not file_path:
            raise ValueError("file_path is required")

        events = await self.get_recent_events(session_id, limit=10000)

        out_path = Path(file_path)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = out_path.with_suffix(out_path.suffix + ".tmp")

        csv_columns = [
            "id", "session_id", "timestamp", "event_type", "severity",
            "subtask_id", "api_key_id", "message", "payload", "duration_ms",
        ]

        try:
            with temp_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=csv_columns, extrasaction="ignore")
                writer.writeheader()
                for event in events:
                    payload = event.get("payload")
                    if payload is not None and not isinstance(payload, str):
                        event["payload"] = json.dumps(payload, ensure_ascii=False)
                    writer.writerow(event)

            temp_path.replace(out_path)

            logger.info("Exported %d events for session %s to CSV: %s", len(events), session_id, file_path)
            return len(events)
        except Exception as exc:
            logger.error("Failed to export events to CSV for session %s: %s", session_id, exc)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    # ==================================================================
    #  METHOD 16: export_logs_to_plaintext
    # ==================================================================
    async def export_logs_to_plaintext(self, session_id: str, file_path: str) -> int:
        """
        Export all events for a session to a formatted plaintext log file.

        Each event formatted as human-readable log line via _format_log_line.
        Written atomically via temp file + rename.

        Args:
            session_id: Session whose events to export.
            file_path: Destination plaintext file path.

        Returns:
            Number of event lines written.

        Raises:
            ValueError: If session_id or file_path is empty.
            sqlite3.Error: If query fails.
            OSError: If file operations fail.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not file_path:
            raise ValueError("file_path is required")

        events = await self.get_recent_events(session_id, limit=10000)

        out_path = Path(file_path)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)

        temp_path = out_path.with_suffix(out_path.suffix + ".tmp")

        try:
            with temp_path.open("w", encoding="utf-8") as fh:
                fh.write("=" * 80 + "\n")
                fh.write("  ORCHESTRATOR LOG EXPORT\n")
                fh.write(f"  Session: {session_id}\n")
                fh.write(f"  Exported: {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n")
                fh.write(f"  Events: {len(events)}\n")
                fh.write("=" * 80 + "\n\n")

                for event in events:
                    line = _format_log_line(event)
                    fh.write(line + "\n")
                    payload = event.get("payload")
                    if payload is not None:
                        if isinstance(payload, (dict, list)):
                            payload_str = json.dumps(payload, indent=2, ensure_ascii=False)
                        else:
                            payload_str = str(payload)
                        indented = textwrap.indent(payload_str, "    | ")
                        fh.write(indented + "\n")

                fh.write("\n" + "=" * 80 + "\n")
                fh.write(f"  END OF EXPORT ({len(events)} events)\n")
                fh.write("=" * 80 + "\n")

            temp_path.replace(out_path)

            logger.info("Exported %d events for session %s to plaintext: %s", len(events), session_id, file_path)
            return len(events)
        except Exception as exc:
            logger.error("Failed to export events to plaintext for session %s: %s", session_id, exc)
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    # ==================================================================
    #  METHOD 17: get_checkpoint_data
    # ==================================================================
    async def get_checkpoint_data(self, session_id: str) -> Dict[str, Any]:
        """
        Retrieve complete checkpoint data for a session.

        Gathers session record, all subtasks, and recent events into a single
        structured dictionary suitable for serialization or disaster recovery.

        Args:
            session_id: Session to checkpoint.

        Returns:
            Checkpoint dictionary with exported_at, session, subtasks,
            recent_events, and summary counts.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If any query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        session_row = await self.get_session(session_id)
        subtask_rows = await self.get_session_subtasks(session_id)
        event_rows = await self.get_recent_events(session_id, limit=500)

        return _build_checkpoint(session_row, subtask_rows, event_rows)

    # ==================================================================
    #  METHOD 18: reset_non_approved_subtasks
    # ==================================================================
    async def reset_non_approved_subtasks(self, session_id: str) -> int:
        """
        Reset non-terminal subtasks back to QUEUED state.

        Updates subtasks whose status is NOT APPROVED, DEAD_LETTER, or BLOCKED
        to QUEUED with retry_count reset to 0.

        Args:
            session_id: Session whose subtasks to reset.

        Returns:
            Number of subtask rows reset.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If update fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = """
            UPDATE subtask_states
            SET status = 'QUEUED', retry_count = 0
            WHERE session_id = ?
              AND status NOT IN ('APPROVED', 'DEAD_LETTER', 'BLOCKED')
        """

        try:
            cur = await self._execute(sql, (session_id,))
            updated = cur.rowcount
            if updated > 0:
                logger.info("Reset %d non-approved subtasks to QUEUED for session %s", updated, session_id)
            else:
                logger.debug("No subtasks to reset for session %s (all terminal)", session_id)
            return updated
        except sqlite3.Error as exc:
            logger.error("Failed to reset subtasks for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  METHOD 19: close
    # ==================================================================
    async def close(self) -> None:
        """
        Gracefully shut down the state store.

        Closes thread-local connection and shuts down ThreadPoolExecutor.
        Safe to call multiple times.

        Raises:
            RuntimeError: If executor shutdown fails.
        """
        logger.info("Shutting down SQLiteStateStore")

        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
                self._local.conn = None
                logger.debug("Closed thread-local SQLite connection")
            except sqlite3.Error as exc:
                logger.warning("Error closing thread-local connection: %s", exc)

        try:
            self._pool.shutdown(wait=True)
            logger.info("ThreadPoolExecutor shut down successfully")
        except Exception as exc:
            logger.error("Error shutting down ThreadPoolExecutor: %s", exc)
            raise

    # ==================================================================
    #  METHOD 20: get_token_usage_per_key
    # ==================================================================
    async def get_token_usage_per_key(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve token and cost usage aggregated by assigned API key.

        Returns per-key breakdown ordered by total cost descending.

        Args:
            session_id: Session to query.

        Returns:
            List of per-key dictionaries with assigned_key, total_tokens,
            total_cost_usd.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = """
            SELECT assigned_key                  AS assigned_key,
                   COALESCE(SUM(tokens_used), 0) AS total_tokens,
                   COALESCE(SUM(cost_usd), 0.0)  AS total_cost_usd
            FROM subtask_states
            WHERE session_id = ? AND assigned_key IS NOT NULL
            GROUP BY assigned_key
            ORDER BY total_cost_usd DESC
        """

        try:
            rows = await self._fetchall(sql, (session_id,))
            return [
                {
                    "assigned_key": r.get("assigned_key"),
                    "total_tokens": r.get("total_tokens", 0) or 0,
                    "total_cost_usd": r.get("total_cost_usd", 0.0) or 0.0,
                }
                for r in rows
            ]
        except sqlite3.Error as exc:
            logger.error("Failed to fetch token usage per key for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_reasoning_history
    # ==================================================================
    async def get_reasoning_history(
        self, subtask_id: str, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Retrieve reasoning history for a subtask.

        Args:
            subtask_id: Subtask to query.
            limit: Max records (capped at 1000).

        Returns:
            List of reasoning dictionaries with qg_result JSON parsed.

        Raises:
            ValueError: If subtask_id is empty or limit non-positive.
            sqlite3.Error: If query fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > 1000:
            limit = 1000

        sql = "SELECT * FROM agent_reasoning WHERE subtask_id = ? ORDER BY attempt_no ASC LIMIT ?"

        try:
            rows = await self._fetchall(sql, (subtask_id, limit))
            return _deserialize_json_rows(rows, self._REASONING_JSON_COLS, self._REASONING_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch reasoning history for subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: log_command
    # ==================================================================
    async def log_command(
        self,
        session_id: str,
        command: str,
        source: str,
        result: Optional[str] = None,
    ) -> int:
        """
        Log a command execution into command_history.

        Args:
            session_id: Session this command belongs to.
            command: The command string executed.
            source: Origin of command (cli, api, system).
            result: Optional command output.

        Returns:
            Auto-incremented id of inserted command row.

        Raises:
            ValueError: If session_id, command, or source is empty.
            sqlite3.Error: If insert fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not command:
            raise ValueError("command is required")
        if not source:
            raise ValueError("source is required")

        ts = time.time()
        sql = "INSERT INTO command_history (session_id, timestamp, command, source, result) VALUES (?, ?, ?, ?, ?)"

        try:
            cur = await self._execute(sql, (session_id, ts, command, source, result))
            cmd_id = cur.lastrowid
            logger.debug("Logged command id=%s session=%s source=%s", cmd_id, session_id, source)
            return int(cmd_id) if cmd_id is not None else -1
        except sqlite3.Error as exc:
            logger.error("Failed to log command for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_command_history
    # ==================================================================
    async def get_command_history(
        self, session_id: str, limit: int = 200
    ) -> List[Dict[str, Any]]:
        """
        Retrieve command history for a session.

        Args:
            session_id: Session to query.
            limit: Max commands (capped at 5000).

        Returns:
            List of command dictionaries.

        Raises:
            ValueError: If session_id is empty or limit non-positive.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > 5000:
            limit = 5000

        sql = "SELECT * FROM command_history WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?"

        try:
            return await self._fetchall(sql, (session_id, limit))
        except sqlite3.Error as exc:
            logger.error("Failed to fetch command history for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_all_sessions
    # ==================================================================
    async def get_all_sessions(
        self, status_filter: Optional[str] = None, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Retrieve all sessions, optionally filtered by status.

        Args:
            status_filter: Optional status to filter.
            limit: Max sessions (capped at 1000).

        Returns:
            List of session dictionaries with config_snapshot JSON parsed.

        Raises:
            ValueError: If limit non-positive.
            sqlite3.Error: If query fails.
        """
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > 1000:
            limit = 1000

        if status_filter:
            sql = "SELECT * FROM sessions WHERE status = ? ORDER BY last_active DESC LIMIT ?"
            params = (status_filter, limit)
        else:
            sql = "SELECT * FROM sessions ORDER BY last_active DESC LIMIT ?"
            params = (limit,)

        try:
            rows = await self._fetchall(sql, params)
            return _deserialize_json_rows(rows, self._SESSION_JSON_COLS, self._SESSION_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch sessions: %s", exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_subtask_count
    # ==================================================================
    async def get_subtask_count(self, session_id: str) -> int:
        """
        Get the total number of subtasks for a session.

        Args:
            session_id: Session to count subtasks for.

        Returns:
            Total subtask count (0 if no subtasks).

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = "SELECT COUNT(*) AS cnt FROM subtask_states WHERE session_id = ?"

        try:
            row = await self._fetchone(sql, (session_id,))
            return row["cnt"] if row else 0
        except sqlite3.Error as exc:
            logger.error("Failed to count subtasks for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_events_by_subtask
    # ==================================================================
    async def get_events_by_subtask(
        self, subtask_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Retrieve events for a specific subtask.

        Args:
            subtask_id: Subtask to query events for.
            limit: Max events (capped at 5000).

        Returns:
            List of event dictionaries with payload JSON parsed.

        Raises:
            ValueError: If subtask_id is empty or limit non-positive.
            sqlite3.Error: If query fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")
        if limit <= 0:
            raise ValueError("limit must be positive")
        if limit > 5000:
            limit = 5000

        sql = "SELECT * FROM orchestrator_events WHERE subtask_id = ? ORDER BY timestamp DESC LIMIT ?"

        try:
            rows = await self._fetchall(sql, (subtask_id, limit))
            return _deserialize_json_rows(rows, self._EVENT_JSON_COLS, self._EVENT_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch events for subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_event_counts_by_type
    # ==================================================================
    async def get_event_counts_by_type(self, session_id: str) -> Dict[str, int]:
        """
        Count events grouped by event type for a session.

        Args:
            session_id: Session to analyze.

        Returns:
            Dictionary mapping event_type to count.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = """
            SELECT event_type, COUNT(*) AS cnt
            FROM orchestrator_events
            WHERE session_id = ?
            GROUP BY event_type
            ORDER BY cnt DESC
        """

        try:
            rows = await self._fetchall(sql, (session_id,))
            return {r["event_type"]: r["cnt"] for r in rows}
        except sqlite3.Error as exc:
            logger.error("Failed to count events by type for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_subtask_reasoning_summary
    # ==================================================================
    async def get_subtask_reasoning_summary(self, subtask_id: str) -> Optional[Dict[str, Any]]:
        """
        Get summary of all reasoning attempts for a subtask.

        Args:
            subtask_id: Subtask to summarize.

        Returns:
            Summary dictionary with total_attempts, latest_thinking,
            latest_response, latest_qg_result, or None if no records.

        Raises:
            ValueError: If subtask_id is empty.
            sqlite3.Error: If query fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")

        sql = """
            SELECT COUNT(*) AS total_attempts, MAX(attempt_no) AS latest_attempt
            FROM agent_reasoning WHERE subtask_id = ?
        """

        try:
            summary = await self._fetchone(sql, (subtask_id,))
            if summary is None or summary["total_attempts"] == 0:
                return None

            latest_sql = "SELECT * FROM agent_reasoning WHERE subtask_id = ? AND attempt_no = ?"
            latest = await self._fetchone(latest_sql, (subtask_id, summary["latest_attempt"]))
            if latest is None:
                return None

            latest = _deserialize_json_row(latest, self._REASONING_JSON_COLS, self._REASONING_JSON_DEFAULTS)

            return {
                "subtask_id": subtask_id,
                "total_attempts": summary["total_attempts"],
                "latest_attempt_no": summary["latest_attempt"],
                "latest_thinking": latest.get("thinking") if latest else None,
                "latest_response": latest.get("response") if latest else None,
                "latest_qg_result": latest.get("qg_result") if latest else None,
            }
        except sqlite3.Error as exc:
            logger.error("Failed to get reasoning summary for subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_subtasks_with_retry_history
    # ==================================================================
    async def get_subtasks_with_retry_history(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get all subtasks that have been retried at least once.

        Args:
            session_id: Session to query.

        Returns:
            List of subtask dictionaries with JSON parsed where retry_count > 0.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = "SELECT * FROM subtask_states WHERE session_id = ? AND retry_count > 0 ORDER BY retry_count DESC, created_at"

        try:
            rows = await self._fetchall(sql, (session_id,))
            return _deserialize_json_rows(rows, self._SUBTASK_JSON_COLS, self._SUBTASK_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch retry history for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_dead_letter_subtasks
    # ==================================================================
    async def get_dead_letter_subtasks(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get all subtasks in DEAD_LETTER state.

        Args:
            session_id: Session to query.

        Returns:
            List of dead letter subtask dictionaries with JSON parsed.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = "SELECT * FROM subtask_states WHERE session_id = ? AND status = 'DEAD_LETTER' ORDER BY completed_at DESC"

        try:
            rows = await self._fetchall(sql, (session_id,))
            return _deserialize_json_rows(rows, self._SUBTASK_JSON_COLS, self._SUBTASK_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch dead letter subtasks for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_database_stats
    # ==================================================================
    async def get_database_stats(self) -> Dict[str, Any]:
        """
        Get aggregate statistics about the database contents.

        Returns:
            Dictionary with total_events, total_subtasks, total_reasoning_records,
            total_commands, total_sessions, db_path, db_size_bytes.

        Raises:
            sqlite3.Error: If query fails.
            OSError: If DB file size cannot be determined.
        """
        counts_sql = """
            SELECT
                (SELECT COUNT(*) FROM orchestrator_events) AS total_events,
                (SELECT COUNT(*) FROM subtask_states)      AS total_subtasks,
                (SELECT COUNT(*) FROM agent_reasoning)     AS total_reasoning,
                (SELECT COUNT(*) FROM command_history)     AS total_commands,
                (SELECT COUNT(*) FROM sessions)            AS total_sessions
        """

        try:
            row = await self._fetchone(counts_sql)
            db_size = 0
            try:
                db_size = Path(self.db_path).stat().st_size
            except OSError as size_exc:
                logger.warning("Could not determine DB file size: %s", size_exc)

            return {
                "total_events": row.get("total_events", 0) if row else 0,
                "total_subtasks": row.get("total_subtasks", 0) if row else 0,
                "total_reasoning_records": row.get("total_reasoning", 0) if row else 0,
                "total_commands": row.get("total_commands", 0) if row else 0,
                "total_sessions": row.get("total_sessions", 0) if row else 0,
                "db_path": self.db_path,
                "db_size_bytes": db_size,
            }
        except sqlite3.Error as exc:
            logger.error("Failed to get database stats: %s", exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_latest_subtask_events
    # ==================================================================
    async def get_latest_subtask_events(
        self, subtask_id: str, event_types: Optional[Sequence[str]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get the latest events for a subtask, optionally filtered by event types.

        Args:
            subtask_id: Subtask to query.
            event_types: Optional sequence of event type strings to filter.

        Returns:
            List of event dictionaries with payload JSON parsed.

        Raises:
            ValueError: If subtask_id is empty.
            sqlite3.Error: If query fails.
        """
        if not subtask_id:
            raise ValueError("subtask_id is required")

        if event_types:
            placeholders = ", ".join("?" for _ in event_types)
            sql = f"""
                SELECT * FROM orchestrator_events
                WHERE subtask_id = ? AND event_type IN ({placeholders})
                ORDER BY timestamp DESC
                LIMIT 50
            """
            params = (subtask_id,) + tuple(event_types)
        else:
            sql = "SELECT * FROM orchestrator_events WHERE subtask_id = ? ORDER BY timestamp DESC LIMIT 50"
            params = (subtask_id,)

        try:
            rows = await self._fetchall(sql, params)
            return _deserialize_json_rows(rows, self._EVENT_JSON_COLS, self._EVENT_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to fetch latest events for subtask %s: %s", subtask_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: search_events
    # ==================================================================
    async def search_events(
        self,
        session_id: str,
        query: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Search event messages by substring match.

        Args:
            session_id: Session to search within.
            query: Substring to search for in message column.
            limit: Max results (capped at 1000).

        Returns:
            List of matching event dictionaries.

        Raises:
            ValueError: If session_id or query is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if not query:
            raise ValueError("query is required")
        if limit > 1000:
            limit = 1000

        sql = """
            SELECT * FROM orchestrator_events
            WHERE session_id = ? AND message LIKE ?
            ORDER BY timestamp DESC
            LIMIT ?
        """

        try:
            rows = await self._fetchall(sql, (session_id, f"%{query}%", limit))
            return _deserialize_json_rows(rows, self._EVENT_JSON_COLS, self._EVENT_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to search events for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  ADDITIONAL UTILITY: get_session_timeline
    # ==================================================================
    async def get_session_timeline(self, session_id: str) -> List[Dict[str, Any]]:
        """
        Get a full chronological timeline of events for a session.

        Args:
            session_id: Session to get timeline for.

        Returns:
            List of all event dictionaries ordered by timestamp ascending.

        Raises:
            ValueError: If session_id is empty.
            sqlite3.Error: If query fails.
        """
        if not session_id:
            raise ValueError("session_id is required")

        sql = "SELECT * FROM orchestrator_events WHERE session_id = ? ORDER BY timestamp ASC"

        try:
            rows = await self._fetchall(sql, (session_id,))
            return _deserialize_json_rows(rows, self._EVENT_JSON_COLS, self._EVENT_JSON_DEFAULTS)
        except sqlite3.Error as exc:
            logger.error("Failed to get timeline for session %s: %s", session_id, exc)
            raise

    # ==================================================================
    #  Context manager support
    # ==================================================================

    async def __aenter__(self) -> "SQLiteStateStore":
        """Async context manager entry -- returns the store instance."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit -- ensures cleanup on exit."""
        await self.close()


# ==============================================================================
#                         MODULE-LEVEL FACTORY
# ==============================================================================


async def create_state_store(db_path: str = "orchestrator.db") -> SQLiteStateStore:
    """
    Factory function to create and return a new SQLiteStateStore instance.

    This is the recommended way to create a store instance within an async
    context.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        Initialized and ready-to-use SQLiteStateStore instance.

    Example::

        store = await create_state_store("/data/orchestrator.db")
        await store.create_session("sess_001", "Build API", 5, {"model": "gpt-4"})
        await store.close()
    """
    return SQLiteStateStore(db_path=db_path)


# ==============================================================================
#                         __ALL__ DEFINITION
# ==============================================================================

__all__ = [
    "SQLiteStateStore",
    "create_state_store",
    "SCHEMA_SQL",
    "_serialize_json",
    "_deserialize_json",
    "VALID_SEVERITIES",
    "VALID_SUBTASK_STATUSES",
    "VALID_SESSION_STATUSES",
    "VALID_EVENT_TYPES",
]
