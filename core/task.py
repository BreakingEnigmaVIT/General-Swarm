"""Task — the atomic unit of work passed between agents."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


# ── Enums ─────────────────────────────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


# ── Sub-models ────────────────────────────────────────────────────────────────

class TaskConstraints(BaseModel):
    timeout: float = 300.0
    budget: Optional[float] = None
    allowed_tools: Optional[list[str]] = None
    max_iterations: int = 20


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenUsage") -> "TokenUsage":
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


class TraceEntry(BaseModel):
    timestamp: datetime = Field(default_factory=_now)
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TaskResult(BaseModel):
    output: Any
    success: bool
    error: Optional[str] = None
    token_usage: TokenUsage = Field(default_factory=TokenUsage)
    cost: float = 0.0
    duration: float = 0.0
    iterations: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


# ── Core Task ─────────────────────────────────────────────────────────────────

class Task(BaseModel):
    id: str = Field(default_factory=_uid)
    parent_id: Optional[str] = None
    goal: str
    input_payload: dict[str, Any] = Field(default_factory=dict)
    success_criteria: Optional[str] = None
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    assigned_agent: Optional[str] = None
    status: TaskStatus = TaskStatus.PENDING
    result: Optional[TaskResult] = None
    trace_log: list[TraceEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    def add_trace(self, event: str, **detail: Any) -> None:
        self.trace_log.append(TraceEntry(event=event, detail=detail))
        self.updated_at = _now()

    def mark_running(self, agent_id: str) -> None:
        self.status = TaskStatus.RUNNING
        self.assigned_agent = agent_id
        self.add_trace("task_running", agent_id=agent_id)

    def mark_completed(self, result: TaskResult) -> None:
        self.status = TaskStatus.COMPLETED
        self.result = result
        self.add_trace("task_completed", success=result.success)

    def mark_failed(self, error: str) -> None:
        self.status = TaskStatus.FAILED
        self.result = TaskResult(output=None, success=False, error=error)
        self.add_trace("task_failed", error=error)

    def fork(self, goal: str, **overrides: Any) -> "Task":
        """Create a child task derived from this one."""
        return Task(
            parent_id=self.id,
            goal=goal,
            constraints=self.constraints.model_copy(),
            **overrides,
        )
