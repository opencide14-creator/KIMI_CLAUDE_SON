"""
Proactive Orchestration Engine — 24/7 Background Intelligence

Inspired by Kimi K2.6's proactive orchestration:
  - Autonomous background agents that monitor, analyze, and act
  - Predictive task decomposition based on patterns
  - Self-healing: detects failures and auto-remediates
  - Continuous optimization of key rotation and batch sizes
  - Anomaly detection on agent outputs
  - Automatic template selection based on task fingerprinting

Latency is a feature. Proactive prediction eliminates latency.
"""

from __future__ import annotations

import asyncio, hashlib, json, logging, statistics, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Tuple
from collections import deque

logger = logging.getLogger("Proactive")


@dataclass
class TaskFingerprint:
    """Fingerprint of a task for pattern matching"""
    title_hash: str
    keyword_signature: List[str]
    output_type: str
    avg_tokens: int = 0
    avg_duration_ms: float = 0.0
    success_rate: float = 1.0
    best_template: str = "BLANK_CODE_GENERATION"
    best_key_tier: str = "PREMIUM"

    def to_dict(self) -> dict:
        return {
            "title_hash": self.title_hash,
            "keywords": self.keyword_signature,
            "output_type": self.output_type,
            "avg_tokens": self.avg_tokens,
            "avg_duration_ms": round(self.avg_duration_ms, 2),
            "success_rate": round(self.success_rate, 4),
            "best_template": self.best_template,
            "best_key_tier": self.best_key_tier,
        }


@dataclass
class AnomalyEvent:
    """Detected anomaly in the system"""
    timestamp: float
    anomaly_type: str  # "kappa_drop", "key_failure", "timeout_spike", "cost_spike", "duplicate_output"
    severity: str      # "LOW", "MEDIUM", "HIGH", "CRITICAL"
    description: str
    affected_agents: List[str] = field(default_factory=list)
    recommended_action: str = ""


