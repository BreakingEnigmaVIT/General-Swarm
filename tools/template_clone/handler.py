"""Clone a boilerplate template repo into the workspace with param substitution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError, ToolInputError
from tools.base import ToolHandler

WORKSPACE_ROOT = (Path.cwd() / "workspace").resolve()
WORKSPACE_ROOT.mkdir(exist_ok=True)

MANIFEST_FILENAME = ".swarm-template-manifest.json"


def _norm_rel(rel: str) -> str:
    return rel.replace("\\", "/")


def _resolve_file_mode(rel: str, files_spec: list[Any]) -> str:
    rel = _norm_rel(rel)
    for item in files_spec:
        if not isinstance(item, dict):
            continue
        p = _norm_rel(str(item.get("path", "")))
        if not p or p.endswith("/"):
            continue
        if rel == p:
            return str(item.get("mode", "extend"))
    best_len = -1
    best_mode = "extend"
    for item in files_spec:
        if not isinstance(item, dict):
            continue
        p = _norm_rel(str(item.get("path", "")))
        if not p.endswith("/"):
            continue
        base = p.rstrip("/")
        if rel == base or rel.startswith(base + "/"):
            if len(base) > best_len:
                best_len = len(base)
                best_mode = str(item.get("mode", "extend"))
    return best_mode


def _iter_repo_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(root)
        if ".git" in rel.parts:
            continue
        out.append(p)
    out.sort(key=lambda x: _norm_rel(str(x.relative_to(root))))
    return out


def _sanitize_token_text(text: str, token: str) -> str:
    if not token:
        return text
    return text.replace(token, "***")


def _clone_error(msg: str, template_id: str, token: str) -> dict[str, Any]:
    return {
        "error": _sanitize_token_text(msg, token),
        "cloned_to": "",
        "template_id": template_id,
        "template_version": "",
        "files": [],
        "locked_hashes": {},
        "params_applied": {},
        "warnings": [],
    }


class TemplateCloneHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        template_id = str(inputs["template_id"])
        target_rel = inputs["target_dir"]
        params_in: dict[str, Any] = dict(inputs.get("params") or {})

        if ".." in Path(target_rel).parts:
            raise SafetyError("target_dir escapes workspace root")

        target_path = (WORKSPACE_ROOT / target_rel).resolve()
        if not str(target_path).startswith(str(WORKSPACE_ROOT)):
            raise SafetyError("target_dir escapes workspace root")
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists():
            raise ToolInputError(f"target_dir already exists: {target_path}")

        org = os.environ.get("SWARM_TEMPLATE_ORG", "").strip()
        token = os.environ.get("SWARM_GITHUB_TOKEN", "").strip()
        if not org or not token:
            return _clone_error("SWARM_TEMPLATE_ORG or SWARM_GITHUB_TOKEN not set", template_id, token)

        repo_name = f"boilerplate-{template_id}"
        clone_url = f"https://{token}@github.com/{org}/{repo_name}.git"

        tmp_base = tempfile.mkdtemp(prefix="swarm_tpl_clone_")
        clone_root = Path(tmp_base) / "repo"
        try:

            def _git_clone() -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["git", "clone", "--depth", "1", clone_url, str(clone_root)],
                    capture_output=True,
                    text=True,
                    timeout=90,
                )

            result = await asyncio.to_thread(_git_clone)
            if result.returncode != 0:
                msg = (result.stderr or "") + (result.stdout or "")
                shutil.rmtree(tmp_base, ignore_errors=True)
                return _clone_error(msg.strip() or "git clone failed", template_id, token)
        except Exception as exc:
            shutil.rmtree(tmp_base, ignore_errors=True)
            return _clone_error(str(exc), template_id, token)

        warnings: list[str] = []
        tpl_path = clone_root / "template.json"
        manifest_doc: dict[str, Any] = {}
        files_spec: list[Any] = []
        template_version = "unknown"
        params_applied: dict[str, str] = {}

        if not tpl_path.is_file():
            warnings.append("template.json not found — all files treated as extend mode")
            params_applied = {str(k): str(v) for k, v in params_in.items()}
        else:
            try:
                loaded = json.loads(tpl_path.read_text(encoding="utf-8"))
            except Exception:
                warnings.append("template.json invalid — all files treated as extend mode")
                params_applied = {str(k): str(v) for k, v in params_in.items()}
            else:
                if not isinstance(loaded, dict):
                    warnings.append("template.json invalid — all files treated as extend mode")
                    params_applied = {str(k): str(v) for k, v in params_in.items()}
                else:
                    manifest_doc = loaded
                    template_version = str(manifest_doc.get("version", "unknown"))
                    raw_files = manifest_doc.get("files")
                    files_spec = raw_files if isinstance(raw_files, list) else []
                    spec_params = manifest_doc.get("params")
                    spec_list = spec_params if isinstance(spec_params, list) else []
                    missing: list[str] = []
                    defaults: dict[str, str] = {}
                    for spec_p in spec_list:
                        if not isinstance(spec_p, dict):
                            continue
                        key = str(spec_p.get("key", ""))
                        if not key:
                            continue
                        if spec_p.get("required") is True and key not in params_in:
                            missing.append(key)
                        if key not in params_in and "default" in spec_p:
                            defaults[key] = str(spec_p.get("default"))
                    if missing:
                        shutil.rmtree(tmp_base, ignore_errors=True)
                        raise ToolInputError(
                            f"Missing required template params: {', '.join(sorted(missing))}"
                        )
                    params_applied = {**defaults, **{str(k): str(v) for k, v in params_in.items()}}

        locked_hashes: dict[str, str] = {}
        for fp in _iter_repo_files(clone_root):
            rel = _norm_rel(str(fp.relative_to(clone_root)))
            mode = _resolve_file_mode(rel, files_spec) if files_spec else "extend"

            raw_bytes = fp.read_bytes()
            if mode == "locked":
                locked_hashes[rel] = hashlib.sha256(raw_bytes).hexdigest()
                continue
            if mode == "extend":
                continue
            if mode == "configure":
                try:
                    text = raw_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    continue
                new_text = text
                for k, v in params_applied.items():
                    new_text = new_text.replace("{{" + k + "}}", v)
                if new_text != text:
                    fp.write_text(new_text, encoding="utf-8", newline="")

        files_out: list[dict[str, str]] = []
        for fp in _iter_repo_files(clone_root):
            rel = _norm_rel(str(fp.relative_to(clone_root)))
            mode = _resolve_file_mode(rel, files_spec) if files_spec else "extend"
            sha = locked_hashes.get(rel, "")
            files_out.append({"path": rel, "mode": mode, "sha256": sha})

        disk_manifest = {
            "locked_hashes": locked_hashes,
            "template_id": template_id,
            "template_version": template_version,
        }
        (clone_root / MANIFEST_FILENAME).write_text(
            json.dumps(disk_manifest, indent=2), encoding="utf-8"
        )
        rel_manifest = _norm_rel(MANIFEST_FILENAME)
        files_out.append({"path": rel_manifest, "mode": "extend", "sha256": ""})
        files_out.sort(key=lambda x: x["path"])

        try:
            shutil.move(str(clone_root), str(target_path))
        except Exception as exc:
            shutil.rmtree(tmp_base, ignore_errors=True)
            return _clone_error(str(exc), template_id, token)

        shutil.rmtree(tmp_base, ignore_errors=True)

        return {
            "cloned_to": str(target_path.relative_to(Path.cwd().resolve())),
            "template_id": template_id,
            "template_version": template_version,
            "files": files_out,
            "locked_hashes": locked_hashes,
            "params_applied": params_applied,
            "warnings": warnings,
        }

    async def self_test(self) -> bool:
        import json as _json
        from unittest.mock import patch

        fixture = Path(tempfile.mkdtemp(prefix="swarm_tpl_fixture_"))
        tid = f"_selftest_tpl_clone_{uuid.uuid4().hex[:12]}"
        try:
            tpl = {
                "id": "fake-template",
                "version": "0.0.1",
                "params": [{"key": "PROJECT_NAME", "required": True, "description": "n"}],
                "files": [{"path": "config.py", "mode": "configure"}],
            }
            (fixture / "template.json").write_text(_json.dumps(tpl), encoding="utf-8")
            (fixture / "config.py").write_text("app={{PROJECT_NAME}}", encoding="utf-8")

            def _fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
                if len(cmd) >= 3 and cmd[0] == "git" and cmd[1] == "clone":
                    dest = Path(cmd[-1])
                    shutil.copytree(fixture, dest, dirs_exist_ok=True)
                    return subprocess.CompletedProcess(cmd, 0, "", "")
                return subprocess.CompletedProcess(cmd, 1, "", "unexpected command")

            with patch.dict(
                os.environ,
                {"SWARM_TEMPLATE_ORG": "BreakingEnigmaVIT", "SWARM_GITHUB_TOKEN": "fake-token"},
            ):
                with patch("tools.template_clone.handler.subprocess.run", side_effect=_fake_run):
                    out = await self._run(
                        {
                            "template_id": "fake-template",
                            "target_dir": tid,
                            "params": {"PROJECT_NAME": "MyApp"},
                        }
                    )
            if out.get("error"):
                return False
            if out.get("params_applied", {}).get("PROJECT_NAME") != "MyApp":
                return False
            dest = WORKSPACE_ROOT / tid
            if not dest.is_dir():
                return False
            cfg = (dest / "config.py").read_text(encoding="utf-8")
            if "MyApp" not in cfg or "{{PROJECT_NAME}}" in cfg:
                return False
            return "cloned_to" in out and tid in str(out["cloned_to"])
        finally:
            shutil.rmtree(fixture, ignore_errors=True)
            shutil.rmtree(WORKSPACE_ROOT / tid, ignore_errors=True)


handler = TemplateCloneHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
