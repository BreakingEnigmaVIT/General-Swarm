"""Tests for gke_monitor agent and scaling config."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from configs.loader import load_agent_spec, load_topology_spec


def test_spec_valid() -> None:
    spec = load_agent_spec(Path("agents/gke_monitor/spec.yaml"))
    assert spec.role == "gke_monitor"
    assert "gcloud_cli" in spec.tools
    assert "human_input" in spec.tools
    assert "k8s_rollout_undo" in spec.tools


@pytest.mark.asyncio
async def test_on_spawn_reads_env(monkeypatch) -> None:
    from agents.gke_monitor import hooks

    monkeypatch.setenv("SWARM_GKE_CLUSTER", "test-cluster")
    monkeypatch.setenv("SWARM_GKE_REGION", "us-central1")
    monkeypatch.setenv("SWARM_GKE_PROJECT", "my-project")
    monkeypatch.setenv("SWARM_K8S_NAMESPACE", "swarm-dev")

    agent = MagicMock()
    agent.spec.role = "gke_monitor"
    agent._scratchpad.write = AsyncMock(return_value=None)

    await hooks.on_spawn(agent)
    agent._scratchpad.write.assert_awaited()
    _key, ctx = agent._scratchpad.write.call_args[0]
    assert _key == "gke_context"
    assert ctx["cluster"] == "test-cluster"


@pytest.mark.asyncio
async def test_on_spawn_missing_env_does_not_raise(monkeypatch) -> None:
    from agents.gke_monitor import hooks

    for var in (
        "SWARM_GKE_CLUSTER",
        "SWARM_GKE_REGION",
        "SWARM_GKE_PROJECT",
        "SWARM_K8S_NAMESPACE",
    ):
        monkeypatch.delenv(var, raising=False)

    agent = MagicMock()
    agent.spec.role = "gke_monitor"
    agent._scratchpad.write = AsyncMock(return_value=None)

    await hooks.on_spawn(agent)
    agent._scratchpad.write.assert_awaited()


def test_gke_monitor_in_scaling_config() -> None:
    raw = yaml.safe_load(Path("configs/scaling.yaml").read_text(encoding="utf-8"))
    assert "gke_monitor" in raw["roles"]
    assert raw["roles"]["gke_monitor"]["type"] == "ScaledJob"


def test_gke_monitor_in_topology() -> None:
    topo = load_topology_spec(Path("examples/software_delivery/topology.yaml"))
    roles = [a.role for a in topo.agents]
    assert "gke_monitor" in roles
    allow = topo.safety.tool_allowlist or []
    assert "gcloud_cli" in allow
