"""OAuth 2.0 flow helper — builds authorization URLs and exchanges codes for tokens."""

from __future__ import annotations

import os
import secrets
import urllib.parse
from typing import Any

from tools.base import ToolHandler


class OAuthFlowHelperHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        action = inputs["action"]

        if action == "build_auth_url":
            return self._build_auth_url(inputs)
        elif action == "exchange_code":
            return await self._exchange_code(inputs)
        elif action == "refresh_token":
            return await self._refresh_token(inputs)
        return {"error": f"Unknown action: {action}"}

    def _build_auth_url(self, inputs: dict) -> dict:
        client_id = inputs.get("client_id") or os.environ.get("OAUTH_CLIENT_ID", "")
        auth_endpoint = inputs["auth_endpoint"]
        redirect_uri = inputs["redirect_uri"]
        scopes = inputs.get("scopes", [])
        state = inputs.get("state") or secrets.token_urlsafe(16)

        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(scopes),
            "state": state,
        }
        extra = inputs.get("extra_params", {})
        params.update(extra)

        url = auth_endpoint + "?" + urllib.parse.urlencode(params)
        return {"authorization_url": url, "state": state}

    async def _exchange_code(self, inputs: dict) -> dict:
        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        client_id = inputs.get("client_id") or os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = inputs.get("client_secret") or os.environ.get("OAUTH_CLIENT_SECRET", "")
        token_endpoint = inputs["token_endpoint"]
        code = inputs["code"]
        redirect_uri = inputs["redirect_uri"]

        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(token_endpoint, data=data)

        if resp.status_code != 200:
            return {"error": f"Token exchange failed: {resp.status_code}", "body": resp.text[:500]}

        token_data = resp.json()
        return {
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type", "Bearer"),
        }

    async def _refresh_token(self, inputs: dict) -> dict:
        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        client_id = inputs.get("client_id") or os.environ.get("OAUTH_CLIENT_ID", "")
        client_secret = inputs.get("client_secret") or os.environ.get("OAUTH_CLIENT_SECRET", "")
        token_endpoint = inputs["token_endpoint"]
        refresh_token = inputs["refresh_token"]

        data = {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
        }
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(token_endpoint, data=data)

        if resp.status_code != 200:
            return {"error": f"Token refresh failed: {resp.status_code}", "body": resp.text[:500]}

        token_data = resp.json()
        return {
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", refresh_token),
            "expires_in": token_data.get("expires_in"),
            "token_type": token_data.get("token_type", "Bearer"),
        }

    async def self_test(self) -> bool:
        result = self._build_auth_url({
            "action": "build_auth_url",
            "auth_endpoint": "https://example.com/oauth/authorize",
            "redirect_uri": "https://localhost/callback",
            "scopes": ["read", "write"],
        })
        return "authorization_url" in result


handler = OAuthFlowHelperHandler()
