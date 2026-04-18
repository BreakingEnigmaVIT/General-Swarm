"""Linter dispatcher — Ruff / ESLint / golangci-lint."""

from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _detect_lang(path: Path) -> str:
    suffixes = {f.suffix for f in path.rglob("*") if f.is_file()} if path.is_dir() else {path.suffix}
    if ".py" in suffixes:
        return "python"
    if ".ts" in suffixes or ".tsx" in suffixes or ".js" in suffixes:
        return "node"
    if ".go" in suffixes:
        return "go"
    return "python"


class LinterRunHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        rel = inputs["path"]
        target = (_CWD / rel).resolve()
        runtime = inputs.get("runtime", "auto")
        fix = inputs.get("fix", False)
        if runtime == "auto":
            runtime = _detect_lang(target)

        if runtime == "python":
            return await self._ruff(target, fix)
        elif runtime == "node":
            return await self._eslint(target, fix)
        elif runtime == "go":
            return await self._golangci(target)
        return {"error": f"Unknown runtime: {runtime}"}

    async def _run_cmd(self, cmd: list, cwd: Path = _CWD) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(cwd)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=50)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError:
            return "", f"{cmd[0]} not found", 127
        except asyncio.TimeoutError:
            return "", "timed out", -1

    async def _ruff(self, target: Path, fix: bool) -> dict:
        cmd = [sys.executable, "-m", "ruff", "check", str(target), "--output-format=json"]
        if fix:
            cmd.append("--fix")
        stdout, stderr, rc = await self._run_cmd(cmd)
        findings = []
        try:
            raw = json.loads(stdout) if stdout.strip() else []
            for f in raw:
                findings.append({
                    "file": f.get("filename", ""),
                    "line": f.get("location", {}).get("row"),
                    "col": f.get("location", {}).get("column"),
                    "code": f.get("code", ""),
                    "message": f.get("message", ""),
                    "severity": "error" if f.get("fix") is None else "warning",
                })
        except json.JSONDecodeError:
            pass
        return {"runtime": "ruff", "findings": findings, "count": len(findings), "exit_code": rc, "stderr": stderr[:1000]}

    async def _eslint(self, target: Path, fix: bool) -> dict:
        cmd = ["npx", "eslint", str(target), "--format=json"]
        if fix:
            cmd.append("--fix")
        stdout, stderr, rc = await self._run_cmd(cmd)
        findings = []
        try:
            raw = json.loads(stdout) if stdout.strip() else []
            for file_result in raw:
                for msg in file_result.get("messages", []):
                    findings.append({
                        "file": file_result.get("filePath", ""),
                        "line": msg.get("line"),
                        "col": msg.get("column"),
                        "code": msg.get("ruleId", ""),
                        "message": msg.get("message", ""),
                        "severity": "error" if msg.get("severity") == 2 else "warning",
                    })
        except json.JSONDecodeError:
            pass
        return {"runtime": "eslint", "findings": findings, "count": len(findings), "exit_code": rc}

    async def _golangci(self, target: Path) -> dict:
        cmd = ["golangci-lint", "run", "--out-format=json", str(target)]
        stdout, stderr, rc = await self._run_cmd(cmd)
        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for issue in data.get("Issues", []):
                findings.append({
                    "file": issue.get("Pos", {}).get("Filename", ""),
                    "line": issue.get("Pos", {}).get("Line"),
                    "code": issue.get("FromLinter", ""),
                    "message": issue.get("Text", ""),
                    "severity": "warning",
                })
        except json.JSONDecodeError:
            pass
        return {"runtime": "golangci-lint", "findings": findings, "count": len(findings), "exit_code": rc}

    async def self_test(self) -> bool:
        result = await self._run({"path": ".", "runtime": "python"})
        return "findings" in result


handler = LinterRunHandler()
