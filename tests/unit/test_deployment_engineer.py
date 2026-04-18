"""Tests for deployment_engineer agent spec, tools, and hooks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from configs.loader import load_agent_spec
from core.registry import bootstrap_registries
from core.task import Task
from tools.cloud_run_deploy.handler import CloudRunDeployHandler
from tools.gcloud_cli.handler import GcloudCliHandler

from agents.deployment_engineer import hooks


def test_spec_valid() -> None:
    spec = load_agent_spec(Path("agents/deployment_engineer/spec.yaml"))
    assert spec.role == "deployment_engineer"
    assert "cloud_run_deploy" in spec.tools
    assert "human_input" in spec.tools


@pytest.mark.asyncio
async def test_required_tools_registered() -> None:
    tr, _ar, _pr = bootstrap_registries(
        tools_dir="./tools",
        agents_dir="./agents",
        groq_api_key="",
        default_model="llama-3.3-70b-versatile",
    )
    assert "cloud_run_deploy" in tr.list()
    assert "gcloud_cli" in tr.list()
    cr = tr.lookup("cloud_run_deploy")
    gc = tr.lookup("gcloud_cli")
    assert isinstance(cr, CloudRunDeployHandler)
    assert isinstance(gc, GcloudCliHandler)
    out_cr = await cr.self_test()
    out_gc = await gc.self_test()
    assert isinstance(out_cr, bool)
    assert isinstance(out_gc, bool)


@pytest.mark.asyncio
async def test_hooks_on_task_assigned_logs_environment() -> None:
    agent = MagicMock()
    agent.spec.role = "deployment_engineer"

    async def _write(key: str, val: str) -> None:
        return None

    agent._scratchpad.write = _write

    task = Task(goal="deploy prod")
    with patch.object(hooks, "log") as mock_log:
        mock_log.info = MagicMock()
        await hooks.on_task_assigned(agent, task)
    mock_log.info.assert_called()
    _args, kwargs = mock_log.info.call_args
    assert kwargs.get("environment") == "prod"
