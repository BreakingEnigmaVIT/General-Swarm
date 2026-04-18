"""Wikipedia lookup using the wikipedia-api package or httpx fallback."""

from __future__ import annotations
from typing import Any
from tools.base import ToolHandler


class WikiLookupHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        term = inputs["term"]
        sentences = int(inputs.get("sentences", 5))
        try:
            import wikipedia
            wikipedia.set_lang("en")
            try:
                page = wikipedia.page(term, auto_suggest=True)
                summary = wikipedia.summary(term, sentences=sentences, auto_suggest=True)
                return {"title": page.title, "summary": summary, "url": page.url}
            except wikipedia.DisambiguationError as e:
                options = e.options[:5]
                page = wikipedia.page(options[0], auto_suggest=False)
                summary = wikipedia.summary(options[0], sentences=sentences, auto_suggest=False)
                return {"title": page.title, "summary": summary, "url": page.url, "disambiguated": True}
            except wikipedia.PageError:
                return {"error": f"No Wikipedia page found for '{term}'"}
        except ImportError:
            return await self._httpx_fallback(term, sentences)

    async def _httpx_fallback(self, term: str, sentences: int) -> dict:
        try:
            import httpx
            url = "https://en.wikipedia.org/api/rest_v1/page/summary/" + term.replace(" ", "_")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url)
            if resp.status_code != 200:
                return {"error": f"Wikipedia API error {resp.status_code}"}
            data = resp.json()
            return {
                "title": data.get("title", ""),
                "summary": data.get("extract", "")[:sentences * 200],
                "url": data.get("content_urls", {}).get("desktop", {}).get("page", ""),
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def self_test(self) -> bool:
        result = await self._run({"term": "Python programming language", "sentences": 2})
        return "summary" in result or "error" in result


handler = WikiLookupHandler()
