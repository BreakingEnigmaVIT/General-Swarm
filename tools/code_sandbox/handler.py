"""Sandboxed code execution — isolated subprocess with timeout and workspace jail."""

from __future__ import annotations
import asyncio
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from tools.base import ToolHandler
from core.exceptions import SafetyError

_CWD = Path.cwd()
_RUNTIME_CMD = {
    "python": [sys.executable],
    "node": ["node"],
    "go": ["go", "run"],
    "bash": ["bash", "-e"],
}


class CodeSandboxHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        runtime = inputs["runtime"]
        code = inputs["code"]
        timeout = int(inputs.get("timeout_seconds", 30))
        stdin_data = inputs.get("stdin_data", None)

        working_dir = _CWD
        if inputs.get("working_dir"):
            wd = (_CWD / inputs["working_dir"]).resolve()
            if not str(wd).startswith(str(_CWD)):
                raise SafetyError("working_dir escapes project root")
            working_dir = wd

        cmd = _RUNTIME_CMD.get(runtime)
        if cmd is None:
            return {"error": f"Unsupported runtime '{runtime}'"}

        # Write code to temp file
        suffix = {"python": ".py", "node": ".js", "go": ".go", "bash": ".sh"}.get(runtime, ".tmp")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, dir=working_dir, delete=False, encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = Path(f.name)

        try:
            full_cmd = cmd + [str(tmp_path)]
            proc = await asyncio.create_subprocess_exec(
                *full_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE if stdin_data else None,
                cwd=str(working_dir),
            )
            try:
                stdin_bytes = stdin_data.encode() if stdin_data else None
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(input=stdin_bytes), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return {"error": f"Timed out after {timeout}s", "exit_code": -1}

            return {
                "stdout": stdout.decode(errors="replace")[:10000],
                "stderr": stderr.decode(errors="replace")[:5000],
                "exit_code": proc.returncode,
                "success": proc.returncode == 0,
            }
        except FileNotFoundError:
            return {"error": f"Runtime '{runtime}' not found on PATH"}
        finally:
            tmp_path.unlink(missing_ok=True)

    async def self_test(self) -> bool:
        result = await self._run({"code": "print('hello')", "runtime": "python"})
        return result.get("stdout", "").strip() == "hello"


handler = CodeSandboxHandler()
