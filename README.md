# Swarm

A general-purpose, extensible LLM agent swarm. Groq-backed, hybrid-coordinated (orchestrator + P2P subswarms), local-first with a clean path to cloud.

## Quick Start

```bash
# Install
pip install -e .

# Configure
cp .env.example .env
# Set GROQ_API_KEY in .env

# Check your environment
swarm doctor

# Run against a goal (single agent, no topology file needed)
swarm run --goal "What are the top 5 Python web frameworks? Compare briefly."

# Run with a multi-agent topology
swarm run examples/research_swarm/topology.yaml \
  --goal "Research the current state of LLM fine-tuning techniques"

# Run the coding swarm
swarm run examples/coding_swarm/topology.yaml \
  --goal "Write a Python function to parse and validate email addresses with tests"
```

## CLI Commands

```
swarm run [topology] --goal "<text>"    Run a swarm against a goal
swarm list agents|tools|providers       Inspect registries
swarm scaffold agent|tool|topology      Generate extension templates
swarm validate <file>                   Lint a spec or topology
swarm trace <trace-id>                  Pretty-print a trace
swarm replay <trace-id>                 Re-execute a historical trace
swarm cost [<trace-id>]                 Token usage and cost report
swarm dashboard                         Start local API + dashboard
swarm doctor                            Validate environment + credentials
```

## Adding a Custom Agent (3 steps)

```bash
swarm scaffold agent my-agent
# Edit agents/my-agent/spec.yaml — fill in system_prompt, tools, model
swarm validate agents/my-agent/spec.yaml
```

See [docs/how-to-add-an-agent.md](docs/how-to-add-an-agent.md).

## Adding a Custom Tool (3 steps)

```bash
swarm scaffold tool my-tool
# Edit tools/my-tool/spec.yaml and tools/my-tool/handler.py
swarm validate tools/my-tool/spec.yaml
```

See [docs/how-to-add-a-tool.md](docs/how-to-add-a-tool.md).

## Built-in Agents

| Role | Purpose |
|---|---|
| `orchestrator` | Decomposes goals, builds task graphs, dispatches agents |
| `researcher` | Web search + fetch → structured research briefs |
| `coder` | Write, edit, run code; filesystem + shell access |
| `critic` | Reviews output against criteria; structured critique |
| `planner` | Fuzzy goal → detailed step-by-step plan |
| `summarizer` | Condenses long content into structured summaries |
| `router` | Fast classifier that routes requests to specialists |
| `referee` | Weighs debate arguments; declares resolution |
| `memory_steward` | Decides what to persist to long-term memory |
| `human_liaison` | Pauses for human input; relays answers to swarm |

## Built-in Tools

`echo` · `web_search` · `web_fetch` · `filesystem` · `shell_exec` · `calculator` · `http_request` · `data_parse` · `memory_store` · `memory_retrieve` · `send_message` · `write_blackboard` · `spawn_agent` · `human_input` · `self_reflect`

## Architecture

See [docs/architecture.md](docs/architecture.md) for the full design. **Redis Streams workers and KEDA** (stream names, consumer groups, ScaledObject/ScaledJob) are described in [docs/keda-architecture.md](docs/keda-architecture.md).

## Tests

```bash
# Unit + integration (no API key needed)
pytest tests/unit tests/integration -v

# End-to-end (requires GROQ_API_KEY)
pytest tests/e2e -v
```

## Docker

```bash
cp .env.example .env   # set GROQ_API_KEY
docker compose up --build
# API: http://localhost:8765
```

Optional **queue workers** (set `SWARM_DEPLOYMENT_MODE=redis-workers` in `.env` for the API service):

```bash
docker compose --profile queue-workers up --build
```

See [docs/deployment.md](docs/deployment.md) for environment variables and local worker processes.

## Non-Goals (v1)

No fine-tuning, no image/voice/video tools, no checked-in production Kubernetes/Helm chart (see [docs/keda-architecture.md](docs/keda-architecture.md) for the scaling contract), no multi-tenancy. See the build plan for the full list.
