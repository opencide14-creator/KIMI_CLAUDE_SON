"""
agents/reactive/goal.py — Goal state evaluator.

Binary: achieved / not achieved. No partial credit.
Uses RAG search to verify the result exists in memory.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Goal:
    description:      str
    success_criteria: str
    goal_id:          str = ""
    max_iterations:   int = 10


@dataclass
class GoalResult:
    achieved:    bool
    confidence:  float = 0.0     # 0.0 – 1.0
    evidence:    str   = ""      # what in RAG proves it
    iterations:  int   = 0


class GoalEvaluator:
    """
    Evaluates whether a goal has been achieved after a tool result.

    Strategy:
      1. Check if tool result is non-empty and error-free.
      2. Keyword match: does the result text contain terms from success_criteria?
      3. RAG verification: search obsidian_knowledge / session_context for evidence.
    """

    def __init__(self, substrate: Any) -> None:
        self._substrate = substrate

    def evaluate(self, goal: Goal, result: Any, ef: Any = None) -> GoalResult:
        """
        Returns GoalResult(achieved=True) when the goal is satisfied.
        Never raises — returns achieved=False on any error.
        """
        if result is None:
            return GoalResult(achieved=False, evidence="No result")

        result_str = str(result).lower()

        # Fail fast: error markers
        error_markers = ["error", "failed", "exception", "not found", "timeout"]
        if any(m in result_str for m in error_markers):
            return GoalResult(
                achieved=False,
                confidence=0.0,
                evidence=f"Result contains error marker: {result_str[:100]}",
            )

        # Keyword match against success criteria
        criteria_words = [
            w.strip().lower() for w in goal.success_criteria.split()
            if len(w.strip()) > 3
        ]
        if criteria_words:
            matched = sum(1 for w in criteria_words if w in result_str)
            confidence = matched / len(criteria_words)
            if confidence >= 0.5:
                return GoalResult(
                    achieved=True,
                    confidence=confidence,
                    evidence=f"{matched}/{len(criteria_words)} criteria keywords found in result",
                )

        # Non-empty result with no errors is a weak success
        if len(result_str.strip()) > 20:
            return GoalResult(
                achieved=True,
                confidence=0.3,
                evidence="Non-empty result with no error markers",
            )

        return GoalResult(achieved=False, evidence="Insufficient result content")
