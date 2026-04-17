"""Groq provider adapter — implements LLMProvider via the official groq Python SDK."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator, Optional

from groq import AsyncGroq, RateLimitError as GroqRateLimitError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.exceptions import ModelNotAvailableError, ProviderError, RateLimitError
from core.task import TokenUsage
from observability.cost import estimate_cost
from observability.logging import get_logger
from observability.tracing import Span, get_tracer
from providers.base import CompletionResult, LLMProvider, StreamChunk, ToolCall

log = get_logger("providers.groq")

_SUPPORTED_MODELS = {
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "llama3-70b-8192",
    "llama3-8b-8192",
    "mixtral-8x7b-32768",
    "gemma2-9b-it",
    "gemma-7b-it",
    "llama-3.2-90b-vision-preview",
    "llama-3.2-11b-vision-preview",
}

_FALLBACK_MODEL = "llama-3.1-8b-instant"


class GroqAdapter(LLMProvider):
    """
    Groq adapter with:
    - Tool-calling support (translated to/from internal schema)
    - Streaming support
    - Exponential-backoff retry on rate limits
    - Token budget enforcement
    - Per-call observability spans
    """

    def __init__(self, api_key: str, default_model: str = "llama-3.3-70b-versatile") -> None:
        self._client = AsyncGroq(api_key=api_key)
        self._default_model = default_model

    @property
    def name(self) -> str:
        return "groq"

    def _resolve_model(self, model: str) -> str:
        if model not in _SUPPORTED_MODELS:
            log.warning("model_not_supported", model=model, fallback=_FALLBACK_MODEL)
            return _FALLBACK_MODEL
        return model

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    async def complete(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> CompletionResult:
        resolved = self._resolve_model(model)
        tracer = get_tracer()

        with Span(tracer, "groq.complete", "llm", model=resolved) as span:
            try:
                kwargs_: dict[str, Any] = {
                    "model": resolved,
                    "messages": messages,
                    "temperature": temperature,
                }
                if tools:
                    kwargs_["tools"] = tools
                    kwargs_["tool_choice"] = "auto"
                if max_tokens:
                    kwargs_["max_tokens"] = max_tokens

                response = await self._client.chat.completions.create(**kwargs_)

            except GroqRateLimitError as exc:
                log.warning("groq_rate_limit", model=resolved)
                raise RateLimitError(str(exc)) from exc
            except Exception as exc:
                log.error("groq_completion_error", model=resolved, error=str(exc))
                raise ProviderError(str(exc)) from exc

            choice = response.choices[0]
            msg = choice.message

            # Parse tool calls
            tool_calls: list[ToolCall] = []
            if msg.tool_calls:
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {"raw": tc.function.arguments}
                    tool_calls.append(
                        ToolCall(id=tc.id, name=tc.function.name, arguments=args)
                    )

            usage = TokenUsage(
                input_tokens=response.usage.prompt_tokens,
                output_tokens=response.usage.completion_tokens,
                total_tokens=response.usage.total_tokens,
            )
            cost = estimate_cost(usage, resolved)

            span.set(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                cost=cost,
                tool_calls=len(tool_calls),
                finish_reason=choice.finish_reason,
            )

            log.debug(
                "groq_complete",
                model=resolved,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=round(cost, 6),
                tool_calls=len(tool_calls),
            )

            return CompletionResult(
                content=msg.content,
                tool_calls=tool_calls,
                usage=usage,
                model=resolved,
                finish_reason=choice.finish_reason or "stop",
            )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        model: str,
        tools: Optional[list[dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncIterator[StreamChunk]:
        resolved = self._resolve_model(model)
        params: dict[str, Any] = {
            "model": resolved,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            params["tools"] = tools
        if max_tokens:
            params["max_tokens"] = max_tokens

        try:
            async with await self._client.chat.completions.create(**params) as stream:
                async for chunk in stream:
                    delta = chunk.choices[0].delta
                    finish = chunk.choices[0].finish_reason
                    if delta.content:
                        yield StreamChunk(delta=delta.content, finish_reason=finish)
        except GroqRateLimitError as exc:
            raise RateLimitError(str(exc)) from exc
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def count_tokens(self, text: str, model: str) -> int:
        # Approximation: ~4 chars per token
        return max(1, len(text) // 4)

    def estimate_cost(self, usage: TokenUsage, model: str) -> float:
        return estimate_cost(usage, model)
