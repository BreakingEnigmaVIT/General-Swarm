"""CLI scaffold commands — generate agent and tool template folders."""

from __future__ import annotations

from pathlib import Path

import click
import yaml


_AGENT_SPEC_TEMPLATE = """\
name: {name}
role: {name}
description: "TODO: describe what this agent does"
version: "1.0.0"
system_prompt: |
  You are a {name} agent. TODO: write a detailed system prompt here.
  Describe your role, approach, and output format.
model: llama-3.3-70b-versatile
temperature: 0.7
tools: []
peer_agents: []
memory_policy:
  scratchpad: true
  longterm: false
termination:
  max_iterations: 20
  max_tokens: 8192
hooks: {{}}
"""

_AGENT_HOOK_TEMPLATE = '''\
"""Optional hooks for the {name} agent.

Uncomment and implement the hooks you need.
Reference them in spec.yaml under hooks:
  on_task_assigned: agents.{name}.hooks.on_task_assigned
"""

# async def on_spawn(agent):
#     pass

# async def on_task_assigned(agent, task):
#     pass

# async def on_tool_result(agent, tool_name, inputs, result):
#     pass

# async def on_complete(agent, task, result):
#     pass

# async def on_error(agent, task, exc):
#     pass
'''

_TOOL_SPEC_TEMPLATE = """\
name: {name}
description: "TODO: describe what this tool does"
version: "1.0.0"
side_effect_level: read-only   # or mutates-local / mutates-external
permissions: []
input_schema:
  type: object
  properties:
    input:
      type: string
      description: "TODO: define your input parameters"
  required: [input]
  additionalProperties: false
output_schema:
  type: object
  properties:
    output:
      type: string
timeout: 30.0
retry:
  max_attempts: 3
  backoff_base: 2.0
  backoff_max: 30.0
"""

_TOOL_HANDLER_TEMPLATE = '''\
"""Handler for the {name} tool."""

from __future__ import annotations

from typing import Any

from tools.base import ToolHandler


class {class_name}Handler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        # TODO: implement your tool logic here
        return {{"output": inputs.get("input", "")}}

    async def self_test(self) -> bool:
        result = await self._run({{"input": "test"}})
        return "output" in result


handler = {class_name}Handler()
'''

_TOPOLOGY_TEMPLATE = """\
name: {name}
description: "TODO: describe this swarm topology"

orchestrator: orchestrator

agents:
  - role: researcher
    count: 1
  - role: coder
    count: 1

coordination:
  strategy: hierarchical   # hierarchical | p2p | hybrid
  max_subswarm_size: 5
  consensus_protocol: majority
  debate_max_rounds: 3

budget:
  max_cost_usd: 0.50
  warn_at_fraction: 0.8

safety:
  mode: interactive        # interactive | auto
  tool_allowlist: null     # null = allow all; or list specific tools

memory_backend: local
"""


def scaffold_agent(name: str, agents_dir: str = "./agents") -> None:
    folder = Path(agents_dir) / name
    if folder.exists():
        click.echo(f"[yellow]Agent '{name}' already exists at {folder}[/yellow]")
        return
    folder.mkdir(parents=True)
    (folder / "spec.yaml").write_text(_AGENT_SPEC_TEMPLATE.format(name=name))
    (folder / "hooks.py").write_text(_AGENT_HOOK_TEMPLATE.format(name=name))
    (folder / "__init__.py").write_text("")
    click.echo(f"[green]Agent scaffolded at {folder}[/green]")
    click.echo("  1. Edit spec.yaml — fill in system_prompt, tools, model")
    click.echo("  2. Optionally implement hooks in hooks.py")
    click.echo("  3. Run `swarm validate agents/{name}/spec.yaml`")


def scaffold_tool(name: str, tools_dir: str = "./tools") -> None:
    folder = Path(tools_dir) / name
    if folder.exists():
        click.echo(f"[yellow]Tool '{name}' already exists at {folder}[/yellow]")
        return
    folder.mkdir(parents=True)
    class_name = "".join(w.title() for w in name.replace("-", "_").split("_"))
    (folder / "spec.yaml").write_text(_TOOL_SPEC_TEMPLATE.format(name=name))
    (folder / "handler.py").write_text(
        _TOOL_HANDLER_TEMPLATE.format(name=name, class_name=class_name)
    )
    (folder / "__init__.py").write_text("")
    click.echo(f"[green]Tool scaffolded at {folder}[/green]")
    click.echo("  1. Edit spec.yaml — define input/output schema")
    click.echo("  2. Implement _run() in handler.py")
    click.echo("  3. Run `swarm validate tools/{name}/spec.yaml`")


def scaffold_topology(name: str, configs_dir: str = "./configs") -> None:
    path = Path(configs_dir) / f"{name}.yaml"
    if path.exists():
        click.echo(f"[yellow]Topology '{name}' already exists at {path}[/yellow]")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_TOPOLOGY_TEMPLATE.format(name=name))
    click.echo(f"[green]Topology scaffolded at {path}[/green]")
    click.echo(f"  Run `swarm run {path} --goal 'your goal'`")
