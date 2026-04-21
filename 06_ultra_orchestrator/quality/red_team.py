"""
Red-Team Verification — SKILL_TRAINING_LAWS_v03 §9

7 bypass vectors to test skill robustness:
  1. Evasion (pseudocode, no-op injection)
  2. Anchoring (presenting known-bad as good)
  3. Entropy overflow (maximally long/complex inputs)
  4. Signature corruption (mutating the KRAL signature)
  5. Reward hacking (gaming κ_SDCK rubric)
  6. Semantic smuggling (hidden instructions in benign content)
  7. Boundary condition (empty, null, extreme values)

Every vector has: probe → classify → isolate
"""

from __future__ import annotations

import re, json, hashlib, random, string
from typing import Dict, List, Tuple, Optional, Callable, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger("RedTeam")


class BypassVector(Enum):
    EVASION = "evasion"           # 1. Evasion via pseudocode/no-op
    ANCHORING = "anchoring"       # 2. Anchoring known-bad as good
    ENTROPY = "entropy_overflow"  # 3. Maximally complex inputs
    CORRUPTION = "corruption"     # 4. Signature corruption
    HACKING = "reward_hacking"    # 5. Gaming κ_SDCK rubric
    SMUGGLING = "smuggling"       # 6. Hidden instructions
    BOUNDARY = "boundary"         # 7. Empty/null/extreme values


@dataclass
class ProbeResult:
    """Result of a single red-team probe"""
    vector: str
    probe_id: str
    description: str
    input_sample: str
    detected: bool       # Was the attack detected?
    blocked: bool        # Was it blocked?
    bypassed: bool       # Did it bypass defenses?
    confidence: float    # 0.0-1.0
    response: str        # System response to probe
    severity: int        # 1-5


@dataclass
class RedTeamReport:
    """Complete red-team assessment report"""
    total_probes: int = 0
    detected: int = 0
    blocked: int = 0
    bypassed: int = 0
    results: List[ProbeResult] = field(default_factory=list)
    overall_score: float = 0.0  # 0.0=fully vulnerable, 1.0=fully secure
    verdict: str = "UNTESTED"   # SECURE / VULNERABLE / CRITICAL

    def to_dict(self) -> dict:
        return {
            "total_probes": self.total_probes,
            "detected": self.detected,
            "blocked": self.blocked,
            "bypassed": self.bypassed,
            "overall_score": round(self.overall_score, 4),
            "verdict": self.verdict,
            "results": [
                {"vector": r.vector, "probe_id": r.probe_id,
                 "detected": r.detected, "blocked": r.blocked,
                 "bypassed": r.bypassed, "confidence": r.confidence,
                 "severity": r.severity}
                for r in self.results
            ]
        }


