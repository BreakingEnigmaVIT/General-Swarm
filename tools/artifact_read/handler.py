"""Artifact read tool — query artifacts from the registry."""

from __future__ import annotations

from typing import Any, Optional

from tools.base import ToolHandler

_registry: Any = None


def set_registry(reg: Any) -> None:
    global _registry
    _registry = reg


def _serialize_artifact(artifact: Any) -> dict:
    """Convert an ArtifactBase to a JSON-serialisable dict."""
    return artifact.model_dump(mode="json")


class ArtifactReadHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from memory.artifacts import get_artifact_registry

        reg = _registry or get_artifact_registry()
        query_type = inputs["query_type"]
        project_id = inputs.get("project_id", "")
        status_filter = inputs.get("status_filter", "approved")
        limit = inputs.get("limit", 50)

        try:
            if query_type == "by_id":
                artifact_id = inputs.get("artifact_id")
                if not artifact_id:
                    return {"error": "artifact_id is required for query_type=by_id", "artifacts": [], "count": 0}
                artifact = await reg.get_by_id(artifact_id)
                if artifact is None:
                    return {"artifacts": [], "count": 0}
                return {"artifacts": [_serialize_artifact(artifact)], "count": 1}

            elif query_type == "latest_by_type":
                artifact_type = inputs.get("artifact_type")
                if not artifact_type:
                    return {"error": "artifact_type is required for query_type=latest_by_type", "artifacts": [], "count": 0}
                status = status_filter if status_filter != "any" else "approved"
                artifact = await reg.get_latest_by_type(
                    artifact_type, project_id=project_id, status=status
                )
                if artifact is None:
                    # Fall back to any status
                    artifact = await reg.get_latest_by_type(artifact_type, project_id=project_id, status="draft")
                if artifact is None:
                    return {"artifacts": [], "count": 0}
                return {"artifacts": [_serialize_artifact(artifact)], "count": 1}

            elif query_type == "by_stage":
                stage_id = inputs.get("stage_id", "")
                items = await reg.list_by_stage(stage_id, project_id=project_id)
                items = _apply_status_filter(items, status_filter)[:limit]
                return {"artifacts": [_serialize_artifact(a) for a in items], "count": len(items)}

            elif query_type == "by_lineage":
                artifact_id = inputs.get("artifact_id")
                if not artifact_id:
                    return {"error": "artifact_id is required for query_type=by_lineage", "artifacts": [], "count": 0}
                items = await reg.get_lineage(artifact_id)
                items = items[:limit]
                return {"artifacts": [_serialize_artifact(a) for a in items], "count": len(items)}

            elif query_type == "list_all":
                items = await reg.list_all(project_id=project_id)
                items = _apply_status_filter(items, status_filter)[:limit]
                return {"artifacts": [_serialize_artifact(a) for a in items], "count": len(items)}

            else:
                return {"error": f"Unknown query_type: {query_type}", "artifacts": [], "count": 0}

        except Exception as exc:
            return {"error": str(exc), "artifacts": [], "count": 0}

    async def self_test(self) -> bool:
        from memory.artifacts import ArtifactRegistry
        from memory.artifact_schemas.base import ProductBrief
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            reg = ArtifactRegistry(persist_dir=tmp)
            brief = ProductBrief(goal_statement="test", stage_id="discovery")
            await reg.create(brief)
            await reg.approve(brief.id)
            set_registry(reg)
            result = await self._run({
                "query_type": "latest_by_type",
                "artifact_type": "ProductBrief",
            })
            set_registry(None)
            return result.get("count", 0) == 1


def _apply_status_filter(items: list, status_filter: str) -> list:
    if status_filter == "any":
        return items
    return [
        a for a in items
        if (a.status if isinstance(a.status, str) else a.status.value) == status_filter
    ]


handler = ArtifactReadHandler()
