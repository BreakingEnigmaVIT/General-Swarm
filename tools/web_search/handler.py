"""Web search tool using DuckDuckGo (no API key required)."""

from __future__ import annotations

from typing import Any

from tools.base import ToolHandler


class WebSearchHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from duckduckgo_search import DDGS

        query = inputs["query"]
        max_results = int(inputs.get("max_results", 5))

        try:
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
        except Exception as exc:
            return {"results": [], "error": str(exc)}

        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in raw
        ]
        return {"results": results}

    async def self_test(self) -> bool:
        result = await self._run({"query": "Python programming language", "max_results": 2})
        return "results" in result


handler = WebSearchHandler()
