"""Unit tests for cloud_run_deploy tool."""

from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from configs.loader import load_tool_spec
from core.exceptions import SafetyError, ToolInputError
import tools.cloud_run_deploy.handler as mod
from tools.cloud_run_deploy.handler import CloudRunDeployHandler, DEPLOY_SCRIPT_PATH


@pytest.fixture
def handler() -> CloudRunDeployHandler:
    h = CloudRunDeployHandler()
    h.spec = load_tool_spec(Path("tools/cloud_run_deploy/spec.yaml"))
    return h


@pytest.mark.asyncio
async def test_input_validation_rejects_bad_image_tag(handler: CloudRunDeployHandler) -> None:
    with pytest.raises(ToolInputError, match="image_tag"):
        await handler._run(
            {
                "image_tag": "../../etc/passwd:latest",
                "service_name": "myservice",
                "region": "us-central1",
                "project_id": "myproject",
                "environment": "dev",
                "dry_run": True,
            }
        )


@pytest.mark.asyncio
async def test_input_validation_rejects_unknown_region(handler: CloudRunDeployHandler) -> None:
    with pytest.raises(ToolInputError, match="region"):
        await handler._run(
            {
                "image_tag": "gcr.io/foo/bar:tag1",
                "service_name": "myservice",
                "region": "narnia-west1",
                "project_id": "myproject",
                "environment": "dev",
                "dry_run": True,
            }
        )


@pytest.mark.asyncio
async def test_script_integrity_check_fails_on_tampered_script(handler: CloudRunDeployHandler) -> None:
    orig = mod.EXPECTED_SCRIPT_HASH
    try:
        mod.EXPECTED_SCRIPT_HASH = "a" * 64
        with pytest.raises(SafetyError, match="integrity"):
            await handler._run(
                {
                    "image_tag": "gcr.io/foo/bar:tag1",
                    "service_name": "myservice",
                    "region": "us-central1",
                    "project_id": "myproject",
                    "environment": "dev",
                    "dry_run": True,
                }
            )
    finally:
        mod.EXPECTED_SCRIPT_HASH = orig


@pytest.mark.asyncio
async def test_dry_run_returns_success_without_gcloud(handler: CloudRunDeployHandler) -> None:
    fake = CompletedProcess(
        ["bash", str(DEPLOY_SCRIPT_PATH)],
        0,
        "DRY RUN - no changes made.\n",
        "",
    )
    with patch("tools.cloud_run_deploy.handler.subprocess.run", return_value=fake):
        out = await handler._run(
            {
                "image_tag": "gcr.io/foo/bar:tag1",
                "service_name": "myservice",
                "region": "us-central1",
                "project_id": "myproject",
                "environment": "dev",
                "dry_run": True,
            }
        )
    assert out["success"] is True
    assert out["dry_run"] is True


@pytest.mark.asyncio
async def test_self_test_fails_if_script_missing(handler: CloudRunDeployHandler) -> None:
    if not DEPLOY_SCRIPT_PATH.exists():
        pytest.skip("deploy script not present")
    backup = DEPLOY_SCRIPT_PATH.with_suffix(DEPLOY_SCRIPT_PATH.suffix + ".pytest_moved")
    try:
        DEPLOY_SCRIPT_PATH.rename(backup)
        assert await handler.self_test() is False
    finally:
        if backup.exists():
            backup.rename(DEPLOY_SCRIPT_PATH)
