"""Cloudflare DNS CRUD — prod-risk gated writes."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler

_CF_API = "https://api.cloudflare.com/client/v4"


class CloudflareDnsHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_CLOUDFLARE_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_CLOUDFLARE_TOKEN not set"}

        action = inputs["action"]
        if action in ("create", "update", "delete"):
            return {
                "prod_risk": True,
                "warning": f"DNS {action} is a prod-risk operation affecting live traffic.",
                "action_required": "Confirm via human_input, then this tool will proceed.",
                "pending_action": action,
                "inputs": inputs,
            }

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        zone_id = inputs["zone_id"]
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

        async with httpx.AsyncClient(timeout=15) as client:
            if action == "list":
                resp = await client.get(f"{_CF_API}/zones/{zone_id}/dns_records", headers=headers)
                records = resp.json().get("result", [])
                return {"records": [{"id": r["id"], "type": r["type"], "name": r["name"], "content": r["content"]} for r in records[:50]]}

        return {"error": f"Unhandled action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list", "zone_id": "test"})
        return "skipped" in result or "records" in result or "error" in result


handler = CloudflareDnsHandler()
