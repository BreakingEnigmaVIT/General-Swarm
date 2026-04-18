# Deployment Guide

## Local (single-process, default)

```bash
# 1. Install
git clone <repo> && cd swarm
pip install -e .

# 2. Configure
cp .env.example .env
# Edit .env — set GROQ_API_KEY

# 3. Verify
swarm doctor

# 4. Run
swarm run --goal "Research the latest Python packaging tools"
swarm run examples/research_swarm/topology.yaml --goal "Compare FastAPI vs Django"
```

## Local multi-process (Redis bus)

```bash
# Start Redis (Docker)
docker run -d -p 6379:6379 redis:7-alpine

# Configure
echo "SWARM_BUS_TRANSPORT=redis" >> .env
echo "SWARM_REDIS_URL=redis://localhost:6379" >> .env

# Run — agents now communicate via Redis
swarm run examples/research_swarm/topology.yaml --goal "..."
```

## Distributed task execution (Redis Streams + workers)

Use this when **`SWARM_DEPLOYMENT_MODE=redis-workers`** (or **`kubernetes`** with the same queue). The orchestrator **enqueues** each graph task to Redis Streams; separate processes run **`python -m coordination.worker`** per **role**.

**Requirements:**

- Redis reachable at `SWARM_REDIS_URL`.
- One worker process (or container) per **role** you need, each with **`SWARM_WORKER_ROLE`** set to that role (e.g. `coder`, `researcher`).
- Orchestrator / CLI / API must use the same **`SWARM_STREAM_PREFIX`** (default `swarm`) and deployment mode.

**Local example:**

```bash
# Terminal 1 — Redis
docker run -d -p 6379:6379 redis:7-alpine

# .env
# SWARM_BUS_TRANSPORT=redis
# SWARM_REDIS_URL=redis://localhost:6379
# SWARM_DEPLOYMENT_MODE=redis-workers
# GROQ_API_KEY=...

# Terminal 2 — worker for role "coder"
set SWARM_WORKER_ROLE=coder
python -m coordination.worker

# Terminal 3 — run (multi-agent topology so TaskGraphExecutor is used)
swarm run examples/coding_swarm/topology.yaml --goal "Add a hello endpoint"
```

**CI / smoke without LLM:** set **`SWARM_WORKER_STUB=1`** on the worker (returns a fixed `TaskResult`).

**KEDA on Kubernetes:** stream names and consumer groups must match **`docs/keda-architecture.md`**.

## Docker Compose (multi-service)

```bash
cp .env.example .env   # set GROQ_API_KEY
docker compose up --build

# API available at http://localhost:8765
# POST /run  {"goal": "your goal"}
```

### Optional: API + Redis + queue worker (Compose profile)

To run the **dashboard** container with **`SWARM_DEPLOYMENT_MODE=redis-workers`** and one **worker** replica (single role):

```bash
# .env — set GROQ_API_KEY, then e.g.:
# SWARM_DEPLOYMENT_MODE=redis-workers
# SWARM_WORKER_ROLE=coder

docker compose --profile queue-workers up --build
```

The **`swarm`** service serves the API; **`swarm-worker`** consumes tasks for **`SWARM_WORKER_ROLE`**. Add more worker services (or Kubernetes) for additional roles.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GROQ_API_KEY` | *required* | Groq API key |
| `SWARM_DEFAULT_MODEL` | `llama-3.3-70b-versatile` | Default LLM model |
| `SWARM_LOG_LEVEL` | `INFO` | Logging level |
| `SWARM_TRACE_DIR` | `./traces` | Where trace JSONL files are stored |
| `SWARM_MEMORY_DIR` | `./memory_store` | ChromaDB persistence directory |
| `SWARM_BUS_TRANSPORT` | `in-process` | `in-process` or `redis` |
| `SWARM_REDIS_URL` | `redis://localhost:6379` | Redis connection URL |
| `SWARM_DEPLOYMENT_MODE` | `local` | `local` — in-process tasks; **`redis-workers`** / **`kubernetes`** — Redis Streams queue + workers |
| `SWARM_STREAM_PREFIX` | `swarm` | Prefix for Redis stream keys (`{prefix}:tasks:{role}`, results, DLQ, metrics) |
| `SWARM_ORCHESTRATOR_ID` | `orchestrator` | Pub/Sub channel suffix `{prefix}:{id}` for worker completion notifications |
| `SWARM_WORKER_ROLE` | — | **Worker only:** agent role this process consumes (required for `coordination.worker`) |
| `SWARM_WORKER_CONCURRENCY` | `1` | **Worker only:** max concurrent tasks per process |
| `SWARM_WORKER_STUB` | unset | Set to `1` to skip LLM in worker (testing) |
| `HOSTNAME` | OS default | **Worker only:** Redis consumer name (set unique per replica in K8s) |
| `SWARM_AGENTS_DIR` / `SWARM_TOOLS_DIR` | `./agents` / `./tools` | Override paths (default `/app/...` in containers when present) |
| `REDIS_URL` | — | Fallback for worker if `SWARM_REDIS_URL` unset |
| `SWARM_API_PORT` | `8765` | HTTP API port |
| `SWARM_SAFETY_MODE` | `interactive` | `interactive` (confirm destructive tools) or `auto` |

## Layered Config Precedence

```
defaults.yaml (shipped)
    < ~/.swarm/config.yaml (user global)
    < .swarm.yaml (project)
    < environment variables (SWARM_* / GROQ_API_KEY)
    < CLI flags (--api-key, --model, etc.)
```

## Horizontal scaling

- **Message bus:** `SWARM_BUS_TRANSPORT=redis` — agents share **Redis pub/sub** for `MessageBus`.
- **Task queue:** `SWARM_DEPLOYMENT_MODE=redis-workers` — graph tasks go to **Redis Streams**; scale **`coordination.worker`** processes per role.
- **State:** Chroma / scratchpad paths should be shared or per-worker as appropriate; configure **`SWARM_MEMORY_DIR`** consistently for tools that use long-term memory.

**Kubernetes:** use **KEDA** against `{SWARM_STREAM_PREFIX}:tasks:{role}` and consumer group `workers:{role}` — see **`docs/keda-architecture.md`**.

## Observability Backends

Traces are stored as local JSONL files by default. To export to external systems:
- **OpenTelemetry**: wrap `Tracer` with an OTEL exporter (spans are already structured)
- **Grafana/Loki**: point a log shipper at `SWARM_LOG_FILE`
- **Cost dashboards**: `GET /cost/<trace-id>` returns structured cost data
