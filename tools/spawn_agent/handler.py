"""Spawn a sub-agent and await its result (injected factory at runtime)."""

from __future__ import annotations

from typing import Any, Callable, Coroutine, Optional

from core.task import Task, TaskConstraints, TaskResult
from tools.base import ToolHandler

AgentFactory = Callable[[str, str], Coroutine[Any, Any, TaskResult]]

_factory: Optional[AgentFactory] = None


def set_factory(factory: AgentFactory) -> None:
    global _factory
    _factory = factory


class SpawnAgentHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _factory is None:
            return {"output": None, "success": False, "error": "Agent factory not configured"}
        role = inputs["role"]
        goal = inputs["goal"]
        budget = inputs.get("budget_usd")
        result = await _factory(role, goal)
        return {
            "output": result.output,
            "success": result.success,
            "cost": result.cost,
            "error": result.error,
        }

    async def self_test(self) -> bool:
        return True


handler = SpawnAgentHandler()
