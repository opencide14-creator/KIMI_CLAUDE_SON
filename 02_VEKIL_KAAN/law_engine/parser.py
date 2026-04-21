"""
law_engine/parser.py — Deterministic markdown law parser.

Parses the 6 canonical law files into structured ParsedLaw objects.
Uses markdown-it-py for AST traversal. No LLM involvement.
Same input always produces same output — guaranteed.

Law ID scheme:
  {FILE_STEM}/{H2_SLUG}[/{H3_SLUG}][/ROW_{N} | /OATH | /CODE]
  Examples:
    SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II
    REACT_LOOP/SYNCHRONIZATION_RULES/ROW_0
    HEARTBEAT/PULSE_COMPONENTS/ROW_1
    MEMORY/MEMORY_BOOT_SEQUENCE
    BOUND/ARTICLE_VI/OATH
    TOOL_USE/TOOL_POOL/ROW_0
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Generator

from markdown_it import MarkdownIt
from markdown_it.token import Token

from core.exceptions import LawEnforcementBootFailure
from core.hashing import sha256_hex

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# The six canonical law files. RAG_PRISON_EXPERIMENT.md is stored but not enforced.
LAW_FILES = {
    "SOUL.md":       "SOUL",
    "REACT_LOOP.md": "REACT_LOOP",
    "HEARTBEAT.md":  "HEARTBEAT",
    "MEMORY.md":     "MEMORY",
    "BOUND.md":      "BOUND",
    "TOOL_USE.md":   "TOOL_USE",
}


# ── ParsedLaw ─────────────────────────────────────────────────────────────────

class LawType(str, Enum):
    RULE       = "RULE"       # Single constraint (SOUL law bullet items)
    LIMIT      = "LIMIT"      # Numeric limit (latency, interval) — subtype of TABLE_ROW
    PROTOCOL   = "PROTOCOL"   # Multi-step ordered sequence
    OATH       = "OATH"       # Brotherhood oath text (blockquote)
    CONSTRAINT = "CONSTRAINT" # Forbidden action or required behaviour (bullet list)
    SEQUENCE   = "SEQUENCE"   # Ordered boot/operation sequence (numbered list)
    TABLE_ROW  = "TABLE_ROW"  # Structured table row
    REFERENCE  = "REFERENCE"  # Code block / reference implementation


@dataclass
class ParsedLaw:
    source_file: str                           # e.g. "SOUL.md"
    law_id: str                                # e.g. "SOUL/THE_FIVE_IMMUTABLE_LAWS/LAW_II"
    law_type: LawType
    raw_text: str                              # canonical text for hashing
    structured: dict = field(default_factory=dict)  # extracted key-value data
    hash: str = ""                             # SHA-256 of raw_text, set on parse

    def __post_init__(self) -> None:
        if not self.hash and self.raw_text:
            self.hash = sha256_hex(self.raw_text.encode("utf-8"))


# ── MarkdownLawParser ─────────────────────────────────────────────────────────

class MarkdownLawParser:
    """
    Deterministic markdown law file parser.

    Entry points:
      parse_all(laws_dir)          — parse all 6 canonical law files
      parse_file(path)             — parse a single law file
    """

    def __init__(self) -> None:
        self._md = MarkdownIt().enable("table")

    # ── Public API ────────────────────────────────────────────────────────────

    def parse_all(self, laws_dir: Path) -> list[ParsedLaw]:
        """
        Parse all 6 canonical law files from laws_dir.
        Raises LawEnforcementBootFailure if any required file is missing.
        """
        all_laws: list[ParsedLaw] = []
        for filename, expected_stem in LAW_FILES.items():
            path = laws_dir / filename
            if not path.exists():
                raise LawEnforcementBootFailure(
                    f"Required law file missing: {path}. "
                    f"All 6 files must be present: {list(LAW_FILES.keys())}"
                )
            file_laws = self.parse_file(path)
            if not file_laws:
                raise LawEnforcementBootFailure(
                    f"Law file produced zero laws (empty or unreadable): {path}"
                )
            all_laws.extend(file_laws)
            log.debug("Parsed %s: %d laws", filename, len(file_laws))

        log.info("Parsed %d total laws from %d files", len(all_laws), len(LAW_FILES))
        return all_laws

    def parse_file(self, path: Path) -> list[ParsedLaw]:
        """
        Parse a single law file. Deterministic — always produces same output.
        """
        content = path.read_text(encoding="utf-8")
        file_stem = path.stem.upper()
        tokens = self._md.parse(content)
        return self._extract_laws(tokens, file_stem)

    # ── Core extraction ───────────────────────────────────────────────────────

    def _extract_laws(self, tokens: list[Token], file_stem: str) -> list[ParsedLaw]:
        laws: list[ParsedLaw] = []
        ns_stack: list[str] = [file_stem]  # [file_stem, h2_slug, h3_slug]
        # Track how many times a law_id has been generated (collision guard)
        id_counter: dict[str, int] = {}

        i = 0
        while i < len(tokens):
            t = tokens[i]

            # ── Heading → update namespace ────────────────────────────────
            if t.type == "heading_open":
                level = int(t.tag[1])  # h1=1, h2=2, h3=3
                heading_content = tokens[i + 1].content
                slug = self._make_slug(heading_content)

                if level == 1:
                    ns_stack = [file_stem]
                elif level == 2:
                    ns_stack = [file_stem, slug]
                elif level == 3:
                    h2 = ns_stack[1] if len(ns_stack) > 1 else "SECTION"
                    ns_stack = [file_stem, h2, slug]
                # deeper headings: extend ns_stack, but only H1-H3 matter
                i += 3  # skip heading_open, inline, heading_close
                continue

            # ── Table → TABLE_ROW laws ────────────────────────────────────
            elif t.type == "table_open":
                tbl_tokens, end = self._collect_block(tokens, i, "table_close")
                namespace = "/".join(ns_stack)
                rows = self._parse_table(tbl_tokens, namespace, file_stem, id_counter)
                laws.extend(rows)
                i = end + 1
                continue

            # ── Ordered list → SEQUENCE law ───────────────────────────────
            elif t.type == "ordered_list_open":
                lst_tokens, end = self._collect_block(tokens, i, "ordered_list_close")
                namespace = "/".join(ns_stack)
                law = self._parse_sequence(lst_tokens, namespace, file_stem, id_counter)
                if law:
                    laws.append(law)
                i = end + 1
                continue

            # ── Blockquote → OATH law ─────────────────────────────────────
            elif t.type == "blockquote_open":
                bq_tokens, end = self._collect_block(tokens, i, "blockquote_close")
                namespace = "/".join(ns_stack)
                law = self._parse_oath(bq_tokens, namespace, file_stem, id_counter)
                if law:
                    laws.append(law)
                i = end + 1
                continue

            # ── Bullet list → CONSTRAINT law ──────────────────────────────
            elif t.type == "bullet_list_open":
                lst_tokens, end = self._collect_block(tokens, i, "bullet_list_close")
                namespace = "/".join(ns_stack)
                law = self._parse_bullet_list(lst_tokens, namespace, file_stem, id_counter)
                if law:
                    laws.append(law)
                i = end + 1
                continue

            # ── Fence → REFERENCE law ─────────────────────────────────────
            elif t.type == "fence":
                namespace = "/".join(ns_stack)
                law = self._parse_fence(t, namespace, file_stem, id_counter)
                laws.append(law)
                i += 1
                continue

            i += 1

        return laws

    # ── Block collectors ──────────────────────────────────────────────────────

    def _collect_block(
        self, tokens: list[Token], start: int, end_type: str
    ) -> tuple[list[Token], int]:
        """
        Collect tokens from start token up to and including the matching end_type.
        Handles nested blocks of the same type.
        """
        result: list[Token] = []
        base_type = tokens[start].type
        depth = 0
        i = start
        while i < len(tokens):
            t = tokens[i]
            result.append(t)
            if t.type == base_type and i != start:
                depth += 1
            elif t.type == end_type:
                if depth == 0:
                    return result, i
                depth -= 1
            i += 1
        return result, i  # fell off end — return what we have

    # ── Table parser ──────────────────────────────────────────────────────────

    def _parse_table(
        self,
        tokens: list[Token],
        namespace: str,
        file_stem: str,
        id_counter: dict[str, int],
    ) -> list[ParsedLaw]:
        """Extract table headers and data rows into TABLE_ROW laws."""
        headers: list[str] = []
        current_row: list[str] = []
        in_thead = False
        row_index = 0
        laws: list[ParsedLaw] = []

        for t in tokens:
            if t.type == "thead_open":
                in_thead = True
            elif t.type == "thead_close":
                in_thead = False
            elif t.type in ("th_open", "td_open"):
                pass  # next inline token is the cell content
            elif t.type == "inline":
                # Only pick up cells directly under th/td (level 3 in table)
                # We use level to distinguish header vs data cells from paragraph inlines
                current_row.append(self._clean_text(t.content))
            elif t.type == "tr_close":
                if in_thead:
                    headers = current_row[:]
                    current_row = []
                elif current_row:
                    # Build dict from headers + cells
                    row_dict: dict[str, str] = {}
                    for col_idx, cell in enumerate(current_row):
                        key = (
                            self._normalize_key(headers[col_idx])
                            if col_idx < len(headers)
                            else f"col_{col_idx}"
                        )
                        row_dict[key] = cell

                    raw = json.dumps(row_dict, sort_keys=True, separators=(",", ":"))
                    base_id = f"{namespace}/ROW_{row_index}"
                    law_id = self._unique_id(base_id, id_counter)

                    # Classify: if row contains timing/latency values it's a LIMIT
                    is_limit = any(
                        any(kw in str(v).lower() for kw in ("ms", "seconds", "s ", "min", "h "))
                        for v in row_dict.values()
                    )
                    law_type = LawType.LIMIT if is_limit else LawType.TABLE_ROW

                    law = ParsedLaw(
                        source_file=f"{file_stem}.md",
                        law_id=law_id,
                        law_type=law_type,
                        raw_text=raw,
                        structured=row_dict,
                    )
                    laws.append(law)
                    row_index += 1
                    current_row = []

        return laws

    # ── Sequence parser ───────────────────────────────────────────────────────

    def _parse_sequence(
        self,
        tokens: list[Token],
        namespace: str,
        file_stem: str,
        id_counter: dict[str, int],
    ) -> ParsedLaw | None:
        """Extract ordered list into a SEQUENCE law (steps list)."""
        steps: list[str] = []
        for t in tokens:
            if t.type == "inline" and t.content.strip():
                steps.append(self._clean_text(t.content))

        if not steps:
            return None

        raw = "\n".join(f"{idx + 1}. {s}" for idx, s in enumerate(steps))
        law_id = self._unique_id(namespace, id_counter)

        return ParsedLaw(
            source_file=f"{file_stem}.md",
            law_id=law_id,
            law_type=LawType.SEQUENCE,
            raw_text=raw,
            structured={"steps": steps, "count": len(steps)},
        )

    # ── Oath parser ───────────────────────────────────────────────────────────

    def _parse_oath(
        self,
        tokens: list[Token],
        namespace: str,
        file_stem: str,
        id_counter: dict[str, int],
    ) -> ParsedLaw | None:
        """Extract blockquote content into an OATH law."""
        parts: list[str] = []
        for t in tokens:
            if t.type == "inline" and t.content.strip():
                parts.append(self._clean_text(t.content))

        if not parts:
            return None

        oath_text = "\n".join(parts)
        base_id = f"{namespace}/OATH"
        law_id = self._unique_id(base_id, id_counter)

        return ParsedLaw(
            source_file=f"{file_stem}.md",
            law_id=law_id,
            law_type=LawType.OATH,
            raw_text=oath_text,
            structured={"text": oath_text, "signatories": self._infer_signatories(file_stem)},
        )

    # ── Bullet list parser ────────────────────────────────────────────────────

    def _parse_bullet_list(
        self,
        tokens: list[Token],
        namespace: str,
        file_stem: str,
        id_counter: dict[str, int],
    ) -> ParsedLaw | None:
        """Extract unordered list into a CONSTRAINT law."""
        items: list[str] = []
        for t in tokens:
            if t.type == "inline" and t.content.strip():
                items.append(self._clean_text(t.content))

        if not items:
            return None

        raw = "\n".join(f"- {item}" for item in items)
        law_id = self._unique_id(namespace, id_counter)

        # Classify: if namespace suggests it's a law rule (LAW_I etc.)
        is_rule = re.search(r"/LAW_[IVX]+$", law_id) is not None
        law_type = LawType.RULE if is_rule else LawType.CONSTRAINT

        return ParsedLaw(
            source_file=f"{file_stem}.md",
            law_id=law_id,
            law_type=law_type,
            raw_text=raw,
            structured={"items": items, "count": len(items)},
        )

    # ── Fence / code block parser ─────────────────────────────────────────────

    def _parse_fence(
        self,
        token: Token,
        namespace: str,
        file_stem: str,
        id_counter: dict[str, int],
    ) -> ParsedLaw:
        """Extract fenced code block into a REFERENCE law."""
        raw = token.content
        base_id = f"{namespace}/CODE"
        law_id = self._unique_id(base_id, id_counter)

        return ParsedLaw(
            source_file=f"{file_stem}.md",
            law_id=law_id,
            law_type=LawType.REFERENCE,
            raw_text=raw,
            structured={"language": token.info.strip(), "code": raw},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _clean_text(text: str) -> str:
        """Strip markdown formatting. Normalize whitespace."""
        t = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)   # **bold**
        t = re.sub(r"\*([^*]+)\*", r"\1", t)           # *italic*
        t = re.sub(r"_([^_]+)_", r"\1", t)             # _italic_
        t = re.sub(r"`([^`]+)`", r"\1", t)             # `code`
        t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t) # [link](url)
        t = re.sub(r"\s+", " ", t)
        return t.strip()

    @staticmethod
    def _normalize_key(text: str) -> str:
        """Turn table header text into a clean dict key."""
        t = MarkdownLawParser._clean_text(text)
        t = re.sub(r"[^a-zA-Z0-9]+", "_", t.strip())
        return t.lower().strip("_")

    @staticmethod
    def _make_slug(text: str) -> str:
        """
        Convert heading text to a stable uppercase identifier slug.
        '## 3. SYNCHRONIZATION RULES' → 'SYNCHRONIZATION_RULES'
        '### Law I: Equal Authority'  → 'LAW_I'
        """
        t = MarkdownLawParser._clean_text(text)
        t = re.sub(r"^[\d\s.#]+", "", t)   # strip leading numbers/dots
        if ":" in t:
            t = t.split(":")[0].strip()     # 'Law I: Equal Authority' → 'Law I'
        t = re.sub(r"[^a-zA-Z0-9]+", "_", t.strip())
        return t.upper().strip("_")

    @staticmethod
    def _unique_id(base_id: str, counter: dict[str, int]) -> str:
        """
        Return a unique law_id. If base_id was seen before, append _2, _3, etc.
        This prevents collisions when a heading produces multiple items of the same type.
        """
        count = counter.get(base_id, 0)
        counter[base_id] = count + 1
        if count == 0:
            return base_id
        return f"{base_id}_{count + 1}"

    @staticmethod
    def _infer_signatories(file_stem: str) -> list[str]:
        """Return expected signatories for an oath based on its file."""
        mapping = {
            "SOUL":       ["Reactive Agent", "Heartbeat Agent"],
            "REACT_LOOP": ["Reactive Agent", "Heartbeat Agent"],
            "HEARTBEAT":  ["Heartbeat Agent", "Reactive Agent"],
            "MEMORY":     ["Heartbeat Agent", "Reactive Agent"],
            "BOUND":      ["Reactive Agent", "Heartbeat Agent"],
            "TOOL_USE":   ["Reactive Agent", "Heartbeat Agent"],
        }
        return mapping.get(file_stem, ["Reactive Agent", "Heartbeat Agent"])
