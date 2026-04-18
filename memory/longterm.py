"""Long-term vector memory backed by chromadb (local, embedded).

The backend is pluggable: subclass LongTermMemory and register under a different name.
Default is LocalChromaMemory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from memory.base import MemoryInterface
from observability.logutil import get_logger

log = get_logger("memory.longterm")


class LocalChromaMemory(MemoryInterface):
    """
    Uses chromadb with persistent local storage.
    Falls back to a pure-Python in-memory index if chromadb isn't installed
    (so tests don't need the heavy dependency).
    """

    def __init__(self, persist_dir: str = "./memory_store", collection: str = "swarm") -> None:
        self._persist_dir = Path(persist_dir)
        self._collection_name = collection
        self._client: Any = None
        self._collection: Any = None
        self._fallback: dict[str, dict] = {}
        self._use_fallback = False
        self._init()

    def _init(self) -> None:
        try:
            import chromadb

            self._persist_dir.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
            log.info("longterm_memory_ready", backend="chroma", dir=str(self._persist_dir))
        except ImportError:
            log.warning("chromadb_missing", fallback="in-memory")
            self._use_fallback = True

    def _doc_id(self, key: str) -> str:
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    # ── MemoryInterface impl ───────────────────────────────────────────────────

    async def write(self, key: str, value: Any, metadata: Optional[dict] = None) -> None:
        doc = json.dumps(value) if not isinstance(value, str) else value
        doc_id = self._doc_id(key)
        meta = {"key": key, **(metadata or {})}

        if self._use_fallback:
            self._fallback[key] = {"id": doc_id, "doc": doc, "meta": meta}
            return

        try:
            self._collection.upsert(ids=[doc_id], documents=[doc], metadatas=[meta])
        except Exception as exc:
            log.error("longterm_write_error", key=key, error=str(exc))

    async def read(self, key: str) -> Optional[Any]:
        if self._use_fallback:
            entry = self._fallback.get(key)
            return entry["doc"] if entry else None
        try:
            doc_id = self._doc_id(key)
            result = self._collection.get(ids=[doc_id])
            docs = result.get("documents") or []
            return docs[0] if docs else None
        except Exception as exc:
            log.error("longterm_read_error", key=key, error=str(exc))
            return None

    async def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        if self._use_fallback:
            q = query.lower()
            results = []
            for k, v in self._fallback.items():
                if q in v["doc"].lower() or q in k.lower():
                    results.append({"key": k, "content": v["doc"], "score": 1.0})
            return results[:limit]

        try:
            result = self._collection.query(
                query_texts=[query],
                n_results=min(limit, max(1, self._collection.count())),
            )
            items = []
            docs = result.get("documents", [[]])[0]
            metas = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0]
            for doc, meta, dist in zip(docs, metas, distances):
                items.append({
                    "key": meta.get("key", ""),
                    "content": doc,
                    "score": 1 - dist,
                    "metadata": meta,
                })
            return items
        except Exception as exc:
            log.error("longterm_search_error", query=query, error=str(exc))
            return []

    async def delete(self, key: str) -> None:
        if self._use_fallback:
            self._fallback.pop(key, None)
            return
        try:
            self._collection.delete(ids=[self._doc_id(key)])
        except Exception as exc:
            log.error("longterm_delete_error", key=key, error=str(exc))

    async def clear(self) -> None:
        if self._use_fallback:
            self._fallback.clear()
            return
        try:
            self._client.delete_collection(self._collection_name)
            self._collection = self._client.get_or_create_collection(self._collection_name)
        except Exception as exc:
            log.error("longterm_clear_error", error=str(exc))

    def close(self) -> None:
        """Release the ChromaDB client and its SQLite lock (important on Windows)."""
        if self._use_fallback or self._client is None:
            return
        try:
            self._collection = None
            # Stop every cached system so SQLite file handles are closed, then
            # clear the cache so future clients don't reuse the stopped system.
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
            log.warning("longterm_close_error", error=str(exc))
