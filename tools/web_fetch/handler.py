"""Fetch and clean a web page."""

from __future__ import annotations

from typing import Any

from tools.base import ToolHandler


class WebFetchHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        import httpx
        from bs4 import BeautifulSoup

        url = inputs["url"]
        max_chars = int(inputs.get("max_chars", 8000))

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; SwarmBot/1.0; +https://github.com/swarm)"
            )
        }

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=25.0) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                html = resp.text
        except Exception as exc:
            return {"url": url, "title": "", "content": "", "char_count": 0, "error": str(exc)}

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()

        title = soup.title.string.strip() if soup.title else ""
        text = soup.get_text(separator="\n", strip=True)
        # Collapse blank lines
        lines = [l for l in text.splitlines() if l.strip()]
        content = "\n".join(lines)[:max_chars]

        return {
            "url": url,
            "title": title,
            "content": content,
            "char_count": len(content),
        }

    async def self_test(self) -> bool:
        return True  # Skip network call in unit tests


handler = WebFetchHandler()
