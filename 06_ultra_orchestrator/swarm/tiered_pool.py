"""
Tiered API Pool — Kimi K2.6 scale-out for 300 agents

4 tiers of API keys:
  Tier 1 (Premium):    4 keys × 200K TPM — coding/expert tasks
  Tier 2 (Standard):   8 keys × 150K TPM — general tasks  
  Tier 3 (Batch):      16 keys × 100K TPM — parallel batch work
  Tier 4 (Overflow):   32 keys × 50K TPM  — low-priority fill

Total: 60 keys, 7.2M TPM aggregate capacity
Smart routing: task priority → tier → key with highest capacity ratio
"""

from __future__ import annotations

import asyncio, hashlib, json, logging, math, os, random, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum
import aiohttp

logger = logging.getLogger("TieredPool")


class KeyTier(Enum):
    PREMIUM = 1   # 4 keys, 200K TPM
    STANDARD = 2  # 8 keys, 150K TPM
    BATCH = 3     # 16 keys, 100K TPM
    OVERFLOW = 4  # 32 keys, 50K TPM


TIER_CONFIG = {
    KeyTier.PREMIUM:  {"count": 4,  "tpm": 200000, "rpm": 60, "priority_weight": 4.0},
    KeyTier.STANDARD: {"count": 8,  "tpm": 150000, "rpm": 60, "priority_weight": 3.0},
    KeyTier.BATCH:    {"count": 16, "tpm": 100000, "rpm": 40, "priority_weight": 2.0},
    KeyTier.OVERFLOW: {"count": 32, "tpm": 50000,  "rpm": 30, "priority_weight": 1.0},
}


@dataclass
class TieredKey:
    """Single API key in the tiered pool"""
    key_id: str
    tier: KeyTier
    api_key: str
    max_tpm: int
    max_rpm: int
    priority_weight: float
    current_tokens: float = 0.0
    last_refill: float = 0.0
    requests_in_flight: int = 0
    consecutive_429: int = 0
    backoff_until: float = 0.0
    circuit_state: str = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
    total_tokens: int = 0
    total_cost: float = 0.0
    total_requests: int = 0
    success_count: int = 0

    @property
    def capacity_ratio(self) -> float:
        return self.current_tokens / self.max_tpm if self.max_tpm > 0 else 0.0

    def refill(self):
        now = time.time()
        elapsed = now - self.last_refill
        tokens_to_add = elapsed * (self.max_tpm / 60.0)
        self.current_tokens = min(self.max_tpm * 0.80,
                                   self.current_tokens + tokens_to_add)
        self.last_refill = now

    def can_handle(self, estimated_tokens: int) -> bool:
        if time.time() < self.backoff_until:
            return False
        if self.circuit_state == "OPEN":
            return False
        return self.current_tokens >= estimated_tokens * 1.2

    def consume(self, tokens: int, cost: float):
        self.current_tokens = max(0, self.current_tokens - tokens)
        self.total_tokens += tokens
        self.total_cost += cost
        self.total_requests += 1

    def record_429(self):
        self.consecutive_429 += 1
        delays = {1: 60, 2: 300}
        delay = delays.get(self.consecutive_429, 900)
        self.backoff_until = time.time() + delay
        if self.consecutive_429 >= 3:
            self.circuit_state = "OPEN"
            logger.critical(f"CIRCUIT OPEN: {self.key_id} (tier={self.tier.name})")

    def record_success(self):
        self.consecutive_429 = 0
        self.success_count += 1
        if self.circuit_state == "HALF_OPEN":
            self.circuit_state = "CLOSED"

    def record_failure(self, status_code: int):
        if status_code == 429:
            self.record_429()
        elif status_code in (401, 403):
            self.circuit_state = "OPEN"
            logger.critical(f"KEY DEAD: {self.key_id} (auth failure)")

    def release_slot(self):
        self.requests_in_flight = max(0, self.requests_in_flight - 1)

    def to_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "tier": self.tier.name,
            "capacity_ratio": round(self.capacity_ratio, 4),
            "circuit_state": self.circuit_state,
            "backoff_remaining": max(0, self.backoff_until - time.time()),
            "in_flight": self.requests_in_flight,
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 4),
            "success_rate": (self.success_count / max(1, self.total_requests)),
        }


