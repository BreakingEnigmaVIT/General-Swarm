"""Dependency CVE audit — pip-audit / npm audit / govulncheck."""

from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _detect_runtime(wd: Path) -> str:
    if (wd / "pyproject.toml").exists() or (wd / "requirements.txt").exists():
        return "python"
    if (wd / "package.json").exists():
        return "node"
    if (wd / "go.mod").exists():
        return "go"
    return "python"


class DependencyAuditHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        wd = _CWD / inputs.get("path", ".")
        runtime = inputs.get("runtime", "auto")
        if runtime == "auto":
            runtime = _detect_runtime(wd)

        if runtime == "python":
            return await self._pip_audit(wd)
        elif runtime == "node":
            return await self._npm_audit(wd)
        elif runtime == "go":
            return await self._govulncheck(wd)
        return {"error": f"Unknown runtime: {runtime}"}

    async def _run_cmd(self, cmd: list, cwd: Path) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(cwd)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=90)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError as exc:
            return "", str(exc), 127

    async def _pip_audit(self, wd: Path) -> dict:
        stdout, stderr, rc = await self._run_cmd(
            [sys.executable, "-m", "pip_audit", "--format=json"], wd
        )
        vulns = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for dep in data.get("dependencies", []):
                for v in dep.get("vulns", []):
                    vulns.append({
                        "package": dep.get("name", ""),
                        "version": dep.get("version", ""),
                        "cve": v.get("id", ""),
                        "severity": v.get("fix_versions", ["unknown"])[0] if v.get("fix_versions") else "unknown",
                        "description": v.get("description", "")[:300],
                    })
        except json.JSONDecodeError:
            pass
        return {"runtime": "pip-audit", "vulnerabilities": vulns, "count": len(vulns), "exit_code": rc, "stderr": stderr[:500]}

    async def _npm_audit(self, wd: Path) -> dict:
        stdout, stderr, rc = await self._run_cmd(["npm", "audit", "--json"], wd)
        vulns = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for name, info in data.get("vulnerabilities", {}).items():
                vulns.append({
                    "package": name,
                    "severity": info.get("severity", "unknown"),
                    "cve": ", ".join(info.get("cves", [])),
                    "description": info.get("title", "")[:300],
                })
        except json.JSONDecodeError:
            pass
        return {"runtime": "npm-audit", "vulnerabilities": vulns, "count": len(vulns)}

    async def _govulncheck(self, wd: Path) -> dict:
        stdout, stderr, rc = await self._run_cmd(["govulncheck", "-json", "./..."], wd)
        vulns = []
        try:
            for line in stdout.splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "finding" in obj:
                    f = obj["finding"]
                    vulns.append({
                        "osv_id": f.get("osv", ""),
                        "package": f.get("module", {}).get("path", ""),
                        "severity": "medium",
                        "description": f.get("osv", "")[:300],
                    })
        except (json.JSONDecodeError, KeyError):
            pass
        return {"runtime": "govulncheck", "vulnerabilities": vulns, "count": len(vulns)}

    async def self_test(self) -> bool:
        result = await self._run({"path": "."})
        return "vulnerabilities" in result or "error" in result


handler = DependencyAuditHandler()
