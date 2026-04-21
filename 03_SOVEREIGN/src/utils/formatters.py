"""Formatting utilities for the SOVEREIGN GUI."""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Any

from src.constants import COLORS, METHOD_COLORS, status_color


def fmt_bytes(n: int) -> str:
    if n < 1024:      return f"{n} B"
    if n < 1048576:   return f"{n/1024:.1f} KB"
    if n < 1073741824:return f"{n/1048576:.1f} MB"
    return f"{n/1073741824:.1f} GB"


def fmt_ms(ms: float) -> str:
    if ms < 1:    return "<1ms"
    if ms < 1000: return f"{ms:.0f}ms"
    return f"{ms/1000:.2f}s"


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M:%S.%f")[:-3]


def fmt_timestamp(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def fmt_method_html(method: str) -> str:
    color = METHOD_COLORS.get(method.upper(), COLORS["text_muted"])
    return f'<span style="color:{color};font-weight:bold;">{method}</span>'


def fmt_status_html(code: int) -> str:
    color = status_color(code)
    return f'<span style="color:{color};font-weight:bold;">{code}</span>'


def pretty_json(data: Any, indent: int = 2) -> str:
    try:
        if isinstance(data, (bytes, str)):
            data = json.loads(data)
        return json.dumps(data, indent=indent, ensure_ascii=False)
    except (json.JSONDecodeError, TypeError):
        return str(data)


def truncate(text: str, max_len: int = 80, suffix: str = "…") -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len - len(suffix)] + suffix


def mask_key(key: str) -> str:
    if len(key) < 12:
        return "***"
    return key[:8] + "…" + key[-4:]


def highlight_json_syntax(text: str) -> str:
    """Return HTML with simple JSON syntax highlighting."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # Strings
    text = re.sub(
        r'"((?:[^"\\]|\\.)*)"',
        lambda m: f'<span style="color:{COLORS["neon_green"]}">"{m.group(1)}"</span>',
        text
    )
    # Numbers
    text = re.sub(
        r'\b(\d+\.?\d*)\b',
        lambda m: f'<span style="color:{COLORS["neon_orange"]};">{m.group(1)}</span>',
        text
    )
    # Booleans / null
    text = re.sub(
        r'\b(true|false|null)\b',
        lambda m: f'<span style="color:{COLORS["neon_purple"]};">{m.group(1)}</span>',
        text
    )
    return f'<pre style="font-family:Consolas,monospace;font-size:11px;">{text}</pre>'
