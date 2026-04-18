"""Execute shell commands with working-directory jail and command denylist."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError
from tools.base import ToolHandler

_CWD = Path.cwd()

_DENYLIST = [
    "rm -rf /",
    "sudo",
    "mkfs",
    "dd if=",
    ":(){:|:&};:",  # fork bomb
    "chmod 777 /",
    "wget http",
    "curl http",
    "taskkill /im ",   # kills ALL processes by image name — use /pid instead
    "taskkill /f /im ", # same with force flag
    "killall ",        # kills all processes matching a name — use kill <pid> instead
    "pkill ",          # kills all processes matching a pattern — use kill <pid> instead
]


def _check_command(cmd: str) -> None:
    lower = cmd.lower()
    for blocked in _DENYLIST:
        if blocked in lower:
            raise SafetyError(f"Command blocked by denylist: contains '{blocked}'")


class ShellExecHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        command = inputs["command"]
        timeout = float(inputs.get("timeout", 30))
        working_dir = inputs.get("working_dir", ".")

        _check_command(command)

        # Jail working directory
        exec_dir = (_CWD / working_dir).resolve()
        if not str(exec_dir).startswith(str(_CWD)):
            raise SafetyError("working_dir escapes project root")

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(exec_dir),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return {
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
                "returncode": proc.returncode,
            }
        except asyncio.TimeoutError:
            proc.kill()
            return {"stdout": "", "stderr": "Command timed out", "returncode": -1}
        except Exception as exc:
            return {"stdout": "", "stderr": str(exc), "returncode": -1}

    async def self_test(self) -> bool:
        r = await self._run({"command": "echo hello"})
        return r["returncode"] == 0 and "hello" in r["stdout"]


handler = ShellExecHandler()
