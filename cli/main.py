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
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

# Load .env before anything reads os.environ (tools, providers, LLM clients)
load_dotenv(override=False)

console = Console()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_config(
    api_key: Optional[str],
    model: Optional[str],
    log_level: str,
    trace_dir: str,
    safety_mode: str,
    provider: Optional[str] = None,
    openrouter_api_key: Optional[str] = None,
) -> "SwarmConfig":  # type: ignore[name-defined]
    from configs.loader import load_swarm_config
    overrides = {}
    if api_key:
        overrides["groq_api_key"] = api_key
    if openrouter_api_key:
        overrides["openrouter_api_key"] = openrouter_api_key
    if provider:
        overrides["provider"] = provider
    if model:
        overrides["default_model"] = model
    overrides["log_level"] = log_level
    overrides["trace_dir"] = trace_dir
    overrides["safety_mode"] = safety_mode
    return load_swarm_config(overrides)


def _setup_logging(cfg: "SwarmConfig") -> None:  # type: ignore[name-defined]
    from observability.logutil import configure_logging
    configure_logging(cfg.log_level, cfg.log_file)


def _bootstrap(cfg: "SwarmConfig") -> tuple:  # type: ignore[name-defined]
    from core.registry import bootstrap_registries
    from observability.tracing import configure_tracer
    configure_tracer(cfg.trace_dir)
    tr, ar, pr = bootstrap_registries(
        tools_dir=cfg.tools_dir,
        agents_dir=cfg.agents_dir,
        groq_api_key=cfg.groq_api_key,
        openrouter_api_key=cfg.openrouter_api_key,
        default_model=cfg.default_model,
    )
    return tr, ar, pr


def _build_runtime(cfg, topology_path: Optional[str], deploy: bool = True):
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
    provider = pr.get_or_default(cfg.provider or "groq")

    runtime = SwarmRuntime(
        topology=topology,
        provider=provider,
        tool_handlers=tool_handlers,
        agent_specs=agent_specs,
        bus=bus,
        longterm_memory=longterm,
        ledger=ledger,
        deployment_mode=cfg.deployment_mode,
        redis_url=cfg.redis_url,
        deploy=deploy,
    )
    return runtime, ledger


# ── Main group ────────────────────────────────────────────────────────────────

@click.group()
@click.version_option("0.1.0", prog_name="swarm")
def cli() -> None:
    """Swarm — a general-purpose, extensible LLM agent swarm. Supports Groq and OpenRouter."""


# ── run ───────────────────────────────────────────────────────────────────────

