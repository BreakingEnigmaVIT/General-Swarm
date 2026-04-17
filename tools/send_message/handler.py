"""Send a message to another agent via the bus (injected at runtime)."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from coordination.bus import MessageBus
from core.message import Message, MessageType
from tools.base import ToolHandler

_bus: Optional[MessageBus] = None
_sender_id: str = "unknown"


def set_bus(bus: MessageBus, sender_id: str) -> None:
    global _bus, _sender_id
    _bus = bus
    _sender_id = sender_id


class SendMessageHandler(ToolHandler):
    async def _run(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if _bus is None:
            return {"sent": False, "error": "Bus not configured"}
        type_map = {
            "request": MessageType.REQUEST,
            "notification": MessageType.NOTIFICATION,
            "handoff": MessageType.HANDOFF,
        }
        msg_type = type_map.get(inputs.get("message_type", "request"), MessageType.REQUEST)
        msg = Message(
            sender_id=_sender_id,
            recipient_id=inputs["recipient_id"],
            type=msg_type,
            payload=inputs["payload"],
            correlation_id=str(uuid.uuid4()),
        )
        await _bus.send(msg)
        return {"sent": True, "message_id": msg.id}

    async def self_test(self) -> bool:
        return True


handler = SendMessageHandler()
