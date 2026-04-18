"""Service definitions for `swarm auth` flows."""

from __future__ import annotations

from typing import Any

AUTH_SERVICES: list[dict[str, Any]] = [
    {
        "name": "Groq",
        "env_key": "GROQ_API_KEY",
        "method": "apikey",
        "required": True,
        "description": "API key from console.groq.com — powers all LLM calls",
        "test_cmd": None,
    },
    {
        "name": "GCP",
        "env_key": "GOOGLE_APPLICATION_CREDENTIALS",
        "method": "adc",
        "required": True,
        "description": "Google Cloud credentials for gcloud CLI and APIs",
        "test_cmd": "gcloud auth print-access-token --quiet",
    },
    {
        "name": "GitHub",
        "env_key": "SWARM_GITHUB_TOKEN",
        "method": "apikey",
        "required": False,
        "description": "Personal Access Token with repo + workflow scopes",
        "test_cmd": None,
    },
    {
        "name": "Cloudflare",
        "env_key": "SWARM_CLOUDFLARE_TOKEN",
        "method": "apikey",
        "required": False,
        "description": "API token scoped to Zone:DNS Edit for your domain",
        "test_cmd": None,
    },
    {
        "name": "Linear",
        "env_key": "SWARM_LINEAR_TOKEN",
        "method": "apikey",
        "required": False,
        "description": "Personal API key from Linear settings > API",
        "test_cmd": None,
    },
    {
        "name": "Notion",
        "env_key": "SWARM_NOTION_TOKEN",
        "method": "oauth",
        "required": False,
        "description": "Integration token — browser flow will open automatically",
        "test_cmd": None,
    },
    {
        "name": "PagerDuty",
        "env_key": "SWARM_PAGERDUTY_TOKEN",
        "method": "apikey",
        "required": False,
        "description": "User API token from PagerDuty My Profile > API Access",
        "test_cmd": None,
    },
    {
        "name": "Supabase",
        "env_key": "SWARM_SUPABASE_KEY",
        "method": "apikey",
        "required": False,
        "description": "Service role key from Supabase project settings > API",
        "test_cmd": None,
    },
    {
        "name": "Intercom",
        "env_key": "SWARM_INTERCOM_TOKEN",
        "method": "oauth",
        "required": False,
        "description": "Access token — browser flow will open automatically",
        "test_cmd": None,
    },
    {
        "name": "GKE cluster",
        "env_key": "SWARM_GKE_CLUSTER",
        "method": "apikey",
        "required": False,
        "description": "Your GKE cluster name (not a secret, just config)",
        "test_cmd": "gcloud container clusters list --format value(name)",
    },
]
