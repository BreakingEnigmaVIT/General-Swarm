"""Unit and integration tests for in-process and Redis Stream task queues."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from coordination.task_queue import InProcessTaskQueue, RedisStreamTaskQueue
from core.task import Task


def _redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or os.environ.get("SWARM_REDIS_URL")


def _redis_reachable(url: str) -> bool:
    try:
        import redis as sync_redis

        client = sync_redis.from_url(url, socket_connect_timeout=2)
        try:
            client.ping()
            return True
        finally:
            client.close()
    except Exception:
        return False


@pytest.mark.asyncio
async def test_in_process_queue_depth_and_drain() -> None:
    q = InProcessTaskQueue()
    role = "coder"
    for i in range(3):
        await q.enqueue(role, Task(goal=f"t{i}"))
    assert await q.depth(role) == 3
    seen = []
    for _ in range(3):
        item = await q.dequeue(role, f"workers:{role}", "c1", block_ms=1000)
        assert item is not None
        mid, task = item
        seen.append(task.goal)
        await q.acknowledge(role, mid)
    assert await q.depth(role) == 0
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_redis_stream_queue_enqueue_dequeue_ack() -> None:
    url = _redis_url()
    if not url:
        pytest.skip("REDIS_URL / SWARM_REDIS_URL not set")
    if not _redis_reachable(url):
        pytest.skip("Redis server not reachable at REDIS_URL")
    prefix = f"swarm_ut_{uuid.uuid4().hex[:10]}"
    q = RedisStreamTaskQueue(url, stream_prefix=prefix)
    role = "ut_role"
    try:
        task = Task(goal="one", input_payload={"agent_role": role})
        await q.enqueue(role, task)
        assert await q.depth(role) == 1
        group = f"workers:{role}"
        consumer = "ut-consumer"
        item = await q.dequeue(role, group, consumer, block_ms=5000)
        assert item is not None
        msg_id, got = item
        assert got.id == task.id
        await q.acknowledge(role, msg_id)
        assert await q.depth(role) == 0
    finally:
        await q.aclose()


@pytest.mark.asyncio
async def test_worker_subprocess_writes_result() -> None:
    url = _redis_url()
    if not url:
        pytest.skip("REDIS_URL / SWARM_REDIS_URL not set")
    if not _redis_reachable(url):
        pytest.skip("Redis server not reachable at REDIS_URL")
    repo = Path(__file__).resolve().parents[2]
    prefix = f"swarm_wk_{uuid.uuid4().hex[:10]}"
    role = "worker_integration_role"

    env = os.environ.copy()
    env["SWARM_WORKER_ROLE"] = role
    env["SWARM_WORKER_CONCURRENCY"] = "1"
    env["SWARM_WORKER_STUB"] = "1"
    env["HOSTNAME"] = "pytest-worker-1"
    env["SWARM_REDIS_URL"] = url
    env["SWARM_STREAM_PREFIX"] = prefix
    env["SWARM_ORCHESTRATOR_ID"] = "test-orch"
    env["SWARM_AGENTS_DIR"] = str(repo / "agents")
    env["SWARM_TOOLS_DIR"] = str(repo / "tools")
    env["PYTHONPATH"] = str(repo)

    proc = subprocess.Popen(
        [sys.executable, "-m", "coordination.worker"],
        env=env,
        cwd=str(repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    q = RedisStreamTaskQueue(url, stream_prefix=prefix)
    try:
        await asyncio.sleep(1.5)
        task = Task(goal="integration", input_payload={"agent_role": role})
        await q.enqueue(role, task)
        result = await q.wait_for_result(task.id, timeout=5.0, poll_interval=0.2)
        assert result is not None
        assert result.success is True
        assert result.output == "ok"
    finally:
        if proc.poll() is None:
            if hasattr(signal, "SIGTERM"):
                proc.send_signal(signal.SIGTERM)
            else:
                proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        await q.aclose()
