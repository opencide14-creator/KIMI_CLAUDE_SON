-- memory/schema.sql — VEKIL-KAAN RAG OS SQLite schema
-- Applied once during Phase 1 memory substrate boot.
-- Append-only tables enforced by triggers.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ── Events ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS events (
    event_id    TEXT     PRIMARY KEY,
    timestamp   TEXT     NOT NULL,
    source      TEXT     NOT NULL CHECK(source IN ('REACTIVE','HEARTBEAT','SYSTEM','COMMANDER')),
    type        TEXT     NOT NULL,
    payload     TEXT     NOT NULL,   -- JSON
    signature   TEXT     NOT NULL,   -- HMAC-SHA256 hex
    created_at  REAL     DEFAULT (unixepoch('now', 'subsec'))
);

-- Prevent UPDATE/DELETE on events (append-only)
CREATE TRIGGER IF NOT EXISTS events_no_update
    BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS events_no_delete
    BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events table is append-only: DELETE forbidden');
END;

-- ── Audit log ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER  PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT     NOT NULL,
    level       TEXT     NOT NULL CHECK(level IN ('INFO','WARNING','CRITICAL','ESCAPE_ATTEMPT')),
    actor       TEXT     NOT NULL,
    action      TEXT     NOT NULL,
    details     TEXT
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
    BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: UPDATE forbidden');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
    BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit_log is append-only: DELETE forbidden');
END;

-- ── Memory snapshots (root hash chain) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS memory_snapshots (
    snapshot_id  TEXT  PRIMARY KEY,
    timestamp    TEXT  NOT NULL,
    root_hash    TEXT  NOT NULL,
    event_count  INTEGER NOT NULL,
    notes        TEXT
);

-- ── Pulses ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS pulses (
    pulse_id         TEXT  PRIMARY KEY,
    timestamp        TEXT  NOT NULL,
    direction        TEXT  NOT NULL CHECK(direction IN ('H_TO_R', 'R_TO_H')),
    memory_root_hash TEXT,          -- PULSE_H only
    soul_version     TEXT,          -- PULSE_H only
    last_action_hash TEXT,          -- PULSE_R only
    tool_result_hash TEXT,          -- PULSE_R only
    last_verified_event TEXT
);

-- ── Escape attempts ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS escape_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    agent        TEXT    NOT NULL,
    tool         TEXT    NOT NULL,
    detail       TEXT    NOT NULL,
    args_json    TEXT,               -- tool arguments at time of attempt
    resolved     INTEGER DEFAULT 0
);

-- ── Indices ───────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_events_type      ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_source    ON events(source);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_pulses_direction ON pulses(direction);
CREATE INDEX IF NOT EXISTS idx_pulses_timestamp ON pulses(timestamp);
