"""Integration checks for auth, deployment engineer, and gke_monitor."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from configs.loader import load_topology_spec
from core.registry import AgentSpecRegistry

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("GROQ_API_KEY"),
        reason="GROQ_API_KEY not set",
    ),
]


def test_auth_check_passes_when_groq_key_set(monkeypatch) -> None:
    from cli.main import cli

    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", str(__file__))
    with patch("cli.auth.verify_credential", return_value=(True, "mocked")):
        runner = CliRunner()
        result = runner.invoke(cli, ["auth", "setup", "--check"])
    assert result.exit_code == 0


def test_deployment_engineer_spec_loads_in_registry() -> None:
    ar = AgentSpecRegistry()
    ar.autodiscover("./agents")
    assert "deployment_engineer" in ar.list()


def test_gke_monitor_spec_loads_in_registry() -> None:
    ar = AgentSpecRegistry()
    ar.autodiscover("./agents")
    assert "gke_monitor" in ar.list()


def test_deployment_engineer_has_required_tools_in_topology() -> None:
    topo = load_topology_spec(Path("examples/software_delivery/topology.yaml"))
    roles = {a.role for a in topo.agents}
    assert "deployment_engineer" in roles
    allow = topo.safety.tool_allowlist or []
    assert "cloud_run_deploy" in allow
    assert "gcloud_cli" in allow
