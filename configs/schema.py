"""All Pydantic models for spec files (AgentSpec, ToolSpec) and swarm config."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ── Shared primitives ─────────────────────────────────────────────────────────

class RetryPolicy(BaseModel):
    max_attempts: int = 3
    backoff_base: float = 2.0
    backoff_max: float = 60.0


class MemoryPolicy(BaseModel):
    scratchpad: bool = True
    longterm: bool = False
    longterm_write_on_complete: bool = True


class TerminationPolicy(BaseModel):
    max_iterations: int = 20
    max_tokens: int = 8192


# ── Tool spec ─────────────────────────────────────────────────────────────────

SideEffectLevel = Literal["read-only", "mutates-local", "mutates-external"]


class ToolSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    version: str = "1.0.0"
    side_effect_level: SideEffectLevel = "read-only"
    permissions: list[str] = Field(default_factory=list)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    timeout: float = 30.0
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    prod_risk: bool = Field(default=False, validation_alias=AliasChoices("prod-risk", "prod_risk"))
    reversal: Optional[str] = None

    @staticmethod
    def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
        """Strip JSON Schema keywords unsupported by Groq/Llama tool-calling.

        Llama models fall back to the broken <function=...> text format when
        they encounter keywords like default/minimum/maximum in property defs.
        """
        _UNSUPPORTED = {"default", "minimum", "maximum", "minLength", "maxLength",
                        "minItems", "maxItems", "exclusiveMinimum", "exclusiveMaximum",
                        "multipleOf", "pattern", "format", "examples", "$schema"}
        out: dict[str, Any] = {}
        for k, v in schema.items():
            if k in _UNSUPPORTED:
                continue
            if k == "properties" and isinstance(v, dict):
                out[k] = {pk: ToolSpec._sanitize_schema(pv) if isinstance(pv, dict) else pv
                          for pk, pv in v.items()}
            elif k == "items" and isinstance(v, dict):
                out[k] = ToolSpec._sanitize_schema(v)
            else:
                out[k] = v
        return out

    def to_openai_function(self) -> dict[str, Any]:
        """Convert to OpenAI/Groq tool-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self._sanitize_schema(self.input_schema),
            },
        }


# ── Agent spec ────────────────────────────────────────────────────────────────

class AgentSpec(BaseModel):
    name: str
    role: str
    description: str = ""
    version: str = "1.0.0"
    system_prompt: str
    model: str = "llama-3.3-70b-versatile"
    temperature: float = 0.7
    tools: list[str] = Field(default_factory=list)
    peer_agents: list[str] = Field(default_factory=list)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    termination: TerminationPolicy = Field(default_factory=TerminationPolicy)
    hooks: dict[str, str] = Field(default_factory=dict)
    resource_profile: Optional[str] = None

    @field_validator("temperature")
    @classmethod
    def _check_temp(cls, v: float) -> float:
        if not 0.0 <= v <= 2.0:
            raise ValueError("temperature must be in [0, 2]")
        return v


# ── Swarm topology ────────────────────────────────────────────────────────────

class AgentSlot(BaseModel):
    """One agent role slot in a topology."""
    role: str
    count: int = 1
    tools_override: Optional[list[str]] = None
    model_override: Optional[str] = None


class CoordinationConfig(BaseModel):
    strategy: Literal["hierarchical", "p2p", "hybrid", "lifecycle"] = "hierarchical"
    max_subswarm_size: int = 5
    consensus_protocol: Literal["majority", "weighted", "debate"] = "majority"
    debate_max_rounds: int = 3
    # Lifecycle-aware mode fields (§20.3)
    lifecycle: Optional[str] = None          # e.g. "software_delivery"
    approval_gates: bool = True              # pause for human at gate phases


class BudgetConfig(BaseModel):
    max_cost_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    warn_at_fraction: float = 0.8


class SafetyConfig(BaseModel):
    mode: Literal["auto", "interactive"] = "interactive"
    tool_allowlist: Optional[list[str]] = None
    domain_allowlist: Optional[list[str]] = None
    command_denylist: list[str] = Field(
        default_factory=lambda: ["rm -rf", "sudo", "format", "mkfs"]
    )
    require_confirmation_for: list[SideEffectLevel] = Field(
        default_factory=lambda: ["mutates-external"]
    )


class ConnectorConfig(BaseModel):
    """Optional external service credentials (all optional; tools skip gracefully if absent)."""
    github_token: Optional[str] = None
    linear_token: Optional[str] = None
    notion_token: Optional[str] = None
    cloudflare_token: Optional[str] = None
    supabase_url: Optional[str] = None
    supabase_key: Optional[str] = None
    pagerduty_token: Optional[str] = None
    intercom_token: Optional[str] = None
    doppler_token: Optional[str] = None
    prometheus_url: Optional[str] = None
    loki_url: Optional[str] = None
    grafana_url: Optional[str] = None
    grafana_token: Optional[str] = None
    uptime_kuma_url: Optional[str] = None


class TopologySpec(BaseModel):
    name: str
    description: str = ""
    orchestrator: str = "orchestrator"
    agents: list[AgentSlot] = Field(default_factory=list)
    coordination: CoordinationConfig = Field(default_factory=CoordinationConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    safety: SafetyConfig = Field(default_factory=SafetyConfig)
    memory_backend: str = "local"
    # Workforce extension fields
    connectors: ConnectorConfig = Field(default_factory=ConnectorConfig)
    prod_risk_policy: Literal["always_require_human", "follow_safety_mode"] = "always_require_human"
    dry_run: bool = True                     # DevOps tools dry-run by default in non-interactive CI
    frontend_template: str = "react_tailwind"
    backend_template: str = "fastapi"
    scale: int = 1                           # multiplier for build-layer agent counts


# ── Global swarm config (env + file layered) ──────────────────────────────────

class SwarmConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SWARM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    provider: str = "gemini"
    default_model: str = "gemini-2.5-flash"
    log_level: str = "INFO"
    log_file: Optional[str] = None
    trace_dir: str = "./traces"
    memory_dir: str = "./memory_store"
    memory_backend: str = "local"
    bus_transport: str = "in-process"
    redis_url: str = "redis://localhost:6379"
    deployment_mode: Literal["local", "redis-workers", "kubernetes"] = "local"
    agents_dir: str = "./agents"
    tools_dir: str = "./tools"
    api_host: str = "0.0.0.0"
    api_port: int = 8765
    dashboard_port: int = 8766
    safety_mode: str = "interactive"
    default_budget_usd: Optional[float] = None

    model_config = SettingsConfigDict(
        env_prefix="SWARM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )
