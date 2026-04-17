"""Echo tool — trivial read-only tool used for testing the tool pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import ToolHandler


class EchoHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"echoed": inputs["message"]}

    async def self_test(self) -> bool:
        result = await self._run({"message": "hello"})
        return result == {"echoed": "hello"}


handler = EchoHandler()

# Pre-load spec so the module-level handler is usable without going through the registry
_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec
    handler.spec = load_tool_spec(_spec_path)
