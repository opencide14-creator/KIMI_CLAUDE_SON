"""
Gateway Heartbeat Enforcer — Phase 8 Integration
═════════════════════════════════════════════════
Wraps HeartbeatAgent to verify every gateway request/response.

If heartbeat dies or objects, all traffic is blocked.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import HTTPException

from src.core.agents.heartbeat_agent import HeartbeatAgent
from src.core.agents.reactive_agent import ReactiveAgent
from src.core.agents.dual_loop import DualReActLoop

log = logging.getLogger(__name__)


class GatewayHeartbeatEnforcer:
    """
    Enforces dual-agent heartbeat verification on every gateway action.

    LAW_3 enforcement: No request is forwarded without Heartbeat approval.
    If HeartbeatAgent is not alive, ALL requests are rejected with 503.
    """

    def __init__(self):
        self._heartbeat = HeartbeatAgent()
        self._reactive = ReactiveAgent()
        self._dual_loop = DualReActLoop(self._reactive, self._heartbeat)
        self._initialized = False
        log.info("GatewayHeartbeatEnforcer created")

    def initialize(self) -> bool:
        """Boot the dual-agent system. Returns False if boot fails."""
        try:
            # Boot reactive first
            if not self._reactive.boot():
                log.error("ReactiveAgent boot failed")
                return False
            # Then heartbeat
            if not self._heartbeat.boot():
                log.error("HeartbeatAgent boot failed")
                return False
            self._initialized = True
            log.info("GatewayHeartbeatEnforcer initialized — agents ONLINE")
            return True
        except Exception as e:
            log.error("Heartbeat initialization failed: %s", e)
            return False

    def shutdown(self):
        """Gracefully shutdown agents."""
        self._reactive.stop()
        self._heartbeat.stop()
        self._initialized = False
        log.info("GatewayHeartbeatEnforcer shutdown")

    def verify_request(self, request_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Verify an incoming request before forwarding.

        Args:
            request_info: Dict with 'method', 'path', 'model', 'session_id', etc.

        Returns:
            Verification result dict

        Raises:
            HTTPException(503): If heartbeat is dead or objects
        """
        if not self._initialized:
            log.warning("Heartbeat not initialized — blocking request")
            raise HTTPException(status_code=503, detail="Heartbeat system offline")

        if not self._heartbeat.is_alive:
            log.warning("HEARTBEAT_DEAD — blocking all traffic (LAW_3)")
            raise HTTPException(status_code=503, detail="LAW_3: Heartbeat agent dead — traffic blocked")

        # Build plan for heartbeat verification
        plan = {
            "tool": "forward_request",
            "args": {
                "method": request_info.get("method", "POST"),
                "path": request_info.get("path", "/"),
                "model": request_info.get("model", "unknown"),
                "session_id": request_info.get("session_id", "unknown"),
                "target_host": request_info.get("target_host", "unknown"),
            }
        }

        # Reactive proposes (optional — usually quick approve for reads)
        reactive_result = self._reactive.verify(plan)
        if not reactive_result.passed:
            # Reactive objects — escalate to flag
            log.warning("Reactive objects to plan: %s", reactive_result.reason)
            self._heartbeat._memory.write_flag("REACTIVE", reactive_result.reason, plan)
            raise HTTPException(status_code=403, detail=f"Reactive objection: {reactive_result.reason}")

        # Heartbeat verifies (MANDATORY)
        heartbeat_result = self._heartbeat.verify(plan)
        if not heartbeat_result.passed:
            log.warning("HEARTBEAT VETO: %s (LAW_%s)", heartbeat_result.reason, heartbeat_result.violated_law or "?")
            raise HTTPException(
                status_code=403,
                detail=f"Heartbeat veto [LAW_{heartbeat_result.violated_law}]: {heartbeat_result.reason}"
            )

        # Both approved — log and allow
        self._heartbeat.ingest(
            "forward_request",
            plan["args"],
            f"APPROVED by Reactive + Heartbeat (seq={self._heartbeat._pulse_seq})"
        )

        return {
            "approved": True,
            "by": ["reactive", "heartbeat"],
            "sequence": self._heartbeat._pulse_seq,
            "timestamp": time.time(),
            "plan": plan,
        }

    def verify_response(self, request_info: Dict[str, Any], response_status: int, response_hash: str) -> bool:
        """
        Verify the response before returning it to the client.

        Less strict — primarily logs and checks for anomalies.
        """
        if not self._initialized:
            return True  # Don't block responses if heartbeat just started

        plan = {
            "tool": "return_response",
            "args": {
                "original_path": request_info.get("path", "/"),
                "status_code": response_status,
                "response_hash": response_hash,
                "session_id": request_info.get("session_id", "unknown"),
            }
        }

        result = self._heartbeat.verify(plan)
        if not result.passed:
            log.warning("Response verification failed: %s", result.reason)
            # Response violations are logged but not blocked
            # (Better to return a potentially bad response than hang the client)
            return False

        self._heartbeat.ingest("return_response", plan["args"], f"status={response_status}")
        return True

    def get_status(self) -> Dict[str, Any]:
        """Return current heartbeat status."""
        return {
            "initialized": self._initialized,
            "heartbeat_alive": self._heartbeat.is_alive if self._initialized else False,
            "reactive_alive": self._reactive.is_alive if self._initialized else False,
            "pulse_seq": self._heartbeat._pulse_seq if self._initialized else 0,
            "dual_loop": self._dual_loop.sense() if self._initialized else {},
        }
