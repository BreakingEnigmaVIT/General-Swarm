"""Unit tests for core.task models."""

import pytest
from core.task import Task, TaskConstraints, TaskResult, TaskStatus, TokenUsage


def test_task_default_status():
    t = Task(goal="do something")
    assert t.status == TaskStatus.PENDING


def test_task_mark_running():
    t = Task(goal="do something")
    t.mark_running("agent-1")
    assert t.status == TaskStatus.RUNNING
    assert t.assigned_agent == "agent-1"


def test_task_mark_completed():
    t = Task(goal="do something")
    result = TaskResult(output="done", success=True)
    t.mark_completed(result)
    assert t.status == TaskStatus.COMPLETED
    assert t.result.success is True


def test_task_mark_failed():
    t = Task(goal="do something")
    t.mark_failed("network error")
    assert t.status == TaskStatus.FAILED
    assert t.result.error == "network error"


def test_task_fork_inherits_parent_id():
    parent = Task(goal="parent goal")
    child = parent.fork(goal="child goal")
    assert child.parent_id == parent.id
    assert child.goal == "child goal"


def test_task_trace_log():
    t = Task(goal="x")
    t.add_trace("test_event", key="value")
    assert len(t.trace_log) == 1
    assert t.trace_log[0].event == "test_event"
    assert t.trace_log[0].detail == {"key": "value"}


def test_token_usage_addition():
    a = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    b = TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30)
    c = a + b
    assert c.input_tokens == 30
    assert c.output_tokens == 15
    assert c.total_tokens == 45


def test_task_constraints_defaults():
    c = TaskConstraints()
    assert c.timeout == 300.0
    assert c.max_iterations == 20
    assert c.budget is None
