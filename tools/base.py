"""Tool base class and self-test contract."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

import jsonschema

from configs.schema import ToolSpec
from core.exceptions import ToolInputError, ToolTimeoutError
from observability.logging import get_logger
from observability.tracing import Span, get_tracer

log = get_logger("tools")


class ToolHandler(ABC):
    """
    All tools must subclass this and implement `_run`.

    The public `run()` method validates inputs, enforces the timeout,
    wraps the call in a trace span, and validates the output.
    """

    spec: ToolSpec  # set by registry after loading

    @abstractmethod
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]: ...

    async def run(self, inputs: dict[str, Any], agent_id: Optional[str] = None) -> dict[str, Any]:
        # Input validation
        if self.spec.input_schema:
            try:
                jsonschema.validate(inputs, self.spec.input_schema)
            except jsonschema.ValidationError as exc:
                raise ToolInputError(f"Tool '{self.spec.name}': {exc.message}") from exc

        tracer = get_tracer()
        with Span(tracer, f"tool.{self.spec.name}", "tool", agent_id=agent_id) as span:
            span.set(inputs=inputs)
            try:
                result = await asyncio.wait_for(
                    self._run(inputs), timeout=self.spec.timeout
                )
                span.set(output_keys=list(result.keys()))
                log.debug("tool_ok", tool=self.spec.name, agent_id=agent_id)
                return result
            except asyncio.TimeoutError:
                raise ToolTimeoutError(
                    f"Tool '{self.spec.name}' timed out after {self.spec.timeout}s"
                )

    async def self_test(self) -> bool:
        """Override to provide a test case. Return True on success."""
        return True

    def get_openai_schema(self) -> dict[str, Any]:
        return self.spec.to_openai_function()
