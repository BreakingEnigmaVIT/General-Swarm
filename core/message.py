"""Inter-agent Message model — all communication flows through the bus."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uid() -> str:
    return str(uuid.uuid4())


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFICATION = "notification"
    VOTE = "vote"
    HANDOFF = "handoff"
    BROADCAST = "broadcast"


class Message(BaseModel):
    id: str = Field(default_factory=_uid)
    sender_id: str
    recipient_id: Optional[str] = None
    channel: Optional[str] = None
    type: MessageType
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=_now)

    @classmethod
    def request(
        cls,
        sender_id: str,
        recipient_id: str,
        payload: dict[str, Any],
        correlation_id: Optional[str] = None,
    ) -> "Message":
        return cls(
            sender_id=sender_id,
            recipient_id=recipient_id,
            type=MessageType.REQUEST,
            payload=payload,
            correlation_id=correlation_id or _uid(),
        )

    @classmethod
    def response(
        cls,
        sender_id: str,
        recipient_id: str,
        payload: dict[str, Any],
        correlation_id: str,
    ) -> "Message":
        return cls(
            sender_id=sender_id,
            recipient_id=recipient_id,
            type=MessageType.RESPONSE,
            payload=payload,
            correlation_id=correlation_id,
        )

    @classmethod
    def broadcast(
        cls,
        sender_id: str,
        channel: str,
        payload: dict[str, Any],
    ) -> "Message":
        return cls(
            sender_id=sender_id,
            channel=channel,
            type=MessageType.BROADCAST,
            payload=payload,
        )
