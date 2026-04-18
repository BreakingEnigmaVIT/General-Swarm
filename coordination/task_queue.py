"""Abstract and Redis-backed asyncio task queues for dispatching work to remote workers."""

from __future__ import annotations

import asyncio
import os
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any, Optional

from core.task import Task, TaskResult

try:
    from redis import asyncio as aioredis
    from redis.exceptions import ResponseError
except ImportError:  # pragma: no cover - optional dependency
    aioredis = None  # type: ignore[assignment]

    class ResponseError(Exception):  # type: ignore[no-redef]
        """Placeholder when redis is not installed."""


class TaskQueue(ABC):
    """Queue abstraction: enqueue tasks per agent role, dequeue with consumer groups, DLQ."""

    @abstractmethod
    async def enqueue(self, role: str, task: Task) -> str:
        """Append a task for the given role; return a stable message id."""

    @abstractmethod
    async def dequeue(
        self, role: str, group: str, consumer: str, block_ms: int
    ) -> Optional[tuple[str, Task]]:
        """Read one pending message or block up to block_ms; None on timeout."""

    @abstractmethod
    async def acknowledge(self, role: str, message_id: str) -> None:
        """Mark a message as successfully processed."""

    @abstractmethod
    async def nack(self, role: str, message_id: str) -> None:
        """Negative acknowledgement; may requeue or move to DLQ after max retries."""

    @abstractmethod
    async def depth(self, role: str) -> int:
        """Approximate number of tasks waiting in the role's queue."""

    @abstractmethod
    async def drain_dlq(self, role: str) -> list[Task]:
        """Return all tasks currently in the dead-letter queue for the role."""

    async def wait_for_result(
        self, task_id: str, timeout: float, poll_interval: float = 0.5
    ) -> Optional[TaskResult]:
        """Poll until a remote worker stores a result (Redis); unused for in-process queues."""
        return None


