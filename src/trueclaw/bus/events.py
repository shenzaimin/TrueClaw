from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class InboundMessageEvent:
    channel: str
    sender_id: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    session_key_override: str | None = None


@dataclass
class OutboundMessageEvent:
    channel: str
    chat_id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
