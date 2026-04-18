"""SAST scanner — Semgrep primary, Bandit fallback for Python."""

from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()
_SEVERITY_MAP = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}


class SastScanHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        target = str((_CWD / inputs["path"]).resolve())
        ruleset = inputs.get("ruleset", "p/owasp-top-ten")

        # Try Semgrep
        result = await self._semgrep(target, ruleset)
        if "error" not in result or "not found" not in result.get("error", ""):
            return result

        # Fallback to Bandit (Python only)
        return await self._bandit(target)

    async def _run_cmd(self, cmd: list) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(_CWD)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError:
            return "", f"{cmd[0]} not found on PATH", 127

    async def _semgrep(self, target: str, ruleset: str) -> dict:
        cmd = ["semgrep", "--config", ruleset, "--json", target]
        stdout, stderr, rc = await self._run_cmd(cmd)
        if "not found" in stderr:
            return {"error": "semgrep not found", "findings": []}

        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for r in data.get("results", []):
                sev = r.get("extra", {}).get("severity", "WARNING")
                findings.append({
                    "tool": "semgrep",
                    "severity": _SEVERITY_MAP.get(sev, "medium"),
                    "rule_id": r.get("check_id", ""),
                    "title": r.get("extra", {}).get("message", ""),
                    "file": r.get("path", ""),
                    "line": r.get("start", {}).get("line"),
                    "cve": "",
                })
        except json.JSONDecodeError:
            pass

        blocks = any(f["severity"] in ("critical", "high") for f in findings)
        return {"tool": "semgrep", "findings": findings, "count": len(findings), "blocks_progression": blocks}

    async def _bandit(self, target: str) -> dict:
        cmd = [sys.executable, "-m", "bandit", "-r", target, "-f", "json", "-q"]
        stdout, stderr, rc = await self._run_cmd(cmd)
        if "not found" in stderr or "No module named bandit" in stderr:
            return {"error": "Neither semgrep nor bandit installed", "findings": []}

        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
            for r in data.get("results", []):
                findings.append({
                    "tool": "bandit",
                    "severity": sev_map.get(r.get("issue_severity", "LOW"), "low"),
                    "rule_id": r.get("test_id", ""),
                    "title": r.get("issue_text", ""),
                    "file": r.get("filename", ""),
                    "line": r.get("line_number"),
                    "cve": "",
                })
        except json.JSONDecodeError:
            pass

        blocks = any(f["severity"] in ("critical", "high") for f in findings)
        return {"tool": "bandit", "findings": findings, "count": len(findings), "blocks_progression": blocks}

    async def self_test(self) -> bool:
        result = await self._run({"path": "."})
        return "findings" in result


handler = SastScanHandler()
