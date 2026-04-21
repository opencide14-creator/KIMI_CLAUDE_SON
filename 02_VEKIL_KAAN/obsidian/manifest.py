"""
obsidian/manifest.py — .manifest.json delta tracker.

The manifest tracks every vault file that has been ingested:
  - content_hash: SHA-256 of file bytes at ingest time
  - ingested_at: ISO8601 timestamp
  - chunk_ids: list of ChromaDB chunk IDs produced
  - title: page title at ingest time

On next ingest: recompute hash, compare to manifest.
  hash match → skip (content identical, even if mtime changed)
  hash diff  → re-ingest (content changed)
  not in manifest → new file, ingest

Manifest lives at: {vault_root}/.manifest.json
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import ManifestCorrupted
from core.hashing import sha256_hex

log = logging.getLogger(__name__)

MANIFEST_FILENAME = ".manifest.json"


class ManifestEntry:
    """Single file entry in the manifest."""
    __slots__ = ("rel_path", "content_hash", "ingested_at", "chunk_ids", "title")

    def __init__(
        self,
        rel_path: str,
        content_hash: str,
        ingested_at: str,
        chunk_ids: list[str],
        title: str = "",
    ) -> None:
        self.rel_path     = rel_path
        self.content_hash = content_hash
        self.ingested_at  = ingested_at
        self.chunk_ids    = chunk_ids
        self.title        = title

    def to_dict(self) -> dict[str, Any]:
        return {
            "rel_path":     self.rel_path,
            "content_hash": self.content_hash,
            "ingested_at":  self.ingested_at,
            "chunk_ids":    self.chunk_ids,
            "title":        self.title,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ManifestEntry":
        return cls(
            rel_path=d["rel_path"],
            content_hash=d["content_hash"],
            ingested_at=d.get("ingested_at", ""),
            chunk_ids=d.get("chunk_ids", []),
            title=d.get("title", ""),
        )


class ManifestManager:
    """
    Load, query, update, and persist the .manifest.json file.

    Usage:
        mgr = ManifestManager()
        manifest = mgr.load(vault_path)              # or {} if first run
        needs = mgr.needs_ingest(page_path, manifest, vault_root)
        manifest = mgr.update_entry(manifest, page, chunk_ids, vault_root)
        mgr.save(vault_path, manifest)
    """

    # ── Load / save ───────────────────────────────────────────────────────────

    def load(self, vault_path: Path) -> dict[str, ManifestEntry]:
        """
        Load manifest from vault_path/.manifest.json.
        Returns empty dict on first run.
        Raises ManifestCorrupted if file exists but is invalid JSON.
        """
        manifest_path = vault_path / MANIFEST_FILENAME
        if not manifest_path.exists():
            log.debug("Manifest not found — first run, starting empty")
            return {}

        try:
            raw = manifest_path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as e:
            raise ManifestCorrupted(
                f"Manifest at {manifest_path} is corrupted: {e}. "
                f"Delete it to force a full re-ingest."
            ) from e

        manifest: dict[str, ManifestEntry] = {}
        for rel_path, entry_dict in data.items():
            try:
                manifest[rel_path] = ManifestEntry.from_dict(entry_dict)
            except (KeyError, TypeError) as e:
                log.warning("Skipping corrupt manifest entry '%s': %s", rel_path, e)

        log.debug("Manifest loaded: %d entries", len(manifest))
        return manifest

    def save(self, vault_path: Path, manifest: dict[str, ManifestEntry]) -> None:
        """Persist manifest to vault_path/.manifest.json."""
        manifest_path = vault_path / MANIFEST_FILENAME
        data = {rel: entry.to_dict() for rel, entry in manifest.items()}
        try:
            manifest_path.write_text(
                json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as e:
            raise ManifestCorrupted(f"Cannot write manifest to {manifest_path}: {e}") from e
        log.debug("Manifest saved: %d entries", len(manifest))

    # ── Delta detection ───────────────────────────────────────────────────────

    def needs_ingest(
        self,
        file_path: Path,
        manifest: dict[str, ManifestEntry],
        vault_root: Path,
    ) -> bool:
        """
        Return True if file needs to be (re-)ingested.
        False if content_hash matches the manifest entry.
        """
        rel_path = file_path.relative_to(vault_root).as_posix()
        entry = manifest.get(rel_path)
        if entry is None:
            return True  # new file

        current_hash = sha256_hex(file_path.read_bytes())
        return current_hash != entry.content_hash

    def update_entry(
        self,
        manifest: dict[str, ManifestEntry],
        rel_path: str,
        content_hash: str,
        chunk_ids: list[str],
        title: str = "",
    ) -> dict[str, ManifestEntry]:
        """
        Add or update an entry in the manifest dict.
        Returns the updated manifest (mutates in place and returns it).
        """
        manifest[rel_path] = ManifestEntry(
            rel_path=rel_path,
            content_hash=content_hash,
            ingested_at=datetime.now(timezone.utc).isoformat(),
            chunk_ids=chunk_ids,
            title=title,
        )
        return manifest

    def get_stale_chunk_ids(
        self,
        rel_path: str,
        manifest: dict[str, ManifestEntry],
    ) -> list[str]:
        """
        Return chunk IDs from the previous ingest of this file.
        These must be deleted from ChromaDB before re-ingesting.
        """
        entry = manifest.get(rel_path)
        return entry.chunk_ids if entry else []

    def get_all_tracked_paths(self, manifest: dict[str, ManifestEntry]) -> set[str]:
        """Return set of all rel_paths tracked in the manifest."""
        return set(manifest.keys())

    def count(self, manifest: dict[str, ManifestEntry]) -> int:
        return len(manifest)

    def total_chunks(self, manifest: dict[str, ManifestEntry]) -> int:
        return sum(len(e.chunk_ids) for e in manifest.values())
