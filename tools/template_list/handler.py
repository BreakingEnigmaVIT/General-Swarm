"""List available boilerplate templates from the GitHub template org."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tools.base import ToolHandler


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _parse_ci_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _is_stale(last_ci: str | None, stale_days: int) -> bool:
    if last_ci is None:
        return True
    d = _parse_ci_date(last_ci)
    if d is None:
        return True
    return (_today_utc() - d) > timedelta(days=stale_days)


def _matches_filter_tag(entry: dict[str, Any], tag: str) -> bool:
    t = tag.lower()
    if t in (entry.get("id") or "").lower():
        return True
    for x in entry.get("tags") or []:
        if t in str(x).lower():
            return True
    return False


class TemplateListHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        org = os.environ.get("SWARM_TEMPLATE_ORG", "").strip()
        token = os.environ.get("SWARM_GITHUB_TOKEN", "").strip()
        if not org or not token:
            return {
                "templates": [],
                "total": 0,
                "stale_hidden": 0,
                "error": "SWARM_TEMPLATE_ORG or SWARM_GITHUB_TOKEN not set",
            }

        filter_tag = (inputs.get("filter_tag") or "").strip()
        include_stale = bool(inputs.get("include_stale", False))

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repos_url = f"https://api.github.com/orgs/{org}/repos?per_page=100&type=public"

        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(repos_url, headers=headers)
            if resp.status_code != 200:
                return {
                    "templates": [],
                    "total": 0,
                    "stale_hidden": 0,
                    "error": f"GitHub API error {resp.status_code}: {resp.text[:500]}",
                }
            repos = resp.json()
            if not isinstance(repos, list):
                repos = []

            boilerplate = [
                r for r in repos if isinstance(r, dict) and (
                    str(r.get("name", "")).startswith("boilerplate-")
                    or str(r.get("name", "")).startswith("boilerplate_")
                )
            ]

            entries: list[dict[str, Any]] = []
            for repo in boilerplate:
                name = str(repo.get("name", ""))
                html_url = str(repo.get("html_url", ""))
                if name.startswith("boilerplate-"):
                    tpl_id = name[len("boilerplate-"):]
                elif name.startswith("boilerplate_"):
                    tpl_id = name[len("boilerplate_"):]
                else:
                    tpl_id = name
                raw_url = f"https://raw.githubusercontent.com/{org}/{name}/main/template.json"

                raw = await client.get(raw_url, headers=headers)

                if raw.status_code == 404:
                    entry = {
                        "id": tpl_id,
                        "version": "unknown",
                        "description": "template.json not yet available",
                        "repo_url": html_url,
                        "last_ci_pass": None,
                        "stale": True,
                        "params": [],
                        "tags": [],
                    }
                elif raw.status_code != 200:
                    entry = {
                        "id": tpl_id,
                        "version": "unknown",
                        "description": f"template.json fetch failed ({raw.status_code})",
                        "repo_url": html_url,
                        "last_ci_pass": None,
                        "stale": True,
                        "params": [],
                        "tags": [],
                    }
                else:
                    try:
                        data = raw.json()
                    except Exception:
                        entry = {
                            "id": tpl_id,
                            "version": "unknown",
                            "description": "invalid template.json",
                            "repo_url": html_url,
                            "last_ci_pass": None,
                            "stale": True,
                            "params": [],
                            "tags": [],
                        }
                    else:
                        if not isinstance(data, dict):
                            data = {}
                        last_ci = data.get("last_ci_pass")
                        last_ci_s = last_ci if isinstance(last_ci, str) else None
                        stale_days = int(data.get("stale_days", 60))
                        stale = _is_stale(last_ci_s, stale_days)
                        entry = {
                            "id": str(data.get("id", tpl_id)),
                            "version": str(data.get("version", "unknown")),
                            "description": str(data.get("description", "")),
                            "repo_url": html_url,
                            "last_ci_pass": last_ci_s,
                            "stale": stale,
                            "params": data.get("params") if isinstance(data.get("params"), list) else [],
                            "tags": data.get("tags") if isinstance(data.get("tags"), list) else [],
                        }
                entries.append(entry)

        if filter_tag:
            entries = [e for e in entries if _matches_filter_tag(e, filter_tag)]

        stale_hidden = 0
        if not include_stale:
            kept: list[dict[str, Any]] = []
            for e in entries:
                if e.get("stale"):
                    stale_hidden += 1
                else:
                    kept.append(e)
            entries = kept

        entries.sort(key=lambda e: (e.get("stale", False), str(e.get("id", "")).lower()))

        return {
            "templates": entries,
            "total": len(entries),
            "stale_hidden": stale_hidden,
        }

    async def self_test(self) -> bool:
        if not os.environ.get("SWARM_GITHUB_TOKEN"):
            return True
        from unittest.mock import AsyncMock, MagicMock, patch

        today = _today_utc().isoformat()

        def _tpl(tpl_id: str) -> str:
            return json.dumps(
                {
                    "id": tpl_id,
                    "version": "1.0.0",
                    "description": f"d {tpl_id}",
                    "last_ci_pass": today,
                    "stale_days": 60,
                    "params": [],
                    "tags": [tpl_id.split("-")[0]],
                }
            )

        repos_payload = [
            {
                "name": "boilerplate-fastapi-postgres",
                "html_url": "https://github.com/BreakingEnigmaVIT/boilerplate-fastapi-postgres",
            },
            {
                "name": "boilerplate-nextjs-app-router",
                "html_url": "https://github.com/BreakingEnigmaVIT/boilerplate-nextjs-app-router",
            },
        ]

        async def fake_get(url: str, headers: Any = None, **kwargs: Any) -> MagicMock:
            m = MagicMock()
            if "/orgs/" in url and "/repos" in url:
                m.status_code = 200
                m.json = lambda: repos_payload
                m.text = ""
                return m
            if "raw.githubusercontent.com" in url and "fastapi-postgres" in url:
                m.status_code = 200
                m.json = lambda: json.loads(_tpl("fastapi-postgres"))
                m.text = _tpl("fastapi-postgres")
                return m
            if "raw.githubusercontent.com" in url and "nextjs-app-router" in url:
                m.status_code = 200
                m.json = lambda: json.loads(_tpl("nextjs-app-router"))
                m.text = _tpl("nextjs-app-router")
                return m
            m.status_code = 404
            m.json = lambda: {}
            m.text = ""
            return m

        inst = MagicMock()
        inst.get = AsyncMock(side_effect=fake_get)
        inst.__aenter__ = AsyncMock(return_value=inst)
        inst.__aexit__ = AsyncMock(return_value=False)

        with patch.dict(
            os.environ,
            {"SWARM_TEMPLATE_ORG": "BreakingEnigmaVIT", "SWARM_GITHUB_TOKEN": "fake-token"},
        ):
            with patch("httpx.AsyncClient", return_value=inst):
                out = await self._run({"include_stale": True})
        if out.get("total") != 2:
            return False
        ids = {t["id"] for t in out["templates"]}
        return ids == {"fastapi-postgres", "nextjs-app-router"}


handler = TemplateListHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec

    handler.spec = load_tool_spec(_spec_path)
