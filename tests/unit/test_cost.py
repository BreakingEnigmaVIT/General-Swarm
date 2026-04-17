"""Unit tests for cost accounting."""

import pytest
from core.task import TokenUsage
from observability.cost import CostLedger, estimate_cost


def test_estimate_cost_known_model():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)
    cost = estimate_cost(usage, "llama-3.3-70b-versatile")
    assert cost == pytest.approx(0.59 + 0.79)


def test_estimate_cost_unknown_model_uses_default():
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000, total_tokens=2_000_000)
    cost = estimate_cost(usage, "unknown-model-xyz")
    assert cost > 0


def test_ledger_record_and_total():
    ledger = CostLedger()
    usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
    ledger.record("agent-1", "llama-3.1-8b-instant", usage, "task-1")
    ledger.record("agent-1", "llama-3.1-8b-instant", usage, "task-2")
    assert ledger.total_tokens == 300
    assert ledger.total_cost > 0


def test_ledger_by_agent():
    ledger = CostLedger()
    usage = TokenUsage(input_tokens=100, output_tokens=50, total_tokens=150)
    ledger.record("agent-a", "llama-3.1-8b-instant", usage)
    ledger.record("agent-b", "llama-3.1-8b-instant", usage)
    by_agent = ledger.by_agent()
    assert "agent-a" in by_agent
    assert "agent-b" in by_agent


def test_ledger_summary():
    ledger = CostLedger()
    usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    ledger.record("agent-1", "llama-3.1-8b-instant", usage, "task-1")
    summary = ledger.summary()
    assert "total_cost_usd" in summary
    assert "total_tokens" in summary
    assert summary["total_tokens"] == 15
