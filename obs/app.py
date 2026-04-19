"""
Swarm Observer — live Streamlit UI for monitoring swarm execution.
Launch via:  swarm obs
"""
from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading
import time
from datetime import datetime as _dt
from pathlib import Path
from typing import Optional

# ── Bootstrap project root ─────────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=False)

import streamlit as st

# ── Page config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Swarm Observer",
    page_icon="🐝",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Global CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
/* Span tree connector lines */
.span-tree { font-family: 'JetBrains Mono', 'Fira Mono', monospace; font-size: 0.82rem; }

/* Kind pills */
.pill {
    display: inline-block;
    padding: 1px 7px;
    border-radius: 10px;
    font-size: 0.72rem;
    font-weight: 600;
    letter-spacing: 0.03em;
    margin-left: 4px;
    vertical-align: middle;
}
.pill-tool   { background:#1f4e79; color:#9eceff; }
.pill-agent  { background:#1a3d1a; color:#7ddc7d; }
.pill-llm    { background:#3b1f5e; color:#d4aaff; }
.pill-task   { background:#5e3300; color:#ffbb6e; }
.pill-bus    { background:#3d1f00; color:#ffb347; }
.pill-custom { background:#2a2a2a; color:#aaa; }

/* Status dots */
.dot-ok    { color: #2ecc71; font-size: 0.8rem; }
.dot-error { color: #e74c3c; font-size: 0.8rem; }

/* Artifact status badges */
.badge-draft      { background:#555; color:#ddd; padding:2px 8px; border-radius:6px; font-size:0.75rem; }
.badge-approved   { background:#1a4a1a; color:#7ddc7d; padding:2px 8px; border-radius:6px; font-size:0.75rem; }
.badge-superseded { background:#4a3000; color:#ffcc80; padding:2px 8px; border-radius:6px; font-size:0.75rem; }

/* Topology card */
.topo-card {
    border: 1px solid #333;
    border-radius:8px;
    padding:10px 14px;
    margin-bottom:6px;
    background:#111;
}
</style>
""", unsafe_allow_html=True)

# ── Kind metadata ──────────────────────────────────────────────────────────────
KIND_ICON  = {"tool":"🔧","agent":"🤖","llm":"🧠","task":"📋","bus":"📡","custom":"⚙️"}
KIND_CLASS = {"tool":"tool","agent":"agent","llm":"llm","task":"task","bus":"bus","custom":"custom"}

# ── Topology discovery ─────────────────────────────────────────────────────────

def _scan_topologies() -> list[dict]:
    """Return list of {label, path, name, description, strategy} for all topology YAMLs."""
    import yaml
    results: list[dict] = []
    globs = [
        _ROOT / "examples" / "*" / "topology.yaml",
        _ROOT / "configs" / "*.yaml",
    ]
    seen: set[Path] = set()
    for pattern in globs:
        for p in sorted(_ROOT.glob(str(pattern.relative_to(_ROOT)))):
            if p in seen:
                continue
            seen.add(p)
            try:
                data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
                name = data.get("name", p.stem)
                desc = (data.get("description") or "").strip().splitlines()[0][:80] if data.get("description") else ""
                strategy = (data.get("coordination") or {}).get("strategy", "—")
                results.append({
                    "label": f"{name}  [{strategy}]",
                    "path": str(p.relative_to(_ROOT)),
                    "name": name,
                    "description": desc,
                    "strategy": strategy,
                    "agents": [a.get("role","?") for a in (data.get("agents") or [])],
                })
            except Exception:
                pass
    return results

# ── Observer patch ─────────────────────────────────────────────────────────────

_active_queue: Optional[queue.Queue] = None

def _patch_tracer_once() -> None:
    from observability import tracing as _t
    if getattr(_t.Tracer, "_obs_patched", False):
        return
    _orig = _t.Tracer._write
    def _patched(self, span):  # noqa: ANN001
        _orig(self, span)
        global _active_queue
        if _active_queue is not None:
            try:
                _active_queue.put_nowait(span.model_dump(mode="json"))
            except Exception:
                pass
    _t.Tracer._write = _patched  # type: ignore[method-assign]
    _t.Tracer._obs_patched = True  # type: ignore[attr-defined]

# ── Background swarm thread ────────────────────────────────────────────────────

def _run_swarm(goal: str, topology_path: Optional[str], q: queue.Queue, result: dict) -> None:
    global _active_queue
    _active_queue = q
    try:
        from configs.loader import load_swarm_config
        from core.registry import bootstrap_registries
        from observability.tracing import configure_tracer

        trace_dir = os.environ.get("SWARM_OBS_TRACE_DIR", "./traces")
        provider  = os.environ.get("SWARM_OBS_PROVIDER") or None
        model     = os.environ.get("SWARM_OBS_MODEL") or None

        overrides: dict = {"log_level": "WARNING", "trace_dir": trace_dir, "safety_mode": "auto"}
        if provider:
            overrides["provider"] = provider
        if model:
            overrides["default_model"] = model

        cfg = load_swarm_config(overrides)
        configure_tracer(trace_dir)
        _patch_tracer_once()

        bootstrap_registries(
            tools_dir=cfg.tools_dir,
            agents_dir=cfg.agents_dir,
            groq_api_key=cfg.groq_api_key,
            openrouter_api_key=cfg.openrouter_api_key,
            gemini_api_key=cfg.gemini_api_key,
            default_model=cfg.default_model,
        )

        from configs.loader import load_topology_spec
        from configs.schema import AgentSlot, TopologySpec
        from coordination.bus import create_bus
        from coordination.orchestrator import SwarmRuntime
        from core.registry import get_agent_spec_registry, get_provider_registry, get_tool_registry
        from memory.longterm import LocalChromaMemory
        from observability.cost import reset_ledger

        tr = get_tool_registry()
        ar = get_agent_spec_registry()
        pr = get_provider_registry()

        if topology_path:
            topology = load_topology_spec(Path(topology_path))
        else:
            roles = ar.list()
            topology = TopologySpec(
                name="default",
                agents=[AgentSlot(role=r) for r in roles] if roles else [AgentSlot(role="echo")],
            )

        bus      = create_bus(cfg.bus_transport, cfg.redis_url)
        longterm = LocalChromaMemory(persist_dir=cfg.memory_dir)
        ledger   = reset_ledger()
        prov     = pr.get_or_default(cfg.provider or "gemini")

        runtime = SwarmRuntime(
            topology=topology,
            provider=prov,
            tool_handlers={n: h for n, h in tr.items()},
            agent_specs={n: s for n, s in ar.items()},
            bus=bus,
            longterm_memory=longterm,
            ledger=ledger,
            deployment_mode=cfg.deployment_mode,
            redis_url=cfg.redis_url,
            deploy=False,
        )

        result["trace_id"]   = runtime.trace_id
        result["memory_dir"] = cfg.memory_dir
        result["status"]     = "running"

        r = asyncio.run(runtime.run(goal))

        result.update({
            "status":     "done",
            "success":    r.success,
            "output":     r.output,
            "run_error":  r.error,
            "cost":       r.cost,
            "tokens":     r.token_usage.total_tokens if r.token_usage else 0,
            "iterations": r.iterations,
        })
    except Exception as exc:
        import traceback as _tb
        result.update({"status": "error", "run_error": str(exc), "traceback": _tb.format_exc()})
    finally:
        _active_queue = None
        q.put(None)

# ── Session state init ─────────────────────────────────────────────────────────
for _k, _v in {
    "event_queue": None, "spans": [], "running": False,
    "result": None, "start_time": None, "topologies": None,
}.items():
    if _k not in st.session_state:
        st.session_state[_k] = _v

# Cache topology scan (once per session)
if st.session_state.topologies is None:
    st.session_state.topologies = _scan_topologies()
topos: list[dict] = st.session_state.topologies

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 🐝 Swarm Observer")
    st.divider()

    # ── Topology picker ────────────────────────────────────────────────────────
    st.markdown("#### Topology")
    topo_labels = ["(auto — all agents)"] + [t["label"] for t in topos]
    topo_choice = st.selectbox(
        "Select topology",
        options=range(len(topo_labels)),
        format_func=lambda i: topo_labels[i],
        disabled=st.session_state.running,
        label_visibility="collapsed",
    )

    selected_topo: Optional[dict] = topos[topo_choice - 1] if topo_choice > 0 else None

    if selected_topo:
        st.markdown(
            f"<div class='topo-card'>"
            f"<b>{selected_topo['name']}</b><br>"
            f"<span style='color:#aaa;font-size:0.8rem'>{selected_topo['description']}</span><br>"
            f"<span style='color:#666;font-size:0.75rem'>strategy: {selected_topo['strategy']}"
            f" · agents: {', '.join(selected_topo['agents'][:6])}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    custom_topo = st.text_input(
        "…or paste custom path",
        placeholder="examples/coding_swarm/topology.yaml",
        disabled=st.session_state.running,
    )
    topology_path: Optional[str] = custom_topo.strip() or (selected_topo["path"] if selected_topo else None)

    st.divider()

    # ── Goal ───────────────────────────────────────────────────────────────────
    st.markdown("#### Goal")
    goal_input = st.text_area(
        "Goal",
        placeholder="Describe what you want the swarm to accomplish…",
        height=110,
        disabled=st.session_state.running,
        label_visibility="collapsed",
    )

    c1, c2 = st.columns(2)
    run_btn   = c1.button("▶ Run",   type="primary",
                          disabled=st.session_state.running or not (goal_input or "").strip(),
                          use_container_width=True)
    clear_btn = c2.button("🗑 Clear", disabled=st.session_state.running,
                          use_container_width=True)

    st.divider()

    # ── Status ─────────────────────────────────────────────────────────────────
    if st.session_state.running:
        elapsed = time.time() - (st.session_state.start_time or time.time())
        st.info(f"⏳ Running… {elapsed:.0f}s")
    elif st.session_state.result:
        s = st.session_state.result.get("status")
        if s == "done":
            st.success("✓ Complete") if st.session_state.result.get("success") else st.error("✗ Failed")
        elif s == "error":
            st.error("✗ Error")

    # ── Options ────────────────────────────────────────────────────────────────
    with st.expander("⚙️ Provider / Model", expanded=False):
        obs_provider = st.selectbox("Provider", ["(from .env)", "gemini", "groq", "openrouter"],
                                    disabled=st.session_state.running)
        obs_model = st.text_input("Model override", placeholder="leave blank for default",
                                  disabled=st.session_state.running)

    st.divider()
    nc = len(st.session_state.spans)
    tool_n = sum(1 for s in st.session_state.spans if s.get("kind") == "tool")
    llm_n  = sum(1 for s in st.session_state.spans if s.get("kind") == "llm")
    st.caption(f"Spans: **{nc}** · Tools: **{tool_n}** · LLM: **{llm_n}**")
    _tid = (st.session_state.result or {}).get("trace_id")
    if _tid:
        st.caption(f"Trace: `{_tid[:8]}…`")

# ── Button handlers ────────────────────────────────────────────────────────────
if clear_btn:
    st.session_state.update({
        "spans": [], "result": None, "running": False,
        "start_time": None, "event_queue": None,
    })
    st.rerun()

if run_btn and (goal_input or "").strip():
    if obs_provider and obs_provider != "(from .env)":
        os.environ["SWARM_OBS_PROVIDER"] = obs_provider
    if obs_model:
        os.environ["SWARM_OBS_MODEL"] = obs_model

    eq = queue.Queue()
    res: dict = {}
    st.session_state.update({
        "spans": [], "result": res, "running": True,
        "start_time": time.time(), "event_queue": eq,
    })
    threading.Thread(
        target=_run_swarm,
        args=(goal_input.strip(), topology_path, eq, res),
        daemon=True,
    ).start()
    st.rerun()

# ── Drain queue ────────────────────────────────────────────────────────────────
if st.session_state.running and st.session_state.event_queue is not None:
    while True:
        try:
            item = st.session_state.event_queue.get_nowait()
            if item is None:
                st.session_state.running = False
                break
            st.session_state.spans.append(item)
        except queue.Empty:
            break

spans: list[dict] = st.session_state.spans

# ── Helpers ────────────────────────────────────────────────────────────────────

def _dur(span: dict) -> str:
    try:
        s = _dt.fromisoformat(span["start_time"])
        e = _dt.fromisoformat(span["end_time"])
        ms = (e - s).total_seconds() * 1000
        if ms < 1000:
            return f"{ms:.0f} ms"
        return f"{ms/1000:.2f} s"
    except Exception:
        return ""


def _dur_color(span: dict) -> str:
    try:
        s = _dt.fromisoformat(span["start_time"])
        e = _dt.fromisoformat(span["end_time"])
        ms = (e - s).total_seconds() * 1000
        if ms < 500:
            return "#2ecc71"
        if ms < 5000:
            return "#f39c12"
        return "#e74c3c"
    except Exception:
        return "#aaa"


def _pill(kind: str) -> str:
    cls = KIND_CLASS.get(kind, "custom")
    return f"<span class='pill pill-{cls}'>{kind}</span>"


def _status_dot(status: str) -> str:
    if status == "ok":
        return "<span class='dot-ok'>●</span>"
    return "<span class='dot-error'>●</span>"


def _ts(span: dict) -> str:
    raw = span.get("start_time", "")
    try:
        return _dt.fromisoformat(raw).strftime("%H:%M:%S")
    except Exception:
        return raw[:19] if raw else ""


def _span_summary(span: dict) -> str:
    """One-line human description of what this span did."""
    kind  = span.get("kind", "")
    name  = span.get("name", "")
    attrs = span.get("attributes", {})
    agent = span.get("agent_id") or ""

    if kind == "tool":
        inp = attrs.get("input") or attrs.get("tool_input") or attrs.get("args")
        if isinstance(inp, dict):
            snippet = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(inp.items())[:2])
        elif inp:
            snippet = str(inp)[:60]
        else:
            snippet = ""
        return f"called `{name}`" + (f" → {snippet}" if snippet else "")

    if kind == "llm":
        model  = attrs.get("model", "")
        tokens = attrs.get("total_tokens", "")
        cost   = attrs.get("cost", 0.0)
        return f"LLM call" + (f" [{model}]" if model else "") + (f" · {tokens} tok · ${cost:.5f}" if tokens else "")

    if kind == "agent":
        role = attrs.get("role", name)
        goal = str(attrs.get("goal") or attrs.get("task") or "")[:60]
        return f"agent `{role}`" + (f" → {goal}" if goal else "")

    if kind == "task":
        desc = str(attrs.get("description") or attrs.get("goal") or name)[:70]
        return desc

    return name


def _render_attrs(attrs: dict) -> None:
    """Render attributes with smart per-kind formatting."""
    if not attrs:
        return
    # Surface key fields first
    for key in ("input", "tool_input", "args"):
        if attrs.get(key) is not None:
            st.markdown("**↗ Input**")
            v = attrs[key]
            if isinstance(v, dict):
                st.json(v, expanded=True)
            else:
                st.code(str(v)[:3000], language=None)
            break
    for key in ("output", "tool_output", "result"):
        if attrs.get(key) is not None:
            st.markdown("**↙ Output**")
            v = attrs[key]
            if isinstance(v, dict):
                st.json(v, expanded=False)
            else:
                out = str(v)
                st.code(out[:3000], language=None)
            break
    for key in ("prompt", "messages"):
        if attrs.get(key) is not None:
            st.markdown("**Prompt / Messages**")
            v = attrs[key]
            if isinstance(v, list):
                st.json(v, expanded=False)
            else:
                st.code(str(v)[:3000], language=None)
            break
    for key in ("response", "content"):
        if attrs.get(key) is not None:
            st.markdown("**Response**")
            st.code(str(attrs[key])[:3000], language=None)
            break
    # Remaining attrs
    skip = {"input","tool_input","args","output","tool_output","result","prompt","messages","response","content"}
    rest = {k: v for k, v in attrs.items() if k not in skip}
    if rest:
        with st.expander("All attributes", expanded=False):
            st.json(rest, expanded=False)


# ── Main tabs ──────────────────────────────────────────────────────────────────
tab_feed, tab_tools, tab_llm, tab_trace, tab_agents, tab_artifacts, tab_result = st.tabs([
    "📡 Live Feed", "🔧 Tool Calls", "🧠 LLM", "🌲 Trace", "🤖 By Agent", "📦 Artifacts", "📄 Result",
])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Live Feed
# ═══════════════════════════════════════════════════════════════════════════════
with tab_feed:
    if not spans:
        st.caption("No events yet — type a goal and click ▶ Run.")
    else:
        # Summary row
        by_kind: dict[str, int] = {}
        for sp in spans:
            k = sp.get("kind", "custom")
            by_kind[k] = by_kind.get(k, 0) + 1
        cols = st.columns(max(len(by_kind), 1))
        for i, (k, cnt) in enumerate(sorted(by_kind.items())):
            cols[i].metric(f"{KIND_ICON.get(k,'⚙️')} {k}", cnt)
        st.divider()

        # Filter
        cf1, cf2 = st.columns([2, 1])
        feed_kind  = cf1.multiselect("Filter by kind", options=list(by_kind.keys()), default=[])
        feed_limit = cf2.number_input("Max shown", min_value=10, max_value=500, value=100, step=10)

        filtered = [s for s in spans if not feed_kind or s.get("kind") in feed_kind]
        with st.container(height=620):
            for sp in reversed(filtered[-int(feed_limit):]):
                kind   = sp.get("kind", "custom")
                icon   = KIND_ICON.get(kind, "⚙️")
                status = sp.get("status", "ok")
                dur    = _dur(sp)
                ts     = _ts(sp)
                agent  = sp.get("agent_id") or ""
                summary = _span_summary(sp)

                dot   = "✅" if status == "ok" else "❌"
                label = f"{dot} {icon} **{sp.get('name','')}** — {summary}"
                if dur:
                    label += f"  `{dur}`"
                if agent:
                    label += f"  · _{agent}_"
                if ts:
                    label += f"  `{ts}`"

                with st.expander(label, expanded=False):
                    c1, c2, c3, c4 = st.columns(4)
                    c1.markdown(f"**Kind:** `{kind}`")
                    c2.markdown(f"**Status:** `{status}`")
                    c3.markdown(f"**Duration:** `{dur or '—'}`")
                    c4.markdown(f"**Agent:** `{agent or '—'}`")
                    if sp.get("task_id"):
                        st.markdown(f"**Task ID:** `{sp['task_id']}`")
                    st.divider()
                    _render_attrs(sp.get("attributes", {}))
                    if sp.get("error"):
                        st.error(sp["error"])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Tool Calls
# ═══════════════════════════════════════════════════════════════════════════════
with tab_tools:
    tool_spans = [s for s in spans if s.get("kind") == "tool"]
    if not tool_spans:
        st.caption("No tool calls recorded yet.")
    else:
        # Aggregation row
        tool_names: dict[str, int] = {}
        for s in tool_spans:
            n = s.get("name", "?")
            tool_names[n] = tool_names.get(n, 0) + 1
        errors = sum(1 for s in tool_spans if s.get("status") != "ok")

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Total calls", len(tool_spans))
        m2.metric("Unique tools", len(tool_names))
        m3.metric("Errors", errors, delta=None, delta_color="inverse" if errors else "off")
        avg_ms = 0.0
        for s in tool_spans:
            try:
                sv = _dt.fromisoformat(s["start_time"])
                ev = _dt.fromisoformat(s["end_time"])
                avg_ms += (ev - sv).total_seconds() * 1000
            except Exception:
                pass
        m4.metric("Avg duration", f"{avg_ms / max(len(tool_spans),1):.0f} ms")

        st.divider()

        # Which tools were called
        with st.expander("Tool frequency", expanded=False):
            try:
                import pandas as pd
                st.bar_chart(pd.Series(tool_names).sort_values(ascending=False))
            except ImportError:
                for n, c in sorted(tool_names.items(), key=lambda x: -x[1]):
                    st.markdown(f"- `{n}` — {c}×")

        # Individual calls
        tf1, tf2 = st.columns([3, 1])
        tool_filter = tf1.multiselect("Filter by tool", options=list(tool_names.keys()), default=[])
        only_errors = tf2.checkbox("Errors only")

        shown = [
            s for s in tool_spans
            if (not tool_filter or s.get("name") in tool_filter)
            and (not only_errors or s.get("status") != "ok")
        ]

        for sp in reversed(shown):
            name   = sp.get("name", "unknown")
            agent  = sp.get("agent_id") or "—"
            status = sp.get("status", "ok")
            dur    = _dur(sp)
            attrs  = sp.get("attributes", {})
            dc     = _dur_color(sp)
            badge  = "✅" if status == "ok" else "❌"

            # Quick output preview for label
            out_preview = ""
            for k in ("output", "tool_output", "result"):
                if attrs.get(k):
                    out_preview = str(attrs[k])[:50].replace("\n", " ")
                    break

            label = f"{badge} 🔧 **{name}** · agent `{agent}` · <span style='color:{dc}'>{dur}</span>"
            if out_preview:
                label += f" → _{out_preview}_"

            with st.expander(label, expanded=False):
                c1, c2, c3 = st.columns(3)
                c1.markdown(f"**Tool:** `{name}`")
                c2.markdown(f"**Agent:** `{agent}`")
                c3.markdown(f"**Status:** `{status}` · **Duration:** `{dur}`")
                st.divider()
                _render_attrs(attrs)
                if sp.get("error"):
                    st.error(sp["error"])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: LLM Calls
# ═══════════════════════════════════════════════════════════════════════════════
with tab_llm:
    llm_spans = [s for s in spans if s.get("kind") == "llm"]
    if not llm_spans:
        st.caption("No LLM calls recorded yet.")
    else:
        total_tokens = sum(s.get("attributes", {}).get("total_tokens", 0) for s in llm_spans)
        total_cost   = sum(s.get("attributes", {}).get("cost", 0.0) for s in llm_spans)
        total_prompt = sum(s.get("attributes", {}).get("prompt_tokens", 0) for s in llm_spans)
        total_comp   = sum(s.get("attributes", {}).get("completion_tokens", 0) for s in llm_spans)

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("LLM calls", len(llm_spans))
        m2.metric("Total tokens", f"{total_tokens:,}")
        m3.metric("Prompt tok", f"{total_prompt:,}")
        m4.metric("Completion tok", f"{total_comp:,}")
        m5.metric("Est. cost", f"${total_cost:.4f}")
        st.divider()

        for sp in reversed(llm_spans):
            attrs  = sp.get("attributes", {})
            agent  = sp.get("agent_id") or "—"
            model  = attrs.get("model", "?")
            tokens = attrs.get("total_tokens", "?")
            cost   = attrs.get("cost", 0.0)
            dur    = _dur(sp)
            status = sp.get("status", "ok")
            badge  = "✅" if status == "ok" else "❌"

            with st.expander(
                f"{badge} 🧠 `{model}` · agent `{agent}` · {tokens} tok · ${cost:.5f} · {dur}",
                expanded=False,
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.markdown(f"**Model:** `{model}`")
                c2.markdown(f"**Prompt:** `{attrs.get('prompt_tokens','?')} tok`")
                c3.markdown(f"**Completion:** `{attrs.get('completion_tokens','?')} tok`")
                c4.markdown(f"**Duration:** `{dur}`")
                st.divider()
                _render_attrs(attrs)
                if sp.get("error"):
                    st.error(sp["error"])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Trace Tree
# ═══════════════════════════════════════════════════════════════════════════════
with tab_trace:
    if not spans:
        st.caption("No trace data yet.")
    else:
        # Build parent→children map
        id_map: dict[str, dict] = {s["span_id"]: s for s in spans if "span_id" in s}
        children: dict[str, list[dict]] = {sid: [] for sid in id_map}
        roots: list[dict] = []
        for sp in spans:
            if "span_id" not in sp:
                continue
            pid = sp.get("parent_span_id")
            if pid and pid in children:
                children[pid].append(sp)
            else:
                roots.append(sp)

        # Sort children by start_time
        for kids in children.values():
            kids.sort(key=lambda x: x.get("start_time", ""))

        # Render tree recursively as styled HTML
        def _tree_html(sp: dict, depth: int = 0, is_last: bool = True) -> str:
            kind    = sp.get("kind", "custom")
            icon    = KIND_ICON.get(kind, "⚙️")
            name    = sp.get("name", "?")
            status  = sp.get("status", "ok")
            dur     = _dur(sp)
            ts      = _ts(sp)
            agent   = sp.get("agent_id") or ""
            summary = _span_summary(sp)
            dc      = _dur_color(sp)

            indent  = "&nbsp;" * (depth * 4)
            connector = "└─" if is_last else "├─"
            dot     = "✓" if status == "ok" else "✗"
            dot_col = "#2ecc71" if status == "ok" else "#e74c3c"
            pill    = _pill(kind)

            agent_html = f" <span style='color:#666;font-size:0.78rem'>@{agent}</span>" if agent else ""
            dur_html   = f" <span style='color:{dc};font-size:0.78rem'>{dur}</span>" if dur else ""
            ts_html    = f" <span style='color:#555;font-size:0.75rem'>{ts}</span>" if ts else ""
            dot_html   = f"<span style='color:{dot_col}'>{dot}</span>"

            row = (
                f"<div style='line-height:1.8;'>"
                f"<span style='color:#444'>{indent}{connector}</span> "
                f"{dot_html} {icon} <b>{name}</b>{pill}"
                f" <span style='color:#bbb;font-size:0.82rem'>— {summary}</span>"
                f"{agent_html}{dur_html}{ts_html}"
                f"</div>"
            )

            child_list = children.get(sp.get("span_id", ""), [])
            child_html = "".join(
                _tree_html(c, depth + 1, i == len(child_list) - 1)
                for i, c in enumerate(child_list)
            )
            return row + child_html

        html_tree = "".join(_tree_html(r, 0, i == len(roots) - 1) for i, r in enumerate(roots))
        st.markdown(
            f"<div style='"
            f"font-family:monospace; font-size:0.82rem; "
            f"overflow-x:auto; overflow-y:auto; max-height:580px; "
            f"border:1px solid #222; border-radius:6px; padding:12px;'>"
            f"{html_tree}</div>",
            unsafe_allow_html=True,
        )

        st.divider()

        # Flat timeline table
        st.markdown("#### Timeline")
        try:
            import pandas as pd
            rows = []
            for sp in spans:
                dur_ms = ""
                try:
                    sv = _dt.fromisoformat(sp["start_time"])
                    ev = _dt.fromisoformat(sp["end_time"])
                    dur_ms = f"{(ev-sv).total_seconds()*1000:.0f}"
                except Exception:
                    pass
                rows.append({
                    "Kind":     sp.get("kind", ""),
                    "Name":     sp.get("name", ""),
                    "Summary":  _span_summary(sp)[:60],
                    "Agent":    sp.get("agent_id") or "—",
                    "Status":   sp.get("status", ""),
                    "Started":  _ts(sp),
                    "Dur (ms)": dur_ms,
                })
            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True, height=320)
        except ImportError:
            st.caption("Install pandas for the timeline table.")

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: By Agent
# ═══════════════════════════════════════════════════════════════════════════════
with tab_agents:
    if not spans:
        st.caption("No spans yet.")
    else:
        agent_map: dict[str, list[dict]] = {}
        for sp in spans:
            ag = sp.get("agent_id") or "(orchestrator)"
            agent_map.setdefault(ag, []).append(sp)

        for ag, ag_spans in sorted(agent_map.items()):
            tools_used = [s.get("name") for s in ag_spans if s.get("kind") == "tool"]
            llm_count  = sum(1 for s in ag_spans if s.get("kind") == "llm")
            tok_total  = sum(s.get("attributes", {}).get("total_tokens", 0) for s in ag_spans)
            cost_total = sum(s.get("attributes", {}).get("cost", 0.0) for s in ag_spans)

            with st.expander(
                f"🤖 **{ag}** — {len(ag_spans)} spans · {len(set(tools_used))} tools · {llm_count} LLM calls · ${cost_total:.4f}",
                expanded=False,
            ):
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Spans", len(ag_spans))
                c2.metric("LLM calls", llm_count)
                c3.metric("Tokens", f"{tok_total:,}")
                c4.metric("Cost", f"${cost_total:.4f}")

                if tools_used:
                    st.markdown("**Tools called:** " + " · ".join(f"`{t}`" for t in sorted(set(tools_used))))
                st.divider()

                for sp in ag_spans:
                    kind   = sp.get("kind", "")
                    icon   = KIND_ICON.get(kind, "⚙️")
                    status = sp.get("status", "ok")
                    dur    = _dur(sp)
                    dot    = "✅" if status == "ok" else "❌"
                    summary = _span_summary(sp)

                    with st.expander(f"{dot} {icon} **{sp.get('name','')}** — {summary}  `{dur}`", expanded=False):
                        _render_attrs(sp.get("attributes", {}))
                        if sp.get("error"):
                            st.error(sp["error"])

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Artifacts
# ═══════════════════════════════════════════════════════════════════════════════
with tab_artifacts:
    # Determine memory dir from result or fall back to default
    memory_dir = (st.session_state.result or {}).get("memory_dir", "./memory_store")

    col_af1, col_af2 = st.columns([3, 1])
    with col_af2:
        refresh_artifacts = st.button("🔄 Refresh", use_container_width=True)

    @st.cache_data(ttl=5, show_spinner=False)
    def _load_artifacts(mdir: str) -> list[dict]:
        try:
            from memory.artifacts import ArtifactRegistry
            reg = ArtifactRegistry(persist_dir=mdir)
            items = asyncio.run(reg.list_all())
            return [a.model_dump(mode="json") for a in items]
        except Exception as exc:
            return [{"_error": str(exc)}]

    if refresh_artifacts:
        st.cache_data.clear()

    raw_artifacts = _load_artifacts(memory_dir)

    if not raw_artifacts:
        st.caption("No artifacts yet. Run a workforce topology to generate artifacts.")
    elif raw_artifacts and raw_artifacts[0].get("_error"):
        st.warning(f"Could not load artifacts: {raw_artifacts[0]['_error']}")
    else:
        # Filter controls
        all_types   = sorted(set(a.get("artifact_type","") for a in raw_artifacts))
        all_stages  = sorted(set(a.get("stage_id","") or "—" for a in raw_artifacts))
        all_statuses = ["draft","approved","superseded"]

        fc1, fc2, fc3 = st.columns(3)
        f_type   = fc1.multiselect("Type",   options=all_types,    default=[])
        f_stage  = fc2.multiselect("Stage",  options=all_stages,   default=[])
        f_status = fc3.multiselect("Status", options=all_statuses, default=[])

        filtered_arts = [
            a for a in raw_artifacts
            if (not f_type   or a.get("artifact_type") in f_type)
            and (not f_stage  or (a.get("stage_id") or "—") in f_stage)
            and (not f_status or a.get("status") in f_status)
        ]

        # Summary metrics
        by_status: dict[str, int] = {}
        for a in raw_artifacts:
            s = a.get("status", "?")
            by_status[s] = by_status.get(s, 0) + 1
        sm1, sm2, sm3, sm4 = st.columns(4)
        sm1.metric("Total",      len(raw_artifacts))
        sm2.metric("Draft",      by_status.get("draft", 0))
        sm3.metric("Approved",   by_status.get("approved", 0))
        sm4.metric("Superseded", by_status.get("superseded", 0))
        st.divider()

        # Status badge HTML
        STATUS_BADGE = {
            "draft":      "<span class='badge-draft'>draft</span>",
            "approved":   "<span class='badge-approved'>✓ approved</span>",
            "superseded": "<span class='badge-superseded'>superseded</span>",
        }

        # Group by type for cleaner presentation
        by_type_map: dict[str, list[dict]] = {}
        for a in filtered_arts:
            t = a.get("artifact_type", "unknown")
            by_type_map.setdefault(t, []).append(a)

        for atype, arts in sorted(by_type_map.items()):
            st.markdown(f"#### 📦 {atype}  <span style='color:#666;font-size:0.85rem'>({len(arts)})</span>",
                        unsafe_allow_html=True)

            for art in sorted(arts, key=lambda x: x.get("created_at",""), reverse=True):
                aid     = art.get("id","")
                status  = art.get("status","")
                stage   = art.get("stage_id") or "—"
                author  = art.get("author_agent_id") or "—"
                version = art.get("version", 1)
                created = str(art.get("created_at",""))[:19]
                lineage = art.get("lineage",[])
                badge   = STATUS_BADGE.get(status, status)

                header = (
                    f"{badge} &nbsp; `{aid[:8]}` &nbsp; "
                    f"stage: **{stage}** &nbsp; author: `{author}` &nbsp; "
                    f"v{version} &nbsp; `{created}`"
                )

                with st.expander(header, expanded=False):
                    # Render type-specific fields intelligently
                    skip_base = {"id","artifact_type","version","stage_id","author_agent_id",
                                 "project_id","created_at","status","lineage"}
                    type_fields = {k: v for k, v in art.items() if k not in skip_base}

                    if type_fields:
                        # Try to surface the most meaningful text fields up front
                        text_keys = [k for k, v in type_fields.items()
                                     if isinstance(v, str) and len(v) > 20]
                        list_keys = [k for k, v in type_fields.items() if isinstance(v, list)]

                        for k in text_keys:
                            st.markdown(f"**{k.replace('_',' ').title()}**")
                            st.markdown(type_fields[k])
                            st.divider()

                        for k in list_keys:
                            val = type_fields[k]
                            if not val:
                                continue
                            st.markdown(f"**{k.replace('_',' ').title()}** ({len(val)})")
                            if val and isinstance(val[0], dict):
                                try:
                                    import pandas as pd
                                    st.dataframe(pd.DataFrame(val), use_container_width=True)
                                except Exception:
                                    st.json(val, expanded=False)
                            else:
                                for item in val:
                                    st.markdown(f"- {item}")

                        # Remaining scalar fields
                        rest = {k: v for k, v in type_fields.items()
                                if k not in text_keys and k not in list_keys and v}
                        if rest:
                            sc = st.columns(min(len(rest), 3))
                            for i, (k, v) in enumerate(rest.items()):
                                sc[i % 3].markdown(f"**{k.replace('_',' ').title()}:** `{v}`")

                    if lineage:
                        st.divider()
                        st.markdown(f"**Lineage chain:** " + " → ".join(f"`{p[:8]}`" for p in lineage))

# ═══════════════════════════════════════════════════════════════════════════════
# Tab: Result
# ═══════════════════════════════════════════════════════════════════════════════
with tab_result:
    result = st.session_state.result

    if not result:
        st.caption("Run a goal to see the final result here.")
    elif result.get("status") == "running" or st.session_state.running:
        st.info("⏳ Swarm is still running…")
    elif result.get("status") == "error":
        st.error("The swarm raised an unhandled exception:")
        st.code(result.get("traceback") or result.get("run_error","Unknown error"))
    else:
        if result.get("success"):
            st.success("✓ Swarm completed successfully")
        else:
            st.error(f"✗ Swarm failed — {result.get('run_error','')}")

        output = result.get("output")
        if output:
            st.markdown("### Output")
            st.markdown(str(output))

        c1, c2, c3 = st.columns(3)
        c1.metric("Cost (USD)",  f"${result.get('cost',0.0):.4f}")
        c2.metric("Tokens",      f"{result.get('tokens',0):,}")
        c3.metric("Iterations",  result.get("iterations",0))

        tid = result.get("trace_id")
        if tid:
            st.divider()
            st.caption(f"Full trace ID: `{tid}`")
            st.caption(f"Inspect trace: `swarm trace {tid}`")

# ── Auto-refresh while running ─────────────────────────────────────────────────
if st.session_state.running:
    time.sleep(0.6)
    st.rerun()
