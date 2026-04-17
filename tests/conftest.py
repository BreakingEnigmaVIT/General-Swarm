"""Shared fixtures for all test tiers."""

from __future__ import annotations

import asyncio

# Load .env before any test module is imported so GROQ_API_KEY is available
from dotenv import load_dotenv
load_dotenv()
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from configs.schema import AgentSpec, TerminationPolicy, ToolSpec
from core.task import Task, TaskConstraints
from providers.base import CompletionResult, LLMProvider, StreamChunk, ToolCall
from core.task import TokenUsage


# ── Mock LLM provider (deterministic, free) ───────────────────────────────────

class MockProvider(LLMProvider):
    """Returns scripted responses; never calls a real API."""

    def __init__(self, responses: list[CompletionResult] | None = None) -> None:
        self._responses = responses or []
        self._call_count = 0

    @property
    def name(self) -> str:
        return "mock"

    async def complete(self, messages, model, tools=None, temperature=0.7,
                       max_tokens=None, **kwargs) -> CompletionResult:
        if self._responses and self._call_count < len(self._responses):
            r = self._responses[self._call_count]
        else:
            r = CompletionResult(
                content="Mock response",
                tool_calls=[],
                usage=TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15),
                model=model,
            )
        self._call_count += 1
        return r

    async def stream(self, messages, model, tools=None, temperature=0.7,
                     max_tokens=None, **kwargs):
        yield StreamChunk(delta="Mock stream", finish_reason="stop")

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)

    def estimate_cost(self, usage: TokenUsage, model: str) -> float:
        return usage.total_tokens * 0.000001


@pytest.fixture
def mock_provider() -> MockProvider:
    return MockProvider()


@pytest.fixture
def mock_provider_with_tool_call() -> MockProvider:
    """Provider that first returns a tool call, then a final answer."""
    return MockProvider(responses=[
        CompletionResult(
            content=None,
            tool_calls=[ToolCall(id="call_1", name="echo", arguments={"message": "hello"})],
            usage=TokenUsage(input_tokens=20, output_tokens=10, total_tokens=30),
            model="mock",
        ),
        CompletionResult(
            content="Tool returned: hello",
            tool_calls=[],
            usage=TokenUsage(input_tokens=30, output_tokens=10, total_tokens=40),
            model="mock",
        ),
    ])


# ── Agent spec fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def echo_agent_spec() -> AgentSpec:
    return AgentSpec(
        name="echo",
        role="echo",
        system_prompt="You are a helpful assistant.",
        model="mock",
        tools=["echo"],
        termination=TerminationPolicy(max_iterations=5, max_tokens=1024),
    )


@pytest.fixture
def basic_task() -> Task:
    return Task(
        goal="Say hello world",
        constraints=TaskConstraints(max_iterations=5),
    )


# ── Tool spec fixture ─────────────────────────────────────────────────────────

@pytest.fixture
def echo_tool_spec() -> ToolSpec:
    return ToolSpec(
        name="echo",
        description="Echoes input",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_schema={
            "type": "object",
            "properties": {"echoed": {"type": "string"}},
        },
    )


# ── Bus fixture ───────────────────────────────────────────────────────────────

@pytest.fixture
def in_process_bus():
    from coordination.bus import InProcessBus
    return InProcessBus()
