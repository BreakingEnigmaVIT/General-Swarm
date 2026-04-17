"""Unit tests for the TaskGraph DAG executor."""

import pytest
from coordination.task_graph import TaskGraph, TaskGraphExecutor
from core.exceptions import CyclicTaskGraphError
from core.task import Task, TaskResult, TaskStatus


def make_task(goal: str) -> Task:
    return Task(goal=goal)


@pytest.mark.asyncio
async def test_single_task_runs():
    graph = TaskGraph()
    t = make_task("do x")
    graph.add_task(t)

    async def exec_fn(task: Task) -> TaskResult:
        return TaskResult(output="done", success=True)

    executor = TaskGraphExecutor(executor=exec_fn)
    results = await executor.run(graph)
    assert len(results) == 1
    assert results[0].success is True


@pytest.mark.asyncio
async def test_dependency_ordering():
    graph = TaskGraph()
    order = []

    t1 = make_task("first")
    t2 = make_task("second")
    graph.add_task(t1)
    graph.add_task(t2)
    graph.add_dependency(t2.id, t1.id)  # t2 depends on t1

    async def exec_fn(task: Task) -> TaskResult:
        order.append(task.goal)
        return TaskResult(output="ok", success=True)

    executor = TaskGraphExecutor(executor=exec_fn)
    await executor.run(graph)

    assert order.index("first") < order.index("second")


@pytest.mark.asyncio
async def test_parallel_independent_tasks():
    import asyncio, time
    graph = TaskGraph()
    start_times = {}

    t1 = make_task("task_a")
    t2 = make_task("task_b")
    graph.add_task(t1)
    graph.add_task(t2)

    async def exec_fn(task: Task) -> TaskResult:
        start_times[task.goal] = time.monotonic()
        await asyncio.sleep(0.05)
        return TaskResult(output="ok", success=True)

    executor = TaskGraphExecutor(executor=exec_fn)
    t_start = time.monotonic()
    await executor.run(graph)
    elapsed = time.monotonic() - t_start

    # Both tasks ran in parallel — total time should be ~0.05s, not ~0.1s
    assert elapsed < 0.09, f"Tasks didn't run in parallel (elapsed={elapsed:.3f}s)"


def test_cycle_detection():
    graph = TaskGraph()
    t1 = make_task("a")
    t2 = make_task("b")
    graph.add_task(t1)
    graph.add_task(t2)
    graph.add_dependency(t2.id, t1.id)
    with pytest.raises(CyclicTaskGraphError):
        graph.add_dependency(t1.id, t2.id)


def test_is_complete():
    graph = TaskGraph()
    t = make_task("x")
    graph.add_task(t)
    assert not graph.is_complete()
    t.mark_completed(TaskResult(output="ok", success=True))
    graph.update_task(t)
    assert graph.is_complete()
