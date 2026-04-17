"""Echo tool — trivial read-only tool used for testing the tool pipeline."""

from __future__ import annotations

from typing import Any

from tools.base import ToolHandler


class EchoHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {"echoed": inputs["message"]}

    async def self_test(self) -> bool:
        result = await self._run({"message": "hello"})
        return result == {"echoed": "hello"}


handler = EchoHandler()
