"""Intercom query tool with PII redaction."""

from __future__ import annotations
import os
import re
from typing import Any
from tools.base import ToolHandler

_INTERCOM_API = "https://api.intercom.io"

# PII patterns to redact
_PII_PATTERNS = [
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE]"),
    (re.compile(r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b"), "[CARD]"),
]


def _redact(text: str) -> str:
    for pattern, replacement in _PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_conversation(conv: dict) -> dict:
    """Redact PII from a conversation object."""
    safe = {}
    for k, v in conv.items():
        if k in ("author", "user", "contact") and isinstance(v, dict):
            # Remove identifying fields
            safe[k] = {kk: vv for kk, vv in v.items() if kk not in ("email", "name", "phone", "user_id")}
        elif isinstance(v, str):
            safe[k] = _redact(v)
        elif isinstance(v, list):
            safe[k] = [_redact(item) if isinstance(item, str) else item for item in v]
        else:
            safe[k] = v
    return safe


class IntercomQueryHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_INTERCOM_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_INTERCOM_TOKEN not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        action = inputs["action"]
        limit = min(int(inputs.get("limit", 20)), 50)
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

        async with httpx.AsyncClient(timeout=15) as client:
            if action == "list_conversations":
                resp = await client.get(f"{_INTERCOM_API}/conversations", headers=headers,
                                        params={"per_page": limit, "order": "desc"})
                convs = resp.json().get("conversations", [])
                return {"conversations": [_redact_conversation(c) for c in convs[:limit]], "count": len(convs)}

            elif action == "search_conversations":
                query = inputs.get("query", "")
                body = {"query": {"field": "body", "operator": "~", "value": query}}
                resp = await client.post(f"{_INTERCOM_API}/conversations/search", json=body, headers=headers)
                convs = resp.json().get("conversations", {}).get("data", [])
                return {"conversations": [_redact_conversation(c) for c in convs[:limit]], "count": len(convs)}

            elif action == "list_tags":
                resp = await client.get(f"{_INTERCOM_API}/tags", headers=headers)
                return {"tags": resp.json().get("data", [])}

        return {"error": f"Unknown action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_tags"})
        return "skipped" in result or "tags" in result or "error" in result


handler = IntercomQueryHandler()
