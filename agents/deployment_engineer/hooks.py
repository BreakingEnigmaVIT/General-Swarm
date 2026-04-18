"""Lifecycle hooks for the deployment engineer agent."""

from __future__ import annotations

import re
from typing import Any

from core.agent import Agent
from core.task import Task, TaskResult
from observability.logutil import get_logger

log = get_logger("agents.deployment_engineer")


def _infer_environment(goal: str) -> str:
    g = goal.lower()
    if "prod" in g or "production" in g:
        return "prod"
    if "staging" in g:
        return "staging"
    return "dev"


async def on_task_assigned(agent: Agent, task: Task) -> None:
    env = _infer_environment(task.goal)
    log.info(
        "deployment_task_received",
        environment=env,
        role=agent.spec.role,
        task_id=task.id,
    )
    has_artifact = bool(re.search(r"artifact|DeploymentPlan|[a-f0-9-]{8,}", task.goal, re.I))
    has_service = "service_name" in task.goal.lower() or "service" in task.goal.lower()
    if not has_artifact and not has_service:
        await agent._scratchpad.write(
            "deployment_hook_note",
            "No artifact id in goal — will search artifact registry for latest "
            "DeploymentPlan in current stage.",
        )


async def on_complete(agent: Agent, task: Task, result: TaskResult) -> None:
    service_url: str | None = None
    out = result.output
    if isinstance(out, dict):
        service_url = out.get("service_url") or out.get("url")
    elif isinstance(out, str) and "http" in out:
        service_url = out[:200]

    env = _infer_environment(task.goal)

    if result.success:
        log.info(
            "deployment_complete",
            service_url=service_url,
            environment=env,
            role=agent.spec.role,
        )
    else:
        log.warning(
            "deployment_failed_escalate",
            environment=env,
            role=agent.spec.role,
        )

    task.add_trace(
        "deployment_engineer_complete",
        role="deployment_engineer",
        environment=env,
        success=result.success,
        service_url=service_url,
    )
