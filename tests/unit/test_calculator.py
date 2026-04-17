"""Unit tests for the calculator tool."""

import pytest
from configs.schema import ToolSpec
from tools.calculator.handler import CalculatorHandler


@pytest.fixture
def handler():
    h = CalculatorHandler()
    h.spec = ToolSpec(
        name="calculator",
        description="Calc",
        input_schema={
            "type": "object",
            "properties": {"expression": {"type": "string"}},
            "required": ["expression"],
        },
        output_schema={},
    )
    return h


@pytest.mark.asyncio
async def test_basic_arithmetic(handler):
    r = await handler._run({"expression": "2 + 2"})
    assert r["result"] == 4


@pytest.mark.asyncio
async def test_power(handler):
    r = await handler._run({"expression": "2 ** 10"})
    assert r["result"] == 1024


@pytest.mark.asyncio
async def test_math_function(handler):
    r = await handler._run({"expression": "sqrt(144)"})
    assert r["result"] == 12.0


@pytest.mark.asyncio
async def test_injection_blocked(handler):
    r = await handler._run({"expression": "import os"})
    assert "error" in r


@pytest.mark.asyncio
async def test_dunder_blocked(handler):
    r = await handler._run({"expression": "__import__('os')"})
    assert "error" in r


@pytest.mark.asyncio
async def test_invalid_expression(handler):
    r = await handler._run({"expression": "not_a_number + ?"})
    assert "error" in r


@pytest.mark.asyncio
async def test_self_test(handler):
    assert await handler.self_test() is True
