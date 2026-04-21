"""
obsidian/watcher.py — Vault filesystem watcher (watchdog).

Watches the Obsidian vault directory for changes.
On file create/modify/delete → calls callback with list of changed paths.
Debounced: multiple rapid changes are batched within DEBOUNCE_SECONDS.

Runs as a daemon thread — does not block the main process.
Stopped cleanly via stop() or context manager.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import (
    FileCreatedEvent,
    FileDeletedEvent,
    FileModifiedEvent,
    FileMovedEvent,
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

log = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 2.0


class _VaultEventHandler(FileSystemEventHandler):
    """
    Watchdog event handler that collects changed paths and fires a
    debounced callback.
    """

    def __init__(
        self, callback: Callable[[list[Path]], None], debounce: float
    ) -> None:
        super().__init__()
        self._callback   = callback
        self._debounce   = debounce
        self._pending:   set[Path] = set()
        self._lock       = threading.Lock()
        self._timer:     threading.Timer | None = None

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._add(Path(str(event.src_path)))

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._add(Path(str(event.src_path)))

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory and str(event.src_path).endswith(".md"):
            self._add(Path(str(event.src_path)))

    def on_moved(self, event: FileMovedEvent) -> None:  # type: ignore[override]
        # Treat as delete old + create new
        if not event.is_directory:
            if str(event.src_path).endswith(".md"):
                self._add(Path(str(event.src_path)))
            if str(event.dest_path).endswith(".md"):
                self._add(Path(str(event.dest_path)))

    def _add(self, path: Path) -> None:
        with self._lock:
            self._pending.add(path)
            if self._timer is not None:
                self._timer.cancel()
            self._timer = threading.Timer(self._debounce, self._fire)
            self._timer.daemon = True
            self._timer.start()

    def _fire(self) -> None:
        with self._lock:
            paths = list(self._pending)
            self._pending.clear()
            self._timer = None

        if paths:
            log.debug("Vault watcher firing callback for %d paths", len(paths))
            try:
                self._callback(paths)
            except Exception as e:
                log.error("Vault watcher callback error: %s", e)


class VaultWatcher:
    """
    Watch an Obsidian vault directory for markdown file changes.

    Usage:
        def on_change(paths: list[Path]):
            pipeline.incremental_ingest(paths)

        watcher = VaultWatcher()
        watcher.start(vault_path, on_change)
        ...
        watcher.stop()

    Or as a context manager:
        with VaultWatcher() as w:
            w.start(vault_path, on_change)
    """

    def __init__(self, debounce_seconds: float = DEBOUNCE_SECONDS) -> None:
        self._debounce = debounce_seconds
        self._observer: Observer | None = None

    def start(self, vault_path: Path, callback: Callable[[list[Path]], None]) -> None:
        """
        Start watching vault_path. Calls callback(changed_paths) on changes.
        Non-blocking — runs in a daemon thread.
        """
        if self._observer is not None:
            raise RuntimeError("VaultWatcher already running. Call stop() first.")

        handler  = _VaultEventHandler(callback, self._debounce)
        observer = Observer()
        observer.schedule(handler, str(vault_path), recursive=True)
        observer.daemon = True
        observer.start()
        self._observer = observer
        log.info("VaultWatcher started for %s (debounce=%.1fs)", vault_path, self._debounce)

    def stop(self) -> None:
        """Stop the watcher. Safe to call if not running."""
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5.0)
            self._observer = None
            log.info("VaultWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._observer is not None and self._observer.is_alive()

    def __enter__(self) -> "VaultWatcher":
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
