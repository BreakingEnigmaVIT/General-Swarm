"""context7_search — multi-strategy GitHub repo search using a PAT.

Runs three parallel GitHub API calls:
  1. /search/repositories  — keyword + language + topic + stars filter
  2. /search/code          — finds repos whose source files match key terms
  3. /user/repos + /user/orgs → org repos  — surfaces private/org repos the PAT
                                             can access, scored against the query

Results are deduplicated by repo URL, scored with a heuristic that weighs
topic overlap, language match, description keyword match, and recency, then
returned sorted by descending similarity score.
"""

from __future__ import annotations

import asyncio
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tools.base import ToolHandler

# ── Tech-stack keyword → GitHub topic aliases ────────────────────────────────

_TECH_ALIASES: dict[str, list[str]] = {
    "fastapi":    ["fastapi", "python"],
    "flask":      ["flask", "python"],
    "django":     ["django", "python"],
    "react":      ["react", "typescript"],
    "nextjs":     ["nextjs", "typescript"],
    "next.js":    ["nextjs", "typescript"],
    "vue":        ["vue", "javascript"],
    "angular":    ["angular", "typescript"],
    "express":    ["express", "nodejs"],
    "node":       ["nodejs", "javascript"],
    "postgres":   ["postgresql"],
    "postgresql": ["postgresql"],
    "mysql":      ["mysql"],
    "mongo":      ["mongodb"],
    "mongodb":    ["mongodb"],
    "redis":      ["redis"],
    "docker":     ["docker"],
    "kubernetes": ["kubernetes"],
    "graphql":    ["graphql"],
    "rest":       ["rest-api"],
    "crud":       ["crud"],
    "tailwind":   ["tailwindcss"],
    "supabase":   ["supabase"],
    "prisma":     ["prisma"],
    "sqlalchemy": ["sqlalchemy"],
    "jwt":        ["jwt", "authentication"],
    "auth":       ["authentication"],
}

_STOP_WORDS = {
    "a", "an", "the", "with", "and", "or", "for", "to", "of", "in",
    "on", "at", "by", "is", "it", "as", "app", "application",
    "project", "build", "using", "use", "that", "this",
}


def _extract_topics(query: str, explicit: list[str]) -> list[str]:
    topics: list[str] = list(explicit)
    lower = query.lower()
    for kw, mapped in _TECH_ALIASES.items():
        if kw in lower:
            topics.extend(mapped)
    seen: set[str] = set()
    out: list[str] = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _keywords(text: str) -> list[str]:
    words = re.findall(r"[a-zA-Z0-9.+#_-]{2,}", text.lower())
    return [w for w in words if w not in _STOP_WORDS]


def _build_repo_query(query: str, language: str, topics: list[str], min_stars: int) -> str:
    kws = _keywords(query)[:4]
    parts = [" ".join(kws)]
    if language:
        parts.append(f"language:{language.lower()}")
    for topic in topics[:3]:
        parts.append(f"topic:{topic}")
    if min_stars > 0:
        parts.append(f"stars:>={min_stars}")
    return " ".join(parts)


def _build_code_query(query: str, language: str) -> str:
    kws = _keywords(query)[:3]
    parts = [" ".join(kws)]
    if language:
        parts.append(f"language:{language.lower()}")
    return " ".join(parts)


def _months_since(pushed_at: str) -> float:
    if not pushed_at:
        return 999.0
    try:
        dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - dt
        return delta.days / 30.0
    except Exception:
        return 999.0


def _score(repo: dict[str, Any], query_lower: str, topics_wanted: list[str], language: str) -> float:
    score = 0.0

    repo_topics: list[str] = repo.get("topics") or []
    repo_lang: str = (repo.get("language") or "").lower()
    repo_desc: str = (repo.get("description") or "").lower()
    repo_name: str = (repo.get("name") or "").lower()
    stars: int = int(repo.get("stargazers_count") or repo.get("stars") or 0)

    # Topic overlap — highest weight
    if topics_wanted:
        overlap = sum(1 for t in topics_wanted if t in repo_topics)
        score += 0.45 * (overlap / len(topics_wanted))

    # Language match
    if language and repo_lang == language.lower():
        score += 0.20
    elif not language:
        score += 0.05

    # Description + name keyword match
    q_words = set(re.findall(r"[a-z0-9]{3,}", query_lower))
    d_words = set(re.findall(r"[a-z0-9]{3,}", repo_desc + " " + repo_name))
    if q_words:
        score += 0.25 * (len(q_words & d_words) / len(q_words))

    # Recency bonus (maintained within 12 months)
    age_months = _months_since(repo.get("pushed_at") or repo.get("last_push") or "")
    if age_months <= 6:
        score += 0.07
    elif age_months <= 12:
        score += 0.03

    # Stars bonus (log-scaled, max 0.03)
    if stars >= 50:
        import math
        score += min(0.03, 0.01 * math.log10(stars / 10 + 1))

    return round(min(score, 1.0), 3)


