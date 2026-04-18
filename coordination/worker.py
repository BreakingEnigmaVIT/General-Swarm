"""Runnable worker entrypoint: consume Redis Stream tasks for one role and execute Agent.run_task."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Optional, Set

from configs.loader import load_swarm_config
from configs.schema import AgentSpec
from coordination.bus import create_bus
from coordination.task_queue import RedisStreamTaskQueue
from core.agent import Agent
from core.registry import bootstrap_registries
from core.task import Task, TaskResult
from memory.longterm import LocalChromaMemory
from observability.cost import CostLedger
from observability.logging import configure_logging, get_logger
from observability.tracing import configure_tracer

log = get_logger("coordination.worker")


def _resolve_dir(env_key: str, container_default: str, local_relative: str) -> Path:
    override = os.environ.get(env_key)
    if override:
        return Path(override)
    c = Path(container_default)
    if c.exists():
        return c
    return Path(__file__).resolve().parent.parent / local_relative


def _fallback_spec(role: str, default_model: str) -> AgentSpec:
    return AgentSpec(
        name=role,
        role=role,
        system_prompt=f"You are a {role} agent. Complete the assigned task thoroughly.",
        model=default_model,
    )


async def _run_worker() -> None:
    stop = asyncio.Event()

    def _on_term(*_args: Any) -> None:
        stop.set()

    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _on_term)

    role = os.environ.get("SWARM_WORKER_ROLE")
    if not role:
        log.error("missing_env", var="SWARM_WORKER_ROLE")
        sys.exit(1)

    concurrency = max(1, int(os.environ.get("SWARM_WORKER_CONCURRENCY", "1")))
    consumer = os.environ.get("HOSTNAME", "worker")

    agents_dir = str(_resolve_dir("SWARM_AGENTS_DIR", "/app/agents", "agents"))
    tools_dir = str(_resolve_dir("SWARM_TOOLS_DIR", "/app/tools", "tools"))

    cfg = load_swarm_config()
    configure_logging(cfg.log_level, cfg.log_file)
    configure_tracer(cfg.trace_dir)

    redis_url = os.environ.get("SWARM_REDIS_URL") or os.environ.get("REDIS_URL", cfg.redis_url)
    stream_prefix = os.environ.get("SWARM_STREAM_PREFIX", "swarm")
    queue = RedisStreamTaskQueue(redis_url, stream_prefix=stream_prefix)

    tr, ar, pr = bootstrap_registries(
        tools_dir=tools_dir,
        agents_dir=agents_dir,
        groq_api_key=cfg.groq_api_key,
        default_model=cfg.default_model,
    )
    bus = create_bus(cfg.bus_transport, redis_url)
    longterm = LocalChromaMemory(persist_dir=cfg.memory_dir)
    ledger = CostLedger()
    agent_specs: dict[str, AgentSpec] = {name: spec for name, spec in ar.items()}
    tool_handlers = {name: h for name, h in tr.items()}

    provider = None
    if pr.list():
        provider = pr.get_or_default("groq")

    orch_id = os.environ.get("SWARM_ORCHESTRATOR_ID", "orchestrator")
    pub_channel = f"{stream_prefix}:{orch_id}"
    group = f"workers:{role}"
    sem = asyncio.Semaphore(concurrency)
    redis_client = await queue._get_redis()
    stub = os.environ.get("SWARM_WORKER_STUB") == "1"

    background: Set[asyncio.Task[None]] = set()

    async def _process_one(msg_id: str, task: Task) -> None:
        async with sem:
            start = time.perf_counter()
            result: Optional[TaskResult] = None
            try:
                if stub:
                    result = TaskResult(output="ok", success=True)
                else:
                    if provider is None:
                        raise RuntimeError("No LLM provider registered (set GROQ_API_KEY)")
                    spec = agent_specs.get(role) or _fallback_spec(role, cfg.default_model)
                    tools = {n: h for n, h in tool_handlers.items() if n in spec.tools}
                    agent = Agent(
                        spec=spec,
                        provider=provider,
                        tool_handlers=tools,
                        bus=bus,
                        longterm_memory=longterm,
                        ledger=ledger,
                    )
                    result = await agent.run_task(task)

                await queue.store_result(task.id, result)
                await redis_client.publish(
                    pub_channel,
                    json.dumps({"task_id": task.id, "success": result.success}),
                )
                await queue.acknowledge(role, msg_id)
            except Exception as exc:
                log.error("worker_task_failed", task_id=task.id[:8], error=str(exc))
                await queue.nack(role, msg_id)
            finally:
                duration_ms = (time.perf_counter() - start) * 1000
                tokens = result.token_usage.total_tokens if result else 0
                status = "error"
                if result is not None:
                    status = "ok" if result.success else "error"
                try:
                    await queue.emit_metric(
                        role=role,
                        task_id=task.id,
                        duration_ms=duration_ms,
                        token_usage=tokens,
                        status=status,
                    )
                except Exception:
                    pass

    try:
        while not stop.is_set():
            item = await queue.dequeue(role, group, consumer, block_ms=2000)
            if item is None:
                continue
            msg_id, task = item
            t = asyncio.create_task(_process_one(msg_id, task))
            background.add(t)
            t.add_done_callback(background.discard)
    finally:
        if background:
            await asyncio.gather(*background, return_exceptions=True)
        await queue.aclose()
        await bus.close()

    sys.exit(0)


def main() -> None:
    asyncio.run(_run_worker())


if __name__ == "__main__":
    main()