class ProactiveEngine:
    """
    24/7 proactive orchestration intelligence.
    Monitors, predicts, and auto-optimizes the swarm.
    """

    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self._fingerprints: Dict[str, TaskFingerprint] = {}
        self._kappa_history: deque = deque(maxlen=window_size)
        self._duration_history: deque = deque(maxlen=window_size)
        self._cost_history: deque = deque(maxlen=window_size)
        self._token_history: deque = deque(maxlen=window_size)
        self._anomalies: List[AnomalyEvent] = []
        self._predictions: Dict[str, Any] = {}
        self._optimization_suggestions: List[dict] = []
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable] = []

    def register_callback(self, callback: Callable):
        self._callbacks.append(callback)

    async def _notify(self, event_type: str, data: dict):
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(event_type, data)
                else:
                    cb(event_type, data)
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def start(self):
        """Start proactive monitoring."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Proactive engine started")

    async def stop(self):
        """Stop proactive monitoring."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Proactive engine stopped")

    async def _monitor_loop(self):
        """Background monitoring loop."""
        while self._running:
            try:
                # Run all proactive checks every 5 seconds
                await self._detect_anomalies()
                await self._generate_predictions()
                await self._generate_optimizations()
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Proactive monitor error: {e}")
                await asyncio.sleep(10)

    def record_agent_completion(self, agent_id: str, title: str,
                                 kappa: float, duration_ms: float,
                                 tokens: int, cost: float,
                                 output_type: str, template: str,
                                 key_tier: str, approved: bool):
        """Record an agent completion for pattern learning."""
        self._kappa_history.append(kappa)
        self._duration_history.append(duration_ms)
        self._cost_history.append(cost)
        self._token_history.append(tokens)

        # Update fingerprint
        fp_hash = self._fingerprint_title(title)
        if fp_hash not in self._fingerprints:
            self._fingerprints[fp_hash] = TaskFingerprint(
                title_hash=fp_hash,
                keyword_signature=self._extract_keywords(title),
                output_type=output_type,
            )

        fp = self._fingerprints[fp_hash]
        # Exponential moving average
        alpha = 0.3
        fp.avg_tokens = int(alpha * tokens + (1 - alpha) * fp.avg_tokens) if fp.avg_tokens else tokens
        fp.avg_duration_ms = alpha * duration_ms + (1 - alpha) * fp.avg_duration_ms if fp.avg_duration_ms else duration_ms
        fp.success_rate = alpha * (1.0 if approved else 0.0) + (1 - alpha) * fp.success_rate

        # Track best template
        if approved:
            fp.best_template = template
            fp.best_key_tier = key_tier

    def _fingerprint_title(self, title: str) -> str:
        """Create a hash fingerprint of a task title."""
        normalized = title.lower().strip()
        # Remove numbers and special chars
        normalized = ''.join(c for c in normalized if c.isalpha() or c.isspace())
        normalized = ' '.join(normalized.split())  # collapse whitespace
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _extract_keywords(self, title: str) -> List[str]:
        """Extract significant keywords from title."""
        stopwords = {"the", "a", "an", "and", "or", "but", "in", "on", "at",
                     "to", "for", "of", "with", "by", "from", "as", "is", "are",
                     "create", "generate", "write", "implement", "build", "make"}
        words = title.lower().split()
        keywords = [w for w in words if len(w) > 3 and w not in stopwords]
        return keywords[:10]

    async def _detect_anomalies(self):
        """Detect anomalies in recent agent performance."""
        if len(self._kappa_history) < 10:
            return

        recent_kappa = list(self._kappa_history)[-20:]
        avg_kappa = statistics.mean(recent_kappa)
        std_kappa = statistics.stdev(recent_kappa) if len(recent_kappa) > 1 else 0

        # Anomaly 1: Kappa drop
        if avg_kappa < 0.70:
            await self._raise_anomaly(
                "kappa_drop", "HIGH",
                f"Average κ dropped to {avg_kappa:.3f} over last {len(recent_kappa)} agents",
                recommended_action="Scale down batch size, increase quality gate scrutiny"
            )
        elif avg_kappa < 0.85:
            await self._raise_anomaly(
                "kappa_drop", "MEDIUM",
                f"Average κ declining: {avg_kappa:.3f}",
                recommended_action="Review recent rejections for pattern"
            )

        # Anomaly 2: Duration spike
        if len(self._duration_history) >= 10:
            recent_dur = list(self._duration_history)[-10:]
            avg_dur = statistics.mean(recent_dur)
            if avg_dur > 30000:  # >30s average
                await self._raise_anomaly(
                    "timeout_spike", "MEDIUM",
                    f"Average duration spiked to {avg_dur/1000:.1f}s",
                    recommended_action="Check API key health, consider tier upgrade"
                )

        # Anomaly 3: Cost spike
        if len(self._cost_history) >= 10:
            recent_cost = list(self._cost_history)[-10:]
            avg_cost = statistics.mean(recent_cost)
            if avg_cost > 0.50:  # >$0.50 per agent average
                await self._raise_anomaly(
                    "cost_spike", "MEDIUM",
                    f"Average cost per agent: ${avg_cost:.4f}",
                    recommended_action="Review token usage, optimize prompts"
                )

    async def _raise_anomaly(self, anomaly_type: str, severity: str,
                              description: str, affected: List[str] = None,
                              recommended_action: str = ""):
        event = AnomalyEvent(
            timestamp=time.time(),
            anomaly_type=anomaly_type,
            severity=severity,
            description=description,
            affected_agents=affected or [],
            recommended_action=recommended_action
        )
        self._anomalies.append(event)
        logger.warning(f"ANOMALY [{severity}] {anomaly_type}: {description}")
        await self._notify("anomaly", {
            "type": anomaly_type,
            "severity": severity,
            "description": description,
            "action": recommended_action,
        })

    async def _generate_predictions(self):
        """Generate predictions based on historical patterns."""
        if len(self._kappa_history) < 20:
            return

        # Predict completion time
        total_remaining = getattr(self, '_estimated_total', 100)
        approved = sum(1 for k in self._kappa_history if k >= 0.95)
        rate = approved / len(self._kappa_history)
        avg_duration = statistics.mean(self._duration_history) if self._duration_history else 5000

        self._predictions = {
            "estimated_completion_sec": total_remaining * avg_duration / 1000 / max(rate, 0.01),
            "predicted_success_rate": rate,
            "predicted_avg_kappa": statistics.mean(list(self._kappa_history)[-20:]),
            "predicted_avg_duration_ms": avg_duration,
            "timestamp": time.time(),
        }

    async def _generate_optimizations(self):
        """Generate optimization suggestions."""
        suggestions = []

        # Optimize batch size based on kappa trend
        if len(self._kappa_history) >= 20:
            recent = list(self._kappa_history)[-20:]
            trend = recent[-1] - recent[0]
            if trend < -0.1:
                suggestions.append({
                    "type": "batch_size",
                    "action": "decrease",
                    "reason": f"κ declining trend: {trend:+.3f}",
                    "recommended_value": 10,
                })
            elif trend > 0.1:
                suggestions.append({
                    "type": "batch_size",
                    "action": "increase",
                    "reason": f"κ improving trend: {trend:+.3f}",
                    "recommended_value": 40,
                })

        # Optimize key tier based on success rate
        for fp_hash, fp in self._fingerprints.items():
            if fp.success_rate < 0.7 and fp.best_key_tier != "PREMIUM":
                suggestions.append({
                    "type": "key_tier",
                    "action": "upgrade",
                    "reason": f"Success rate {fp.success_rate:.0%} for {fp.output_type}",
                    "recommended_tier": "PREMIUM",
                    "fingerprint": fp_hash,
                })

        self._optimization_suggestions = suggestions

        if suggestions:
            await self._notify("optimization", {
                "suggestions": suggestions,
                "count": len(suggestions),
            })

    def suggest_template(self, title: str, output_type: str) -> str:
        """Suggest best template based on task fingerprint."""
        fp_hash = self._fingerprint_title(title)
        if fp_hash in self._fingerprints:
            fp = self._fingerprints[fp_hash]
            if fp.success_rate > 0.8:
                return fp.best_template

        # Fallback to keyword matching
        keywords = self._extract_keywords(title)
        template_scores = {
            "BLANK_CODE_GENERATION": 0,
            "BLANK_POWERSHELL": 0,
            "BLANK_ANALYSIS": 0,
            "BLANK_STRUCTURED_DATA": 0,
        }
        code_kw = {"code", "function", "class", "implement", "algorithm", "api", "module"}
        ps_kw = {"powershell", "script", "windows", "registry", "service", "wmi"}
        analysis_kw = {"analyze", "compare", "evaluate", "assess", "review", "research"}
        data_kw = {"json", "xml", "csv", "data", "schema", "structure", "parse"}

        for kw in keywords:
            if kw in code_kw: template_scores["BLANK_CODE_GENERATION"] += 1
            if kw in ps_kw: template_scores["BLANK_POWERSHELL"] += 1
            if kw in analysis_kw: template_scores["BLANK_ANALYSIS"] += 1
            if kw in data_kw: template_scores["BLANK_STRUCTURED_DATA"] += 1

        best = max(template_scores, key=template_scores.get)
        if template_scores[best] == 0:
            best = "BLANK_CODE_GENERATION"  # default
        return best

    def suggest_key_tier(self, title: str, priority: str) -> str:
        """Suggest best key tier for a task."""
        fp_hash = self._fingerprint_title(title)
        if fp_hash in self._fingerprints:
            fp = self._fingerprints[fp_hash]
            return fp.best_key_tier

        tier_map = {
            "CRITICAL": "PREMIUM",
            "HIGH": "STANDARD",
            "NORMAL": "BATCH",
            "LOW": "OVERFLOW",
        }
        return tier_map.get(priority, "BATCH")

    def get_predictions(self) -> dict:
        return dict(self._predictions)

    def get_anomalies(self, limit: int = 50) -> List[dict]:
        return [
            {"timestamp": a.timestamp, "type": a.anomaly_type,
             "severity": a.severity, "description": a.description,
             "action": a.recommended_action}
            for a in self._anomalies[-limit:]
        ]

    def get_optimizations(self) -> List[dict]:
        return list(self._optimization_suggestions)

    def get_fingerprints(self) -> Dict[str, dict]:
        return {k: v.to_dict() for k, v in self._fingerprints.items()}

    def get_stats(self) -> dict:
        return {
            "agents_recorded": len(self._kappa_history),
            "avg_kappa": round(statistics.mean(self._kappa_history), 4) if self._kappa_history else 0,
            "avg_duration_ms": round(statistics.mean(self._duration_history), 2) if self._duration_history else 0,
            "avg_cost_usd": round(statistics.mean(self._cost_history), 6) if self._cost_history else 0,
            "anomalies_detected": len(self._anomalies),
            "fingerprints_learned": len(self._fingerprints),
            "predictions": self._predictions,
        }


__all__ = ["ProactiveEngine", "TaskFingerprint", "AnomalyEvent"]