class TieredAPIPool:
    """
    4-tier API key pool for 300-agent scale-out.
    Routes tasks by priority to the appropriate tier.
    """

    def __init__(self, safety_margin: float = 0.80):
        self.safety_margin = safety_margin
        self.keys: Dict[str, TieredKey] = {}
        self._lock = asyncio.Lock()
        self._session: Optional[aiohttp.ClientSession] = None
        self._request_count = 0
        self._setup_session()
        self._load_all_keys()

    def _setup_session(self):
        connector = aiohttp.TCPConnector(limit=1000, limit_per_host=200)
        timeout = aiohttp.ClientTimeout(total=120)
        self._session = aiohttp.ClientSession(
            connector=connector, timeout=timeout,
            headers={"User-Agent": "UltraOrchestrator/2.0"}
        )

    def _load_all_keys(self):
        """Load keys from environment variables for all tiers."""
        key_idx = 0
        for tier, config in TIER_CONFIG.items():
            for i in range(config["count"]):
                key_idx += 1
                env_var = f"KIMI_{tier.name}_KEY_{i+1}"
                api_key = os.environ.get(env_var, "")
                if not api_key:
                    # Fallback to legacy naming
                    api_key = os.environ.get(f"KIMI_KEY_{key_idx}", "")

                if api_key:
                    key_id = f"{tier.name[0]}{i+1:02d}"  # P01, S03, B12, O31
                    self.keys[key_id] = TieredKey(
                        key_id=key_id,
                        tier=tier,
                        api_key=api_key,
                        max_tpm=config["tpm"],
                        max_rpm=config["rpm"],
                        priority_weight=config["priority_weight"],
                        last_refill=time.time(),
                        current_tokens=config["tpm"] * safety_margin
                    )
                    logger.info(f"Loaded key {key_id} ({tier.name})")

        total = len(self.keys)
        expected = sum(c["count"] for c in TIER_CONFIG.values())
        logger.info(f"TieredPool: {total}/{expected} keys loaded")

    def get_tier_for_task(self, priority: str) -> KeyTier:
        """Map task priority to key tier."""
        tier_map = {
            "CRITICAL": KeyTier.PREMIUM,
            "HIGH": KeyTier.STANDARD,
            "NORMAL": KeyTier.BATCH,
            "LOW": KeyTier.OVERFLOW,
        }
        return tier_map.get(priority, KeyTier.BATCH)

    async def get_optimal_key(self, priority: str = "NORMAL",
                               estimated_tokens: int = 1000) -> Optional[str]:
        """Get best available key considering tier priority."""
        async with self._lock:
            target_tier = self.get_tier_for_task(priority)
            candidates = []

            # Search target tier first, then fall back
            tiers_to_check = [target_tier]
            for t in KeyTier:
                if t not in tiers_to_check:
                    tiers_to_check.append(t)

            for tier in tiers_to_check:
                tier_keys = [k for k in self.keys.values() if k.tier == tier]
                for key in tier_keys:
                    key.refill()
                    if key.can_handle(estimated_tokens):
                        score = key.capacity_ratio * key.priority_weight
                        candidates.append((score, key))

            if not candidates:
                return None

            # Select key with highest weighted capacity
            candidates.sort(key=lambda x: x[0], reverse=True)
            selected = candidates[0][1]
            selected.requests_in_flight += 1
            return selected.key_id

    async def release_key(self, key_id: str):
        """Release a key after use."""
        async with self._lock:
            if key_id in self.keys:
                self.keys[key_id].release_slot()

    async def send_request(self, messages: list, key_id: Optional[str] = None,
                           priority: str = "NORMAL",
                           temperature: float = 0.7,
                           max_tokens: int = 4096,
                           estimated_tokens: int = 1000) -> dict:
        """Send request via tiered pool."""
        if key_id is None:
            key_id = await self.get_optimal_key(priority, estimated_tokens)
        if key_id is None:
            raise RuntimeError("No API keys available in any tier")

        key = self.keys[key_id]
        key.refill()

        url = "https://api.moonshot.ai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {key.api_key[:8]}...",
            "Content-Type": "application/json",
        }
        body = {
            "model": "kimi-k2-6",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        # Use actual key (not masked) for request
        real_headers = {
            "Authorization": f"Bearer {key.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(3):
            try:
                async with self._session.post(url, headers=real_headers,
                                               json=body) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        content = data["choices"][0]["message"].get("content", "")
                        reasoning = data["choices"][0]["message"].get("reasoning", "")
                        usage = data.get("usage", {})
                        pt = usage.get("prompt_tokens", 0)
                        ct = usage.get("completion_tokens", 0)
                        cost = (pt * 0.50 + ct * 1.50) / 1_000_000

                        key.consume(pt + ct, cost)
                        key.record_success()

                        return {
                            "content": content,
                            "reasoning": reasoning,
                            "prompt_tokens": pt,
                            "completion_tokens": ct,
                            "total_tokens": pt + ct,
                            "cost_usd": cost,
                            "key_used": key_id,
                        }
                    elif resp.status == 429:
                        key.record_failure(429)
                        # Try different key
                        new_key = await self.get_optimal_key(priority, estimated_tokens)
                        if new_key and new_key != key_id:
                            key.release_slot()
                            key_id = new_key
                            key = self.keys[key_id]
                            await asyncio.sleep(2 ** attempt)
                            continue
                    elif resp.status in (401, 403):
                        key.record_failure(resp.status)
                        raise RuntimeError(f"Auth failed for key {key_id}")
                    else:
                        key.record_failure(resp.status)
                        await asyncio.sleep(2 ** attempt)
            except Exception as e:
                logger.error(f"Request failed (attempt {attempt+1}): {e}")
                await asyncio.sleep(2 ** attempt)

        key.release_slot()
        raise RuntimeError(f"All retries exhausted for key {key_id}")

    async def get_pool_status(self) -> dict:
        """Get full pool status across all tiers."""
        status = {"total_keys": len(self.keys), "tiers": {}}
        for tier in KeyTier:
            tier_keys = [k for k in self.keys.values() if k.tier == tier]
            status["tiers"][tier.name] = {
                "key_count": len(tier_keys),
                "available": sum(1 for k in tier_keys
                                if k.can_handle(1000)),
                "total_tokens": sum(k.total_tokens for k in tier_keys),
                "total_cost": round(sum(k.total_cost for k in tier_keys), 4),
                "keys": [k.to_dict() for k in tier_keys],
            }
        return status

    async def close(self):
        if self._session:
            await self._session.close()

    @property
    def total_aggregate_tpm(self) -> int:
        return sum(k.max_tpm for k in self.keys.values())

    @property
    def active_key_count(self) -> int:
        return len(self.keys)


__all__ = ["TieredAPIPool", "TieredKey", "KeyTier", "TIER_CONFIG"]
