"""Retrieve from long-term memory."""

from __future__ import annotations

from typing import Any, Optional

from memory.longterm import LocalChromaMemory
from tools.base import ToolHandler

_memory: Optional[LocalChromaMemory] = None


def set_memory(mem: LocalChromaMemory) -> None:
    global _memory
    _memory = mem


class MemoryRetrieveHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _memory is None:
            return {"memories": [], "error": "Long-term memory not configured"}
        results = await _memory.search(inputs["query"], limit=int(inputs.get("limit", 5)))
        return {"memories": results}

    async def self_test(self) -> bool:
        return True


handler = MemoryRetrieveHandler()
