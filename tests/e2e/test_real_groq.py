"""
End-to-end tests — real Groq API calls.
Skipped automatically if GROQ_API_KEY is not set.

Run explicitly:
  pytest tests/e2e/ -v
"""

from __future__ import annotations

import os

import pytest
from dotenv import load_dotenv

load_dotenv()  # ensure .env is loaded before the skip check

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
skip_without_key = pytest.mark.skipif(
    not GROQ_API_KEY,
    reason="GROQ_API_KEY not set — skipping real API tests",
)


@skip_without_key
@pytest.mark.asyncio
async def test_groq_simple_completion():
    from providers.groq.adapter import GroqAdapter
    from core.task import TokenUsage

    adapter = GroqAdapter(api_key=GROQ_API_KEY, default_model="llama-3.1-8b-instant")
    result = await adapter.complete(
        messages=[{"role": "user", "content": "What is 2+2? Reply with just the number."}],
        model="llama-3.1-8b-instant",
        temperature=0.0,
    )
    assert result.content is not None
    assert "4" in result.content
    assert result.usage.total_tokens > 0


@skip_without_key
@pytest.mark.asyncio
async def test_single_agent_echo_task():
    """Full pipeline: config → registry → agent → Groq → result."""
    from configs.loader import load_swarm_config
    from core.registry import bootstrap_registries
    from core.agent import Agent
    from core.task import Task
    from coordination.bus import InProcessBus

    cfg = load_swarm_config({"groq_api_key": GROQ_API_KEY})
    tr, ar, pr = bootstrap_registries(
        tools_dir="./tools",
        agents_dir="./agents",
        groq_api_key=GROQ_API_KEY,
    )
    provider = pr.lookup("groq")
    spec = ar.lookup("echo")
    tools = {n: h for n, h in tr.items() if n in spec.tools}

    agent = Agent(spec=spec, provider=provider, tool_handlers=tools, bus=InProcessBus())
    result = await agent.run_task(Task(goal="Say hello in one sentence."))
    assert result.success is True
    assert isinstance(result.output, str)
    assert len(result.output) > 0


@skip_without_key
@pytest.mark.asyncio
async def test_swarm_run_simple_goal():
    """Full swarm run against a simple single-agent goal."""
    from configs.loader import load_swarm_config
    from core.registry import bootstrap_registries
    from coordination.bus import create_bus
    from coordination.orchestrator import SwarmRuntime
    from configs.schema import TopologySpec, AgentSlot
    from memory.longterm import LocalChromaMemory
    from observability.cost import reset_ledger
    import tempfile, os

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_swarm_config({"groq_api_key": GROQ_API_KEY, "memory_dir": tmpdir})
        tr, ar, pr = bootstrap_registries(
            tools_dir="./tools",
            agents_dir="./agents",
            groq_api_key=GROQ_API_KEY,
        )
        topology = TopologySpec(name="e2e-test",
                                agents=[AgentSlot(role="echo")])
        bus = create_bus("in-process")
        longterm_memory = LocalChromaMemory(persist_dir=tmpdir)
        runtime = SwarmRuntime(
            topology=topology,
            provider=pr.lookup("groq"),
            tool_handlers={n: h for n, h in tr.items()},
            agent_specs={n: s for n, s in ar.items()},
            bus=bus,
            longterm_memory=longterm_memory,
            ledger=reset_ledger(),
        )
        result = await runtime.run("What is the capital of France? One word.")
        longterm_memory.close()
        assert result.success is True
        assert "Paris" in str(result.output)
