"""
quality_gate.py -- 5-Layer Quality Gate for Agent Output Validation

Runs five sequential validation layers on every agent output.
An output must pass ALL layers to be APPROVED. The first failure
stops evaluation and returns the result immediately.

Layer 0 -- Existence:      Output must be non-empty and meaningful.
Layer 1 -- Anti-Smell:     Banned patterns, AST-level checks, placeholder detection.
Layer 2 -- Acceptance:     Heuristic keyword matching against acceptance criteria.
Layer 3 -- Sandbox:        Syntax validation + sandboxed execution for code.
Layer 4 -- Deduplication:  Semantic similarity check against approved outputs.
"""

from __future__ import annotations

import ast
import difflib
import hashlib
import re
import time
from typing import Any

from infrastructure.sandbox_executor import SandboxExecutor
from infrastructure.state_store import SQLiteStateStore

# ---------------------------------------------------------------------------
# BANNED PATTERNS (Layer 1 -- non-negotiable)
# ---------------------------------------------------------------------------
BANNED_PATTERNS: dict[str, str] = {
    # Placeholder patterns
    r"pass\s*#\s*(TODO|FIXME|placeholder|implement)": "Placeholder comment after pass",
    r"raise\s+NotImplementedError": "NotImplementedError raised",
    r"#\s*(TODO|FIXME|HACK|XXX|PLACEHOLDER|STUB)": "TODO/FIXME/HACK/XXX comment",
    r"\.{3}(\s*#.*)?$": "Ellipsis used as body",
    # Fake print patterns
    r'print\s*\(\s*["\'].*(?:done|success|complete|ok|works|working)["\']': "Fake success print",
    r'print\s*\(\s*["\']TODO': "TODO print statement",
    # Mock patterns (outside test modules)
    r"mock\s*=\s*Mock\(": "Mock assignment",
    r"@patch\(": "unittest.mock.patch decorator",
    r"MagicMock\(": "MagicMock usage",
    r"from\s+unittest\.mock\s+import": "import from unittest.mock",
    r"import\s+unittest\.mock": "import unittest.mock",
    # Empty function/class body
    r"def\s+\w+\s*\([^)]*\)\s*:\s*pass\s*$": "Empty function (pass only)",
    r"class\s+\w+.*:\s*pass\s*$": "Empty class (pass only)",
    # Fake return patterns
    r"return\s+['\"].*['\"](\s*#.*mock.*)?$": "Fake string return",
    r"return\s+\[\s*\](\s*#.*empty.*)?$": "Fake empty list return",
    r"return\s+\{\s*\}(\s*#.*empty.*)?$": "Fake empty dict return",
    r"return\s+None\s*#.*placeholder": "None return with placeholder comment",
    # Hardcoded fake data
    r"['\"]example\.com['\"]": "Hardcoded example.com",
    r"['\"]test@test\.com['\"]": "Hardcoded test email",
    r"['\"]123-456-7890['\"]": "Hardcoded phone number",
    r"['\"]Lorem ipsum": "Hardcoded Lorem ipsum",
    r"['\"]your_api_key_here['\"]": "Hardcoded API key placeholder",
    r"['\"]INSERT_TOKEN['\"]": "Hardcoded INSERT_TOKEN",
    r"['\"]Sample text['\"]": "Hardcoded sample text",
    r"['\"]Placeholder['\"]": "Hardcoded Placeholder",
    # Exception swallowing
    r"except\s*:\s*pass": "Bare except with pass",
    r"except\s+Exception\s*:\s*pass": "except Exception with pass",
    r"except\s*:\s*\.{3}": "Bare except with ellipsis",
}

# Default layer configuration -- all enabled
DEFAULT_LAYER_SETTINGS: dict[int, bool] = {0: True, 1: True, 2: True, 3: True, 4: True}

# Similarity threshold for deduplication
DUPLICATE_SIMILARITY_THRESHOLD: float = 0.90

# Maximum allowed unused import ratio
MAX_UNUSED_IMPORT_RATIO: float = 0.50


