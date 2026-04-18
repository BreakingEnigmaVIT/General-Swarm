"""DAG-based task graph executor.

Supports:
- Declaring dependency edges between tasks
- Running independent tasks in parallel (asyncio.gather)
- Dynamic graph mutation (agents can add subtasks mid-run)
- Global timeout and budget enforcement
- Replayable execution trace via the observability layer
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Coroutine, Literal, Optional

import networkx as nx

from coordination.task_queue import TaskQueue
from core.exceptions import CyclicTaskGraphError
from core.task import Task, TaskResult, TaskStatus
from observability.logutil import get_logger
from observability.tracing import Span, get_tracer

log = get_logger("coordination.task_graph")

ExecutorFn = Callable[[Task], Coroutine[Any, Any, TaskResult]]


class TaskGraph:
    """Directed acyclic graph of Tasks with dependency tracking."""

    def __init__(self) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()

    def add_task(self, task: Task) -> None:
        self._graph.add_node(task.id, task=task)

    def add_dependency(self, task_id: str, depends_on_id: str) -> None:
        """task_id starts only after depends_on_id completes."""
        self._graph.add_edge(depends_on_id, task_id)
        if not nx.is_directed_acyclic_graph(self._graph):
            self._graph.remove_edge(depends_on_id, task_id)
            raise CyclicTaskGraphError(
                f"Adding {depends_on_id} → {task_id} would create a cycle"
            )

    def get_task(self, task_id: str) -> Task:
        return self._graph.nodes[task_id]["task"]

    def update_task(self, task: Task) -> None:
        self._graph.nodes[task.id]["task"] = task

    def ready_tasks(self) -> list[Task]:
        """Tasks with PENDING status whose all predecessors are COMPLETED."""
        ready = []
        for node_id in self._graph.nodes:
            task = self._graph.nodes[node_id]["task"]
            if task.status != TaskStatus.PENDING:
                continue
            preds = list(self._graph.predecessors(node_id))
            if all(self._graph.nodes[p]["task"].status == TaskStatus.COMPLETED for p in preds):
                ready.append(task)
        return ready

    def is_complete(self) -> bool:
        return all(
            self._graph.nodes[n]["task"].status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            for n in self._graph.nodes
        )

    def has_failed(self) -> bool:
        return any(
            self._graph.nodes[n]["task"].status == TaskStatus.FAILED
            for n in self._graph.nodes
        )

    def all_tasks(self) -> list[Task]:
        return [self._graph.nodes[n]["task"] for n in nx.topological_sort(self._graph)]

    def summary(self) -> dict:
        tasks = self.all_tasks()
        return {
            "total": len(tasks),
            "pending": sum(1 for t in tasks if t.status == TaskStatus.PENDING),
            "running": sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            "completed": sum(1 for t in tasks if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in tasks if t.status == TaskStatus.FAILED),
        }


# ── Graph executor ────────────────────────────────────────────────────────────

class TaskGraphExecutor:
    """
    Runs a TaskGraph using the provided executor function for each leaf task.
    Independent tasks run in parallel; dependents wait.
    """

    def __init__(
        self,
        executor: ExecutorFn,
        global_timeout: Optional[float] = None,
        global_budget: Optional[float] = None,
        task_queue: Optional[TaskQueue] = None,
        dispatch_mode: Literal["in_process", "task_queue"] = "in_process",
    ) -> None:
        self._executor = executor
        self._global_timeout = global_timeout
        self._global_budget = global_budget
        self._total_cost = 0.0
        if task_queue is not None:
            self._task_queue = task_queue
            self._dispatch_mode: Literal["in_process", "task_queue"] = "task_queue"
        else:
            self._task_queue = None
            if dispatch_mode == "task_queue":
                raise ValueError("task_queue is required when dispatch_mode is 'task_queue'")
            self._dispatch_mode = "in_process"

    async def run(self, graph: TaskGraph) -> list[TaskResult]:
        start = time.time()
        results: list[TaskResult] = []

        while not graph.is_complete():
            if self._global_timeout and time.time() - start > self._global_timeout:
                log.warning("task_graph_timeout")
                break
            if self._global_budget and self._total_cost >= self._global_budget:
                log.warning("task_graph_budget_exhausted", spent=self._total_cost)
                break

            ready = graph.ready_tasks()
            if not ready:
                # All remaining tasks blocked on failed predecessors
                if graph.has_failed():
                    log.warning("task_graph_stalled_on_failure")
                    break
                await asyncio.sleep(0.05)
                continue

            # Mark all ready tasks as running
            for task in ready:
                task.mark_running("graph-executor")
                graph.update_task(task)

            # Execute them in parallel
            batch_results = await asyncio.gather(
                *[self._run_one(task, graph) for task in ready],
                return_exceptions=False,
            )
            results.extend(batch_results)

        return results

    async def _run_one(self, task: Task, graph: TaskGraph) -> TaskResult:
        try:
            if self._task_queue is not None:
                role = task.input_payload.get("agent_role", "general")
                await self._task_queue.enqueue(role, task)
                result = await self._task_queue.wait_for_result(
                    task.id,
                    timeout=task.constraints.timeout,
                    poll_interval=0.5,
                )
                if result is None:
                    tracer = get_tracer()
                    with Span(tracer, "task_timeout", "task", task_id=task.id):
                        pass
                    err = "task_timeout"
                    task.mark_failed(err)
                    graph.update_task(task)
                    return TaskResult(output=None, success=False, error=err)
                self._total_cost += result.cost
                task.mark_completed(result)
                graph.update_task(task)
                log.info("task_completed", task_id=task.id[:8], cost=round(result.cost, 6))
                return result

            result = await self._executor(task)
            self._total_cost += result.cost
            task.mark_completed(result)
            graph.update_task(task)
            log.info("task_completed", task_id=task.id[:8], cost=round(result.cost, 6))
            return result
        except Exception as exc:
            log.error("task_failed", task_id=task.id[:8], error=str(exc))
            task.mark_failed(str(exc))
            graph.update_task(task)
            return TaskResult(output=None, success=False, error=str(exc))
