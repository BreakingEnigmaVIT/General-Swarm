"""Artifact write tool — validate + persist typed artifacts to the registry."""

from __future__ import annotations

from typing import Any, Optional

from tools.base import ToolHandler

_registry: Any = None


def set_registry(reg: Any) -> None:
    global _registry
    _registry = reg


class ArtifactWriteHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from memory.artifact_schemas.base import ARTIFACT_REGISTRY, ArtifactType
        from memory.artifacts import get_artifact_registry

        reg = _registry or get_artifact_registry()

        # artifact_type is required — return a clear instructional error rather
        # than crashing with a KeyError that the model may not recover from.
        if "artifact_type" not in inputs:
            return {
                "error": (
                    "Missing required field 'artifact_type'. "
                    f"Valid types: {list(ARTIFACT_REGISTRY.keys())}. "
                    "Call artifact_write with artifact_type as a top-level field, "
                    "e.g. {\"artifact_type\": \"ArchitectureDoc\", \"payload\": {...}}"
                )
            }

        artifact_type_str = inputs["artifact_type"]
        payload = inputs.get("payload", {})

        # Resolve artifact class
        cls = ARTIFACT_REGISTRY.get(artifact_type_str)
        if cls is None:
            return {
                "error": (
                    f"Unknown artifact_type '{artifact_type_str}'. "
                    f"Valid types: {list(ARTIFACT_REGISTRY.keys())}"
                )
            }

        # Merge metadata from tool inputs into payload
        payload.setdefault("artifact_type", artifact_type_str)
        if inputs.get("stage_id"):
            payload["stage_id"] = inputs["stage_id"]
        if inputs.get("author_agent_id"):
            payload["author_agent_id"] = inputs["author_agent_id"]
        if inputs.get("project_id"):
            payload["project_id"] = inputs["project_id"]
        if inputs.get("lineage"):
            payload["lineage"] = inputs["lineage"]

        # Validate against Pydantic schema
        try:
            artifact = cls.model_validate(payload)
        except Exception as exc:
            return {"error": f"Schema validation failed for '{artifact_type_str}': {exc}"}

        # Persist
        try:
            artifact = await reg.create(artifact)
        except Exception as exc:
            return {"error": f"Registry write failed: {exc}"}

        # Optionally approve immediately
        if inputs.get("auto_approve"):
            try:
                artifact = await reg.approve(artifact.id)
            except Exception as exc:
                return {
                    "artifact_id": artifact.id,
                    "artifact_type": artifact_type_str,
                    "status": "draft",
                    "error": f"Created but approval failed: {exc}",
                }

        return {
            "artifact_id": artifact.id,
            "artifact_type": artifact_type_str,
            "status": artifact.status if isinstance(artifact.status, str) else artifact.status.value,
        }

    async def self_test(self) -> bool:
        from memory.artifacts import ArtifactRegistry
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            reg = ArtifactRegistry(persist_dir=tmp)
            set_registry(reg)
            result = await self._run({
                "artifact_type": "ProductBrief",
                "payload": {
                    "goal_statement": "Build a test product",
                    "target_market": "Developers",
                },
                "stage_id": "discovery",
                "author_agent_id": "test_agent",
            })
            set_registry(None)
            return "artifact_id" in result and "error" not in result


handler = ArtifactWriteHandler()
