"""Kubernetes rollout undo (placeholder — extend with kubectl rollout undo)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.base import ToolHandler


class K8sRolloutUndoHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        return {
            "deployment": inputs["deployment"],
            "namespace": inputs["namespace"],
            "skipped": True,
            "message": "k8s_rollout_undo stub — no cluster mutation in stub mode",
        }

    async def self_test(self) -> bool:
        r = await self._run({"deployment": "test", "namespace": "default"})
        return r.get("skipped") is True


handler = K8sRolloutUndoHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
