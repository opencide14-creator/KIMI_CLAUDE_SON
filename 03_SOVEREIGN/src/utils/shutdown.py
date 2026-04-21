"""Graceful Shutdown Protocol — standardized lifecycle management for all services.

Ensures resources are properly released on exit with timeout-based cleanup.
Applies to ProxyEngine, GatewayRouter, AgentMemory, and any service requiring
orderly shutdown.
"""
from __future__ import annotations
import asyncio
import logging
import threading
import time
from typing import Any, Optional

log = logging.getLogger(__name__)


class ShutdownProtocol:
    """Standardized graceful shutdown for all services.

    Handles async and sync resources uniformly with timeout-based enforcement.
    Logs all operations for auditability.

    Usage:
        protocol = ShutdownProtocol("ProxyEngine", timeout=10.0)
        await protocol.shutdown(
            self._server,
            self._addon,
            self._thread,
        )
    """

    def __init__(self, service_name: str, timeout: float = 5.0):
        """Initialize shutdown protocol.

        Args:
            service_name: Human-readable name for logging.
            timeout: Total shutdown timeout in seconds. Default 5.0s.
        """
        self.service_name = service_name
        self.timeout = timeout
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self):
        """Cancel any in-progress shutdown."""
        self._cancelled = True

    async def shutdown(self, *resources: Any) -> None:
        """Gracefully shutdown resources in order with timeout per resource.

        Handles both async (close()) and sync (stop()) methods.
        Thread objects are joined if still alive.

        Args:
            resources: Any objects with close(), stop(), or that are threading.Thread.
        """
        if self._cancelled:
            log.debug("Shutdown cancelled for %s", self.service_name)
            return

        log.info("Shutting down %s...", self.service_name)

        if not resources:
            log.info("%s shutdown complete (no resources)", self.service_name)
            return

        # Calculate per-resource timeout
        timeout_per_resource = self.timeout / len(resources) if resources else self.timeout
        start_time = time.monotonic()

        for i, resource in enumerate(resources):
            if self._cancelled:
                log.warning("%s: shutdown cancelled at resource %d", self.service_name, i)
                break

            elapsed = time.monotonic() - start_time
            remaining_timeout = max(0.1, self.timeout - elapsed)
            resource_timeout = min(timeout_per_resource, remaining_timeout)

            await self._shutdown_resource(resource, resource_timeout, index=i)

        log.info("%s shutdown complete", self.service_name)

    async def _shutdown_resource(self, resource: Any, timeout: float, index: int) -> None:
        """Shutdown a single resource with timeout enforcement.

        Args:
            resource: The resource to shutdown.
            timeout: Maximum seconds to wait.
            index: Resource index for logging.
        """
        resource_name = getattr(resource, '__class__', type(resource)).__name__

        try:
            # Thread object - join it
            if isinstance(resource, threading.Thread):
                await self._shutdown_thread(resource, timeout, resource_name)
                return

            # Async close() method
            if hasattr(resource, 'close') and asyncio.iscoroutinefunction(resource.close):
                await asyncio.wait_for(
                    resource.close(),
                    timeout=timeout
                )
                log.debug("%s: %s.close() completed", self.service_name, resource_name)

            # Sync close() method
            elif hasattr(resource, 'close'):
                await asyncio.wait_for(
                    asyncio.to_thread(resource.close),
                    timeout=timeout
                )
                log.debug("%s: %s.close() completed", self.service_name, resource_name)

            # Async stop() method
            elif hasattr(resource, 'stop') and asyncio.iscoroutinefunction(resource.stop):
                await asyncio.wait_for(
                    resource.stop(),
                    timeout=timeout
                )
                log.debug("%s: %s.stop() completed", self.service_name, resource_name)

            # Sync stop() method
            elif hasattr(resource, 'stop'):
                await asyncio.wait_for(
                    asyncio.to_thread(resource.stop),
                    timeout=timeout
                )
                log.debug("%s: %s.stop() completed", self.service_name, resource_name)

            # Has neither close nor stop - log and skip
            else:
                log.debug("%s: %s has no close/stop method, skipping", self.service_name, resource_name)

        except asyncio.TimeoutError:
            log.warning(
                "%s: shutdown timeout for %s (%.1fs elapsed, index=%d)",
                self.service_name, resource_name, timeout, index
            )
        except RuntimeError as e:
            # Event loop closed during teardown — benign in test environments
            if "loop is closed" in str(e).lower() or "Event loop is closed" in str(e):
                log.debug("%s: event loop already closed for %s (benign)", self.service_name, resource_name)
            else:
                log.error(
                    "%s: runtime error shutting down %s: %s",
                    self.service_name, resource_name, e, exc_info=True
                )
        except Exception as e:
            log.error(
                "%s: error shutting down %s: %s",
                self.service_name, resource_name, e, exc_info=True
            )

    async def _shutdown_thread(self, thread: threading.Thread, timeout: float, name: str) -> None:
        """Join a thread with timeout.

        Args:
            thread: The thread to join.
            timeout: Maximum seconds to wait.
            name: Thread name for logging.
        """
        if not thread.is_alive():
            log.debug("%s: thread %s already stopped", self.service_name, name)
            return

        log.debug("%s: joining thread %s (timeout=%.1fs)", self.service_name, name, timeout)

        # Run join in executor to allow cancellation
        loop = asyncio.get_event_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(None, thread.join, timeout),
                timeout=timeout
            )
            log.debug("%s: thread %s joined successfully", self.service_name, name)
        except asyncio.TimeoutError:
            log.warning(
                "%s: thread %s did not stop within %.1fs",
                self.service_name, name, timeout
            )


async def graceful_shutdown(service_name: str, *resources: Any, timeout: float = 5.0) -> None:
    """Convenience function for one-off graceful shutdown.

    Args:
        service_name: Human-readable name for logging.
        resources: Objects to shutdown.
        timeout: Total shutdown timeout in seconds.
    """
    protocol = ShutdownProtocol(service_name, timeout=timeout)
    await protocol.shutdown(*resources)