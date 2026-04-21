"""SOVEREIGN — Network Sovereignty Command Center.
All application-wide constants, enumerations, color palette.
"""
from __future__ import annotations
from enum import Enum, auto
from pathlib import Path
import os

# ── Application identity ───────────────────────────────────────────
APP_NAME    = "SOVEREIGN"
APP_VERSION = "1.0.0"
APP_SUBTITLE = "Network Sovereignty Command Center"
APP_DIR     = Path.home() / ".sovereign"
DATA_DIR    = APP_DIR / "data"
CERT_DIR    = APP_DIR / "certs"
LOG_DIR     = APP_DIR / "logs"
CONFIG_FILE = APP_DIR / "config.toml"
VAULT_FILE  = APP_DIR / "vault.json"    # encrypted key store
ROUTES_FILE = APP_DIR / "routes.json"   # gateway routing rules

# ── Default ports ──────────────────────────────────────────────────
DEFAULT_PROXY_PORT   = 8080   # MITM proxy listener
DEFAULT_GATEWAY_PORT = 4000   # AI gateway (LiteLLM-compatible)
DEFAULT_WS_MONITOR   = 4001   # WebSocket monitor passthrough

# ── Supported protocols ────────────────────────────────────────────
class Protocol(Enum):
    HTTP    = "HTTP"
    HTTPS   = "HTTPS"
    WS      = "WS"
    WSS     = "WSS"
    MCP     = "MCP"       # Model Context Protocol (HTTP/SSE)
    GRPC    = "gRPC"

# ── Traffic direction ──────────────────────────────────────────────
class Direction(Enum):
    REQUEST  = "→"
    RESPONSE = "←"
    WS_SEND  = "↑"
    WS_RECV  = "↓"

# ── Service status ─────────────────────────────────────────────────
class ServiceStatus(Enum):
    STOPPED  = "stopped"
    STARTING = "starting"
    RUNNING  = "running"
    ERROR    = "error"

# ── Connection status for individual hosts ─────────────────────────
class HostStatus(Enum):
    UNKNOWN = "unknown"
    OPEN    = "open"
    CLOSED  = "closed"
    FILTERED= "filtered"

# ── Intercept actions ──────────────────────────────────────────────
class InterceptAction(Enum):
    PASSTHROUGH = "passthrough"  # allow unchanged
    FORWARD     = "forward"      # reroute to different host
    BLOCK       = "block"        # drop connection
    MODIFY      = "modify"       # alter request/response
    REPLAY      = "replay"       # resend stored request

# ── Certificate types ──────────────────────────────────────────────
class CertType(Enum):
    CA         = "Certificate Authority"
    SERVER     = "Server Certificate"
    CLIENT     = "Client Certificate"
    SELFSIGNED = "Self-Signed"

# ── AI model backends ──────────────────────────────────────────────
class ModelProvider(Enum):
    ANTHROPIC = "anthropic"
    KIMI      = "kimi"
    OPENAI    = "openai"
    OLLAMA    = "ollama"
    GROQ      = "groq"
    MISTRAL   = "mistral"
    GEMINI    = "gemini"
    CUSTOM    = "custom"

# ── Known API hosts to intercept ──────────────────────────────────
KNOWN_AI_HOSTS = {
    "api.anthropic.com":    ModelProvider.ANTHROPIC,
    "api.openai.com":       ModelProvider.OPENAI,
    "api.kimi.com":         ModelProvider.KIMI,
    "api.groq.com":         ModelProvider.GROQ,
    "api.mistral.ai":       ModelProvider.MISTRAL,
    "generativelanguage.googleapis.com": ModelProvider.GEMINI,
    # OpenCode infrastructure
    "api.opencode.ai":      ModelProvider.CUSTOM,
    "opencode.ai":          ModelProvider.CUSTOM,
    "gateway.opencode.ai":  ModelProvider.CUSTOM,
}

# ── Well-known AI API paths ────────────────────────────────────────
AI_API_PATHS = {
    "/v1/messages",
    "/v1/chat/completions",
    "/v1/completions",
    "/coding/v1/chat/completions",
}