def _repo_to_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_full_name":  item.get("full_name", ""),
        "html_url":        item.get("html_url", ""),
        "description":     item.get("description") or "",
        "stars":           item.get("stargazers_count", 0),
        "language":        item.get("language") or "",
        "topics":          item.get("topics") or [],
        "clone_url":       item.get("clone_url", ""),
        "default_branch":  item.get("default_branch", "main"),
        "last_push":       item.get("pushed_at", ""),
        "private":         bool(item.get("private", False)),
        "similarity_score": 0.0,
    }


class Context7SearchHandler(ToolHandler):
    """Multi-strategy GitHub repo search (repo search + code search + PAT-accessible repos)."""

    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_GITHUB_TOKEN", "").strip()
        if not token:
            return {
                "results": [], "total_found": 0, "query_used": "",
                "error": "SWARM_GITHUB_TOKEN is not set. Add it to .env.",
            }

        query: str        = inputs["query"]
        language: str     = inputs.get("language") or ""
        topics_in: list   = inputs.get("topics") or []
        min_stars: int    = int(inputs.get("min_stars", 10))
        max_results: int  = min(int(inputs.get("max_results", 10)), 30)
        threshold: float  = float(inputs.get("similarity_threshold", 0.3))

        topics_wanted = _extract_topics(query, topics_in)
        repo_query    = _build_repo_query(query, language, topics_wanted, min_stars)
        code_query    = _build_code_query(query, language)
        query_lower   = query.lower()

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:

            # ── 1. Repo search ────────────────────────────────────────────────
            async def _repo_search() -> list[dict[str, Any]]:
                r = await client.get(
                    "https://api.github.com/search/repositories",
                    headers=headers,
                    params={"q": repo_query, "sort": "stars", "order": "desc",
                            "per_page": max_results * 3},
                )
                if r.status_code != 200:
                    return []
                return r.json().get("items") or []

            # ── 2. Code search → extract unique parent repos ──────────────────
            async def _code_search() -> list[dict[str, Any]]:
                r = await client.get(
                    "https://api.github.com/search/code",
                    headers=headers,
                    params={"q": code_query, "per_page": 20},
                )
                if r.status_code != 200:
                    return []
                items = r.json().get("items") or []
                repos_seen: set[str] = set()
                unique: list[dict[str, Any]] = []
                for item in items:
                    repo = item.get("repository") or {}
                    full = repo.get("full_name", "")
                    if full and full not in repos_seen:
                        repos_seen.add(full)
                        # Fetch full repo metadata to get stars/topics/etc.
                        detail = await client.get(
                            f"https://api.github.com/repos/{full}",
                            headers=headers,
                        )
                        if detail.status_code == 200:
                            unique.append(detail.json())
                        if len(unique) >= 10:
                            break
                return unique

            # ── 3. PAT-accessible repos: user + org repos ─────────────────────
            async def _accessible_repos() -> list[dict[str, Any]]:
                collected: list[dict[str, Any]] = []

                # User's own repos (includes private)
                ur = await client.get(
                    "https://api.github.com/user/repos",
                    headers=headers,
                    params={"sort": "updated", "per_page": 50,
                            "visibility": "all", "affiliation": "owner,collaborator,organization_member"},
                )
                if ur.status_code == 200:
                    collected.extend(ur.json() or [])

                # Orgs the user belongs to
                orgs_r = await client.get(
                    "https://api.github.com/user/orgs",
                    headers=headers,
                    params={"per_page": 10},
                )
                if orgs_r.status_code == 200:
                    for org in (orgs_r.json() or [])[:5]:
                        login = org.get("login", "")
                        if not login:
                            continue
                        org_r = await client.get(
                            f"https://api.github.com/orgs/{login}/repos",
                            headers=headers,
                            params={"sort": "updated", "per_page": 30},
                        )
                        if org_r.status_code == 200:
                            collected.extend(org_r.json() or [])

                return collected

            repo_items, code_items, accessible_items = await asyncio.gather(
                _repo_search(),
                _code_search(),
                _accessible_repos(),
            )

        # ── Merge, deduplicate, score ─────────────────────────────────────────
        seen: dict[str, dict[str, Any]] = {}  # full_name → result dict

        for item in (repo_items + code_items + accessible_items):
            if not isinstance(item, dict):
                continue
            full_name = item.get("full_name", "")
            if not full_name:
                continue
            result = _repo_to_result(item)
            result["similarity_score"] = _score(item, query_lower, topics_wanted, language)
            if full_name not in seen or result["similarity_score"] > seen[full_name]["similarity_score"]:
                seen[full_name] = result

        results = [r for r in seen.values() if r["similarity_score"] >= threshold]
        results.sort(key=lambda r: r["similarity_score"], reverse=True)
        results = results[:max_results]

        best_match = results[0] if results else None

        return {
            "results":     results,
            "total_found": len(results),
            "query_used":  repo_query,
            "best_match":  best_match,
        }

    async def self_test(self) -> bool:
        q = _build_repo_query("FastAPI React todo app Postgres", "python", ["fastapi", "react"], 5)
        return "fastapi" in q.lower() or "todo" in q.lower()


handler = Context7SearchHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec
    handler.spec = load_tool_spec(_spec_path)
