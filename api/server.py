"""
HTTP / WebSocket API — thin FastAPI layer over the swarm runtime.

Endpoints:
  POST /run          — submit a goal, receive streaming result
  GET  /traces       — list all trace IDs
  GET  /traces/{id}  — get full trace
  GET  /cost/{id}    — cost summary for a trace
  GET  /agents       — list registered agent roles
  GET  /tools        — list registered tools
  WS   /ws           — WebSocket for live agent events
  GET  /health       — health check
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="Swarm API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State (set by CLI on startup) ─────────────────────────────────────────────

# _runtime_factory is called per /run request so each run gets a fresh,
# isolated SwarmRuntime — no state bleed between consecutive API calls.
_runtime_factory: Optional[Callable[[], Tuple[Any, Any]]] = None
_config: Optional[Any] = None
_ws_clients: list[WebSocket] = []


def set_runtime_factory(factory: Callable[[], Tuple[Any, Any]], config: Any) -> None:
    """Register a zero-argument callable that returns (SwarmRuntime, CostLedger).

    Called once by `swarm dashboard` after bootstrapping registries.
    Each POST /run invocation calls this factory to obtain a fresh runtime.
    """
    global _runtime_factory, _config
    _runtime_factory = factory
    _config = config


def set_runtime(runtime: Any, config: Any) -> None:
    """Legacy shim — wraps a pre-built runtime in a factory. Kept for compat."""
    def _factory() -> Tuple[Any, Any]:
        return runtime, None
    set_runtime_factory(_factory, config)


# ── Request / Response models ─────────────────────────────────────────────────

class RunRequest(BaseModel):
    goal: str
    topology: Optional[str] = None
    budget_usd: Optional[float] = None


class RunResponse(BaseModel):
    trace_id: str
    output: Any
    success: bool
    cost_usd: float
    tokens: int
    iterations: int


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "runtime_ready": _runtime_factory is not None}


@app.post("/run", response_model=RunResponse)
async def run_goal(req: RunRequest) -> RunResponse:
    if _runtime_factory is None:
        raise HTTPException(
            503,
            detail=(
                "Runtime not initialised. "
                "The dashboard must be started with `swarm dashboard` "
                "so that the agent and tool registries are bootstrapped "
                "before accepting run requests."
            ),
        )
    # Build a fresh runtime per request — isolates state between runs.
    runtime, _ledger = _runtime_factory()
    result = await runtime.run(req.goal)
    trace_id = getattr(runtime, "trace_id", "")
    token_total = 0
    if result.token_usage:
        token_total = getattr(result.token_usage, "total_tokens", 0)
    return RunResponse(
        trace_id=trace_id,
        output=result.output,
        success=result.success,
        cost_usd=result.cost or 0.0,
        tokens=token_total,
        iterations=getattr(result, "iterations", 0),
    )


@app.get("/traces")
async def list_traces() -> dict:
    if _config is None:
        return {"traces": []}
    from observability.tracing import Tracer
    tracer = Tracer(_config.trace_dir)
    return {"traces": tracer.list_traces()}


@app.get("/traces/{trace_id}")
async def get_trace(trace_id: str) -> dict:
    if _config is None:
        raise HTTPException(503, "Config not set")
    from observability.replay import load_trace
    spans = load_trace(trace_id, _config.trace_dir)
    if not spans:
        raise HTTPException(404, f"Trace {trace_id} not found")
    return {"trace_id": trace_id, "spans": [s.model_dump() for s in spans]}


@app.get("/cost/{trace_id}")
async def get_cost(trace_id: str) -> dict:
    if _config is None:
        raise HTTPException(503, "Config not set")
    from observability.replay import cost_summary
    return cost_summary(trace_id, _config.trace_dir)


@app.get("/agents")
async def list_agents() -> dict:
    from core.registry import get_agent_spec_registry
    ar = get_agent_spec_registry()
    agents = [
        {"role": name, "description": spec.description, "model": spec.model}
        for name, spec in ar.items()
    ]
    return {"agents": agents}


@app.get("/tools")
async def list_tools() -> dict:
    from core.registry import get_tool_registry
    tr = get_tool_registry()
    tools = [
        {"name": name, "description": handler.spec.description,
         "side_effect_level": handler.spec.side_effect_level}
        for name, handler in tr.items()
    ]
    return {"tools": tools}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    _ws_clients.append(ws)
    try:
        while True:
            await ws.receive_text()  # keep connection alive
    except Exception:
        pass
    finally:
        _ws_clients.remove(ws)


async def broadcast_event(event: dict) -> None:
    for ws in list(_ws_clients):
        try:
            import json
            await ws.send_text(json.dumps(event))
        except Exception:
            _ws_clients.remove(ws)
