"""
Artifact Registry — typed, versioned, lineage-tracked artifact store.

Built on top of the existing LocalChromaMemory (long-term memory) using a
dedicated collection name `artifacts`.  Agents must never write workforce data
directly to long-term memory; they always go through this module so type safety
and lineage are enforced (§19.2).

Supported operations:
  create        — validate + persist a new artifact (status=draft)
  approve       — transition draft → approved
  supersede     — transition approved → superseded (new artifact takes its place)
  get_by_id     — fetch one artifact by id
  get_latest_by_type — newest approved artifact of a given type in a project
  get_lineage   — walk parent chain
  list_by_stage — all artifacts for a given stage
  list_by_type  — all artifacts for a given type

Audit log:
  Every status transition is appended to an append-only JSONL file under
  persist_dir/artifact_audit.jsonl (retained even after memory pruning).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Type

from memory.artifact_schemas.base import (
    ARTIFACT_REGISTRY,
    ArtifactBase,
    ArtifactStatus,
    ArtifactType,
)
from observability.logutil import get_logger

log = get_logger("memory.artifacts")

_COLLECTION = "artifacts"


class ArtifactRegistry:
    """
    Thin typed index on top of LocalChromaMemory.

    Pass in the same LocalChromaMemory instance used by the rest of the swarm;
    this registry uses a separate ChromaDB collection internally.
    """

    def __init__(self, persist_dir: str = "./memory_store") -> None:
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._audit_path = self._persist_dir / "artifact_audit.jsonl"
        self._client: Any = None
        self._col: Any = None
        self._fallback: dict[str, dict] = {}
        self._use_fallback = False
        self._init()

    # ── Init ──────────────────────────────────────────────────────────────────

    def _init(self) -> None:
        try:
            import chromadb
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._col = self._client.get_or_create_collection(
                name=_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            log.info("artifact_registry_ready", backend="chroma", dir=str(self._persist_dir))
        except ImportError:
            log.warning("chromadb_missing", fallback="in-memory")
            self._use_fallback = True

    # ── Serialisation helpers ─────────────────────────────────────────────────

    def _serialize(self, artifact: ArtifactBase) -> str:
        return artifact.model_dump_json()

    def _deserialize(self, raw: str, artifact_type: str) -> ArtifactBase:
        cls: Type[ArtifactBase] = ARTIFACT_REGISTRY.get(artifact_type, ArtifactBase)
        return cls.model_validate_json(raw)

    def _meta(self, artifact: ArtifactBase) -> dict:
        return {
            "id": artifact.id,
            "artifact_type": artifact.artifact_type
            if isinstance(artifact.artifact_type, str)
            else artifact.artifact_type.value,
            "stage_id": artifact.stage_id,
            "project_id": artifact.project_id,
            "author_agent_id": artifact.author_agent_id,
            "status": artifact.status
            if isinstance(artifact.status, str)
            else artifact.status.value,
            "version": str(artifact.version),
            "created_at": artifact.created_at.isoformat(),
            "lineage": json.dumps(artifact.lineage),
        }

    # ── Audit log ─────────────────────────────────────────────────────────────

    def _audit(self, artifact_id: str, from_status: str, to_status: str, note: str = "") -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "artifact_id": artifact_id,
            "from": from_status,
            "to": to_status,
            "note": note,
        }
        with self._audit_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")

    # ── CRUD ──────────────────────────────────────────────────────────────────

    async def create(self, artifact: ArtifactBase) -> ArtifactBase:
        """Validate and persist a new artifact (status remains draft)."""
        artifact.status = ArtifactStatus.draft
        doc = self._serialize(artifact)
        meta = self._meta(artifact)

        if self._use_fallback:
            self._fallback[artifact.id] = {"doc": doc, "meta": meta}
        else:
            try:
                self._col.upsert(ids=[artifact.id], documents=[doc], metadatas=[meta])
            except Exception as exc:
                log.error("artifact_create_error", id=artifact.id, error=str(exc))

        self._audit(artifact.id, "—", ArtifactStatus.draft, "created")
        log.info("artifact_created", id=artifact.id[:8], type=meta["artifact_type"])
        return artifact

    async def approve(self, artifact_id: str) -> Optional[ArtifactBase]:
        """Transition draft → approved."""
        artifact = await self.get_by_id(artifact_id)
        if artifact is None:
            log.warning("artifact_not_found", id=artifact_id)
            return None
        prev = artifact.status if isinstance(artifact.status, str) else artifact.status.value
        artifact.status = ArtifactStatus.approved
        await self._update(artifact)
        self._audit(artifact_id, prev, ArtifactStatus.approved)
        log.info("artifact_approved", id=artifact_id[:8])
        return artifact

    async def supersede(self, artifact_id: str, successor_id: str) -> Optional[ArtifactBase]:
        """Transition approved → superseded; link successor."""
        artifact = await self.get_by_id(artifact_id)
        if artifact is None:
            return None
        prev = artifact.status if isinstance(artifact.status, str) else artifact.status.value
        artifact.status = ArtifactStatus.superseded
        await self._update(artifact)
        self._audit(artifact_id, prev, ArtifactStatus.superseded, f"superseded_by={successor_id}")
        log.info("artifact_superseded", id=artifact_id[:8], successor=successor_id[:8])
        return artifact

    async def _update(self, artifact: ArtifactBase) -> None:
        doc = self._serialize(artifact)
        meta = self._meta(artifact)
        if self._use_fallback:
            self._fallback[artifact.id] = {"doc": doc, "meta": meta}
        else:
            try:
                self._col.upsert(ids=[artifact.id], documents=[doc], metadatas=[meta])
            except Exception as exc:
                log.error("artifact_update_error", id=artifact.id, error=str(exc))

    # ── Queries ───────────────────────────────────────────────────────────────

    async def get_by_id(self, artifact_id: str) -> Optional[ArtifactBase]:
        if self._use_fallback:
            entry = self._fallback.get(artifact_id)
            if not entry:
                return None
            meta = entry["meta"]
            return self._deserialize(entry["doc"], meta["artifact_type"])
        try:
            result = self._col.get(ids=[artifact_id])
            docs = result.get("documents") or []
            metas = result.get("metadatas") or []
            if not docs:
                return None
            return self._deserialize(docs[0], metas[0]["artifact_type"])
        except Exception as exc:
            log.error("artifact_get_error", id=artifact_id, error=str(exc))
            return None

    async def get_latest_by_type(
        self,
        artifact_type: str,
        project_id: str = "",
        stage_id: str = "",
        status: str = ArtifactStatus.approved,
    ) -> Optional[ArtifactBase]:
        """Return the newest artifact of a type, optionally scoped to project/stage."""
        items = await self.list_by_type(artifact_type, project_id=project_id, stage_id=stage_id)
        matching = [a for a in items if (
            (a.status if isinstance(a.status, str) else a.status.value) == status
        )]
        if not matching:
            return None
        return max(matching, key=lambda a: a.created_at)

    async def get_lineage(self, artifact_id: str, depth: int = 10) -> list[ArtifactBase]:
        """Walk up the lineage chain and return parent artifacts."""
        chain: list[ArtifactBase] = []
        visited: set[str] = set()
        current = await self.get_by_id(artifact_id)
        if current is None:
            return chain
        for _ in range(depth):
            if not current.lineage:
                break
            for parent_id in current.lineage:
                if parent_id in visited:
                    continue
                visited.add(parent_id)
                parent = await self.get_by_id(parent_id)
                if parent:
                    chain.append(parent)
                    current = parent
                    break
            else:
                break
        return chain

    async def list_by_stage(self, stage_id: str, project_id: str = "") -> list[ArtifactBase]:
        return await self._query_where({"stage_id": stage_id, "project_id": project_id})

    async def list_by_type(
        self, artifact_type: str, project_id: str = "", stage_id: str = ""
    ) -> list[ArtifactBase]:
        where: dict[str, str] = {"artifact_type": artifact_type}
        if project_id:
            where["project_id"] = project_id
        if stage_id:
            where["stage_id"] = stage_id
        return await self._query_where(where)

    async def list_all(self, project_id: str = "") -> list[ArtifactBase]:
        if project_id:
            return await self._query_where({"project_id": project_id})
        return await self._query_where({})

    async def _query_where(self, where: dict[str, str]) -> list[ArtifactBase]:
        if self._use_fallback:
            results = []
            for entry in self._fallback.values():
                meta = entry["meta"]
                if all(meta.get(k) == v for k, v in where.items() if v):
                    try:
                        results.append(self._deserialize(entry["doc"], meta["artifact_type"]))
                    except Exception:
                        pass
            return results

        try:
            # Build ChromaDB where clause — only include non-empty filter values
            chroma_where: dict[str, Any] = {
                k: {"$eq": v} for k, v in where.items() if v
            }
            if chroma_where:
                # ChromaDB requires $and for multiple conditions
                if len(chroma_where) == 1:
                    result = self._col.get(where=chroma_where)
                else:
                    result = self._col.get(where={"$and": [{k: v} for k, v in chroma_where.items()]})
            else:
                result = self._col.get()

            docs = result.get("documents") or []
            metas = result.get("metadatas") or []
            items = []
            for doc, meta in zip(docs, metas):
                try:
                    items.append(self._deserialize(doc, meta["artifact_type"]))
                except Exception as exc:
                    log.warning("artifact_deserialize_error", error=str(exc))
            return items
        except Exception as exc:
            log.error("artifact_query_error", where=where, error=str(exc))
            return []

    def close(self) -> None:
        if self._use_fallback or self._client is None:
            return
        try:
            self._col = None
            try:
                from chromadb.api.client import SharedSystemClient
                for system in list(SharedSystemClient._identifier_to_system.values()):
                    try:
                        system.stop()
                    except Exception:
                        pass
                SharedSystemClient._identifier_to_system = {}
                SharedSystemClient._identifier_to_refcount = {}
            except Exception:
                pass
            self._client = None
        except Exception as exc:
            log.warning("artifact_registry_close_error", error=str(exc))


# ── Module-level singleton ────────────────────────────────────────────────────

_registry: Optional[ArtifactRegistry] = None


def get_artifact_registry(persist_dir: str = "./memory_store") -> ArtifactRegistry:
    global _registry
    if _registry is None:
        _registry = ArtifactRegistry(persist_dir=persist_dir)
    return _registry


def reset_artifact_registry(persist_dir: str = "./memory_store") -> ArtifactRegistry:
    global _registry
    if _registry is not None:
        _registry.close()
    _registry = ArtifactRegistry(persist_dir=persist_dir)
    return _registry
