"""
Integration tests — multi-component flows against mocked LLM provider.
No real API calls; no cost.
"""

from __future__ import annotations

import pytest
from configs.schema import AgentSpec, TerminationPolicy, ToolSpec
from coordination.bus import InProcessBus
from core.agent import Agent
from core.task import Task, TaskConstraints
from providers.base import CompletionResult, ToolCall
from core.task import TokenUsage
from tools.echo.handler import EchoHandler


def _echo_spec():
    h = EchoHandler()
    h.spec = ToolSpec(
        name="echo",
        description="Echo",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_schema={},
    )
    return h


@pytest.mark.asyncio
async def test_agent_uses_echo_tool_and_returns_result(mock_provider_with_tool_call):
    spec = AgentSpec(
        name="echo", role="echo",
        system_prompt="Use the echo tool when asked.",
        tools=["echo"],
        termination=TerminationPolicy(max_iterations=5, max_tokens=1024),
    )
    bus = InProcessBus()
    agent = Agent(
        spec=spec,
        provider=mock_provider_with_tool_call,
        tool_handlers={"echo": _echo_spec()},
        bus=bus,
    )
    task = Task(goal="Echo 'hello'", constraints=TaskConstraints(max_iterations=5))
    result = await agent.run_task(task)

    assert result.success is True
    assert result.iterations == 2
    assert result.token_usage.total_tokens > 0


@pytest.mark.asyncio
async def test_agent_respects_budget(mock_provider):
    """Agent should raise BudgetExceededError if the ledger is already over budget."""
    from core.exceptions import BudgetExceededError
    from observability.cost import CostLedger
    from core.task import TokenUsage

    ledger = CostLedger()
    # Pre-fill ledger above the budget
    ledger.record("other", "llama-3.1-8b-instant",
                  TokenUsage(input_tokens=10_000_000, output_tokens=10_000_000,
                             total_tokens=20_000_000))

    spec = AgentSpec(
        name="x", role="x",
        system_prompt="Do stuff",
        tools=[],
        termination=TerminationPolicy(max_iterations=5, max_tokens=512),
    )
    bus = InProcessBus()
    agent = Agent(
        spec=spec, provider=mock_provider,
        tool_handlers={}, bus=bus, ledger=ledger,
    )
    task = Task(goal="something", constraints=TaskConstraints(budget=0.0001, max_iterations=5))
    with pytest.raises(BudgetExceededError):
        await agent.run_task(task)


@pytest.mark.asyncio
async def test_filesystem_tool_write_read(tmp_path, monkeypatch):
    import tools.filesystem.handler as fsh
    from tools.filesystem.handler import FilesystemHandler
    from configs.schema import ToolSpec

    monkeypatch.setattr(fsh, "_CWD", tmp_path)

    h = FilesystemHandler()
    h.spec = ToolSpec(
        name="filesystem", description="fs",
        input_schema={"type": "object", "properties": {"operation": {}, "path": {}},
                      "required": ["operation", "path"]},
        output_schema={}, side_effect_level="mutates-local",
    )

    write_r = await h._run({"operation": "write", "path": "hello.txt", "content": "world"})
    assert "written" in write_r

    read_r = await h._run({"operation": "read", "path": "hello.txt"})
    assert read_r["content"] == "world"


@pytest.mark.asyncio
async def test_data_parse_json():
    from tools.data_parse.handler import DataParseHandler
    from configs.schema import ToolSpec

    h = DataParseHandler()
    h.spec = ToolSpec(
        name="data_parse", description="dp",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={},
    )
    r = await h._run({"format": "json", "content": '{"x": 1, "y": 2}'})
    assert r["data"] == {"x": 1, "y": 2}


@pytest.mark.asyncio
async def test_data_parse_keys():
    from tools.data_parse.handler import DataParseHandler
    from configs.schema import ToolSpec

    h = DataParseHandler()
    h.spec = ToolSpec(
        name="data_parse", description="dp",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={},
    )
    r = await h._run({"format": "json", "content": '{"a": 1, "b": 2}', "operation": "keys"})
    assert set(r["keys"]) == {"a", "b"}
