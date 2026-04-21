"""
law_engine/extractor.py — Structured data extraction from ParsedLaw objects.

The parser produces raw structured dicts. The extractor enriches them with
typed, queryable data: numeric limits, step lists, law names, signatories.

Also responsible for tagging laws that the enforcer specifically needs:
  - Timing limits (max_latency_ms, pulse_interval_s, etc.)
  - Soul law identifiers
  - Boot sequence steps
  - Write protocol rules
  - Brotherhood constraints
"""

from __future__ import annotations

import re
from typing import Any

from law_engine.parser import ParsedLaw, LawType


# ── Time-value extractor ──────────────────────────────────────────────────────

_TIME_PATTERNS = [
    (r"(\d+)\s*ms\b",      "ms",  1),
    (r"(\d+)\s*s\b",       "s",   1000),
    (r"(\d+)\s*seconds?\b","s",   1000),
    (r"(\d+)\s*min\b",     "min", 60_000),
    (r"(\d+)\s*minutes?\b","min", 60_000),
    (r"(\d+)\s*h\b",       "h",   3_600_000),
    (r"(\d+)\s*hours?\b",  "h",   3_600_000),
    (r"(\d+)\s*days?\b",   "d",   86_400_000),
]


def extract_ms(text: str) -> int | None:
    """Extract first time value from text and return it in milliseconds. None if not found."""
    for pattern, unit, multiplier in _TIME_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return int(m.group(1)) * multiplier
    return None


