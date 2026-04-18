"""OpenRouter provider adapter — OpenAI-compatible API via httpx."""

from __future__ import annotations

import json
import uuid
from typing import Any, AsyncIterator, Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.exceptions import ProviderError, RateLimitError
from core.task import TokenUsage
from observability.cost import estimate_cost
from observability.logging import get_logger
from observability.tracing import Span, get_tracer
from providers.base import CompletionResult, LLMProvider, StreamChunk, ToolCall

log = get_logger("providers.openrouter")

_BASE_URL = "https://openrouter.ai/api/v1"

# Groq model IDs → OpenRouter equivalents
_GROQ_TO_OPENROUTER: dict[str, str] = {
    "llama-3.3-70b-versatile":          "meta-llama/llama-3.3-70b-instruct",
    "llama-3.1-70b-versatile":          "meta-llama/llama-3.1-70b-instruct",
    "llama-3.1-8b-instant":             "meta-llama/llama-3.1-8b-instruct",
    "llama3-70b-8192":                  "meta-llama/llama-3-70b-instruct",
    "llama3-8b-8192":                   "meta-llama/llama-3-8b-instruct",
    "mixtral-8x7b-32768":               "mistralai/mixtral-8x7b-instruct",
    "gemma2-9b-it":                     "google/gemma-2-9b-it",
    "gemma-7b-it":                      "google/gemma-7b-it",
    "llama-3.2-90b-vision-preview":     "meta-llama/llama-3.2-90b-vision-instruct",
    "llama-3.2-11b-vision-preview":     "meta-llama/llama-3.2-11b-vision-instruct",
}


class OpenRouterAdapter(LLMProvider):
    """
    OpenRouter adapter (OpenAI-compatible endpoint).
    Supports tool-calling, streaming, and exponential-backoff retry on rate limits.
    """

    def __init__(self, api_key: str, default_model: str = "openai/gpt-4o-mini") -> None:
        self._api_key = api_key
        self._default_model = default_model
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/general-swarm",
            "X-Title": "General Swarm",
        }

    @property
    def name(self) -> str:
        return "openrouter"

    def _resolve_model(self, model: str) -> str:
        """Translate Groq-style model IDs to OpenRouter IDs if needed."""
        resolved = _GROQ_TO_OPENROUTER.get(model, model)
        if resolved != model:
            log.debug("model_remapped", from_model=model, to_model=resolved)
        return resolved

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
        model = self._resolve_model(model)
        tracer = get_tracer()

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if max_tokens:
            payload["max_tokens"] = max_tokens

        with Span(tracer, "openrouter.complete", "llm", model=model) as span:
            try:
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(
                        f"{_BASE_URL}/chat/completions",
                        headers=self._headers,
                        json=payload,
                    )

                if response.status_code == 429:
                    raise RateLimitError(f"OpenRouter rate limit: {response.text}")
                if response.status_code != 200:
                    raise ProviderError(f"OpenRouter error {response.status_code}: {response.text}")

                data = response.json()

            except (RateLimitError, ProviderError):
                raise
            except Exception as exc:
                log.error("openrouter_completion_error", model=model, error=str(exc))
                raise ProviderError(str(exc)) from exc

            choice = data["choices"][0]
            msg = choice["message"]

            # Parse tool calls
            tool_calls: list[ToolCall] = []
            if msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except (json.JSONDecodeError, KeyError):
                        args = {"raw": tc.get("function", {}).get("arguments", "")}
                    tool_calls.append(
                        ToolCall(
                            id=tc.get("id", str(uuid.uuid4())),
                            name=tc["function"]["name"],
                            arguments=args,
                        )
                    )

            raw_usage = data.get("usage", {})
            usage = TokenUsage(
                input_tokens=raw_usage.get("prompt_tokens", 0),
                output_tokens=raw_usage.get("completion_tokens", 0),
                total_tokens=raw_usage.get("total_tokens", 0),
            )
            cost = estimate_cost(usage, model)

            span.set(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                cost=cost,
                tool_calls=len(tool_calls),
                finish_reason=choice.get("finish_reason", "stop"),
            )

            log.debug(
                "openrouter_complete",
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cost_usd=round(cost, 6),
                tool_calls=len(tool_calls),
            )

            return CompletionResult(
                content=msg.get("content"),
                tool_calls=tool_calls,
                usage=usage,
                model=model,
                finish_reason=choice.get("finish_reason") or "stop",
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
        model = self._resolve_model(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    f"{_BASE_URL}/chat/completions",
                    headers=self._headers,
                    json=payload,
                ) as response:
                    if response.status_code == 429:
                        raise RateLimitError(f"OpenRouter rate limit: {response.status_code}")
                    if response.status_code != 200:
                        raise ProviderError(f"OpenRouter error {response.status_code}")

                    async for line in response.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        raw = line[6:]
                        if raw.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(raw)
                            delta = chunk["choices"][0]["delta"]
                            finish = chunk["choices"][0].get("finish_reason")
                            content = delta.get("content", "")
                            if content:
                                yield StreamChunk(delta=content, finish_reason=finish)
                        except (json.JSONDecodeError, KeyError, IndexError):
                            continue
        except (RateLimitError, ProviderError):
            raise
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

    def count_tokens(self, text: str, model: str) -> int:
        return max(1, len(text) // 4)

    def estimate_cost(self, usage: TokenUsage, model: str) -> float:
        return estimate_cost(usage, model)
