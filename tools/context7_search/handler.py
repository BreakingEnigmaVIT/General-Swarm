"""context7_search — find GitHub repos that match a project description.

Uses the GitHub Search API (code search + repo search) to surface publicly
available codebases that could serve as a starting point.  The name follows
the user-facing "context7" search concept: given a project context, find the
best 7-10 existing repositories before deciding to build from scratch.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from tools.base import ToolHandler

# ── Helpers ───────────────────────────────────────────────────────────────────

# Keywords that frequently appear in tech-stack descriptions; mapped to GitHub
# topics/language names so we can build a tighter search query.
_TECH_ALIASES: dict[str, list[str]] = {
    "fastapi":      ["fastapi", "python"],
    "flask":        ["flask",   "python"],
    "django":       ["django",  "python"],
    "react":        ["react",   "typescript"],
    "nextjs":       ["nextjs",  "typescript"],
    "next.js":      ["nextjs",  "typescript"],
    "vue":          ["vue",     "javascript"],
    "angular":      ["angular", "typescript"],
    "express":      ["express", "nodejs"],
    "node":         ["nodejs",  "javascript"],
    "postgres":     ["postgresql"],
    "postgresql":   ["postgresql"],
    "mysql":        ["mysql"],
    "mongo":        ["mongodb"],
    "mongodb":      ["mongodb"],
    "redis":        ["redis"],
    "docker":       ["docker"],
    "kubernetes":   ["kubernetes"],
    "graphql":      ["graphql"],
    "rest":         ["rest-api"],
    "crud":         ["crud"],
    "tailwind":     ["tailwindcss"],
    "supabase":     ["supabase"],
    "prisma":       ["prisma"],
    "sqlalchemy":   ["sqlalchemy"],
    "alembic":      ["alembic"],
    "jwt":          ["jwt", "authentication"],
    "auth":         ["authentication"],
}


def _extract_topics(query: str, explicit_topics: list[str]) -> list[str]:
    """Build a deduplicated list of relevant GitHub topics from the query text."""
    topics: list[str] = list(explicit_topics)
    lower = query.lower()
    for keyword, mapped in _TECH_ALIASES.items():
        if keyword in lower:
            topics.extend(mapped)
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _build_search_query(
    query: str,
    language: str,
    topics: list[str],
    min_stars: int,
) -> str:
    """Convert natural-language query into a GitHub search query string."""
    # Extract significant nouns/tech words from the free-text query
    stop_words = {
        "a", "an", "the", "with", "and", "or", "for", "to", "of", "in",
        "on", "at", "by", "is", "it", "as", "app", "application",
        "project", "build", "using", "use", "that", "this",
    }
    words = re.findall(r"[a-zA-Z0-9.+#_-]{2,}", query.lower())
    keywords = [w for w in words if w not in stop_words][:6]

    parts = [" ".join(keywords[:4])]  # free-text portion

    if language:
        parts.append(f"language:{language.lower()}")

    for topic in topics[:3]:  # GitHub allows a few topic: filters
        parts.append(f"topic:{topic}")

    if min_stars > 0:
        parts.append(f"stars:>={min_stars}")

    return " ".join(parts)


def _similarity_score(
    repo: dict[str, Any],
    query_lower: str,
    topics_wanted: list[str],
    language: str,
) -> float:
    """Heuristic similarity: topic overlap + description match + language match."""
    score = 0.0

    repo_topics: list[str] = repo.get("topics") or []
    repo_lang: str = (repo.get("language") or "").lower()
    repo_desc: str = (repo.get("description") or "").lower()
    repo_name: str = (repo.get("name") or "").lower()

    # Topic overlap (most weight)
    if topics_wanted:
        overlap = sum(1 for t in topics_wanted if t in repo_topics)
        score += 0.5 * (overlap / len(topics_wanted))

    # Language match
    if language and repo_lang == language.lower():
        score += 0.2
    elif not language:
        score += 0.1  # no preference — neutral bonus

    # Description / name keyword match
    query_words = set(re.findall(r"[a-z0-9]{3,}", query_lower))
    desc_words  = set(re.findall(r"[a-z0-9]{3,}", repo_desc + " " + repo_name))
    if query_words:
        kw_overlap = len(query_words & desc_words) / len(query_words)
        score += 0.3 * kw_overlap

    return round(min(score, 1.0), 3)


class Context7SearchHandler(ToolHandler):
    """Search GitHub for repositories matching a project description."""

    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        token = os.environ.get("SWARM_GITHUB_TOKEN", "").strip()
        if not token:
            return {
                "results": [],
                "total_found": 0,
                "query_used": "",
                "error": (
                    "SWARM_GITHUB_TOKEN is not set. "
                    "Add it to .env to enable GitHub repo search."
                ),
            }

        query:      str       = inputs["query"]
        language:   str       = inputs.get("language") or ""
        topics_in:  list[str] = inputs.get("topics") or []
        min_stars:  int       = int(inputs.get("min_stars", 10))
        max_results: int      = min(int(inputs.get("max_results", 10)), 30)
        threshold:  float     = float(inputs.get("similarity_threshold", 0.3))

        topics_wanted = _extract_topics(query, topics_in)
        gh_query      = _build_search_query(query, language, topics_wanted, min_stars)

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        import httpx
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(
                "https://api.github.com/search/repositories",
                headers=headers,
                params={
                    "q":        gh_query,
                    "sort":     "stars",
                    "order":    "desc",
                    "per_page": max_results * 2,  # fetch extra to allow filtering
                },
            )

        if resp.status_code == 401:
            return {
                "results": [], "total_found": 0, "query_used": gh_query,
                "error": "GitHub token invalid or expired (401). Check SWARM_GITHUB_TOKEN.",
            }
        if resp.status_code == 403:
            return {
                "results": [], "total_found": 0, "query_used": gh_query,
                "error": "GitHub rate limit exceeded (403). Wait a minute or use a token with higher limits.",
            }
        if resp.status_code != 200:
            return {
                "results": [], "total_found": 0, "query_used": gh_query,
                "error": f"GitHub API error {resp.status_code}: {resp.text[:400]}",
            }

        data  = resp.json()
        items = data.get("items") or []
        query_lower = query.lower()

        results: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            score = _similarity_score(item, query_lower, topics_wanted, language)
            if score < threshold:
                continue
            results.append({
                "repo_full_name":  item.get("full_name", ""),
                "html_url":        item.get("html_url", ""),
                "description":     item.get("description") or "",
                "stars":           item.get("stargazers_count", 0),
                "language":        item.get("language") or "",
                "topics":          item.get("topics") or [],
                "similarity_score": score,
                "clone_url":       item.get("clone_url", ""),
                "default_branch":  item.get("default_branch", "main"),
                "last_push":       item.get("pushed_at", ""),
            })

        results.sort(key=lambda r: r["similarity_score"], reverse=True)
        results = results[:max_results]

        best_match = results[0] if results else None

        return {
            "results":     results,
            "total_found": len(results),
            "query_used":  gh_query,
            "best_match":  best_match,
        }

    async def self_test(self) -> bool:
        # Offline: just confirm the query builder works
        q = _build_search_query(
            "FastAPI React todo app Postgres", "python", ["fastapi", "react"], 5
        )
        return "fastapi" in q.lower() or "todo" in q.lower()


handler = Context7SearchHandler()

_spec_path = Path(__file__).parent / "spec.yaml"
if _spec_path.exists():
    from configs.loader import load_tool_spec
    handler.spec = load_tool_spec(_spec_path)
