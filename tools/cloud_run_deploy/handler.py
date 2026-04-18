"""Deploy a container image to GCP Cloud Run via the repo-tracked deploy script."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError, ToolInputError
from observability.logutil import get_logger
from tools.base import ToolHandler

log = get_logger("tools.cloud_run_deploy")

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEPLOY_SCRIPT_PATH = _REPO_ROOT / "scripts" / "deploy_cloud_run.sh"

# SHA-256 of scripts/deploy_cloud_run.sh (update when the script changes).
EXPECTED_SCRIPT_HASH = "d7805b5e3cf76ead0872020819952ccef88973d5e1539fa1d9543e3ef7ed1e62"

_IMAGE_TAG_RE = re.compile(r"^[a-zA-Z0-9._\-/]+:[a-zA-Z0-9._\-]{1,128}$")
_SERVICE_NAME_RE = re.compile(r"^[a-z][a-z0-9\-]{1,49}$")
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9\-]{5,29}$")

GCP_REGIONS: frozenset[str] = frozenset(
    {
        "us-central1",
        "us-east1",
        "us-east4",
        "us-west1",
        "us-west2",
        "us-west3",
        "us-west4",
        "us-south1",
        "europe-west1",
        "europe-west2",
        "europe-west3",
        "europe-west4",
        "europe-west6",
        "europe-west8",
        "europe-west9",
        "europe-north1",
        "asia-east1",
        "asia-northeast1",
        "asia-southeast1",
        "australia-southeast1",
        "southamerica-east1",
        "northamerica-northeast1",
    }
)

_SERVICE_URL_LINE = re.compile(r"Service URL:\s*(https?://\S+)")
_REVISION_LINE = re.compile(r"Serving traffic at revision:\s*(\S+)")


def _tail(s: str, max_len: int) -> str:
    if len(s) <= max_len:
        return s
    return s[-max_len:]


def _parse_service_url(stdout: str) -> str | None:
    for line in stdout.splitlines():
        m = _SERVICE_URL_LINE.search(line)
        if m:
            return m.group(1).strip()
    return None


def _parse_revision(stdout: str) -> str | None:
    for line in stdout.splitlines():
        m = _REVISION_LINE.search(line)
        if m:
            return m.group(1).strip()
    return None


class CloudRunDeployHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not DEPLOY_SCRIPT_PATH.is_file():
            raise ToolInputError(f"deploy_cloud_run.sh not found at {DEPLOY_SCRIPT_PATH}. Run setup first.")

        script_bytes = DEPLOY_SCRIPT_PATH.read_bytes()
        actual_hash = hashlib.sha256(script_bytes).hexdigest()
        if EXPECTED_SCRIPT_HASH and actual_hash != EXPECTED_SCRIPT_HASH:
            raise SafetyError(
                f"Script integrity check failed. Expected {EXPECTED_SCRIPT_HASH}, got {actual_hash}. "
                "The deploy script may have been modified. Aborting."
            )

        image_tag = inputs["image_tag"]
        if not isinstance(image_tag, str) or ".." in image_tag or image_tag.startswith("/"):
            raise ToolInputError(
                "image_tag must be a full URI with tag (no absolute paths or '..' segments); "
                "expected pattern like gcr.io/project/image:tag."
            )
        if not _IMAGE_TAG_RE.match(image_tag):
            raise ToolInputError(
                "image_tag must be a full URI with tag matching "
                "'^[a-zA-Z0-9._\\-/]+:[a-zA-Z0-9._\\-]{1,128}$' (e.g. gcr.io/proj/svc:tag)."
            )

        service_name = inputs["service_name"]
        if not isinstance(service_name, str) or not _SERVICE_NAME_RE.match(service_name):
            raise ToolInputError(
                "service_name must match '^[a-z][a-z0-9\\-]{1,49}$' (2–50 chars, lowercase start)."
            )

        region = inputs["region"]
        if not isinstance(region, str) or region not in GCP_REGIONS:
            raise ToolInputError(
                f"region must be a supported GCP region. Permitted: {sorted(GCP_REGIONS)}"
            )

        project_id = inputs["project_id"]
        if not isinstance(project_id, str) or not _PROJECT_ID_RE.match(project_id):
            raise ToolInputError(
                "project_id must match '^[a-z][a-z0-9\\-]{5,29}$' (6–30 chars, lowercase start)."
            )

        environment = inputs["environment"]
        dry_run = bool(inputs.get("dry_run", True))

        if environment == "prod" and dry_run:
            log.warning(
                "cloud_run_deploy_prod_dry_run",
                message="prod deploy requires dry_run=false to be set explicitly when you intend to deploy.",
            )

        cmd: list[str] = [
            "bash",
            str(DEPLOY_SCRIPT_PATH),
            "--image",
            image_tag,
            "--service",
            service_name,
            "--region",
            region,
            "--project",
            project_id,
            "--environment",
            environment,
        ]
        if dry_run:
            cmd.append("--dry-run")

        log.debug("cloud_run_deploy_cmd", command=cmd)

        t0 = time.perf_counter()
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=240,
            )
        except subprocess.TimeoutExpired:
            log.debug(
                "cloud_run_deploy_done",
                returncode=-1,
                elapsed_ms=int((time.perf_counter() - t0) * 1000),
            )
            return {
                "success": False,
                "service_url": None,
                "revision": None,
                "stdout": "",
                "stderr": "Deploy timed out after 240 seconds.",
                "dry_run": dry_run,
                "script_hash": actual_hash,
            }
        except FileNotFoundError as exc:
            raise ToolInputError("bash not available in this environment.") from exc

        elapsed_ms = int((time.perf_counter() - t0) * 1000)
        log.debug(
            "cloud_run_deploy_done",
            returncode=proc.returncode,
            elapsed_ms=elapsed_ms,
        )

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        log.debug(
            "cloud_run_deploy_io_lens",
            stdout_chars=len(stdout),
            stderr_chars=len(stderr),
        )

        return {
            "success": proc.returncode == 0,
            "service_url": _parse_service_url(stdout),
            "revision": _parse_revision(stdout),
            "stdout": _tail(stdout, 4000),
            "stderr": _tail(stderr, 2000),
            "dry_run": dry_run,
            "script_hash": actual_hash,
        }

    async def self_test(self) -> bool:
        if not DEPLOY_SCRIPT_PATH.exists():
            log.warning("cloud_run_deploy_self_test", detail="deploy script missing", path=str(DEPLOY_SCRIPT_PATH))
            return False
        if os.name != "nt" and not os.access(DEPLOY_SCRIPT_PATH, os.X_OK):
            log.warning("cloud_run_deploy_self_test", detail="deploy script not executable", path=str(DEPLOY_SCRIPT_PATH))
            return False
        try:
            await self._run(
                {
                    "image_tag": "gcr.io/example/app:v1",
                    "service_name": "myservice",
                    "region": "us-central1",
                    "project_id": "myproject",
                    "environment": "dev",
                    "dry_run": True,
                }
            )
            return True
        except Exception as exc:
            log.warning("cloud_run_deploy_self_test", detail=str(exc))
            return False


handler = CloudRunDeployHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
