"""Filesystem operations — jailed to the working directory."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from core.exceptions import SafetyError
from tools.base import ToolHandler

_CWD = Path.cwd()


def _safe_path(rel: str) -> Path:
    p = (_CWD / rel).resolve()
    if not str(p).startswith(str(_CWD)):
        raise SafetyError(f"Path escape attempt: {rel!r} resolves outside working directory")
    return p


class FilesystemHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        op = inputs["operation"]
        path = _safe_path(inputs["path"])

        if op == "read":
            if not path.exists():
                return {"error": f"File not found: {path}"}
            return {"content": path.read_text(encoding="utf-8", errors="replace"),
                    "path": str(path.relative_to(_CWD))}

        elif op == "write":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(inputs.get("content", ""), encoding="utf-8")
            return {"written": str(path.relative_to(_CWD)), "bytes": path.stat().st_size}

        elif op == "append":
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(inputs.get("content", ""))
            return {"appended": str(path.relative_to(_CWD))}

        elif op == "list":
            if not path.exists():
                return {"entries": [], "error": "Path not found"}
            recursive = inputs.get("recursive", False)
            entries = (
                [str(p.relative_to(_CWD)) for p in sorted(path.rglob("*"))]
                if recursive
                else [str(p.relative_to(_CWD)) for p in sorted(path.iterdir())]
            )
            return {"entries": entries[: inputs.get("max_results", 20)]}

        elif op == "search":
            query = inputs.get("query", "").lower()
            recursive = inputs.get("recursive", False)
            base = path if path.is_dir() else path.parent
            pattern = "**/*" if recursive else "*"
            matches = []
            for fp in sorted(base.glob(pattern)):
                if not fp.is_file():
                    continue
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                    if query in text.lower() or query in fp.name.lower():
                        matches.append(str(fp.relative_to(_CWD)))
                except OSError:
                    pass
                if len(matches) >= inputs.get("max_results", 20):
                    break
            return {"matches": matches}

        elif op == "delete":
            if path.exists():
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path)
                else:
                    path.unlink()
                return {"deleted": str(path.relative_to(_CWD))}
            return {"error": "Path not found"}

        elif op == "exists":
            return {"exists": path.exists(), "is_file": path.is_file(), "is_dir": path.is_dir()}

        return {"error": f"Unknown operation: {op}"}

    async def self_test(self) -> bool:
        import tempfile, os
        with tempfile.NamedTemporaryFile(dir=_CWD, suffix=".txt", delete=False) as f:
            fname = Path(f.name).name
        try:
            await self._run({"operation": "write", "path": fname, "content": "hello"})
            r = await self._run({"operation": "read", "path": fname})
            return r.get("content") == "hello"
        finally:
            (_CWD / fname).unlink(missing_ok=True)


handler = FilesystemHandler()
