from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from trueclaw.bus.events import InboundMessageEvent, OutboundMessageEvent
from trueclaw.bus.queue import MessageBus


class BaseChannel(ABC):
    name: str = "base"
    display_name: str = "Base"

    def __init__(self, config: Any, bus: MessageBus):
        self.config = config
        self.bus = bus
        self._running = False

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return {"enabled": False}

    @property
    def is_running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...

    @abstractmethod
    async def send(self, msg: OutboundMessageEvent) -> None:
        ...

    async def _handle_message(
        self,
        *,
        sender_id: str,
        chat_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        session_key: str | None = None,
    ) -> None:
        await self.bus.publish_inbound(
            InboundMessageEvent(
                channel=self.name,
                sender_id=str(sender_id),
                chat_id=str(chat_id),
                content=content,
                metadata=metadata or {},
                session_key_override=session_key,
            )
        )
