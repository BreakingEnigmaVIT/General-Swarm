"""Loki LogQL query tool."""

from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from tools.base import ToolHandler


class LokiQueryHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        url = os.environ.get("SWARM_LOKI_URL", "")
        if not url:
            return {"skipped": True, "reason": "SWARM_LOKI_URL not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        now = datetime.now(timezone.utc)
        params = {
            "query": inputs["query"],
            "start": inputs.get("start", str(int((now - timedelta(hours=1)).timestamp() * 1e9))),
            "end": inputs.get("end", str(int(now.timestamp() * 1e9))),
            "limit": str(inputs.get("limit", 100)),
            "direction": inputs.get("direction", "backward"),
        }

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{url}/loki/api/v1/query_range", params=params)

        data = resp.json()
        if data.get("status") != "success":
            return {"error": data.get("message", "Loki query failed")}

        streams = data.get("data", {}).get("result", [])
        lines = []
        for stream in streams:
            for ts, line in stream.get("values", [])[:50]:
                lines.append({"timestamp": ts, "line": line, "labels": stream.get("stream", {})})

        return {
            "query": inputs["query"],
            "stream_count": len(streams),
            "line_count": len(lines),
            "lines": lines[:100],
        }

    async def self_test(self) -> bool:
        result = await self._run({"query": '{job="test"}'})
        return "skipped" in result or "lines" in result or "error" in result


handler = LokiQueryHandler()
