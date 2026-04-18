"""Supabase admin operations via the Management API or supabase-py."""

from __future__ import annotations

import os
from typing import Any

from tools.base import ToolHandler

_SUPABASE_API = "https://api.supabase.com/v1"


class SupabaseAdminHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]
        access_token = inputs.get("access_token") or os.environ.get("SUPABASE_ACCESS_TOKEN", "")
        project_ref = inputs.get("project_ref") or os.environ.get("SUPABASE_PROJECT_REF", "")

        if not access_token:
            return {"error": "SUPABASE_ACCESS_TOKEN not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=30, base_url=_SUPABASE_API) as client:
            if action == "list_projects":
                resp = await client.get("/projects", headers=headers)
                return self._handle(resp, "projects")

            elif action == "get_project":
                if not project_ref:
                    return {"error": "project_ref required"}
                resp = await client.get(f"/projects/{project_ref}", headers=headers)
                return self._handle(resp, "project")

            elif action == "list_tables":
                if not project_ref:
                    return {"error": "project_ref required"}
                resp = await client.get(
                    f"/projects/{project_ref}/database/tables",
                    headers=headers,
                )
                return self._handle(resp, "tables")

            elif action == "run_sql":
                if not project_ref:
                    return {"error": "project_ref required"}
                sql = inputs.get("sql", "")
                if not sql:
                    return {"error": "sql required"}
                resp = await client.post(
                    f"/projects/{project_ref}/database/query",
                    headers=headers,
                    json={"query": sql},
                )
                return self._handle(resp, "result")

            elif action == "list_functions":
                if not project_ref:
                    return {"error": "project_ref required"}
                resp = await client.get(f"/projects/{project_ref}/functions", headers=headers)
                return self._handle(resp, "functions")

            elif action == "get_project_settings":
                if not project_ref:
                    return {"error": "project_ref required"}
                resp = await client.get(
                    f"/projects/{project_ref}/config/database",
                    headers=headers,
                )
                return self._handle(resp, "settings")

            elif action == "list_secrets":
                if not project_ref:
                    return {"error": "project_ref required"}
                resp = await client.get(f"/projects/{project_ref}/secrets", headers=headers)
                return self._handle(resp, "secrets")

        return {"error": f"Unknown action: {action}"}

    def _handle(self, resp: Any, key: str) -> dict:
        if resp.status_code >= 400:
            return {"error": f"Supabase API error {resp.status_code}", "body": resp.text[:500]}
        try:
            data = resp.json()
        except Exception:
            return {"error": "Non-JSON response", "body": resp.text[:500]}
        if isinstance(data, list):
            return {key: data, "count": len(data)}
        return {key: data}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_projects"})
        return "error" in result or "projects" in result


handler = SupabaseAdminHandler()
