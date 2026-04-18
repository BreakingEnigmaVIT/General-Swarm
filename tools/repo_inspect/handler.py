"""Repository inspector — read-only tree + language + dependency detection."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()
_LANG_MAP = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".go": "Go",
    ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".php": "PHP",
}
_DEP_FILES = {
    "requirements.txt": "pip", "pyproject.toml": "pip",
    "package.json": "npm", "go.mod": "go", "Cargo.toml": "cargo",
    "Gemfile": "gem",
}


class RepoInspectHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        root = ((_CWD / inputs.get("path", ".")).resolve())
        max_depth = int(inputs.get("max_depth", 3))
        include_deps = inputs.get("include_deps", True)

        tree = self._build_tree(root, root, max_depth, 0, [])
        langs = self._detect_langs(root)
        deps = self._detect_deps(root) if include_deps else {}

        return {
            "root": str(root.relative_to(_CWD) if root != _CWD else "."),
            "tree": tree[:200],
            "languages": langs,
            "dependency_files": deps,
        }

    def _build_tree(self, root, path, max_depth, depth, lines):
        if depth > max_depth:
            return lines
        try:
            entries = sorted(path.iterdir())
        except PermissionError:
            return lines
        for entry in entries:
            if entry.name.startswith(".") or entry.name in ("node_modules", "__pycache__", ".git", "venv", ".venv"):
                continue
            indent = "  " * depth
            lines.append(f"{indent}{'📁' if entry.is_dir() else '📄'} {entry.name}")
            if entry.is_dir():
                self._build_tree(root, entry, max_depth, depth + 1, lines)
        return lines

    def _detect_langs(self, root: Path) -> dict:
        counts: dict[str, int] = {}
        for p in root.rglob("*"):
            if p.is_file() and p.suffix in _LANG_MAP:
                lang = _LANG_MAP[p.suffix]
                counts[lang] = counts.get(lang, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def _detect_deps(self, root: Path) -> dict:
        found = {}
        for fname, tool in _DEP_FILES.items():
            p = root / fname
            if p.exists():
                try:
                    content = p.read_text(encoding="utf-8", errors="replace")
                    if fname == "package.json":
                        data = json.loads(content)
                        deps = list(data.get("dependencies", {}).keys())[:20]
                        found[fname] = {"tool": tool, "deps": deps}
                    else:
                        found[fname] = {"tool": tool, "size_bytes": p.stat().st_size}
                except Exception:
                    found[fname] = {"tool": tool}
        return found

    async def self_test(self) -> bool:
        result = await self._run({})
        return "tree" in result and "languages" in result


handler = RepoInspectHandler()
