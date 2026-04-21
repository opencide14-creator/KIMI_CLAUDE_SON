"""
obsidian/vault.py — Obsidian vault interface.

Thin wrapper around the filesystem. Knows about vault structure:
  - Lists markdown files (skips dotfiles, _raw/, _archives/)
  - Resolves wikilinks within the vault
  - Reports vault statistics
"""

from __future__ import annotations

import logging
from pathlib import Path

from core.exceptions import VaultNotFound

log = logging.getLogger(__name__)

# Directories inside the vault to skip during ingest
SKIP_DIRS = {"_raw", "_archives", ".obsidian", ".git", "__pycache__"}


class ObsidianVault:
    """
    Vault filesystem interface.

    vault = ObsidianVault(path)
    vault.verify()        # raises VaultNotFound if path invalid
    files = vault.list_markdown_files()
    """

    def __init__(self, vault_path: Path) -> None:
        self._path = vault_path

    @property
    def path(self) -> Path:
        return self._path

    def verify(self) -> None:
        """
        Raise VaultNotFound if vault path doesn't exist or isn't a directory.
        """
        if not self._path.exists():
            raise VaultNotFound(
                f"Obsidian vault not found: {self._path}. "
                f"Set OBSIDIAN_VAULT_PATH in .env to a valid directory."
            )
        if not self._path.is_dir():
            raise VaultNotFound(
                f"OBSIDIAN_VAULT_PATH is not a directory: {self._path}"
            )

    def list_markdown_files(self) -> list[Path]:
        """
        Recursively list all .md files in the vault.
        Skips: hidden files/dirs (starting with .), SKIP_DIRS, non-.md files.
        Returns sorted list for deterministic ordering.
        """
        files: list[Path] = []
        for path in sorted(self._path.rglob("*.md")):
            # Skip hidden paths
            if any(part.startswith(".") for part in path.parts):
                continue
            # Skip skip-dirs
            rel_parts = path.relative_to(self._path).parts
            if any(part in SKIP_DIRS for part in rel_parts):
                continue
            files.append(path)
        return files

    def read_file(self, path: Path) -> str:
        """Read a vault file as UTF-8 text."""
        try:
            return path.read_text(encoding="utf-8")
        except OSError as e:
            from core.exceptions import ObsidianError
            raise ObsidianError(f"Cannot read {path}: {e}") from e

    def stat(self) -> dict[str, int]:
        """Return basic vault statistics."""
        files = self.list_markdown_files()
        total_size = sum(f.stat().st_size for f in files if f.exists())
        return {
            "markdown_files": len(files),
            "total_bytes": total_size,
        }

    def exists(self) -> bool:
        return self._path.exists() and self._path.is_dir()
