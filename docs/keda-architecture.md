# KEDA architecture (Redis Streams task queue)

This document describes how **KEDA** scales Swarm **worker** processes when the runtime uses **`SWARM_DEPLOYMENT_MODE=redis-workers`** (or **`kubernetes`** with the same queue backend). It matches the implementation in `coordination/task_queue.py` and `coordination/worker.py`.

## Runtime split

| Component | Responsibility |
|-----------|----------------|
| **Orchestrator / API / CLI** | Builds `TaskGraph`, enqueues tasks per `agent_role`, polls result keys, aggregates output. |
| **Worker** (`python -m coordination.worker`) | Consumes one Redis Stream per role, runs `Agent.run_task`, writes results, ACKs messages. |

With `deployment_mode=local`, tasks stay **in-process** (`InProcessTaskQueue`); Redis and KEDA are not involved for execution.

## Redis data model

All keys use prefix **`SWARM_STREAM_PREFIX`** (default `swarm`), e.g. `swarm:tasks:coder`.

| Key / stream | Purpose |
|--------------|---------|
| `{prefix}:tasks:{role}` | Stream of pending work. Payload field: `payload` = `Task.model_dump_json()`. |
| `{prefix}:results:{task_id}` | String value: `TaskResult` JSON, TTL **3600s** (orchestrator polls with `GET`). |
| `{prefix}:dlq:{role}` | DLQ stream after **3** `nack`s on the same pending message. |
| `{prefix}:metrics` | Stream of per-task metric events (role, task_id, duration_ms, token_usage, status). |
| `{prefix}:{SWARM_ORCHESTRATOR_ID}` | Pub/Sub channel for completion notifications (optional; orchestrator today polls results). |

## Consumer groups

- **Group name:** `workers:{role}` (must match KEDA `consumerGroup` metadata).
- **Consumer name:** `HOSTNAME` env in the worker pod (unique per replica).

Workers call `XREADGROUP` with `BLOCK`; the orchestrator uses `XADD` on enqueue and `XACK` is done by the worker after success.

## KEDA ScaledObject (per role)

Each **agent role** that can receive graph tasks should have:

- **scaleTargetRef:** `Deployment` (or similar) running `coordination.worker` with `SWARM_WORKER_ROLE` set to that role.
- **Trigger:** `redis` scaler with:
  - **stream:** `{SWARM_STREAM_PREFIX}:tasks:{role}` (e.g. `swarm:tasks:market_research`)
  - **consumerGroup:** `workers:{role}`
  - **pendingEntriesCount** or **stream length**-based metadata as supported by your KEDA version (target: scale out when work exists).

Minimum replicas can be **0** so idle roles cost nothing; set **minReplicas ≥ 1** for latency-sensitive roles (e.g. chief orchestrator if ever modeled as a worker).

## KEDA ScaledJob (sandbox roles)

For **short-lived**, **isolated** jobs (e.g. `code_sandbox`, `test_runner`, `security_scan`), use **ScaledJob** instead of ScaledObject:

- Job template runs the **sandbox** image with `SWARM_WORKER_ROLE` fixed.
- Same Redis trigger on the corresponding `{prefix}:tasks:{role}` stream.
- Tight **TTL after finish** and **no outbound network** in the sandbox namespace (see network policies in a full cluster rollout).

## Alignment checklist

1. **`SWARM_DEPLOYMENT_MODE`** — `redis-workers` or `kubernetes` on processes that **enqueue** tasks.
2. **`SWARM_REDIS_URL`** — Same Redis for bus (if `SWARM_BUS_TRANSPORT=redis`), streams, and results.
3. **`SWARM_STREAM_PREFIX`** — Same everywhere (orchestrator + every worker + KEDA manifests).
4. **Worker image** — Same codebase as orchestrator; entrypoint `python -m coordination.worker`.
5. **KEDA Redis scaler** — Stream and consumer group names must match `RedisStreamTaskQueue` exactly.

## Future repo layout (Helm / GitOps)

When `deploy/k8s/` and `deploy/keda/` land in-tree, manifests should:

- Render one **Deployment + ServiceAccount** per worker role (or grouped by tier).
- Render one **ScaledObject** (or **ScaledJob**) per role referencing the table above.
- Mount `agents/`, `tools/`, `configs/` read-only; use **emptyDir** for `/tmp`, traces, workspace.

This document is the **contract** those manifests must follow until generated YAML is checked in.
