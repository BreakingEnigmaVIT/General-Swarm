"""
Swarm CLI — primary user surface.

Commands:
  swarm run <topology> --goal "<text>"
  swarm list agents|tools|providers|topologies
  swarm scaffold agent|tool|topology <name>
  swarm validate <file>
  swarm replay <trace-id>
  swarm trace <trace-id>
  swarm cost [<trace-id>|--since <date>]
  swarm dashboard
  swarm doctor
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config(
    api_key: Optional[str],
    model: Optional[str],
    log_level: str,
    trace_dir: str,
    safety_mode: str,
) -> "SwarmConfig":  # type: ignore[name-defined]
    from configs.loader import load_swarm_config
    overrides = {}
    if api_key:
        overrides["groq_api_key"] = api_key
    if model:
        overrides["default_model"] = model
    overrides["log_level"] = log_level
    overrides["trace_dir"] = trace_dir
    overrides["safety_mode"] = safety_mode
    return load_swarm_config(overrides)


def _setup_logging(cfg: "SwarmConfig") -> None:  # type: ignore[name-defined]
    from observability.logging import configure_logging
    configure_logging(cfg.log_level, cfg.log_file)


def _bootstrap(cfg: "SwarmConfig") -> tuple:  # type: ignore[name-defined]
    from core.registry import bootstrap_registries
    from observability.tracing import configure_tracer
    configure_tracer(cfg.trace_dir)
    tr, ar, pr = bootstrap_registries(
        tools_dir=cfg.tools_dir,
        agents_dir=cfg.agents_dir,
        groq_api_key=cfg.groq_api_key,
        default_model=cfg.default_model,
    )
    return tr, ar, pr


def _build_runtime(cfg, topology_path: Optional[str]):
    from configs.loader import load_topology_spec
    from configs.schema import TopologySpec, AgentSlot
    from coordination.bus import create_bus
    from coordination.orchestrator import SwarmRuntime
    from memory.longterm import LocalChromaMemory
    from observability.cost import reset_ledger
    from core.registry import get_tool_registry, get_agent_spec_registry, get_provider_registry

    tr = get_tool_registry()
    ar = get_agent_spec_registry()
    pr = get_provider_registry()

    if topology_path:
        topology = load_topology_spec(Path(topology_path))
    else:
        # Default single-agent topology using all registered agents
        roles = ar.list()
        topology = TopologySpec(
            name="default",
            agents=[AgentSlot(role=r) for r in roles] if roles else [AgentSlot(role="echo")],
        )

    bus = create_bus(cfg.bus_transport, cfg.redis_url)
    longterm = LocalChromaMemory(persist_dir=cfg.memory_dir)
    ledger = reset_ledger()

    agent_specs = {name: spec for name, spec in ar.items()}
    tool_handlers = {name: handler for name, handler in tr.items()}
    provider = pr.get_or_default("groq")

    runtime = SwarmRuntime(
        topology=topology,
        provider=provider,
        tool_handlers=tool_handlers,
        agent_specs=agent_specs,
        bus=bus,
        longterm_memory=longterm,
        ledger=ledger,
    )
    return runtime, ledger


# ── Main group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="swarm")
def cli() -> None:
    """Swarm — a general-purpose, extensible LLM agent swarm backed by Groq."""


# ── run ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topology", required=False, default=None)
@click.option("--goal", "-g", required=True, help="High-level goal for the swarm")
@click.option("--api-key", envvar="GROQ_API_KEY", default=None, help="Groq API key")
@click.option("--model", "-m", default=None, help="Override default LLM model")
@click.option("--log-level", default="INFO", show_default=True)
@click.option("--trace-dir", default="./traces", show_default=True)
@click.option("--safety-mode", default="interactive", type=click.Choice(["interactive", "auto"]))
@click.option("--budget", default=None, type=float, help="Max spend in USD")
@click.option("--json", "output_json", is_flag=True, help="Output result as JSON")
def run(
    topology: Optional[str],
    goal: str,
    api_key: Optional[str],
    model: Optional[str],
    log_level: str,
    trace_dir: str,
    safety_mode: str,
    budget: Optional[float],
    output_json: bool,
) -> None:
    """Launch a swarm against a goal.

    TOPOLOGY is an optional path to a topology YAML file.
    If omitted, a default single-agent topology is used.

    \b
    Example:
      swarm run --goal "Research the latest LLM benchmarks"
      swarm run configs/research.yaml --goal "Write a Python web scraper"
    """
    cfg = _load_config(api_key, model, log_level, trace_dir, safety_mode)
    _setup_logging(cfg)

    if not cfg.groq_api_key:
        console.print("[bold red]Error:[/bold red] GROQ_API_KEY is not set.")
        console.print("  Set it in .env or pass --api-key")
        sys.exit(1)

    _bootstrap(cfg)

    runtime, ledger = _build_runtime(cfg, topology)
    if budget:
        runtime.topology.budget.max_cost_usd = budget

    console.print(Panel(
        f"[bold cyan]Goal:[/bold cyan] {goal}\n"
        f"[dim]Trace ID:[/dim] {runtime.trace_id[:8]}…",
        title="[bold]Swarm Run[/bold]",
        border_style="blue",
    ))

    result = asyncio.run(runtime.run(goal))

    if output_json:
        click.echo(json.dumps({
            "trace_id": runtime.trace_id,
            "output": result.output,
            "success": result.success,
            "cost_usd": result.cost,
            "tokens": result.token_usage.total_tokens,
            "iterations": result.iterations,
            "error": result.error,
        }, indent=2))
        return

    border = "green" if result.success else "red"
    status = "✓ Complete" if result.success else "✗ Failed"
    console.print(Panel(
        str(result.output or result.error or "No output"),
        title=f"[bold]{status}[/bold]",
        border_style=border,
    ))
    console.print(
        f"[dim]Cost: ${result.cost:.4f}  |  "
        f"Tokens: {result.token_usage.total_tokens}  |  "
        f"Iterations: {result.iterations}  |  "
        f"Trace: {runtime.trace_id[:8]}[/dim]"
    )


# ── list ──────────────────────────────────────────────────────────────────────

@cli.command("list")
@click.argument("kind", type=click.Choice(["agents", "tools", "providers", "topologies"]))
@click.option("--agents-dir", default="./agents")
@click.option("--tools-dir", default="./tools")
@click.option("--configs-dir", default="./configs")
@click.option("--json", "output_json", is_flag=True)
def list_cmd(
    kind: str, agents_dir: str, tools_dir: str, configs_dir: str, output_json: bool
) -> None:
    """List registered agents, tools, providers, or topologies."""
    from configs.loader import load_swarm_config
    cfg = load_swarm_config()
    _setup_logging(cfg)

    if kind == "agents":
        from configs.loader import load_agent_spec
        rows = []
        for spec_path in sorted(Path(agents_dir).rglob("spec.yaml")):
            try:
                s = load_agent_spec(spec_path)
                rows.append((s.role, s.description[:60], s.model, ", ".join(s.tools[:3])))
            except Exception as e:
                rows.append((str(spec_path), f"ERROR: {e}", "", ""))
        if output_json:
            click.echo(json.dumps([{"role": r[0], "description": r[1]} for r in rows], indent=2))
            return
        t = Table(title="Registered Agents")
        for col in ("Role", "Description", "Model", "Tools"):
            t.add_column(col)
        for row in rows:
            t.add_row(*row)
        console.print(t)

    elif kind == "tools":
        from configs.loader import load_tool_spec
        rows = []
        for spec_path in sorted(Path(tools_dir).rglob("spec.yaml")):
            try:
                s = load_tool_spec(spec_path)
                rows.append((s.name, s.description[:60], s.side_effect_level))
            except Exception as e:
                rows.append((str(spec_path), f"ERROR: {e}", ""))
        if output_json:
            click.echo(json.dumps([{"name": r[0], "description": r[1]} for r in rows], indent=2))
            return
        t = Table(title="Registered Tools")
        for col in ("Name", "Description", "Side Effects"):
            t.add_column(col)
        for row in rows:
            t.add_row(*row)
        console.print(t)

    elif kind == "providers":
        rows = [("groq", "Groq LLaMA API", "https://groq.com")]
        if output_json:
            click.echo(json.dumps([{"name": r[0]} for r in rows], indent=2))
            return
        t = Table(title="Providers")
        for col in ("Name", "Description", "URL"):
            t.add_column(col)
        for row in rows:
            t.add_row(*row)
        console.print(t)

    elif kind == "topologies":
        rows = []
        for p in sorted(Path(configs_dir).glob("*.yaml")):
            rows.append((p.stem, str(p)))
        if output_json:
            click.echo(json.dumps([{"name": r[0], "path": r[1]} for r in rows], indent=2))
            return
        t = Table(title="Topology Files")
        for col in ("Name", "Path"):
            t.add_column(col)
        for row in rows:
            t.add_row(*row)
        console.print(t)


# ── scaffold ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("kind", type=click.Choice(["agent", "tool", "topology"]))
@click.argument("name")
@click.option("--agents-dir", default="./agents")
@click.option("--tools-dir", default="./tools")
@click.option("--configs-dir", default="./configs")
def scaffold(
    kind: str, name: str, agents_dir: str, tools_dir: str, configs_dir: str
) -> None:
    """Generate a new agent, tool, or topology template.

    \b
    Examples:
      swarm scaffold agent my-analyst
      swarm scaffold tool sql-query
      swarm scaffold topology data-pipeline
    """
    from cli.scaffold import scaffold_agent, scaffold_tool, scaffold_topology
    if kind == "agent":
        scaffold_agent(name, agents_dir)
    elif kind == "tool":
        scaffold_tool(name, tools_dir)
    elif kind == "topology":
        scaffold_topology(name, configs_dir)


# ── validate ──────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("path")
def validate(path: str) -> None:
    """Validate a spec or topology YAML file."""
    p = Path(path)
    if not p.exists():
        console.print(f"[red]File not found: {path}[/red]")
        sys.exit(1)
    try:
        if "agents" in str(p):
            from configs.loader import load_agent_spec
            s = load_agent_spec(p)
            console.print(f"[green]✓ Agent spec valid:[/green] role={s.role}, model={s.model}")
        elif "tools" in str(p):
            from configs.loader import load_tool_spec
            s = load_tool_spec(p)
            console.print(f"[green]✓ Tool spec valid:[/green] name={s.name}")
        else:
            from configs.loader import load_topology_spec
            s = load_topology_spec(p)
            console.print(f"[green]✓ Topology valid:[/green] name={s.name}")
    except Exception as exc:
        console.print(f"[bold red]✗ Validation failed:[/bold red] {exc}")
        sys.exit(1)


# ── trace ─────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("trace_id")
@click.option("--trace-dir", default="./traces")
@click.option("--json", "output_json", is_flag=True)
def trace(trace_id: str, trace_dir: str, output_json: bool) -> None:
    """Pretty-print or dump a trace."""
    from observability.replay import load_trace, pretty_print_trace
    spans = load_trace(trace_id, trace_dir)
    if not spans:
        console.print(f"[red]Trace '{trace_id}' not found in {trace_dir}[/red]")
        sys.exit(1)
    if output_json:
        click.echo(json.dumps([s.model_dump(mode="json") for s in spans], indent=2, default=str))
    else:
        pretty_print_trace(spans)


# ── replay ────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("trace_id")
@click.option("--trace-dir", default="./traces")
@click.option("--fresh", is_flag=True, help="Re-run with live LLM calls instead of cached outputs")
def replay(trace_id: str, trace_dir: str, fresh: bool) -> None:
    """Re-execute a historical trace for debugging or regression testing."""
    from observability.replay import load_trace, pretty_print_trace
    spans = load_trace(trace_id, trace_dir)
    if not spans:
        console.print(f"[red]Trace {trace_id!r} not found[/red]")
        sys.exit(1)

    if not fresh:
        console.print(f"[yellow]Replaying trace {trace_id[:8]} (deterministic — cached outputs)[/yellow]")
        pretty_print_trace(spans)
        console.print("[dim]Use --fresh to re-run with live LLM calls[/dim]")
    else:
        console.print(f"[yellow]Fresh replay not yet implemented — use 'swarm run' instead[/yellow]")


# ── cost ──────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("trace_id", required=False)
@click.option("--trace-dir", default="./traces")
@click.option("--json", "output_json", is_flag=True)
def cost(trace_id: Optional[str], trace_dir: str, output_json: bool) -> None:
    """Show token usage and cost for a trace (or all traces)."""
    from observability.replay import cost_summary, load_trace
    from observability.tracing import Tracer

    tracer = Tracer(trace_dir)

    if trace_id:
        summary = cost_summary(trace_id, trace_dir)
        if output_json:
            click.echo(json.dumps(summary, indent=2))
        else:
            t = Table(title=f"Cost: {trace_id[:8]}")
            t.add_column("Metric")
            t.add_column("Value")
            t.add_row("Total cost (USD)", f"${summary['total_cost_usd']:.6f}")
            t.add_row("Total tokens", str(summary["total_tokens"]))
            t.add_row("Spans", str(summary["span_count"]))
            console.print(t)
    else:
        trace_ids = tracer.list_traces()
        rows = []
        for tid in trace_ids[-20:]:
            s = cost_summary(tid, trace_dir)
            rows.append((tid[:8], f"${s['total_cost_usd']:.6f}", str(s["total_tokens"])))
        if output_json:
            click.echo(json.dumps(rows, indent=2))
        else:
            t = Table(title="Cost Summary (last 20 traces)")
            for col in ("Trace ID", "Cost (USD)", "Tokens"):
                t.add_column(col)
            for row in rows:
                t.add_row(*row)
            console.print(t)


# ── dashboard ─────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--host", default="0.0.0.0")
@click.option("--port", default=8765, type=int)
@click.option("--api-key", envvar="GROQ_API_KEY", default=None)
def dashboard(host: str, port: int, api_key: Optional[str]) -> None:
    """Start the local API server and open the dashboard."""
    import uvicorn
    from configs.loader import load_swarm_config
    cfg = load_swarm_config({"groq_api_key": api_key} if api_key else None)
    _setup_logging(cfg)

    console.print(Panel(
        f"[bold cyan]Swarm Dashboard[/bold cyan]\n"
        f"API:  http://{host}:{port}\n"
        f"Docs: http://{host}:{port}/docs",
        border_style="cyan",
    ))

    from api.server import app, set_runtime
    uvicorn.run(app, host=host, port=port, log_level="warning")


# ── doctor ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--api-key", envvar="GROQ_API_KEY", default=None)
@click.option("--agents-dir", default="./agents")
@click.option("--tools-dir", default="./tools")
def doctor(api_key: Optional[str], agents_dir: str, tools_dir: str) -> None:
    """Validate the environment, credentials, and registry integrity."""
    ok = True

    def check(label: str, condition: bool, fix: str = "") -> None:
        nonlocal ok
        icon = "[green]✓[/green]" if condition else "[red]✗[/red]"
        console.print(f"  {icon}  {label}")
        if not condition:
            ok = False
            if fix:
                console.print(f"      [dim]Fix: {fix}[/dim]")

    console.print("[bold]Swarm Doctor[/bold]\n")

    # Credentials
    from dotenv import load_dotenv
    load_dotenv()
    key = api_key or ""
    import os
    key = key or os.environ.get("GROQ_API_KEY", "")
    check("GROQ_API_KEY is set", bool(key), "Set GROQ_API_KEY in .env or environment")

    # Directories
    check("agents/ directory exists", Path(agents_dir).exists(), f"mkdir {agents_dir}")
    check("tools/ directory exists", Path(tools_dir).exists(), f"mkdir {tools_dir}")

    # Spec files
    agent_specs = list(Path(agents_dir).rglob("spec.yaml"))
    tool_specs = list(Path(tools_dir).rglob("spec.yaml"))
    check(f"Found {len(agent_specs)} agent spec(s)", len(agent_specs) > 0)
    check(f"Found {len(tool_specs)} tool spec(s)", len(tool_specs) > 0)

    # Validate all specs
    from configs.loader import load_agent_spec, load_tool_spec
    for p in agent_specs:
        try:
            load_agent_spec(p)
            check(f"  Agent spec valid: {p.parent.name}", True)
        except Exception as e:
            check(f"  Agent spec valid: {p.parent.name}", False, str(e))

    for p in tool_specs:
        try:
            load_tool_spec(p)
            check(f"  Tool spec valid: {p.parent.name}", True)
        except Exception as e:
            check(f"  Tool spec valid: {p.parent.name}", False, str(e))

    # Python packages
    packages = [
        ("groq", "pip install groq"),
        ("pydantic", "pip install pydantic"),
        ("structlog", "pip install structlog"),
        ("rich", "pip install rich"),
        ("duckduckgo_search", "pip install duckduckgo-search"),
        ("httpx", "pip install httpx"),
        ("bs4", "pip install beautifulsoup4"),
        ("networkx", "pip install networkx"),
        ("fastapi", "pip install fastapi"),
    ]
    for pkg, fix in packages:
        try:
            __import__(pkg)
            check(f"Package '{pkg}'", True)
        except ImportError:
            check(f"Package '{pkg}'", False, fix)

    console.print()
    if ok:
        console.print("[bold green]All checks passed — ready to run![/bold green]")
    else:
        console.print("[bold red]Some checks failed. Fix the issues above and re-run.[/bold red]")
        sys.exit(1)


if __name__ == "__main__":
    cli()
