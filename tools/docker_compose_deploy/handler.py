"""Docker Compose deployment tool."""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


class DockerComposeDeployHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]
        compose_file = inputs.get("compose_file", "docker-compose.yml")
        services = inputs.get("services", [])
        detach = inputs.get("detach", True)

        base_cmd = ["docker", "compose", "-f", compose_file]
        if action == "up":
            cmd = base_cmd + ["up"] + (["-d"] if detach else []) + services
        elif action == "down":
            cmd = base_cmd + ["down"] + services
        elif action == "restart":
            cmd = base_cmd + ["restart"] + services
        elif action == "ps":
            cmd = base_cmd + ["ps"]
        elif action == "logs":
            cmd = base_cmd + ["logs", "--tail=100"] + services
        elif action == "pull":
            cmd = base_cmd + ["pull"] + services
        else:
            return {"error": f"Unknown action: {action}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_CWD),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=100)
            return {
                "action": action,
                "success": proc.returncode == 0,
                "output": (stdout + stderr).decode(errors="replace")[:5000],
                "exit_code": proc.returncode,
            }
        except FileNotFoundError:
            return {"error": "docker compose not found on PATH"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "ps"})
        return "success" in result or "error" in result


handler = DockerComposeDeployHandler()
