"""
obsidian/parser.py — Obsidian markdown parser.

Handles:
  - YAML frontmatter (--- delimited)
  - Wikilinks [[Target]] and [[Target|Alias]]
  - Inline tags #tag
  - H1/H2/H3 heading extraction
  - Content hash (SHA-256) for delta detection
  - Body text (frontmatter stripped)

Deterministic: same file → same parse output always.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from core.exceptions import ObsidianError
from core.hashing import sha256_file, sha256_hex

# ── Regex patterns ────────────────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_WIKILINK_RE    = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]")
_INLINE_TAG_RE  = re.compile(r"(?<!\w)#([a-zA-Z][a-zA-Z0-9_/-]*)")
_HEADING_RE     = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_H2_SPLIT_RE    = re.compile(r"(?=^## .+$)", re.MULTILINE)


@dataclass
class ObsidianPage:
    """Parsed representation of a single Obsidian markdown file."""
    path:          Path
    frontmatter:   dict[str, Any]    = field(default_factory=dict)
    wikilinks:     list[str]         = field(default_factory=list)  # target names
    inline_tags:   list[str]         = field(default_factory=list)
    headings:      list[tuple[int, str]] = field(default_factory=list)  # (level, text)
    body:          str               = ""   # content with frontmatter stripped
    content_hash:  str               = ""   # SHA-256 of raw file bytes

    # Convenience properties
    @property
    def title(self) -> str:
        """Best-guess title: frontmatter title → first H1 → stem of filename."""
        if self.frontmatter.get("title"):
            return str(self.frontmatter["title"])
        for level, text in self.headings:
            if level == 1:
                return text
        return self.path.stem

    @property
    def tags(self) -> list[str]:
        """Merged tags from frontmatter + inline tags, deduplicated."""
        fm_tags = self.frontmatter.get("tags") or []
        if isinstance(fm_tags, str):
            fm_tags = [t.strip() for t in fm_tags.split(",")]
        combined = list(fm_tags) + list(self.inline_tags)
        return list(dict.fromkeys(combined))  # preserve order, deduplicate

    @property
    def category(self) -> str:
        return str(self.frontmatter.get("category", "uncategorized"))

    @property
    def sources(self) -> list[str]:
        src = self.frontmatter.get("sources") or []
        if isinstance(src, str):
            src = [s.strip() for s in src.split(",")]
        return list(src)

    def relative_path(self, vault_root: Path) -> str:
        """Path relative to vault root, using forward slashes."""
        return self.path.relative_to(vault_root).as_posix()

    def split_body_by_h2(self, body: str) -> list[tuple[str | None, str]]:
        """
        Split body by H2 headings.
        Returns list of (heading_text_or_None, section_content) pairs.
        First item has heading=None (preamble before first H2, or whole body if no H2).
        Delegates to ObsidianParser._H2_SPLIT_RE logic.
        """
        parts = _H2_SPLIT_RE.split(body)
        result: list[tuple[str | None, str]] = []
        for part in parts:
            part = part.strip()
            if not part:
                continue
            first_line = part.split("\n", 1)[0]
            if first_line.startswith("## "):
                heading = first_line.lstrip("# ").strip()
                content = part[len(first_line):].strip()
                result.append((heading, content))
            else:
                result.append((None, part))
        return result


class ObsidianParser:
    """
    Parse Obsidian markdown files into structured ObsidianPage objects.
    All operations are deterministic and stateless.
    """

    def parse(self, path: Path) -> ObsidianPage:
        """
        Parse a single markdown file.
        Raises ObsidianError if file cannot be read.
        """
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as e:
            raise ObsidianError(f"Cannot read vault file {path}: {e}") from e

        content_hash = sha256_hex(raw.encode("utf-8"))
        frontmatter, body = self._split_frontmatter(raw)
        wikilinks  = self.extract_wikilinks(body)
        inline_tags = self._extract_inline_tags(body)
        headings   = self._extract_headings(body)

        return ObsidianPage(
            path=path,
            frontmatter=frontmatter,
            wikilinks=wikilinks,
            inline_tags=inline_tags,
            headings=headings,
            body=body,
            content_hash=content_hash,
        )

    def parse_many(self, paths: list[Path]) -> list[ObsidianPage]:
        """Parse multiple files. Skips files that raise ObsidianError (logs warning)."""
        import logging
        log = logging.getLogger(__name__)
        pages = []
        for p in paths:
            try:
                pages.append(self.parse(p))
            except ObsidianError as e:
                log.warning("Skipping %s: %s", p, e)
        return pages

    # ── Frontmatter ───────────────────────────────────────────────────────────

    def _split_frontmatter(self, raw: str) -> tuple[dict[str, Any], str]:
        """
        Split raw file content into (frontmatter_dict, body_text).
        Returns ({}, raw) if no frontmatter present.
        """
        m = _FRONTMATTER_RE.match(raw)
        if not m:
            return {}, raw

        try:
            fm = yaml.safe_load(m.group(1)) or {}
        except yaml.YAMLError:
            fm = {}

        # Normalise YAML datetime → ISO string (for JSON serialisability)
        for k, v in list(fm.items()):
            if isinstance(v, datetime):
                fm[k] = v.isoformat()

        body = raw[m.end():]
        return fm, body

    # ── Wikilinks ─────────────────────────────────────────────────────────────

    def extract_wikilinks(self, content: str) -> list[str]:
        """
        Extract all wikilink targets from content.
        [[Target]]           → "Target"
        [[Target|Alias]]     → "Target"
        Deduplicated, preserving first-seen order.
        """
        found = _WIKILINK_RE.findall(content)
        return list(dict.fromkeys(found))

    def resolve_wikilink(self, link: str, vault_root: Path) -> Path | None:
        """
        Try to find the file a wikilink points to.
        Search order: exact match → case-insensitive → add .md suffix.
        Returns None if not found.
        """
        candidates = [
            vault_root / link,
            vault_root / f"{link}.md",
        ]
        for c in candidates:
            if c.exists():
                return c
        # Case-insensitive search (for cross-platform compatibility)
        link_lower = link.lower()
        for md_file in vault_root.rglob("*.md"):
            if md_file.stem.lower() == link_lower:
                return md_file
        return None

    # ── Tags & headings ───────────────────────────────────────────────────────

    def _extract_inline_tags(self, body: str) -> list[str]:
        """Extract #hashtags from body text. Deduplicated, preserving order."""
        found = _INLINE_TAG_RE.findall(body)
        return list(dict.fromkeys(found))

    def _extract_headings(self, body: str) -> list[tuple[int, str]]:
        """Extract (level, text) tuples for H1–H6 headings."""
        return [
            (len(hashes), text.strip())
            for hashes, text in _HEADING_RE.findall(body)
        ]

    # ── Body sections ─────────────────────────────────────────────────────────

    def split_body_by_h2(self, body: str) -> list[tuple[str | None, str]]:
        """
        Split body by H2 headings.
        Returns list of (heading_text_or_None, section_content) pairs.
        First item has heading=None (preamble before first H2, or whole body if no H2).
        """
        parts = _H2_SPLIT_RE.split(body)
        result: list[tuple[str | None, str]] = []

        for part in parts:
            part = part.strip()
            if not part:
                continue
            # Check if part starts with H2
            first_line = part.split("\n", 1)[0]
            if first_line.startswith("## "):
                heading = first_line.lstrip("# ").strip()
                content = part[len(first_line):].strip()
                result.append((heading, content))
            else:
                result.append((None, part))

        return result
