"""Per-agent ephemeral working memory.  Destroyed when the agent completes.

Auto-summarizes when approaching the configured token limit so the agent's
conversation history never exceeds context window size.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from memory.base import MemoryInterface
from observability.logging import get_logger

log = get_logger("memory.scratchpad")

_APPROX_TOKENS_PER_CHAR = 0.25


class Scratchpad(MemoryInterface):
    """
    Simple in-memory KV store + text buffer.

    .write / .read are for structured notes (dict).
    .append_message / .get_messages track the agent's conversation history.
    """

    def __init__(self, agent_id: str, max_tokens: int = 6000) -> None:
        self._agent_id = agent_id
        self._max_tokens = max_tokens
        self._store: dict[str, Any] = {}
        self._messages: list[dict[str, Any]] = []

    # ── MemoryInterface impl ───────────────────────────────────────────────────

    async def write(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        self._store[key] = {"value": value, "metadata": metadata or {}}

    async def read(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        return entry["value"] if entry else None

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = query.lower()
        results = []
        for k, v in self._store.items():
            text = json.dumps(v).lower()
            if q in text or q in k.lower():
                results.append({"key": k, **v})
        return results[:limit]

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def clear(self) -> None:
        self._store.clear()
        self._messages.clear()

    # ── Conversation history ──────────────────────────────────────────────────

    def append_message(self, message: dict[str, Any]) -> None:
        self._messages.append(message)
        self._maybe_compact()

    def get_messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def token_estimate(self) -> int:
        total = sum(len(json.dumps(m)) for m in self._messages)
        return int(total * _APPROX_TOKENS_PER_CHAR)

    def _maybe_compact(self) -> None:
        if self.token_estimate() < self._max_tokens:
            return
        # Keep system message + last 4 exchanges; summarise the rest
        system = [m for m in self._messages if m.get("role") == "system"]
        rest = [m for m in self._messages if m.get("role") != "system"]
        keep_tail = rest[-8:] if len(rest) > 8 else rest
        dropped = len(rest) - len(keep_tail)
        if dropped > 0:
            summary = {
                "role": "system",
                "content": (
                    f"[COMPACTED: {dropped} earlier messages omitted to stay within context limit.]"
                ),
            }
            self._messages = system + [summary] + keep_tail
            log.debug("scratchpad_compacted", agent_id=self._agent_id, dropped=dropped)
