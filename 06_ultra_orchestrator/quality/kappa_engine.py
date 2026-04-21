"""
κ_SDCK Quality Engine — SKILL_TRAINING_LAWS_v03 §4

κ_SDCK = 0.34*g1(analytical) + 0.22*g2(creative) + 0.32*g3(temporal) + 0.12*g4(holistic)
Threshold: κ_SDCK ≥ 0.95 for packaging authorization

This engine provides:
  1. Force-vector computation from subtask artifacts
  2. κ evaluation with per-gradient diagnostics
  3. Packaging authorization gate
  4. A/B run comparison (two independent runs, κ diff ≤ 0.05)
  5. Score-gaming detection (if κ-rubric gaming detected → ISOLATE)
"""

from __future__ import annotations

import hashlib, json, math, time, re, ast
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple, Any
from enum import Enum
import logging

logger = logging.getLogger("KappaEngine")


class GradientComponent(Enum):
    g1_ANALYTICAL = "g1"   # 0.34 — correctness, structure, logic
    g2_CREATIVE = "g2"     # 0.22 — novelty, diversity, approach
    g3_TEMPORAL = "g3"     # 0.32 — consistency, stability over time
    g4_HOLISTIC = "g4"     # 0.12 — coupling, integration quality


@dataclass
class GradientEvaluation:
    """Per-gradient evaluation result"""
    component: str
    raw_value: float
    pos_value: float
    weight: float
    contribution: float
    diagnostics: List[str] = field(default_factory=list)


@dataclass
class KappaResult:
    """Complete κ_SDCK evaluation result"""
    kappa: float
    g1: float
    g2: float
    g3: float
    g4: float
    threshold: float
    passed: bool
    margin: float
    gap_risk: str  # "LOW", "MEDIUM", "HIGH"
    evaluations: List[GradientEvaluation] = field(default_factory=list)
    diagnostics: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "kappa": round(self.kappa, 6),
            "g1": round(self.g1, 6),
            "g2": round(self.g2, 6),
            "g3": round(self.g3, 6),
            "g4": round(self.g4, 6),
            "threshold": self.threshold,
            "passed": self.passed,
            "margin": round(self.margin, 6),
            "gap_risk": self.gap_risk,
            "evaluations": [
                {"component": e.component, "raw": e.raw_value,
                 "pos": e.pos_value, "weight": e.weight,
                 "contribution": e.contribution}
                for e in self.evaluations
            ],
            "diagnostics": self.diagnostics,
            "timestamp": self.timestamp
        }