@cli.command()
@click.argument("topology", required=False, default=None)
@click.option("--goal", "-g", required=True, help="High-level goal for the swarm")
@click.option("--provider", "-p", default=None, type=click.Choice(["groq", "openrouter"]), help="LLM provider to use")
@click.option("--api-key", envvar="GROQ_API_KEY", default=None, help="Groq API key")
@click.option("--openrouter-api-key", envvar="OPENROUTER_API_KEY", default=None, help="OpenRouter API key")
@click.option("--model", "-m", default=None, help="Override default LLM model")
@click.option("--log-level", default="INFO", show_default=True)
@click.option("--trace-dir", default="./traces", show_default=True)
@click.option("--safety-mode", default="interactive", type=click.Choice(["interactive", "auto"]))
@click.option("--budget", default=None, type=float, help="Max spend in USD")
@click.option("--json", "output_json", is_flag=True, help="Output result as JSON")
def run(
    topology: Optional[str],
    goal: str,
    provider: Optional[str],
    api_key: Optional[str],
    openrouter_api_key: Optional[str],
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
      swarm run --provider openrouter --model meta-llama/llama-3.3-70b-instruct --goal "..."
      swarm run configs/research.yaml --goal "Write a Python web scraper"
    """
    cfg = _load_config(api_key, model, log_level, trace_dir, safety_mode, provider, openrouter_api_key)
    _setup_logging(cfg)

    active_provider = cfg.provider or "groq"
    if active_provider == "openrouter" and not cfg.openrouter_api_key:
        console.print("[bold red]Error:[/bold red] OPENROUTER_API_KEY is not set.")
        console.print("  Set it in .env or pass --openrouter-api-key")
        sys.exit(1)
    if active_provider == "groq" and not cfg.groq_api_key:
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
        rows = [
            ("groq", "Groq LLaMA API", "https://groq.com"),
            ("openrouter", "OpenRouter (OpenAI-compatible multi-model)", "https://openrouter.ai"),
        ]
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
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8765, type=int, show_default=True)
@click.option("--api-key", envvar="GROQ_API_KEY", default=None, help="Groq API key")
@click.option("--openrouter-api-key", envvar="OPENROUTER_API_KEY", default=None, help="OpenRouter API key")
@click.option("--provider", "-p", default=None, type=click.Choice(["groq", "openrouter"]))
@click.option("--topology", default=None, help="Optional topology YAML path")
@click.option("--log-level", default="INFO", show_default=True)
def dashboard(
    host: str,
    port: int,
    api_key: Optional[str],
    openrouter_api_key: Optional[str],
    provider: Optional[str],
    topology: Optional[str],
    log_level: str,
) -> None:
    """Start the local API server and open the dashboard."""
    import uvicorn

    cfg = _load_config(
        api_key=api_key,
        model=None,
        log_level=log_level,
        trace_dir="./traces",
        safety_mode="auto",   # API mode: no interactive prompts
        provider=provider,
        openrouter_api_key=openrouter_api_key,
    )
    _setup_logging(cfg)

    # Bootstrap registries once so all tools/agents/providers are available
    # to the per-request factory below.
    _bootstrap(cfg)

    console.print(Panel(
        f"[bold cyan]Swarm Dashboard[/bold cyan]\n"
        f"API:  http://{host}:{port}\n"
        f"Docs: http://{host}:{port}/docs\n"
        f"Provider: {cfg.provider or 'groq'}",
        border_style="cyan",
    ))

    # Wire a factory so each POST /run gets a fresh, isolated SwarmRuntime.
    # Using a factory (rather than a single shared runtime) prevents state
    # leakage between consecutive API calls.
    def _make_runtime():
        return _build_runtime(cfg, topology_path=topology, deploy=False)

    from api.server import app, set_runtime_factory
    set_runtime_factory(_make_runtime, cfg)

    uvicorn.run(app, host=host, port=port, log_level="warning")


# ── doctor ────────────────────────────────────────────────────────────────────

@cli.command()
@click.option("--api-key", envvar="GROQ_API_KEY", default=None)
@click.option("--openrouter-api-key", envvar="OPENROUTER_API_KEY", default=None)
@click.option("--agents-dir", default="./agents")
@click.option("--tools-dir", default="./tools")
def doctor(api_key: Optional[str], openrouter_api_key: Optional[str], agents_dir: str, tools_dir: str) -> None:
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
    import os
    key = api_key or os.environ.get("GROQ_API_KEY", "")
    or_key = openrouter_api_key or os.environ.get("OPENROUTER_API_KEY", "")
    check("GROQ_API_KEY is set", bool(key), "Set GROQ_API_KEY in .env or environment")
    check("OPENROUTER_API_KEY is set", bool(or_key), "Set OPENROUTER_API_KEY in .env or environment (optional)")

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


# ── Artifact commands ─────────────────────────────────────────────────────────

@cli.group("artifact")
def artifact_group() -> None:
    """Inspect and manage workforce artifacts."""


@artifact_group.command("list")
@click.option("--project", default="", help="Filter by project id")
@click.option("--type", "artifact_type", default="", help="Filter by artifact type")
@click.option("--stage", default="", help="Filter by stage id")
@click.option("--status", default="any", type=click.Choice(["draft", "approved", "superseded", "any"]))
@click.option("--memory-dir", default="./memory_store")
@click.option("--json", "output_json", is_flag=True)
def artifact_list(
    project: str, artifact_type: str, stage: str, status: str,
    memory_dir: str, output_json: bool
) -> None:
    """List artifacts in the registry.

    \b
    Examples:
      swarm artifact list
      swarm artifact list --type PRD --status approved
      swarm artifact list --stage planning --project my-project
    """
    from memory.artifacts import ArtifactRegistry

    reg = ArtifactRegistry(persist_dir=memory_dir)

    async def _fetch():
        if artifact_type:
            items = await reg.list_by_type(artifact_type, project_id=project, stage_id=stage)
        elif stage:
            items = await reg.list_by_stage(stage, project_id=project)
        else:
            items = await reg.list_all(project_id=project)
        if status != "any":
            items = [
                a for a in items
                if (a.status if isinstance(a.status, str) else a.status.value) == status
            ]
        return items

    items = asyncio.run(_fetch())

    if output_json:
        click.echo(json.dumps([a.model_dump(mode="json") for a in items], indent=2, default=str))
        return

    t = Table(title=f"Artifacts ({len(items)})")
    for col in ("ID", "Type", "Stage", "Status", "Created"):
        t.add_column(col)
    for a in items:
        console.print(t.add_row(
            a.id[:8],
            a.artifact_type if isinstance(a.artifact_type, str) else a.artifact_type.value,
            a.stage_id or "—",
            a.status if isinstance(a.status, str) else a.status.value,
            a.created_at.strftime("%Y-%m-%d %H:%M"),
        ) or "")
    console.print(t)


@artifact_group.command("show")
@click.argument("artifact_id")
@click.option("--memory-dir", default="./memory_store")
@click.option("--json", "output_json", is_flag=True)
def artifact_show(artifact_id: str, memory_dir: str, output_json: bool) -> None:
    """Pretty-print a single artifact including lineage.

    \b
    Example:
      swarm artifact show abc12345
    """
    from memory.artifacts import ArtifactRegistry

    reg = ArtifactRegistry(persist_dir=memory_dir)

    async def _fetch():
        artifact = await reg.get_by_id(artifact_id)
        if artifact is None:
            # Try prefix search — find first matching id
            all_items = await reg.list_all()
            for a in all_items:
                if a.id.startswith(artifact_id):
                    artifact = a
                    break
        lineage = await reg.get_lineage(artifact.id) if artifact else []
        return artifact, lineage

    artifact, lineage = asyncio.run(_fetch())

    if artifact is None:
        console.print(f"[red]Artifact '{artifact_id}' not found.[/red]")
        sys.exit(1)

    if output_json:
        out = {
            "artifact": artifact.model_dump(mode="json"),
            "lineage": [a.model_dump(mode="json") for a in lineage],
        }
        click.echo(json.dumps(out, indent=2, default=str))
        return

    a_type = artifact.artifact_type if isinstance(artifact.artifact_type, str) else artifact.artifact_type.value
    a_status = artifact.status if isinstance(artifact.status, str) else artifact.status.value
    console.print(Panel(
        f"[bold]Type:[/bold]    {a_type}\n"
        f"[bold]ID:[/bold]      {artifact.id}\n"
        f"[bold]Stage:[/bold]   {artifact.stage_id or '—'}\n"
        f"[bold]Status:[/bold]  {a_status}\n"
        f"[bold]Version:[/bold] {artifact.version}\n"
        f"[bold]Author:[/bold]  {artifact.author_agent_id or '—'}\n"
        f"[bold]Created:[/bold] {artifact.created_at}\n"
        f"[bold]Lineage:[/bold] {', '.join(artifact.lineage) or 'root'}",
        title=f"[bold]Artifact: {artifact.id[:8]}[/bold]",
        border_style="cyan",
    ))
    if lineage:
        console.print("\n[bold]Parent chain:[/bold]")
        for parent in lineage:
            p_type = parent.artifact_type if isinstance(parent.artifact_type, str) else parent.artifact_type.value
            p_status = parent.status if isinstance(parent.status, str) else parent.status.value
            console.print(f"  ↑ {parent.id[:8]}  [{p_type}]  {p_status}")


# ── Phase gate commands ────────────────────────────────────────────────────────

@cli.group("phase")
def phase_group() -> None:
    """Manage lifecycle phase approval gates."""


@phase_group.command("approve")
@click.argument("trace_id")
@click.argument("phase_id")
@click.option("--trace-dir", default="./traces")
def phase_approve(trace_id: str, phase_id: str, trace_dir: str) -> None:
    """Manually approve a pending lifecycle phase gate.

    \b
    Example:
      swarm phase approve abc12345 planning
    """
    _write_phase_gate_decision(trace_dir, trace_id, phase_id, approved=True, reason="")
    console.print(f"[green]✓ Phase '{phase_id}' approved for trace {trace_id[:8]}[/green]")


@phase_group.command("reject")
@click.argument("trace_id")
@click.argument("phase_id")
@click.option("--reason", default="", help="Reason for rejection")
@click.option("--trace-dir", default="./traces")
def phase_reject(trace_id: str, phase_id: str, reason: str, trace_dir: str) -> None:
    """Reject a pending lifecycle phase gate.

    \b
    Example:
      swarm phase reject abc12345 quality --reason "Test coverage too low"
    """
    _write_phase_gate_decision(trace_dir, trace_id, phase_id, approved=False, reason=reason)
    console.print(f"[red]✗ Phase '{phase_id}' rejected for trace {trace_id[:8]}[/red]")
    if reason:
        console.print(f"  Reason: {reason}")


def _write_phase_gate_decision(
    trace_dir: str, trace_id: str, phase_id: str, approved: bool, reason: str
) -> None:
    """Write a phase gate decision file that the orchestrator polls."""
    import datetime
    gate_dir = Path(trace_dir) / "phase_gates"
    gate_dir.mkdir(parents=True, exist_ok=True)
    gate_file = gate_dir / f"{trace_id}_{phase_id}.json"
    gate_file.write_text(json.dumps({
        "trace_id": trace_id,
        "phase_id": phase_id,
        "approved": approved,
        "reason": reason,
        "decided_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, indent=2))


# ── Workforce run ─────────────────────────────────────────────────────────────

@cli.command("workforce")
@click.argument("topology")
@click.option("--goal", "-g", required=True, help="Product goal for the workforce")
@click.option("--provider", "-p", default=None, type=click.Choice(["groq", "openrouter"]), help="LLM provider to use")
@click.option("--api-key", envvar="GROQ_API_KEY", default=None)
@click.option("--openrouter-api-key", envvar="OPENROUTER_API_KEY", default=None, help="OpenRouter API key")
@click.option("--model", "-m", default=None)
@click.option("--log-level", default="INFO", show_default=True)
@click.option("--trace-dir", default="./traces", show_default=True)
@click.option("--memory-dir", default="./memory_store", show_default=True)
@click.option("--approve-all", is_flag=True, help="Auto-approve all phase gates (CI mode)")
@click.option("--deploy/--no-deploy", default=False, show_default=True,
              help="Include deployment phases (deployment + post_launch). Omit to stop at quality and go straight to feedback/iteration.")
@click.option("--budget", default=None, type=float, help="Max spend in USD")
@click.option("--json", "output_json", is_flag=True)
def workforce(
    topology: str,
    goal: str,
    provider: Optional[str],
    api_key: Optional[str],
    openrouter_api_key: Optional[str],
    model: Optional[str],
    log_level: str,
    trace_dir: str,
    memory_dir: str,
    approve_all: bool,
    deploy: bool,
    budget: Optional[float],
    output_json: bool,
) -> None:
    """Run a product goal through the full AI workforce lifecycle.

    TOPOLOGY must point to a topology YAML with coordination.strategy=lifecycle.

    \b
    Example:
      swarm workforce examples/software_delivery/topology.yaml \\
        --goal "Build a URL-shortener SaaS with email/password auth and a dashboard"
    """
    safety_mode = "auto" if approve_all else "interactive"
    cfg = _load_config(api_key, model, log_level, trace_dir, safety_mode, provider, openrouter_api_key)
    cfg.memory_dir = memory_dir
    _setup_logging(cfg)

    active_provider = cfg.provider or "groq"
    if active_provider == "openrouter" and not cfg.openrouter_api_key:
        console.print("[bold red]Error:[/bold red] OPENROUTER_API_KEY is not set.")
        sys.exit(1)
    if active_provider == "groq" and not cfg.groq_api_key:
        console.print("[bold red]Error:[/bold red] GROQ_API_KEY is not set.")
        sys.exit(1)

    _bootstrap(cfg)

    from configs.loader import load_topology_spec
    topo = load_topology_spec(Path(topology))
    if topo.coordination.strategy != "lifecycle":
        console.print(
            f"[yellow]Warning:[/yellow] topology strategy is '{topo.coordination.strategy}', "
            "not 'lifecycle'. Overriding to 'lifecycle'."
        )
        topo.coordination.strategy = "lifecycle"

    if approve_all:
        topo.coordination.approval_gates = False

    if budget:
        topo.budget.max_cost_usd = budget

    # Reset artifact registry for this run
    from memory.artifacts import reset_artifact_registry
    reset_artifact_registry(persist_dir=memory_dir)

    runtime, ledger = _build_runtime(cfg, topology, deploy=deploy)
    runtime.topology = topo

    console.print(Panel(
        f"[bold cyan]Workforce Run[/bold cyan]\n"
        f"[bold]Goal:[/bold] {goal}\n"
        f"[dim]Lifecycle:[/dim] {topo.coordination.lifecycle or 'software_delivery'}\n"
        f"[dim]Trace:[/dim]    {runtime.trace_id[:8]}…\n"
        f"[dim]Gates:[/dim]    {'auto-approved' if approve_all else 'interactive'}\n"
        f"[dim]Deploy:[/dim]   {'enabled' if deploy else 'skipped (use --deploy to include)'}",
        title="[bold]AI Digital Workforce[/bold]",
        border_style="magenta",
    ))

    result = asyncio.run(runtime.run(goal))

    if output_json:
        click.echo(json.dumps({
            "trace_id": runtime.trace_id,
            "output": result.output,
            "success": result.success,
            "cost_usd": result.cost,
            "tokens": result.token_usage.total_tokens,
            "phases": result.metadata.get("phases", []),
            "error": result.error,
        }, indent=2, default=str))
        return

    border = "green" if result.success else "red"
    status = "✓ Workforce Complete" if result.success else "✗ Workforce Failed"
    console.print(Panel(
        str(result.output or result.error or "No output"),
        title=f"[bold]{status}[/bold]",
        border_style=border,
    ))

    phases = result.metadata.get("phases", [])
    if phases:
        t = Table(title="Phase Summary")
        for col in ("Phase", "Success", "Outputs Missing"):
            t.add_column(col)
        for ph in phases:
            t.add_row(
                ph["phase_id"],
                "[green]✓[/green]" if ph["success"] else "[red]✗[/red]",
                ", ".join(ph.get("output_missing", [])) or "—",
            )
        console.print(t)

    console.print(
        f"[dim]Cost: ${result.cost:.4f}  |  "
        f"Tokens: {result.token_usage.total_tokens}  |  "
        f"Trace: {runtime.trace_id[:8]}[/dim]"
    )

    console.print(
        "\n[dim]Inspect artifacts:[/dim]  swarm artifact list\n"
        "[dim]Show artifact:[/dim]     swarm artifact show <id>"
    )


if __name__ == "__main__":
    cli()
