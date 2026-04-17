"""Hierarchical tracing — one trace per user request, spans for tasks/tools/LLM calls.

Trace data is stored as JSONL under the configured trace_dir so it can be replayed
and exported to OpenTelemetry-compatible backends later.
"""

from __future__ import annotations

import json
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

# ── Context vars (async-safe) ────────────────────────────────────────────────

_current_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_current_span_id: ContextVar[str] = ContextVar("span_id", default="")


def get_trace_id() -> str:
    return _current_trace_id.get()


def get_span_id() -> str:
    return _current_span_id.get()


def set_trace_id(trace_id: str) -> None:
    _current_trace_id.set(trace_id)


def new_trace_id() -> str:
    tid = str(uuid.uuid4())
    _current_trace_id.set(tid)
    return tid


def new_span_id() -> str:
    sid = str(uuid.uuid4())
    _current_span_id.set(sid)
    return sid


# ── Models ────────────────────────────────────────────────────────────────────

class SpanEvent(BaseModel):
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    name: str
    kind: str  # agent | task | tool | llm | bus | custom
    agent_id: Optional[str] = None
    task_id: Optional[str] = None
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time: Optional[datetime] = None
    status: str = "ok"  # ok | error
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None

    def finish(self, status: str = "ok", error: Optional[str] = None) -> None:
        self.end_time = datetime.now(timezone.utc)
        self.status = status
        self.error = error

    @property
    def duration_ms(self) -> Optional[float]:
        if self.end_time:
            return (self.end_time - self.start_time).total_seconds() * 1000
        return None


# ── Tracer ────────────────────────────────────────────────────────────────────

class Tracer:
    """
    Writes SpanEvent records to a per-trace JSONL file.
    Thread-safe append; compatible with async via asyncio.to_thread if needed.
    """

    def __init__(self, trace_dir: str = "./traces") -> None:
        self._dir = Path(trace_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def start_span(
        self,
        name: str,
        kind: str,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        **attributes: Any,
    ) -> SpanEvent:
        trace_id = get_trace_id() or new_trace_id()
        parent_span_id = get_span_id() or None
        span_id = new_span_id()
        span = SpanEvent(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            name=name,
            kind=kind,
            agent_id=agent_id,
            task_id=task_id,
            attributes=attributes,
        )
        return span

    def finish_span(self, span: SpanEvent, status: str = "ok", error: Optional[str] = None) -> None:
        span.finish(status=status, error=error)
        self._write(span)

    def _write(self, span: SpanEvent) -> None:
        path = self._dir / f"{span.trace_id}.jsonl"
        line = span.model_dump_json() + "\n"
        with self._lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def load_trace(self, trace_id: str) -> list[SpanEvent]:
        path = self._dir / f"{trace_id}.jsonl"
        if not path.exists():
            return []
        spans = []
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    spans.append(SpanEvent.model_validate_json(line))
        return spans

    def list_traces(self) -> list[str]:
        return [p.stem for p in sorted(self._dir.glob("*.jsonl"))]


# ── Context-manager span helper ───────────────────────────────────────────────

class Span:
    """Thin context manager wrapping a SpanEvent."""

    def __init__(self, tracer: Tracer, name: str, kind: str, **kwargs: Any) -> None:
        self._tracer = tracer
        self._span = tracer.start_span(name, kind, **kwargs)

    def set(self, **attributes: Any) -> None:
        self._span.attributes.update(attributes)

    def __enter__(self) -> "Span":
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, _tb: Any) -> bool:
        if exc_type is not None:
            self._tracer.finish_span(self._span, status="error", error=str(exc_val))
        else:
            self._tracer.finish_span(self._span)
        return False


# ── Module-level default tracer (replaced by SwarmRuntime on startup) ─────────

_default_tracer: Optional[Tracer] = None


def get_tracer() -> Tracer:
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = Tracer()
    return _default_tracer


def configure_tracer(trace_dir: str) -> Tracer:
    global _default_tracer
    _default_tracer = Tracer(trace_dir)
    return _default_tracer
