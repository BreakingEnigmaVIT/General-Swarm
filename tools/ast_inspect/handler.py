"""AST inspector — cyclomatic complexity, nesting, length analysis."""

from __future__ import annotations
import ast
import sys
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _cyclomatic_complexity(node: ast.AST) -> int:
    """Simple cyclomatic complexity: 1 + decision points."""
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.ExceptHandler,
                               ast.With, ast.Assert, ast.comprehension)):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += len(child.values) - 1
    return complexity


class AstInspectHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        target = (_CWD / inputs["path"]).resolve()
        threshold = int(inputs.get("complexity_threshold", 10))

        files = [target] if target.is_file() else list(target.rglob("*.py"))
        issues = []
        metrics = []

        for fp in files[:50]:  # cap to avoid huge scans
            if not fp.is_file() or fp.suffix != ".py":
                continue
            try:
                source = fp.read_text(encoding="utf-8", errors="replace")
                tree = ast.parse(source, filename=str(fp))
            except SyntaxError as e:
                issues.append({"file": str(fp.relative_to(_CWD)), "issue": f"SyntaxError: {e}", "severity": "error"})
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                complexity = _cyclomatic_complexity(node)
                length = (node.end_lineno or 0) - node.lineno
                nesting = _max_nesting(node)
                rel = str(fp.relative_to(_CWD))

                entry = {
                    "file": rel,
                    "function": node.name,
                    "line": node.lineno,
                    "complexity": complexity,
                    "length_lines": length,
                    "max_nesting": nesting,
                }
                metrics.append(entry)

                if complexity > threshold:
                    issues.append({**entry, "issue": f"Cyclomatic complexity {complexity} > {threshold}", "severity": "warning"})
                if length > 100:
                    issues.append({**entry, "issue": f"Function too long: {length} lines", "severity": "info"})
                if nesting > 5:
                    issues.append({**entry, "issue": f"Deep nesting: {nesting} levels", "severity": "warning"})

        return {
            "files_analyzed": len(files),
            "functions_analyzed": len(metrics),
            "issues": issues,
            "issue_count": len(issues),
            "summary": {
                "max_complexity": max((m["complexity"] for m in metrics), default=0),
                "avg_complexity": round(sum(m["complexity"] for m in metrics) / len(metrics), 1) if metrics else 0,
            },
        }

    async def self_test(self) -> bool:
        result = await self._run({"path": "tools/"})
        return "issues" in result


def _max_nesting(node: ast.AST, depth: int = 0) -> int:
    max_d = depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try,
                               ast.ExceptHandler, ast.FunctionDef, ast.AsyncFunctionDef)):
            max_d = max(max_d, _max_nesting(child, depth + 1))
        else:
            max_d = max(max_d, _max_nesting(child, depth))
    return max_d


handler = AstInspectHandler()
