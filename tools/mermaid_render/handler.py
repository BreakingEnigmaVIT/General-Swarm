"""Mermaid renderer. Writes .mmd source always; generates SVG via mmdc CLI if available."""

from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


def _safe_path(rel: str) -> Path:
    from core.exceptions import SafetyError
    p = (_CWD / rel).resolve()
    if not str(p).startswith(str(_CWD)):
        raise SafetyError(f"Path escape: {rel!r}")
    return p


class MermaidRenderHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        source = inputs["source"]
        base = _safe_path(inputs["output_path"])
        base.parent.mkdir(parents=True, exist_ok=True)

        mmd_path = base.with_suffix(".mmd")
        mmd_path.write_text(source, encoding="utf-8")

        svg_path = base.with_suffix(".svg")
        svg_generated = False
        error = None

        # Try mmdc (mermaid-js CLI) if installed
        try:
            result = subprocess.run(
                ["mmdc", "-i", str(mmd_path), "-o", str(svg_path)],
                capture_output=True, text=True, timeout=20,
            )
            svg_generated = result.returncode == 0
            if not svg_generated:
                error = result.stderr[:500]
        except FileNotFoundError:
            error = "mmdc not installed — SVG generation skipped. Install: npm install -g @mermaid-js/mermaid-cli"
        except Exception as exc:
            error = str(exc)

        return {
            "mmd_path": str(mmd_path.relative_to(_CWD)),
            "svg_path": str(svg_path.relative_to(_CWD)) if svg_generated else None,
            "svg_generated": svg_generated,
            "warning": error,
        }

    async def self_test(self) -> bool:
        import tempfile, os
        with tempfile.NamedTemporaryFile(dir=_CWD, suffix="", delete=False, prefix="mermaid_test") as f:
            base = f.name
        try:
            result = await self._run({
                "source": "graph TD\n  A --> B",
                "output_path": Path(base).name,
            })
            return "mmd_path" in result
        finally:
            for ext in [".mmd", ".svg"]:
                Path(base + ext).unlink(missing_ok=True)
            Path(base).unlink(missing_ok=True)


handler = MermaidRenderHandler()
