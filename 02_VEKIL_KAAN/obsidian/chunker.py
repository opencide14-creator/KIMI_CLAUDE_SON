"""
obsidian/chunker.py — Semantic heading-based chunker.

Strategy:
  1. Split body by H2 headings — each H2 section is one semantic unit.
  2. If a section exceeds MAX_WORDS, split further at paragraph boundaries.
  3. Preamble (content before first H2) is its own chunk.
  4. Each chunk carries full metadata: source file, heading path, position.
  5. Frontmatter fields are injected into chunk metadata (not the text).

Chunk ID: "{relative_path}#{section_index}" — stable for same content structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_WORDS = 400   # soft limit per chunk
MIN_WORDS = 10    # chunks below this are merged into previous (avoid tiny orphans)

_PARA_SPLIT_RE = re.compile(r"\n\n+")


@dataclass
class Chunk:
    """
    A single text chunk ready for ChromaDB ingestion.
    """
    chunk_id:    str             # e.g. "concepts/soul-laws.md#0"
    text:        str             # chunk body text
    metadata:    dict[str, Any]  # ChromaDB metadata (all scalar values)

    @property
    def word_count(self) -> int:
        return len(self.text.split())


class SemanticChunker:
    """
    Splits an ObsidianPage into Chunks for ChromaDB ingestion.

    Usage:
        chunker = SemanticChunker()
        chunks = chunker.chunk(page, vault_root)
    """

    def __init__(self, max_words: int = MAX_WORDS, min_words: int = MIN_WORDS) -> None:
        self.max_words = max_words
        self.min_words = min_words

    def chunk(self, page: "ObsidianPage", vault_root: Path) -> list[Chunk]:  # type: ignore[name-defined]
        """
        Split a parsed ObsidianPage into Chunks.
        Returns at least one chunk (even for empty files).
        """
        from obsidian.parser import ObsidianPage

        rel_path = page.relative_path(vault_root)
        sections = page.split_body_by_h2(page.body) if page.body.strip() else []

        if not sections:
            # Empty file — return single empty chunk so the file is tracked
            return [self._make_chunk(
                chunk_id=f"{rel_path}#0",
                text="",
                heading=None,
                page=page,
                rel_path=rel_path,
                section_index=0,
                total_sections=1,
            )]

        chunks: list[Chunk] = []
        section_index = 0
        total_sections = len(sections)

        for heading, content in sections:
            # Split oversized sections at paragraph boundaries
            sub_chunks = self._split_section(content)

            for sub_idx, sub_text in enumerate(sub_chunks):
                if not sub_text.strip():
                    continue

                chunk_id = (
                    f"{rel_path}#{section_index}"
                    if len(sub_chunks) == 1
                    else f"{rel_path}#{section_index}.{sub_idx}"
                )

                chunks.append(self._make_chunk(
                    chunk_id=chunk_id,
                    text=sub_text.strip(),
                    heading=heading,
                    page=page,
                    rel_path=rel_path,
                    section_index=section_index,
                    total_sections=total_sections,
                ))

            section_index += 1

        # Merge orphaned tiny chunks into the previous
        return self._merge_tiny(chunks)

    def _split_section(self, content: str) -> list[str]:
        """
        Split a section into sub-chunks if it exceeds max_words.
        Strategy:
          1. Split at paragraph boundaries (blank lines).
          2. If a single paragraph still exceeds max_words, split at sentence boundaries.
          3. If no splits possible, return as single chunk (hard minimum).
        """
        if len(content.split()) <= self.max_words:
            return [content]

        # Phase 1: paragraph split
        paragraphs = [p.strip() for p in _PARA_SPLIT_RE.split(content) if p.strip()]

        # Phase 2: if single oversized paragraph, split further at sentences
        expanded: list[str] = []
        for para in paragraphs:
            if len(para.split()) > self.max_words:
                # sentence boundary split: after . ! ? followed by whitespace
                sentences = re.split(r"(?<=[.!?])\s+", para)
                current: list[str] = []
                cw = 0
                for sent in sentences:
                    sw = len(sent.split())
                    if cw + sw > self.max_words and current:
                        expanded.append(" ".join(current))
                        current = [sent]
                        cw = sw
                    else:
                        current.append(sent)
                        cw += sw
                if current:
                    expanded.append(" ".join(current))
            else:
                expanded.append(para)

        # Phase 3: batch expanded fragments into chunks within max_words
        sub_chunks: list[str] = []
        current_parts: list[str] = []
        current_words = 0

        for part in expanded:
            words = len(part.split())
            if current_words + words > self.max_words and current_parts:
                sub_chunks.append("\n\n".join(current_parts))
                current_parts = [part]
                current_words = words
            else:
                current_parts.append(part)
                current_words += words

        if current_parts:
            sub_chunks.append("\n\n".join(current_parts))

        # Last resort: if still a single oversized chunk with no splits possible,
        # do hard word-count split (preserves word boundaries)
        if len(sub_chunks) == 1 and len(sub_chunks[0].split()) > self.max_words:
            words = sub_chunks[0].split()
            hard_chunks = []
            for i in range(0, len(words), self.max_words):
                hard_chunks.append(" ".join(words[i:i + self.max_words]))
            return hard_chunks

        return sub_chunks if sub_chunks else [content]

    def _make_chunk(
        self,
        chunk_id: str,
        text: str,
        heading: str | None,
        page: "ObsidianPage",  # type: ignore[name-defined]
        rel_path: str,
        section_index: int,
        total_sections: int,
    ) -> Chunk:
        """Build a Chunk with full metadata from the page."""
        # ChromaDB metadata must be scalar (str, int, float, bool)
        metadata: dict[str, Any] = {
            "source_path":     rel_path,
            "title":           page.title,
            "category":        page.category,
            "section_heading": heading or "__preamble__",
            "section_index":   section_index,
            "total_sections":  total_sections,
            "word_count":      len(text.split()),
            "content_hash":    page.content_hash,
            # Wikilinks as pipe-separated string (scalar for ChromaDB)
            "wikilinks":       "|".join(page.wikilinks) if page.wikilinks else "",
            # Tags as pipe-separated string
            "tags":            "|".join(page.tags) if page.tags else "",
        }

        # Add frontmatter scalars (str/int/float/bool only — skip complex types)
        for k, v in page.frontmatter.items():
            if k in metadata:
                continue
            if isinstance(v, (str, int, float, bool)):
                metadata[f"fm_{k}"] = v
            elif isinstance(v, list) and all(isinstance(i, str) for i in v):
                metadata[f"fm_{k}"] = "|".join(v)

        return Chunk(chunk_id=chunk_id, text=text, metadata=metadata)

    def _merge_tiny(self, chunks: list[Chunk]) -> list[Chunk]:
        """Merge chunks below min_words into the previous chunk."""
        if len(chunks) <= 1:
            return chunks

        result: list[Chunk] = []
        for chunk in chunks:
            if chunk.word_count < self.min_words and result:
                prev = result[-1]
                merged_text = prev.text + "\n\n" + chunk.text
                merged = Chunk(
                    chunk_id=prev.chunk_id,
                    text=merged_text.strip(),
                    metadata={**prev.metadata, "word_count": len(merged_text.split())},
                )
                result[-1] = merged
            else:
                result.append(chunk)

        return result
