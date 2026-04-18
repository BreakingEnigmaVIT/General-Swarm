"""KEDA ScaledObject apply (placeholder)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import ToolHandler


class KedaScaledObjectApplyHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "manifest_path": inputs["manifest_path"],
            "skipped": True,
            "message": "keda_scaledobject_apply stub",
        }

    async def self_test(self) -> bool:
        r = await self._run({"manifest_path": "configs/keda-placeholder.yaml"})
        return r.get("skipped") is True


handler = KedaScaledObjectApplyHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
