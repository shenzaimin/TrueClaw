from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessage:
    channel: str
    chat_id: str
    user_id: str
    text: str
    thread_id: str | None = None
    message_id: str | None = None
    trace_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundChunk:
    kind: str = "text"
    text: str | None = None


@dataclass
class OutboundMessage:
    channel: str
    chat_id: str
    chunks: list[OutboundChunk]
    thread_id: str | None = None
    trace_id: str | None = None
