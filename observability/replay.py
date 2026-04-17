"""Trace replay — re-run a trace deterministically or with fresh LLM calls."""

from __future__ import annotations

from typing import Any

from observability.tracing import SpanEvent, Tracer


def load_trace(trace_id: str, trace_dir: str = "./traces") -> list[SpanEvent]:
    tracer = Tracer(trace_dir)
    return tracer.load_trace(trace_id)


def pretty_print_trace(spans: list[SpanEvent]) -> None:
    """Print trace as an indented tree to stdout."""
    from rich.console import Console
    from rich.tree import Tree

    console = Console()
    id_to_span = {s.span_id: s for s in spans}
    roots = [s for s in spans if not s.parent_span_id or s.parent_span_id not in id_to_span]

    def _add_children(tree_node: Any, span: SpanEvent) -> None:
        children = [s for s in spans if s.parent_span_id == span.span_id]
        for child in children:
            dur = f"{child.duration_ms:.1f}ms" if child.duration_ms else "?"
            label = f"[{'green' if child.status == 'ok' else 'red'}]{child.name}[/] ({child.kind}) {dur}"
            child_node = tree_node.add(label)
            _add_children(child_node, child)

    for root in roots:
        dur = f"{root.duration_ms:.1f}ms" if root.duration_ms else "?"
        t = Tree(f"[bold]{root.name}[/bold] ({root.kind}) {dur} trace={root.trace_id[:8]}")
        _add_children(t, root)
        console.print(t)


def cost_summary(trace_id: str, trace_dir: str = "./traces") -> dict:
    spans = load_trace(trace_id, trace_dir)
    total_cost = 0.0
    total_tokens = 0
    by_agent: dict[str, float] = {}
    for span in spans:
        if span.kind == "llm":
            cost = span.attributes.get("cost", 0.0)
            tokens = span.attributes.get("total_tokens", 0)
            agent_id = span.agent_id or "unknown"
            total_cost += cost
            total_tokens += tokens
            by_agent[agent_id] = by_agent.get(agent_id, 0.0) + cost
    return {
        "trace_id": trace_id,
        "total_cost_usd": round(total_cost, 6),
        "total_tokens": total_tokens,
        "by_agent": {k: round(v, 6) for k, v in by_agent.items()},
        "span_count": len(spans),
    }
