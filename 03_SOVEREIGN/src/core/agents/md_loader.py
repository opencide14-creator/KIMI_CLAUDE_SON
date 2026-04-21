"""Markdown Config Loader — single source of truth for all agent behavior.

Every agent reads its config from Markdown. Python never hardcodes
agent laws, timeouts, step definitions, or oath text. If you want to
change agent behavior: edit the Markdown. Python just executes it.
"""
from __future__ import annotations
import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

AGENTS_DOCS_DIR = Path(__file__).parent.parent.parent.parent / "agents" / "docs"


class MarkdownConfig:
    """Parse a structured Markdown config file into a Python dict."""

    def __init__(self, path: Path):
        self._path = path
        self._raw  = ""
        self._data: Dict[str, Any] = {}
        self._loaded = False

    def load(self) -> "MarkdownConfig":
        if not self._path.exists():
            raise FileNotFoundError(f"Agent config not found: {self._path}")
        self._raw  = self._path.read_text(encoding="utf-8")
        self._data = self._parse(self._raw)
        self._loaded = True
        log.info("Loaded %s (%d chars)", self._path.name, len(self._raw))
        return self

    def reload(self) -> "MarkdownConfig":
        """Hot-reload from disk. Agents can call this to pick up changes."""
        self._loaded = False
        return self.load()

    @property
    def raw(self) -> str:
        return self._raw

    @property
    def hash(self) -> str:
        return hashlib.sha256(self._raw.encode()).hexdigest()[:16]

    def get(self, key: str, default: Any = None) -> Any:
        # Direct lookup first
        if key in self._data:
            return self._data[key]
        # Search all sections for the key
        for k, v in self._data.items():
            if k.startswith("section:") and isinstance(v, dict) and key in v:
                return v[key]
        return default

    def get_int(self, key: str, default: int = 0) -> int:
        v = self.get(key, default)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    def get_list(self, key: str) -> List[str]:
        v = self._data.get(key, [])
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return []

    def get_section(self, section_name: str) -> Dict[str, Any]:
        """Return a sub-dict for a named section."""
        return self._data.get(f"section:{section_name}", {})

    def get_sections_by_prefix(self, prefix: str) -> List[Dict[str, Any]]:
        """Return all sections matching a prefix (e.g. 'LAW_', 'STEP_', 'ARTICLE_')."""
        result = []
        for k, v in self._data.items():
            if k.startswith(f"section:{prefix}"):
                result.append(v)
        # Sort by priority if present, else by key
        result.sort(key=lambda s: (int(s.get("PRIORITY", 99)), s.get("ID", s.get("NAME", ""))))
        return result

    def full_text_of_section(self, heading: str) -> str:
        """Extract raw text between two headings."""
        lines   = self._raw.splitlines()
        in_sect = False
        out     = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("## ") or stripped.startswith("### "):
                if stripped.lstrip("#").strip() == heading:
                    in_sect = True
                    continue
                elif in_sect:
                    break
            if in_sect:
                out.append(line)
        return "\n".join(out).strip()

    # ── Parser ─────────────────────────────────────────────────────

    def _parse(self, text: str) -> Dict[str, Any]:
        """
        Simple structured Markdown parser.
        Reads:
          - Top-level ## META: key: value pairs (VERSION, STATUS, etc.)
          - ### SECTION blocks: each becomes a dict entry
          - Bare "- KEY: VALUE" lines → flat key-value pairs
        """
        data: Dict[str, Any] = {}
        lines = text.splitlines()
        i = 0
        current_section: Optional[str] = None
        current_section_data: Dict[str, Any] = {}

        while i < len(lines):
            line = lines[i].rstrip()

            # H1 heading — skip
            if line.startswith("# ") and not line.startswith("## "):
                i += 1
                continue

            # H2 heading → new top-level section
            if line.startswith("## ") and not line.startswith("### "):
                if current_section and current_section_data:
                    data[f"section:{current_section}"] = current_section_data
                heading = line[3:].strip()
                # Extract key:value from heading line e.g. "## VERSION: 3.0"
                if ":" in heading:
                    k, _, v = heading.partition(":")
                    data[k.strip()] = v.strip()
                    current_section = k.strip()
                else:
                    current_section = heading
                current_section_data = {"_name": heading}
                i += 1
                continue

            # H3 heading → sub-section
            if line.startswith("### "):
                if current_section and current_section_data:
                    data[f"section:{current_section}"] = current_section_data
                sub_heading = line[4:].strip()
                current_section = sub_heading
                current_section_data = {"_name": sub_heading, "ID": sub_heading}
                i += 1
                continue

            # Bullet key-value: "- KEY: VALUE"
            m = re.match(r"^[-*]\s+([A-Z0-9_]+):\s*(.*)$", line)
            if m:
                k = m.group(1).strip()
                v = m.group(2).strip()
                if current_section_data is not None:
                    current_section_data[k] = v
                else:
                    data[k] = v
                i += 1
                continue

            # Bare key: value (no bullet)
            m2 = re.match(r"^([A-Z0-9_]+):\s+(.+)$", line)
            if m2:
                k = m2.group(1).strip()
                v = m2.group(2).strip()
                if current_section_data is not None:
                    current_section_data[k] = v
                else:
                    data[k] = v
                i += 1
                continue

            i += 1

        # Flush last section
        if current_section and current_section_data:
            data[f"section:{current_section}"] = current_section_data

        return data


# ── Singleton loaders per file ─────────────────────────────────────

_cache: Dict[str, MarkdownConfig] = {}


def load_md(filename: str) -> MarkdownConfig:
    """Load (and cache) a Markdown config from agents/docs/."""
    if filename not in _cache:
        path = AGENTS_DOCS_DIR / filename
        cfg  = MarkdownConfig(path).load()
        _cache[filename] = cfg
    return _cache[filename]


def reload_all():
    """Hot-reload all cached Markdown configs from disk."""
    for filename, cfg in _cache.items():
        cfg.reload()
        log.info("Hot-reloaded: %s", filename)


# ── Convenience accessors ──────────────────────────────────────────

def soul_config() -> MarkdownConfig:
    return load_md("SOUL.md")

def heartbeat_config() -> MarkdownConfig:
    return load_md("HEARTBEAT.md")

def react_loop_config() -> MarkdownConfig:
    return load_md("REACT_LOOP.md")

def memory_config() -> MarkdownConfig:
    return load_md("MEMORY.md")

def bound_config() -> MarkdownConfig:
    return load_md("BOUND.md")
