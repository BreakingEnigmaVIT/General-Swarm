"""Unit tests for core.message."""

from core.message import Message, MessageType


def test_message_request_factory():
    msg = Message.request("agent-a", "agent-b", {"task": "do x"})
    assert msg.type == MessageType.REQUEST
    assert msg.sender_id == "agent-a"
    assert msg.recipient_id == "agent-b"
    assert msg.correlation_id is not None


def test_message_response_factory():
    msg = Message.response("agent-b", "agent-a", {"result": "done"}, "corr-123")
    assert msg.type == MessageType.RESPONSE
    assert msg.correlation_id == "corr-123"


def test_message_broadcast_factory():
    msg = Message.broadcast("agent-a", "swarm-channel", {"event": "started"})
    assert msg.type == MessageType.BROADCAST
    assert msg.channel == "swarm-channel"
    assert msg.recipient_id is None


def test_message_has_unique_id():
    m1 = Message.request("a", "b", {})
    m2 = Message.request("a", "b", {})
    assert m1.id != m2.id
