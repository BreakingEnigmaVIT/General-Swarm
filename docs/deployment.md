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

## Docker Compose (multi-service)

```bash
cp .env.example .env   # set GROQ_API_KEY
docker-compose up --build

# API available at http://localhost:8765
# POST /run  {"goal": "your goal"}
```

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

## Horizontal Scaling (future)

The system is designed for horizontal scaling:
- Set `bus_transport = redis` — all agents share the same message bus
- Each agent can run in a separate process or container
- State lives in memory backends (Redis / ChromaDB), not in process memory
- Add more worker processes pointing at the same Redis and ChromaDB instances

No code changes required — only config.

## Observability Backends

Traces are stored as local JSONL files by default. To export to external systems:
- **OpenTelemetry**: wrap `Tracer` with an OTEL exporter (spans are already structured)
- **Grafana/Loki**: point a log shipper at `SWARM_LOG_FILE`
- **Cost dashboards**: `GET /cost/<trace-id>` returns structured cost data
