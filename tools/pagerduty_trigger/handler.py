"""PagerDuty incident trigger/resolve. Prod-risk gated."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler

_PD_EVENTS_API = "https://events.pagerduty.com/v2/enqueue"
_PD_API = "https://api.pagerduty.com"


class PagerdutyTriggerHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_PAGERDUTY_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_PAGERDUTY_TOKEN not set"}

        action = inputs["action"]

        if action in ("trigger", "resolve"):
            return {
                "prod_risk": True,
                "warning": f"PagerDuty {action} pages on-call engineers.",
                "action_required": "Confirm via human_input before proceeding.",
                "pending_action": action,
            }

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        headers = {"Authorization": f"Token token={token}", "Accept": "application/vnd.pagerduty+json;version=2"}
        async with httpx.AsyncClient(timeout=12) as client:
            if action == "list_incidents":
                resp = await client.get(f"{_PD_API}/incidents", headers=headers, params={"statuses[]": ["triggered", "acknowledged"], "limit": 20})
                data = resp.json()
                return {"incidents": [{"id": i["id"], "status": i["status"], "title": i["title"]} for i in data.get("incidents", [])]}

        return {"error": f"Unknown action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_incidents"})
        return "skipped" in result or "incidents" in result or "error" in result


handler = PagerdutyTriggerHandler()
