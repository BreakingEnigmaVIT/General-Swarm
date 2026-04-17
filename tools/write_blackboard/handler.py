"""Write to the shared subswarm blackboard (injected at runtime)."""

from __future__ import annotations

from typing import Any, Optional

from memory.blackboard import Blackboard
from tools.base import ToolHandler

_blackboard: Optional[Blackboard] = None


def set_blackboard(bb: Blackboard) -> None:
    global _blackboard
    _blackboard = bb


class WriteBlackboardHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _blackboard is None:
            return {"written": False, "error": "No active blackboard"}
        await _blackboard.write(inputs["key"], inputs["value"])
        return {"written": True}

    async def self_test(self) -> bool:
        return True


handler = WriteBlackboardHandler()
