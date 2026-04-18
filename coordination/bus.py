"""
Transport-agnostic message bus interface + two implementations:
  - InProcessBus  — asyncio queues, zero dependencies, for local single-process runs
  - RedisBus      — pub/sub via redis-py, for multi-process distributed runs (Phase 10)

Swapping transports is a one-line config change: bus_transport = "redis"
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional

from core.message import Message
from observability.logutil import get_logger
from observability.tracing import Span, get_tracer

log = get_logger("coordination.bus")


# ── Interface ─────────────────────────────────────────────────────────────────

class MessageBus(ABC):
    @abstractmethod
    async def send(self, message: Message) -> None: ...

    @abstractmethod
    async def subscribe(
        self, agent_id: str, channel: Optional[str] = None
    ) -> AsyncIterator[Message]: ...

    @abstractmethod
    async def broadcast(self, message: Message, channel: str) -> None: ...

    @abstractmethod
    async def close(self) -> None: ...


# ── In-process (asyncio queues) ───────────────────────────────────────────────

class InProcessBus(MessageBus):
    """
    Each agent subscribes with its ID and optionally a channel name.
    Direct messages go to the recipient's queue; broadcasts go to all
    subscribers of the named channel.
    """

    def __init__(self) -> None:
        self._agent_queues: dict[str, asyncio.Queue[Message]] = {}
        self._channel_queues: dict[str, list[asyncio.Queue[Message]]] = {}
        self._lock = asyncio.Lock()

    async def _get_or_create_queue(self, agent_id: str) -> asyncio.Queue[Message]:
        async with self._lock:
            if agent_id not in self._agent_queues:
                self._agent_queues[agent_id] = asyncio.Queue()
            return self._agent_queues[agent_id]

    async def send(self, message: Message) -> None:
        tracer = get_tracer()
        with Span(tracer, "bus.send", "bus",
                  sender=message.sender_id, recipient=message.recipient_id):
            log.debug("bus_send", sender=message.sender_id, recipient=message.recipient_id,
                      msg_type=message.type)
            if message.recipient_id:
                q = await self._get_or_create_queue(message.recipient_id)
                await q.put(message)
            if message.channel:
                await self.broadcast(message, message.channel)

    async def broadcast(self, message: Message, channel: str) -> None:
        async with self._lock:
            queues = list(self._channel_queues.get(channel, []))
        for q in queues:
            await q.put(message)

    async def subscribe(
        self, agent_id: str, channel: Optional[str] = None
    ) -> AsyncIterator[Message]:
        q = await self._get_or_create_queue(agent_id)
        if channel:
            async with self._lock:
                self._channel_queues.setdefault(channel, []).append(q)
        try:
            while True:
                message = await q.get()
                yield message
        finally:
            if channel:
                async with self._lock:
                    ch = self._channel_queues.get(channel, [])
                    if q in ch:
                        ch.remove(q)

    async def close(self) -> None:
        async with self._lock:
            self._agent_queues.clear()
            self._channel_queues.clear()


# ── Redis bus (Phase 10) ──────────────────────────────────────────────────────

class RedisBus(MessageBus):
    """
    Requires: pip install redis[asyncio]
    Set bus_transport = "redis" and redis_url in config.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379") -> None:
        self._url = redis_url
        self._pub: Optional[object] = None
        self._redis_mod: Optional[object] = None

    async def _get_client(self) -> object:
        if self._pub is None:
            import redis.asyncio as aioredis  # type: ignore[import]
            self._redis_mod = aioredis
            self._pub = await aioredis.from_url(self._url)
        return self._pub

    async def send(self, message: Message) -> None:
        client = await self._get_client()
        payload = message.model_dump_json()
        channel = message.recipient_id or message.channel or "broadcast"
        await client.publish(f"swarm:{channel}", payload)  # type: ignore[union-attr]

    async def broadcast(self, message: Message, channel: str) -> None:
        client = await self._get_client()
        payload = message.model_dump_json()
        await client.publish(f"swarm:{channel}", payload)  # type: ignore[union-attr]

    async def subscribe(
        self, agent_id: str, channel: Optional[str] = None
    ) -> AsyncIterator[Message]:
        import redis.asyncio as aioredis  # type: ignore[import]
        client = await aioredis.from_url(self._url)
        channels = [f"swarm:{agent_id}"]
        if channel:
            channels.append(f"swarm:{channel}")
        async with client.pubsub() as ps:
            await ps.subscribe(*channels)
            async for raw in ps.listen():
                if raw["type"] == "message":
                    msg = Message.model_validate_json(raw["data"])
                    yield msg

    async def close(self) -> None:
        if self._pub:
            await self._pub.close()  # type: ignore[union-attr]
            self._pub = None


# ── Factory ───────────────────────────────────────────────────────────────────

def create_bus(transport: str = "in-process", redis_url: str = "redis://localhost:6379") -> MessageBus:
    if transport == "in-process":
        return InProcessBus()
    if transport in ("redis", "nats"):
        return RedisBus(redis_url)
    raise ValueError(f"Unknown bus transport: {transport!r}")
