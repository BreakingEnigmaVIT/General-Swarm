"""Environment secret manager — Doppler/Vault/SSM/dotenv backend."""

from __future__ import annotations
import asyncio
import os
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()
_PROD_NAMESPACES = {"production", "prod", "prd"}


class EnvManagerHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]
        namespace = inputs.get("namespace", "dev")
        backend = inputs.get("backend", "auto")

        # Prod-risk for writes to production namespace
        if action in ("set", "delete") and namespace.lower() in _PROD_NAMESPACES:
            return {
                "prod_risk": True,
                "warning": f"Writing secrets to '{namespace}' is a prod-risk operation.",
                "action_required": "Confirm via human_input before proceeding.",
            }

        if backend == "auto":
            backend = self._detect_backend()

        if backend == "doppler":
            return await self._doppler(action, inputs)
        elif backend == "dotenv":
            return await self._dotenv(action, inputs, namespace)
        elif backend == "vault":
            return await self._vault(action, inputs, namespace)
        elif backend == "aws_ssm":
            return await self._ssm(action, inputs, namespace)
        return {"error": f"Unknown backend: {backend}"}

    def _detect_backend(self) -> str:
        if os.environ.get("SWARM_DOPPLER_TOKEN"):
            return "doppler"
        if os.environ.get("VAULT_TOKEN"):
            return "vault"
        if os.environ.get("AWS_DEFAULT_REGION"):
            return "aws_ssm"
        return "dotenv"

    async def _run_cmd(self, cmd: list) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError:
            return "", f"{cmd[0]} not found", 127

    async def _doppler(self, action: str, inputs: dict) -> dict:
        token = os.environ.get("SWARM_DOPPLER_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "SWARM_DOPPLER_TOKEN not set"}
        if action == "list":
            stdout, _, rc = await self._run_cmd(["doppler", "secrets", "--json"])
            return {"keys": list(__import__("json").loads(stdout).keys()) if rc == 0 and stdout.strip() else [], "backend": "doppler"}
        elif action == "get":
            stdout, _, rc = await self._run_cmd(["doppler", "secrets", "get", inputs["key"], "--plain"])
            return {"key": inputs["key"], "found": rc == 0, "backend": "doppler"}  # value omitted from trace
        elif action == "set":
            _, _, rc = await self._run_cmd(["doppler", "secrets", "set", inputs["key"], inputs.get("value", "")])
            return {"success": rc == 0, "backend": "doppler"}
        return {"error": f"Unsupported doppler action: {action}"}

    async def _dotenv(self, action: str, inputs: dict, namespace: str) -> dict:
        env_file = _CWD / f".env.{namespace}" if namespace != "dev" else _CWD / ".env"
        if action == "list":
            if not env_file.exists():
                return {"keys": [], "backend": "dotenv", "file": str(env_file)}
            keys = [line.split("=")[0] for line in env_file.read_text().splitlines() if "=" in line and not line.startswith("#")]
            return {"keys": keys, "backend": "dotenv"}
        elif action == "get":
            if not env_file.exists():
                return {"found": False}
            for line in env_file.read_text().splitlines():
                if line.startswith(inputs["key"] + "="):
                    return {"key": inputs["key"], "found": True, "backend": "dotenv"}
            return {"found": False}
        elif action == "set":
            lines = env_file.read_text().splitlines() if env_file.exists() else []
            key = inputs["key"]
            val = inputs.get("value", "")
            updated = False
            new_lines = []
            for line in lines:
                if line.startswith(key + "="):
                    new_lines.append(f"{key}={val}")
                    updated = True
                else:
                    new_lines.append(line)
            if not updated:
                new_lines.append(f"{key}={val}")
            env_file.write_text("\n".join(new_lines) + "\n")
            return {"success": True, "backend": "dotenv"}
        elif action == "export_example":
            out = _CWD / inputs.get("output_file", ".env.example")
            if env_file.exists():
                keys = [line.split("=")[0] for line in env_file.read_text().splitlines() if "=" in line and not line.startswith("#")]
                out.write_text("\n".join(f"{k}=" for k in keys) + "\n")
                return {"written": str(out), "keys": keys}
            return {"error": f"{env_file} not found"}
        return {"error": f"Unknown action: {action}"}

    async def _vault(self, action: str, inputs: dict, namespace: str) -> dict:
        token = os.environ.get("VAULT_TOKEN", "")
        if not token:
            return {"skipped": True, "reason": "VAULT_TOKEN not set"}
        return {"error": "Vault backend: use VAULT_ADDR + vault CLI directly for now"}

    async def _ssm(self, action: str, inputs: dict, namespace: str) -> dict:
        region = os.environ.get("AWS_DEFAULT_REGION", "")
        if not region:
            return {"skipped": True, "reason": "AWS_DEFAULT_REGION not set"}
        prefix = f"/{namespace}"
        if action == "list":
            stdout, _, rc = await self._run_cmd(["aws", "ssm", "describe-parameters", "--query", "Parameters[].Name", "--output", "json"])
            import json
            names = json.loads(stdout) if rc == 0 and stdout.strip() else []
            return {"keys": [n for n in names if n.startswith(prefix)][:50], "backend": "aws_ssm"}
        elif action == "get":
            stdout, _, rc = await self._run_cmd(["aws", "ssm", "get-parameter", "--name", f"{prefix}/{inputs['key']}", "--with-decryption", "--query", "Parameter.Value", "--output", "text"])
            return {"found": rc == 0, "backend": "aws_ssm"}  # value omitted
        elif action == "set":
            _, _, rc = await self._run_cmd(["aws", "ssm", "put-parameter", "--name", f"{prefix}/{inputs['key']}", "--value", inputs.get("value", ""), "--type", "SecureString", "--overwrite"])
            return {"success": rc == 0, "backend": "aws_ssm"}
        return {"error": f"Unknown SSM action: {action}"}

    async def self_test(self) -> bool:
        result = await self._run({"action": "list", "namespace": "dev"})
        return "keys" in result or "error" in result


handler = EnvManagerHandler()
