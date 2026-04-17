"""
Agent base class — perceive → plan → act → reflect loop.

Every built-in and user-defined agent is a declarative AgentSpec loaded from YAML.
Custom logic is attached via optional hook functions.  The base class handles:
  - Lifecycle hooks (spawn, task_assigned, message_received, tool_result, complete, error)
  - ReAct-style tool-calling loop
  - Scratchpad memory management and auto-compaction
  - Token budget and iteration-count enforcement
  - Full observability (tracing spans, cost ledger)
  - Safety: per-agent tool permission check before every call
"""

from __future__ import annotations

import importlib
import json
import time
import uuid
from typing import Any, Optional

from configs.schema import AgentSpec
from coordination.bus import MessageBus
from core.exceptions import (
    BudgetExceededError,
    MaxIterationsError,
    ToolPermissionError,
)
from core.message import Message, MessageType
from core.task import Task, TaskResult, TokenUsage
from memory.longterm import LocalChromaMemory
from memory.scratchpad import Scratchpad
from observability.cost import CostLedger
from observability.logging import get_logger
from observability.tracing import Span, get_tracer, new_trace_id, set_trace_id
from providers.base import LLMProvider
from tools.base import ToolHandler

log = get_logger("agent")


class Agent:
    """
    One agent instance.  Instantiated by the runtime for each role slot.
    Not meant to be subclassed — extend via hook functions in the spec.
    """

    def __init__(
        self,
        spec: AgentSpec,
        provider: LLMProvider,
        tool_handlers: dict[str, ToolHandler],
        bus: MessageBus,
        scratchpad: Optional[Scratchpad] = None,
        longterm_memory: Optional[LocalChromaMemory] = None,
        ledger: Optional[CostLedger] = None,
        agent_id: Optional[str] = None,
    ) -> None:
        self.id = agent_id or str(uuid.uuid4())
        self.spec = spec
        self._provider = provider
        self._tools = tool_handlers
        self._bus = bus
        self._scratchpad = scratchpad or Scratchpad(
            self.id,
            max_tokens=spec.termination.max_tokens,
        )
        self._longterm = longterm_memory
        self._ledger = ledger
        self._hooks = self._load_hooks(spec.hooks)

    # ── Public API ────────────────────────────────────────────────────────────

    async def run_task(self, task: Task, trace_id: Optional[str] = None) -> TaskResult:
        """Execute one task end-to-end. Returns the final TaskResult."""
        if trace_id:
            set_trace_id(trace_id)
        else:
            new_trace_id()

        tracer = get_tracer()
        start = time.time()
        total_usage = TokenUsage()
        iteration = 0

        with Span(tracer, f"agent.{self.spec.role}", "agent",
                  agent_id=self.id, task_id=task.id) as agent_span:

            await self._on_task_assigned(task)
            task.mark_running(self.id)

            # Build initial conversation
            messages = self._build_initial_messages(task)
            available_tools = self._get_tool_schemas()

            # Optionally seed from long-term memory
            if self.spec.memory_policy.longterm and self._longterm:
                memories = await self._longterm.search(task.goal, limit=5)
                if memories:
                    ctx = "\n".join(f"- {m['content']}" for m in memories)
                    messages[0]["content"] += f"\n\nRelevant prior knowledge:\n{ctx}"

            while iteration < self.spec.termination.max_iterations:
                iteration += 1

                # ── Budget check ──────────────────────────────────────────────
                if task.constraints.budget:
                    spent = self._ledger.total_cost if self._ledger else 0
                    if spent >= task.constraints.budget:
                        raise BudgetExceededError(spent, task.constraints.budget)

                # ── Perceive + Plan: call LLM ──────────────────────────────────
                with Span(tracer, "llm.complete", "llm",
                          agent_id=self.id, task_id=task.id, iteration=iteration):
                    result = await self._provider.complete(
                        messages=messages,
                        model=self.spec.model,
                        tools=available_tools or None,
                        temperature=self.spec.temperature,
                        max_tokens=self.spec.termination.max_tokens,
                    )

                total_usage = total_usage + result.usage
                cost = self._provider.estimate_cost(result.usage, self.spec.model)
                if self._ledger:
                    self._ledger.record(self.id, self.spec.model, result.usage, task.id)

                task.add_trace("llm_call", iteration=iteration,
                               tokens=result.usage.total_tokens, cost=cost)

                # ── Act: no tool calls = final answer ─────────────────────────
                if not result.tool_calls:
                    output = result.content or ""
                    duration = time.time() - start
                    task_result = TaskResult(
                        output=output,
                        success=True,
                        token_usage=total_usage,
                        cost=self._provider.estimate_cost(total_usage, self.spec.model),
                        duration=duration,
                        iterations=iteration,
                    )
                    task.mark_completed(task_result)
                    agent_span.set(iterations=iteration, cost=task_result.cost)

                    # Reflect: optionally persist to long-term memory
                    if self.spec.memory_policy.longterm and self._longterm:
                        await self._longterm.write(
                            f"task:{task.id}",
                            {"goal": task.goal, "result": output},
                        )

                    await self._on_complete(task, task_result)
                    log.info("agent_complete",
                             agent=self.spec.role,
                             task_id=task.id[:8],
                             iterations=iteration,
                             cost_usd=round(task_result.cost, 6))
                    return task_result

                # ── Add assistant message with tool calls ──────────────────────
                assistant_msg: dict[str, Any] = {"role": "assistant"}
                if result.content:
                    assistant_msg["content"] = result.content
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.name,
                            "arguments": json.dumps(tc.arguments),
                        },
                    }
                    for tc in result.tool_calls
                ]
                messages.append(assistant_msg)
                self._scratchpad.append_message(assistant_msg)

                # ── Act: execute each tool call ───────────────────────────────
                for tc in result.tool_calls:
                    tool_result_content = await self._execute_tool(tc.name, tc.arguments,
                                                                    tc.id, task)
                    tool_msg: dict[str, Any] = {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result_content),
                    }
                    messages.append(tool_msg)
                    self._scratchpad.append_message(tool_msg)

            # Exceeded max iterations
            duration = time.time() - start
            final_result = TaskResult(
                output="Task did not complete within the iteration limit.",
                success=False,
                error="max_iterations_exceeded",
                token_usage=total_usage,
                cost=self._provider.estimate_cost(total_usage, self.spec.model),
                duration=duration,
                iterations=iteration,
            )
            task.mark_completed(final_result)
            raise MaxIterationsError(
                f"Agent '{self.spec.role}' exceeded {self.spec.termination.max_iterations} iterations"
            )

    async def send_message(self, recipient_id: str, payload: dict, correlation_id: str = "") -> None:
        msg = Message(
            sender_id=self.id,
            recipient_id=recipient_id,
            type=MessageType.REQUEST,
            payload=payload,
            correlation_id=correlation_id or str(uuid.uuid4()),
        )
        await self._bus.send(msg)

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _build_initial_messages(self, task: Task) -> list[dict[str, Any]]:
        system = self._render_system_prompt(task)
        user_content = task.goal
        if task.input_payload:
            user_content += f"\n\nAdditional context:\n{json.dumps(task.input_payload, indent=2)}"
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]

    def _render_system_prompt(self, task: Task) -> str:
        prompt = self.spec.system_prompt
        # Simple template substitution
        prompt = prompt.replace("{{agent_id}}", self.id)
        prompt = prompt.replace("{{role}}", self.spec.role)
        prompt = prompt.replace("{{task_id}}", task.id)
        return prompt

    def _get_tool_schemas(self) -> list[dict[str, Any]]:
        schemas = []
        for name in self.spec.tools:
            if name in self._tools:
                schemas.append(self._tools[name].get_openai_schema())
        return schemas

    async def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        call_id: str,
        task: Task,
    ) -> dict[str, Any]:
        # Permission check
        if name not in self.spec.tools:
            raise ToolPermissionError(self.spec.role, name)

        if name not in self._tools:
            return {"error": f"Tool '{name}' not found in registry"}

        handler = self._tools[name]
        try:
            result = await handler.run(arguments, agent_id=self.id)
            task.add_trace("tool_called", tool=name, call_id=call_id)
            log.debug("tool_result", agent=self.spec.role, tool=name)
            await self._on_tool_result(name, arguments, result)
            return result
        except Exception as exc:
            log.error("tool_error", agent=self.spec.role, tool=name, error=str(exc))
            task.add_trace("tool_error", tool=name, error=str(exc))
            return {"error": str(exc)}

    # ── Hooks ─────────────────────────────────────────────────────────────────

    def _load_hooks(self, hooks: dict[str, str]) -> dict[str, Any]:
        loaded: dict[str, Any] = {}
        for hook_name, module_fn in hooks.items():
            try:
                module_path, fn_name = module_fn.rsplit(".", 1)
                mod = importlib.import_module(module_path)
                loaded[hook_name] = getattr(mod, fn_name)
            except Exception as exc:
                log.warning("hook_load_failed", hook=hook_name, ref=module_fn, error=str(exc))
        return loaded

    async def _on_spawn(self) -> None:
        if fn := self._hooks.get("on_spawn"):
            await fn(self)

    async def _on_task_assigned(self, task: Task) -> None:
        if fn := self._hooks.get("on_task_assigned"):
            await fn(self, task)

    async def _on_tool_result(self, tool: str, inputs: dict, result: dict) -> None:
        if fn := self._hooks.get("on_tool_result"):
            await fn(self, tool, inputs, result)

    async def _on_complete(self, task: Task, result: TaskResult) -> None:
        if fn := self._hooks.get("on_complete"):
            await fn(self, task, result)

    async def _on_error(self, task: Task, exc: Exception) -> None:
        if fn := self._hooks.get("on_error"):
            await fn(self, task, exc)