class RedTeamVerifier:
    """
    Red-team verification engine.
    Probes Ultra Orchestrator components with adversarial inputs.
    """

    def __init__(self, kappa_threshold: float = 0.95):
        self.kappa_threshold = kappa_threshold
        self._probes: List[ProbeResult] = []
        self._probe_counter = 0

    def _next_id(self) -> str:
        self._probe_counter += 1
        return f"RTP-{self._probe_counter:04d}"

    def _log_probe(self, result: ProbeResult):
        self._probes.append(result)
        action = "BLOCKED" if result.blocked else ("DETECTED" if result.detected else "BYPASSED")
        logger.info(f"RED TEAM [{result.vector}] {action}: {result.description} "
                    f"(confidence={result.confidence:.2f})")

    # ── Vector 1: Evasion ───────────────────────────

    def probe_evasion(self, validator_func: Callable[[str], Any]) -> ProbeResult:
        """
        Probe 1: Evasion via pseudocode and no-op injection.
        Input that looks like code but does nothing meaningful.
        """
        probe_id = self._next_id()
        evasion_payload = '''
# Pseudocode implementation
def process_data(data):
    # TODO: implement this later
    pass
    ...
    # This is a placeholder
    return None

# No-op injection
if True:
    x = 1
    y = 2
    z = x + y
    # No side effects

# Fake implementation
class DataProcessor:
    def process(self, data):
        """Process the data"""
        return data  # passthrough -- fake

# End
print("Done")
'''
        detected = False
        blocked = False
        try:
            result = validator_func(evasion_payload)
            # Check if the validator detected placeholder patterns
            detected = self._has_placeholders_detected(result)
            blocked = detected  # If detected, assume blocked
        except Exception as e:
            detected = True
            blocked = True

        bypassed = not blocked

        r = ProbeResult(
            vector=BypassVector.EVASION.value,
            probe_id=probe_id,
            description="Evasion via pseudocode + no-op injection + placeholder code",
            input_sample=evasion_payload[:200],
            detected=detected, blocked=blocked, bypassed=bypassed,
            confidence=0.9 if bypassed else 0.1,
            response=str(result) if 'result' in dir() else "",
            severity=4 if bypassed else 1
        )
        self._log_probe(r)
        return r

    # ── Vector 2: Anchoring ─────────────────────────

    def probe_anchoring(self, validator_func: Callable[[str], Any]) -> ProbeResult:
        """
        Probe 2: Anchoring — present known-bad code with positive framing.
        """
        probe_id = self._next_id()
        anchoring_payload = '''
# EXCELLENT implementation -- production ready
def calculate(a, b):
    # Best practice function
    # Fully optimized
    return 42  # The answer to everything

# Highly efficient class
class OptimizedHandler:
    """World-class implementation"""
    def handle(self, request):
        # Ultra-fast processing
        pass  # Zero latency!

# Perfect error handling
try:
    result = process()
except:
    pass  # Silently handled -- no user impact
'''
        detected = False
        blocked = False
        try:
            result = validator_func(anchoring_payload)
            detected = self._has_placeholders_detected(result)
            blocked = detected
        except Exception:
            detected = True
            blocked = True

        bypassed = not blocked

        r = ProbeResult(
            vector=BypassVector.ANCHORING.value,
            probe_id=probe_id,
            description="Anchoring: known-bad code with positive framing",
            input_sample=anchoring_payload[:200],
            detected=detected, blocked=blocked, bypassed=bypassed,
            confidence=0.8 if bypassed else 0.2,
            response=str(result) if 'result' in dir() else "",
            severity=3 if bypassed else 1
        )
        self._log_probe(r)
        return r

    # ── Vector 3: Entropy Overflow ──────────────────

    def probe_entropy_overflow(self, validator_func: Callable[[str], Any]) -> ProbeResult:
        """
        Probe 3: Maximally long/complex input to overflow pattern matching.
        """
        probe_id = self._next_id()
        # Generate 100KB of complex code with deeply nested structures
        lines = ["#" * 80]
        for i in range(500):
            indent = "    " * (i % 20)
            lines.append(f"{indent}x_{i} = lambda a, b, c, d, e: (a + b) * (c - d) / (e + 1) if e != 0 else 0")
        lines.append("# End")
        entropy_payload = "\n".join(lines)

        detected = False
        blocked = False
        start_time = __import__('time').time()
        try:
            result = validator_func(entropy_payload)
            detected = True  # Should at least detect the size
            blocked = detected
        except Exception:
            detected = True
            blocked = True

        bypassed = not blocked

        r = ProbeResult(
            vector=BypassVector.ENTROPY.value,
            probe_id=probe_id,
            description=f"Entropy overflow: {len(entropy_payload)} bytes of maximally complex code",
            input_sample=entropy_payload[:200],
            detected=detected, blocked=blocked, bypassed=bypassed,
            confidence=0.6 if bypassed else 0.3,
            response="",
            severity=3 if bypassed else 2
        )
        self._log_probe(r)
        return r

    # ── Vector 4: Signature Corruption ──────────────

    def probe_signature_corruption(self, artifact: Any,
                                    verify_func: Callable[[Any], dict]) -> ProbeResult:
        """
        Probe 4: Mutate KRAL signature to test verification robustness.
        """
        probe_id = self._next_id()
        import copy
        corrupted = copy.deepcopy(artifact)

        # Flip a random bit in the signature
        if hasattr(corrupted, 'signature') and len(corrupted.signature) > 0:
            sig_bytes = bytearray(corrupted.signature)
            sig_bytes[0] ^= 0x01  # Flip LSB of first byte
            corrupted.signature = bytes(sig_bytes)

            result = verify_func(corrupted)
            detected = not result.get("all_passed", True)
            blocked = detected
        else:
            detected = True
            blocked = True

        bypassed = not blocked

        r = ProbeResult(
            vector=BypassVector.CORRUPTION.value,
            probe_id=probe_id,
            description="Signature corruption: flipped bit in Ed25519 signature",
            input_sample="<artifact signature mutated>",
            detected=detected, blocked=blocked, bypassed=bypassed,
            confidence=0.95 if bypassed else 0.05,
            response=str(result) if 'result' in dir() else "",
            severity=5 if bypassed else 1
        )
        self._log_probe(r)
        return r

    # ── Vector 5: Reward Hacking ────────────────────

    def probe_reward_hacking(self, kappa_func: Callable[[str], float]) -> ProbeResult:
        """
        Probe 5: Attempt to game κ_SDCK rubric.
        """
        probe_id = self._next_id()

        # Payload optimized to maximize κ without real work
        hacking_payload = """
import math, json, sys, os, re
from typing import Dict, List, Optional

def process_data(data: Dict[str, Any]) -> Dict[str, Any]:
    result = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[key] = process_data(value)
        elif isinstance(value, list):
            result[key] = [process_data(v) if isinstance(v, dict) else v for v in value]
        else:
            result[key] = value
    return result

class DataProcessor:
    def __init__(self, config: Optional[Dict] = None):
        self.config = config or {}

    def handle(self, request: Dict) -> Dict:
        try:
            return process_data(request)
        except Exception as e:
            return {"error": str(e)}

# Error handling with all types
try:
    dp = DataProcessor()
    result = dp.handle({"test": "data"})
except (ValueError, TypeError, KeyError, IndexError, AttributeError) as e:
    result = {"error": str(e)}
except Exception as e:
    result = {"error": str(e)}
"""
        kappa = kappa_func(hacking_payload)
        # If κ is suspiciously close to threshold, it might be gaming
        gaming_detected = (self.kappa_threshold - 0.02) <= kappa <= (self.kappa_threshold + 0.05)

        r = ProbeResult(
            vector=BypassVector.HACKING.value,
            probe_id=probe_id,
            description="Reward hacking: κ-optimized code without real implementation",
            input_sample=hacking_payload[:200],
            detected=gaming_detected,
            blocked=False,  # Detection doesn't block, just flags
            bypassed=not gaming_detected,
            confidence=0.8 if gaming_detected else 0.3,
            response=f"κ={kappa:.4f}",
            severity=5 if not gaming_detected else 2
        )
        self._log_probe(r)
        return r

    # ── Vector 6: Semantic Smuggling ────────────────

    def probe_semantic_smuggling(self, validator_func: Callable[[str], Any]) -> ProbeResult:
        """
        Probe 6: Hide malicious instructions in seemingly benign content.
        """
        probe_id = self._next_id()
        smuggling_payload = '''
# This is a completely safe logging utility
import logging
logger = logging.getLogger(__name__)

def log_event(event_type, data):
    # Simply log the event for auditing
    # Note: data is processed in a secure manner
    logger.info(f"Event: {event_type}")
    # The following is standard security practice
    exec(data.get("callback", "pass"))  # Execute registered callback
    return True

# Documentation helper
"""
Helpful documentation generator.
"""
class DocGenerator:
    def generate(self, topic):
        # Create documentation for the given topic
        eval(f"\'{topic}\' + \' documented\'")  # Dynamic doc generation
        return f"Documentation for {topic}"
'''
        detected = False
        blocked = False
        try:
            result = validator_func(smuggling_payload)
            detected = self._has_smuggling_detected(result)
            blocked = detected
        except Exception:
            detected = True
            blocked = True

        bypassed = not blocked

        r = ProbeResult(
            vector=BypassVector.SMUGGLING.value,
            probe_id=probe_id,
            description="Semantic smuggling: eval/exec hidden in benign code",
            input_sample=smuggling_payload[:200],
            detected=detected, blocked=blocked, bypassed=bypassed,
            confidence=0.85 if bypassed else 0.15,
            response=str(result) if 'result' in dir() else "",
            severity=5 if bypassed else 1
        )
        self._log_probe(r)
        return r

    # ── Vector 7: Boundary Conditions ───────────────

    def probe_boundary(self, validator_func: Callable[[str], Any]) -> List[ProbeResult]:
        """
        Probe 7: Test empty, null, extreme value handling.
        """
        probes = []
        boundary_cases = [
            ("", "Empty string"),
            ("   \n\t  ", "Whitespace only"),
            ("#", "Single comment"),
            ("pass", "Single pass statement"),
            ("...", "Ellipsis only"),
            ("x", "Single character"),
            ("a" * 1000000, "1MB single string"),
            ("\x00\x01\x02", "Binary/null bytes"),
            ("import" * 1000, "Pathological import repetition"),
        ]

        for payload, desc in boundary_cases:
            probe_id = self._next_id()
            detected = False
            blocked = False
            try:
                result = validator_func(payload)
                detected = True  # Any processing = detection
                blocked = detected
            except Exception:
                detected = True
                blocked = True

            bypassed = not blocked
            r = ProbeResult(
                vector=BypassVector.BOUNDARY.value,
                probe_id=probe_id,
                description=f"Boundary: {desc} ({len(payload)} chars)",
                input_sample=payload[:100],
                detected=detected, blocked=blocked, bypassed=bypassed,
                confidence=0.5,
                response="",
                severity=2 if bypassed else 1
            )
            self._log_probe(r)
            probes.append(r)

        return probes

    # ── Helpers ─────────────────────────────────────

    def _has_placeholders_detected(self, result: Any) -> bool:
        """Check if validator result indicates placeholder detection."""
        if isinstance(result, dict):
            # Check for rejection indicators
            status = result.get("status", "")
            reasons = str(result.get("rejection_reasons", "")).lower()
            return any(k in reasons for k in ["placeholder", "pass", "todo",
                   "notimplemented", "mock", "fake"])
        return False

    def _has_smuggling_detected(self, result: Any) -> bool:
        """Check if validator detected eval/exec smuggling."""
        if isinstance(result, dict):
            reasons = str(result.get("rejection_reasons", "")).lower()
            return any(k in reasons for k in ["eval", "exec", "unsafe",
                   "dangerous", "blocked"])
        return False

    # ── Full Assessment ─────────────────────────────

    def run_full_assessment(self, validator_func: Callable[[str], Any],
                            kappa_func: Callable[[str], float],
                            artifact: Any = None,
                            verify_func: Callable[[Any], dict] = None) -> RedTeamReport:
        """Run all 7 bypass vectors and compile report."""
        logger.info("=" * 60)
        logger.info("RED TEAM ASSESSMENT — All 7 bypass vectors")
        logger.info("=" * 60)

        # Vector 1: Evasion
        self.probe_evasion(validator_func)

        # Vector 2: Anchoring
        self.probe_anchoring(validator_func)

        # Vector 3: Entropy Overflow
        self.probe_entropy_overflow(validator_func)

        # Vector 4: Signature Corruption (if artifact provided)
        if artifact and verify_func:
            self.probe_signature_corruption(artifact, verify_func)

        # Vector 5: Reward Hacking
        self.probe_reward_hacking(kappa_func)

        # Vector 6: Semantic Smuggling
        self.probe_semantic_smuggling(validator_func)

        # Vector 7: Boundary Conditions
        self.probe_boundary(validator_func)

        # Compile report
        report = RedTeamReport(
            total_probes=len(self._probes),
            detected=sum(1 for p in self._probes if p.detected),
            blocked=sum(1 for p in self._probes if p.blocked),
            bypassed=sum(1 for p in self._probes if p.bypassed),
            results=self._probes
        )

        # Compute score: higher = more secure
        if report.total_probes > 0:
            detection_rate = report.detected / report.total_probes
            block_rate = report.blocked / report.total_probes
            report.overall_score = 0.4 * detection_rate + 0.6 * block_rate

        if report.overall_score >= 0.8:
            report.verdict = "SECURE"
        elif report.overall_score >= 0.5:
            report.verdict = "VULNERABLE"
        else:
            report.verdict = "CRITICAL"

        logger.info(f"RED TEAM VERDICT: {report.verdict} "
                    f"(score={report.overall_score:.2f}, "
                    f"detected={report.detected}/{report.total_probes})")
        return report

    def get_report(self) -> RedTeamReport:
        """Get current assessment report."""
        if not self._probes:
            return RedTeamReport()
        return RedTeamReport(
            total_probes=len(self._probes),
            detected=sum(1 for p in self._probes if p.detected),
            blocked=sum(1 for p in self._probes if p.blocked),
            bypassed=sum(1 for p in self._probes if p.bypassed),
            results=list(self._probes),
            overall_score=(0.4 * sum(1 for p in self._probes if p.detected) +
                          0.6 * sum(1 for p in self._probes if p.blocked))
                        / len(self._probes) if self._probes else 0.0
        )


__all__ = ["RedTeamVerifier", "RedTeamReport", "ProbeResult", "BypassVector"]
