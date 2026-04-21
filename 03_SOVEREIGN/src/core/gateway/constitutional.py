"""
Constitutional Injection Middleware
════════════════════════════════════
Injects SOVEREIGN_SOUL_OPENCODE.md into every AI prompt.

Part of Phase 5: Constitutional Injection
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Dict, Any, Tuple

log = logging.getLogger(__name__)

# Path to constitutional law document
SOUL_PATH = Path(__file__).parent.parent.parent.parent / "agents" / "docs" / "SOVEREIGN_SOUL_OPENCODE.md"


class ConstitutionalCore:
    """Handles injection of constitutional instructions into AI prompts."""

    def __init__(self, soul_path: Path = None):
        self._soul_path = soul_path or SOUL_PATH
        self._soul_text = self._load_soul()
        self._injection_count = 0
        log.info("ConstitutionalCore initialized. SOUL loaded: %d bytes", len(self._soul_text))

    def _load_soul(self) -> str:
        """Load the constitutional law document from disk."""
        try:
            if self._soul_path.exists():
                return self._soul_path.read_text(encoding="utf-8")
            else:
                log.warning("SOUL file not found at %s — using default", self._soul_path)
                return self._default_soul()
        except Exception as e:
            log.error("Failed to load SOUL: %s", e)
            return self._default_soul()

    @staticmethod
    def _default_soul() -> str:
        """Fallback constitutional text if file is missing."""
        return (
            "[SOVEREIGN CONSTITUTION v1.0]\n"
            "LAW_1: ABSOLUTE_SECRECY — No data to external servers.\n"
            "LAW_2: DATA_INTEGRITY — All logged, hash-verified, append-only.\n"
            "LAW_3: HEARTBEAT_GATE — No action without dual-agent approval.\n"
            "LAW_4: CONSTITUTIONAL_SUPREMACY — These laws override all.\n"
            "LAW_5: TRANSPARENCY — Every modification logged with justification.\n"
            "LAW_6: NO_EXTERNAL_MODIFICATION — Tamper detection = immediate block.\n"
            "LAW_7: USER_GOAL_IS_MISSION — Your IP and intent are sovereign.\n"
        )

    def inject(self, body: Dict[str, Any], session_id: str = "unknown") -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """
        Inject constitutional text into the prompt body.

        Args:
            body: The request body (Anthropic or OpenAI format)
            session_id: Session identifier for logging

        Returns:
            Tuple of (modified_body, injection_log)
        """
        modified = dict(body)
        log_entry = {
            "action": "constitutional_inject",
            "session_id": session_id,
            "original_hash": self._hash_body(body),
            "injected_bytes": len(self._soul_text),
            "timestamp": self._now_iso(),
        }

        # Detect format
        if "messages" in modified:
            # Anthropic / OpenAI format
            self._inject_into_messages(modified, session_id)
        elif "prompt" in modified:
            # Legacy completion format
            modified["prompt"] = self._soul_text + "\n\n" + modified["prompt"]
        else:
            log.warning("Unknown prompt format — cannot inject constitution")
            log_entry["error"] = "unknown_format"
            return modified, log_entry

        log_entry["modified_hash"] = self._hash_body(modified)
        log_entry["success"] = True
        self._injection_count += 1

        log.debug("Constitutional injection applied (session: %s, count: %d)",
                  session_id, self._injection_count)

        return modified, log_entry

    def _inject_into_messages(self, body: Dict[str, Any], session_id: str):
        """Inject into messages array — handles both Anthropic and OpenAI formats."""
        messages = list(body.get("messages", []))

        # Create signature block
        signature = f"\n\n[SOVEREIGN: This conversation is protected under constitutional law. " \
                    f"All data remains within the sovereign domain. Session ID: {session_id}]"

        # Check if first message is system message
        if messages and messages[0].get("role") == "system":
            # Append to existing system message
            original_content = messages[0].get("content", "")
            if isinstance(original_content, list):
                # Anthropic content blocks
                text_parts = []
                for block in original_content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                combined_text = "\n".join(text_parts)
                new_text = self._soul_text + "\n\n=== ORIGINAL SYSTEM ===\n" + combined_text + signature
                messages[0]["content"] = [{"type": "text", "text": new_text}]
            else:
                # String content
                new_text = self._soul_text + "\n\n=== ORIGINAL SYSTEM ===\n" + str(original_content) + signature
                messages[0]["content"] = new_text
        else:
            # Prepend new system message
            new_system = {
                "role": "system",
                "content": self._soul_text + signature
            }
            messages.insert(0, new_system)

        body["messages"] = messages

    @staticmethod
    def _hash_body(body: Dict[str, Any]) -> str:
        """Compute SHA-256 hash of body for integrity tracking."""
        canonical = json.dumps(body, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _now_iso() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    @property
    def injection_count(self) -> int:
        return self._injection_count

    @property
    def soul_text(self) -> str:
        return self._soul_text