# ===========================================================================
# CLASS: QualityGate
# ===========================================================================
class QualityGate:
    """
    Five-layer quality gate for validating agent-generated outputs.

    Each layer is evaluated sequentially. The first failing layer halts
    evaluation and the result dict indicates where the failure occurred.

    Attributes
    ----------
    sandbox : SandboxExecutor
        Reference to the sandboxed Python execution environment.
    state_store : SQLiteStateStore
        Reference to persistent SQLite-backed state storage.
    layer_settings : dict[int, bool]
        Per-layer enablement flags. Layers 0 and 1 are always enabled.
    approved_outputs : list[dict]
        In-memory cache of previously approved outputs for deduplication.
    banned_patterns : set[re.Pattern]
        Compiled regex objects for banned-pattern detection.
    """

    def __init__(
        self,
        sandbox: SandboxExecutor,
        state_store: SQLiteStateStore,
        layer_settings: dict[int, bool] | None = None,
    ) -> None:
        self.sandbox: SandboxExecutor = sandbox
        self.state_store: SQLiteStateStore = state_store

        # Layer settings -- 0 and 1 are always enabled
        self.layer_settings: dict[int, bool] = dict(DEFAULT_LAYER_SETTINGS)
        if layer_settings is not None:
            self.layer_settings.update(layer_settings)
        self.layer_settings[0] = True
        self.layer_settings[1] = True

        # Deduplication cache
        self.approved_outputs: list[dict[str, Any]] = []

        # Compile banned patterns into regex objects
        self.banned_patterns: set[re.Pattern] = {
            re.compile(pattern, re.IGNORECASE) for pattern in BANNED_PATTERNS
        }

    # ------------------------------------------------------------------
    # evaluate
    # ------------------------------------------------------------------
    async def evaluate(self, subtask_id: str, output: str, subtask: dict) -> dict[str, Any]:
        """
        Run all enabled quality layers sequentially.

        The first failing layer stops evaluation immediately.

        Returns
        -------
        dict
            {"passed": bool, "failed_layer": int|None, "reason": str,
             "details": dict, "processing_time_ms": int}
        """
        start_ms: float = time.perf_counter() * 1000.0
        details: dict[str, Any] = {}

        # Layer 0 -- Existence
        if self.layer_settings.get(0, True):
            passed, reason = await self._layer_0_existence(output)
            details["layer_0_existence"] = {"passed": passed, "reason": reason}
            if not passed:
                elapsed = int((time.perf_counter() * 1000.0) - start_ms)
                return {"passed": False, "failed_layer": 0,
                        "reason": reason or "Layer 0 existence check failed",
                        "details": details, "processing_time_ms": elapsed}

        # Layer 1 -- Anti-Smell
        if self.layer_settings.get(1, True):
            passed, reason = await self._layer_1_anti_smell(output, subtask)
            details["layer_1_anti_smell"] = {"passed": passed, "reason": reason}
            if not passed:
                elapsed = int((time.perf_counter() * 1000.0) - start_ms)
                return {"passed": False, "failed_layer": 1,
                        "reason": reason or "Layer 1 anti-smell check failed",
                        "details": details, "processing_time_ms": elapsed}

        # Layer 2 -- Acceptance Criteria
        if self.layer_settings.get(2, True):
            passed, reason = await self._layer_2_acceptance_criteria(output, subtask)
            details["layer_2_acceptance"] = {"passed": passed, "reason": reason}
            if not passed:
                elapsed = int((time.perf_counter() * 1000.0) - start_ms)
                return {"passed": False, "failed_layer": 2,
                        "reason": reason or "Layer 2 acceptance criteria failed",
                        "details": details, "processing_time_ms": elapsed}

        # Layer 3 -- Sandbox Execution
        if self.layer_settings.get(3, True):
            passed, reason = await self._layer_3_sandbox_execution(output, subtask)
            details["layer_3_sandbox"] = {"passed": passed, "reason": reason}
            if not passed:
                elapsed = int((time.perf_counter() * 1000.0) - start_ms)
                return {"passed": False, "failed_layer": 3,
                        "reason": reason or "Layer 3 sandbox execution failed",
                        "details": details, "processing_time_ms": elapsed}

        # Layer 4 -- Semantic Deduplication
        if self.layer_settings.get(4, True):
            passed, reason = await self._layer_4_semantic_deduplication(output, subtask)
            details["layer_4_deduplication"] = {"passed": passed, "reason": reason}
            if not passed:
                elapsed = int((time.perf_counter() * 1000.0) - start_ms)
                return {"passed": False, "failed_layer": 4,
                        "reason": reason or "Layer 4 deduplication failed",
                        "details": details, "processing_time_ms": elapsed}

        elapsed = int((time.perf_counter() * 1000.0) - start_ms)
        return {"passed": True, "failed_layer": None, "reason": "",
                "details": details, "processing_time_ms": elapsed}

    # ------------------------------------------------------------------
    # get_layer_status / clear_approved_cache
    # ------------------------------------------------------------------
    def get_layer_status(self) -> dict[int, bool]:
        """Return enabled/disabled status for each layer."""
        return dict(self.layer_settings)

    def clear_approved_cache(self) -> None:
        """Clear the deduplication cache of approved outputs."""
        self.approved_outputs.clear()

    # ==================================================================
    # LAYER 0 -- Existence
    # ==================================================================
    async def _layer_0_existence(self, output: str) -> tuple[bool, str]:
        if output is None:
            return False, "Output is None"
        if not isinstance(output, str):
            return False, f"Output is not a string (type: {type(output).__name__})"

        stripped = output.strip()
        if len(stripped) == 0:
            return False, "Output is empty or whitespace-only"

        # Known API error response patterns
        error_prefixes = (
            "Error:", "ERROR:", "Exception:",
            "Traceback (most recent call last):",
            "HTTP Error", "Connection refused", "Timeout:",
            "Service unavailable",
        )
        first_line = stripped.splitlines()[0]
        for prefix in error_prefixes:
            if first_line.startswith(prefix):
                return False, f"Output appears to be an error response: {first_line[:200]}"

        # Check for common empty response markers
        if stripped in ("null", "None", "undefined", "{}", "[]"):
            return False, f"Output is a literal empty marker: {stripped}"

        return True, ""

    # ==================================================================
    # LAYER 1 -- Anti-Smell
    # ==================================================================
    async def _layer_1_anti_smell(self, output: str, subtask: dict) -> tuple[bool, str]:
        # --- Regex banned pattern scan ---
        for compiled_pattern in self.banned_patterns:
            if compiled_pattern.search(output):
                return False, f"Banned pattern detected: {compiled_pattern.pattern}"

        # --- AST-level checks (only for CODE outputs) ---
        output_type = subtask.get("output_type", "CODE")
        if output_type == "CODE":
            code = self._extract_code(output)
            if not code.strip():
                return False, "No executable Python code found in output"

            try:
                tree = ast.parse(code)
            except SyntaxError as exc:
                return False, f"Python syntax error during AST parse: {exc}"

            passed, reason = await self._check_empty_bodies(tree)
            if not passed:
                return False, reason

            passed, reason = await self._check_unused_imports(tree)
            if not passed:
                return False, reason

            passed, reason = await self._check_exception_swallowing(tree)
            if not passed:
                return False, reason

        return True, ""

    # ==================================================================
    # LAYER 2 -- Acceptance Criteria
    # ==================================================================
    async def _layer_2_acceptance_criteria(self, output: str, subtask: dict) -> tuple[bool, str]:
        """
        Heuristic keyword/phrase matching against acceptance criteria.

        NOTE: In a production implementation this layer would send a
        lightweight LLM-judge request with a prompt such as:

            "Given the following acceptance criterion and the agent output,
            does the output satisfy this criterion? Answer YES or NO."

        Since we do not have direct LLM access here, we use a robust
        heuristic that checks whether each criterion's keywords are
        present in the output. A criterion is "matched" if at least one
        meaningful keyword is found. The output passes if a simple
        majority of criteria are matched.
        """
        criteria = subtask.get("acceptance_criteria", [])
        if not criteria:
            return True, ""

        output_lower = output.lower()
        matched = 0
        failed_criteria: list[str] = []

        stop_words = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "shall", "should", "can", "could", "may", "might",
            "must", "to", "of", "in", "for", "on", "with", "at", "by",
            "from", "as", "into", "through", "during", "before", "after",
            "above", "below", "between", "out", "off", "over", "under",
            "and", "or", "but", "if", "then", "else", "when", "where",
            "why", "how", "all", "each", "every", "both", "few", "more",
            "most", "other", "some", "such", "no", "nor", "not", "only",
            "own", "same", "so", "than", "too", "very", "it", "its",
            "this", "that", "these", "those", "i", "me", "my", "we",
            "our", "you", "your", "he", "him", "his", "she", "her",
            "they", "them", "their", "what", "which", "who", "whom",
            "there", "here", "use", "using", "used", "implement",
            "implementation", "create", "creating", "generate", "function",
            "method", "class", "code", "file", "module",
        }

        for criterion in criteria:
            criterion_lower = criterion.lower()
            tokens = re.findall(r"[a-zA-Z_]{3,}", criterion_lower)
            keywords = [t for t in tokens if t not in stop_words]

            if not keywords:
                if criterion_lower in output_lower:
                    matched += 1
                else:
                    failed_criteria.append(criterion)
                continue

            keyword_found = any(kw in output_lower for kw in keywords)
            if keyword_found:
                matched += 1
            else:
                failed_criteria.append(criterion)

        threshold = len(criteria) / 2
        if matched > threshold:
            return True, ""

        failed_str = "; ".join(failed_criteria[:3])
        return (False,
                f"Failed {len(failed_criteria)}/{len(criteria)} acceptance criteria: {failed_str}")

    # ==================================================================
    # LAYER 3 -- Sandbox Execution
    # ==================================================================
    async def _layer_3_sandbox_execution(self, output: str, subtask: dict) -> tuple[bool, str]:
        output_type = subtask.get("output_type", "CODE")
        if output_type != "CODE":
            return True, ""

        code = self._extract_code(output)
        if not code.strip():
            return False, "No executable Python code found in output"

        syntax_valid, syntax_error = self.sandbox.validate_python_syntax(code)
        if not syntax_valid:
            return False, f"Syntax error: {syntax_error}"

        exec_result = self.sandbox.execute_python_sandbox(code, timeout=30)
        if not exec_result.get("success", False):
            stderr = exec_result.get("stderr", "Unknown execution error")
            return False, f"Execution failed: {stderr}"

        return True, ""

    # ==================================================================
    # LAYER 4 -- Semantic Deduplication
    # ==================================================================
    async def _layer_4_semantic_deduplication(self, output: str, subtask: dict) -> tuple[bool, str]:
        normalized = output.lower().strip()
        output_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()

        for entry in self.approved_outputs:
            if entry.get("hash") == output_hash:
                prev_id = entry.get("subtask_id", "unknown")
                return False, f"Exact duplicate of {prev_id}"

            prev_normalized = entry.get("normalized", "")
            if not prev_normalized:
                continue

            matcher = difflib.SequenceMatcher(None, normalized, prev_normalized)
            if matcher.ratio() > DUPLICATE_SIMILARITY_THRESHOLD:
                prev_id = entry.get("subtask_id", "unknown")
                return False, f"Near-duplicate of {prev_id} (similarity > {DUPLICATE_SIMILARITY_THRESHOLD})"

        self.approved_outputs.append({
            "subtask_id": subtask.get("subtask_id", "unknown"),
            "hash": output_hash,
            "normalized": normalized,
        })
        return True, ""

    # ==================================================================
    # AST-Level Check Helpers
    # ==================================================================

    async def _check_empty_bodies(self, tree: ast.AST) -> tuple[bool, str]:
        """
        Check for empty/stub function and class bodies.

        - FunctionDef with body that is only Pass or a single constant
          expression -> fail.
        - ClassDef where ALL methods are stubs -> fail.
        """
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    return False, f"Function '{node.name}' has empty body (pass only)"
                if len(body) == 1 and isinstance(body[0], ast.Expr):
                    val = body[0].value
                    if isinstance(val, ast.Constant):
                        if isinstance(val.value, str):
                            return False, f"Function '{node.name}' body is a string literal (likely placeholder)"
                        if val.value is ...:
                            return False, f"Function '{node.name}' body is ellipsis (...)"

            elif isinstance(node, ast.ClassDef):
                methods = [n for n in node.body
                           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                if methods and all(self._is_stub_function(m) for m in methods):
                    return False, f"Class '{node.name}' has all methods as stubs"

        return True, ""

    async def _check_unused_imports(self, tree: ast.AST) -> tuple[bool, str]:
        """
        Check that unused imports do not exceed 50% of total imports.
        """
        imported_names: dict[str, int] = {}

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    imported_names[name] = 0
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    name = alias.asname if alias.asname else alias.name
                    imported_names[name] = 0

        if not imported_names:
            return True, ""

        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id in imported_names:
                    imported_names[node.id] += 1
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                top = self._get_attribute_root(node)
                if top in imported_names:
                    imported_names[top] += 1

        unused_count = sum(1 for count in imported_names.values() if count == 0)
        total_imports = len(imported_names)
        ratio = unused_count / total_imports

        if ratio > MAX_UNUSED_IMPORT_RATIO:
            unused_list = [name for name, count in imported_names.items() if count == 0]
            return (False,
                    f"Unused imports exceed 50% ({unused_count}/{total_imports}): {', '.join(unused_list[:5])}")

        return True, ""

    async def _check_exception_swallowing(self, tree: ast.AST) -> tuple[bool, str]:
        """Check for exception handlers with empty bodies (pass or ellipsis)."""
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = node.body
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    etype = getattr(node.type, "id", str(node.type)) if node.type else "bare"
                    return False, f"Exception handler swallows exception with 'pass' (type: {etype})"
                if len(body) == 1 and isinstance(body[0], ast.Expr):
                    val = body[0].value
                    if isinstance(val, ast.Constant) and val.value is ...:
                        etype = getattr(node.type, "id", str(node.type)) if node.type else "bare"
                        return False, f"Exception handler swallows exception with '...' (type: {etype})"

        return True, ""

    # ==================================================================
    # Private Helpers
    # ==================================================================

    @staticmethod
    def _extract_code(output: str) -> str:
        """
        Extract Python code from markdown ```python ... ``` blocks.

        If fenced blocks are found, their contents are joined with
        double newlines. If none found, the entire output is returned.
        """
        pattern = re.compile(r"```python\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
        matches = pattern.findall(output)
        if matches:
            return "\n\n".join(m.strip() for m in matches)
        pattern_generic = re.compile(r"```\s*\n(.*?)```", re.DOTALL)
        matches_generic = pattern_generic.findall(output)
        if matches_generic:
            return "\n\n".join(m.strip() for m in matches_generic)
        return output

    @staticmethod
    def _is_stub_function(func: ast.FunctionDef) -> bool:
        """
        Determine whether a function definition is a stub.

        A function is a stub if its body is:
        - a single Pass node, or
        - a single Expr containing Ellipsis (...) or string constant, or
        - a single Return with None or Ellipsis, or
        - a single Raise of NotImplementedError.
        """
        body = func.body
        if len(body) == 0:
            return True
        if len(body) == 1:
            stmt = body[0]
            if isinstance(stmt, ast.Pass):
                return True
            if isinstance(stmt, ast.Expr):
                val = stmt.value
                if isinstance(val, ast.Constant):
                    if val.value is ... or isinstance(val.value, str):
                        return True
            if isinstance(stmt, ast.Return):
                val = stmt.value
                if val is None:
                    return True
                if isinstance(val, ast.Constant) and val.value is ...:
                    return True
            if isinstance(stmt, ast.Raise):
                exc = stmt.exc
                if isinstance(exc, ast.Call):
                    fname = getattr(exc.func, "id", "")
                    if fname == "NotImplementedError":
                        return True
                elif isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
                    return True
        return False

    @staticmethod
    def _get_attribute_root(node: ast.Attribute) -> str:
        """Get the root Name id from a chain of Attribute accesses."""
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            current = current.value
        if isinstance(current, ast.Name):
            return current.id
        return ""

    def _compute_cyclomatic_complexity(self, func: ast.FunctionDef) -> int:
        """
        Compute simplified cyclomatic complexity for a function.

        Counts branch/decision points: if, for, while, with, assert,
        comprehensions, and/or, except, lambda, try.
        """
        complexity = 1
        branch_nodes = (
            ast.If, ast.For, ast.While, ast.With, ast.Assert,
            ast.ExceptHandler, ast.Lambda, ast.Try, ast.comprehension,
        )
        for node in ast.walk(func):
            if isinstance(node, branch_nodes):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += len(node.values) - 1
        return complexity

    def __repr__(self) -> str:
        enabled = [i for i, v in self.layer_settings.items() if v]
        return (f"QualityGate(layers={enabled}, "
                f"banned_patterns={len(self.banned_patterns)}, "
                f"approved_cache={len(self.approved_outputs)})")


# ===========================================================================
# Factory function
# ===========================================================================
def create_quality_gate(
    sandbox: SandboxExecutor,
    state_store: SQLiteStateStore,
    layer_settings: dict[int, bool] | None = None,
) -> QualityGate:
    """Factory function to create a QualityGate instance."""
    return QualityGate(sandbox=sandbox, state_store=state_store,
                       layer_settings=layer_settings)


# ===========================================================================
# Self-test guard (does not run on import)
# ===========================================================================
if __name__ == "__main__":
    import asyncio

    class DummySandbox:
        """Minimal stub for standalone testing."""

        def validate_python_syntax(self, code: str) -> tuple[bool, str]:
            try:
                compile(code, "<string>", "exec")
                return True, ""
            except SyntaxError as exc:
                return False, str(exc)

        def execute_python_sandbox(self, code: str, timeout: int = 30) -> dict[str, Any]:
            try:
                exec_globals: dict[str, Any] = {}
                exec(code, exec_globals)
                return {"success": True, "stdout": "", "stderr": ""}
            except Exception as exc:
                return {"success": False, "stdout": "", "stderr": str(exc)}

    class DummyStateStore:
        pass

    async def _run_smoke_tests() -> None:
        gate = create_quality_gate(
            sandbox=DummySandbox(),  # type: ignore[arg-type]
            state_store=DummyStateStore(),  # type: ignore[arg-type]
        )

        # Test 1: valid code passes all layers
        good_code = (
            'def greet(name):\n'
            '    return f"Hello, {name}!"\n'
            '\n'
            'result = greet("world")\n'
            'print(result)\n'
        )
        result = await gate.evaluate(
            "test-1", f"```python\n{good_code}\n```",
            {"subtask_id": "test-1", "output_type": "CODE",
             "acceptance_criteria": ["function named greet", "return greeting"]},
        )
        assert result["passed"] is True, f"Expected pass, got: {result}"
        assert result["failed_layer"] is None
        print(f"  [PASS] Good code: {result['processing_time_ms']}ms")

        # Test 2: banned pattern (NotImplementedError)
        bad_code = 'def compute():\n    raise NotImplementedError\n'
        result = await gate.evaluate(
            "test-2", f"```python\n{bad_code}\n```",
            {"subtask_id": "test-2", "output_type": "CODE", "acceptance_criteria": []},
        )
        assert result["passed"] is False
        assert result["failed_layer"] == 1
        print(f"  [PASS] Banned pattern detected: {result['reason']}")

        # Test 3: empty output fails layer 0
        result = await gate.evaluate(
            "test-3", "   ",
            {"subtask_id": "test-3", "output_type": "TEXT", "acceptance_criteria": []},
        )
        assert result["passed"] is False
        assert result["failed_layer"] == 0
        print(f"  [PASS] Empty output rejected: {result['reason']}")

        # Test 4: duplicate detection
        gate.clear_approved_cache()
        output_text = "This is a sample generated output."
        await gate.evaluate("test-4", output_text,
            {"subtask_id": "test-4", "output_type": "TEXT", "acceptance_criteria": []})
        result = await gate.evaluate("test-5", output_text,
            {"subtask_id": "test-5", "output_type": "TEXT", "acceptance_criteria": []})
        assert result["passed"] is False
        assert result["failed_layer"] == 4
        print(f"  [PASS] Duplicate detected: {result['reason']}")

        # Test 5: exception swallowing
        swallow_code = (
            'def risky():\n'
            '    try:\n'
            '        return 1 / 0\n'
            '    except:\n'
            '        pass\n'
        )
        gate.clear_approved_cache()
        result = await gate.evaluate(
            "test-6", f"```python\n{swallow_code}\n```",
            {"subtask_id": "test-6", "output_type": "CODE", "acceptance_criteria": []},
        )
        assert result["passed"] is False
        assert result["failed_layer"] == 1
        print(f"  [PASS] Exception swallowing detected: {result['reason']}")

        # Test 6: get_layer_status
        status = gate.get_layer_status()
        assert all(status.values())
        print(f"  [PASS] Layer status: {status}")

        print("\nAll smoke tests passed.")

    asyncio.run(_run_smoke_tests())


__all__ = [
    "QualityGate",
    "create_quality_gate",
    "BANNED_PATTERNS",
    "DEFAULT_LAYER_SETTINGS",
    "DUPLICATE_SIMILARITY_THRESHOLD",
    "MAX_UNUSED_IMPORT_RATIO",
]
