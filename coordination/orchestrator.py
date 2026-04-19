"""
Orchestrator — top-level coordinator agent.

Responsibilities:
1. Receive the user's high-level goal
2. Decompose it into a TaskGraph via LLM (structured JSON output)
3. Assign tasks to appropriate agent roles
4. Execute the graph using TaskGraphExecutor (parallel where possible)
5. Aggregate results into a final answer
6. Decide when to use a P2P subswarm vs. single agent assignment

Lifecycle-aware mode (§20):
  When topology.coordination.strategy == "lifecycle", the orchestrator loads the
  declared lifecycle YAML and executes phases sequentially, validating artifact
  contracts at each boundary.  A new _run_lifecycle() method handles this path;
  the original _run() / hierarchical / p2p paths are untouched.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Literal, Optional

from configs.schema import AgentSpec, TopologySpec
from coordination.bus import MessageBus
from coordination.subswarm import SubswarmCoordinator
from coordination.task_graph import TaskGraph, TaskGraphExecutor
from coordination.task_queue import RedisStreamTaskQueue
from core.agent import Agent
from core.exceptions import SwarmError
from core.task import Task, TaskConstraints, TaskResult, TokenUsage
from memory.longterm import LocalChromaMemory
from memory.scratchpad import Scratchpad
from observability.cost import CostLedger
from observability.logutil import get_logger
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
        deployment_mode: str = "local",
        redis_url: str = "redis://localhost:6379",
        deploy: bool = True,
    ) -> None:
        self.topology = topology
        self._provider = provider
        self._tools = tool_handlers
        self._agent_specs = agent_specs
        self._bus = bus
        self._longterm = longterm_memory
        self._ledger = ledger or CostLedger()
        self.trace_id = trace_id or str(uuid.uuid4())
        self._deployment_mode = deployment_mode
        self._redis_url = redis_url
        self._deploy = deploy
        self._stop_after_phase: Optional[str] = None
        self._resume_from_phase: Optional[str] = None

        # Wire runtime-injectable tools
        self._wire_tools()

    def _wire_tools(self) -> None:
        """Inject runtime dependencies into tools that need them."""
        import tools.memory_store.handler as ms
        import tools.memory_retrieve.handler as mr
        import tools.self_reflect.handler as sr
        import tools.send_message.handler as sm_h
        import tools.spawn_agent.handler as sa
        import tools.contractor.handler as ct

        if self._longterm:
            ms.set_memory(self._longterm)
            mr.set_memory(self._longterm)
        # Resolve the best available model for self_reflect: prefer topology
        # slot override, then fall back to the first registered agent spec's
        # model, then a safe default.
        _reflect_model = (
            (self.topology.agents[0].model_override if self.topology.agents else None)
            or next((s.model for s in self._agent_specs.values() if s.model), None)
            or "gemini-2.5-flash"
        )
        sr.set_provider(self._provider, _reflect_model)
        sa.set_factory(self._spawn_agent_for_goal)
        ct.set_factory(self._spawn_agent_for_goal)

    def _make_agent(self, role: str, agent_id: Optional[str] = None) -> Agent:
        spec = self._agent_specs.get(role)
        if spec is None:
            # Fallback: create a generic spec — resolve a non-None model
            _fallback_model = (
                (self.topology.agents[0].model_override if self.topology.agents else None)
                or next((s.model for s in self._agent_specs.values() if s.model), None)
                or "gemini-2.5-flash"
            )
            from configs.schema import AgentSpec as AS
            spec = AS(
                name=role, role=role,
                system_prompt=f"You are a {role} agent. Complete the assigned task thoroughly.",
                model=_fallback_model,
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

    async def _spawn_agent_for_goal(
        self, role: str, goal: str, *, timeout: float = 300.0, max_iterations: int = 20
    ) -> TaskResult:
        agent = self._make_agent(role)
        task = Task(goal=goal, constraints=TaskConstraints(
            budget=self.topology.budget.max_cost_usd,
            timeout=timeout,
            max_iterations=max_iterations,
        ))
        return await agent.run_task(task, trace_id=self.trace_id)

    async def run(self, goal: str) -> TaskResult:
        """Main entry point: run the swarm against a user goal."""
        log.info("swarm_run_start", goal=goal[:80], trace_id=self.trace_id[:8])

        strategy = self.topology.coordination.strategy

        # ── Lifecycle-aware mode (§20) ────────────────────────────────────────
        if strategy == "lifecycle":
            return await self._run_lifecycle(goal)

        root_task = Task(
            goal=goal,
            constraints=TaskConstraints(
                budget=self.topology.budget.max_cost_usd,
                timeout=300.0,
                max_iterations=30,
            ),
        )

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

        tq: Optional[RedisStreamTaskQueue] = None
        dispatch_mode: Literal["in_process", "task_queue"] = "in_process"
        if self._deployment_mode in ("redis-workers", "kubernetes"):
            tq = RedisStreamTaskQueue(self._redis_url)
            dispatch_mode = "task_queue"

        executor = TaskGraphExecutor(
            executor=self._execute_task,
            global_timeout=root_task.constraints.timeout,
            global_budget=root_task.constraints.budget,
            task_queue=tq,
            dispatch_mode=dispatch_mode,
        )
        try:
            results = await executor.run(task_graph)
        finally:
            if tq is not None:
                await tq.aclose()

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

    # ── Lifecycle-aware orchestration (§20) ───────────────────────────────────

    async def _run_lifecycle(self, goal: str) -> TaskResult:
        """
        Execute the swarm in lifecycle-aware mode.

        Phases run sequentially; each phase:
          1. Validates required input artifacts are present in the registry.
          2. Dispatches the phase's default agents as a sub-DAG.
          3. Validates required output artifacts were produced.
          4. Emits PhaseBoundary trace spans.
          5. Optionally pauses for human approval.
        """
        lifecycle_name = self.topology.coordination.lifecycle or "software_delivery"
        lifecycle = _load_lifecycle(lifecycle_name)
        approval_gates = self.topology.coordination.approval_gates
        safety_mode = self.topology.safety.mode

        from memory.artifacts import get_artifact_registry
        artifact_reg = get_artifact_registry()

        from observability.tracing import get_tracer, Span
        tracer = get_tracer()

        all_results: list[TaskResult] = []
        total_usage = TokenUsage()
        phase_summaries: list[dict] = []

        # Wire artifact registry into the artifact tools
        _wire_artifact_tools(artifact_reg)

        phases = lifecycle.get("phases", [])
        budget_alloc = lifecycle.get("budget_allocation", {})
        total_budget = self.topology.budget.max_cost_usd

        # When deploy=False, strip deployment phases and relax iteration's
        # required inputs (FeedbackDigest won't be produced without post_launch).
        if not self._deploy:
            phases = [p for p in phases if not p.get("skip_without_deploy", False)]
            phases = [
                {**p, "required_input_artifact_types": []}
                if p["id"] == "iteration" else p
                for p in phases
            ]
            log.info("lifecycle_deploy_skipped",
                     skipped=["deployment", "post_launch"],
                     note="use --deploy to include deployment phases")

        log.info("lifecycle_start", lifecycle=lifecycle_name, phases=len(phases),
                 trace_id=self.trace_id[:8])

        current_phase_idx = 0
        while current_phase_idx < len(phases):
            phase = phases[current_phase_idx]
            phase_id = phase["id"]
            phase_name = phase["name"]
            phase_budget = (
                total_budget * budget_alloc.get(phase_id, 10) / 100
                if total_budget else None
            )

            # ── Skip phases before resume point ───────────────────────────────
            if self._resume_from_phase:
                if phase_id != self._resume_from_phase:
                    current_phase_idx += 1
                    continue
                self._resume_from_phase = None  # reached target, start running

            # ── Emit phase_start span ─────────────────────────────────────────
            with Span(tracer, f"phase.{phase_id}", "phase_start",
                      agent_id="chief_orchestrator") as span:
                span.set(phase_id=phase_id, phase_name=phase_name)

            log.info("phase_start", phase=phase_id, trace_id=self.trace_id[:8])

            # ── Validate input artifacts ──────────────────────────────────────
            required_inputs = phase.get("required_input_artifact_types", [])
            if required_inputs:
                missing = await _check_artifacts(artifact_reg, required_inputs)
                if missing:
                    log.error("phase_input_missing", phase=phase_id, missing=missing)
                    return TaskResult(
                        output=None,
                        success=False,
                        error=(
                            f"Phase '{phase_id}' missing required input artifacts: {missing}. "
                            "Previous phase may not have completed successfully."
                        ),
                        cost=self._ledger.total_cost,
                        token_usage=total_usage,
                        iterations=len(all_results),
                    )

            # ── Build and run per-phase sub-DAG ───────────────────────────────
            agent_roles = phase.get("default_agents", [])
            phase_goal = (
                f"[Phase: {phase_name}] {goal}\n\n"
                f"Produce the following artifacts: "
                f"{', '.join(phase.get('required_output_artifact_types', []))}"
            )

            phase_task = Task(
                goal=phase_goal,
                constraints=TaskConstraints(
                    budget=phase_budget,
                    timeout=600.0,
                    max_iterations=50,
                ),
                input_payload={"phase_id": phase_id, "lifecycle": lifecycle_name},
            )

            phase_results = await self._run_phase_agents(agent_roles, phase_task)
            all_results.extend(phase_results)
            for r in phase_results:
                total_usage = total_usage + r.token_usage

            phase_success = any(r.success for r in phase_results)

            # ── Validate output artifacts ─────────────────────────────────────
            required_outputs = phase.get("required_output_artifact_types", [])
            output_missing: list[str] = []
            if required_outputs and phase_success:
                output_missing = await _check_artifacts(artifact_reg, required_outputs)

            # ── Emit phase_end span ───────────────────────────────────────────
            with Span(tracer, f"phase.{phase_id}", "phase_end",
                      agent_id="chief_orchestrator") as span:
                span.set(
                    phase_id=phase_id,
                    success=phase_success,
                    output_artifacts_missing=output_missing,
                )

            phase_summaries.append({
                "phase_id": phase_id,
                "success": phase_success,
                "output_missing": output_missing,
                "result_count": len(phase_results),
            })

            # ── Handle phase failure ──────────────────────────────────────────
            if not phase_success or output_missing:
                on_failure = phase.get("on_failure", "abort")
                log.warning("phase_failed", phase=phase_id, on_failure=on_failure,
                            output_missing=output_missing)
                if on_failure == "abort":
                    return TaskResult(
                        output=None,
                        success=False,
                        error=f"Phase '{phase_id}' failed. Missing outputs: {output_missing}",
                        cost=self._ledger.total_cost,
                        token_usage=total_usage,
                        iterations=len(all_results),
                        metadata={"phases": phase_summaries},
                    )
                # reroute_to_iteration — jump to the iteration phase
                iteration_idx = next(
                    (i for i, p in enumerate(phases) if p["id"] == "iteration"), None
                )
                if iteration_idx is not None:
                    current_phase_idx = iteration_idx
                    continue

            # ── Human approval gate ───────────────────────────────────────────
            has_gate = phase.get("human_approval_gate", False)
            if has_gate and approval_gates and safety_mode == "interactive":
                approved = await _human_phase_gate(phase_id, phase_name, self.trace_id)
                if not approved:
                    with Span(tracer, f"phase.{phase_id}", "phase_gate_rejected",
                              agent_id="chief_orchestrator") as span:
                        span.set(phase_id=phase_id)
                    return TaskResult(
                        output=None,
                        success=False,
                        error=f"Phase '{phase_id}' rejected by human at approval gate.",
                        cost=self._ledger.total_cost,
                        token_usage=total_usage,
                        iterations=len(all_results),
                        metadata={"phases": phase_summaries},
                    )
                with Span(tracer, f"phase.{phase_id}", "phase_gate_approved",
                          agent_id="chief_orchestrator") as span:
                    span.set(phase_id=phase_id)

            # ── Pause after phase if requested (feedback loop) ────────────────
            if self._stop_after_phase and phase_id == self._stop_after_phase:
                return TaskResult(
                    output=f"Paused after '{phase_id}' phase. Ready for feedback.",
                    success=True,
                    cost=self._ledger.total_cost,
                    token_usage=total_usage,
                    iterations=len(all_results),
                    metadata={
                        "lifecycle": lifecycle_name,
                        "phases": phase_summaries,
                        "paused_after": phase_id,
                    },
                )

            current_phase_idx += 1

        # ── Final aggregation ─────────────────────────────────────────────────
        successful = [r for r in all_results if r.success]
        combined_output = "\n\n".join(str(r.output) for r in successful if r.output)

        return TaskResult(
            output=combined_output or "Lifecycle completed.",
            success=bool(successful),
            token_usage=total_usage,
            cost=self._ledger.total_cost,
            iterations=len(all_results),
            metadata={"lifecycle": lifecycle_name, "phases": phase_summaries},
        )

    async def _run_phase_agents(self, agent_roles: list[str], phase_task: Task) -> list[TaskResult]:
        """Run each agent role in the phase sequentially (simple ordered dispatch)."""
        import asyncio
        results: list[TaskResult] = []
        for role in agent_roles:
            # Skip roles not registered in the topology (graceful degradation)
            registered = [slot.role for slot in self.topology.agents]
            if role not in registered:
                log.debug("phase_agent_skipped", role=role, reason="not_in_topology")
                continue
            try:
                result = await self._spawn_agent_for_goal(
                    role, phase_task.goal,
                    timeout=phase_task.constraints.timeout,
                    max_iterations=phase_task.constraints.max_iterations,
                )
                results.append(result)
            except Exception as exc:
                log.error("phase_agent_error", role=role, error=str(exc))
                results.append(TaskResult(output=None, success=False, error=str(exc)))
        return results

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
            if self._agent_specs else "gemini-2.5-flash",
            temperature=0.2,
        )

        if self._ledger:
            self._ledger.record("orchestrator", "gemini-2.5-flash",
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


# ── Lifecycle helpers (module-level, not on the runtime) ──────────────────────

def _load_lifecycle(name: str) -> dict:
    """Load a lifecycle YAML from configs/lifecycles/<name>.yaml."""
    import yaml

    search_paths = [
        Path(f"configs/lifecycles/{name}.yaml"),
        Path(f"./configs/lifecycles/{name}.yaml"),
        Path(__file__).parent.parent / "configs" / "lifecycles" / f"{name}.yaml",
    ]
    for p in search_paths:
        if p.exists():
            with p.open(encoding="utf-8") as fh:
                return yaml.safe_load(fh)
    raise FileNotFoundError(
        f"Lifecycle '{name}' not found. Searched: {[str(p) for p in search_paths]}"
    )


async def _check_artifacts(registry: Any, required_types: list[str]) -> list[str]:
    """Return list of artifact types that are missing (no approved instance)."""
    missing = []
    for art_type in required_types:
        result = await registry.get_latest_by_type(art_type, status="approved")
        # Also accept draft artifacts so partially-completed phases can progress
        if result is None:
            result = await registry.get_latest_by_type(art_type, status="draft")
        if result is None:
            missing.append(art_type)
    return missing


def _wire_artifact_tools(registry: Any) -> None:
    """Inject the ArtifactRegistry into artifact_write / artifact_read tool handlers."""
    try:
        import tools.artifact_write.handler as aw
        aw.set_registry(registry)
    except Exception:
        pass
    try:
        import tools.artifact_read.handler as ar
        ar.set_registry(registry)
    except Exception:
        pass


async def _human_phase_gate(phase_id: str, phase_name: str, trace_id: str) -> bool:
    """
    Pause for interactive human approval at a phase gate.

    Returns True if approved, False if rejected.
    In non-TTY environments (CI), auto-approves and logs a warning.
    """
    import sys
    log.info(
        "phase_gate_waiting",
        phase=phase_id,
        trace_id=trace_id[:8],
        prompt=f"Approve phase '{phase_name}'?",
    )

    if not sys.stdin.isatty():
        log.warning("phase_gate_auto_approved", phase=phase_id, reason="non_interactive")
        return True

    try:
        from rich.console import Console
        console = Console()
        console.print(
            f"\n[bold yellow]Phase Gate:[/bold yellow] '{phase_name}' is ready for review.\n"
            f"  Trace: {trace_id[:8]}\n"
            f"  Approve to continue, reject to stop the run.\n"
        )
        answer = console.input("  Approve? [y/N] ").strip().lower()
        return answer in ("y", "yes")
    except Exception:
        answer = input(f"\n[Phase Gate] Approve '{phase_name}'? [y/N] ").strip().lower()
        return answer in ("y", "yes")
