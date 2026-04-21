#!/usr/bin/env python3
"""
VEKIL-MEMORY MCP Server
Memory substrate using ChromaDB (vector) + SQLite (event log).
Implements MCP stdio protocol (JSON-RPC 2.0).
"""
import json
import sys
import os
import time
import hashlib
import sqlite3
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
CHROMA_PATH = os.environ.get("CHROMA_PATH", "./data/chroma")
SQLITE_PATH = os.environ.get("SQLITE_PATH", "./data/vekil.db")

def ensure_db():
    Path(SQLITE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            agent TEXT NOT NULL,
            event_type TEXT NOT NULL,
            payload TEXT NOT NULL,
            hash TEXT NOT NULL,
            UNIQUE(timestamp, agent, event_type)
        )
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS append_only_events
        BEFORE DELETE ON events
        BEGIN
            SELECT RAISE(ABORT, 'Append-only violation: events cannot be deleted');
        END;
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS hash_chain (
            id INTEGER PRIMARY KEY,
            last_hash TEXT NOT NULL DEFAULT 'genesis'
        )
    """)
    conn.execute("INSERT OR IGNORE INTO hash_chain (id, last_hash) VALUES (1, 'genesis')")
    conn.commit()
    conn.close()

def log_event(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{ts}] MEMORY {msg}", file=sys.stderr)

def compute_hash(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

def chain_hash(new_hash):
    conn = sqlite3.connect(SQLITE_PATH)
    row = conn.execute("SELECT last_hash FROM hash_chain WHERE id = 1").fetchone()
    prev = row[0] if row else "genesis"
    chained = hashlib.sha256(f"{prev}:{new_hash}".encode()).hexdigest()
    conn.execute("UPDATE hash_chain SET last_hash = ? WHERE id = 1", (chained,))
    conn.commit()
    conn.close()
    return chained

# ── ChromaDB (best-effort) ─────────────────────────────────────────
def get_chroma_client():
    try:
        import chromadb
        Path(CHROMA_PATH).mkdir(parents=True, exist_ok=True)
        return chromadb.PersistentClient(path=CHROMA_PATH)
    except ImportError:
        return None

def ensure_collections(client):
    if not client:
        return {}
    collections = {}
    for name in ["events", "agents", "laws", "knowledge"]:
        try:
            collections[name] = client.get_or_create_collection(name)
        except Exception:
            pass
    return collections

# ── MCP Protocol ────────────────────────────────────────────────────
def send_response(req_id, result):
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    print(json.dumps(msg), flush=True)

def send_error(req_id, code, message):
    msg = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    print(json.dumps(msg), flush=True)

# ── Tools ───────────────────────────────────────────────────────────
def tool_write_event(agent, event_type, payload):
    ensure_db()
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload_str = json.dumps(payload)
    h = compute_hash({"ts": timestamp, "agent": agent, "type": event_type, "payload": payload})
    conn = sqlite3.connect(SQLITE_PATH)
    try:
        conn.execute(
            "INSERT INTO events (timestamp, agent, event_type, payload, hash) VALUES (?, ?, ?, ?, ?)",
            (timestamp, agent, event_type, payload_str, h)
        )
        conn.commit()
        chained = chain_hash(h)
        # Also store in Chroma if available
        chroma = get_chroma_client()
        cols = ensure_collections(chroma)
        if "events" in cols:
            try:
                cols["events"].add(
                    documents=[payload_str],
                    metadatas=[{"agent": agent, "type": event_type, "timestamp": timestamp}],
                    ids=[f"{timestamp}_{agent}_{event_type}"]
                )
            except Exception:
                pass
        return {"ok": True, "hash": h, "chain": chained}
    except sqlite3.IntegrityError:
        return {"error": "Duplicate event"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()

def tool_semantic_search(collection, query, n_results=5):
    chroma = get_chroma_client()
    cols = ensure_collections(chroma)
    if collection not in cols:
        return {"error": f"Collection '{collection}' not available"}
    try:
        results = cols[collection].query(query_texts=[query], n_results=n_results)
        return {
            "documents": results.get("documents", [[]])[0],
            "metadatas": results.get("metadatas", [[]])[0],
            "distances": results.get("distances", [[]])[0]
        }
    except Exception as e:
        return {"error": str(e)}

def tool_get_hash(target):
    if target == "latest":
        ensure_db()
        conn = sqlite3.connect(SQLITE_PATH)
        row = conn.execute("SELECT last_hash FROM hash_chain WHERE id = 1").fetchone()
        conn.close()
        return {"hash": row[0] if row else "genesis"}
    elif target == "events_count":
        ensure_db()
        conn = sqlite3.connect(SQLITE_PATH)
        row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        conn.close()
        return {"count": row[0] if row else 0}
    else:
        return {"error": "Unknown target. Use 'latest' or 'events_count'."}

# ── Main Loop ───────────────────────────────────────────────────────
def main():
    ensure_db()
    log_event("Booting vekil-memory MCP server")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = req.get("method")
        req_id = req.get("id")
        params = req.get("params", {})
        if method == "initialize":
            send_response(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "vekil-memory", "version": "4.0.0"}
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send_response(req_id, {
                "tools": [
                    {
                        "name": "write_event",
                        "description": "Write an append-only event to the memory substrate",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "agent": {"type": "string"},
                                "event_type": {"type": "string"},
                                "payload": {"type": "object"}
                            },
                            "required": ["agent", "event_type", "payload"]
                        }
                    },
                    {
                        "name": "semantic_search",
                        "description": "Search ChromaDB collections by semantic similarity",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "collection": {"type": "string", "enum": ["events", "agents", "laws", "knowledge"]},
                                "query": {"type": "string"},
                                "n_results": {"type": "integer", "default": 5}
                            },
                            "required": ["collection", "query"]
                        }
                    },
                    {
                        "name": "get_hash",
                        "description": "Get latest chained hash or event count",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "target": {"type": "string", "enum": ["latest", "events_count"]}
                            },
                            "required": ["target"]
                        }
                    }
                ]
            })
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "write_event":
                result = tool_write_event(args.get("agent", "unknown"), args.get("event_type", "generic"), args.get("payload", {}))
            elif name == "semantic_search":
                result = tool_semantic_search(args.get("collection", "events"), args.get("query", ""), args.get("n_results", 5))
            elif name == "get_hash":
                result = tool_get_hash(args.get("target", "latest"))
            else:
                send_error(req_id, -32601, f"Unknown tool: {name}")
                continue
            send_response(req_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
        elif req_id is not None:
            send_error(req_id, -32601, f"Unknown method: {method}")

if __name__ == "__main__":
    main()
