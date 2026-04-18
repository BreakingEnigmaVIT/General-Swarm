# Deploy

- **Local / Compose:** [docs/deployment.md](../docs/deployment.md) — environment variables, Redis bus, optional `queue-workers` Compose profile.
- **Kubernetes / KEDA contract:** [docs/keda-architecture.md](../docs/keda-architecture.md) — Redis stream names, consumer groups `workers:{role}`, worker entrypoint `python -m coordination.worker`.

Helm charts and generated manifests are not checked in yet; new manifests should follow the KEDA document so scalers match `coordination/task_queue.py`.