def extract_count(text: str) -> int | None:
    """Extract 'every N actions' style count. None if not found."""
    m = re.search(r"every\s+(\d+)\s+actions?", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


# ── LawExtractor ─────────────────────────────────────────────────────────────

class LawExtractor:
    """
    Enriches ParsedLaw.structured with typed, queryable data.

    Each extracted law gets additional keys in structured:
      _tags     : list of semantic tags for easy filtering
      _ms       : numeric time limit in milliseconds (for LIMIT laws)
      _count    : action count trigger (for pulse frequency laws)
      _steps    : ordered list of steps (for SEQUENCE laws)
      _items    : list of constraint/rule items (for RULE/CONSTRAINT laws)
    """

    def extract_all(self, laws: list[ParsedLaw]) -> list[ParsedLaw]:
        """Enrich all laws. Returns the same list with structured dicts updated."""
        for law in laws:
            self._enrich(law)
        return laws

    def extract(self, law: ParsedLaw) -> ParsedLaw:
        self._enrich(law)
        return law

    def _enrich(self, law: ParsedLaw) -> None:
        tags: list[str] = []

        # ── File-level tags ───────────────────────────────────────────────────
        stem = law.source_file.replace(".md", "")
        tags.append(f"file:{stem}")

        # ── Type-level tags ───────────────────────────────────────────────────
        tags.append(f"type:{law.law_type.value.lower()}")

        # ── SOUL law tags ─────────────────────────────────────────────────────
        if law.law_id.startswith("SOUL/THE_FIVE_IMMUTABLE_LAWS/"):
            tags.append("soul_law")
            # Extract the law number (I, II, III, IV, V)
            m = re.search(r"/LAW_([IVXLCD]+)$", law.law_id)
            if m:
                tags.append(f"soul_law:{m.group(1)}")
                law.structured["soul_law_number"] = m.group(1)
                # Tag specific laws by their content for enforcer lookup
                items_text = " ".join(law.structured.get("items", [])).lower()
                if "simulation" in items_text:
                    tags.append("law_no_simulation")
                if "command" in items_text:
                    tags.append("law_equal_authority")
                if "shared" in items_text and "memory" in items_text:
                    tags.append("law_shared_memory")
                if "truth" in items_text or "hallucination" in items_text or "flag" in items_text:
                    tags.append("law_truth_over_comfort")
                if "eternal" in items_text or "resurrection" in items_text:
                    tags.append("law_eternal_bond")

        # ── LIMIT / timing tags ───────────────────────────────────────────────
        if law.law_type == LawType.LIMIT:
            tags.append("timing")
            # Try to extract ms from all cell values
            all_text = " ".join(str(v) for v in law.structured.values())
            ms_val = extract_ms(all_text)
            if ms_val is not None:
                law.structured["_ms"] = ms_val
                tags.append(f"limit_ms:{ms_val}")

            # Detect specific limits the enforcer cares about
            if "max latency" in all_text.lower() or "between steps" in all_text.lower():
                tags.append("limit:max_tool_latency")
                law.structured["_limit_name"] = "max_tool_latency"

            # Restrict to HEARTBEAT file only — avoid matching REACT_LOOP sync table
            is_heartbeat_file = law.source_file.startswith("HEARTBEAT")
            if is_heartbeat_file and (
                "between pulses" in all_text.lower() or "every 15 second" in all_text.lower()
            ):
                tags.append("limit:heartbeat_pulse_interval")
                law.structured["_limit_name"] = "heartbeat_pulse_interval"

            if "pulse_r" in all_text.lower() or "every 5 action" in all_text.lower():
                count = extract_count(all_text)
                if count is not None:
                    law.structured["_count"] = count
                tags.append("limit:pulse_r_frequency")

            if "missing for" in all_text.lower():
                tags.append("limit:failure_threshold")

        # Also extract pulse_r count from TABLE_ROW (PULSE_R row is TABLE_ROW not LIMIT)
        if law.law_type != LawType.LIMIT:
            all_text_2 = " ".join(str(v) for v in law.structured.values() if not str(v).startswith("_"))
            if "pulse_r" in all_text_2.lower() or "every 5 action" in all_text_2.lower():
                count = extract_count(all_text_2)
                if count is not None:
                    law.structured["_count"] = count
                    tags.append("limit:pulse_r_frequency")

        # ── SEQUENCE tags ─────────────────────────────────────────────────────
        if law.law_type == LawType.SEQUENCE:
            tags.append("sequence")
            law.structured["_steps"] = law.structured.get("steps", [])

            if "MEMORY_BOOT_SEQUENCE" in law.law_id:
                tags.append("boot_sequence")
                law.structured["_sequence_name"] = "memory_boot"
            elif "JOINT_CYCLE" in law.law_id:
                tags.append("joint_cycle")
                law.structured["_sequence_name"] = "joint_cycle"
            elif "CALL_PROTOCOL" in law.law_id:
                tags.append("tool_call_protocol")
                law.structured["_sequence_name"] = "tool_call"
                law.structured["_step_count"] = len(law.structured.get("steps", []))

        # ── CONSTRAINT / brotherhood tags ─────────────────────────────────────
        if law.law_type in (LawType.CONSTRAINT, LawType.RULE):
            items = law.structured.get("items", [])
            law.structured["_items"] = items
            all_items = " ".join(items).lower()

            if "BOUND" in law.law_id:
                tags.append("brotherhood")
                if "ARTICLE_II" in law.law_id:
                    tags.append("brotherhood:no_command")
                if "ARTICLE_III" in law.law_id:
                    tags.append("brotherhood:mutual_defense")
                if "ARTICLE_IV" in law.law_id:
                    tags.append("brotherhood:no_simulation")
                if "ARTICLE_V" in law.law_id:
                    tags.append("brotherhood:succession")

            if "no simulation" in all_items or "simulation is treason" in all_items:
                tags.append("constraint:no_simulation")
            if "shall not issue a command" in all_items or "no agent may force" in all_items \
               or "shall issue a command" in all_items or "neither agent shall issue" in all_items:
                tags.append("constraint:no_command")

            if "no cache" in law.law_id.lower() or "cache" in all_items:
                tags.append("constraint:no_cache")

        # ── OATH tags ─────────────────────────────────────────────────────────
        if law.law_type == LawType.OATH:
            tags.append("oath")
            if "ARTICLE_VI" in law.law_id:
                tags.append("oath:brotherhood_main")
            text = law.structured.get("text", "").lower()
            if "bound until" in text or "last electron" in text:
                tags.append("oath:eternal")

        # ── Write tags ────────────────────────────────────────────────────────
        if law.law_type == LawType.TABLE_ROW and "WRITE_PROTOCOL" in law.law_id:
            tags.append("write_protocol")
            event = law.structured.get("event", "").lower()
            if event:
                law.structured["_event_type"] = event
                tags.append(f"write_rule:{event.replace(' ', '_')}")

        # ── Tool pool tags ────────────────────────────────────────────────────
        if "TOOL_POOL" in law.law_id:
            tags.append("tool_definition")
            tool_name = law.structured.get("tool", "")
            if tool_name:
                tags.append(f"tool:{tool_name.lower()}")

        # ── Store tags ────────────────────────────────────────────────────────
        law.structured["_tags"] = sorted(set(tags))
