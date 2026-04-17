"""Safe math expression evaluator."""

from __future__ import annotations

import math
from typing import Any

from tools.base import ToolHandler

_SAFE_GLOBALS = {
    "__builtins__": {},
    **{k: v for k, v in math.__dict__.items() if not k.startswith("_")},
    "abs": abs, "round": round, "min": min, "max": max, "sum": sum,
    "int": int, "float": float, "pow": pow,
}


class CalculatorHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        expr = inputs["expression"]
        # Reject obvious injection attempts
        for banned in ["import", "__", "open", "exec", "eval", "os", "sys"]:
            if banned in expr:
                return {"error": f"Expression contains disallowed token: '{banned}'"}
        try:
            result = eval(expr, _SAFE_GLOBALS, {})  # noqa: S307
            return {"expression": expr, "result": result}
        except Exception as exc:
            return {"expression": expr, "error": str(exc)}

    async def self_test(self) -> bool:
        r = await self._run({"expression": "2 ** 10"})
        return r.get("result") == 1024


handler = CalculatorHandler()
