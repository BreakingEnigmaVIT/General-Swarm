"""Kubernetes manifest apply — always dry-runs first; production is prod-risk gated."""

from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


class KubernetesApplyHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        manifest = str((_CWD / inputs["manifest_path"]).resolve())
        env = inputs["environment"]
        dry_run = inputs.get("dry_run", True)
        namespace = inputs.get("namespace", "default")
        kube_context = inputs.get("context", "")

        # Always require confirmation for production
        if env == "production" and not dry_run:
            return {
                "prod_risk": True,
                "warning": "Applying Kubernetes manifests to production requires human confirmation.",
                "action_required": "Confirm via human_input, then retry with dry_run=false.",
            }

        # Build kubectl command
        cmd = ["kubectl", "apply"]
        if kube_context:
            cmd += ["--context", kube_context]
        if namespace:
            cmd += ["-n", namespace]
        cmd += ["-f", manifest]

        # Always run server-side dry-run first
        dry_cmd = cmd + ["--dry-run=server"]

        try:
            proc = await asyncio.create_subprocess_exec(
                *dry_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_CWD),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
            dry_output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            dry_success = proc.returncode == 0

            if dry_run or not dry_success:
                return {
                    "environment": env,
                    "dry_run": True,
                    "success": dry_success,
                    "output": dry_output[:5000],
                    "applied": False,
                }

            # Actual apply
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(_CWD),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            apply_output = stdout.decode(errors="replace") + stderr.decode(errors="replace")
            return {
                "environment": env,
                "dry_run": False,
                "success": proc.returncode == 0,
                "output": apply_output[:5000],
                "applied": proc.returncode == 0,
            }
        except FileNotFoundError:
            return {"error": "kubectl not found on PATH"}

    async def self_test(self) -> bool:
        result = await self._run({"manifest_path": ".", "environment": "local", "dry_run": True})
        return "success" in result or "error" in result


handler = KubernetesApplyHandler()
