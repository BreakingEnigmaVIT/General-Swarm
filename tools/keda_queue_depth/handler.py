"""KEDA queue depth probe (placeholder)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import ToolHandler


class KedaQueueDepthHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "role": inputs["role"],
            "depth": 0,
            "dlq_depth": 0,
            "message": "keda_queue_depth stub",
        }

    async def self_test(self) -> bool:
        r = await self._run({"role": "coder"})
        return "depth" in r


handler = KedaQueueDepthHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
