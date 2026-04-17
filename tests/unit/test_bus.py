"""Unit tests for the in-process message bus."""

import asyncio
import pytest
from coordination.bus import InProcessBus
from core.message import Message, MessageType


@pytest.mark.asyncio
async def test_direct_message_delivery():
    bus = InProcessBus()
    sent = []

    async def receiver():
        async for msg in bus.subscribe("agent-b"):
            sent.append(msg)
            break

    task = asyncio.create_task(receiver())
    await asyncio.sleep(0.01)

    msg = Message.request("agent-a", "agent-b", {"hello": "world"})
    await bus.send(msg)

    await asyncio.wait_for(task, timeout=2.0)
    assert len(sent) == 1
    assert sent[0].payload == {"hello": "world"}


@pytest.mark.asyncio
async def test_broadcast_reaches_subscribers():
    bus = InProcessBus()
    received_a, received_b = [], []

    async def sub_a():
        async for msg in bus.subscribe("a", channel="updates"):
            received_a.append(msg)
            break

    async def sub_b():
        async for msg in bus.subscribe("b", channel="updates"):
            received_b.append(msg)
            break

    ta = asyncio.create_task(sub_a())
    tb = asyncio.create_task(sub_b())
    await asyncio.sleep(0.01)

    bcast = Message.broadcast("sender", "updates", {"event": "new_task"})
    await bus.broadcast(bcast, "updates")

    await asyncio.wait_for(asyncio.gather(ta, tb), timeout=2.0)
    assert len(received_a) == 1
    assert len(received_b) == 1


@pytest.mark.asyncio
async def test_message_not_delivered_to_wrong_recipient():
    bus = InProcessBus()
    received = []

    async def receiver():
        async for msg in bus.subscribe("target"):
            received.append(msg)
            break

    task = asyncio.create_task(receiver())
    await asyncio.sleep(0.01)

    # Send to different recipient
    msg = Message.request("sender", "other-agent", {"x": 1})
    await bus.send(msg)

    # Also send to correct recipient to unblock
    msg2 = Message.request("sender", "target", {"x": 2})
    await bus.send(msg2)

    await asyncio.wait_for(task, timeout=2.0)
    assert all(m.recipient_id == "target" for m in received)
