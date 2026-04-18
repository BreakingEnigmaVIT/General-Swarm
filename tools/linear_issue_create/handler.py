"""Linear issue creator. Gracefully skipped if SWARM_LINEAR_TOKEN is absent."""

from __future__ import annotations
import os
from typing import Any
from tools.base import ToolHandler

_LINEAR_API = "https://api.linear.app/graphql"


class LinearIssueCreateHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_LINEAR_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_LINEAR_TOKEN not set"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        title = inputs["title"]
        description = inputs.get("description", "")
        priority = inputs.get("priority", 3)
        team_id = inputs.get("team_id", "")
        issue_id = inputs.get("issue_id", "")

        headers = {"Authorization": token, "Content-Type": "application/json"}

        if issue_id:
            mutation = """
            mutation UpdateIssue($id: String!, $title: String!, $description: String) {
              issueUpdate(id: $id, input: {title: $title, description: $description}) {
                issue { id title url }
              }
            }"""
            variables = {"id": issue_id, "title": title, "description": description}
            key = "issueUpdate"
        else:
            mutation = """
            mutation CreateIssue($title: String!, $description: String, $teamId: String!, $priority: Int) {
              issueCreate(input: {title: $title, description: $description, teamId: $teamId, priority: $priority}) {
                issue { id title url }
              }
            }"""
            variables = {"title": title, "description": description, "teamId": team_id, "priority": priority}
            key = "issueCreate"

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(_LINEAR_API, json={"query": mutation, "variables": variables}, headers=headers)
            data = resp.json()
            issue = data.get("data", {}).get(key, {}).get("issue", {})
            if not issue:
                return {"error": str(data.get("errors", "Unknown Linear error"))}
            return {"issue_id": issue.get("id"), "url": issue.get("url"), "title": issue.get("title")}
        except Exception as exc:
            return {"error": str(exc)}

    async def self_test(self) -> bool:
        result = await self._run({"title": "Test issue"})
        return "skipped" in result or "issue_id" in result


handler = LinearIssueCreateHandler()
