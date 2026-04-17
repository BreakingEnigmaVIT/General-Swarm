"""HTTP request tool with domain allowlist support."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from tools.base import ToolHandler

_DOMAIN_ALLOWLIST: list[str] | None = None  # None = allow all; set from topology safety config


def set_domain_allowlist(domains: list[str] | None) -> None:
    global _DOMAIN_ALLOWLIST
    _DOMAIN_ALLOWLIST = domains


class HttpRequestHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        import httpx

        url = inputs["url"]
        method = inputs.get("method", "GET").upper()
        headers = inputs.get("headers") or {}
        body = inputs.get("body")
        timeout = float(inputs.get("timeout", 15))

        # Domain allowlist check
        if _DOMAIN_ALLOWLIST is not None:
            domain = urlparse(url).netloc
            if not any(domain.endswith(allowed) for allowed in _DOMAIN_ALLOWLIST):
                return {"error": f"Domain not in allowlist: {domain}"}

        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
                resp = await client.request(
                    method, url, headers=headers,
                    content=body.encode() if body else None
                )
            return {
                "status_code": resp.status_code,
                "headers": dict(resp.headers),
                "body": resp.text[:10000],
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def self_test(self) -> bool:
        return True


handler = HttpRequestHandler()
