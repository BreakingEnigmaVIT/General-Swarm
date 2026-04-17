"""Abstract LLM provider interface — all agent code calls this, never a SDK directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

from pydantic import BaseModel

from core.task import TokenUsage


# ── Data models ───────────────────────────────────────────────────────────────

class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class CompletionResult(BaseModel):
    content: Optional[str] = None
    tool_calls: list[ToolCall] = []
    usage: TokenUsage = TokenUsage()
    model: str = ""
    finish_reason: str = "stop"


class StreamChunk(BaseModel):
    delta: str
    finish_reason: Optional[str] = None


# ── Interface ─────────────────────────────────────────────────────────────────

class LLMProvider(ABC):
    """
    Every provider adapter must implement this interface.
    All methods are async; the interface is transport-agnostic.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> CompletionResult: ...

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]: ...

    @abstractmethod
    def count_tokens(self, text: str, model: str) -> int: ...

    @abstractmethod
    def estimate_cost(self, usage: TokenUsage, model: str) -> float: ...
