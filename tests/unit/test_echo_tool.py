"""Unit tests for the echo tool handler."""

import pytest
from configs.schema import ToolSpec
from tools.echo.handler import EchoHandler


@pytest.fixture
def handler():
    h = EchoHandler()
    h.spec = ToolSpec(
        name="echo",
        description="Echo",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
        output_schema={},
    )
    return h


@pytest.mark.asyncio
async def test_echo_returns_message(handler):
    result = await handler.run({"message": "hello"})
    assert result == {"echoed": "hello"}


@pytest.mark.asyncio
async def test_echo_empty_string(handler):
    result = await handler.run({"message": ""})
    assert result["echoed"] == ""


@pytest.mark.asyncio
async def test_self_test_passes(handler):
    assert await handler.self_test() is True


@pytest.mark.asyncio
async def test_schema_returned():
    from tools.echo.handler import handler as h
    schema = h.get_openai_schema()
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "echo"


@pytest.mark.asyncio
async def test_missing_required_field_raises(handler):
    from core.exceptions import ToolInputError
    with pytest.raises(ToolInputError):
        await handler.run({})
