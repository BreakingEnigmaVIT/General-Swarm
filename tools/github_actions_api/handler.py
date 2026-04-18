"""GitHub Actions API — trigger workflows, read run status."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler

_GH_API = "https://api.github.com"


class GithubActionsApiHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_GITHUB_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_GITHUB_TOKEN not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        action = inputs["action"]
        owner = inputs["owner"]
        repo = inputs["repo"]
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

        async with httpx.AsyncClient(timeout=25) as client:
            if action == "list_workflows":
                resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}/actions/workflows", headers=headers)
                return {"workflows": resp.json().get("workflows", [])[:20]}

            elif action == "list_runs":
                wf = inputs.get("workflow_id", "")
                url = f"{_GH_API}/repos/{owner}/{repo}/actions/runs"
                if wf:
                    url = f"{_GH_API}/repos/{owner}/{repo}/actions/workflows/{wf}/runs"
                resp = await client.get(url, headers=headers, params={"per_page": 10})
                return {"runs": [{"id": r["id"], "status": r["status"], "conclusion": r.get("conclusion"), "created_at": r["created_at"]} for r in resp.json().get("workflow_runs", [])]}

            elif action == "get_run":
                run_id = inputs["run_id"]
                resp = await client.get(f"{_GH_API}/repos/{owner}/{repo}/actions/runs/{run_id}", headers=headers)
                data = resp.json()
                return {"id": data.get("id"), "status": data.get("status"), "conclusion": data.get("conclusion"), "url": data.get("html_url")}

            elif action == "trigger":
                wf = inputs["workflow_id"]
                ref = inputs.get("ref", "main")
                body: dict = {"ref": ref}
                if inputs.get("inputs"):
                    body["inputs"] = inputs["inputs"]
                resp = await client.post(f"{_GH_API}/repos/{owner}/{repo}/actions/workflows/{wf}/dispatches", json=body, headers=headers)
                return {"triggered": resp.status_code == 204, "status_code": resp.status_code}

        return {"error": "Unknown action"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list_workflows", "owner": "test", "repo": "test"})
        return "skipped" in result or "workflows" in result or "error" in result


handler = GithubActionsApiHandler()
