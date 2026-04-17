"""
Orchestrator — top-level coordinator agent.

Responsibilities:
1. Receive the user's high-level goal
2. Decompose it into a TaskGraph via LLM (structured JSON output)
3. Assign tasks to appropriate agent roles
4. Execute the graph using TaskGraphExecutor (parallel where possible)
5. Aggregate results into a final answer
6. Decide when to use a P2P subswarm vs. single agent assignment

The orchestrator's plan (task graph) is exposed to the observability layer
before execution so users can inspect and optionally approve it.
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Optional

from configs.schema import AgentSpec, TopologySpec
from coordination.bus import MessageBus
from coordination.subswarm import SubswarmCoordinator
from coordination.task_graph import TaskGraph, TaskGraphExecutor
from core.agent import Agent
from core.exceptions import SwarmError
from core.task import Task, TaskConstraints, TaskResult, TokenUsage
from memory.longterm import LocalChromaMemory
from memory.scratchpad import Scratchpad
from observability.cost import CostLedger
from observability.logging import get_logger
from providers.base import LLMProvider
from tools.base import ToolHandler

log = get_logger("orchestrator")

_DECOMPOSE_SYSTEM = """\
You are a task decomposition engine. Given a high-level goal, break it into
concrete subtasks that can be assigned to specialist agents.

Available agent roles: {roles}

Output ONLY valid JSON in this exact structure (no markdown, no explanation):
{{
  "tasks": [
    {{
      "id": "t1",
      "description": "...",
      "agent_role": "...",
      "depends_on": []
    }}
  ]
}}

