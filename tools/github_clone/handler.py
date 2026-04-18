"""github_clone — clone any GitHub repo into the built/ workspace directory."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError, ToolInputError
from tools.base import ToolHandler

_BUILT_ROOT = (Path.cwd() / "built").resolve()
_BUILT_ROOT.mkdir(exist_ok=True)


def _normalise_url(repo_url: str, token: str) -> tuple[str, str]:
    """Return (authenticated_clone_url, repo_full_name)."""
    url = repo_url.strip()

    # Accept "owner/repo" shorthand
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", url):
        url = f"https://github.com/{url}"

    # Strip trailing .git for the full_name, keep for clone
    full_name = re.sub(r"^https?://github\.com/", "", url).rstrip("/").removesuffix(".git")

    # Inject token into HTTPS URL so private repos work without SSH keys
    if token and url.startswith("https://"):
        url = url.replace("https://", f"https://{token}@", 1)

    if not url.endswith(".git"):
        url = url + ".git"

    return url, full_name


def _sanitise(text: str, token: str) -> str:
    """Remove the token from any error text before returning it to the agent."""
    if token:
        text = text.replace(token, "***")
    return text


class GitHubCloneHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token      = os.environ.get("SWARM_GITHUB_TOKEN", "").strip()
        repo_url   = inputs["repo_url"]
        target_rel = inputs["target_dir"].strip("/")
        branch     = inputs.get("branch") or ""
        depth      = int(inputs.get("depth", 1))

        # Safety: prevent path traversal
        if ".." in Path(target_rel).parts:
            raise SafetyError("target_dir must not contain '..'")
        target_path = (_BUILT_ROOT / target_rel).resolve()
        if not str(target_path).startswith(str(_BUILT_ROOT)):
            raise SafetyError("target_dir escapes built/ root")
        if target_path.exists():
            raise ToolInputError(f"target_dir already exists: {target_path}")

        clone_url, full_name = _normalise_url(repo_url, token)

        # Build git clone command
        cmd: list[str] = ["git", "clone"]
        if depth > 0:
            cmd += ["--depth", str(depth)]
        if branch:
            cmd += ["--branch", branch, "--single-branch"]
        cmd += [clone_url, str(target_path)]

        def _run_clone() -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=150,
            )

        try:
            result = await asyncio.to_thread(_run_clone)
        except subprocess.TimeoutExpired:
            return {
                "cloned_to": "", "repo_full_name": full_name,
                "error": "git clone timed out after 150 s",
            }
        except Exception as exc:
            return {
                "cloned_to": "", "repo_full_name": full_name,
                "error": _sanitise(str(exc), token),
            }

        if result.returncode != 0:
            msg = (result.stderr or "") + (result.stdout or "")
            return {
                "cloned_to": "", "repo_full_name": full_name,
                "error": _sanitise(msg.strip() or "git clone failed", token),
            }

        # Determine default branch and HEAD commit
        default_branch = branch or "main"
        commit_sha = ""
        try:
            rb = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, cwd=str(target_path), timeout=10,
            )
            if rb.returncode == 0:
                default_branch = rb.stdout.strip() or default_branch

            rc = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True, cwd=str(target_path), timeout=10,
            )
            if rc.returncode == 0:
                commit_sha = rc.stdout.strip()
        except Exception:
            pass

        # Count files and list top-level dirs
        all_files   = [p for p in target_path.rglob("*") if p.is_file() and ".git" not in p.parts]
        top_dirs    = sorted({
            p.relative_to(target_path).parts[0]
            for p in target_path.iterdir()
            if p.is_dir() and p.name != ".git"
        })
        rel_cloned  = str(target_path.relative_to(Path.cwd().resolve()))

        return {
            "cloned_to":      rel_cloned,
            "repo_full_name": full_name,
            "default_branch": default_branch,
            "commit_sha":     commit_sha,
            "file_count":     len(all_files),
            "top_level_dirs": top_dirs,
        }

    async def self_test(self) -> bool:
        return True  # clone requires network — skip in unit test mode


handler = GitHubCloneHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec
    handler.spec = load_tool_spec(_spec_path)
