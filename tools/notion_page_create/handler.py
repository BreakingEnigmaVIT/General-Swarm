"""Notion page creator. Gracefully skipped if SWARM_NOTION_TOKEN is absent."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler

_NOTION_API = "https://api.notion.com/v1"
_NOTION_VERSION = "2022-06-28"


class NotionPageCreateHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_NOTION_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_NOTION_TOKEN not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        title = inputs["title"]
        content = inputs.get("content", "")
        parent_id = inputs.get("parent_page_id", "")
        page_id = inputs.get("page_id", "")

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Notion-Version": _NOTION_VERSION,
        }

        # Convert content to Notion paragraph blocks (simplified)
        blocks = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": para[:2000]}}]
                }
            }
            for para in content.split("\n\n") if para.strip()
        ][:100]  # Notion API limit

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if page_id:
                    # Update existing page title
                    resp = await client.patch(
                        f"{_NOTION_API}/pages/{page_id}",
                        json={"properties": {"title": {"title": [{"text": {"content": title}}]}}},
                        headers=headers,
                    )
                else:
                    if not parent_id:
                        return {"error": "parent_page_id is required when creating a new page"}
                    payload = {
                        "parent": {"page_id": parent_id},
                        "properties": {"title": {"title": [{"text": {"content": title}}]}},
                        "children": blocks,
                    }
                    resp = await client.post(f"{_NOTION_API}/pages", json=payload, headers=headers)

            data = resp.json()
            if resp.status_code >= 400:
                return {"error": data.get("message", str(resp.status_code))}
            return {"page_id": data.get("id"), "url": data.get("url"), "title": title}
        except Exception as exc:
            return {"error": str(exc)}

    async def self_test(self) -> bool:
        result = await self._run({"title": "Test", "content": "Hello"})
        return "skipped" in result or "page_id" in result


handler = NotionPageCreateHandler()
