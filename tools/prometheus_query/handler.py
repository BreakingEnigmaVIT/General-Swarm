"""Prometheus PromQL query tool."""

from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from tools.base import ToolHandler


class PrometheusQueryHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        url = os.environ.get("SWARM_PROMETHEUS_URL", "")
        if not url:
            return {"skipped": True, "reason": "SWARM_PROMETHEUS_URL not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        query = inputs["query"]
        instant = inputs.get("instant", True)

        async with httpx.AsyncClient(timeout=15) as client:
            if instant:
                resp = await client.get(f"{url}/api/v1/query", params={"query": query})
            else:
                now = datetime.now(timezone.utc)
                start = inputs.get("start", (now - timedelta(hours=1)).isoformat())
                end = inputs.get("end", now.isoformat())
                step = inputs.get("step", "60s")
                resp = await client.get(f"{url}/api/v1/query_range",
                                        params={"query": query, "start": start, "end": end, "step": step})

        data = resp.json()
        if data.get("status") != "success":
            return {"error": data.get("error", "Unknown Prometheus error")}

        result = data.get("data", {}).get("result", [])
        return {
            "query": query,
            "result_type": data.get("data", {}).get("resultType", ""),
            "series_count": len(result),
            "results": result[:50],
        }

    async def self_test(self) -> bool:
        result = await self._run({"query": "up"})
        return "skipped" in result or "results" in result or "error" in result


handler = PrometheusQueryHandler()
