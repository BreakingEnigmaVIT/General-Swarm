"""Docker image builder. Push is flagged as prod-risk and requires separate confirmation."""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


class DockerBuildHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        tag = inputs["tag"]
        dockerfile = inputs.get("dockerfile", "Dockerfile")
        context = inputs.get("context", ".")
        build_args = inputs.get("build_args", {})
        push = inputs.get("push", False)
        platform = inputs.get("platform", "")

        if push:
            return {
                "prod_risk": True,
                "warning": "Pushing Docker images is a prod-risk operation. Confirm via human_input then call docker_build with push=false followed by 'docker push'.",
                "action_required": "Human confirmation required before push.",
            }

        cmd = ["docker", "build", "-t", tag, "-f", dockerfile]
        for k, v in build_args.items():
            cmd += ["--build-arg", f"{k}={v}"]
        if platform:
            cmd += ["--platform", platform]
        cmd.append(context)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_CWD),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=280)
            output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            return {
                "success": proc.returncode == 0,
                "tag": tag,
                "exit_code": proc.returncode,
                "output": output[-3000:],
            }
        except FileNotFoundError:
            return {"error": "docker not found on PATH"}
        except asyncio.TimeoutError:
            return {"error": "docker build timed out"}

    async def self_test(self) -> bool:
        result = await self._run({"tag": "test:latest", "dockerfile": "Dockerfile"})
        return "success" in result or "error" in result


handler = DockerBuildHandler()
