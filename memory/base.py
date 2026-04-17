"""Unified read/write/search interface for all three memory tiers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional


class MemoryInterface(ABC):
    """
    All three memory tiers expose the same interface.
    Which tier is used is determined by the tool/call-site, not the agent.
    """

    @abstractmethod
    async def write(self, key: str, value: Any, metadata: Optional[dict] = None) -> None: ...

    @abstractmethod
    async def read(self, key: str) -> Optional[Any]: ...

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]: ...

    @abstractmethod
    async def delete(self, key: str) -> None: ...

    @abstractmethod
    async def clear(self) -> None: ...
