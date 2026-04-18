"""Shared blackboard for P2P subswarms — append-only, versioned, scoped to one subswarm run."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from memory.base import MemoryInterface
from observability.logutil import get_logger

log = get_logger("memory.blackboard")


class BlackboardEntry:
    def __init__(self, key: str, value: Any, author_id: str, version: int) -> None:
        self.key = key
        self.value = value
        self.author_id = author_id
        self.version = version
        self.timestamp = time.time()


class Blackboard(MemoryInterface):
    """
    Append-only shared store for a subswarm.

    - Writes create a new versioned entry; old entries are kept for audit.
    - `read` returns the latest version for a key.
    - `search` does simple substring matching across all entries.
    - `snapshot` returns the complete history (used when subswarm dissolves).
    """

    def __init__(self, swarm_id: str) -> None:
        self._swarm_id = swarm_id
        self._entries: list[BlackboardEntry] = []
        self._version = 0
        self._lock = asyncio.Lock()

    # ── MemoryInterface impl ───────────────────────────────────────────────────

    async def write(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        async with self._lock:
            self._version += 1
            author = (metadata or {}).get("author_id", "unknown")
            self._entries.append(BlackboardEntry(key, value, author, self._version))
            log.debug("blackboard_write", swarm=self._swarm_id, key=key, v=self._version)

    async def read(self, key: str) -> Optional[Any]:
        # latest version for key
        for entry in reversed(self._entries):
            if entry.key == key:
                return entry.value
        return None

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = query.lower()
        seen: dict[str, BlackboardEntry] = {}
        for entry in reversed(self._entries):
            if entry.key not in seen:
                text = str(entry.value).lower() + entry.key.lower()
                if q in text:
                    seen[entry.key] = entry
            if len(seen) >= limit:
                break
        return [
            {"key": e.key, "value": e.value, "author": e.author_id, "version": e.version}
            for e in seen.values()
        ]

    async def delete(self, key: str) -> None:
        # Blackboard is append-only; tombstone instead
        await self.write(key, "__DELETED__", metadata={"tombstone": True})

    async def clear(self) -> None:
        async with self._lock:
            self._entries.clear()
            self._version = 0

    # ── Extra API ─────────────────────────────────────────────────────────────

    def snapshot(self) -> list[dict[str, Any]]:
        """Full audit history — called on subswarm dissolution."""
        return [
            {
                "key": e.key,
                "value": e.value,
                "author": e.author_id,
                "version": e.version,
                "ts": e.timestamp,
            }
            for e in self._entries
        ]

    def latest(self) -> dict[str, Any]:
        """Flat dict of latest values for all keys."""
        result: dict[str, Any] = {}
        for entry in reversed(self._entries):
            if entry.key not in result and entry.value != "__DELETED__":
                result[entry.key] = entry.value
        return result
