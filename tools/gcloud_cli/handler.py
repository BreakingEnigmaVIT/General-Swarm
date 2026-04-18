"""Thin allowlisted wrapper around the gcloud CLI — no arbitrary shell."""

from __future__ import annotations

import asyncio
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from core.exceptions import ToolInputError
from observability.logutil import get_logger
from tools.base import ToolHandler

log = get_logger("tools.gcloud_cli")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

ALLOWLISTED_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "run",
        "container",
        "compute",
        "iam",
        "projects",
        "services",
        "auth",
        "config",
        "dns",
        "storage",
        "secrets",
        "monitoring",
        "logging",
        "artifacts",
        "builds",
        "deploy",
    }
)

BLOCKED_WRITE_SUBCOMMANDS: frozenset[str] = frozenset(
    {
        "delete",
        "remove",
        "destroy",
        "reset",
        "disable",
        "deprovision",
        "purge",
        "drop",
    }
)

SHELL_OPERATORS: frozenset[str] = frozenset(
    ("&&", "||", ";", "|", ">", ">>", "<", "$(", "`", "\n", "\r")
)


def _tail(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[-max_len:]


class GcloudCliHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        raw_cmd = str(inputs["command"]).strip()
        for op in SHELL_OPERATORS:
            if op in raw_cmd:
                raise ToolInputError(f"Shell operator or newline disallowed in command: {op!r}")

        if not raw_cmd.startswith("gcloud "):
            raise ToolInputError("Command must start with 'gcloud '.")

        try:
            tokens = shlex.split(raw_cmd)
        except ValueError as exc:
            raise ToolInputError(f"Malformed command string: {exc}") from exc

        if len(tokens) < 2:
            raise ToolInputError("Command must include a gcloud subcommand after 'gcloud'.")

        group = tokens[1]
        if group not in ALLOWLISTED_SUBCOMMANDS:
            raise ToolInputError(
                f"gcloud subcommand '{group}' is not in the allowed list. "
                f"Permitted: {sorted(ALLOWLISTED_SUBCOMMANDS)}"
            )

        destructive_ok = "gcloud_cli:destructive" in (self.spec.permissions or [])
        for tok in tokens[2:]:
            if tok in BLOCKED_WRITE_SUBCOMMANDS and not destructive_ok:
                raise ToolInputError(
                    f"Destructive subcommand '{tok}' is blocked. "
                    "Add gcloud_cli:destructive to the agent's tool permissions to enable."
                )

        timeout = float(inputs.get("timeout", 30))
        timeout = max(1.0, min(timeout, 120.0))

        fmt = inputs.get("format", "default")
        if fmt != "default" and "--format" not in tokens:
            tokens = [*tokens, "--format", str(fmt)]

        cmd_joined = shlex.join(tokens)
        log.debug("gcloud_cli_cmd", command=cmd_joined, timeout_sec=timeout)

        t0 = time.perf_counter()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                tokens,
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            log.debug("gcloud_cli_done", returncode=-1, elapsed_ms=int((time.perf_counter() - t0) * 1000))
            return {
                "success": False,
                "stdout": "",
                "stderr": f"Command timed out after {int(timeout)}s",
                "returncode": -1,
                "command_run": cmd_joined,
            }
        except FileNotFoundError as exc:
            raise ToolInputError("gcloud not found on PATH. Install the Google Cloud SDK.") from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.debug("gcloud_cli_done", returncode=proc.returncode, elapsed_ms=elapsed_ms)
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        log.debug("gcloud_cli_io_lens", stdout_chars=len(stdout), stderr_chars=len(stderr))

        return {
            "success": proc.returncode == 0,
            "stdout": _tail(stdout, 8000),
            "stderr": _tail(stderr, 2000),
            "returncode": proc.returncode if proc.returncode is not None else -1,
            "command_run": cmd_joined,
        }

    async def self_test(self) -> bool:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["gcloud", "--version"],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            log.warning("gcloud_cli_self_test", detail="gcloud not found on PATH")
            return False
        except Exception as exc:
            log.warning("gcloud_cli_self_test", detail=str(exc))
            return False
        ok = proc.returncode == 0 and "Google Cloud SDK" in (proc.stdout or "")
        if not ok:
            log.warning("gcloud_cli_self_test", detail="unexpected gcloud --version output")
        return ok


handler = GcloudCliHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
