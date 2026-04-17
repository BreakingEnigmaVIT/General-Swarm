"""All custom exceptions for the swarm runtime."""

from __future__ import annotations


class SwarmError(Exception):
    """Base for all swarm errors."""


# ── Registry ──────────────────────────────────────────────────────────────────

class RegistryError(SwarmError):
    """Problem with a registry operation."""


class NotRegisteredError(RegistryError):
    """Lookup for a name that was never registered."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{kind} '{name}' is not registered")
        self.kind = kind
        self.name = name


class AlreadyRegisteredError(RegistryError):
    """Attempt to register a name that already exists."""

    def __init__(self, kind: str, name: str) -> None:
        super().__init__(f"{kind} '{name}' is already registered")
        self.kind = kind
        self.name = name


# ── Spec / Config ─────────────────────────────────────────────────────────────

class SpecValidationError(SwarmError):
    """A spec file (YAML) failed validation."""

    def __init__(self, path: str, detail: str) -> None:
        super().__init__(f"Spec validation failed for '{path}': {detail}")
        self.path = path
        self.detail = detail


class ConfigError(SwarmError):
    """Configuration loading or validation failure."""


# ── Provider / LLM ────────────────────────────────────────────────────────────

class ProviderError(SwarmError):
    """An LLM provider call failed."""


class RateLimitError(ProviderError):
    """Provider rate-limit hit; safe to retry."""


class ModelNotAvailableError(ProviderError):
    """Requested model is unavailable."""

    def __init__(self, model: str) -> None:
        super().__init__(f"Model '{model}' is not available")
        self.model = model


# ── Tool ──────────────────────────────────────────────────────────────────────

class ToolError(SwarmError):
    """A tool execution failed."""


class ToolPermissionError(ToolError):
    """Agent attempted to call a tool it is not allowed to use."""

    def __init__(self, agent_role: str, tool_name: str) -> None:
        super().__init__(f"Agent '{agent_role}' is not permitted to call tool '{tool_name}'")
        self.agent_role = agent_role
        self.tool_name = tool_name


class ToolTimeoutError(ToolError):
    """Tool execution exceeded its configured timeout."""


class ToolInputError(ToolError):
    """Tool received invalid input (schema validation failure)."""


# ── Agent ─────────────────────────────────────────────────────────────────────

class AgentError(SwarmError):
    """Generic agent-level error."""


class MaxIterationsError(AgentError):
    """Agent exceeded its max iteration limit without producing a final answer."""


class BudgetExceededError(AgentError):
    """Task budget (token cost) was exhausted."""

    def __init__(self, spent: float, limit: float) -> None:
        super().__init__(f"Budget exceeded: spent ${spent:.4f}, limit ${limit:.4f}")
        self.spent = spent
        self.limit = limit


# ── Task / Coordination ───────────────────────────────────────────────────────

class TaskError(SwarmError):
    """Task-level error."""


class CyclicTaskGraphError(TaskError):
    """The task graph contains a cycle."""


class SubswarmError(SwarmError):
    """Error during subswarm execution."""


# ── Memory ────────────────────────────────────────────────────────────────────

class MemoryError(SwarmError):
    """Memory backend error."""


# ── Safety ────────────────────────────────────────────────────────────────────

class SafetyError(SwarmError):
    """A safety policy blocked an action."""


class PromptInjectionWarning(SwarmError):
    """Possible prompt-injection detected in untrusted content."""
