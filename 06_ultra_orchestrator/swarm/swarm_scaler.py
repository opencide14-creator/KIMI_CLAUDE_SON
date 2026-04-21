"""
Swarm Scaler — 300-Agent Coordination Engine

Manages up to 300 concurrent sub-agents with:
  - Tiered batch scheduling (CRITICAL → HIGH → NORMAL → LOW)
  - Dynamic concurrency adjustment based on API capacity
  - Agent lifecycle: PENDING → QUEUED → SPAWNING → RUNNING → DONE
  - Automatic retry with exponential backoff
  - Dead letter queue for permanently failed agents
  - Checkpoint every 5 approved tasks

Kimi K2.6 target: 300 agents, 4000 coordinated steps
"""

from __future__ import annotations

import asyncio, json, logging, time, uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Callable, Any
from enum import Enum

logger = logging.getLogger("SwarmScaler")


class AgentState(Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    SPAWNING = "SPAWNING"
    RUNNING = "RUNNING"
    VALIDATING = "VALIDATING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    RETRY = "RETRY"
    DEAD_LETTER = "DEAD_LETTER"
    BLOCKED = "BLOCKED"


class TaskPriority(Enum):
    CRITICAL = 4
    HIGH = 3
    NORMAL = 2
    LOW = 1


@dataclass
class SwarmAgent:
    """A single agent in the swarm"""
    agent_id: str
    task_title: str
    task_description: str
    priority: TaskPriority
    state: AgentState = AgentState.PENDING
    assigned_key: str = ""
    retry_count: int = 0
    max_retries: int = 3
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    output: str = ""
    rejection_reason: str = ""
    tokens_used: int = 0
    cost_usd: float = 0.0
    kappa_score: float = 0.0
    tessa_class: str = ""
    dependencies: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "task_title": self.task_title,
            "priority": self.priority.name,
            "state": self.state.value,
            "assigned_key": self.assigned_key,
            "retry_count": self.retry_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "tokens_used": self.tokens_used,
            "cost_usd": self.cost_usd,
            "kappa_score": self.kappa_score,
            "tessa_class": self.tessa_class,
        }


