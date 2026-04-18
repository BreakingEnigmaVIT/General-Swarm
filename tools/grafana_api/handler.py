"""Grafana API — list/read dashboards, create is prod-risk gated."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler


class GrafanaApiHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        url = os.environ.get("SWARM_GRAFANA_URL", "")
        token = os.environ.get("SWARM_GRAFANA_TOKEN", "")
        if not url:
            return {"skipped": True, "reason": "SWARM_GRAFANA_URL not set"}

        action = inputs["action"]
        if action == "create_dashboard":
            return {
                "prod_risk": True,
                "warning": "Creating Grafana dashboards modifies shared observability infrastructure.",
                "action_required": "Confirm via human_input before proceeding.",
            }

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        headers = {"Authorization": f"Bearer {token}"} if token else {}

        async with httpx.AsyncClient(timeout=15) as client:
            if action == "list_dashboards":
                resp = await client.get(f"{url}/api/search", headers=headers, params={"type": "dash-db", "limit": 20})
                return {"dashboards": [{"uid": d.get("uid"), "title": d.get("title"), "url": d.get("url")} for d in resp.json()[:20]]}

            elif action == "get_dashboard":
                uid = inputs.get("uid", "")
                resp = await client.get(f"{url}/api/dashboards/uid/{uid}", headers=headers)
                data = resp.json()
                return {"title": data.get("dashboard", {}).get("title"), "uid": uid, "panels": len(data.get("dashboard", {}).get("panels", []))}

            elif action == "search":
                resp = await client.get(f"{url}/api/search", headers=headers, params={"query": inputs.get("query", ""), "limit": 20})
                return {"results": resp.json()[:20]}

        return {"error": f"Unknown action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_dashboards"})
        return "skipped" in result or "dashboards" in result or "error" in result


handler = GrafanaApiHandler()
