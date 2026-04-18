"""Lifecycle hooks for the GKE monitor agent."""

from __future__ import annotations

import os

from core.agent import Agent
from core.task import Task, TaskResult
from observability.logutil import get_logger

log = get_logger("agents.gke_monitor")


async def on_spawn(agent: Agent) -> None:
    ctx = {
        "cluster": os.environ.get("SWARM_GKE_CLUSTER"),
        "region": os.environ.get("SWARM_GKE_REGION"),
        "project": os.environ.get("SWARM_GKE_PROJECT"),
        "namespace": os.environ.get("SWARM_K8S_NAMESPACE"),
    }
    missing = [k for k, v in ctx.items() if not v]
    if missing:
        log.warning(
            "gke_monitor_env_incomplete",
            missing_keys=missing,
            role=agent.spec.role,
        )
    await agent._scratchpad.write("gke_context", ctx)


async def on_complete(agent: Agent, task: Task, result: TaskResult) -> None:
    out = result.output
    overall: str | None = None
    if isinstance(out, dict):
        overall = out.get("overall_status")
    out_s = str(result.output)
    if result.success and ("HealthReport" in out_s or overall is not None):
        log.info(
            "gke_health_check_complete",
            overall_status=overall or "reported",
            role=agent.spec.role,
            task_id=task.id,
        )
    if not result.success:
        log.warning(
            "gke_monitor_error",
            role=agent.spec.role,
            task_id=task.id,
            detail="manual inspection recommended",
        )