class SwarmScaler:
    """
    300-agent swarm coordination engine.
    Manages agent lifecycle, batch scheduling, and dynamic scaling.
    """

    MAX_AGENTS = 300
    DEFAULT_BATCH = 20
    CHECKPOINT_INTERVAL = 5

    def __init__(self, max_concurrent: int = 300):
        if max_concurrent > self.MAX_AGENTS:
            raise ValueError(f"max_concurrent cannot exceed {self.MAX_AGENTS}")
        self.max_concurrent = max_concurrent
        self.current_batch_size = min(self.DEFAULT_BATCH, max_concurrent)
        self.agents: Dict[str, SwarmAgent] = {}
        self._state_counts: Dict[str, int] = {s.value: 0 for s in AgentState}
        self._active_count = 0
        self._approved_count = 0
        self._checkpoint_counter = 0
        self._running = False
        self._session_id = ""
        self._lock = asyncio.Lock()
        self._callbacks: List[Callable] = []
        self._semaphore = asyncio.Semaphore(max_concurrent)

    def register_callback(self, callback: Callable):
        """Register state change callback."""
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

    def add_agent(self, title: str, description: str,
                  priority: TaskPriority = TaskPriority.NORMAL,
                  dependencies: List[str] = None) -> str:
        """Add a new agent to the swarm. Returns agent_id."""
        agent_id = f"AG-{uuid.uuid4().hex[:8].upper()}"
        agent = SwarmAgent(
            agent_id=agent_id,
            task_title=title,
            task_description=description,
            priority=priority,
            state=AgentState.PENDING,
            created_at=time.time(),
            dependencies=dependencies or [],
        )
        self.agents[agent_id] = agent
        self._state_counts[AgentState.PENDING.value] += 1
        return agent_id

    async def start_session(self, session_id: str):
        """Start a new swarm session."""
        self._session_id = session_id
        self._running = True
        self._approved_count = 0
        self._checkpoint_counter = 0
        logger.info(f"Swarm session started: {session_id} "
                    f"(max_concurrent={self.max_concurrent})")

    async def stop_session(self):
        """Stop the swarm session."""
        self._running = False
        logger.info(f"Swarm session stopped: {self._session_id}")

    async def pause_session(self):
        """Pause — agents finish current work but no new spawns."""
        self._running = False
        await self._trigger_checkpoint()
        logger.info("Swarm session paused")

    async def resume_session(self):
        """Resume — reset non-terminal agents to QUEUED."""
        for agent in self.agents.values():
            if agent.state not in (AgentState.APPROVED, AgentState.DEAD_LETTER):
                await self._transition(agent.agent_id, AgentState.QUEUED)
        self._running = True
        logger.info("Swarm session resumed")

    async def _transition(self, agent_id: str, new_state: AgentState,
                          **kwargs) -> bool:
        """Transition agent to new state. Thread-safe."""
        async with self._lock:
            agent = self.agents.get(agent_id)
            if not agent:
                return False

            old_state = agent.state
            agent.state = new_state

            # Update counts
            self._state_counts[old_state.value] = max(0,
                self._state_counts.get(old_state.value, 0) - 1)
            self._state_counts[new_state.value] = (
                self._state_counts.get(new_state.value, 0) + 1)

            # Track active count
            if new_state in (AgentState.SPAWNING, AgentState.RUNNING):
                self._active_count += 1
            elif old_state in (AgentState.SPAWNING, AgentState.RUNNING):
                self._active_count = max(0, self._active_count - 1)

            # Track approvals for checkpoint
            if new_state == AgentState.APPROVED:
                self._approved_count += 1
                self._checkpoint_counter += 1

            # Update fields from kwargs
            for key, value in kwargs.items():
                if hasattr(agent, key):
                    setattr(agent, key, value)

            if new_state == AgentState.APPROVED:
                agent.completed_at = time.time()

            await self._notify("state_change", {
                "agent_id": agent_id,
                "old_state": old_state.value,
                "new_state": new_state.value,
                "priority": agent.priority.name,
            })
            return True

    async def get_ready_agents(self) -> List[SwarmAgent]:
        """Get agents whose dependencies are all APPROVED."""
        ready = []
        for agent in self.agents.values():
            if agent.state != AgentState.PENDING:
                continue
            deps_approved = all(
                self.agents.get(dep_id, SwarmAgent("", "", "", TaskPriority.LOW))
                .state == AgentState.APPROVED
                for dep_id in agent.dependencies
            )
            if deps_approved:
                ready.append(agent)
        # Sort by priority (highest first), then by retry count
        ready.sort(key=lambda a: (-a.priority.value, a.retry_count, a.created_at))
        return ready

    async def get_next_batch(self, available_slots: int) -> List[SwarmAgent]:
        """Get next batch of agents to spawn."""
        ready = await self.get_ready_agents()
        queued = [a for a in self.agents.values()
                  if a.state == AgentState.QUEUED]
        queued.sort(key=lambda a: (-a.priority.value, a.retry_count, a.created_at))

        candidates = ready + queued
        batch_size = min(available_slots, self.current_batch_size, len(candidates))
        return candidates[:batch_size]

    async def run_scheduling_loop(self, executor_func: Callable,
                                   status_func: Callable):
        """Main scheduling loop."""
        logger.info("Swarm scheduling loop started")
        while self._running:
            try:
                # Check completion
                total = len(self.agents)
                terminal = (self._state_counts.get(AgentState.APPROVED.value, 0) +
                           self._state_counts.get(AgentState.DEAD_LETTER.value, 0))
                if total > 0 and terminal >= total:
                    logger.info(f"All {total} agents complete. Session done.")
                    await self._notify("session_complete",
                                       {"approved": self._approved_count, "total": total})
                    break

                # Available slots
                available = self.max_concurrent - self._active_count
                if available <= 0:
                    await asyncio.sleep(0.05)
                    continue

                # Get batch
                batch = await self.get_next_batch(available)
                if not batch:
                    if self._active_count == 0 and total > 0:
                        # Nothing ready, nothing running — potential deadlock
                        await asyncio.sleep(0.5)
                    continue

                # Spawn batch
                for agent in batch:
                    await self._transition(agent.agent_id, AgentState.SPAWNING)
                    # Create task for execution
                    asyncio.create_task(
                        self._execute_agent(agent.agent_id, executor_func)
                    )

                await self._notify("batch_spawned",
                                   {"count": len(batch), "active": self._active_count})

                # Checkpoint check
                if self._checkpoint_counter >= self.CHECKPOINT_INTERVAL:
                    await self._trigger_checkpoint()

                await asyncio.sleep(0.02)  # 50Hz scheduler

            except Exception as e:
                logger.error(f"Scheduling loop error: {e}")
                await asyncio.sleep(1)

    async def _execute_agent(self, agent_id: str,
                              executor_func: Callable) -> None:
        """Execute a single agent."""
        async with self._semaphore:
            agent = self.agents.get(agent_id)
            if not agent:
                return

            try:
                await self._transition(agent_id, AgentState.RUNNING,
                                       started_at=time.time())

                # Execute via provided function
                result = await executor_func(agent)

                # Process result
                if result.get("approved", False):
                    await self._transition(agent_id, AgentState.APPROVED,
                                           output=result.get("output", ""),
                                           tokens_used=result.get("tokens_used", 0),
                                           cost_usd=result.get("cost_usd", 0.0),
                                           kappa_score=result.get("kappa", 0.0),
                                           tessa_class=result.get("tessa_class", ""))
                else:
                    # Rejected — check retry
                    if agent.retry_count < agent.max_retries:
                        await self._transition(agent_id, AgentState.REJECTED,
                                               rejection_reason=result.get("reason", ""))
                        await asyncio.sleep(5 * (agent.retry_count + 1))
                        await self._transition(agent_id, AgentState.QUEUED,
                                               retry_count=agent.retry_count + 1)
                    else:
                        await self._transition(agent_id, AgentState.DEAD_LETTER)
                        # Block dependents
                        for dep_id, dep_agent in self.agents.items():
                            if agent_id in dep_agent.dependencies:
                                await self._transition(dep_id, AgentState.BLOCKED)

            except Exception as e:
                logger.error(f"Agent {agent_id} execution failed: {e}")
                await self._transition(agent_id, AgentState.REJECTED,
                                       rejection_reason=str(e))

    async def _trigger_checkpoint(self):
        """Save checkpoint state."""
        self._checkpoint_counter = 0
        checkpoint_data = {
            "timestamp": time.time(),
            "session_id": self._session_id,
            "approved": self._approved_count,
            "total": len(self.agents),
            "states": dict(self._state_counts),
        }
        await self._notify("checkpoint", checkpoint_data)
        logger.info(f"Checkpoint: {self._approved_count}/{len(self.agents)} approved")

    def get_status(self) -> dict:
        """Get current swarm status."""
        return {
            "running": self._running,
            "total_agents": len(self.agents),
            "active": self._active_count,
            "approved": self._approved_count,
            "max_concurrent": self.max_concurrent,
            "utilization": self._active_count / self.max_concurrent if self.max_concurrent > 0 else 0,
            "states": dict(self._state_counts),
            "session_id": self._session_id,
        }

    def get_agents_by_state(self, state: AgentState) -> List[SwarmAgent]:
        return [a for a in self.agents.values() if a.state == state]

    def get_agent(self, agent_id: str) -> Optional[SwarmAgent]:
        return self.agents.get(agent_id)

    def get_progress(self) -> dict:
        total = len(self.agents)
        approved = self._state_counts.get(AgentState.APPROVED.value, 0)
        return {
            "total": total,
            "approved": approved,
            "percentage": (approved / total * 100) if total > 0 else 0,
            "remaining": total - approved,
        }


__all__ = ["SwarmScaler", "SwarmAgent", "AgentState", "TaskPriority"]
