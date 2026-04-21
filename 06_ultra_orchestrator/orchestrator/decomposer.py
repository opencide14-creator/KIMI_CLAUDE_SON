"""
Task Decomposer Module — Ultra Orchestrator

Breaks down a high-level task into a DAG of atomic SubTasks.

Algorithms implemented:
  • DFS-based cycle detection (3-color / white-gray-black)
  • Longest-path critical path computation (DAG)
  • BFS topological level assignment
  • Topological sorting for execution ordering
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger("orchestrator.decomposer")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OutputType(str, Enum):
    """Classification of the artefact a SubTask produces."""

    CODE = "CODE"
    TEXT = "TEXT"
    ANALYSIS = "ANALYSIS"
    COMMAND = "COMMAND"
    STRUCTURED_DATA = "STRUCTURED_DATA"


class Priority(str, Enum):
    """Execution priority for a SubTask."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    NORMAL = "NORMAL"
    LOW = "LOW"


# ---------------------------------------------------------------------------
# SubTask dataclass
# ---------------------------------------------------------------------------

@dataclass
class SubTask:
    """Single atomic unit of work within the orchestration graph."""

    id: str                              # "ST-{UUID}"
    parent_task_id: str                  # "TASK-{UUID}"
    title: str                           # ≤ 80 chars, action verb
    description: str                     # Full context / requirements
    acceptance_criteria: list[str]       # Measurable pass/fail criteria
    input_dependencies: list[str]        # Other SubTask IDs this depends on
    output_type: OutputType
    output_schema: dict = None           # JSONSchema when output_type == STRUCTURED_DATA
    priority: Priority = Priority.NORMAL
    max_retries: int = 3
    timeout_seconds: int = 120
    assigned_api_key: str = None
    template_name: str = "BLANK_CODE_GENERATION"
    status: str = "PENDING"
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    rejection_reasons: list = field(default_factory=list)
    retry_count: int = 0
    token_usage: dict = None
    estimated_tokens: int = 1000

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Convert SubTask to a plain dictionary (JSON-safe)."""
        return {
            "id": self.id,
            "parent_task_id": self.parent_task_id,
            "title": self.title,
            "description": self.description,
            "acceptance_criteria": list(self.acceptance_criteria),
            "input_dependencies": list(self.input_dependencies),
            "output_type": self.output_type.value if isinstance(self.output_type, OutputType) else str(self.output_type),
            "output_schema": dict(self.output_schema) if self.output_schema else None,
            "priority": self.priority.value if isinstance(self.priority, Priority) else str(self.priority),
            "max_retries": self.max_retries,
            "timeout_seconds": self.timeout_seconds,
            "assigned_api_key": self.assigned_api_key,
            "template_name": self.template_name,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "rejection_reasons": list(self.rejection_reasons),
            "retry_count": self.retry_count,
            "token_usage": dict(self.token_usage) if self.token_usage else None,
            "estimated_tokens": self.estimated_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SubTask":
        """Reconstruct a SubTask from a plain dictionary."""
        # Defensive copy so we don't mutate the caller's dict
        d = dict(data)

        # Convert string enums back to enum members
        ot = d.get("output_type", "TEXT")
        if isinstance(ot, str):
            d["output_type"] = OutputType(ot) if ot in {e.value for e in OutputType} else OutputType.TEXT

        pr = d.get("priority", "NORMAL")
        if isinstance(pr, str):
            d["priority"] = Priority(pr) if pr in {e.value for e in Priority} else Priority.NORMAL

        # Pop fields that belong to the dataclass init
        init_fields = {f.name for f in cls.__dataclass_fields__.values()}
        kwargs = {k: v for k, v in d.items() if k in init_fields}

        return cls(**kwargs)


# ---------------------------------------------------------------------------
# TaskGraph
# ---------------------------------------------------------------------------

class TaskGraph:
    """Dependency DAG composed of SubTask nodes.

    Attributes
    ----------
    subtasks : dict[str, SubTask]
        All subtasks indexed by their ID.
    dependencies : dict[str, list[str]]
        Forward edges: subtask_id → list of subtask IDs it depends on.
    dependents : dict[str, list[str]]
        Reverse edges: subtask_id → list of subtask IDs that depend on it.
    critical_path : list[str]
        Ordered list of subtask IDs on the longest (critical) path.
    topological_levels : dict[str, int]
        Maps subtask_id → topological level (0 = root).
    """

    def __init__(self) -> None:
        self.subtasks: dict[str, SubTask] = {}
        self.dependencies: dict[str, list[str]] = {}
        self.dependents: dict[str, list[str]] = {}
        self.critical_path: list[str] = []
        self.topological_levels: dict[str, int] = {}

    # ------------------------------------------------------------------
    # 1. add_subtask
    # ------------------------------------------------------------------

    def add_subtask(self, subtask: SubTask) -> None:
        """Add a subtask node to the graph, initialising adjacency lists."""
        sid = subtask.id
        if sid in self.subtasks:
            logger.warning("Subtask %s already exists in graph; overwriting.", sid)
        self.subtasks[sid] = subtask
        if sid not in self.dependencies:
            self.dependencies[sid] = []
        if sid not in self.dependents:
            self.dependents[sid] = []

    # ------------------------------------------------------------------
    # 2. add_dependency
    # ------------------------------------------------------------------

    def add_dependency(self, subtask_id: str, depends_on_id: str) -> None:
        """Add a directed edge: *subtask_id* depends on *depends_on_id*.

        Raises on self-dependency. Updates both forward and reverse maps.
        """
        if subtask_id == depends_on_id:
            raise ValueError(f"Self-dependency detected on subtask {subtask_id}")

        # Ensure both nodes exist in adjacency structures
        if subtask_id not in self.dependencies:
            self.dependencies[subtask_id] = []
        if depends_on_id not in self.dependents:
            self.dependents[depends_on_id] = []

        if depends_on_id not in self.dependencies[subtask_id]:
            self.dependencies[subtask_id].append(depends_on_id)

        if subtask_id not in self.dependents[depends_on_id]:
            self.dependents[depends_on_id].append(subtask_id)

    # ------------------------------------------------------------------
    # 3. detect_cycles — DFS 3-color (white / gray / black)
    # ------------------------------------------------------------------

    def detect_cycles(self) -> list[str]:
        """Return a list of subtask IDs involved in a cycle, or [] if acyclic.

        Uses the classic white-gray-black DFS coloring algorithm.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[str, int] = {sid: WHITE for sid in self.subtasks}
        parent: dict[str, str] = {}

        # Collect all nodes that appear in either adjacency map
        all_nodes = set(self.subtasks) | set(self.dependencies) | set(self.dependents)
        for node in all_nodes:
            if node not in color:
                color[node] = WHITE

        cycle_nodes: list[str] = []

        def _dfs(node: str) -> bool:
            """Return True if a cycle is found starting from *node*."""
            nonlocal cycle_nodes
            color[node] = GRAY

            for neighbor in self.dependencies.get(node, []):
                if neighbor not in color:
                    color[neighbor] = WHITE
                if color[neighbor] == GRAY:
                    # Back edge found — reconstruct cycle
                    cycle = [neighbor]
                    cur = node
                    while cur != neighbor:
                        cycle.append(cur)
                        cur = parent.get(cur, neighbor)
                        if cur is None:
                            break
                    cycle.append(neighbor)
                    cycle.reverse()
                    cycle_nodes = cycle
                    return True
                if color[neighbor] == WHITE:
                    parent[neighbor] = node
                    if _dfs(neighbor):
                        return True

            color[node] = BLACK
            return False

        for node in list(color.keys()):
            if color[node] == WHITE:
                if _dfs(node):
                    # Remove duplicate closing node for cleaner output
                    if len(cycle_nodes) > 1 and cycle_nodes[0] == cycle_nodes[-1]:
                        cycle_nodes = cycle_nodes[:-1]
                    return cycle_nodes

        return []

    # ------------------------------------------------------------------
    # 4. compute_critical_path — longest path in DAG
    # ------------------------------------------------------------------

    def compute_critical_path(self) -> list[str]:
        """Find the critical path (longest path) through the DAG.

        Uses topological order + dynamic programming.  Returns ordered list
        of subtask IDs on the critical path.
        """
        if not self.subtasks:
            self.critical_path = []
            return []

        # Kahn's algorithm to produce a topological order
        in_degree: dict[str, int] = {sid: 0 for sid in self.subtasks}
        for sid, deps in self.dependencies.items():
            for d in deps:
                if d in in_degree:
                    in_degree[d] = in_degree.get(d, 0)  # ensure key exists
            in_degree[sid] = len(deps)

        # Ensure every node appears
        for sid in self.subtasks:
            if sid not in in_degree:
                in_degree[sid] = 0

        queue = [sid for sid, deg in in_degree.items() if deg == 0]
        topo_order: list[str] = []

        adj = {sid: [] for sid in self.subtasks}
        for sid in self.subtasks:
            for dep in self.dependencies.get(sid, []):
                if dep in adj:
                    adj[dep].append(sid)

        while queue:
            node = queue.pop(0)
            topo_order.append(node)
            for neighbor in adj.get(node, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        # DP: longest distance from any start node
        dist: dict[str, int] = {sid: 0 for sid in self.subtasks}
        backptr: dict[str, Optional[str]] = {sid: None for sid in self.subtasks}

        for node in topo_order:
            for neighbor in adj.get(node, []):
                if dist[neighbor] < dist[node] + 1:
                    dist[neighbor] = dist[node] + 1
                    backptr[neighbor] = node

        # Find the node with maximum distance
        if not dist:
            self.critical_path = []
            return []

        end_node = max(dist, key=lambda k: dist[k])

        # Backtrack to reconstruct path
        path: list[str] = []
        cur: Optional[str] = end_node
        while cur is not None:
            path.append(cur)
            cur = backptr.get(cur)
        path.reverse()

        self.critical_path = path
        return path

    # ------------------------------------------------------------------
    # 5. compute_topological_levels — BFS level assignment
    # ------------------------------------------------------------------

    def compute_topological_levels(self) -> dict[str, int]:
        """Assign topological levels via BFS.

        Level 0 = no dependencies.
        Level N = 1 + max(level of all dependencies).
        """
        if not self.subtasks:
            self.topological_levels = {}
            return {}

        levels: dict[str, int] = {}

        # Start with root nodes (no dependencies)
        queue: list[str] = []
        for sid in self.subtasks:
            deps = self.dependencies.get(sid, [])
            if not deps:
                levels[sid] = 0
                queue.append(sid)

        # Ensure all nodes are eventually visited (even disconnected ones)
        remaining = set(self.subtasks) - set(levels)
        if remaining:
            # Nodes with dependencies not in graph get level 0 as fallback
            for sid in remaining:
                deps = self.dependencies.get(sid, [])
                external_deps = [d for d in deps if d not in self.subtasks]
                if external_deps and not any(d in self.subtasks for d in deps):
                    levels[sid] = 0
                    queue.append(sid)

        # Rebuild remaining set
        remaining = set(self.subtasks) - set(levels)
        # Add any still-unleveled node with all deps already leveled
        changed = True
        while remaining and changed:
            changed = False
            for sid in list(remaining):
                deps = self.dependencies.get(sid, [])
                if all(d in levels for d in deps):
                    if deps:
                        levels[sid] = 1 + max(levels[d] for d in deps)
                    else:
                        levels[sid] = 0
                    queue.append(sid)
                    remaining.remove(sid)
                    changed = True

        # Fallback: anything still remaining gets level 0
        for sid in remaining:
            levels[sid] = 0

        self.topological_levels = levels
        return levels

    # ------------------------------------------------------------------
    # 6. get_ready_subtasks
    # ------------------------------------------------------------------

    def get_ready_subtasks(self, approved_ids: set[str]) -> list[SubTask]:
        """Return subtasks whose dependencies are ALL in *approved_ids*
        and whose status is ``PENDING``."""
        ready: list[SubTask] = []
        for sid, st in self.subtasks.items():
            if st.status != "PENDING":
                continue
            deps = self.dependencies.get(sid, [])
            if all(d in approved_ids for d in deps):
                ready.append(st)
        return ready

    # ------------------------------------------------------------------
    # 7. get_subtasks_at_level
    # ------------------------------------------------------------------

    def get_subtasks_at_level(self, level: int) -> list[SubTask]:
        """Return subtasks assigned to the given topological level."""
        if not self.topological_levels:
            self.compute_topological_levels()
        return [
            self.subtasks[sid]
            for sid, lvl in self.topological_levels.items()
            if lvl == level and sid in self.subtasks
        ]

    # ------------------------------------------------------------------
    # 8. get_terminal_subtasks
    # ------------------------------------------------------------------

    def get_terminal_subtasks(self) -> list[SubTask]:
        """Return leaf nodes — subtasks with no dependents."""
        terminals: list[SubTask] = []
        for sid, st in self.subtasks.items():
            deps = self.dependents.get(sid, [])
            if not deps:
                terminals.append(st)
        return terminals

    # ------------------------------------------------------------------
    # 9. to_dict
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise the entire graph to a dictionary."""
        return {
            "subtasks": {sid: st.to_dict() for sid, st in self.subtasks.items()},
            "dependencies": {k: list(v) for k, v in self.dependencies.items()},
            "dependents": {k: list(v) for k, v in self.dependents.items()},
            "critical_path": list(self.critical_path),
            "topological_levels": dict(self.topological_levels),
        }

    # ------------------------------------------------------------------
    # 10. from_dict
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "TaskGraph":
        """Deserialise a TaskGraph from a dictionary."""
        graph = cls()

        subtasks_data = data.get("subtasks", {})
        for sid, st_dict in subtasks_data.items():
            try:
                subtask = SubTask.from_dict(st_dict)
                graph.subtasks[sid] = subtask
            except Exception as exc:
                logger.warning("Skipping deserialisation of subtask %s: %s", sid, exc)

        deps_data = data.get("dependencies", {})
        for k, v in deps_data.items():
            graph.dependencies[k] = list(v)

        dents_data = data.get("dependents", {})
        for k, v in dents_data.items():
            graph.dependents[k] = list(v)

        graph.critical_path = list(data.get("critical_path", []))
        graph.topological_levels = dict(data.get("topological_levels", {}))

        return graph


# ---------------------------------------------------------------------------
# TaskDecomposer
# ---------------------------------------------------------------------------

class TaskDecomposer:
    """Decomposes a high-level task into a DAG of atomic SubTasks.

    Uses an LLM (via KimiAPIPool) to generate the decomposition, then
    validates the resulting graph (cycle detection, critical path, etc.).
    """

    def __init__(self, api_pool: Any, state_store: Any) -> None:
        """Parameters
        ----------
        api_pool : KimiAPIPool
            Pool of API keys for LLM requests.
        state_store : SQLiteStateStore
            Persistent store for subtask state.
        """
        self.api_pool = api_pool
        self.state_store = state_store

    # ------------------------------------------------------------------
    # 1. decompose
    # ------------------------------------------------------------------

    async def decompose(
        self,
        task_title: str,
        task_description: str,
        session_id: str,
        max_subtasks: int = 300,
        template_hint: str = None,
    ) -> TaskGraph:
        """Decompose a high-level task into a TaskGraph.

        Flow
        ----
        1. Build decomposition prompt.
        2. Send to LLM via api_pool (KEY_1).
        3. Parse response into SubTasks.
        4. Build TaskGraph with dependency edges.
        5. Detect cycles → retry once with explicit NO-CYCLES instruction.
        6. Compute critical path & topological levels.
        7. Persist subtasks to state_store.
        8. Return TaskGraph.
        """
        logger.info(
            "[decompose] session=%s title='%s' max_subtasks=%d",
            session_id, task_title, max_subtasks,
        )

        parent_task_id = f"TASK-{session_id}"
        prompt = self._build_decomposition_prompt(task_title, task_description, max_subtasks)

        if template_hint:
            prompt += f"\n\nAdditional hint: {template_hint}\n"

        # ---- Attempt 1 ---------------------------------------------------
        response_text = await self._call_llm(prompt)
        graph = self._parse_decomposition_response(response_text, session_id)

        # Enforce parent_task_id on every subtask
        for st in graph.subtasks.values():
            st.parent_task_id = parent_task_id

        # ---- Cycle detection ---------------------------------------------
        cycle = graph.detect_cycles()
        if cycle:
            logger.error(
                "[decompose] Cycle detected on attempt 1: %s — retrying with NO-CYCLES instruction",
                cycle,
            )
            # ---- Attempt 2 (explicit NO CYCLES) --------------------------
            no_cycle_prompt = prompt + (
                "\n\n[CRITICAL] The previous decomposition contained a dependency cycle. "
                "Ensure this decomposition is a DAG — NO CYCLES. "
                "Use topological ordering and only forward-pointing dependencies."
            )
            response_text = await self._call_llm(no_cycle_prompt)
            graph = self._parse_decomposition_response(response_text, session_id)
            for st in graph.subtasks.values():
                st.parent_task_id = parent_task_id

            cycle = graph.detect_cycles()
            if cycle:
                logger.error(
                    "[decompose] Cycle STILL present after retry: %s — returning graph anyway; "
                    "scheduler may need to break cycles manually.",
                    cycle,
                )

        # ---- Compute graph analytics -------------------------------------
        graph.compute_critical_path()
        graph.compute_topological_levels()

        # ---- Persist -----------------------------------------------------
        await self._persist_graph(graph, session_id)

        logger.info(
            "[decompose] Completed session=%s subtasks=%d critical_path_len=%d",
            session_id, len(graph.subtasks), len(graph.critical_path),
        )
        return graph

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    async def _call_llm(self, prompt: str) -> str:
        """Send prompt to LLM via api_pool using KEY_1."""
        # KEY_1 is documented as the most reliable key
        messages = [
            {"role": "system", "content": "You are a software task decomposition engine."},
            {"role": "user", "content": prompt},
        ]
        # api_pool.call expects: key_id, messages, temperature, etc.
        response = await self.api_pool.call(
            key_id="KEY_1",
            messages=messages,
            temperature=0.2,
            max_tokens=8000,
        )
        # api_pool.call returns a dict with "content" or the raw string
        if isinstance(response, dict):
            return response.get("content", "") or response.get("text", "")
        return str(response)

    async def _persist_graph(self, graph: TaskGraph, session_id: str) -> None:
        """Persist every SubTask in the graph to the state store."""
        for st in graph.subtasks.values():
            try:
                await self.state_store.save_subtask(session_id, st.to_dict())
            except Exception as exc:
                logger.warning("Failed to persist subtask %s: %s", st.id, exc)

    # ------------------------------------------------------------------
    # 2. _build_decomposition_prompt
    # ------------------------------------------------------------------

    def _build_decomposition_prompt(
        self, title: str, description: str, max_subtasks: int
    ) -> str:
        """Build a detailed prompt requesting structured subtask decomposition."""

        prompt = f"""You are an expert software task decomposer. Your job is to break down a high-level task into a directed acyclic graph (DAG) of atomic subtasks.

# TASK TITLE
{title}

# TASK DESCRIPTION
{description}

# DECOMPOSITION RULES
1. **Atomic Responsibility** — Each subtask must do ONE thing. A single subtask should not mix code generation with analysis, or testing with documentation. Keep concerns separated.
2. **Dependency Declaration** — Every subtask must explicitly list which other subtask IDs it depends on. Dependencies must form a DAG — no cycles allowed. Dependencies only flow forward (earlier → later).
3. **Acceptance Criteria** — Provide 2–5 measurable, pass/fail acceptance criteria for each subtask. They must be unambiguous.
4. **Priority Assignment** — Assign priority per subtask:
   - CRITICAL: Blocks the entire project if it fails
   - HIGH: Important functionality, many downstream dependents
   - NORMAL: Standard work
   - LOW: Nice-to-have, can be deferred
5. **NO CYCLES** — The dependency graph MUST be acyclic. Use topological ordering. If subtask B depends on A, nothing in A's transitive closure may depend on B.
6. **Output Types** — Each subtask produces one of: CODE, TEXT, ANALYSIS, COMMAND, STRUCTURED_DATA.
7. **Max Subtasks** — Produce at most {max_subtasks} subtasks. If the task is small, produce fewer. Quality over quantity.
8. **ID Format** — Use sequential IDs like "ST-001", "ST-002", etc. These will be rewritten to UUIDs internally.

# RESPONSE FORMAT
Return ONLY a JSON object with this exact structure (no markdown fences, no extra text):

{{
  "subtasks": [
    {{
      "title": "Implement user authentication middleware",
      "description": "Create Express middleware that validates JWT tokens from the Authorization header...",
      "acceptance_criteria": [
        "Middleware rejects requests without Authorization header with 401",
        "Middleware accepts valid JWT and sets req.user",
        "Middleware returns 403 for expired tokens"
      ],
      "input_dependencies": ["ST-001"],
      "output_type": "CODE",
      "output_schema": null,
      "priority": "HIGH",
      "template_name": "BLANK_CODE_GENERATION",
      "estimated_tokens": 2000
    }}
  ]
}}

# IMPORTANT
- Return ONLY valid JSON. Do not include markdown code fences.
- Ensure the dependency graph is acyclic.
- Every subtask must have at least one acceptance criterion.
- Use short, action-verb titles (≤ 80 characters).
- The "description" field should contain enough context for an LLM agent to complete the task without additional research.
"""
        return prompt

    # ------------------------------------------------------------------
    # 3. _parse_decomposition_response
    # ------------------------------------------------------------------

    def _parse_decomposition_response(self, response: str, session_id: str) -> TaskGraph:
        """Parse LLM response (JSON or markdown ```json block) into a TaskGraph."""
        graph = TaskGraph()

        if not response or not response.strip():
            logger.error("[parse] Empty response from LLM")
            return graph

        # ---- Extract JSON from markdown fences if present --------------
        json_str = response.strip()

        # Try ```json ... ``` blocks
        fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", json_str, re.DOTALL)
        if fence_match:
            json_str = fence_match.group(1)
        else:
            # Try finding the first { and last }
            start = json_str.find("{")
            end = json_str.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_str = json_str[start:end + 1]

        # ---- Parse JSON --------------------------------------------------
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as exc:
            logger.error("[parse] JSON decode error: %s — raw snippet: %s", exc, json_str[:500])
            return graph

        subtasks_list = data.get("subtasks", [])
        if not subtasks_list:
            logger.warning("[parse] No subtasks found in decomposition response")
            return graph

        # ---- Build ID mapping (sequential → UUID) -----------------------
        id_mapping: dict[str, str] = {}
        for block in subtasks_list:
            orig_id = block.get("id") or block.get("subtask_id") or block.get("sequence", "")
            if orig_id:
                id_mapping[orig_id] = self._generate_uuid()

        # ---- First pass: create SubTasks --------------------------------
        for block in subtasks_list:
            try:
                subtask = self._parse_subtask_block(block, session_id)
                # Remap ID if it was a sequential one
                orig_id = block.get("id") or block.get("subtask_id") or block.get("sequence", "")
                if orig_id and orig_id in id_mapping:
                    subtask.id = id_mapping[orig_id]
                graph.add_subtask(subtask)
            except Exception as exc:
                logger.warning("[parse] Failed to parse subtask block: %s — block=%s", exc, block)

        # ---- Second pass: add dependency edges --------------------------
        for block in subtasks_list:
            orig_id = block.get("id") or block.get("subtask_id") or block.get("sequence", "")
            if not orig_id or orig_id not in id_mapping:
                continue

            new_id = id_mapping[orig_id]
            raw_deps = block.get("input_dependencies", block.get("dependencies", []))
            for dep in raw_deps:
                dep_str = str(dep)
                # Map dependency ID
                mapped_dep = id_mapping.get(dep_str, dep_str)
                if mapped_dep in graph.subtasks:
                    try:
                        graph.add_dependency(new_id, mapped_dep)
                    except ValueError as exc:
                        logger.warning("[parse] Bad dependency edge %s → %s: %s", new_id, mapped_dep, exc)
                else:
                    # Dependency refers to a non-existent subtask — log and skip
                    logger.debug(
                        "[parse] Dependency %s of %s not yet in graph; skipping edge.",
                        mapped_dep, new_id,
                    )

        logger.info(
            "[parse] Parsed %d subtasks into graph", len(graph.subtasks)
        )
        return graph

    # ------------------------------------------------------------------
    # 4. _parse_subtask_block
    # ------------------------------------------------------------------

    def _parse_subtask_block(self, block: dict, session_id: str) -> SubTask:
        """Convert a parsed dict block into a SubTask object."""
        # Generate a fresh UUID for this subtask
        st_id = self._generate_uuid()

        # Title
        title = block.get("title", "Untitled subtask")
        if len(title) > 80:
            title = title[:77] + "..."

        # Description
        description = block.get("description", block.get("desc", ""))

        # Acceptance criteria
        ac_raw = block.get("acceptance_criteria", block.get("acceptance", []))
        if isinstance(ac_raw, str):
            acceptance_criteria = [ac_raw]
        else:
            acceptance_criteria = list(ac_raw) if ac_raw else []

        # Input dependencies (will be remapped by caller)
        raw_deps = block.get("input_dependencies", block.get("dependencies", []))
        input_dependencies = [str(d) for d in raw_deps] if raw_deps else []

        # Output type
        ot_raw = block.get("output_type", "CODE")
        try:
            output_type = OutputType(str(ot_raw).upper())
        except ValueError:
            output_type = OutputType.CODE

        # Output schema (only for STRUCTURED_DATA)
        output_schema = block.get("output_schema")
        if output_schema and not isinstance(output_schema, dict):
            output_schema = None

        # Priority
        pr_raw = block.get("priority", "NORMAL")
        try:
            priority = Priority(str(pr_raw).upper())
        except ValueError:
            priority = Priority.NORMAL

        # Numeric fields with safe defaults
        max_retries = self._safe_int(block.get("max_retries"), 3)
        timeout_seconds = self._safe_int(block.get("timeout_seconds"), 120)
        estimated_tokens = self._safe_int(block.get("estimated_tokens"), 1000)

        # Template name
        template_name = block.get("template_name", "BLANK_CODE_GENERATION")

        return SubTask(
            id=st_id,
            parent_task_id=f"TASK-{session_id}",
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            input_dependencies=input_dependencies,
            output_type=output_type,
            output_schema=output_schema,
            priority=priority,
            max_retries=max_retries,
            timeout_seconds=timeout_seconds,
            template_name=template_name,
            created_at=time.time(),
            estimated_tokens=estimated_tokens,
        )

    # ------------------------------------------------------------------
    # 5. _generate_uuid
    # ------------------------------------------------------------------

    def _generate_uuid(self) -> str:
        """Return a short deterministic-feeling UUID: ST- + 12 hex chars."""
        return "ST-" + uuid.uuid4().hex[:12]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        """Coerce *value* to int, falling back to *default*."""
        if value is None:
            return default
        try:
            return int(value)
        except (ValueError, TypeError):
            return default
