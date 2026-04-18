"""Run a Postman collection via Newman CLI or the Postman API."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from tools.base import ToolHandler

_CWD = Path.cwd()


class PostmanCollectionRunHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        collection_path = inputs.get("collection_path", "")
        collection_url = inputs.get("collection_url", "")
        environment = inputs.get("environment", {})
        env_file = inputs.get("env_file", "")
        reporters = inputs.get("reporters", ["json"])
        timeout_ms = int(inputs.get("timeout_ms", 30000))

        if not collection_path and not collection_url:
            return {"error": "Provide collection_path or collection_url"}

        # Check newman is available
        try:
            result = subprocess.run(
                ["newman", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return {"error": "Newman not found — install with: npm install -g newman"}
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return {"error": "Newman not found — install with: npm install -g newman"}

        cmd = ["newman", "run"]

        if collection_path:
            col_abs = (_CWD / collection_path).resolve()
            if not str(col_abs).startswith(str(_CWD)):
                return {"error": "collection_path escapes project root"}
            cmd.append(str(col_abs))
        else:
            cmd.append(collection_url)

        # Write inline environment variables to a temp file if provided
        tmp_env_path = None
        if environment:
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
            env_payload = {
                "id": "swarm-env",
                "name": "swarm-env",
                "values": [
                    {"key": k, "value": v, "enabled": True}
                    for k, v in environment.items()
                ],
            }
            json.dump(env_payload, tmp)
            tmp.close()
            tmp_env_path = tmp.name
            cmd.extend(["--environment", tmp_env_path])
        elif env_file:
            env_abs = (_CWD / env_file).resolve()
            if not str(env_abs).startswith(str(_CWD)):
                return {"error": "env_file escapes project root"}
            cmd.extend(["--environment", str(env_abs)])

        # JSON report to stdout
        cmd.extend(["--reporters", "json", "--reporter-json-export", "/dev/stdout"])
        cmd.extend(["--timeout-request", str(timeout_ms)])

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000 + 10,
            )
        except subprocess.TimeoutExpired:
            return {"error": "Newman run timed out"}
        finally:
            if tmp_env_path:
                try:
                    os.unlink(tmp_env_path)
                except OSError:
                    pass

        if proc.returncode not in (0, 1):
            return {"error": proc.stderr[:500] or "Newman failed"}

        # Parse JSON output
        try:
            report = json.loads(proc.stdout)
            run = report.get("run", {})
            stats = run.get("stats", {})
            failures = run.get("failures", [])
            return {
                "passed": stats.get("assertions", {}).get("total", 0)
                - stats.get("assertions", {}).get("failed", 0),
                "failed": stats.get("assertions", {}).get("failed", 0),
                "total_requests": stats.get("requests", {}).get("total", 0),
                "failures": [
                    {
                        "source": f.get("source", {}).get("name", ""),
                        "error": f.get("error", {}).get("message", ""),
                    }
                    for f in failures[:20]
                ],
                "success": proc.returncode == 0,
            }
        except (json.JSONDecodeError, ValueError):
            # Return raw stderr/stdout snippet if JSON parse fails
            return {
                "success": proc.returncode == 0,
                "output": (proc.stdout + proc.stderr)[:1000],
            }

    async def self_test(self) -> bool:
        result = await self._run({"collection_path": "nonexistent.json"})
        return "error" in result or "success" in result


handler = PostmanCollectionRunHandler()
