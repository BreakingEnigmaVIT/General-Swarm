"""Unit tests for gcloud_cli tool."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from configs.loader import load_tool_spec
from core.exceptions import ToolInputError
from tools.gcloud_cli.handler import GcloudCliHandler


@pytest.fixture
def handler() -> GcloudCliHandler:
    h = GcloudCliHandler()
    h.spec = load_tool_spec(Path("tools/gcloud_cli/spec.yaml"))
    return h


@pytest.mark.asyncio
async def test_rejects_command_without_gcloud_prefix(handler: GcloudCliHandler) -> None:
    with pytest.raises(ToolInputError, match="gcloud "):
        await handler._run({"command": "rm -rf /"})


@pytest.mark.asyncio
async def test_rejects_shell_operators(handler: GcloudCliHandler) -> None:
    with pytest.raises(ToolInputError, match="&&"):
        await handler._run({"command": "gcloud run list && echo hi"})
    with pytest.raises(ToolInputError, match=r"\|"):
        await handler._run({"command": "gcloud run list | grep foo"})
    with pytest.raises(ToolInputError, match=">"):
        await handler._run({"command": "gcloud run list > /tmp/out"})


@pytest.mark.asyncio
async def test_rejects_unallowlisted_subcommand(handler: GcloudCliHandler) -> None:
    with pytest.raises(ToolInputError, match="firebase"):
        await handler._run({"command": "gcloud firebase list"})


@pytest.mark.asyncio
async def test_rejects_blocked_destructive_verb(handler: GcloudCliHandler) -> None:
    with pytest.raises(ToolInputError, match="delete"):
        await handler._run({"command": "gcloud run services delete myservice"})


@pytest.mark.asyncio
async def test_format_flag_appended(handler: GcloudCliHandler) -> None:
    fake = CompletedProcess(["gcloud", "run", "services", "list"], 0, "ok\n", "")
    with patch("tools.gcloud_cli.handler.subprocess.run", return_value=fake) as mock_run:
        await handler._run(
            {
                "command": "gcloud run services list --region us-central1",
                "format": "json",
            }
        )
    args, kwargs = mock_run.call_args
    cmd_list = args[0]
    assert "--format" in cmd_list
    assert "json" in cmd_list


@pytest.mark.asyncio
async def test_self_test_passes(handler: GcloudCliHandler) -> None:
    fake = CompletedProcess(["gcloud", "--version"], 0, "Google Cloud SDK 456.0.0\n", "")
    with patch("tools.gcloud_cli.handler.asyncio.to_thread", new_callable=AsyncMock, return_value=fake):
        assert await handler.self_test() is True


@pytest.mark.asyncio
async def test_timeout_clamped_to_120(handler: GcloudCliHandler) -> None:
    fake = CompletedProcess(["gcloud", "run", "services", "list"], 0, "", "")
    with patch("tools.gcloud_cli.handler.subprocess.run", return_value=fake) as mock_run:
        await handler._run({"command": "gcloud run services list", "timeout": 9999})
    _args, kwargs = mock_run.call_args
    assert kwargs.get("timeout", 0) <= 120


@pytest.mark.asyncio
async def test_chain_command_never_spawns_subprocess(handler: GcloudCliHandler) -> None:
    mock_run = MagicMock()
    with patch("tools.gcloud_cli.handler.subprocess.run", mock_run):
        with pytest.raises(ToolInputError):
            await handler._run({"command": "gcloud run list && gcloud iam list"})
    mock_run.assert_not_called()
