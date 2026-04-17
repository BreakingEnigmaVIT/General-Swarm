"""Unit tests for Scratchpad and Blackboard memory backends."""

import pytest
from memory.scratchpad import Scratchpad
from memory.blackboard import Blackboard


# ── Scratchpad ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_scratchpad_write_read():
    sp = Scratchpad("test-agent")
    await sp.write("key1", {"data": 42})
    val = await sp.read("key1")
    assert val == {"data": 42}


@pytest.mark.asyncio
async def test_scratchpad_read_missing():
    sp = Scratchpad("test-agent")
    val = await sp.read("ghost")
    assert val is None


@pytest.mark.asyncio
async def test_scratchpad_search():
    sp = Scratchpad("test-agent")
    await sp.write("python", "Python is a programming language")
    results = await sp.search("programming")
    assert len(results) >= 1


@pytest.mark.asyncio
async def test_scratchpad_clear():
    sp = Scratchpad("test-agent")
    await sp.write("x", 1)
    await sp.clear()
    assert await sp.read("x") is None


@pytest.mark.asyncio
async def test_scratchpad_message_history():
    sp = Scratchpad("agent", max_tokens=10000)
    sp.append_message({"role": "user", "content": "hello"})
    sp.append_message({"role": "assistant", "content": "hi"})
    msgs = sp.get_messages()
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"


@pytest.mark.asyncio
async def test_scratchpad_compaction():
    sp = Scratchpad("agent", max_tokens=50)
    for i in range(30):
        sp.append_message({"role": "user", "content": f"message {i} " * 10})
    # After compaction, history should be shorter
    msgs = sp.get_messages()
    assert len(msgs) < 30


# ── Blackboard ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_blackboard_write_read():
    bb = Blackboard("swarm-1")
    await bb.write("finding", "Earth is round")
    val = await bb.read("finding")
    assert val == "Earth is round"


@pytest.mark.asyncio
async def test_blackboard_append_only():
    bb = Blackboard("swarm-1")
    await bb.write("k", "v1")
    await bb.write("k", "v2")
    # read returns latest
    assert await bb.read("k") == "v2"
    # but full history is kept
    assert len(bb._entries) == 2


@pytest.mark.asyncio
async def test_blackboard_latest():
    bb = Blackboard("swarm-1")
    await bb.write("a", "1")
    await bb.write("b", "2")
    await bb.write("a", "3")
    latest = bb.latest()
    assert latest["a"] == "3"
    assert latest["b"] == "2"


@pytest.mark.asyncio
async def test_blackboard_snapshot_contains_all():
    bb = Blackboard("swarm-1")
    await bb.write("x", "first")
    await bb.write("x", "second")
    snap = bb.snapshot()
    assert len(snap) == 2


@pytest.mark.asyncio
async def test_blackboard_search():
    bb = Blackboard("swarm-1")
    await bb.write("research", "Python is popular in data science")
    results = await bb.search("data science")
    assert len(results) >= 1
