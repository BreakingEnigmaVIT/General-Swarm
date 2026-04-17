"""Unit tests for the Agent base class (uses MockProvider — no real API calls)."""

import pytest
from configs.schema import AgentSpec, TerminationPolicy
from core.agent import Agent
from core.task import Task, TaskConstraints, TaskStatus
from providers.base import CompletionResult, ToolCall
from core.task import TokenUsage
from tools.echo.handler import EchoHandler
from configs.schema import ToolSpec


def _make_echo_handler():
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
async def test_agent_simple_completion(mock_provider, echo_agent_spec, basic_task, in_process_bus):
    agent = Agent(
        spec=echo_agent_spec,
        provider=mock_provider,
        tool_handlers={"echo": _make_echo_handler()},
        bus=in_process_bus,
    )
    result = await agent.run_task(basic_task)
    assert result.success is True
    assert result.output == "Mock response"
    assert result.iterations == 1


@pytest.mark.asyncio
async def test_agent_tool_call_loop(mock_provider_with_tool_call, echo_agent_spec,
                                     basic_task, in_process_bus):
    agent = Agent(
        spec=echo_agent_spec,
        provider=mock_provider_with_tool_call,
        tool_handlers={"echo": _make_echo_handler()},
        bus=in_process_bus,
    )
    result = await agent.run_task(basic_task)
    assert result.success is True
    assert "hello" in str(result.output)
    assert result.iterations == 2


@pytest.mark.asyncio
async def test_agent_tool_permission_denied(mock_provider_with_tool_call,
                                             echo_agent_spec, basic_task, in_process_bus):
    # Agent spec has no tools — tool call should produce an error message, not crash
    spec_no_tools = echo_agent_spec.model_copy(update={"tools": []})
    agent = Agent(
        spec=spec_no_tools,
        provider=mock_provider_with_tool_call,
        tool_handlers={"echo": _make_echo_handler()},
        bus=in_process_bus,
    )
    result = await agent.run_task(basic_task)
    # The second LLM call returns a final answer, so the agent still completes
    assert result.iterations >= 1


@pytest.mark.asyncio
async def test_agent_max_iterations_raises(in_process_bus):
    from providers.base import CompletionResult, ToolCall
    from core.exceptions import MaxIterationsError

    # Provider always returns a tool call — agent never finishes
    endless_provider = type("EP", (), {
        "name": "ep",
        "complete": lambda self, *a, **k: _always_tool_call(),
        "stream": None,
        "count_tokens": lambda *a: 1,
        "estimate_cost": lambda *a: 0.0,
    })()

    spec = AgentSpec(
        name="x", role="x",
        system_prompt="x",
        tools=["echo"],
        termination=TerminationPolicy(max_iterations=2, max_tokens=512),
    )
    agent = Agent(
        spec=spec,
        provider=endless_provider,
        tool_handlers={"echo": _make_echo_handler()},
        bus=in_process_bus,
    )
    task = Task(goal="loop", constraints=TaskConstraints(max_iterations=2))
    with pytest.raises(MaxIterationsError):
        await agent.run_task(task)


async def _always_tool_call():
    return CompletionResult(
        content=None,
        tool_calls=[ToolCall(id="c1", name="echo", arguments={"message": "x"})],
        usage=TokenUsage(input_tokens=5, output_tokens=5, total_tokens=10),
        model="mock",
    )
