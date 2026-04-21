#!/usr/bin/env python3
"""
SOVEREIGN GATEWAY MCP Server v4.1
Multi-Key Pool Edition — Routes AI API requests between backends with key rotation.
Implements MCP stdio protocol (JSON-RPC 2.0).
"""
import json
import sys
import os
import time
import itertools
import urllib.request
import urllib.error
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────
CONFIG_PATH = os.environ.get("GATEWAY_CONFIG", "./config/gateway.json")
DEFAULT_BACKEND = "kimi"

# circuit state per backend
CIRCUIT_BREAKERS = {}
FAILURE_COUNTS = {}

# multi-key state per backend: {backend: {"keys": [...], "iterator": itertools.cycle, "key_failures": {key_idx: count}}}
KEY_POOLS = {}

# ── Key Pool ────────────────────────────────────────────────────────
def get_api_keys(backend):
    """Get API key list for a backend. Supports single key or comma-separated multi-key."""
    # 1. Try KIMI_API_KEYS (plural, comma-separated)
    plural_env = f"{backend.upper()}_API_KEYS"
    keys_env = os.environ.get(plural_env, "")
    if keys_env:
        return [k.strip() for k in keys_env.split(",") if k.strip()]
    # 2. Fall back to single key
    single_env = f"{backend.upper()}_API_KEY"
    single = os.environ.get(single_env, "")
    if single:
        return [single]
    return []

def init_key_pool(backend):
    """Initialize round-robin key pool for a backend."""
    keys = get_api_keys(backend)
    if not keys:
        return None
    KEY_POOLS[backend] = {
        "keys": keys,
        "count": len(keys),
        "index": 0,
        "key_failures": {i: 0 for i in range(len(keys))},
        "key_last_fail": {i: 0 for i in range(len(keys))},
    }
    return KEY_POOLS[backend]

def get_next_key(backend):
    """Get next available key via round-robin with per-key cooldown."""
    pool = KEY_POOLS.get(backend)
    if not pool:
        pool = init_key_pool(backend)
        if not pool:
            return None, 0
    keys = pool["keys"]
    count = pool["count"]
    # Try each key in round-robin, skip those in cooldown
    for _ in range(count):
        idx = pool["index"] % count
        pool["index"] += 1
        # Per-key cooldown: 30s after 3 consecutive failures
        if pool["key_failures"].get(idx, 0) >= 3:
            if time.time() - pool["key_last_fail"].get(idx, 0) < 30:
                continue  # skip this key, try next
            pool["key_failures"][idx] = 0  # reset after cooldown
        return keys[idx], idx
    return None, -1  # all keys in cooldown

def record_key_failure(backend, key_idx):
    """Record failure for a specific key."""
    pool = KEY_POOLS.get(backend)
    if pool and key_idx >= 0:
        pool["key_failures"][key_idx] = pool["key_failures"].get(key_idx, 0) + 1
        pool["key_last_fail"][key_idx] = time.time()

def record_key_success(backend, key_idx):
    """Reset failure count for a key on success."""
    pool = KEY_POOLS.get(backend)
    if pool and key_idx >= 0:
        pool["key_failures"][key_idx] = 0

# ── Config ──────────────────────────────────────────────────────────
def load_config():
    try:
        with open(CONFIG_PATH, 'r') as f:
            return json.load(f)
    except Exception:
        return {
            "default_backend": "kimi",
            "backends": {
                "kimi": {"base_url": "https://api.moonshot.cn/v1", "timeout_seconds": 60},
                "anthropic": {"base_url": "https://api.anthropic.com", "timeout_seconds": 60},
                "openai": {"base_url": "https://api.openai.com/v1", "timeout_seconds": 60},
                "ollama": {"base_url": "http://localhost:11434", "timeout_seconds": 120},
            }
        }

def log_event(msg):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[{ts}] GATEWAY {msg}", file=sys.stderr)

def circuit_ok(backend, cfg):
    threshold = cfg.get("backends", {}).get(backend, {}).get("circuit_breaker_threshold", 5)
    cooldown = cfg.get("backends", {}).get(backend, {}).get("circuit_breaker_cooldown_seconds", 30)
    if FAILURE_COUNTS.get(backend, 0) >= threshold:
        last_fail = CIRCUIT_BREAKERS.get(backend, 0)
        if time.time() - last_fail < cooldown:
            return False
        FAILURE_COUNTS[backend] = 0
    return True

def record_failure(backend):
    FAILURE_COUNTS[backend] = FAILURE_COUNTS.get(backend, 0) + 1
    CIRCUIT_BREAKERS[backend] = time.time()

# ── MCP Protocol ────────────────────────────────────────────────────
def send_response(req_id, result):
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    print(json.dumps(msg), flush=True)

