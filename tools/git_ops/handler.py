"""Git operations tool — scoped write permission, prod-risk flagged for main pushes."""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()
_PROD_BRANCHES = {"main", "master", "production", "prod"}


class GitOpsHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]
        wd = _CWD / inputs.get("working_dir", ".")

        if action == "status":
            return await self._git(["status", "--short"], wd)
        elif action == "log":
            return await self._git(["log", "--oneline", "-10"], wd)
        elif action == "diff":
            return await self._git(["diff", "--stat"], wd)
        elif action == "branch":
            name = inputs.get("branch_name", "")
            cmd = ["branch", name] if name else ["branch", "--list"]
            return await self._git(cmd, wd)
        elif action == "checkout":
            name = inputs["branch_name"]
            return await self._git(["checkout", "-B", name], wd)
        elif action == "pull":
            return await self._git(["pull"], wd)
        elif action == "add":
            files = inputs.get("files", ["."])
            return await self._git(["add"] + files, wd)
        elif action == "commit":
            msg = inputs.get("commit_message", "chore: automated commit")
            return await self._git(["commit", "-m", msg], wd)
        elif action == "push":
            remote = inputs.get("remote", "origin")
            branch = inputs.get("branch_name", "")
            cmd = ["push", remote]
            if branch:
                # Flag prod-risk for main/master
                if branch.lower() in _PROD_BRANCHES:
                    return {
                        "prod_risk": True,
                        "warning": f"Pushing to '{branch}' is a prod-risk operation. Human confirmation required.",
                        "action_required": "Approve via human_input tool before executing push.",
                    }
                cmd += [branch]
            return await self._git(cmd, wd)
        elif action == "pr_create":
            return await self._create_pr(inputs, wd)
        return {"error": f"Unknown action: {action}"}

    async def _git(self, args: list, cwd: Path) -> dict:
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            return {
                "success": proc.returncode == 0,
                "output": stdout.decode(errors="replace")[:5000],
                "stderr": stderr.decode(errors="replace")[:2000],
                "exit_code": proc.returncode,
            }
        except FileNotFoundError:
            return {"error": "git not found on PATH"}

    async def _create_pr(self, inputs: dict, wd: Path) -> dict:
        title = inputs.get("pr_title", "Automated PR")
        body = inputs.get("pr_body", "")
        base = inputs.get("base_branch", "main")
        # Use gh CLI if available
        result = await self._git(
            ["gh", "pr", "create", "--title", title, "--body", body, "--base", base],
            wd
        )
        return result

    async def self_test(self) -> bool:
        result = await self._run({"action": "status"})
        return "output" in result or "error" in result


handler = GitOpsHandler()
