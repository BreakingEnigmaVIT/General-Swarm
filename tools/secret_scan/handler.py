"""Secret scanner — detect-secrets or regex fallback."""

from __future__ import annotations
import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()

# Regex patterns for common secret shapes
_PATTERNS = [
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key", "critical"),
    (r"(?i)aws[_\-\s]?secret[_\-\s]?access[_\-\s]?key\s*[:=]\s*\S{40}", "AWS Secret Key", "critical"),
    (r"(?i)sk-[a-zA-Z0-9]{48}", "OpenAI API Key", "critical"),
    (r"(?i)AIza[0-9A-Za-z\-_]{35}", "Google API Key", "high"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "Private Key", "critical"),
    (r"(?i)(?:password|passwd|pwd)\s*[:=]\s*['\"]?[^\s'\"]{8,}['\"]?", "Hardcoded Password", "high"),
    (r"(?i)(?:secret|token|api_key|apikey)\s*[:=]\s*['\"][^\s'\"]{16,}['\"]", "API Token/Secret", "high"),
    (r"ghp_[0-9a-zA-Z]{36}", "GitHub Token", "critical"),
    (r"(?i)bearer\s+[a-zA-Z0-9\-_\.]{20,}", "Bearer Token", "medium"),
]


class SecretScanHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        scan_mode = inputs.get("scan_mode", "full_tree")
        target = (_CWD / inputs["path"]).resolve()

        # Try detect-secrets first
        result = await self._detect_secrets(target, scan_mode, inputs.get("diff_ref", "HEAD~1"))
        if "error" not in result:
            return result

        # Regex fallback
        return await self._regex_scan(target, scan_mode)

    async def _run_cmd(self, cmd: list) -> tuple[str, str, int]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd=str(_CWD)
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=50)
            return stdout.decode(errors="replace"), stderr.decode(errors="replace"), proc.returncode
        except FileNotFoundError:
            return "", "not found", 127

    async def _detect_secrets(self, target: Path, mode: str, diff_ref: str) -> dict:
        if mode == "git_diff":
            cmd = [sys.executable, "-m", "detect_secrets", "scan", "--diff"]
        else:
            cmd = [sys.executable, "-m", "detect_secrets", "scan", str(target)]

        stdout, stderr, rc = await self._run_cmd(cmd)
        if "not found" in stderr or "No module" in stderr:
            return {"error": "detect-secrets not installed"}

        findings = []
        try:
            data = json.loads(stdout) if stdout.strip() else {}
            for path, secrets in data.get("results", {}).items():
                for s in secrets:
                    findings.append({
                        "file": path,
                        "line": s.get("line_number"),
                        "type": s.get("type", ""),
                        "severity": "high",
                        "hashed_secret": s.get("hashed_secret", "")[:16] + "...",
                    })
        except json.JSONDecodeError:
            pass
        return {"tool": "detect-secrets", "findings": findings, "count": len(findings)}

    async def _regex_scan(self, target: Path, mode: str) -> dict:
        findings = []
        files = [target] if target.is_file() else list(target.rglob("*"))
        skip_dirs = {".git", "node_modules", "__pycache__", ".venv", "venv"}

        for fp in files:
            if not fp.is_file():
                continue
            if any(part in skip_dirs for part in fp.parts):
                continue
            if fp.suffix in (".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".bin"):
                continue
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                for pattern, label, severity in _PATTERNS:
                    for m in re.finditer(pattern, text):
                        line_no = text[:m.start()].count("\n") + 1
                        findings.append({
                            "file": str(fp.relative_to(_CWD)),
                            "line": line_no,
                            "type": label,
                            "severity": severity,
                            "match_preview": m.group()[:30] + "...",
                        })
            except Exception:
                pass

        return {
            "tool": "regex_fallback",
            "findings": findings[:100],
            "count": len(findings),
            "blocks_progression": any(f["severity"] == "critical" for f in findings),
        }

    async def self_test(self) -> bool:
        result = await self._run({"path": ".", "scan_mode": "full_tree"})
        return "findings" in result


handler = SecretScanHandler()
