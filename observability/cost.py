"""Per-agent / per-task / per-swarm token usage and cost accounting."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from core.task import TokenUsage

# Groq pricing per 1 M tokens (input / output), USD — approximate, update as needed
_GROQ_PRICING: dict[str, tuple[float, float]] = {
    "llama-3.3-70b-versatile":    (0.59, 0.79),
    "llama-3.1-70b-versatile":    (0.59, 0.79),
    "llama-3.1-8b-instant":       (0.05, 0.08),
    "llama3-70b-8192":            (0.59, 0.79),
    "llama3-8b-8192":             (0.05, 0.08),
    "mixtral-8x7b-32768":         (0.24, 0.24),
    "gemma2-9b-it":               (0.20, 0.20),
    "gemma-7b-it":                (0.07, 0.07),
    "llama-3.2-90b-vision-preview": (0.90, 0.90),
    "llama-3.2-11b-vision-preview": (0.18, 0.18),
}
_DEFAULT_PRICE = (0.59, 0.79)


def estimate_cost(usage: TokenUsage, model: str) -> float:
    in_price, out_price = _GROQ_PRICING.get(model, _DEFAULT_PRICE)
    return (usage.input_tokens * in_price + usage.output_tokens * out_price) / 1_000_000


# ── Ledger ────────────────────────────────────────────────────────────────────

@dataclass
class UsageRecord:
    agent_id: str
    task_id: Optional[str]
    model: str
    usage: TokenUsage
    cost: float


@dataclass
class CostLedger:
    """Thread-safe accumulator for the lifetime of one swarm run."""

    records: list[UsageRecord] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def record(
        self,
        agent_id: str,
        model: str,
        usage: TokenUsage,
        task_id: Optional[str] = None,
    ) -> float:
        cost = estimate_cost(usage, model)
        with self._lock:
            self.records.append(UsageRecord(agent_id, task_id, model, usage, cost))
        return cost

    @property
    def total_cost(self) -> float:
        return sum(r.cost for r in self.records)

    @property
    def total_tokens(self) -> int:
        return sum(r.usage.total_tokens for r in self.records)

    def by_agent(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for r in self.records:
            totals[r.agent_id] = totals.get(r.agent_id, 0.0) + r.cost
        return totals

    def by_task(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for r in self.records:
            if r.task_id:
                totals[r.task_id] = totals.get(r.task_id, 0.0) + r.cost
        return totals

    def summary(self) -> dict:
        return {
            "total_cost_usd": round(self.total_cost, 6),
            "total_tokens": self.total_tokens,
            "records": len(self.records),
            "by_agent": {k: round(v, 6) for k, v in self.by_agent().items()},
        }


# ── Module-level default ledger ───────────────────────────────────────────────

_default_ledger: Optional[CostLedger] = None


def get_ledger() -> CostLedger:
    global _default_ledger
    if _default_ledger is None:
        _default_ledger = CostLedger()
    return _default_ledger


def reset_ledger() -> CostLedger:
    global _default_ledger
    _default_ledger = CostLedger()
    return _default_ledger