class InProcessTaskQueue(TaskQueue):
    """Per-role asyncio queues for local single-process execution (deployment_mode=local)."""

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[tuple[str, Task]]] = defaultdict(
            lambda: asyncio.Queue()
        )
        self._nack_counts: dict[tuple[str, str], int] = {}
        self._dlq: dict[str, list[Task]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def enqueue(self, role: str, task: Task) -> str:
        msg_id = str(uuid.uuid4())
        await self._queues[role].put((msg_id, task))
        return msg_id

    async def dequeue(
        self, role: str, group: str, consumer: str, block_ms: int
    ) -> Optional[tuple[str, Task]]:
        q = self._queues[role]
        if block_ms <= 0:
            if q.empty():
                return None
            return q.get_nowait()
        try:
            return await asyncio.wait_for(q.get(), timeout=block_ms / 1000.0)
        except asyncio.TimeoutError:
            return None

    async def acknowledge(self, role: str, message_id: str) -> None:
        async with self._lock:
            self._nack_counts.pop((role, message_id), None)

    async def nack(self, role: str, message_id: str) -> None:
        async with self._lock:
            key = (role, message_id)
            self._nack_counts[key] = self._nack_counts.get(key, 0) + 1
            if self._nack_counts[key] >= 3:
                # Without the original Task on nack, DLQ only tracks synthetic failures.
                self._nack_counts.pop(key, None)

    async def depth(self, role: str) -> int:
        return self._queues[role].qsize()

    async def drain_dlq(self, role: str) -> list[Task]:
        async with self._lock:
            items = list(self._dlq[role])
            self._dlq[role].clear()
            return items


class RedisStreamTaskQueue(TaskQueue):
    """Redis Streams task transport with consumer groups and a per-role DLQ stream."""

    def __init__(self, redis_url: str, stream_prefix: Optional[str] = None) -> None:
        if aioredis is None:
            raise ImportError("redis package is required for RedisStreamTaskQueue; pip install redis")
        self._redis_url = redis_url
        self._prefix = stream_prefix or os.environ.get("SWARM_STREAM_PREFIX", "swarm")
        self._redis: Optional[Any] = None
        self._connect_lock = asyncio.Lock()
        self._groups_ready: set[str] = set()

    def _tasks_stream(self, role: str) -> str:
        return f"{self._prefix}:tasks:{role}"

    def _results_key(self, task_id: str) -> str:
        return f"{self._prefix}:results:{task_id}"

    def _dlq_stream(self, role: str) -> str:
        return f"{self._prefix}:dlq:{role}"

    def _metrics_stream(self) -> str:
        return f"{self._prefix}:metrics"

    def _nack_key(self, role: str, message_id: str) -> str:
        return f"{self._prefix}:nack:{role}:{message_id}"

    async def _get_redis(self) -> Any:
        if self._redis is None:
            async with self._connect_lock:
                if self._redis is None:
                    self._redis = aioredis.from_url(
                        self._redis_url,
                        decode_responses=True,
                    )
        return self._redis

    async def _ensure_group(self, role: str) -> None:
        if role in self._groups_ready:
            return
        r = await self._get_redis()
        stream = self._tasks_stream(role)
        group = f"workers:{role}"
        try:
            await r.xgroup_create(name=stream, groupname=group, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" in str(exc):
                pass
            else:
                raise
        self._groups_ready.add(role)

    async def enqueue(self, role: str, task: Task) -> str:
        await self._ensure_group(role)
        r = await self._get_redis()
        stream = self._tasks_stream(role)
        msg_id = await r.xadd(stream, {"payload": task.model_dump_json()})
        return str(msg_id)

    async def dequeue(
        self, role: str, group: str, consumer: str, block_ms: int
    ) -> Optional[tuple[str, Task]]:
        await self._ensure_group(role)
        r = await self._get_redis()
        stream = self._tasks_stream(role)
        block_arg: Optional[int] = block_ms if block_ms > 0 else None
        resp = await r.xreadgroup(
            groupname=group,
            consumername=consumer,
            streams={stream: ">"},
            count=1,
            block=block_arg,
        )
        if not resp:
            return None
        _sname, messages = resp[0]
        if not messages:
            return None
        msg_id, fields = messages[0]
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode()
        task = Task.model_validate_json(raw)
        return str(msg_id), task

    async def acknowledge(self, role: str, message_id: str) -> None:
        r = await self._get_redis()
        stream = self._tasks_stream(role)
        group = f"workers:{role}"
        await r.xack(stream, group, message_id)
        await r.delete(self._nack_key(role, message_id))

    async def nack(self, role: str, message_id: str) -> None:
        r = await self._get_redis()
        stream = self._tasks_stream(role)
        group = f"workers:{role}"
        nkey = self._nack_key(role, message_id)
        count = await r.incr(nkey)
        if count >= 3:
            entries = await r.xrange(stream, min=message_id, max=message_id)
            payload = ""
            if entries:
                _mid, fields = entries[0]
                payload = fields.get("payload") or ""
            dlq = self._dlq_stream(role)
            if payload:
                await r.xadd(dlq, {"payload": payload})
            await r.xack(stream, group, message_id)
            await r.delete(nkey)

    async def depth(self, role: str) -> int:
        r = await self._get_redis()
        return int(await r.xlen(self._tasks_stream(role)))

    async def drain_dlq(self, role: str) -> list[Task]:
        r = await self._get_redis()
        dlq = self._dlq_stream(role)
        entries = await r.xrange(dlq, "-", "+")
        out: list[Task] = []
        for _mid, fields in entries:
            raw = fields.get("payload") or ""
            if raw:
                out.append(Task.model_validate_json(raw))
        return out

    async def wait_for_result(
        self, task_id: str, timeout: float, poll_interval: float = 0.5
    ) -> Optional[TaskResult]:
        r = await self._get_redis()
        key = self._results_key(task_id)
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            raw = await r.get(key)
            if raw:
                return TaskResult.model_validate_json(raw)
            now = asyncio.get_event_loop().time()
            if now >= deadline:
                return None
            await asyncio.sleep(poll_interval)

    async def store_result(self, task_id: str, result: TaskResult, ttl_seconds: int = 3600) -> None:
        """Store a finished task result for the orchestrator to poll."""
        r = await self._get_redis()
        key = self._results_key(task_id)
        await r.set(key, result.model_dump_json(), ex=ttl_seconds)

    async def emit_metric(
        self,
        *,
        role: str,
        task_id: str,
        duration_ms: float,
        token_usage: int,
        status: str,
    ) -> None:
        r = await self._get_redis()
        await r.xadd(
            self._metrics_stream(),
            {
                "role": role,
                "task_id": task_id,
                "duration_ms": str(int(duration_ms)),
                "token_usage": str(token_usage),
                "status": status,
            },
        )

    async def aclose(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None