def send_error(req_id, code, message):
    msg = {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    print(json.dumps(msg), flush=True)

# ── Tools ───────────────────────────────────────────────────────────
def tool_route_request(backend, path, method, body, headers):
    cfg = load_config()
    if not circuit_ok(backend, cfg):
        return {"error": f"Circuit breaker OPEN for {backend}"}
    be = cfg.get("backends", {}).get(backend)
    if not be:
        return {"error": f"Unknown backend: {backend}"}
    url = be["base_url"].rstrip("/") + path
    timeout = be.get("timeout_seconds", 60)

    # ── Multi-key routing ────────────────────────────────────────
    api_key, key_idx = get_next_key(backend)
    if not api_key:
        return {"error": f"No API key available for {backend}"}

    req_headers = {"Content-Type": "application/json"}
    if backend == "anthropic":
        req_headers["x-api-key"] = api_key
        req_headers["anthropic-version"] = "2023-06-01"
    else:
        req_headers["Authorization"] = f"Bearer {api_key}"
    req_headers.update(headers or {})

    try:
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method or "POST")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp_body = resp.read().decode()
            record_key_success(backend, key_idx)
            return {"status": resp.status, "body": json.loads(resp_body) if resp_body else {}}
    except urllib.error.HTTPError as e:
        record_key_failure(backend, key_idx)
        # If rate limited (429) or auth failed (401/403), try next key immediately
        if e.code in (429, 401, 403):
            log_event(f"Key {key_idx} failed with HTTP {e.code}, trying next key...")
            # One retry with next key
            next_key, next_idx = get_next_key(backend)
            if next_key and next_idx != key_idx:
                try:
                    req_headers["Authorization"] = f"Bearer {next_key}"
                    req = urllib.request.Request(url, data=data, headers=req_headers, method=method or "POST")
                    with urllib.request.urlopen(req, timeout=timeout) as resp:
                        resp_body = resp.read().decode()
                        record_key_success(backend, next_idx)
                        return {"status": resp.status, "body": json.loads(resp_body) if resp_body else {}}
                except Exception:
                    record_key_failure(backend, next_idx)
        record_failure(backend)
        return {"error": f"HTTP {e.code}", "body": e.read().decode()}
    except Exception as e:
        record_key_failure(backend, key_idx)
        record_failure(backend)
        return {"error": str(e)}

def tool_get_models(backend):
    pool = KEY_POOLS.get(backend) or init_key_pool(backend)
    key_count = pool["count"] if pool else 0
    if backend == "kimi":
        return {"models": ["kimi-k1", "kimi-k1.5", "kimi-k2.5", "k2.6-code-preview"], "key_pool_size": key_count}
    elif backend == "anthropic":
        return {"models": ["claude-opus-4-1", "claude-sonnet-4-20250514", "claude-haiku-4-5"], "key_pool_size": key_count}
    elif backend == "openai":
        return {"models": ["gpt-4o", "gpt-4o-mini", "o3-mini"], "key_pool_size": key_count}
    elif backend == "ollama":
        return {"models": ["llama3", "mistral", "codellama"], "key_pool_size": key_count}
    return {"error": "Unknown backend"}

def tool_health_check():
    cfg = load_config()
    results = {}
    for name, be in cfg.get("backends", {}).items():
        ok = circuit_ok(name, cfg)
        pool = KEY_POOLS.get(name) or init_key_pool(name)
        key_info = {
            "total_keys": pool["count"] if pool else 0,
            "available_keys": sum(1 for i in range(pool["count"]) if pool["key_failures"].get(i, 0) < 3) if pool else 0
        } if pool else {"total_keys": 0, "available_keys": 0}
        results[name] = {
            "circuit": "CLOSED" if ok else "OPEN",
            "failures": FAILURE_COUNTS.get(name, 0),
            "base_url": be.get("base_url", ""),
            **key_info
        }
    return {"status": "healthy", "backends": results}

# ── Main Loop ───────────────────────────────────────────────────────
def main():
    log_event("Booting sovereign-gateway MCP server v4.1 — Multi-Key Pool Edition")
    # Pre-init key pools
    cfg = load_config()
    for be_name in cfg.get("backends", {}).keys():
        init_key_pool(be_name)
        pool = KEY_POOLS.get(be_name)
        if pool:
            log_event(f"Backend '{be_name}' loaded with {pool['count']} API key(s)")
        else:
            log_event(f"Backend '{be_name}' has NO API keys configured")
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
                "serverInfo": {"name": "sovereign-gateway", "version": "4.1.0"}
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            send_response(req_id, {
                "tools": [
                    {
                        "name": "route_request",
                        "description": "Route an HTTP request to a backend AI API with multi-key failover",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "backend": {"type": "string", "enum": ["kimi", "anthropic", "openai", "ollama"]},
                                "path": {"type": "string"},
                                "method": {"type": "string"},
                                "body": {"type": "object"},
                                "headers": {"type": "object"}
                            },
                            "required": ["backend", "path"]
                        }
                    },
                    {
                        "name": "get_models",
                        "description": "List available models for a backend",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "backend": {"type": "string", "enum": ["kimi", "anthropic", "openai", "ollama"]}
                            },
                            "required": ["backend"]
                        }
                    },
                    {
                        "name": "health_check",
                        "description": "Check gateway and backend health with key pool status",
                        "inputSchema": {"type": "object", "properties": {}}
                    }
                ]
            })
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            if name == "route_request":
                result = tool_route_request(
                    args.get("backend", DEFAULT_BACKEND),
                    args.get("path", "/v1/chat/completions"),
                    args.get("method", "POST"),
                    args.get("body"),
                    args.get("headers")
                )
            elif name == "get_models":
                result = tool_get_models(args.get("backend", DEFAULT_BACKEND))
            elif name == "health_check":
                result = tool_health_check()
            else:
                send_error(req_id, -32601, f"Unknown tool: {name}")
                continue
            send_response(req_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
        elif req_id is not None:
            send_error(req_id, -32601, f"Unknown method: {method}")

if __name__ == "__main__":
    main()