Rules:
- Use only agent roles from the list above
- depends_on contains task IDs that must complete before this one starts
- If one agent can handle the full goal, emit a single task
- Keep it focused: prefer 1-4 tasks unless the goal clearly requires more
"""


class SwarmRuntime:
    """
    The main entry point that wires up all components and runs a goal end-to-end.

    Instantiate once per swarm run, then call `run(goal)`.
    """

    def __init__(
        self,
        topology: TopologySpec,
        provider: LLMProvider,
        tool_handlers: dict[str, ToolHandler],
        agent_specs: dict[str, AgentSpec],
        bus: MessageBus,
        longterm_memory: Optional[LocalChromaMemory] = None,
        ledger: Optional[CostLedger] = None,
        trace_id: Optional[str] = None,
    ) -> None:
        self.topology = topology
        self._provider = provider
        self._tools = tool_handlers
        self._agent_specs = agent_specs
        self._bus = bus
        self._longterm = longterm_memory
        self._ledger = ledger or CostLedger()
        self.trace_id = trace_id or str(uuid.uuid4())

        # Wire runtime-injectable tools
        self._wire_tools()

    def _wire_tools(self) -> None:
        """Inject runtime dependencies into tools that need them."""
        import tools.memory_store.handler as ms
        import tools.memory_retrieve.handler as mr
        import tools.self_reflect.handler as sr
        import tools.send_message.handler as sm_h
        import tools.spawn_agent.handler as sa

        if self._longterm:
            ms.set_memory(self._longterm)
            mr.set_memory(self._longterm)
        sr.set_provider(self._provider, self.topology.agents[0].model_override
                        if self.topology.agents else "llama-3.3-70b-versatile")
        sa.set_factory(self._spawn_agent_for_goal)

    def _make_agent(self, role: str, agent_id: Optional[str] = None) -> Agent:
        spec = self._agent_specs.get(role)
        if spec is None:
            # Fallback: create a generic spec
            from configs.schema import AgentSpec as AS
            spec = AS(
                name=role, role=role,
                system_prompt=f"You are a {role} agent. Complete the assigned task thoroughly.",
                model=self.topology.agents[0].model_override if self.topology.agents
                      else "llama-3.3-70b-versatile",
            )

        # Apply per-slot overrides from topology
        for slot in self.topology.agents:
            if slot.role == role:
                if slot.tools_override:
                    spec = spec.model_copy(update={"tools": slot.tools_override})
                if slot.model_override:
                    spec = spec.model_copy(update={"model": slot.model_override})

        tool_subset = {
            name: handler for name, handler in self._tools.items()
            if name in spec.tools
        }

        # Wire send_message tool with correct sender_id
        if "send_message" in self._tools:
            import tools.send_message.handler as sm_h
            aid = agent_id or str(uuid.uuid4())
            sm_h.set_bus(self._bus, aid)

        return Agent(
            spec=spec,
            provider=self._provider,
            tool_handlers=tool_subset,
            bus=self._bus,
            longterm_memory=self._longterm,
            ledger=self._ledger,
            agent_id=agent_id,
        )

    async def _spawn_agent_for_goal(self, role: str, goal: str) -> TaskResult:
        agent = self._make_agent(role)
        task = Task(goal=goal, constraints=TaskConstraints(
            budget=self.topology.budget.max_cost_usd,
            max_iterations=20,
        ))
        return await agent.run_task(task, trace_id=self.trace_id)

    async def run(self, goal: str) -> TaskResult:
        """Main entry point: run the swarm against a user goal."""
        log.info("swarm_run_start", goal=goal[:80], trace_id=self.trace_id[:8])

        root_task = Task(
            goal=goal,
            constraints=TaskConstraints(
                budget=self.topology.budget.max_cost_usd,
                timeout=300.0,
                max_iterations=30,
            ),
        )

        strategy = self.topology.coordination.strategy
        available_roles = [slot.role for slot in self.topology.agents]
        available_roles_str = ", ".join(available_roles) if available_roles else "general"

        # ── Single-agent fast path ────────────────────────────────────────────
        if len(available_roles) <= 1:
            role = available_roles[0] if available_roles else "general"
            agent = self._make_agent(role)
            return await agent.run_task(root_task, trace_id=self.trace_id)

        # ── Multi-agent: decompose and dispatch ───────────────────────────────
        try:
            task_graph = await self._decompose(goal, available_roles_str, root_task)
        except Exception as exc:
            log.warning("decompose_failed", error=str(exc), fallback="single_agent")
            role = available_roles[0]
            agent = self._make_agent(role)
            return await agent.run_task(root_task, trace_id=self.trace_id)

        log.info("task_graph_built", tasks=len(task_graph.all_tasks()))

        executor = TaskGraphExecutor(
            executor=self._execute_task,
            global_timeout=root_task.constraints.timeout,
            global_budget=root_task.constraints.budget,
        )
        results = await executor.run(task_graph)

        # Aggregate results
        successful = [r for r in results if r.success]
        combined_output = "\n\n".join(
            str(r.output) for r in successful if r.output
        )
        total_usage = TokenUsage()
        for r in results:
            total_usage = total_usage + r.token_usage

        return TaskResult(
            output=combined_output or "No output from swarm.",
            success=bool(successful),
            token_usage=total_usage,
            cost=self._ledger.total_cost,
            iterations=len(results),
            metadata={"task_graph": task_graph.summary()},
        )

    async def _decompose(
        self, goal: str, roles_str: str, root_task: Task
    ) -> TaskGraph:
        """Ask the LLM to decompose the goal into a task graph."""
        system = _DECOMPOSE_SYSTEM.format(roles=roles_str)
        result = await self._provider.complete(
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": goal},
            ],
            model=self._agent_specs.get("orchestrator", next(iter(self._agent_specs.values()))).model
            if self._agent_specs else "llama-3.3-70b-versatile",
            temperature=0.2,
        )

        if self._ledger:
            self._ledger.record("orchestrator", "llama-3.3-70b-versatile",
                                result.usage, root_task.id)

        raw = (result.content or "{}").strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        plan = json.loads(raw)
        tasks_data = plan.get("tasks", [])

        graph = TaskGraph()
        id_map: dict[str, str] = {}

        for t_data in tasks_data:
            task = root_task.fork(
                goal=t_data["description"],
                input_payload={"agent_role": t_data.get("agent_role", "general")},
            )
            id_map[t_data["id"]] = task.id
            graph.add_task(task)

        for t_data in tasks_data:
            for dep in t_data.get("depends_on", []):
                if dep in id_map and t_data["id"] in id_map:
                    try:
                        graph.add_dependency(id_map[t_data["id"]], id_map[dep])
                    except Exception:
                        pass  # ignore cycles silently

        return graph

    async def _execute_task(self, task: Task) -> TaskResult:
        """Execute one graph task by creating the appropriate agent."""
        role = task.input_payload.get("agent_role", "general")
        agent = self._make_agent(role)

        # Check if this task warrants a subswarm
        if self.topology.coordination.strategy in ("p2p", "hybrid"):
            # Simplified: only use subswarm if role is explicitly "subswarm"
            if role == "subswarm":
                return await self._run_subswarm(task)

        return await agent.run_task(task, trace_id=self.trace_id)

    async def _run_subswarm(self, task: Task) -> TaskResult:
        """Delegate to SubswarmCoordinator for collaborative tasks."""
        from coordination.subswarm import SubswarmCoordinator
        agents = [self._make_agent(slot.role) for slot in self.topology.agents[:3]]
        coordinator = SubswarmCoordinator(
            agents=agents,
            bus=self._bus,
            protocol=self.topology.coordination.consensus_protocol,
            max_rounds=self.topology.coordination.debate_max_rounds,
        )
        return await coordinator.run(task)