# ── Panel IDs ──────────────────────────────────────────────────────
class PanelID(Enum):
    DASHBOARD = "dashboard"
    CHAT      = "chat"
    INTERCEPT = "intercept"
    FORGE     = "forge"
    GATEWAY   = "gateway"
    STREAMS   = "streams"
    DISCOVER  = "discover"
    VAULT     = "vault"
    INTEL     = "intel"

# ── Dark/neon theme (hacker aesthetic) ────────────────────────────
COLORS = {
    # Backgrounds
    "bg_void":      "#060a0f",   # deepest black
    "bg_dark":      "#0a0f1a",   # main background
    "bg_panel":     "#0f1520",   # panel background
    "bg_card":      "#141d2b",   # card/widget background
    "bg_input":     "#111827",   # input field background
    "bg_highlight": "#1a2540",   # selected/hover

    # Borders
    "border":       "#1e2d45",
    "border_focus": "#2d4a7a",

    # Neon accents
    "neon_blue":    "#00d4ff",   # primary — wire/connection
    "neon_green":   "#00ff88",   # success / active / allowed
    "neon_red":     "#ff2d55",   # danger / blocked / error
    "neon_yellow":  "#ffcc00",   # warning / modifying
    "neon_purple":  "#bf5fff",   # cert / forge
    "neon_orange":  "#ff6b35",   # gateway / routing
    "neon_cyan":    "#00e5cc",   # streams / websocket

    # Text
    "text_primary": "#e2e8f0",
    "text_muted":   "#4a6080",
    "text_dim":     "#2d3f55",
    "text_code":    "#00d4ff",

    # Status colors
    "status_run":   "#00ff88",
    "status_stop":  "#4a6080",
    "status_start": "#ffcc00",
    "status_error": "#ff2d55",
}

# ── Method badge colors ────────────────────────────────────────────
METHOD_COLORS = {
    "GET":     "#00ff88",
    "POST":    "#00d4ff",
    "PUT":     "#ffcc00",
    "PATCH":   "#bf5fff",
    "DELETE":  "#ff2d55",
    "OPTIONS": "#4a6080",
    "HEAD":    "#4a6080",
    "WS":      "#00e5cc",
    "WSS":     "#00e5cc",
    "MCP":     "#ff6b35",
}

# ── Status code badge colors ───────────────────────────────────────
def status_color(code: int) -> str:
    if 200 <= code < 300: return COLORS["neon_green"]
    if 300 <= code < 400: return COLORS["neon_cyan"]
    if 400 <= code < 500: return COLORS["neon_yellow"]
    if code >= 500:        return COLORS["neon_red"]
    return COLORS["text_muted"]

# ── Common AI model names for gateway dropdown ─────────────────────
GATEWAY_MODEL_PRESETS = {
    ModelProvider.ANTHROPIC: [
        "claude-opus-4-6",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    ModelProvider.KIMI: [
        "kimi-for-coding",
        "kimi-k2-thinking-turbo",
        "moonshot-v1-8k",
    ],
    ModelProvider.OPENAI: [
        "gpt-4o",
        "gpt-4o-mini",
        "gpt-4-turbo",
    ],
    ModelProvider.OLLAMA: [
        "llama3:8b",
        "llama3:70b",
        "mistral:7b",
        "codellama:13b",
        "qwen2:7b",
    ],
    ModelProvider.GROQ: [
        "llama3-8b-8192",
        "llama3-70b-8192",
        "mixtral-8x7b-32768",
    ],
}

# ── Provider base URLs ─────────────────────────────────────────────
PROVIDER_URLS = {
    ModelProvider.ANTHROPIC: "https://api.anthropic.com",
    ModelProvider.KIMI:      "https://api.kimi.com/coding/v1",
    ModelProvider.OPENAI:    "https://api.openai.com/v1",
    ModelProvider.OLLAMA:    "http://localhost:11434/v1",
    ModelProvider.GROQ:      "https://api.groq.com/openai/v1",
    ModelProvider.MISTRAL:   "https://api.mistral.ai/v1",
    ModelProvider.GEMINI:    "https://generativelanguage.googleapis.com",
}
