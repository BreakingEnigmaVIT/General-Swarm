"""Tavily search tool. Falls back to DuckDuckGo when SWARM_TAVILY_API_KEY is absent."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler


class TavilySearchHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        query = inputs["query"]
        max_results = int(inputs.get("max_results", 5))
        api_key = os.environ.get("SWARM_TAVILY_API_KEY", "")

        if api_key:
            return await self._tavily(query, max_results, api_key, inputs)
        return await self._ddg_fallback(query, max_results)

    async def _tavily(self, query: str, max_results: int, api_key: str, inputs: dict) -> dict:
        try:
            from tavily import TavilyClient
            client = TavilyClient(api_key=api_key)
            depth = inputs.get("search_depth", "basic")
            domains = inputs.get("include_domains", [])
            resp = client.search(
                query=query,
                max_results=max_results,
                search_depth=depth,
                include_domains=domains or None,
            )
            results = [
                {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
                for r in resp.get("results", [])
            ]
            return {"results": results, "source": "tavily"}
        except ImportError:
            return await self._ddg_fallback(query, max_results)
        except Exception as exc:
            return await self._ddg_fallback(query, max_results)

    async def _ddg_fallback(self, query: str, max_results: int) -> dict:
        try:
            from duckduckgo_search import DDGS
            with DDGS() as ddgs:
                raw = list(ddgs.text(query, max_results=max_results))
            results = [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")} for r in raw]
            return {"results": results, "source": "duckduckgo_fallback"}
        except Exception as exc:
            return {"results": [], "error": str(exc)}

    async def self_test(self) -> bool:
        result = await self._run({"query": "Python programming", "max_results": 2})
        return "results" in result


handler = TavilySearchHandler()
