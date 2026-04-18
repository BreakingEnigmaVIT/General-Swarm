"""
Three hot-reloadable registries — Agent, Tool, Provider — with auto-discovery.

Discovery scans for spec.yaml files under the configured agents_dir / tools_dir,
loads the spec, then imports the optional handler/hook module.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from threading import Lock
from typing import Any, Generic, Optional, TypeVar

from configs.loader import load_agent_spec, load_tool_spec
from configs.schema import AgentSpec, ToolSpec
from core.exceptions import AlreadyRegisteredError, NotRegisteredError
from observability.logging import get_logger
from providers.base import LLMProvider
from tools.base import ToolHandler

log = get_logger("registry")

T = TypeVar("T")


# ── Generic registry ──────────────────────────────────────────────────────────

class Registry(Generic[T]):
    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._entries: dict[str, T] = {}
        self._lock = Lock()

    def register(self, name: str, entry: T, *, overwrite: bool = False) -> None:
        with self._lock:
            if name in self._entries and not overwrite:
                raise AlreadyRegisteredError(self._kind, name)
            self._entries[name] = entry
            log.debug("registered", kind=self._kind, name=name)

    def unregister(self, name: str) -> None:
        with self._lock:
            if name not in self._entries:
                raise NotRegisteredError(self._kind, name)
            del self._entries[name]

    def lookup(self, name: str) -> T:
        with self._lock:
            if name not in self._entries:
                raise NotRegisteredError(self._kind, name)
            return self._entries[name]

    def list(self) -> list[str]:
        with self._lock:
            return sorted(self._entries.keys())

    def validate(self, name: str) -> bool:
        with self._lock:
            return name in self._entries

    def items(self) -> list[tuple[str, T]]:
        with self._lock:
            return list(self._entries.items())


# ── Tool registry ─────────────────────────────────────────────────────────────

class ToolRegistry(Registry[ToolHandler]):
    def __init__(self) -> None:
        super().__init__("tool")

    def autodiscover(self, tools_dir: str = "./tools") -> None:
        base = Path(tools_dir)
        if not base.exists():
            log.warning("tools_dir_missing", path=str(base))
            return
        for spec_path in sorted(base.rglob("spec.yaml")):
            try:
                spec = load_tool_spec(spec_path)
                handler = _load_tool_handler(spec_path.parent, spec)
                handler.spec = spec
                self.register(spec.name, handler, overwrite=True)
                log.info("tool_discovered", name=spec.name, path=str(spec_path))
            except Exception as exc:
                log.error("tool_discovery_error", path=str(spec_path), error=str(exc))


def _load_tool_handler(tool_dir: Path, spec: ToolSpec) -> ToolHandler:
    handler_path = tool_dir / "handler.py"
    if not handler_path.exists():
        raise FileNotFoundError(f"No handler.py found in {tool_dir}")

    # Load under the canonical dotted name (tools.<name>.handler) AND register
    # the parent package (tools.<name>) so that a later
    # `import tools.<name>.handler` finds the same module object in sys.modules
    # rather than doing a fresh file load.  This is critical for module-level
    # singletons (_factory, _memory, etc.) set via set_X() helpers at runtime.
    pkg_name = f"tools.{spec.name.replace('-', '_')}"
    canonical_name = f"{pkg_name}.handler"

    if canonical_name in sys.modules:
        module = sys.modules[canonical_name]
    else:
        # Ensure the parent package entry exists so Python's import machinery
        # doesn't choke when resolving the dotted name later.
        if pkg_name not in sys.modules:
            pkg_init = tool_dir / "__init__.py"
            pkg_spec = importlib.util.spec_from_file_location(
                pkg_name, pkg_init if pkg_init.exists() else None,
                submodule_search_locations=[str(tool_dir)],
            )
            if pkg_spec is not None:
                pkg_mod = importlib.util.module_from_spec(pkg_spec)
                sys.modules[pkg_name] = pkg_mod
                try:
                    pkg_spec.loader.exec_module(pkg_mod)  # type: ignore[union-attr]
                except Exception:
                    pass  # empty __init__.py is fine

        private_name = f"_tool_{spec.name.replace('-', '_')}"
        spec_ = importlib.util.spec_from_file_location(canonical_name, handler_path)
        if spec_ is None:
            raise ImportError(f"Cannot load {handler_path}")
        module = importlib.util.module_from_spec(spec_)
        sys.modules[canonical_name] = module
        sys.modules[private_name] = module
        spec_.loader.exec_module(module)  # type: ignore[union-attr]

    if hasattr(module, "handler"):
        return module.handler
    # Fall back to any ToolHandler subclass in the module
    for attr in dir(module):
        obj = getattr(module, attr)
        if isinstance(obj, ToolHandler):
            return obj
    raise AttributeError(f"No ToolHandler instance or 'handler' in {handler_path}")


# ── Agent spec registry ───────────────────────────────────────────────────────

class AgentSpecRegistry(Registry[AgentSpec]):
    def __init__(self) -> None:
        super().__init__("agent_spec")

    def autodiscover(self, agents_dir: str = "./agents") -> None:
        base = Path(agents_dir)
        if not base.exists():
            log.warning("agents_dir_missing", path=str(base))
            return
        for spec_path in sorted(base.rglob("spec.yaml")):
            try:
                spec = load_agent_spec(spec_path)
                self.register(spec.role, spec, overwrite=True)
                log.info("agent_discovered", role=spec.role, path=str(spec_path))
            except Exception as exc:
                log.error("agent_discovery_error", path=str(spec_path), error=str(exc))


# ── Provider registry ─────────────────────────────────────────────────────────

class ProviderRegistry(Registry[LLMProvider]):
    def __init__(self) -> None:
        super().__init__("provider")

    def get_or_default(self, name: Optional[str] = None) -> LLMProvider:
        names = self.list()
        if not names:
            raise NotRegisteredError("provider", name or "any")
        target = name or names[0]
        return self.lookup(target)


# ── Module-level singleton registries ─────────────────────────────────────────

_tool_registry: Optional[ToolRegistry] = None
_agent_spec_registry: Optional[AgentSpecRegistry] = None
_provider_registry: Optional[ProviderRegistry] = None


def get_tool_registry() -> ToolRegistry:
    global _tool_registry
    if _tool_registry is None:
        _tool_registry = ToolRegistry()
    return _tool_registry


def get_agent_spec_registry() -> AgentSpecRegistry:
    global _agent_spec_registry
    if _agent_spec_registry is None:
        _agent_spec_registry = AgentSpecRegistry()
    return _agent_spec_registry


def get_provider_registry() -> ProviderRegistry:
    global _provider_registry
    if _provider_registry is None:
        _provider_registry = ProviderRegistry()
    return _provider_registry


def bootstrap_registries(
    tools_dir: str = "./tools",
    agents_dir: str = "./agents",
    groq_api_key: str = "",
    openrouter_api_key: str = "",
    default_model: str = "llama-3.3-70b-versatile",
) -> tuple[ToolRegistry, AgentSpecRegistry, ProviderRegistry]:
    """
    Initialise all three registries from scratch:
    - Autodiscover tools and agent specs from disk
    - Register Groq and/or OpenRouter providers
    """
    from providers.groq.adapter import GroqAdapter
    from providers.openrouter.adapter import OpenRouterAdapter

    tr = get_tool_registry()
    ar = get_agent_spec_registry()
    pr = get_provider_registry()

    tr.autodiscover(tools_dir)
    ar.autodiscover(agents_dir)

    if groq_api_key:
        groq = GroqAdapter(api_key=groq_api_key, default_model=default_model)
        pr.register("groq", groq, overwrite=True)

    if openrouter_api_key:
        openrouter = OpenRouterAdapter(api_key=openrouter_api_key, default_model=default_model)
        pr.register("openrouter", openrouter, overwrite=True)

    return tr, ar, pr
