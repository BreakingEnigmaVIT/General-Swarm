"""Uptime Kuma status queries via REST API."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler


class UptimeKumaApiHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        base_url = os.environ.get("SWARM_UPTIME_KUMA_URL", "")
        api_key = os.environ.get("SWARM_UPTIME_KUMA_API_KEY", "")
        if not base_url:
            return {"skipped": True, "reason": "SWARM_UPTIME_KUMA_URL not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        action = inputs["action"]
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

        async with httpx.AsyncClient(timeout=12) as client:
            if action == "list_monitors":
                resp = await client.get(f"{base_url}/api/v1/monitor", headers=headers)
                monitors = resp.json() if resp.status_code == 200 else []
                return {"monitors": monitors[:50] if isinstance(monitors, list) else monitors}

            elif action == "get_monitor":
                mid = inputs.get("monitor_id")
                if not mid:
                    return {"error": "monitor_id required"}
                resp = await client.get(f"{base_url}/api/v1/monitor/{mid}", headers=headers)
                return {"monitor": resp.json()}

            elif action == "heartbeat_list":
                mid = inputs.get("monitor_id")
                resp = await client.get(f"{base_url}/api/v1/monitor/{mid}/beats", headers=headers)
                return {"heartbeats": resp.json()[:20] if resp.status_code == 200 else []}

        return {"error": f"Unknown action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_monitors"})
        return "skipped" in result or "monitors" in result or "error" in result


handler = UptimeKumaApiHandler()
