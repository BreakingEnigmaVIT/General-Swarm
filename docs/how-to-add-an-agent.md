# How to Add a Custom Agent

Adding a new agent requires **three files** in a new folder under `agents/`.
No core code changes needed.

## Step 1 — Scaffold the folder

```bash
swarm scaffold agent my-analyst
```

This creates:
```
agents/my-analyst/
├── spec.yaml      ← declarative spec (fill this in)
├── hooks.py       ← optional custom logic
└── __init__.py
```

## Step 2 — Fill in `spec.yaml`

```yaml
name: my-analyst
role: my-analyst
description: "Analyses financial data and produces structured reports."
version: "1.0.0"
system_prompt: |
  You are a financial analyst. Given data or a question, produce a clear,
  structured report with: Executive Summary, Key Findings, Risks, Recommendations.
  Use the calculator tool for any numerical analysis.
  Always cite sources.
model: llama-3.3-70b-versatile
temperature: 0.3
tools:
  - web_search
  - web_fetch
  - calculator
  - data_parse
  - memory_store
peer_agents:
  - summarizer
memory_policy:
  scratchpad: true
  longterm: true
termination:
  max_iterations: 20
  max_tokens: 8192
hooks: {}
```

### Spec fields reference

| Field | Required | Description |
|---|---|---|
| `name` | ✓ | Human-readable label |
| `role` | ✓ | Unique ID — used by orchestrator to assign tasks |
| `system_prompt` | ✓ | LLM system prompt (supports `{{agent_id}}`, `{{role}}`, `{{task_id}}`) |
| `model` | | Groq model name (default: `llama-3.3-70b-versatile`) |
| `temperature` | | 0.0–2.0 (default: 0.7) |
| `tools` | | Tool names this agent may call |
| `peer_agents` | | Roles this agent may spawn or message |
| `memory_policy.longterm` | | Whether to read/write long-term memory |
| `termination.max_iterations` | | Safety cap on ReAct loop |
| `hooks` | | `hook_name: module.path.function` for custom logic |

## Step 3 — Optional: add hook logic

If you need custom pre/post-processing, edit `hooks.py`:

```python
# agents/my-analyst/hooks.py

async def on_task_assigned(agent, task):
    # e.g., pre-load relevant memories
    if agent._longterm:
        memories = await agent._longterm.search(task.goal, limit=3)
        task.input_payload["prior_research"] = memories

async def on_complete(agent, task, result):
    # e.g., emit a Slack notification (add your own tool)
    pass
```

Then reference it in `spec.yaml`:
```yaml
hooks:
  on_task_assigned: agents.my-analyst.hooks.on_task_assigned
  on_complete: agents.my-analyst.hooks.on_complete
```

## Step 4 — Validate and use

```bash
# Check the spec is valid
swarm validate agents/my-analyst/spec.yaml

# Use it in a topology
swarm list agents          # should appear in the list

# Reference in a topology YAML
# configs/my-topology.yaml:
#   agents:
#     - role: my-analyst
```

## Hot reload

The registry is re-scanned on each `swarm run`. Drop a new spec file and
restart — no code changes required.
