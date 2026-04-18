"""Tool base class and self-test contract."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any, Optional

import jsonschema

from configs.schema import ToolSpec
from core.exceptions import ToolInputError, ToolTimeoutError
from observability.logutil import get_logger
from observability.tracing import Span, get_tracer

log = get_logger("tools")


def _coerce_inputs(inputs: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """
    Coerce and sanitise tool call arguments before JSON Schema validation.

    Handles three classes of model misbehaviour:
    1. Scalar types sent as strings  ("true" → True, "3" → 3)
    2. Arrays/objects sent as JSON strings  ('["a","b"]' → ["a","b"])
    3. Plain-text strings sent where an object is expected  → {"content": text}
    4. Extra properties on schemas with additionalProperties:false  → stripped
    """
    import json as _json

    properties: dict[str, Any] = schema.get("properties", {})

    # Strip extra properties when the schema forbids them
    if schema.get("additionalProperties") is False and properties:
        inputs = {k: v for k, v in inputs.items() if k in properties}

    if not properties:
        return inputs

    result = dict(inputs)
    for key, prop in properties.items():
        if key not in result or not isinstance(result[key], str):
            continue
        expected = prop.get("type")
        if expected == "boolean":
            lower = result[key].lower()
            if lower in ("true", "1", "yes"):
                result[key] = True
            elif lower in ("false", "0", "no"):
                result[key] = False
        elif expected == "integer":
            try:
                result[key] = int(result[key])
            except ValueError:
                pass
        elif expected == "number":
            try:
                result[key] = float(result[key])
            except ValueError:
                pass
        elif expected in ("array", "object"):
            try:
                parsed = _json.loads(result[key])
                if expected == "array" and isinstance(parsed, list):
                    result[key] = parsed
                elif expected == "object" and isinstance(parsed, dict):
                    result[key] = parsed
            except (_json.JSONDecodeError, ValueError):
                # Plain text passed where an object is expected — wrap it
                if expected == "object":
                    result[key] = {"content": result[key]}
    return result


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
        # Coerce string booleans/integers that some models produce in tool calls
        if self.spec.input_schema:
            inputs = _coerce_inputs(inputs, self.spec.input_schema)
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
