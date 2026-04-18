"""Changelog generator from artifact lineage."""

from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from tools.base import ToolHandler

_CWD = Path.cwd()


class ChangelogGenerateHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        from memory.artifacts import get_artifact_registry

        version = inputs["version_tag"]
        release_id = inputs.get("release_decision_id", "")
        project_id = inputs.get("project_id", "")
        output_path = inputs.get("output_path", "")

        reg = get_artifact_registry()

        # Gather recent artifacts
        sections: dict[str, list[str]] = {
            "Features": [],
            "Bug Fixes": [],
            "Architecture": [],
            "Infrastructure": [],
        }

        if release_id:
            lineage = await reg.get_lineage(release_id, depth=20)
            for artifact in lineage:
                a_type = artifact.artifact_type if isinstance(artifact.artifact_type, str) else artifact.artifact_type.value
                if a_type == "CodeChangeSet":
                    summary = getattr(artifact, "summary", "") or f"Layer: {getattr(artifact, 'layer', 'unknown')}"
                    layer = getattr(artifact, "layer", "")
                    if layer in ("frontend", "backend"):
                        sections["Features"].append(f"- {summary}")
                    elif layer == "database":
                        sections["Infrastructure"].append(f"- Database: {summary}")
                    elif layer == "devops":
                        sections["Infrastructure"].append(f"- CI/CD: {summary}")
                elif a_type == "ArchitectureDoc":
                    sections["Architecture"].append("- System architecture updated")
        else:
            # Fall back to listing recent CodeChangeSets
            code_sets = await reg.list_by_type("CodeChangeSet", project_id=project_id)
            for cs in code_sets[-10:]:
                summary = getattr(cs, "summary", "") or f"Layer: {getattr(cs, 'layer', 'unknown')}"
                sections["Features"].append(f"- {summary}")

        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        lines = [f"## [{version}] - {date_str}\n"]
        for section, items in sections.items():
            if items:
                lines.append(f"\n### {section}\n")
                lines.extend(items)

        changelog_entry = "\n".join(lines)

        if output_path:
            out = (_CWD / output_path).resolve()
            if not str(out).startswith(str(_CWD)):
                return {"error": "output_path escapes project root"}
            existing = out.read_text(encoding="utf-8") if out.exists() else ""
            out.write_text(changelog_entry + "\n\n" + existing, encoding="utf-8")

        return {
            "version": version,
            "changelog": changelog_entry,
            "written_to": output_path or None,
        }

    async def self_test(self) -> bool:
        result = await self._run({"version_tag": "v0.1.0"})
        return "changelog" in result


handler = ChangelogGenerateHandler()
