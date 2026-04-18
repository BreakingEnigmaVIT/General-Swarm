"""DB migration tool — dispatches to Alembic or Prisma."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _detect_tool(work_dir: Path) -> str:
    if (work_dir / "alembic.ini").exists():
        return "alembic"
    if (work_dir / "prisma" / "schema.prisma").exists():
        return "prisma"
    return "alembic"


class DbMigrateHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]
        wd = _CWD / inputs.get("working_dir", ".")
        tool = inputs.get("tool", "auto")
        dry_run = inputs.get("dry_run", False)
        revision = inputs.get("revision", "head")

        if tool == "auto":
            tool = _detect_tool(wd)

        if tool == "alembic":
            return await self._alembic(action, revision, wd, dry_run)
        elif tool == "prisma":
            return await self._prisma(action, wd, dry_run)
        return {"error": f"Unknown tool: {tool}"}

    async def _run_cmd(self, cmd: list, cwd: Path) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(cwd)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=50)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError as exc:
            return "", str(exc), 127

    async def _alembic(self, action: str, revision: str, wd: Path, dry_run: bool) -> dict:
        alembic = [sys.executable, "-m", "alembic"]
        if action == "upgrade":
            cmd = alembic + (["upgrade", "--sql", revision] if dry_run else ["upgrade", revision])
        elif action == "downgrade":
            rev = revision if revision != "head" else "-1"
            cmd = alembic + (["downgrade", "--sql", rev] if dry_run else ["downgrade", rev])
        elif action in ("current", "history", "dry_run"):
            cmd = alembic + ["current" if action != "dry_run" else "upgrade", "--sql", "head"]
        else:
            return {"error": f"Unknown action: {action}"}

        stdout, stderr, rc = await self._run_cmd(cmd, wd)

        # Flag destructive operations
        destructive_keywords = ["DROP TABLE", "DROP COLUMN", "TRUNCATE", "DELETE FROM"]
        warnings = [kw for kw in destructive_keywords if kw in stdout.upper()]

        return {
            "tool": "alembic",
            "action": action,
            "dry_run": dry_run,
            "success": rc == 0,
            "output": stdout[:5000],
            "stderr": stderr[:2000],
            "destructive_operations": warnings,
            "requires_confirmation": bool(warnings),
        }

    async def _prisma(self, action: str, wd: Path, dry_run: bool) -> dict:
        if action in ("upgrade", "dry_run"):
            cmd = ["npx", "prisma", "migrate", "deploy" if not dry_run else "diff"]
        elif action == "downgrade":
            return {"error": "Prisma does not support automatic rollback; use manual migration"}
        else:
            cmd = ["npx", "prisma", "migrate", "status"]

        stdout, stderr, rc = await self._run_cmd(cmd, wd)
        return {"tool": "prisma", "action": action, "success": rc == 0, "output": stdout[:5000], "stderr": stderr[:2000]}

    async def self_test(self) -> bool:
        result = await self._run({"action": "history", "working_dir": "."})
        return "success" in result or "error" in result


handler = DbMigrateHandler()