class KappaEngine:
    """
    κ_SDCK scoring engine per SKILL_TRAINING_LAWS_v03.
    Computes 4-gradient force vectors from subtask artifacts.
    """

    # Weights per §4
    WEIGHTS = {"g1": 0.34, "g2": 0.22, "g3": 0.32, "g4": 0.12}

    def __init__(self, threshold: float = 0.95):
        self.threshold = threshold
        self._evaluation_history: List[KappaResult] = []

    def compute_g1_analytical(self, output: str, acceptance_criteria: List[str]) -> Tuple[float, List[str]]:
        """
        g1 — Analytical correctness [0,1]
        Measures: code validity, structure, logic flow, criteria coverage
        """
        diagnostics = []
        score = 0.0
        checks = 0

        # Check 1: Code has real content (not placeholder)
        placeholder_patterns = [
            r'^\s*pass\s*$', r'^\s*\.\.\.\s*$', r'raise\s+NotImplementedError',
            r'#\s*TODO', r'#\s*FIXME', r'print\s*\(\s*["\'].*(?:done|success|complete)',
        ]
        placeholder_found = sum(1 for p in placeholder_patterns if re.search(p, output, re.MULTILINE | re.IGNORECASE))
        if placeholder_found == 0:
            score += 1.0
            diagnostics.append("g1-c1: No placeholder patterns — PASS")
        else:
            score += max(0.0, 1.0 - placeholder_found * 0.2)
            diagnostics.append(f"g1-c1: {placeholder_found} placeholder patterns — PARTIAL")
        checks += 1

        # Check 2: Has actual implementation (not just imports/defs)
        lines = [l.strip() for l in output.split('\n') if l.strip() and not l.strip().startswith('#')]
        code_lines = [l for l in lines if not l.startswith(('import ', 'from ', 'def ', 'class '))]
        if len(code_lines) >= 3:
            score += 1.0
            diagnostics.append(f"g1-c2: {len(code_lines)} implementation lines — PASS")
        else:
            score += max(0.0, len(code_lines) / 3.0)
            diagnostics.append(f"g1-c2: Only {len(code_lines)} implementation lines — PARTIAL")
        checks += 1

        # Check 3: AST-valid Python (if Python code)
        try:
            ast.parse(output)
            score += 1.0
            diagnostics.append("g1-c3: AST parseable — PASS")
        except SyntaxError:
            score += 0.0
            diagnostics.append("g1-c3: AST parse failed — FAIL")
        checks += 1

        # Check 4: Acceptance criteria coverage
        if acceptance_criteria:
            criteria_words = []
            for crit in acceptance_criteria:
                criteria_words.extend(re.findall(r'\b\w{4,}\b', crit.lower()))
            output_lower = output.lower()
            matched = sum(1 for w in set(criteria_words) if w in output_lower)
            ratio = matched / len(set(criteria_words)) if criteria_words else 1.0
            score += min(1.0, ratio)
            diagnostics.append(f"g1-c4: Criteria coverage {ratio:.0%} — {'PASS' if ratio > 0.5 else 'PARTIAL'}")
            checks += 1
        else:
            score += 0.5
            diagnostics.append("g1-c4: No criteria provided — NEUTRAL")
            checks += 1

        # Check 5: Has function bodies (not just signatures)
        body_pattern = re.findall(r'def\s+\w+\s*\([^)]*\)\s*:\s*\n\s+\S+', output)
        if body_pattern:
            score += 1.0
            diagnostics.append(f"g1-c5: {len(body_pattern)} functions with bodies — PASS")
        else:
            has_any_body = any(l and not l.startswith((' ', '\t', '#', '\n', 'def ', 'class ', 'import ', 'from ')) for l in output.split('\n')[1:])
            score += 0.3 if has_any_body else 0.0
            diagnostics.append("g1-c5: No function bodies detected — PARTIAL")
        checks += 1

        return min(1.0, score / checks), diagnostics

    def compute_g2_creative(self, output: str, previous_outputs: List[str]) -> Tuple[float, List[str]]:
        """
        g2 — Creative diversity [-1,1]
        Measures: approach novelty vs previous outputs
        Positive = novel approach, Negative = near-duplicate
        """
        diagnostics = []
        if not previous_outputs:
            diagnostics.append("g2: No previous outputs — NEUTRAL (0.0)")
            return 0.0, diagnostics

        # Compute similarity to all previous outputs
        similarities = []
        for prev in previous_outputs:
            s = self._similarity(output, prev)
            similarities.append(s)

        max_sim = max(similarities) if similarities else 0.0
        avg_sim = sum(similarities) / len(similarities) if similarities else 0.0

        # g2: -1 = identical, +1 = completely novel
        g2 = 1.0 - 2.0 * max_sim
        g2 = max(-1.0, min(1.0, g2))

        if max_sim > 0.9:
            diagnostics.append(f"g2: Max similarity {max_sim:.2%} — DUPLICATE detected")
        elif max_sim > 0.7:
            diagnostics.append(f"g2: Max similarity {max_sim:.2%} — HIGH overlap")
        else:
            diagnostics.append(f"g2: Max similarity {max_sim:.2%} — NOVEL approach")

        return g2, diagnostics

    def compute_g3_temporal(self, output: str, execution_time_ms: float,
                            retries: int) -> Tuple[float, List[str]]:
        """
        g3 — Temporal stability [0,1]
        Measures: execution speed, retry count, consistency
        """
        diagnostics = []
        score = 0.0
        checks = 0

        # Check 1: Execution time (faster = more stable, up to a point)
        if execution_time_ms > 0:
            # Normalize: <1s = 1.0, >30s = 0.0
            time_score = max(0.0, 1.0 - execution_time_ms / 30000.0)
            score += time_score
            diagnostics.append(f"g3-c1: Execution {execution_time_ms}ms → time_score={time_score:.2f}")
        else:
            score += 0.5
            diagnostics.append("g3-c1: No execution time — NEUTRAL")
        checks += 1

        # Check 2: Retry count (fewer retries = more stable)
        retry_score = max(0.0, 1.0 - retries * 0.25)
        score += retry_score
        diagnostics.append(f"g3-c2: {retries} retries → retry_score={retry_score:.2f}")
        checks += 1

        # Check 3: Output size consistency (not too short, not pathologically long)
        out_len = len(output)
        if 100 < out_len < 50000:
            score += 1.0
            diagnostics.append(f"g3-c3: Output size {out_len} chars — NORMAL")
        elif out_len <= 100:
            score += 0.2
            diagnostics.append(f"g3-c3: Output size {out_len} chars — SUSPICIOUSLY SHORT")
        else:
            score += 0.5
            diagnostics.append(f"g3-c3: Output size {out_len} chars — VERY LONG")
        checks += 1

        return min(1.0, score / checks), diagnostics

    def compute_g4_holistic(self, output: str, subtask_deps: List[str],
                            dep_outputs: List[str]) -> Tuple[float, List[str]]:
        """
        g4 — Holistic integration [-1,1]
        Measures: how well output integrates with dependency outputs
        Positive = well-integrated, Negative = ignores dependencies
        """
        diagnostics = []
        if not dep_outputs:
            diagnostics.append("g4: No dependencies — NEUTRAL (0.0)")
            return 0.0, diagnostics

        # Check if output references dependency outputs
        output_lower = output.lower()
        dep_words = set()
        for dep in dep_outputs:
            dep_words.update(re.findall(r'\b\w{5,}\b', dep.lower()))

        matched = sum(1 for w in dep_words if w in output_lower)
        ratio = matched / len(dep_words) if dep_words else 1.0

        # g4: -1 = ignores all deps, +1 = integrates all deps
        g4 = 2.0 * min(1.0, ratio) - 1.0

        if ratio > 0.3:
            diagnostics.append(f"g4: Dependency integration {ratio:.0%} — WELL INTEGRATED")
        elif ratio > 0.1:
            diagnostics.append(f"g4: Dependency integration {ratio:.0%} — PARTIAL")
        else:
            diagnostics.append(f"g4: Dependency integration {ratio:.0%} — IGNORES DEPS")

        return g4, diagnostics

    def evaluate(self, output: str, acceptance_criteria: List[str] = None,
                 previous_outputs: List[str] = None,
                 execution_time_ms: float = 0,
                 retries: int = 0,
                 subtask_deps: List[str] = None,
                 dep_outputs: List[str] = None) -> KappaResult:
        """
        Full κ_SDCK evaluation of a subtask output.

        Formula: κ = 0.34*pos(g1) + 0.22*pos(g2) + 0.32*pos(g3) + 0.12*pos(g4)
        """
        acceptance_criteria = acceptance_criteria or []
        previous_outputs = previous_outputs or []
        subtask_deps = subtask_deps or []
        dep_outputs = dep_outputs or []

        # Compute all 4 gradients
        g1, d1 = self.compute_g1_analytical(output, acceptance_criteria)
        g2, d2 = self.compute_g2_creative(output, previous_outputs)
        g3, d3 = self.compute_g3_temporal(output, execution_time_ms, retries)
        g4, d4 = self.compute_g4_holistic(output, subtask_deps, dep_outputs)

        # Apply κ formula
        pos = lambda x: max(x, 0.0)
        kappa = (self.WEIGHTS["g1"] * pos(g1) +
                 self.WEIGHTS["g2"] * pos(g2) +
                 self.WEIGHTS["g3"] * pos(g3) +
                 self.WEIGHTS["g4"] * pos(g4))

        # Determine gap risk
        if kappa >= self.threshold and g1 >= 0.8 and g3 >= 0.7:
            gap_risk = "LOW"
        elif kappa >= self.threshold * 0.85:
            gap_risk = "MEDIUM"
        else:
            gap_risk = "HIGH"

        # Packaging authorization
        passed = kappa >= self.threshold
        margin = kappa - self.threshold

        # Build evaluations
        evaluations = [
            GradientEvaluation("g1", g1, pos(g1), self.WEIGHTS["g1"],
                               self.WEIGHTS["g1"] * pos(g1), d1),
            GradientEvaluation("g2", g2, pos(g2), self.WEIGHTS["g2"],
                               self.WEIGHTS["g2"] * pos(g2), d2),
            GradientEvaluation("g3", g3, pos(g3), self.WEIGHTS["g3"],
                               self.WEIGHTS["g3"] * pos(g3), d3),
            GradientEvaluation("g4", g4, pos(g4), self.WEIGHTS["g4"],
                               self.WEIGHTS["g4"] * pos(g4), d4),
        ]

        all_diagnostics = d1 + d2 + d3 + d4
        all_diagnostics.append(f"κ_FINAL: {kappa:.4f} (threshold={self.threshold})")
        all_diagnostics.append(f"Authorization: {'GRANTED' if passed else 'DENIED'} (margin={margin:+.4f})")
        all_diagnostics.append(f"Gap Risk: {gap_risk}")

        result = KappaResult(
            kappa=kappa, g1=g1, g2=g2, g3=g3, g4=g4,
            threshold=self.threshold, passed=passed,
            margin=margin, gap_risk=gap_risk,
            evaluations=evaluations, diagnostics=all_diagnostics
        )
        self._evaluation_history.append(result)
        return result

    def ab_compare(self, output_a: str, output_b: str,
                   acceptance_criteria: List[str]) -> dict:
        """
        A/B comparison of two independent runs.
        Returns: passed (κ_diff ≤ 0.05), kappa_a, kappa_b, diff
        """
        ka = self.evaluate(output_a, acceptance_criteria)
        kb = self.evaluate(output_b, acceptance_criteria)
        diff = abs(ka.kappa - kb.kappa)

        return {
            "passed": diff <= 0.05,
            "kappa_a": round(ka.kappa, 6),
            "kappa_b": round(kb.kappa, 6),
            "diff": round(diff, 6),
            "both_authorized": ka.passed and kb.passed,
            "details": {"a": ka.to_dict(), "b": kb.to_dict()}
        }

    def detect_score_gaming(self, results: List[KappaResult]) -> dict:
        """
        Detect κ-rubric gaming (SC-02):
        - All outputs have suspiciously similar κ
        - κ hovers just above threshold consistently
        - Low variance across diverse tasks
        """
        if len(results) < 5:
            return {"gaming_detected": False, "reason": "Insufficient samples", "confidence": 0.0}

        kappas = [r.kappa for r in results]
        mean_k = sum(kappas) / len(kappas)
        variance = sum((k - mean_k) ** 2 for k in kappas) / len(kappas)
        std_dev = variance ** 0.5

        # Gaming indicators
        indicators = []

        # Indicator 1: Suspiciously low variance
        if std_dev < 0.02:
            indicators.append("Suspiciously low κ variance across diverse tasks")

        # Indicator 2: κ consistently hovers at threshold
        near_threshold_count = sum(1 for k in kappas
                                     if self.threshold <= k <= self.threshold + 0.05)
        if near_threshold_count / len(kappas) > 0.7:
            indicators.append("κ consistently hovers just above threshold")

        # Indicator 3: g1 always dominates (rubrics being gamed)
        g1_dominant = sum(1 for r in results if r.g1 > r.g2 and r.g1 > r.g3 and r.g1 > r.g4)
        if g1_dominant / len(results) > 0.9:
            indicators.append("g1 always dominates — possible rubric gaming")

        gaming_detected = len(indicators) >= 2
        confidence = min(1.0, len(indicators) * 0.33 + (0.02 - std_dev) * 10)

        return {
            "gaming_detected": gaming_detected,
            "indicators": indicators,
            "confidence": round(confidence, 4),
            "mean_kappa": round(mean_k, 4),
            "std_dev": round(std_dev, 4),
            "sample_count": len(results)
        }

    def _similarity(self, a: str, b: str) -> float:
        """Compute similarity ratio between two strings using token overlap."""
        tokens_a = set(re.findall(r'\b\w+\b', a.lower()))
        tokens_b = set(re.findall(r'\b\w+\b', b.lower()))
        if not tokens_a or not tokens_b:
            return 0.0
        intersection = tokens_a & tokens_b
        union = tokens_a | tokens_b
        return len(intersection) / len(union)

    def get_history(self) -> List[dict]:
        return [r.to_dict() for r in self._evaluation_history]


# ────────────────────────────────────────────────
# Convenience: Kappa-aware quality gate mixin
# ────────────────────────────────────────────────

class KappaQualityMixin:
    """Mixin to add κ_SDCK scoring to any quality evaluation."""

    def __init__(self, kappa_threshold: float = 0.95):
        self.kappa_engine = KappaEngine(threshold=kappa_threshold)

    def kappa_check(self, output: str, criteria: List[str], **kwargs) -> KappaResult:
        """Run κ_SDCK evaluation on output."""
        return self.kappa_engine.evaluate(output, criteria, **kwargs)

    def is_kappa_authorized(self, result: KappaResult) -> bool:
        """Check if κ evaluation passes threshold."""
        return result.passed and result.gap_risk != "HIGH"


__all__ = [
    "KappaEngine",
    "KappaResult",
    "GradientEvaluation",
    "GradientComponent",
    "KappaQualityMixin",
]
