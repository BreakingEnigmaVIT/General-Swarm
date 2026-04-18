# Swarm Architecture

## Overview

Swarm is a general-purpose, extensible LLM agent swarm backed by Groq.
Every component is a plugin — agents, tools, memory backends, LLM providers,
and coordination strategies all load via a registry pattern.

```
User Goal
    │
    ▼
CLI / API
    │
    ▼
SwarmRuntime  ──────────────────────────────────────────────────────────┐
    │                                                                    │
    ├── OrchestratorAgent                                                │
    │       │ decompose goal → TaskGraph                                 │
    │       │ dispatch tasks to specialist agents                        │
    │       ▼                                                            │
    │   TaskGraphExecutor (async DAG)                                    │
    │       │  local: run Agent in-process                               │
    │       │  redis-workers/k8s: enqueue → Redis Stream → remote Worker│
    │       │                                                            │
    │       ├── Agent (researcher)   ──► ReAct loop                     │
    │       ├── Agent (coder)        ──► ReAct loop                     │
    │       └── Agent (critic)       ──► ReAct loop                     │
    │                                        │                          │
    │                                        ▼                          │
    │                                   Tool calls                      │
    │                                   web_search / filesystem / …     │
    │                                                                    │
    ├── MessageBus  (in-process | Redis)                                 │
    ├── MemorySystem (scratchpad | blackboard | long-term)               │
    ├── ProviderRegistry (groq | …)                                      │
    └── Observability (tracing | cost | replay)                         │
```

## Directory Layout

| Directory | Purpose |
|---|---|
| `core/` | Agent base class, Task/Message models, registries |
| `agents/` | Declarative agent specs (YAML) + optional hook modules |
| `tools/` | Tool specs (YAML) + handler implementations |
| `providers/` | LLM provider adapters (Groq first) |
| `memory/` | Scratchpad, Blackboard, Long-term vector store |
| `coordination/` | Orchestrator, message bus, task graph, subswarms, safety |
| `observability/` | Structured logging, tracing, cost accounting, replay |
| `configs/` | Pydantic schemas, defaults.yaml, layered loader |
| `cli/` | Click-based command-line interface |
| `api/` | FastAPI HTTP/WebSocket server |
| `examples/` | Reference topologies |
| `tests/` | Unit, integration, end-to-end test suites |
| `docs/` | Architecture, how-to guides, deployment |

## Deployment modes and task queue

`SwarmConfig.deployment_mode` (`SWARM_DEPLOYMENT_MODE`) selects how `TaskGraphExecutor` runs leaf tasks:

| Mode | Queue | Behaviour |
|------|--------|-----------|
| `local` (default) | `InProcessTaskQueue` | Tasks run in the same process via `Agent.run_task` (existing behaviour). |
| `redis-workers` | `RedisStreamTaskQueue` | Tasks are **`XADD`**’d per role; workers consume with **`XREADGROUP`**; results sit in Redis keys until the orchestrator picks them up. |
| `kubernetes` | `RedisStreamTaskQueue` | Same as `redis-workers`; name signals cluster/KEDA expectations. |

Stream layout, DLQ, and consumer groups are documented in **[KEDA architecture](keda-architecture.md)**.

**Worker entrypoint:** `python -m coordination.worker` (`coordination/worker.py`) — one OS process per **role** (or one deployment with `SWARM_WORKER_ROLE`).

## Core Data Flow

1. **User** submits a goal via `swarm run` or `POST /run`.
2. **SwarmRuntime** loads the topology, bootstraps registries, creates the bus.
3. **OrchestratorAgent** calls the LLM to decompose the goal into a **TaskGraph**.
4. **TaskGraphExecutor** runs the DAG — independent tasks execute in parallel.
5. Each leaf task is assigned to a specialist **Agent** based on `agent_role`.
6. The Agent runs a **ReAct loop**: `perceive → plan → act → reflect`.
   - *Perceive*: build messages from task + memory
   - *Plan*: call the LLM (Groq)
   - *Act*: execute tool calls returned by LLM
   - *Reflect*: optionally write to long-term memory
7. Results flow back through the TaskGraph to the Orchestrator.
8. Orchestrator aggregates and returns the final answer.

## Agent Lifecycle

```
on_spawn → on_task_assigned → [perceive → plan → act → reflect]* → on_complete
                                                                  └→ on_error
```

Each step is observable (trace span) and interruptible (budget/iteration checks).

## Memory Tiers

| Tier | Scope | Backend | Lifetime |
|---|---|---|---|
| Scratchpad | Per-agent | In-memory | Destroyed on agent complete |
| Blackboard | Per-subswarm | In-memory | Persisted to trace on dissolution |
| Long-term | Swarm-wide | ChromaDB (local) | Persistent across runs |

## Coordination Strategies

- **hierarchical** (default): Orchestrator decomposes and dispatches; no peer-to-peer.
- **p2p**: Subswarm agents collaborate via shared blackboard + consensus protocol.
- **hybrid**: Orchestrator for top-level; P2P subswarms for collaborative subtasks.

## Safety Layers

1. **Static allowlist**: tool must appear in topology's `tool_allowlist`.
2. **Side-effect gate**: `mutates-external` tools require operator confirmation in interactive mode.
3. **Quota**: per-tool and per-agent call limits prevent runaway loops.
4. **Circuit breaker**: tool auto-disabled after N consecutive errors.
5. **Prompt injection**: untrusted content (web pages, files) is demarcated in agent context.
6. **Path jail**: filesystem and shell tools are restricted to the project directory.

## Observability

Every event emits a structured log record AND a trace span:
- Logs: console (coloured) + optional JSONL file
- Traces: JSONL files under `./traces/<trace-id>.jsonl`
- Costs: accumulated in a per-run `CostLedger`
- Replay: `swarm trace <id>` or `swarm replay <id>`

## Transport-Agnostic Bus

```python
# config: bus_transport = "in-process"  →  InProcessBus (asyncio queues)
# config: bus_transport = "redis"       →  RedisBus (pub/sub)
# One config line — zero logic changes in agent code.
```

## Autoscaling (KEDA)

When using **Redis Streams** for tasks, **KEDA** can scale each `coordination.worker` deployment by depth / pending entries on `{prefix}:tasks:{role}` with consumer group `workers:{role}`. See **[keda-architecture.md](keda-architecture.md)** for stream names, groups, and ScaledObject / ScaledJob patterns.
