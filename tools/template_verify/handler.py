"""Verify SHA-256 integrity of locked files in a cloned boilerplate template."""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from core.exceptions import SafetyError
from tools.base import ToolHandler

WORKSPACE_ROOT = (Path.cwd() / "workspace").resolve()
WORKSPACE_ROOT.mkdir(exist_ok=True)

MANIFEST_FILENAME = ".swarm-template-manifest.json"


class TemplateVerifyHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        target_rel = inputs["target_dir"]
        if ".." in Path(target_rel).parts:
            raise SafetyError("target_dir escapes workspace root")

        root = (WORKSPACE_ROOT / target_rel).resolve()
        if not str(root).startswith(str(WORKSPACE_ROOT)):
            raise SafetyError("target_dir escapes workspace root")

        if not root.is_dir():
            return {
                "valid": False,
                "checked": 0,
                "violations": [
                    {
                        "path": str(target_rel),
                        "expected": "directory",
                        "actual": "missing or not a directory",
                    }
                ],
            }

        locked = inputs.get("locked_hashes")
        if locked is None:
            mf = root / MANIFEST_FILENAME
            if not mf.is_file():
                return {
                    "valid": False,
                    "checked": 0,
                    "violations": [
                        {
                            "path": MANIFEST_FILENAME,
                            "expected": "file",
                            "actual": "missing",
                        }
                    ],
                }
            try:
                doc = json.loads(mf.read_text(encoding="utf-8"))
            except Exception as exc:
                return {
                    "valid": False,
                    "checked": 0,
                    "violations": [
                        {
                            "path": MANIFEST_FILENAME,
                            "expected": "valid JSON",
                            "actual": str(exc),
                        }
                    ],
                }
            locked = doc.get("locked_hashes") if isinstance(doc, dict) else None

        if not isinstance(locked, dict):
            return {
                "valid": False,
                "checked": 0,
                "violations": [
                    {
                        "path": "",
                        "expected": "object",
                        "actual": "locked_hashes not found or invalid",
                    }
                ],
            }

        violations: list[dict[str, str]] = []
        checked = 0
        for rel_raw, expected in locked.items():
            if not isinstance(rel_raw, str) or not isinstance(expected, str):
                continue
            rel = rel_raw.replace("\\", "/")
            fp = (root / rel).resolve()
            if not str(fp).startswith(str(root)):
                violations.append(
                    {"path": rel, "expected": expected, "actual": "path escapes workspace"}
                )
                continue
            checked += 1
            if not fp.is_file():
                violations.append({"path": rel, "expected": expected, "actual": "missing"})
                continue
            actual = hashlib.sha256(fp.read_bytes()).hexdigest()
            exp = expected.lower().strip()
            if actual != exp:
                violations.append({"path": rel, "expected": expected, "actual": actual})

        return {"valid": len(violations) == 0, "checked": checked, "violations": violations}

    async def self_test(self) -> bool:
        tid = f"_selftest_tpl_verify_{uuid.uuid4().hex[:12]}"
        root = WORKSPACE_ROOT / tid
        try:
            root.mkdir(parents=True, exist_ok=True)
            locked_path = root / "locked.txt"
            data = b"integrity-check-bytes\n"
            locked_path.write_bytes(data)
            h = hashlib.sha256(data).hexdigest()
            (root / MANIFEST_FILENAME).write_text(
                json.dumps({"locked_hashes": {"locked.txt": h}}),
                encoding="utf-8",
            )
            out = await self._run({"target_dir": tid})
            if not out.get("valid") or out.get("checked") != 1:
                return False
            bad = await self._run({"target_dir": tid, "locked_hashes": {"locked.txt": "0" * 64}})
            return bad.get("valid") is False and len(bad.get("violations", [])) >= 1
        finally:
            shutil.rmtree(root, ignore_errors=True)


handler = TemplateVerifyHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
