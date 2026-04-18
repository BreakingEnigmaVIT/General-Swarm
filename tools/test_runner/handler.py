"""Test runner — auto-detects and dispatches to pytest/jest/go test."""

from __future__ import annotations
import asyncio
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _detect_runtime(work_dir: Path) -> str:
    if (work_dir / "pyproject.toml").exists() or (work_dir / "setup.py").exists() or list(work_dir.rglob("test_*.py")):
        return "python"
    if (work_dir / "package.json").exists():
        return "node"
    if (work_dir / "go.mod").exists():
        return "go"
    return "python"


class TestRunnerHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        wd = _CWD / inputs.get("working_dir", ".")
        runtime = inputs.get("runtime", "auto")
        if runtime == "auto":
            runtime = _detect_runtime(wd)

        test_path = inputs.get("test_path", "")
        coverage = inputs.get("coverage", True)
        extra = inputs.get("extra_args", [])

        if runtime == "python":
            cmd = [sys.executable, "-m", "pytest", "-v", "--tb=short"]
            if coverage:
                cmd += ["--cov=.", "--cov-report=term-missing"]
            if test_path:
                cmd.append(test_path)
            cmd += extra
        elif runtime == "node":
            cmd = ["npx", "jest", "--no-coverage" if not coverage else "--coverage"]
            if test_path:
                cmd.append(test_path)
            cmd += extra
        elif runtime == "go":
            cmd = ["go", "test"]
            if coverage:
                cmd += ["-cover"]
            cmd.append(test_path or "./...")
            cmd += extra
        else:
            return {"error": f"Unknown runtime: {runtime}"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(wd),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=100)
            output = stdout.decode(errors="replace")
            err_out = stderr.decode(errors="replace")
            success = proc.returncode == 0

            # Parse basic stats
            passed = failed = 0
            for line in output.splitlines():
                if "passed" in line:
                    import re
                    m = re.search(r"(\d+) passed", line)
                    if m:
                        passed = int(m.group(1))
                    m = re.search(r"(\d+) failed", line)
                    if m:
                        failed = int(m.group(1))

            return {
                "success": success,
                "exit_code": proc.returncode,
                "runtime": runtime,
                "passed": passed,
                "failed": failed,
                "output": output[:8000],
                "stderr": err_out[:2000],
            }
        except asyncio.TimeoutError:
            return {"error": "Test run timed out", "success": False}
        except FileNotFoundError as exc:
            return {"error": f"Test runner not found: {exc}", "success": False}

    async def self_test(self) -> bool:
        result = await self._run({"runtime": "python", "test_path": "tests/unit/", "coverage": False})
        return "exit_code" in result


handler = TestRunnerHandler()
